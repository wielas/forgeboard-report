"""Immutable domain records shared by report boundaries."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

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


class ActorClass(StrEnum):
    """Exact author classifications used by intervention findings."""

    OPERATOR = "operator"
    WORKER = "worker"
    PREJUDGE = "prejudge"
    AUTOMATED = "automated"
    UNCLASSIFIED = "unclassified"


class EvidenceRole(StrEnum):
    """Whether evidence contributes to an aggregate or only establishes context."""

    CONTRIBUTING = "contributing"
    CONTEXT = "context"


class JudgeOutcome(StrEnum):
    """Canonical outcomes accepted from ``forge.judge.v1``."""

    PASS = "pass"
    BOUNCE = "bounce"


class Availability(StrEnum):
    """Explicit value availability; absence is never represented as numeric zero."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One stable, namespaced reference to normalized source evidence."""

    id: str
    source_id: str
    occurred_at: datetime | None
    role: EvidenceRole


@dataclass(frozen=True, slots=True)
class NormalizationWarning:
    """Typed noncanonical evidence that was retained without being guessed."""

    code: str
    evidence_id: str
    schema: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class BoardCard:
    """One graph chunk joined to its exact opaque Hermes task."""

    chunk_id: str
    task_id: str
    idempotency_key: str
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    native_block_kind: str | None
    evidence_id: str


@dataclass(frozen=True, slots=True)
class BoardLink:
    """One Hermes parent link, retaining endpoints outside the graph too."""

    parent_chunk_id: str | None
    child_chunk_id: str | None
    parent_task_id: str
    child_task_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class NormalizedRun:
    """One mapped run with UTC timestamps and its declared metadata schema."""

    id: int
    chunk_id: str
    task_id: str
    profile: str | None
    step_key: str | None
    status: str
    outcome: str | None
    started_at: datetime
    ended_at: datetime | None
    metadata_schema: str | None
    evidence_id: str


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """One mapped lifecycle event with a UTC occurrence timestamp."""

    id: int
    chunk_id: str
    task_id: str
    run_id: int | None
    kind: str
    occurred_at: datetime
    evidence_id: str


@dataclass(frozen=True, slots=True)
class NormalizedComment:
    """One mapped comment classified only by its exact author."""

    id: int
    chunk_id: str
    task_id: str
    author: str
    actor_class: ActorClass
    occurred_at: datetime
    evidence_id: str


@dataclass(frozen=True, slots=True)
class JudgeScores:
    """The three independent finite Decimal judge dimensions."""

    spec_fidelity: Decimal
    scenario_integrity: Decimal
    architectural_conformance: Decimal


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """One canonical judge verdict sourced from a finished mapped run."""

    id: str
    chunk_id: str
    task_id: str
    run_id: int
    outcome: JudgeOutcome
    scores: JudgeScores
    occurred_at: datetime
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChunkHandoff:
    """One structurally valid ``forge.chunk.v1`` envelope."""

    id: str
    chunk_id: str
    task_id: str
    run_id: int
    pr: str
    occurred_at: datetime | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """A canonical pull-request location derived from a chunk handoff."""

    host: str
    owner: str
    repository: str
    number: int
    url: str

    @classmethod
    def parse(cls, value: str) -> "PullRequestRef":
        """Parse the one permitted HTTPS pull-request URL shape."""
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("URL must not contain whitespace")
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise ValueError("URL is malformed") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.netloc != parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("URL must be an HTTPS URL without query, fragment, or credentials")
        parts = parsed.path.split("/")
        if len(parts) != 5 or parts[0] or not all(parts[index] for index in (1, 2, 4)):
            raise ValueError("URL must name one owner, repository, and pull request")
        owner, repository, pull, number_text = parts[1:]
        if pull != "pull" or not number_text.isascii() or not number_text.isdecimal():
            raise ValueError("URL must end in /pull/<positive-int>")
        number = int(number_text)
        if number <= 0:
            raise ValueError("pull-request number must be positive")
        return cls(
            host=parsed.hostname,
            owner=owner,
            repository=repository,
            number=number,
            url=value,
        )


@dataclass(frozen=True, slots=True)
class PullRequestFact:
    """Verified read-only GitHub state for one canonical pull request."""

    node_id: str
    host: str
    owner: str
    repository: str
    number: int
    url: str
    state: str
    merged_at: datetime | None
    evidence_id: str

    @property
    def ref(self) -> PullRequestRef:
        """Return the canonical reference represented by this fact."""
        return PullRequestRef(self.host, self.owner, self.repository, self.number, self.url)


@dataclass(frozen=True, slots=True)
class DependencyRunFinding:
    """One in-period child start compared with its parent's merge gate."""

    run_id: str
    started_at: datetime
    classification: str


@dataclass(frozen=True, slots=True)
class DependencyWaitFinding:
    """One child-scoped prerequisite wait and its strictly later retry evidence."""

    wait_id: str | None
    wait_at: datetime | None
    status: str
    retry_run_id: str | None
    retry_started_at: datetime | None
    operator_comment_id: str | None
    operator_comment_at: datetime | None
    intervention: str


@dataclass(frozen=True, slots=True)
class DependencyEdgeFinding:
    """Complete structural, gate, and causal audit for one declared edge."""

    parent_id: str
    child_id: str
    attachment: str
    link_evidence_ids: tuple[str, ...]
    handoff_id: str
    handoff_at: datetime
    pull_request_id: str
    pull_request_url: str
    merged_at: datetime | None
    observation: str
    runs: tuple[DependencyRunFinding, ...]
    waits: tuple[DependencyWaitFinding, ...]


@dataclass(frozen=True, slots=True)
class UnexpectedBoardLinkFinding:
    """One captured board link with no matching declared graph edge."""

    parent_chunk_id: str | None
    child_chunk_id: str | None
    parent_task_id: str
    child_task_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class DependencyAudit:
    """Pure CHUNK-4 findings, including every declared and unexpected edge."""

    edges: tuple[DependencyEdgeFinding, ...]
    unexpected_links: tuple[UnexpectedBoardLinkFinding, ...]


@dataclass(frozen=True, slots=True)
class BlockOccurrence:
    """One deduplicated block occurrence retaining all underlying evidence."""

    id: str
    chunk_id: str
    task_id: str
    run_id: int | None
    reason_class: str | None
    native_kinds: tuple[str, ...]
    occurred_at: datetime
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Immutable canonical input to the pure lifecycle metrics engine."""

    request: "ReportRequest"
    graph_fingerprint: SourceFingerprint
    hermes_fingerprint: SourceFingerprint
    hermes_source_files: tuple[SourceFileFingerprint, ...]
    chunks: tuple[GraphChunk, ...]
    edges: tuple[GraphEdge, ...]
    cards: tuple[BoardCard, ...]
    links: tuple[BoardLink, ...]
    runs: tuple[NormalizedRun, ...]
    events: tuple[NormalizedEvent, ...]
    comments: tuple[NormalizedComment, ...]
    verdicts: tuple[JudgeVerdict, ...]
    blocks: tuple[BlockOccurrence, ...]
    handoffs: tuple[ChunkHandoff, ...]
    warnings: tuple[NormalizationWarning, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class VerdictContribution:
    """One in-period canonical verdict contributing to a finding."""

    evidence_id: str
    chunk_id: str
    outcome: JudgeOutcome
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class BounceFinding:
    """Unique bounced/completed chunks plus complete verdict coverage."""

    status: Availability
    numerator: int
    denominator: int
    rate: Decimal | None
    completed_chunk_ids: tuple[str, ...]
    bounced_chunk_ids: tuple[str, ...]
    verdicts: tuple[VerdictContribution, ...]
    completed_without_canonical_verdict: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    """One raw judge score with its stable verdict evidence."""

    evidence_id: str
    chunk_id: str
    occurred_at: datetime
    score: Decimal


@dataclass(frozen=True, slots=True)
class ScoreFinding:
    """One independently calculated quality dimension."""

    name: str
    status: Availability
    total: Decimal
    count: int
    mean: Decimal | None
    scores: tuple[ScoreContribution, ...]


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """The three independent canonical judge quality findings."""

    spec_fidelity: ScoreFinding
    scenario_integrity: ScoreFinding
    architectural_conformance: ScoreFinding


@dataclass(frozen=True, slots=True)
class BlockReasonFinding:
    """One exact canonical reason-class bucket."""

    reason_class: str
    count: int
    occurrence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlockFinding:
    """Canonical reason distribution and explicit unclassified occurrences."""

    total: int
    reasons: tuple[BlockReasonFinding, ...]
    unclassified: tuple[BlockOccurrence, ...]
    occurrences: tuple[BlockOccurrence, ...]


@dataclass(frozen=True, slots=True)
class CommentFinding:
    """One in-period comment with actor and causal disposition."""

    evidence_id: str
    chunk_id: str
    author: str
    actor_class: ActorClass
    occurred_at: datetime
    disposition: str


@dataclass(frozen=True, slots=True)
class NeededIntervention:
    """One block-comment-next-claim chain, unique by chunk in the result."""

    chunk_id: str
    block_id: str
    comment_id: str
    next_claim_id: str
    block_at: datetime
    comment_at: datetime
    next_claim_at: datetime


@dataclass(frozen=True, slots=True)
class CausalIndeterminate:
    """Equal timestamps that cannot honestly establish a causal order."""

    chunk_id: str
    relation: str
    occurred_at: datetime
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterventionFinding:
    """Operator-comment counts, actor distinctions, chains, and timestamp ties."""

    qualifying_comment_count: int
    qualifying_comments: tuple[CommentFinding, ...]
    comments: tuple[CommentFinding, ...]
    needed_to_execute_chunk_count: int
    needed_to_execute: tuple[NeededIntervention, ...]
    indeterminate: tuple[CausalIndeterminate, ...]


@dataclass(frozen=True, slots=True)
class LifecycleMetrics:
    """Pure CHUNK-3 result model consumed by later report construction."""

    bounce: BounceFinding
    quality: QualityFinding
    blocks: BlockFinding
    intervention: InterventionFinding


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
