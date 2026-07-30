"""Mutation and determinism proofs for the signed command."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import forgeboard_report.cli as cli
from fixtures.hermes_019 import build_hermes_019_board
from fixtures.normalize import at, card, raw_snapshot
from forgeboard_report import publish
from forgeboard_report.errors import PublicationWriteError


def _hashes(paths: tuple[Path, ...]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _github_runner(arguments, **_kwargs):
    args = tuple(arguments)
    if args == ("gh", "--version"):
        return subprocess.CompletedProcess(args, 0, "gh version 2.96.0\n", "")
    assert args[:3] == ("gh", "api", "graphql")
    assert "query=" in args[-1] and "mutation" not in args[-1].lower()
    number = 11 if "pullRequest(number: 11)" in args[-1] else 1
    repository = "example/forge" if 'owner: "example"' in args[-1] else "acme/repo"
    payload = {
        "data": {
            "pr0": {
                "pullRequest": {
                    "id": f"NODE-{number}",
                    "url": f"https://github.com/{repository}/pull/{number}",
                    "number": number,
                    "state": "MERGED",
                    "mergedAt": "2025-08-05T00:00:00+00:00",
                }
            }
        }
    }
    return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


def _actual_command_arguments(graph_path: Path, output: Path) -> list[str]:
    return [
        "--board",
        "default",
        "--graph",
        str(graph_path),
        "--from",
        datetime.fromtimestamp(1_754_000_000 - 60, UTC).isoformat(),
        "--to",
        datetime.fromtimestamp(1_754_000_500, UTC).isoformat(),
        "--operator",
        "operator-exact",
        "--output",
        str(output),
    ]


def test_read_only_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    board = build_hermes_019_board(tmp_path / "hermes", board_slug="default")
    try:
        graph_path = tmp_path / "graph.json"
        graph_path.write_text(
            '[{"id":"CHUNK-1","lane":"fixture","depends_on":[]},'
            '{"id":"CHUNK-ARCHIVED","lane":"fixture","depends_on":["CHUNK-1"]}]',
            encoding="utf-8",
        )
        planning = tuple(
            Path(name)
            for name in ("docs/REQUIREMENTS.md", "docs/ROADMAP.md", "docs/decision-log.md")
        )
        inputs = (graph_path, board.database, *board.database.parent.glob("kanban.db-*"), *planning)
        before = _hashes(inputs)
        monkeypatch.setenv("HERMES_KANBAN_DB", str(board.database))
        cli.run(_actual_command_arguments(graph_path, tmp_path / "report"), runner=_github_runner)
        assert _hashes(inputs) == before
    finally:
        board.close()


def test_read_only_failing_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text('[{"id":"A","lane":"fixture","depends_on":[]}]', encoding="utf-8")
    inputs = (
        graph_path,
        Path("docs/REQUIREMENTS.md"),
        Path("docs/ROADMAP.md"),
        Path("docs/decision-log.md"),
    )
    before = _hashes(inputs)
    invalid = [
        "--board",
        "bad/slash",
        "--graph",
        str(graph_path),
        "--from",
        at(10).isoformat(),
        "--to",
        at(50).isoformat(),
        "--operator",
        "operator",
        "--output",
        str(tmp_path / "usage"),
    ]
    assert cli.main(invalid) == 2
    unavailable = invalid.copy()
    unavailable[1] = "forge-board"
    unavailable[3] = str(tmp_path / "missing.json")
    assert cli.main(unavailable) == 3
    cyclic = tmp_path / "cyclic.json"
    cyclic.write_text(
        '[{"id":"A","lane":"x","depends_on":["B"]},{"id":"B","lane":"x","depends_on":["A"]}]',
        encoding="utf-8",
    )
    invalid_core = invalid.copy()
    invalid_core[1], invalid_core[3] = "forge-board", str(cyclic)
    assert cli.main(invalid_core) == 4
    publication_arguments = invalid.copy()
    publication_arguments[1] = "forge-board"
    publication_arguments[3] = str(graph_path)
    publication_arguments[-1] = str(tmp_path / "publication")
    monkeypatch.setattr(
        publish,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PublicationWriteError("full")),
    )
    assert (
        cli.main(
            publication_arguments,
            runner=_github_runner,
            snapshotter=lambda *_args: raw_snapshot(
                cards=(card("A", completed_at=at(20)),),
                board_slug="forge-board",
            ),
        )
        == 5
    )
    assert _hashes(inputs) == before


def _write_fake_gh(directory: Path) -> Path:
    executable = directory / "gh"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """\
import json
import sys

if sys.argv[1:] == ["--version"]:
    print("gh version 2.96.0")
else:
    print('{"data":{"pr0":{"pullRequest":{"id":"NODE-11","url":"https://github.com/example/forge/pull/11","number":11,"state":"MERGED","mergedAt":"2025-08-05T00:00:00+00:00"}}}}')
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_deterministic_output(tmp_path: Path) -> None:
    settings = (("1", "UTC0", "C"), ("777", "Pacific/Auckland", "en_NZ.UTF-8"))
    bundles = []
    for index, (seed, timezone, locale) in enumerate(settings):
        fixture_directory = tmp_path / f"fixture-{index}"
        board = build_hermes_019_board(
            fixture_directory,
            board_slug="default",
            journal_mode="delete",
        )
        try:
            graph_path = fixture_directory / "graph.json"
            graph_path.write_text(
                '[{"id":"CHUNK-1","lane":"fixture","depends_on":[]},'
                '{"id":"CHUNK-ARCHIVED","lane":"fixture","depends_on":["CHUNK-1"]}]',
                encoding="utf-8",
            )
            bin_directory = fixture_directory / "bin"
            bin_directory.mkdir()
            _write_fake_gh(bin_directory)
            destination = fixture_directory / "report"
            environment = {
                **os.environ,
                "HERMES_KANBAN_DB": str(board.database),
                "LC_ALL": locale,
                "PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}",
                "PYTHONHASHSEED": seed,
                "TZ": timezone,
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "forgeboard_report.cli",
                    "--board",
                    "default",
                    "--graph",
                    str(graph_path),
                    "--from",
                    datetime.fromtimestamp(1_754_000_000 - 60, UTC).isoformat(),
                    "--to",
                    datetime.fromtimestamp(1_754_000_500, UTC).isoformat(),
                    "--operator",
                    "operator-exact",
                    "--output",
                    str(destination),
                ],
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            bundles.append(
                tuple((destination / name).read_bytes() for name in ("report.json", "report.md"))
            )
        finally:
            board.close()
    assert bundles[0] == bundles[1]
