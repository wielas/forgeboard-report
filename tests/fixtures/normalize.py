"""In-memory canonical lifecycle fixtures for CHUNK-3."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from forgeboard_report.domain import (
    GraphChunk,
    GraphEdge,
    GraphSnapshot,
    RawHermesCard,
    RawHermesComment,
    RawHermesEvent,
    RawHermesLink,
    RawHermesRun,
    RawHermesSnapshot,
    ReportRequest,
    SourceFingerprint,
    SourceSnapshot,
)
from forgeboard_report.normalize import normalize_sources

BOARD = "forge-board"
BASE = datetime(2026, 7, 29, tzinfo=UTC)


def at(minutes: int) -> datetime:
    """Return a stable aware fixture timestamp."""
    return BASE + timedelta(minutes=minutes)


def judge_metadata(
    verdict: str = "approve",
    scores: tuple[int, int, int, int, int, int] = (3, 3, 3, 3, 3, 3),
    *,
    chunk_id: str = "A",
) -> str:
    """Build one complete rubric judge envelope with all six integer scores."""
    dimensions = (
        "spec_fidelity",
        "scenario_integrity",
        "architectural_conformance",
        "scope_discipline",
        "debt_honesty",
        "doc_reconciliation",
    )
    is_ci_red_sentinel = verdict == "bounce" and all(score == 0 for score in scores)
    findings = (
        []
        if is_ci_red_sentinel
        else [
            {
                "dimension": dimension,
                "severity": (
                    "nit"
                    if score == 2 or (score == 1 and verdict == "approve-with-nits")
                    else "block"
                ),
                "evidence": f"tests/fixtures/normalize.py: {dimension} score",
                "action": f"Address the {dimension} finding.",
            }
            for dimension, score in zip(dimensions, scores, strict=True)
            if score < 3
        ]
    )
    return json.dumps(
        {
            "schema": "forge.judge.v1",
            "chunk_id": chunk_id,
            "pr": "https://github.com/acme/repo/pull/3",
            "verdict": verdict,
            "scores": {
                "spec_fidelity": scores[0],
                "scenario_integrity": scores[1],
                "architectural_conformance": scores[2],
                "scope_discipline": scores[3],
                "debt_honesty": scores[4],
                "doc_reconciliation": scores[5],
            },
            "findings": findings,
            "nits_as_cards": [],
            "spot_check_suggestion": "Inspect the densest changed normalization path.",
            "judge_model": "fixture-judge",
            "tokens_estimate": 0,
        },
        separators=(",", ":"),
    )


def block_metadata(reason_class: str) -> str:
    """Build one exact block envelope."""
    return json.dumps(
        {"schema": "forge.block.v1", "reason_class": reason_class},
        separators=(",", ":"),
    )


def chunk_metadata(chunk_id: str, pr: str = "https://github.com/acme/repo/pull/1") -> str:
    """Build one exact chunk handoff envelope."""
    return json.dumps(
        {"schema": "forge.chunk.v1", "chunk_id": chunk_id, "pr": pr},
        separators=(",", ":"),
    )


def task_id(chunk_id: str) -> str:
    return f"task-{chunk_id}"


def card(
    chunk_id: str,
    *,
    task: str | None = None,
    key: str | None = None,
    status: str | None = None,
    completed_at: object | None = None,
    block_kind: str | None = None,
) -> RawHermesCard:
    """Build a raw mapped task row."""
    if status is None:
        status = "done" if completed_at is not None else "ready"
    return RawHermesCard(
        id=task or task_id(chunk_id),
        title=f"Card {chunk_id}",
        body=f"Prose is not identity for {chunk_id}",
        assignee=None,
        status=status,
        priority=0,
        created_by="forge-bootstrap",
        created_at=at(0),
        started_at=None,
        completed_at=completed_at,
        result=None,
        idempotency_key=key if key is not None else f"{BOARD}-{chunk_id}",
        block_kind=block_kind,
    )


def run(
    run_id: int,
    chunk_id: str,
    *,
    task: str | None = None,
    status: str = "done",
    outcome: str | None = "completed",
    started_at: object | None = None,
    ended_at: object | None = None,
    metadata: str | bytes | None = None,
) -> RawHermesRun:
    """Build a raw mapped run row."""
    return RawHermesRun(
        id=run_id,
        task_id=task or task_id(chunk_id),
        profile="fixture",
        step_key=None,
        status=status,
        outcome=outcome,
        started_at=at(1) if started_at is None else started_at,
        ended_at=ended_at,
        summary="opaque prose",
        metadata=metadata,
        error=None,
    )


def event(
    event_id: int,
    chunk_id: str,
    kind: str,
    occurred_at: object,
    *,
    task: str | None = None,
    run_id: int | None = None,
    payload: str | bytes | None = None,
) -> RawHermesEvent:
    """Build a raw mapped event row."""
    return RawHermesEvent(
        id=event_id,
        task_id=task or task_id(chunk_id),
        run_id=run_id,
        kind=kind,
        payload=payload,
        created_at=occurred_at,
    )


def comment(
    comment_id: int,
    chunk_id: str,
    author: str,
    occurred_at: object,
    *,
    task: str | None = None,
) -> RawHermesComment:
    """Build a raw mapped comment row."""
    return RawHermesComment(
        id=comment_id,
        task_id=task or task_id(chunk_id),
        author=author,
        body="Opaque comment text",
        created_at=occurred_at,
    )


def request(
    *,
    board_slug: str = BOARD,
    operator_ids: tuple[str, ...] = ("operator",),
    from_minute: int = 10,
    to_minute: int = 50,
) -> ReportRequest:
    """Build the standard half-open report request."""
    return ReportRequest(
        board_slug=board_slug,
        graph_path=Path("graph.json"),
        from_original=at(from_minute).isoformat(),
        to_original=at(to_minute).isoformat(),
        operator_ids=operator_ids,
        output_directory=Path("report"),
    )


def graph(
    chunk_ids: tuple[str, ...],
    *,
    edges: tuple[tuple[str, str], ...] = (),
) -> GraphSnapshot:
    """Build an already-validated graph snapshot."""
    dependencies = {
        chunk_id: tuple(sorted(parent for parent, child in edges if child == chunk_id))
        for chunk_id in chunk_ids
    }
    return GraphSnapshot(
        chunks=tuple(
            GraphChunk(chunk_id, "fixture", dependencies[chunk_id])
            for chunk_id in sorted(chunk_ids)
        ),
        edges=tuple(GraphEdge(*edge) for edge in sorted(edges)),
        fingerprint=SourceFingerprint("graph", "a" * 64),
    )


def raw_snapshot(
    *,
    cards: tuple[RawHermesCard, ...],
    runs: tuple[RawHermesRun, ...] = (),
    events: tuple[RawHermesEvent, ...] = (),
    comments: tuple[RawHermesComment, ...] = (),
    links: tuple[RawHermesLink, ...] = (),
    board_slug: str = BOARD,
) -> RawHermesSnapshot:
    """Build one immutable raw Hermes source."""
    return RawHermesSnapshot(
        board_slug=board_slug,
        cards=cards,
        links=links,
        runs=runs,
        events=events,
        comments=comments,
        fingerprint=SourceFingerprint("hermes-board", "b" * 64, "0.19.0"),
        source_files=(),
    )


def normalize_fixture(
    chunk_ids: tuple[str, ...],
    *,
    cards: tuple[RawHermesCard, ...] | None = None,
    runs: tuple[RawHermesRun, ...] = (),
    events: tuple[RawHermesEvent, ...] = (),
    comments: tuple[RawHermesComment, ...] = (),
    links: tuple[RawHermesLink, ...] = (),
    operator_ids: tuple[str, ...] = ("operator",),
    edges: tuple[tuple[str, str], ...] = (),
) -> SourceSnapshot:
    """Normalize a compact all-memory lifecycle fixture."""
    raw = raw_snapshot(
        cards=(tuple(card(chunk_id) for chunk_id in chunk_ids) if cards is None else cards),
        runs=runs,
        events=events,
        comments=comments,
        links=links,
    )
    return normalize_sources(
        raw,
        graph(chunk_ids, edges=edges),
        request(operator_ids=operator_ids),
    )


def multiple_verdict_snapshot() -> SourceSnapshot:
    """Completed chunks with repeated verdicts and dimension-specific scores."""
    return normalize_fixture(
        ("A", "B"),
        cards=(card("A", completed_at=at(40)), card("B", completed_at=at(41))),
        runs=(
            run(
                1,
                "A",
                ended_at=at(20),
                metadata=judge_metadata("approve-with-nits", (2, 1, 2, 3, 2, 2)),
            ),
            run(
                2,
                "A",
                ended_at=at(30),
                metadata=judge_metadata("bounce", (3, 2, 1, 3, 2, 2)),
            ),
            run(
                3,
                "B",
                ended_at=at(35),
                metadata=judge_metadata(
                    "approve",
                    (2, 3, 2, 3, 2, 2),
                    chunk_id="B",
                ),
            ),
        ),
    )


def no_verdict_snapshots() -> tuple[SourceSnapshot, SourceSnapshot]:
    """One missing-coverage snapshot and one zero-denominator snapshot."""
    missing = normalize_fixture(
        ("A",),
        cards=(card("A", completed_at=at(40)),),
    )
    empty = normalize_fixture(("A",))
    return missing, empty


def block_snapshot() -> SourceSnapshot:
    """Paired, unmatched, canonical, native, and unknown block evidence."""
    return normalize_fixture(
        ("A",),
        cards=(card("A", status="blocked", block_kind="dependency"),),
        runs=(
            run(
                10,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(21),
                metadata=block_metadata("needs-human"),
            ),
            run(
                11,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(30),
                metadata='{"schema":"future.block.v2"}',
            ),
        ),
        events=(
            event(
                20,
                "A",
                "blocked",
                at(20),
                run_id=10,
                payload='{"kind":"needs_input","reason":"prose only"}',
            ),
            event(
                21,
                "A",
                "blocked",
                at(40),
                payload='{"kind":"transient","reason":"still prose"}',
            ),
        ),
    )


def conflicting_block_raw() -> tuple[RawHermesSnapshot, GraphSnapshot, ReportRequest]:
    """Duplicate occurrence rows carrying contradictory canonical reasons."""
    raw = raw_snapshot(
        cards=(card("A", status="blocked"),),
        runs=(
            run(
                10,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(20),
                metadata=block_metadata("one"),
            ),
            run(
                10,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(20),
                metadata=block_metadata("two"),
            ),
        ),
    )
    return raw, graph(("A",)), request()


def intervention_snapshot() -> SourceSnapshot:
    """Every actor class plus valid, invalid, and tied causal comments."""
    return normalize_fixture(
        ("A",),
        cards=(card("A", completed_at=at(45)),),
        runs=(
            run(
                30,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(16),
            ),
        ),
        events=(
            event(30, "A", "claimed", at(5)),
            event(31, "A", "blocked", at(15), run_id=30),
            event(32, "A", "claimed", at(30)),
            event(33, "A", "completed", at(45)),
        ),
        comments=(
            comment(30, "A", "operator", at(10)),
            comment(31, "A", "forge-codex-lane", at(11)),
            comment(32, "A", "forge-prejudge", at(12)),
            comment(33, "A", "forge-orchestrator", at(13)),
            comment(34, "A", "mystery", at(14)),
            comment(35, "A", "operator", at(20)),
            comment(36, "A", "operator", at(45)),
            comment(37, "A", "operator", at(46)),
        ),
    )


def boundary_snapshot() -> SourceSnapshot:
    """Both interval bounds, context-only history, and causal timestamp ties."""
    return normalize_fixture(
        ("BOUNDARY", "LOWER", "TIE", "UPPER"),
        cards=(
            card("BOUNDARY", completed_at=at(55)),
            card("LOWER", completed_at=at(10)),
            card("TIE", completed_at=at(45)),
            card("UPPER", completed_at=at(50)),
        ),
        runs=(
            run(
                40,
                "LOWER",
                ended_at=at(10),
                metadata=judge_metadata(
                    "approve-with-nits",
                    (1, 2, 3, 3, 2, 2),
                    chunk_id="LOWER",
                ),
            ),
            run(
                41,
                "UPPER",
                ended_at=at(50),
                metadata=judge_metadata(
                    "bounce",
                    (1, 3, 3, 3, 2, 2),
                    chunk_id="UPPER",
                ),
            ),
            run(
                42,
                "BOUNDARY",
                status="blocked",
                outcome="blocked",
                ended_at=at(16),
            ),
            run(
                43,
                "TIE",
                status="blocked",
                outcome="blocked",
                ended_at=at(30),
            ),
        ),
        events=(
            event(40, "BOUNDARY", "claimed", at(5)),
            event(41, "BOUNDARY", "blocked", at(15), run_id=42),
            event(42, "BOUNDARY", "claimed", at(50)),
            event(43, "BOUNDARY", "completed", at(55)),
            event(44, "TIE", "claimed", at(20)),
            event(45, "TIE", "blocked", at(30), run_id=43),
            event(46, "TIE", "claimed", at(40)),
            event(47, "TIE", "completed", at(45)),
        ),
        comments=(
            comment(40, "BOUNDARY", "operator", at(20)),
            comment(41, "TIE", "operator", at(30)),
        ),
    )
