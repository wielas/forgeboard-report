"""Scale gates for the pure core and bounded live orchestration."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from fixtures.normalize import at, card, chunk_metadata, event, graph, raw_snapshot, request, run
from forgeboard_report import cli
from forgeboard_report.metrics import calculate
from forgeboard_report.normalize import normalize_sources


@pytest.fixture
def scale_sources():
    chunk_ids = tuple(f"C{index:03}" for index in range(100))
    raw = raw_snapshot(
        cards=tuple(card(chunk_id, completed_at=at(40)) for chunk_id in chunk_ids),
        runs=tuple(
            run(index + 1, chunk_ids[index % len(chunk_ids)], ended_at=at(30))
            for index in range(300)
        ),
        events=tuple(
            event(index + 1, chunk_ids[index % len(chunk_ids)], "claimed", at(index % 40))
            for index in range(1000)
        ),
    )
    return raw, graph(chunk_ids), request()


def test_pure_core_under_one_second(scale_sources) -> None:
    raw, graph_snapshot, report_request = scale_sources
    started = time.perf_counter()
    snapshot = normalize_sources(raw, graph_snapshot, report_request)
    calculate(snapshot)
    assert time.perf_counter() - started < 1


class _ModeledClock:
    def __init__(self) -> None:
        self.value = 0.0

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_modeled_live_under_thirty_seconds(tmp_path: Path) -> None:
    chunk_ids = tuple(f"C{index:03}" for index in range(100))
    records = [
        {
            "id": chunk_id,
            "lane": "fixture",
            "depends_on": [] if index == 0 else [chunk_ids[index - 1]],
        }
        for index, chunk_id in enumerate(chunk_ids)
    ]
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(records), encoding="utf-8")
    raw = raw_snapshot(
        cards=tuple(card(chunk_id, completed_at=at(20)) for chunk_id in chunk_ids),
        runs=tuple(
            run(
                index + 1,
                chunk_id,
                ended_at=at(20),
                metadata=chunk_metadata(chunk_id) if index < len(chunk_ids) - 1 else None,
            )
            for index, chunk_id in enumerate(chunk_ids)
        ),
        events=tuple(
            event(index + 1, chunk_id, "completed", at(20), run_id=index + 1)
            for index, chunk_id in enumerate(chunk_ids)
        ),
    )
    clock = _ModeledClock()

    def runner(arguments, **_kwargs):
        clock.advance(5)
        args = tuple(arguments)
        if args == ("gh", "--version"):
            return subprocess.CompletedProcess(args, 0, "gh version 2.96.0\n", "")
        assert args[:3] == ("gh", "api", "graphql") and "mutation" not in args[-1].lower()
        data = {
            "data": {
                "pr0": {
                    "pullRequest": {
                        "id": "NODE-1",
                        "url": "https://github.com/acme/repo/pull/1",
                        "number": 1,
                        "state": "MERGED",
                        "mergedAt": at(20).isoformat(),
                    }
                }
            }
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    cli.run(
        [
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
        ],
        runner=runner,
        snapshotter=lambda *_args: raw,
    )
    assert clock.value < 30
    assert sorted(path.name for path in (tmp_path / "report").iterdir()) == [
        "report.json",
        "report.md",
    ]
