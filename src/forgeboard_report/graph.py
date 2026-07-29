"""Exact-byte graph loading and deterministic graph normalization."""

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from forgeboard_report.domain import GraphChunk, GraphEdge, GraphSnapshot, SourceFingerprint
from forgeboard_report.errors import (
    InvalidGraphError,
    InvalidSchemaError,
    SourceUnavailableError,
)

_GRAPH_KEYS = frozenset({"id", "lane", "depends_on"})


class _DuplicateJsonKey(ValueError):
    pass


class _InvalidJsonConstant(ValueError):
    pass


def load_graph(path: str | Path) -> GraphSnapshot:
    """Read and validate one graph file without consulting external sources."""
    graph_path = Path(path)
    try:
        source_bytes = graph_path.read_bytes()
    except OSError as error:
        raise SourceUnavailableError("graph", f"cannot read {graph_path}: {error}") from error
    return load_graph_bytes(source_bytes)


def load_graph_bytes(source_bytes: bytes) -> GraphSnapshot:
    """Validate graph bytes and retain their exact SHA-256 fingerprint."""
    if not isinstance(source_bytes, bytes):
        raise InvalidSchemaError("graph", "source must be bytes")

    fingerprint = SourceFingerprint(
        kind="graph",
        sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
    records = _decode_records(source_bytes)
    chunks = _normalize_chunks(records)
    edges = tuple(
        sorted(
            (
                GraphEdge(parent_id=parent_id, child_id=chunk.id)
                for chunk in chunks
                for parent_id in chunk.depends_on
            ),
            key=lambda edge: (edge.parent_id, edge.child_id),
        )
    )
    _validate_edges(chunks, edges)
    _reject_cycles(chunks, edges)
    return GraphSnapshot(chunks=chunks, edges=edges, fingerprint=fingerprint)


def _decode_records(source_bytes: bytes) -> list[Any]:
    try:
        decoded = json.loads(
            source_bytes,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidSchemaError("graph", f"must be valid UTF-8 JSON: {error}") from error
    except _DuplicateJsonKey as error:
        raise InvalidSchemaError("graph", str(error)) from error
    except _InvalidJsonConstant as error:
        raise InvalidSchemaError("graph", str(error)) from error

    if not isinstance(decoded, list):
        raise InvalidSchemaError("graph", "root must be one JSON array")
    return decoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"object contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _InvalidJsonConstant(f"invalid JSON constant {value}")


def _normalize_chunks(records: Iterable[Any]) -> tuple[GraphChunk, ...]:
    chunks: list[GraphChunk] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        location = f"record {index}"
        if not isinstance(record, dict):
            raise InvalidSchemaError("graph", f"{location} must be an object")

        keys = frozenset(record)
        if keys != _GRAPH_KEYS:
            missing = sorted(_GRAPH_KEYS - keys)
            unknown = sorted(keys - _GRAPH_KEYS)
            details: list[str] = []
            if missing:
                details.append(f"missing keys {missing}")
            if unknown:
                details.append(f"unknown keys {unknown}")
            raise InvalidSchemaError("graph", f"{location} has {' and '.join(details)}")

        chunk_id = record["id"]
        lane = record["lane"]
        dependencies = record["depends_on"]
        if not isinstance(chunk_id, str) or not chunk_id:
            raise InvalidSchemaError("graph", f"{location} id must be a nonempty string")
        if not isinstance(lane, str) or not lane:
            raise InvalidSchemaError("graph", f"{location} lane must be a nonempty string")
        if not isinstance(dependencies, list):
            raise InvalidSchemaError("graph", f"{location} depends_on must be an array")
        if any(not isinstance(dependency, str) for dependency in dependencies):
            raise InvalidSchemaError("graph", f"{location} dependencies must be strings")
        if len(set(dependencies)) != len(dependencies):
            raise InvalidGraphError("graph", f"chunk {chunk_id!r} has duplicate dependencies")
        if chunk_id in seen_ids:
            raise InvalidGraphError("graph", f"duplicate chunk id {chunk_id!r}")

        seen_ids.add(chunk_id)
        chunks.append(
            GraphChunk(
                id=chunk_id,
                lane=lane,
                depends_on=tuple(sorted(dependencies)),
            )
        )

    return tuple(sorted(chunks, key=lambda chunk: chunk.id))


def _validate_edges(chunks: tuple[GraphChunk, ...], edges: tuple[GraphEdge, ...]) -> None:
    chunk_ids = {chunk.id for chunk in chunks}
    for edge in edges:
        if edge.parent_id == edge.child_id:
            raise InvalidGraphError("graph", f"self-edge on chunk {edge.child_id!r}")
        if edge.parent_id not in chunk_ids:
            raise InvalidGraphError(
                "graph",
                f"dependency {edge.parent_id!r} for chunk {edge.child_id!r} is missing",
            )


def _reject_cycles(chunks: tuple[GraphChunk, ...], edges: tuple[GraphEdge, ...]) -> None:
    adjacency = {chunk.id: [] for chunk in chunks}
    for edge in edges:
        adjacency[edge.parent_id].append(edge.child_id)
    for children in adjacency.values():
        children.sort()

    visited: set[str] = set()
    active: set[str] = set()
    path: list[str] = []

    def visit(chunk_id: str) -> None:
        if chunk_id in active:
            cycle_start = path.index(chunk_id)
            cycle = [*path[cycle_start:], chunk_id]
            raise InvalidGraphError("graph", f"cycle detected: {' -> '.join(cycle)}")
        if chunk_id in visited:
            return

        active.add(chunk_id)
        path.append(chunk_id)
        for child_id in adjacency[chunk_id]:
            visit(child_id)
        path.pop()
        active.remove(chunk_id)
        visited.add(chunk_id)

    for chunk in chunks:
        visit(chunk.id)
