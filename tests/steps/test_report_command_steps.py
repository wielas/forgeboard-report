"""BDD coverage for the signed command boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

import forgeboard_report.cli as cli
from fixtures.normalize import at, card, chunk_metadata, event, raw_snapshot, run
from forgeboard_report.errors import (
    InvalidCoreError,
    PublicationWriteError,
    SourceUnavailableError,
    UsageError,
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
    case["arguments"][1] = "bad/slash"
    case["error"] = UsageError("board", "invalid")
    return case


@given(
    "an unknown board, missing graph file, or unavailable GitHub CLI",
    target_fixture="failing_case",
)
def unavailable_case(tmp_path: Path):
    case = _case(tmp_path)
    case["error"] = SourceUnavailableError("github cli", "unavailable")
    return case


@given("a cyclic graph or malformed canonical evidence", target_fixture="failing_case")
def invalid_core_case(tmp_path: Path):
    case = _case(tmp_path)
    case["error"] = InvalidCoreError("graph", "cycle")
    return case


@given("a simulated publication failure", target_fixture="failing_case")
def publication_case(tmp_path: Path):
    case = _case(tmp_path)
    case["error"] = PublicationWriteError("disk full")
    return case


@when("the command runs")
def failing_command_runs(failing_case, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    error = failing_case["error"]
    monkeypatch.setattr(cli, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    failing_case["exit_code"] = cli.main(failing_case["arguments"])
    failing_case["stderr"] = capsys.readouterr().err


@then("it exits 2 with a diagnostic on stderr and no report directory")
def usage_exit(failing_case) -> None:
    assert failing_case["exit_code"] == 2
    assert failing_case["stderr"] and not failing_case["destination"].exists()


@then("it exits 3 with a diagnostic on stderr and no report directory")
def unavailable_exit(failing_case) -> None:
    assert failing_case["exit_code"] == 3
    assert failing_case["stderr"] and not failing_case["destination"].exists()


@then("it exits 4 with a diagnostic on stderr and no report directory")
def core_exit(failing_case) -> None:
    assert failing_case["exit_code"] == 4
    assert failing_case["stderr"] and not failing_case["destination"].exists()


@then("it exits 5 with a diagnostic on stderr and no report directory")
def publication_exit(failing_case) -> None:
    assert failing_case["exit_code"] == 5
    assert failing_case["stderr"] and not failing_case["destination"].exists()
