"""Immutable domain records shared by report boundaries."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from forgeboard_report.errors import InvalidCoreError, UsageError

_BOARD_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Stable evidence identifying the exact bytes accepted from a source."""

    kind: str
    sha256: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class GraphChunk:
    """One normalized Forge contract chunk."""

    id: str
    lane: str
    depends_on: tuple[str, ...]

    def bootstrap_key(self, board_slug: str) -> str:
        """Return the exact Forge bootstrap identity for a resolved board."""
        return f"{board_slug}-{self.id}"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A declared parent-to-child dependency."""

    parent_id: str
    child_id: str


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    """Validated, deterministic graph records plus exact-byte evidence."""

    chunks: tuple[GraphChunk, ...]
    edges: tuple[GraphEdge, ...]
    fingerprint: SourceFingerprint


@dataclass(frozen=True, slots=True)
class SourceFileFingerprint:
    """Exact-byte evidence for one member of a multi-file source."""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RawHermesCard:
    """One uninterpreted Hermes 0.19 task row."""

    id: str
    title: str
    body: str | None
    assignee: str | None
    status: str
    priority: int
    created_by: str | None
    created_at: object
    started_at: object | None
    completed_at: object | None
    result: str | None
    idempotency_key: str | None
    block_kind: str | None


@dataclass(frozen=True, slots=True)
class RawHermesLink:
    """One parent-to-child row using opaque Hermes task ids."""

    parent_id: str
    child_id: str


@dataclass(frozen=True, slots=True)
class RawHermesRun:
    """One uninterpreted Hermes 0.19 attempt row."""

    id: int
    task_id: str
    profile: str | None
    step_key: str | None
    status: str
    outcome: str | None
    started_at: object
    ended_at: object | None
    summary: str | None
    metadata: str | bytes | None
    error: str | None


@dataclass(frozen=True, slots=True)
class RawHermesEvent:
    """One uninterpreted Hermes 0.19 lifecycle event."""

    id: int
    task_id: str
    run_id: int | None
    kind: str
    payload: str | bytes | None
    created_at: object


@dataclass(frozen=True, slots=True)
class RawHermesComment:
    """One uninterpreted Hermes 0.19 comment."""

    id: int
    task_id: str
    author: str
    body: str
    created_at: object


@dataclass(frozen=True, slots=True)
class RawHermesSnapshot:
    """Stable raw rows extracted from one private Hermes file snapshot."""

    board_slug: str
    cards: tuple[RawHermesCard, ...]
    links: tuple[RawHermesLink, ...]
    runs: tuple[RawHermesRun, ...]
    events: tuple[RawHermesEvent, ...]
    comments: tuple[RawHermesComment, ...]
    fingerprint: SourceFingerprint
    source_files: tuple[SourceFileFingerprint, ...]


@dataclass(frozen=True, slots=True)
class ReportRequest:
    """Validated and resolved operator inputs for one report."""

    board_slug: str
    graph_path: Path
    from_original: str
    to_original: str
    operator_ids: tuple[str, ...]
    output_directory: Path
    from_utc: datetime = field(init=False)
    to_utc: datetime = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.board_slug, str) or not _BOARD_SLUG.fullmatch(self.board_slug):
            raise UsageError(
                "board",
                "must match ^[A-Za-z0-9][A-Za-z0-9_-]*$",
            )

        object.__setattr__(self, "graph_path", Path(self.graph_path))
        object.__setattr__(self, "output_directory", Path(self.output_directory))

        operators = _validate_operators(self.operator_ids)
        object.__setattr__(self, "operator_ids", operators)

        from_utc = _parse_aware_bound("from", self.from_original)
        to_utc = _parse_aware_bound("to", self.to_original)
        if from_utc >= to_utc:
            raise UsageError("interval", "from must be earlier than to")

        object.__setattr__(self, "from_utc", from_utc)
        object.__setattr__(self, "to_utc", to_utc)

    def contains(self, occurrence: datetime) -> bool:
        """Return half-open period membership after normalizing to UTC."""
        if not isinstance(occurrence, datetime) or occurrence.utcoffset() is None:
            raise InvalidCoreError("occurrence", "timestamp must be timezone-aware")
        occurrence_utc = occurrence.astimezone(UTC)
        return self.from_utc <= occurrence_utc < self.to_utc


def _validate_operators(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        raise UsageError("operator", "must be supplied once per identity")

    try:
        operators = tuple(values)  # type: ignore[arg-type]
    except TypeError as error:
        raise UsageError("operator", "at least one identity is required") from error

    if not operators:
        raise UsageError("operator", "at least one identity is required")

    for operator in operators:
        if not isinstance(operator, str) or not operator:
            raise UsageError("operator", "identities must be nonempty strings")
        if operator != operator.strip():
            raise UsageError("operator", "identities must not have surrounding whitespace")

    if len(set(operators)) != len(operators):
        raise UsageError("operator", "duplicate identities are not allowed")

    return tuple(sorted(operators))


def _parse_aware_bound(field_name: str, value: object) -> datetime:
    if not isinstance(value, str):
        raise UsageError(field_name, "must be a timezone-aware ISO-8601 timestamp")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise UsageError(field_name, "must be a timezone-aware ISO-8601 timestamp") from error

    if parsed.utcoffset() is None:
        raise UsageError(field_name, "must be timezone-aware")

    return parsed.astimezone(UTC)
