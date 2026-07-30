"""Mutation and determinism proofs for the signed command."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import forgeboard_report.cli as cli
from fixtures.hermes_019 import build_hermes_019_board
from fixtures.normalize import at, card, chunk_metadata, event, raw_snapshot, run
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
    publication_error = PublicationWriteError("full")
    monkeypatch.setattr(
        cli,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(publication_error),
    )
    assert cli.main(invalid) == 5
    assert _hashes(inputs) == before


def test_deterministic_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(
        '[{"id":"P","lane":"fixture","depends_on":[]},'
        '{"id":"C","lane":"fixture","depends_on":["P"]}]',
        encoding="utf-8",
    )
    records = (
        card("P", completed_at=at(20)),
        card("C"),
    )
    runs = (
        run(1, "P", ended_at=at(20), metadata=chunk_metadata("P")),
        run(2, "C", started_at=at(25), ended_at=at(26)),
    )
    events = (event(1, "P", "completed", at(20), run_id=1),)
    base_arguments = [
        "--board",
        "forge-board",
        "--graph",
        str(graph_path),
        "--from",
        at(10).isoformat(),
        "--to",
        at(50).isoformat(),
        "--operator",
        "operator",
    ]
    settings = (("1", "UTC0", "C"), ("777", "Pacific/Auckland", "en_NZ.UTF-8"))
    bundles = []
    for index, (seed, timezone, locale) in enumerate(settings):
        monkeypatch.setenv("PYTHONHASHSEED", seed)
        monkeypatch.setenv("TZ", timezone)
        monkeypatch.setenv("LC_ALL", locale)
        raw = raw_snapshot(
            cards=records if index == 0 else tuple(reversed(records)),
            runs=runs if index == 0 else tuple(reversed(runs)),
            events=events,
        )
        destination = tmp_path / f"report-{index}"
        cli.run(
            [*base_arguments, "--output", str(destination)],
            runner=_github_runner,
            snapshotter=lambda *_args, snapshot=raw: snapshot,
        )
        bundles.append(
            tuple((destination / name).read_bytes() for name in ("report.json", "report.md"))
        )
    assert bundles[0] == bundles[1]
    assert os.environ["TZ"] == settings[-1][1]
