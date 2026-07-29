"""BDD coverage for request resolution and graph normalization."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from forgeboard_report.domain import ReportRequest
from forgeboard_report.errors import InvalidCoreError, InvalidGraphError, UsageError
from forgeboard_report.graph import load_graph_bytes

scenarios("../features/request_and_graph.feature")


def _request(**overrides: object) -> ReportRequest:
    values: dict[str, object] = {
        "board_slug": "Forge_Board-7",
        "graph_path": Path("docs/chunks/graph.json"),
        "from_original": "2026-07-29T08:00:00+02:00",
        "to_original": "2026-07-29T10:00:00+02:00",
        "operator_ids": ("zeta", "Alpha"),
        "output_directory": Path("reports/period"),
    }
    values.update(overrides)
    return ReportRequest(**values)  # type: ignore[arg-type]


def _graph_bytes(records: object, *, indent: int | None = None) -> bytes:
    return json.dumps(records, indent=indent, separators=None if indent else (",", ":")).encode()


@given(
    "a valid board, exact operators, aware bounds, and an acyclic graph",
    target_fixture="valid_inputs",
)
def valid_inputs() -> tuple[dict[str, object], bytes]:
    request_values = {
        "board_slug": "Forge_Board-7",
        "graph_path": Path("docs/chunks/graph.json"),
        "from_original": "2026-07-29T08:00:00+02:00",
        "to_original": "2026-07-29T10:00:00+02:00",
        "operator_ids": ("zeta", "Alpha"),
        "output_directory": Path("reports/period"),
    }
    graph_bytes = _graph_bytes(
        [
            {"id": "CHUNK-2", "lane": "lane-b", "depends_on": ["CHUNK-1"]},
            {"id": "CHUNK-1", "lane": "lane-a", "depends_on": []},
        ],
        indent=2,
    )
    return request_values, graph_bytes


@when(
    "the request and graph inputs are resolved",
    target_fixture="resolved_inputs",
)
def resolve_valid_inputs(valid_inputs) -> tuple[ReportRequest, object, bytes]:
    request_values, source_bytes = valid_inputs
    return ReportRequest(**request_values), load_graph_bytes(source_bytes), source_bytes


@then("the original inputs and normalized graph evidence are retained")
def assert_resolved_evidence(resolved_inputs) -> None:
    request, graph, source_bytes = resolved_inputs
    assert request.board_slug == "Forge_Board-7"
    assert request.from_original == "2026-07-29T08:00:00+02:00"
    assert request.to_original == "2026-07-29T10:00:00+02:00"
    assert request.from_utc == datetime(2026, 7, 29, 6, tzinfo=UTC)
    assert request.to_utc == datetime(2026, 7, 29, 8, tzinfo=UTC)
    assert request.operator_ids == ("Alpha", "zeta")
    assert [chunk.id for chunk in graph.chunks] == ["CHUNK-1", "CHUNK-2"]
    assert graph.chunks[0].lane == "lane-a"
    assert graph.chunks[1].bootstrap_key(request.board_slug) == "Forge_Board-7-CHUNK-2"
    assert [(edge.parent_id, edge.child_id) for edge in graph.edges] == [("CHUNK-1", "CHUNK-2")]
    assert graph.fingerprint.sha256 == hashlib.sha256(source_bytes).hexdigest()


@then("interval membership includes the lower bound and excludes the upper bound")
def assert_half_open_membership(resolved_inputs) -> None:
    request, _, _ = resolved_inputs
    assert request.contains(datetime(2026, 7, 29, 6, tzinfo=UTC)) is True
    assert request.contains(datetime(2026, 7, 29, 8, tzinfo=UTC)) is False


@given(
    "naïve, unordered, invalid-operator, and traversal-shaped request inputs",
    target_fixture="invalid_requests",
)
def invalid_requests():
    return [
        ("from", {"from_original": "2026-07-29T08:00:00"}),
        (
            "interval",
            {
                "from_original": "2026-07-29T10:00:00+02:00",
                "to_original": "2026-07-29T10:00:00+02:00",
            },
        ),
        (
            "interval",
            {
                "from_original": "2026-07-29T11:00:00+02:00",
                "to_original": "2026-07-29T10:00:00+02:00",
            },
        ),
        ("operator", {"operator_ids": ("",)}),
        ("operator", {"operator_ids": (" Alpha",)}),
        ("operator", {"operator_ids": ("Alpha", "Alpha")}),
        ("operator", {"operator_ids": ()}),
        ("board", {"board_slug": "../forge"}),
    ]


@when("each invalid request is resolved", target_fixture="request_errors")
def resolve_invalid_requests(invalid_requests) -> list[tuple[str, UsageError]]:
    errors = []
    for expected_field, overrides in invalid_requests:
        with pytest.raises(UsageError) as raised:
            _request(**overrides)
        errors.append((expected_field, raised.value))
    return errors


@then("every request fails with a usage error naming its invalid field")
def assert_request_errors(request_errors) -> None:
    for expected_field, error in request_errors:
        assert error.field == expected_field
        assert str(error).startswith(f"{expected_field}:")


@given(
    "graphs with duplicate chunks, duplicate dependencies, missing endpoints, "
    "self-edges, and cycles",
    target_fixture="invalid_graphs",
)
def invalid_graphs() -> list[bytes]:
    return [
        _graph_bytes(
            [
                {"id": "A", "lane": "lane", "depends_on": []},
                {"id": "A", "lane": "lane", "depends_on": []},
            ]
        ),
        _graph_bytes(
            [
                {"id": "A", "lane": "lane", "depends_on": []},
                {"id": "B", "lane": "lane", "depends_on": ["A", "A"]},
            ]
        ),
        _graph_bytes([{"id": "A", "lane": "lane", "depends_on": ["missing"]}]),
        _graph_bytes([{"id": "A", "lane": "lane", "depends_on": ["A"]}]),
        _graph_bytes(
            [
                {"id": "A", "lane": "lane", "depends_on": ["C"]},
                {"id": "B", "lane": "lane", "depends_on": ["A"]},
                {"id": "C", "lane": "lane", "depends_on": ["B"]},
            ]
        ),
    ]


@when("each invalid graph is loaded", target_fixture="graph_errors")
def load_invalid_graphs(invalid_graphs) -> list[InvalidCoreError]:
    errors = []
    for source_bytes in invalid_graphs:
        with pytest.raises(InvalidCoreError) as raised:
            load_graph_bytes(source_bytes)
        errors.append(raised.value)
    return errors


@then("every graph fails with a specific invalid-core error")
def assert_graph_errors(graph_errors) -> None:
    assert all(type(error) is InvalidGraphError for error in graph_errors)
    assert all(error.source == "graph" for error in graph_errors)


@given(
    "equivalent valid graph records in different input orders",
    target_fixture="equivalent_graphs",
)
def equivalent_graphs() -> tuple[bytes, bytes]:
    first = _graph_bytes(
        [
            {"id": "C", "lane": "third", "depends_on": ["B", "A"]},
            {"id": "A", "lane": "first", "depends_on": []},
            {"id": "B", "lane": "second", "depends_on": ["A"]},
        ]
    )
    second = _graph_bytes(
        [
            {"lane": "second", "depends_on": ["A"], "id": "B"},
            {"depends_on": [], "id": "A", "lane": "first"},
            {"depends_on": ["A", "B"], "lane": "third", "id": "C"},
        ],
        indent=2,
    )
    return first, second


@when("both graph byte sequences are loaded", target_fixture="normalized_graphs")
def load_equivalent_graphs(equivalent_graphs):
    first, second = equivalent_graphs
    return load_graph_bytes(first), load_graph_bytes(second), first, second


@then("normalized chunks and edges have identical iteration order")
def assert_equivalent_normalization(normalized_graphs) -> None:
    first_graph, second_graph, _, _ = normalized_graphs
    assert first_graph.chunks == second_graph.chunks
    assert first_graph.edges == second_graph.edges


@then("each graph fingerprint identifies its exact source bytes")
def assert_exact_fingerprints(normalized_graphs) -> None:
    first_graph, second_graph, first_bytes, second_bytes = normalized_graphs
    assert first_graph.fingerprint.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert second_graph.fingerprint.sha256 == hashlib.sha256(second_bytes).hexdigest()
    assert first_graph.fingerprint != second_graph.fingerprint
