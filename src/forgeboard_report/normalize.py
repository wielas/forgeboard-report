"""Pure normalization of graph and raw Hermes records into canonical evidence."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from forgeboard_report.domain import (
    ActorClass,
    BlockOccurrence,
    BoardCard,
    BoardLink,
    ChunkHandoff,
    EvidenceRef,
    EvidenceRole,
    GraphSnapshot,
    JudgeOutcome,
    JudgeScores,
    JudgeVerdict,
    NormalizationWarning,
    NormalizedComment,
    NormalizedEvent,
    NormalizedRun,
    RawHermesCard,
    RawHermesEvent,
    RawHermesRun,
    RawHermesSnapshot,
    ReportRequest,
    SourceSnapshot,
)
from forgeboard_report.errors import InvalidCoreError, InvalidSchemaError

_JUDGE_SCHEMA = "forge.judge.v1"
_BLOCK_SCHEMA = "forge.block.v1"
_CHUNK_SCHEMA = "forge.chunk.v1"
_SCORE_NAMES = (
    "spec_fidelity",
    "scenario_integrity",
    "architectural_conformance",
    "scope_discipline",
    "debt_honesty",
    "doc_reconciliation",
)
_TERMINAL_CARD_STATUSES = frozenset({"done", "archived"})


class _DuplicateJsonKey(ValueError):
    pass


class _InvalidJsonConstant(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _DecodedJudge:
    chunk_id: str
    outcome: JudgeOutcome
    scores: JudgeScores


@dataclass(frozen=True, slots=True)
class _DecodedBlock:
    reason_class: str


@dataclass(frozen=True, slots=True)
class _DecodedChunk:
    chunk_id: str
    pr: str


_DecodedMetadata = _DecodedJudge | _DecodedBlock | _DecodedChunk
_Decoder = Callable[[dict[str, Any], str, str | None], _DecodedMetadata]


def normalize(
    raw_hermes: RawHermesSnapshot,
    graph: GraphSnapshot,
    request: ReportRequest,
) -> SourceSnapshot:
    """Normalize one stable source set without consulting any external state."""
    return normalize_sources(raw_hermes, graph, request)


def normalize_sources(
    raw_hermes: RawHermesSnapshot,
    graph: GraphSnapshot,
    request: ReportRequest,
) -> SourceSnapshot:
    """Join exact identities, decode canonical schemas, and build evidence."""
    if raw_hermes.board_slug != request.board_slug.lower():
        raise InvalidCoreError(
            "Hermes board",
            f"resolved board {raw_hermes.board_slug!r} does not match request "
            f"{request.board_slug!r}",
        )

    cards_by_key: dict[str, list[RawHermesCard]] = {}
    for card in raw_hermes.cards:
        if card.idempotency_key is not None:
            cards_by_key.setdefault(card.idempotency_key, []).append(card)

    chunk_by_task: dict[str, str] = {}
    card_rows = []
    for chunk in graph.chunks:
        bootstrap_key = chunk.bootstrap_key(raw_hermes.board_slug)
        matches = tuple(cards_by_key.get(bootstrap_key, ()))
        if len(matches) != 1:
            raise InvalidCoreError(
                f"graph chunk {chunk.id!r}",
                f"bootstrap key {bootstrap_key!r} matched {len(matches)} Hermes tasks; "
                "expected exactly one",
            )
        card = matches[0]
        if card.id in chunk_by_task:
            raise InvalidCoreError(
                f"hermes:card:{card.id}",
                "one opaque task maps to multiple graph chunks",
            )
        chunk_by_task[card.id] = chunk.id
        card_rows.append(card)

    _reject_duplicate_ids("card", (card.id for card in card_rows))
    mapped_task_ids = frozenset(chunk_by_task)
    mapped_runs = tuple(run for run in raw_hermes.runs if run.task_id in mapped_task_ids)
    mapped_events = tuple(event for event in raw_hermes.events if event.task_id in mapped_task_ids)
    mapped_comments = tuple(
        comment for comment in raw_hermes.comments if comment.task_id in mapped_task_ids
    )
    _reject_conflicting_duplicate_block_reasons(mapped_runs)
    _reject_duplicate_ids("run", (run.id for run in mapped_runs))
    _reject_duplicate_ids("event", (event.id for event in mapped_events))
    _reject_duplicate_ids("comment", (comment.id for comment in mapped_comments))

    cards = tuple(
        sorted(
            (_normalize_card(card, chunk_by_task[card.id]) for card in card_rows),
            key=lambda card: card.chunk_id,
        )
    )
    cards_by_task = {card.task_id: card for card in cards}

    warnings: list[NormalizationWarning] = []
    decoded_by_run: dict[int, _DecodedMetadata] = {}
    metadata_schema_by_run: dict[int, str | None] = {}
    for run in mapped_runs:
        evidence_id = _run_evidence_id(run.id)
        schema, decoded, warning = _decode_metadata(
            run.metadata,
            evidence_id,
            chunk_by_task[run.task_id],
        )
        metadata_schema_by_run[run.id] = schema
        if decoded is not None:
            decoded_by_run[run.id] = decoded
        if warning is not None:
            warnings.append(warning)

    runs = tuple(
        sorted(
            (
                _normalize_run(
                    run,
                    chunk_by_task[run.task_id],
                    metadata_schema_by_run[run.id],
                )
                for run in mapped_runs
            ),
            key=lambda run: (run.chunk_id, run.started_at, run.id),
        )
    )
    events = tuple(
        sorted(
            (_normalize_event(event, chunk_by_task[event.task_id]) for event in mapped_events),
            key=lambda event: (event.chunk_id, event.occurred_at, event.id),
        )
    )
    comments = tuple(
        sorted(
            (
                _normalize_comment(
                    comment,
                    chunk_by_task[comment.task_id],
                    request.operator_ids,
                )
                for comment in mapped_comments
            ),
            key=lambda comment: (comment.chunk_id, comment.occurred_at, comment.id),
        )
    )
    links = _normalize_links(raw_hermes, chunk_by_task)
    verdicts = _normalize_verdicts(runs, decoded_by_run)
    handoffs = _normalize_handoffs(runs, events, decoded_by_run)
    blocks = _normalize_blocks(
        cards_by_task,
        mapped_runs,
        events,
        mapped_events,
        decoded_by_run,
    )
    evidence = _build_evidence(
        request,
        graph,
        cards,
        links,
        runs,
        events,
        comments,
        verdicts,
        blocks,
    )

    return SourceSnapshot(
        request=request,
        graph_fingerprint=graph.fingerprint,
        hermes_fingerprint=raw_hermes.fingerprint,
        hermes_source_files=raw_hermes.source_files,
        chunks=graph.chunks,
        edges=graph.edges,
        cards=cards,
        links=links,
        runs=runs,
        events=events,
        comments=comments,
        verdicts=verdicts,
        blocks=blocks,
        handoffs=handoffs,
        warnings=tuple(
            sorted(
                warnings,
                key=lambda warning: (
                    warning.evidence_id,
                    warning.code,
                    warning.schema or "",
                ),
            )
        ),
        evidence=evidence,
    )


def classify_author(author: str, operator_ids: tuple[str, ...]) -> ActorClass:
    """Classify an exact Hermes comment author without prose or case heuristics."""
    if author in operator_ids:
        return ActorClass.OPERATOR
    if author in {"forge-codex-lane", "builder"}:
        return ActorClass.WORKER
    if author == "forge-prejudge":
        return ActorClass.PREJUDGE
    if author in {"forge-orchestrator", "forge-digest"}:
        return ActorClass.AUTOMATED
    return ActorClass.UNCLASSIFIED


def _normalize_card(card: Any, chunk_id: str) -> BoardCard:
    source = f"hermes:card:{card.id}"
    created_at = _parse_timestamp(card.created_at, source, "created_at")
    started_at = _parse_optional_timestamp(card.started_at, source, "started_at")
    completed_at = _parse_optional_timestamp(card.completed_at, source, "completed_at")
    if card.status in _TERMINAL_CARD_STATUSES and completed_at is None:
        raise InvalidCoreError(source, f"terminal status {card.status!r} requires completed_at")
    if card.status not in _TERMINAL_CARD_STATUSES and completed_at is not None:
        raise InvalidCoreError(
            source,
            f"nonterminal status {card.status!r} contradicts completed_at",
        )
    return BoardCard(
        chunk_id=chunk_id,
        task_id=card.id,
        idempotency_key=card.idempotency_key,
        status=card.status,
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        native_block_kind=card.block_kind,
        evidence_id=source,
    )


def _normalize_run(
    run: RawHermesRun,
    chunk_id: str,
    metadata_schema: str | None,
) -> NormalizedRun:
    source = _run_evidence_id(run.id)
    started_at = _parse_timestamp(run.started_at, source, "started_at")
    ended_at = _parse_optional_timestamp(run.ended_at, source, "ended_at")
    if ended_at is not None and ended_at < started_at:
        raise InvalidCoreError(source, "ended_at precedes started_at")
    return NormalizedRun(
        id=run.id,
        chunk_id=chunk_id,
        task_id=run.task_id,
        profile=run.profile,
        step_key=run.step_key,
        status=run.status,
        outcome=run.outcome,
        started_at=started_at,
        ended_at=ended_at,
        metadata_schema=metadata_schema,
        evidence_id=source,
    )


def _normalize_event(event: RawHermesEvent, chunk_id: str) -> NormalizedEvent:
    source = _event_evidence_id(event.id)
    return NormalizedEvent(
        id=event.id,
        chunk_id=chunk_id,
        task_id=event.task_id,
        run_id=event.run_id,
        kind=event.kind,
        occurred_at=_parse_timestamp(event.created_at, source, "created_at"),
        evidence_id=source,
    )


def _normalize_comment(
    comment: Any,
    chunk_id: str,
    operator_ids: tuple[str, ...],
) -> NormalizedComment:
    source = _comment_evidence_id(comment.id)
    return NormalizedComment(
        id=comment.id,
        chunk_id=chunk_id,
        task_id=comment.task_id,
        author=comment.author,
        actor_class=classify_author(comment.author, operator_ids),
        occurred_at=_parse_timestamp(comment.created_at, source, "created_at"),
        evidence_id=source,
    )


def _normalize_links(
    raw_hermes: RawHermesSnapshot,
    chunk_by_task: dict[str, str],
) -> tuple[BoardLink, ...]:
    links: list[BoardLink] = []
    seen: set[tuple[str, str]] = set()
    for raw_link in raw_hermes.links:
        if raw_link.parent_id not in chunk_by_task or raw_link.child_id not in chunk_by_task:
            continue
        identity = (raw_link.parent_id, raw_link.child_id)
        if identity in seen:
            raise InvalidCoreError(
                _link_evidence_id(*identity),
                "duplicate mapped board link",
            )
        seen.add(identity)
        links.append(
            BoardLink(
                parent_chunk_id=chunk_by_task[raw_link.parent_id],
                child_chunk_id=chunk_by_task[raw_link.child_id],
                parent_task_id=raw_link.parent_id,
                child_task_id=raw_link.child_id,
                evidence_id=_link_evidence_id(*identity),
            )
        )
    return tuple(
        sorted(
            links,
            key=lambda link: (
                link.parent_chunk_id,
                link.child_chunk_id,
                link.evidence_id,
            ),
        )
    )


def _normalize_verdicts(
    runs: tuple[NormalizedRun, ...],
    decoded_by_run: dict[int, _DecodedMetadata],
) -> tuple[JudgeVerdict, ...]:
    verdicts: list[JudgeVerdict] = []
    for run in runs:
        decoded = decoded_by_run.get(run.id)
        if not isinstance(decoded, _DecodedJudge):
            continue
        if run.ended_at is None:
            raise InvalidCoreError(
                run.evidence_id,
                "forge.judge.v1 requires a finished run ended_at",
            )
        verdicts.append(
            JudgeVerdict(
                id=run.evidence_id,
                chunk_id=run.chunk_id,
                task_id=run.task_id,
                run_id=run.id,
                outcome=decoded.outcome,
                scores=decoded.scores,
                occurred_at=run.ended_at,
                evidence_ids=(run.evidence_id,),
            )
        )
    return tuple(
        sorted(
            verdicts,
            key=lambda verdict: (verdict.occurred_at, verdict.chunk_id, verdict.id),
        )
    )


def _normalize_handoffs(
    runs: tuple[NormalizedRun, ...],
    events: tuple[NormalizedEvent, ...],
    decoded_by_run: dict[int, _DecodedMetadata],
) -> tuple[ChunkHandoff, ...]:
    completed_run_keys = {
        (event.task_id, event.run_id)
        for event in events
        if event.kind == "completed" and event.run_id is not None
    }
    handoffs: list[ChunkHandoff] = []
    for run in runs:
        if (
            run.status != "done"
            or run.outcome != "completed"
            or (run.task_id, run.id) not in completed_run_keys
        ):
            continue
        decoded = decoded_by_run.get(run.id)
        if not isinstance(decoded, _DecodedChunk):
            continue
        if decoded.chunk_id != run.chunk_id:
            raise InvalidCoreError(
                run.evidence_id,
                f"forge.chunk.v1 chunk_id {decoded.chunk_id!r} does not match "
                f"mapped chunk {run.chunk_id!r}",
            )
        handoffs.append(
            ChunkHandoff(
                id=run.evidence_id,
                chunk_id=run.chunk_id,
                task_id=run.task_id,
                run_id=run.id,
                pr=decoded.pr,
                occurred_at=run.ended_at,
                evidence_ids=(run.evidence_id,),
            )
        )
    return tuple(
        sorted(
            handoffs,
            key=lambda handoff: (
                handoff.chunk_id,
                handoff.occurred_at or datetime.max.replace(tzinfo=UTC),
                handoff.id,
            ),
        )
    )


def _normalize_blocks(
    cards_by_task: dict[str, BoardCard],
    raw_runs: tuple[RawHermesRun, ...],
    events: tuple[NormalizedEvent, ...],
    raw_events: tuple[RawHermesEvent, ...],
    decoded_by_run: dict[int, _DecodedMetadata],
) -> tuple[BlockOccurrence, ...]:
    block_runs = tuple(
        run for run in raw_runs if run.status == "blocked" or run.outcome == "blocked"
    )
    block_events = tuple(event for event in events if event.kind == "blocked")
    raw_event_by_id = {event.id: event for event in raw_events}
    events_by_run: dict[tuple[str, int], list[NormalizedEvent]] = {}
    unmatched_events: set[int] = {event.id for event in block_events}
    for event in block_events:
        if event.run_id is not None:
            events_by_run.setdefault((event.task_id, event.run_id), []).append(event)

    occurrences: list[BlockOccurrence] = []
    for raw_run in block_runs:
        run_events = tuple(
            sorted(
                events_by_run.get((raw_run.task_id, raw_run.id), ()),
                key=lambda event: (event.occurred_at, event.id),
            )
        )
        unmatched_events.difference_update(event.id for event in run_events)
        occurred_at = run_events[0].occurred_at if run_events else _required_block_run_end(raw_run)
        decoded = decoded_by_run.get(raw_run.id)
        reason_class = decoded.reason_class if isinstance(decoded, _DecodedBlock) else None
        evidence_ids = [_run_evidence_id(raw_run.id)]
        evidence_ids.extend(event.evidence_id for event in run_events)
        native_kinds, supplemental_ids = _native_block_evidence(
            cards_by_task[raw_run.task_id],
            tuple(raw_event_by_id[event.id] for event in run_events),
        )
        evidence_ids.extend(supplemental_ids)
        occurrences.append(
            BlockOccurrence(
                id=f"hermes:block:{raw_run.task_id}:run:{raw_run.id}",
                chunk_id=cards_by_task[raw_run.task_id].chunk_id,
                task_id=raw_run.task_id,
                run_id=raw_run.id,
                reason_class=reason_class,
                native_kinds=native_kinds,
                occurred_at=occurred_at,
                evidence_ids=tuple(sorted(set(evidence_ids))),
            )
        )

    for event in block_events:
        if event.id not in unmatched_events:
            continue
        native_kinds, supplemental_ids = _native_block_evidence(
            cards_by_task[event.task_id],
            (raw_event_by_id[event.id],),
        )
        occurrences.append(
            BlockOccurrence(
                id=f"hermes:block:event:{event.id}",
                chunk_id=event.chunk_id,
                task_id=event.task_id,
                run_id=event.run_id,
                reason_class=None,
                native_kinds=native_kinds,
                occurred_at=event.occurred_at,
                evidence_ids=tuple(
                    sorted(
                        {
                            event.evidence_id,
                            *supplemental_ids,
                        }
                    )
                ),
            )
        )

    return tuple(
        sorted(
            occurrences,
            key=lambda occurrence: (
                occurrence.occurred_at,
                occurrence.chunk_id,
                occurrence.id,
            ),
        )
    )


def _required_block_run_end(run: RawHermesRun) -> datetime:
    return _parse_timestamp(
        run.ended_at,
        _run_evidence_id(run.id),
        "ended_at fallback for blocked run",
    )


def _native_block_evidence(
    card: BoardCard,
    events: tuple[RawHermesEvent, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kinds: set[str] = set()
    evidence_ids: set[str] = set()
    if card.native_block_kind:
        kinds.add(card.native_block_kind)
        evidence_ids.add(card.evidence_id)
    for event in events:
        payload = _decode_optional_object(event.payload)
        kind = payload.get("kind") if payload is not None else None
        if isinstance(kind, str) and kind:
            kinds.add(kind)
            evidence_ids.add(_event_evidence_id(event.id))
    return tuple(sorted(kinds)), tuple(sorted(evidence_ids))


def _build_evidence(
    request: ReportRequest,
    graph: GraphSnapshot,
    cards: tuple[BoardCard, ...],
    links: tuple[BoardLink, ...],
    runs: tuple[NormalizedRun, ...],
    events: tuple[NormalizedEvent, ...],
    comments: tuple[NormalizedComment, ...],
    verdicts: tuple[JudgeVerdict, ...],
    blocks: tuple[BlockOccurrence, ...],
) -> tuple[EvidenceRef, ...]:
    contributing_ids: set[str] = set()
    contributing_ids.update(
        card.evidence_id
        for card in cards
        if card.completed_at is not None and request.contains(card.completed_at)
    )
    contributing_ids.update(
        verdict.id for verdict in verdicts if request.contains(verdict.occurred_at)
    )
    contributing_ids.update(
        comment.evidence_id for comment in comments if request.contains(comment.occurred_at)
    )
    for block in blocks:
        if request.contains(block.occurred_at):
            contributing_ids.update(block.evidence_ids)

    refs: dict[str, EvidenceRef] = {}
    for chunk in graph.chunks:
        _add_evidence(
            refs,
            f"graph:chunk:{chunk.id}",
            chunk.id,
            None,
            contributing_ids,
        )
    for edge in graph.edges:
        edge_id = f"graph:{edge.parent_id}->{edge.child_id}"
        _add_evidence(
            refs,
            edge_id,
            f"{edge.parent_id}->{edge.child_id}",
            None,
            contributing_ids,
        )
    for link in links:
        _add_evidence(
            refs,
            link.evidence_id,
            f"{link.parent_task_id}->{link.child_task_id}",
            None,
            contributing_ids,
        )
    for card in cards:
        _add_evidence(
            refs,
            card.evidence_id,
            card.task_id,
            card.completed_at or card.created_at,
            contributing_ids,
        )
    for run in runs:
        _add_evidence(
            refs,
            run.evidence_id,
            str(run.id),
            run.ended_at or run.started_at,
            contributing_ids,
        )
    for event in events:
        _add_evidence(
            refs,
            event.evidence_id,
            str(event.id),
            event.occurred_at,
            contributing_ids,
        )
    for comment in comments:
        _add_evidence(
            refs,
            comment.evidence_id,
            str(comment.id),
            comment.occurred_at,
            contributing_ids,
        )
    return tuple(
        sorted(
            refs.values(),
            key=lambda ref: (
                ref.occurred_at or datetime.min.replace(tzinfo=UTC),
                ref.id,
            ),
        )
    )


def _add_evidence(
    refs: dict[str, EvidenceRef],
    evidence_id: str,
    source_id: str,
    occurred_at: datetime | None,
    contributing_ids: set[str],
) -> None:
    refs[evidence_id] = EvidenceRef(
        id=evidence_id,
        source_id=source_id,
        occurred_at=occurred_at,
        role=(
            EvidenceRole.CONTRIBUTING if evidence_id in contributing_ids else EvidenceRole.CONTEXT
        ),
    )


def _decode_metadata(
    source_value: str | bytes | None,
    evidence_id: str,
    expected_chunk_id: str | None = None,
) -> tuple[str | None, _DecodedMetadata | None, NormalizationWarning | None]:
    if source_value is None:
        return None, None, None
    try:
        decoded = _decode_json(source_value)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, _InvalidJsonConstant):
        return (
            None,
            None,
            NormalizationWarning(
                code="noncanonical-metadata",
                evidence_id=evidence_id,
                schema=None,
                detail="metadata is not one exact structured envelope",
            ),
        )
    if not isinstance(decoded, dict):
        return (
            None,
            None,
            NormalizationWarning(
                code="noncanonical-metadata",
                evidence_id=evidence_id,
                schema=None,
                detail="metadata is not one object envelope",
            ),
        )

    schema = decoded.get("schema")
    if not isinstance(schema, str) or not schema:
        return (
            None,
            None,
            NormalizationWarning(
                code="noncanonical-metadata",
                evidence_id=evidence_id,
                schema=None,
                detail="metadata has no nonempty string schema",
            ),
        )
    decoder = _DECODERS.get(schema)
    if decoder is None:
        return (
            schema,
            None,
            NormalizationWarning(
                code="unknown-metadata-schema",
                evidence_id=evidence_id,
                schema=schema,
                detail=f"unknown metadata schema {schema!r}",
            ),
        )
    return schema, decoder(decoded, evidence_id, expected_chunk_id), None


def _decode_judge(
    value: dict[str, Any],
    evidence_id: str,
    expected_chunk_id: str | None,
) -> _DecodedJudge:
    _require_keys(
        value,
        {
            "schema",
            "chunk_id",
            "pr",
            "verdict",
            "scores",
            "findings",
            "nits_as_cards",
            "spot_check_suggestion",
            "judge_model",
            "tokens_estimate",
        },
        evidence_id,
        _JUDGE_SCHEMA,
    )
    chunk_id = value["chunk_id"]
    if not isinstance(chunk_id, str) or not chunk_id:
        raise InvalidSchemaError(
            evidence_id,
            "forge.judge.v1 chunk_id must be a nonempty exact string",
        )
    if expected_chunk_id is not None and chunk_id != expected_chunk_id:
        raise InvalidSchemaError(
            evidence_id,
            f"forge.judge.v1 chunk_id {chunk_id!r} does not match mapped chunk "
            f"{expected_chunk_id!r}",
        )
    verdict = value["verdict"]
    if verdict == "bounce":
        outcome = JudgeOutcome.BOUNCE
    elif verdict in {"approve", "approve-with-nits"}:
        outcome = JudgeOutcome.PASS
    else:
        raise InvalidSchemaError(
            evidence_id,
            "forge.judge.v1 verdict must be exactly 'approve', 'approve-with-nits', or 'bounce'",
        )
    scores = value["scores"]
    if not isinstance(scores, dict):
        raise InvalidSchemaError(evidence_id, "forge.judge.v1 scores must be an object")
    _require_keys(scores, set(_SCORE_NAMES), evidence_id, "forge.judge.v1 scores")
    decoded_scores = {
        name: _finite_decimal(scores[name], evidence_id, name) for name in _SCORE_NAMES
    }
    return _DecodedJudge(
        chunk_id=chunk_id,
        outcome=outcome,
        scores=JudgeScores(
            spec_fidelity=decoded_scores["spec_fidelity"],
            scenario_integrity=decoded_scores["scenario_integrity"],
            architectural_conformance=decoded_scores["architectural_conformance"],
        ),
    )


def _decode_block(
    value: dict[str, Any],
    evidence_id: str,
    _expected_chunk_id: str | None,
) -> _DecodedBlock:
    _require_keys(value, {"schema", "reason_class"}, evidence_id, _BLOCK_SCHEMA)
    reason_class = value["reason_class"]
    if not isinstance(reason_class, str) or not reason_class:
        raise InvalidSchemaError(
            evidence_id,
            "forge.block.v1 reason_class must be a nonempty exact string",
        )
    return _DecodedBlock(reason_class=reason_class)


def _decode_chunk(
    value: dict[str, Any],
    evidence_id: str,
    _expected_chunk_id: str | None,
) -> _DecodedChunk:
    _require_keys(value, {"schema", "chunk_id", "pr"}, evidence_id, _CHUNK_SCHEMA)
    chunk_id = value["chunk_id"]
    pr = value["pr"]
    if not isinstance(chunk_id, str) or not chunk_id:
        raise InvalidSchemaError(
            evidence_id,
            "forge.chunk.v1 chunk_id must be a nonempty exact string",
        )
    if not isinstance(pr, str) or not _is_complete_url(pr):
        raise InvalidSchemaError(
            evidence_id,
            "forge.chunk.v1 pr must be one complete URL",
        )
    return _DecodedChunk(chunk_id=chunk_id, pr=pr)


_DECODERS: dict[str, _Decoder] = {
    _JUDGE_SCHEMA: _decode_judge,
    _BLOCK_SCHEMA: _decode_block,
    _CHUNK_SCHEMA: _decode_chunk,
}


def _require_keys(
    value: dict[str, Any],
    required: set[str],
    evidence_id: str,
    label: str,
) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    if missing:
        raise InvalidSchemaError(evidence_id, f"{label} has missing keys {missing}")


def _finite_decimal(value: Any, evidence_id: str, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise InvalidSchemaError(
            evidence_id,
            f"forge.judge.v1 score {name!r} must be an integer",
        )
    if not value.is_finite():
        raise InvalidSchemaError(
            evidence_id,
            f"forge.judge.v1 score {name!r} must be a finite integer",
        )
    if value != value.to_integral_value():
        raise InvalidSchemaError(
            evidence_id,
            f"forge.judge.v1 score {name!r} must be an integer",
        )
    if not Decimal(0) <= value <= Decimal(3):
        raise InvalidSchemaError(
            evidence_id,
            f"forge.judge.v1 score {name!r} must be in 0-3 range",
        )
    return value


def _decode_json(value: str | bytes) -> Any:
    return json.loads(
        value,
        object_pairs_hook=_unique_object,
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=_reject_json_constant,
    )


def _decode_optional_object(value: str | bytes | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        decoded = _decode_json(value)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, _InvalidJsonConstant):
        return None
    return decoded if isinstance(decoded, dict) else None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _InvalidJsonConstant(f"invalid JSON constant {value!r}")


def _is_complete_url(value: str) -> bool:
    if value != value.strip() or any(character.isspace() for character in value):
        return False
    parsed = urlsplit(value)
    return bool(parsed.scheme and parsed.netloc and parsed.path)


def _parse_optional_timestamp(
    value: object | None,
    source: str,
    field: str,
) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value, source, field)


def _parse_timestamp(value: object, source: str, field: str) -> datetime:
    if isinstance(value, bool):
        raise InvalidCoreError(source, f"{field} must be an aware timestamp")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float, Decimal)):
        try:
            parsed = datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError, TypeError) as error:
            raise InvalidCoreError(source, f"{field} must be a valid timestamp") from error
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise InvalidCoreError(
                source,
                f"{field} must be an aware ISO-8601 timestamp",
            ) from error
    else:
        raise InvalidCoreError(source, f"{field} must be an aware timestamp")
    if parsed.utcoffset() is None:
        raise InvalidCoreError(source, f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _reject_duplicate_ids(kind: str, values: Any) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise InvalidCoreError(f"hermes:{kind}:{value}", f"duplicate {kind} id")
        seen.add(value)


def _reject_conflicting_duplicate_block_reasons(
    runs: tuple[RawHermesRun, ...],
) -> None:
    grouped: dict[tuple[str, int], list[RawHermesRun]] = {}
    for run in runs:
        if run.status == "blocked" or run.outcome == "blocked":
            grouped.setdefault((run.task_id, run.id), []).append(run)
    for (task_id, run_id), duplicates in grouped.items():
        if len(duplicates) < 2:
            continue
        reasons: set[str] = set()
        for run in duplicates:
            _schema, decoded, _warning = _decode_metadata(
                run.metadata,
                _run_evidence_id(run_id),
            )
            if isinstance(decoded, _DecodedBlock):
                reasons.add(decoded.reason_class)
        if len(reasons) > 1:
            raise InvalidCoreError(
                f"hermes:block:{task_id}:run:{run_id}",
                f"conflicting canonical reason_class values {sorted(reasons)}",
            )


def _run_evidence_id(run_id: int) -> str:
    return f"hermes:run:{run_id}"


def _event_evidence_id(event_id: int) -> str:
    return f"hermes:event:{event_id}"


def _comment_evidence_id(comment_id: int) -> str:
    return f"hermes:comment:{comment_id}"


def _link_evidence_id(parent_id: str, child_id: str) -> str:
    return f"hermes:link:{parent_id}->{child_id}"
