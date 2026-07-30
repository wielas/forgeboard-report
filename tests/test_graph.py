"""Focused tests for graph schema, request immutability, and typed failures."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forgeboard_report.domain import ReportRequest
from forgeboard_report.errors import (
    InvalidCoreError,
    InvalidGraphError,
    InvalidSchemaError,
    PublicationError,
    SourceInconsistentError,
    SourceUnavailableError,
    UsageError,
)
from forgeboard_report.graph import load_graph, load_graph_bytes


def _bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {"id": "A", "lane": "lane", "depends_on": []}
    record.update(changes)
    return record


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (_bytes({"id": "A"}), "root must be one JSON array"),
        (_bytes([None]), "record 0 must be an object"),
        (_bytes([{"id": "A", "lane": "lane"}]), "missing keys"),
        (_bytes([_record(extra=True)]), "unknown keys"),
        (_bytes([_record(id="")]), "id must be a nonempty string"),
        (_bytes([_record(id=7)]), "id must be a nonempty string"),
        (_bytes([_record(lane="")]), "lane must be a nonempty string"),
        (_bytes([_record(lane=False)]), "lane must be a nonempty string"),
        (_bytes([_record(depends_on="A")]), "depends_on must be an array"),
        (_bytes([_record(depends_on=[1])]), "dependencies must be strings"),
        (b'{"unterminated":', "must be valid UTF-8 JSON"),
        (b"\xff", "must be valid UTF-8 JSON"),
        (b'[{"id":"A","id":"B","lane":"lane","depends_on":[]}]', "duplicate key"),
        (b'[{"id":"A","lane":"lane","depends_on":[],"value":NaN}]', "invalid JSON constant"),
    ],
)
def test_schema_errors_are_specific(source: bytes, message: str) -> None:
    with pytest.raises(InvalidSchemaError, match=message) as raised:
        load_graph_bytes(source)
    assert raised.value.source == "graph"


def test_graph_source_must_be_exact_bytes() -> None:
    with pytest.raises(InvalidSchemaError, match="source must be bytes"):
        load_graph_bytes(bytearray(b"[]"))  # type: ignore[arg-type]


def test_empty_graph_is_valid_and_immutable() -> None:
    graph = load_graph_bytes(b"[]")
    assert graph.chunks == ()
    assert graph.edges == ()
    assert graph.fingerprint.kind == "graph"
    assert graph.fingerprint.version is None
    with pytest.raises(FrozenInstanceError):
        graph.chunks = ()  # type: ignore[misc]


def test_graph_normalizes_dependencies_chunks_and_edges() -> None:
    graph = load_graph_bytes(
        _bytes(
            [
                _record(id="C", lane="L3", depends_on=["B", "A"]),
                _record(id="B", lane="L2", depends_on=["A"]),
                _record(id="A", lane="L1"),
            ]
        )
    )
    assert graph.chunks[2].depends_on == ("A", "B")
    assert graph.edges == tuple(
        sorted(graph.edges, key=lambda edge: (edge.parent_id, edge.child_id))
    )


@pytest.mark.parametrize(
    ("records", "message"),
    [
        ([_record(), _record()], "duplicate chunk id"),
        ([_record(), _record(id="B", depends_on=["A", "A"])], "duplicate dependencies"),
        ([_record(depends_on=["missing"])], "is missing"),
        ([_record(depends_on=["A"])], "self-edge"),
        (
            [
                _record(id="A", depends_on=["C"]),
                _record(id="B", depends_on=["A"]),
                _record(id="C", depends_on=["B"]),
            ],
            "cycle detected: A -> B -> C -> A",
        ),
    ],
)
def test_graph_invariant_errors_are_specific(records, message: str) -> None:
    with pytest.raises(InvalidGraphError, match=message):
        load_graph_bytes(_bytes(records))


def test_load_graph_reads_path_and_reports_missing_source(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.json"
    graph_path.write_bytes(_bytes([_record()]))
    assert load_graph(graph_path).chunks[0].id == "A"

    missing = tmp_path / "missing.json"
    with pytest.raises(SourceUnavailableError, match="cannot read") as raised:
        load_graph(missing)
    assert raised.value.source == "graph"


def test_report_request_is_frozen_sorted_and_uses_utc_membership() -> None:
    request = ReportRequest(
        board_slug="Board_1",
        graph_path="graph.json",  # type: ignore[arg-type]
        from_original="2026-01-02T01:30:00+01:30",
        to_original="2026-01-02T03:30:00+01:30",
        operator_ids=["z", "A"],  # type: ignore[arg-type]
        output_directory="reports/one",  # type: ignore[arg-type]
    )
    assert request.graph_path == Path("graph.json")
    assert request.output_directory == Path("reports/one")
    assert request.operator_ids == ("A", "z")
    assert request.contains(datetime(2026, 1, 2, 0, tzinfo=UTC))
    assert not request.contains(datetime(2026, 1, 2, 2, tzinfo=UTC))
    with pytest.raises(FrozenInstanceError):
        request.board_slug = "other"  # type: ignore[misc]
    with pytest.raises(InvalidCoreError, match="timezone-aware"):
        request.contains(datetime(2026, 1, 2))


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"board_slug": ""}, "board"),
        ({"board_slug": "-board"}, "board"),
        ({"board_slug": "board/name"}, "board"),
        ({"operator_ids": "one"}, "operator"),
        ({"operator_ids": None}, "operator"),
        ({"operator_ids": (7,)}, "operator"),
        ({"operator_ids": ("name ",)}, "operator"),
        ({"operator_ids": ("name`tag",)}, "operator"),
        ({"operator_ids": ("name\ntag",)}, "operator"),
        ({"operator_ids": ("name\rtag",)}, "operator"),
        ({"from_original": 7}, "from"),
        ({"from_original": "not-a-time"}, "from"),
        ({"to_original": "2026-01-02T00:00:00"}, "to"),
    ],
)
def test_report_request_usage_errors_name_the_field(changes, field: str) -> None:
    values = {
        "board_slug": "board",
        "graph_path": Path("graph.json"),
        "from_original": "2026-01-01T00:00:00Z",
        "to_original": "2026-01-02T00:00:00Z",
        "operator_ids": ("operator",),
        "output_directory": Path("report"),
    }
    values.update(changes)
    with pytest.raises(UsageError) as raised:
        ReportRequest(**values)
    assert raised.value.field == field


def test_boundary_errors_retain_actionable_context() -> None:
    inconsistent = SourceInconsistentError("board", "changed during capture")
    invalid = InvalidCoreError("run:7", "contradictory values")
    published = PublicationError("rename", "destination raced")

    assert inconsistent.source == "board"
    assert inconsistent.detail == "changed during capture"
    assert invalid.source == "run:7"
    assert published.stage == "rename"
    assert str(published) == "rename: destination raced"
