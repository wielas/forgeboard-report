"""Pure lifecycle metric calculations over one canonical source snapshot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from forgeboard_report.domain import (
    ActorClass,
    Availability,
    BlockFinding,
    BlockOccurrence,
    BlockReasonFinding,
    BoardCard,
    BounceFinding,
    CausalIndeterminate,
    CommentFinding,
    InterventionFinding,
    JudgeVerdict,
    LifecycleMetrics,
    NeededIntervention,
    NormalizedComment,
    NormalizedEvent,
    QualityFinding,
    ScoreContribution,
    ScoreFinding,
    SourceSnapshot,
    VerdictContribution,
)

_TERMINAL_EVENT_KINDS = frozenset({"completed", "archived"})
_DIVISION_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class _Terminal:
    occurred_at: datetime
    evidence_id: str


def calculate(snapshot: SourceSnapshot) -> LifecycleMetrics:
    """Calculate every CHUNK-3 metric without I/O or ambient state."""
    return calculate_metrics(snapshot)


def calculate_metrics(snapshot: SourceSnapshot) -> LifecycleMetrics:
    """Return deterministic bounce, quality, block, and intervention findings."""
    in_period_verdicts = tuple(
        verdict for verdict in snapshot.verdicts if snapshot.request.contains(verdict.occurred_at)
    )
    return LifecycleMetrics(
        bounce=_calculate_bounce(snapshot, in_period_verdicts),
        quality=_calculate_quality(in_period_verdicts),
        blocks=_calculate_blocks(snapshot),
        intervention=_calculate_intervention(snapshot),
    )


def _calculate_bounce(
    snapshot: SourceSnapshot,
    in_period_verdicts: tuple[JudgeVerdict, ...],
) -> BounceFinding:
    completed_chunk_ids = tuple(
        card.chunk_id
        for card in snapshot.cards
        if card.completed_at is not None and snapshot.request.contains(card.completed_at)
    )
    completed = frozenset(completed_chunk_ids)
    contributing_verdicts = tuple(
        verdict for verdict in in_period_verdicts if verdict.chunk_id in completed
    )
    verdict_chunks = frozenset(verdict.chunk_id for verdict in contributing_verdicts)
    bounced_chunk_ids = tuple(
        sorted(
            {
                verdict.chunk_id
                for verdict in contributing_verdicts
                if verdict.outcome.value == "bounce"
            }
        )
    )
    denominator = len(completed_chunk_ids)
    numerator = len(bounced_chunk_ids)
    status = Availability.AVAILABLE if denominator else Availability.UNAVAILABLE
    return BounceFinding(
        status=status,
        numerator=numerator,
        denominator=denominator,
        rate=_divide(Decimal(numerator), denominator) if denominator else None,
        completed_chunk_ids=completed_chunk_ids,
        bounced_chunk_ids=bounced_chunk_ids,
        verdicts=tuple(
            VerdictContribution(
                evidence_id=verdict.id,
                chunk_id=verdict.chunk_id,
                outcome=verdict.outcome,
                occurred_at=verdict.occurred_at,
            )
            for verdict in contributing_verdicts
        ),
        completed_without_canonical_verdict=tuple(
            chunk_id for chunk_id in completed_chunk_ids if chunk_id not in verdict_chunks
        ),
    )


def _calculate_quality(
    in_period_verdicts: tuple[JudgeVerdict, ...],
) -> QualityFinding:
    return QualityFinding(
        spec_fidelity=_score_finding(
            "spec_fidelity",
            in_period_verdicts,
            lambda verdict: verdict.scores.spec_fidelity,
        ),
        scenario_integrity=_score_finding(
            "scenario_integrity",
            in_period_verdicts,
            lambda verdict: verdict.scores.scenario_integrity,
        ),
        architectural_conformance=_score_finding(
            "architectural_conformance",
            in_period_verdicts,
            lambda verdict: verdict.scores.architectural_conformance,
        ),
    )


def _score_finding(
    name: str,
    verdicts: tuple[JudgeVerdict, ...],
    score_of: Callable[[JudgeVerdict], Decimal],
) -> ScoreFinding:
    scores = tuple(
        ScoreContribution(
            evidence_id=verdict.id,
            chunk_id=verdict.chunk_id,
            occurred_at=verdict.occurred_at,
            score=score_of(verdict),
        )
        for verdict in verdicts
    )
    total = sum((score.score for score in scores), start=Decimal(0))
    count = len(scores)
    return ScoreFinding(
        name=name,
        status=Availability.AVAILABLE if count else Availability.UNAVAILABLE,
        total=total,
        count=count,
        mean=_divide(total, count) if count else None,
        scores=scores,
    )


def _calculate_blocks(snapshot: SourceSnapshot) -> BlockFinding:
    occurrences = tuple(
        block for block in snapshot.blocks if snapshot.request.contains(block.occurred_at)
    )
    classified: dict[str, list[str]] = {}
    unclassified: list[BlockOccurrence] = []
    for occurrence in occurrences:
        if occurrence.reason_class is None:
            unclassified.append(occurrence)
        else:
            classified.setdefault(occurrence.reason_class, []).append(occurrence.id)
    reasons = tuple(
        BlockReasonFinding(
            reason_class=reason_class,
            count=len(occurrence_ids),
            occurrence_ids=tuple(sorted(occurrence_ids)),
        )
        for reason_class, occurrence_ids in sorted(classified.items())
    )
    return BlockFinding(
        total=len(occurrences),
        reasons=reasons,
        unclassified=tuple(unclassified),
        occurrences=occurrences,
    )


def _divide(value: Decimal, count: int) -> Decimal:
    with localcontext(_DIVISION_CONTEXT):
        return value / Decimal(count)


def _calculate_intervention(snapshot: SourceSnapshot) -> InterventionFinding:
    events_by_chunk = _group_events(snapshot)
    cards_by_chunk = {card.chunk_id: card for card in snapshot.cards}
    in_period_comments = tuple(
        comment for comment in snapshot.comments if snapshot.request.contains(comment.occurred_at)
    )
    findings: list[CommentFinding] = []
    indeterminate: list[CausalIndeterminate] = []

    for comment in in_period_comments:
        if comment.actor_class is not ActorClass.OPERATOR:
            findings.append(_comment_finding(comment, "non_operator"))
            continue
        chunk_events = events_by_chunk.get(comment.chunk_id, ())
        claims = tuple(event for event in chunk_events if event.kind == "claimed")
        if not claims:
            findings.append(_comment_finding(comment, "no_dispatch"))
            continue
        dispatch = claims[0]
        if comment.occurred_at < dispatch.occurred_at:
            findings.append(_comment_finding(comment, "pre_dispatch"))
            continue
        if comment.occurred_at == dispatch.occurred_at:
            findings.append(_comment_finding(comment, "indeterminate"))
            indeterminate.append(
                _tie(
                    comment.chunk_id,
                    "dispatch_comment",
                    comment.occurred_at,
                    dispatch.evidence_id,
                    comment.evidence_id,
                )
            )
            continue

        terminal = _first_terminal_after_dispatch(
            cards_by_chunk[comment.chunk_id],
            chunk_events,
            dispatch.occurred_at,
        )
        if terminal is None or comment.occurred_at < terminal.occurred_at:
            findings.append(_comment_finding(comment, "qualifying"))
        elif comment.occurred_at == terminal.occurred_at:
            findings.append(_comment_finding(comment, "indeterminate"))
            indeterminate.append(
                _tie(
                    comment.chunk_id,
                    "comment_terminal",
                    comment.occurred_at,
                    comment.evidence_id,
                    terminal.evidence_id,
                )
            )
        else:
            findings.append(_comment_finding(comment, "post_terminal"))

    findings_tuple = tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.occurred_at,
                finding.chunk_id,
                finding.evidence_id,
            ),
        )
    )
    qualifying = tuple(finding for finding in findings_tuple if finding.disposition == "qualifying")
    needed, chain_ties = _needed_interventions(
        snapshot,
        events_by_chunk,
        qualifying,
    )
    indeterminate.extend(chain_ties)
    return InterventionFinding(
        qualifying_comment_count=len(qualifying),
        qualifying_comments=qualifying,
        comments=findings_tuple,
        needed_to_execute_chunk_count=len(needed),
        needed_to_execute=needed,
        indeterminate=tuple(
            sorted(
                set(indeterminate),
                key=lambda tie: (
                    tie.occurred_at,
                    tie.chunk_id,
                    tie.relation,
                    tie.evidence_ids,
                ),
            )
        ),
    )


def _group_events(
    snapshot: SourceSnapshot,
) -> dict[str, tuple[NormalizedEvent, ...]]:
    grouped: dict[str, list[NormalizedEvent]] = {}
    for event in snapshot.events:
        grouped.setdefault(event.chunk_id, []).append(event)
    return {
        chunk_id: tuple(sorted(events, key=lambda event: (event.occurred_at, event.id)))
        for chunk_id, events in grouped.items()
    }


def _first_terminal_after_dispatch(
    card: BoardCard,
    events: tuple[NormalizedEvent, ...],
    dispatch_at: datetime,
) -> _Terminal | None:
    terminals = [
        _Terminal(event.occurred_at, event.evidence_id)
        for event in events
        if event.kind in _TERMINAL_EVENT_KINDS and event.occurred_at > dispatch_at
    ]
    if terminals:
        return min(
            terminals,
            key=lambda terminal: (terminal.occurred_at, terminal.evidence_id),
        )
    completed_at = card.completed_at
    if completed_at is not None and completed_at > dispatch_at:
        terminals.append(_Terminal(completed_at, card.evidence_id))
    return min(
        terminals,
        key=lambda terminal: (terminal.occurred_at, terminal.evidence_id),
        default=None,
    )


def _needed_interventions(
    snapshot: SourceSnapshot,
    events_by_chunk: dict[str, tuple[NormalizedEvent, ...]],
    qualifying_comments: tuple[CommentFinding, ...],
) -> tuple[tuple[NeededIntervention, ...], tuple[CausalIndeterminate, ...]]:
    comments_by_chunk: dict[str, list[CommentFinding]] = {}
    for comment in qualifying_comments:
        comments_by_chunk.setdefault(comment.chunk_id, []).append(comment)
    blocks_by_chunk: dict[str, list[BlockOccurrence]] = {}
    for block in snapshot.blocks:
        if snapshot.request.contains(block.occurred_at):
            blocks_by_chunk.setdefault(block.chunk_id, []).append(block)

    needed: list[NeededIntervention] = []
    ties: list[CausalIndeterminate] = []
    for chunk_id in sorted(blocks_by_chunk):
        candidates: list[NeededIntervention] = []
        claims = tuple(
            event for event in events_by_chunk.get(chunk_id, ()) if event.kind == "claimed"
        )
        for block in sorted(
            blocks_by_chunk[chunk_id],
            key=lambda occurrence: (occurrence.occurred_at, occurrence.id),
        ):
            next_claim = next(
                (claim for claim in claims if claim.occurred_at >= block.occurred_at),
                None,
            )
            if next_claim is None:
                continue
            if next_claim.occurred_at == block.occurred_at:
                ties.append(
                    _tie(
                        chunk_id,
                        "block_next_claim",
                        block.occurred_at,
                        block.id,
                        next_claim.evidence_id,
                    )
                )
                continue
            for comment in comments_by_chunk.get(chunk_id, ()):
                if comment.occurred_at == block.occurred_at:
                    ties.append(
                        _tie(
                            chunk_id,
                            "block_comment",
                            comment.occurred_at,
                            block.id,
                            comment.evidence_id,
                        )
                    )
                    continue
                if comment.occurred_at == next_claim.occurred_at:
                    ties.append(
                        _tie(
                            chunk_id,
                            "comment_next_claim",
                            comment.occurred_at,
                            comment.evidence_id,
                            next_claim.evidence_id,
                        )
                    )
                    continue
                if block.occurred_at < comment.occurred_at < next_claim.occurred_at:
                    candidates.append(
                        NeededIntervention(
                            chunk_id=chunk_id,
                            block_id=block.id,
                            comment_id=comment.evidence_id,
                            next_claim_id=next_claim.evidence_id,
                            block_at=block.occurred_at,
                            comment_at=comment.occurred_at,
                            next_claim_at=next_claim.occurred_at,
                        )
                    )
        if candidates:
            needed.append(
                min(
                    candidates,
                    key=lambda chain: (
                        chain.block_at,
                        chain.comment_at,
                        chain.next_claim_at,
                        chain.block_id,
                        chain.comment_id,
                        chain.next_claim_id,
                    ),
                )
            )
    return (
        tuple(needed),
        tuple(
            sorted(
                set(ties),
                key=lambda tie: (
                    tie.occurred_at,
                    tie.chunk_id,
                    tie.relation,
                    tie.evidence_ids,
                ),
            )
        ),
    )


def _comment_finding(
    comment: NormalizedComment,
    disposition: str,
) -> CommentFinding:
    return CommentFinding(
        evidence_id=comment.evidence_id,
        chunk_id=comment.chunk_id,
        author=comment.author,
        actor_class=comment.actor_class,
        occurred_at=comment.occurred_at,
        disposition=disposition,
    )


def _tie(
    chunk_id: str,
    relation: str,
    occurred_at: datetime,
    *evidence_ids: str,
) -> CausalIndeterminate:
    return CausalIndeterminate(
        chunk_id=chunk_id,
        relation=relation,
        occurred_at=occurred_at,
        evidence_ids=tuple(sorted(evidence_ids)),
    )
