"""BDD coverage for the signed command boundary."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

import forgeboard_report.cli as cli
from fixtures.normalize import at, card, chunk_metadata, event, raw_snapshot, run
from forgeboard_report import publish
from forgeboard_report.errors import (
    PublicationRenameError,
    PublicationWriteError,
    SourceUnavailableError,
)

scenarios("../features/report_command.feature")


def _runner(arguments, **_kwargs):
    args = tuple(arguments)
    if args == ("gh", "--version"):
        return subprocess.CompletedProcess(args, 0, "gh version 2.96.0\n", "")
    assert args[:3] == ("gh", "api", "graphql")
    assert "query=" in args[-1] and "mutation" not in args[-1].lower()
    payload = {
        "data": {
            "pr0": {
                "pullRequest": {
                    "id": "NODE-1",
                    "url": "https://github.com/acme/repo/pull/1",
                    "number": 1,
                    "state": "MERGED",
                    "mergedAt": at(20).isoformat(),
                }
            },
            "pr1": {
                "pullRequest": {
                    "id": "NODE-2",
                    "url": "https://github.com/acme/repo/pull/2",
                    "number": 2,
                    "state": "OPEN",
                    "mergedAt": None,
                }
            },
        }
    }
    return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


def _case(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(
        '[{"id":"P","lane":"fixture","depends_on":[]},'
        '{"id":"Q","lane":"fixture","depends_on":[]},'
        '{"id":"C","lane":"fixture","depends_on":["P","Q"]}]\n',
        encoding="utf-8",
    )
    raw = raw_snapshot(
        cards=(card("P", completed_at=at(20)), card("Q", completed_at=at(20)), card("C")),
        runs=(
            run(1, "P", ended_at=at(20), metadata=chunk_metadata("P")),
            run(
                2,
                "Q",
                ended_at=at(20),
                metadata=chunk_metadata("Q", "https://github.com/acme/repo/pull/2"),
            ),
            run(3, "C", started_at=at(25), ended_at=at(26)),
            run(4, "C", metadata='{"schema":"future.fixture.v1"}'),
        ),
        events=(
            event(1, "P", "completed", at(20), run_id=1),
            event(2, "Q", "completed", at(20), run_id=2),
        ),
        board_slug="forge-board",
    )
    arguments = [
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
        "--output",
        str(tmp_path / "report"),
    ]
    return {"arguments": arguments, "raw": raw, "destination": tmp_path / "report"}


def _unavailable_gh_runner(arguments, **_kwargs):
    raise FileNotFoundError("gh")


def _old_gh_runner(arguments, **_kwargs):
    args = tuple(arguments)
    assert args == ("gh", "--version")
    return subprocess.CompletedProcess(args, 0, "gh version 2.95.0\n", "")


def _malformed_github_runner(arguments, **_kwargs):
    args = tuple(arguments)
    if args == ("gh", "--version"):
        return subprocess.CompletedProcess(args, 0, "gh version 2.96.0\n", "")
    assert args[:3] == ("gh", "api", "graphql")
    return subprocess.CompletedProcess(args, 0, "{not json", "")


def _unavailable_snapshotter(*_args):
    raise SourceUnavailableError("Hermes board", "unknown board")


@given(
    "a valid recorded board, graph, exact operators, aware period, and fake "
    "merged/unmerged PR facts",
    target_fixture="command_case",
)
def valid_command_case(tmp_path: Path):
    return _case(tmp_path)


@when("the public command runs")
def public_command_runs(command_case) -> None:
    cli.run(
        command_case["arguments"],
        runner=_runner,
        snapshotter=lambda *_args: command_case["raw"],
    )


@then(
    "exactly two complete artifacts contain resolved fingerprints, all canonical metrics, "
    "dependency findings, warnings, and traceable stable ids"
)
def complete_traceable_artifacts(command_case) -> None:
    artifacts = sorted(command_case["destination"].iterdir())
    assert [artifact.name for artifact in artifacts] == ["report.json", "report.md"]
    contents = [artifact.read_bytes() for artifact in artifacts]
    assert all(value.endswith(b"\n") for value in contents)
    data = json.loads(contents[0])
    markdown = contents[1].decode("utf-8")
    assert data["inputs"]["board_slug"] == "forge-board"
    assert data["sources"]["graph"]["sha256"]
    assert set(data["metrics"]) == {
        "bounce_rate",
        "judge_quality",
        "blocked_work",
        "operator_intervention",
    }
    assert {edge["pull_request_id"] for edge in data["dependency_audit"]["edges"]} == {
        "github:node:NODE-1",
        "github:node:NODE-2",
    }
    assert data["warnings"] and data["evidence"]
    assert "github:node:NODE-1" in markdown and "run:3" in markdown


@given(
    "invalid board slug, invalid ISO date, missing operator, or existing output directory",
    target_fixture="failing_case",
)
def invalid_input_case(tmp_path: Path):
    case = _case(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    missing_operator = [
        value for index, value in enumerate(case["arguments"]) if index not in (8, 9)
    ]
    missing_period = [value for index, value in enumerate(case["arguments"]) if index not in (4, 5)]
    missing_to = [value for index, value in enumerate(case["arguments"]) if index not in (6, 7)]
    return {
        "failures": (
            {"arguments": [*case["arguments"][:1], "bad/slash", *case["arguments"][2:]]},
            {"arguments": [*case["arguments"][:5], "not-a-date", *case["arguments"][6:]]},
            {"arguments": [*case["arguments"][:-1], str(existing)]},
            {"arguments": missing_operator},
            {"arguments": missing_period},
            {"arguments": missing_to},
        ),
        "destinations": (case["destination"],) * 5 + (existing,),
        "runner": _runner,
        "snapshotter": lambda *_args: case["raw"],
    }


@given(
    "an unknown board, missing graph file, or unavailable GitHub CLI",
    target_fixture="failing_case",
)
def unavailable_case(tmp_path: Path):
    case = _case(tmp_path)
    missing_graph = tmp_path / "missing.json"
    return {
        "failures": (
            {"arguments": case["arguments"], "snapshotter": _unavailable_snapshotter},
            {"arguments": [*case["arguments"][:3], str(missing_graph), *case["arguments"][4:]]},
            {"arguments": case["arguments"], "runner": _unavailable_gh_runner},
            {"arguments": case["arguments"], "runner": _old_gh_runner},
        ),
        "destinations": (case["destination"],) * 4,
        "runner": _runner,
        "snapshotter": lambda *_args: case["raw"],
    }


@given("a cyclic graph or malformed canonical evidence", target_fixture="failing_case")
def invalid_core_case(tmp_path: Path):
    case = _case(tmp_path)
    cyclic = tmp_path / "cyclic.json"
    cyclic.write_text(
        '[{"id":"A","lane":"fixture","depends_on":["B"]},'
        '{"id":"B","lane":"fixture","depends_on":["A"]}]\n',
        encoding="utf-8",
    )
    malformed_snapshot = replace(
        case["raw"],
        runs=(
            run(
                1,
                "P",
                ended_at=at(20),
                metadata='{"schema":"forge.chunk.v1","chunk_id":"P"}',
            ),
            *case["raw"].runs[1:],
        ),
    )
    return {
        "failures": (
            {"arguments": [*case["arguments"][:3], str(cyclic), *case["arguments"][4:]]},
            {
                "arguments": case["arguments"],
                "snapshotter": lambda *_args: malformed_snapshot,
            },
            {"arguments": case["arguments"], "runner": _malformed_github_runner},
        ),
        "destinations": (case["destination"],) * 3,
        "runner": _runner,
        "snapshotter": lambda *_args: case["raw"],
    }


@given("a simulated publication failure", target_fixture="failing_case")
def publication_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = _case(tmp_path)
    failures = (PublicationWriteError("disk full"), PublicationRenameError("cross-device rename"))
    remaining = iter(failures)
    monkeypatch.setattr(
        publish,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(next(remaining)),
    )
    return {
        "failures": ({"arguments": case["arguments"]},) * len(failures),
        "destinations": (case["destination"],) * len(failures),
        "runner": _runner,
        "snapshotter": lambda *_args: case["raw"],
    }


@when("the command runs")
def failing_command_runs(failing_case, capsys) -> None:
    results = []
    for failure, destination in zip(
        failing_case["failures"], failing_case["destinations"], strict=True
    ):
        exit_code = cli.main(
            failure["arguments"],
            runner=failure.get("runner", failing_case["runner"]),
            snapshotter=failure.get("snapshotter", failing_case["snapshotter"]),
        )
        results.append((exit_code, capsys.readouterr().err, destination))
    failing_case["results"] = tuple(results)


@then("it exits 2 with a diagnostic on stderr and no report directory")
def usage_exit(failing_case) -> None:
    _assert_failures(failing_case, 2)


@then("it exits 3 with a diagnostic on stderr and no report directory")
def unavailable_exit(failing_case) -> None:
    _assert_failures(failing_case, 3)


@then("it exits 4 with a diagnostic on stderr and no report directory")
def core_exit(failing_case) -> None:
    _assert_failures(failing_case, 4)


@then("it exits 5 with a diagnostic on stderr and no report directory")
def publication_exit(failing_case) -> None:
    _assert_failures(failing_case, 5)


def _assert_failures(failing_case, expected_exit_code: int) -> None:
    assert failing_case["results"]
    for exit_code, stderr, destination in failing_case["results"]:
        assert exit_code == expected_exit_code
        assert stderr
        assert not (destination / "report.json").exists()
        assert not (destination / "report.md").exists()
