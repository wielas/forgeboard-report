"""Pure dependency-gate auditing over normalized board and GitHub evidence."""

from __future__ import annotations

from collections.abc import Iterable

from forgeboard_report.domain import (
    ActorClass,
    BlockOccurrence,
    ChunkHandoff,
    DependencyAudit,
    DependencyEdgeFinding,
    DependencyRunFinding,
    DependencyWaitFinding,
    PullRequestFact,
    PullRequestRef,
    SourceSnapshot,
    UnexpectedBoardLinkFinding,
)
from forgeboard_report.errors import InvalidCoreError


def required_pull_request_refs(snapshot: SourceSnapshot) -> tuple[PullRequestRef, ...]:
    """Resolve the singular required parent handoff for every declared edge."""
    handoffs = _required_handoffs(snapshot)
    refs: set[PullRequestRef] = set()
    for edge in snapshot.edges:
        handoff = handoffs[(edge.parent_id, edge.child_id)]
        try:
            refs.add(PullRequestRef.parse(handoff.pr))
        except ValueError as error:
            raise InvalidCoreError(
                handoff.id, f"forge.chunk.v1 pr is not a canonical PR URL: {error}"
            ) from error
    return tuple(sorted(refs, key=_ref_key))


def audit(snapshot: SourceSnapshot, pull_requests: Iterable[PullRequestFact]) -> DependencyAudit:
    """Return one deterministic audit row per declared edge and extra board link."""
    return audit_dependencies(snapshot, pull_requests)


def audit_dependencies(
    snapshot: SourceSnapshot,
    pull_requests: Iterable[PullRequestFact],
) -> DependencyAudit:
    """Audit all dependency facts without source I/O or lifecycle recalculation."""
    handoffs = _required_handoffs(snapshot)
    facts = _facts_by_ref(pull_requests)
    links_by_edge: dict[tuple[str, str], list[str]] = {}
    declared = {(edge.parent_id, edge.child_id) for edge in snapshot.edges}
    unexpected: list[UnexpectedBoardLinkFinding] = []
    for link in snapshot.links:
        identity = (link.parent_chunk_id, link.child_chunk_id)
        if None not in identity and identity in declared:
            links_by_edge.setdefault(identity, []).append(link.evidence_id)  # type: ignore[arg-type]
        else:
            unexpected.append(
                UnexpectedBoardLinkFinding(
                    parent_chunk_id=link.parent_chunk_id,
                    child_chunk_id=link.child_chunk_id,
                    parent_task_id=link.parent_task_id,
                    child_task_id=link.child_task_id,
                    evidence_id=link.evidence_id,
                )
            )

    findings: list[DependencyEdgeFinding] = []
    for edge in snapshot.edges:
        handoff = handoffs[(edge.parent_id, edge.child_id)]
        fact = _required_fact(handoff, facts)
        child_runs = tuple(
            run
            for run in snapshot.runs
            if run.chunk_id == edge.child_id and snapshot.request.contains(run.started_at)
        )
        runs = _classify_runs(child_runs, fact.merged_at)
        waits = _classify_waits(snapshot, edge.child_id)
        link_ids = tuple(sorted(links_by_edge.get((edge.parent_id, edge.child_id), ())))
        findings.append(
            DependencyEdgeFinding(
                parent_id=edge.parent_id,
                child_id=edge.child_id,
                attachment="attached" if link_ids else "missing",
                link_evidence_ids=link_ids,
                handoff_id=handoff.id,
                handoff_at=_handoff_time(handoff),
                pull_request_id=fact.evidence_id,
                pull_request_url=fact.url,
                merged_at=fact.merged_at,
                observation="observed" if runs else "not_observed",
                runs=runs,
                waits=waits,
            )
        )
    return DependencyAudit(
        edges=tuple(sorted(findings, key=lambda finding: (finding.parent_id, finding.child_id))),
        unexpected_links=tuple(
            sorted(
                unexpected,
                key=lambda link: (
                    link.parent_chunk_id or "",
                    link.child_chunk_id or "",
                    link.parent_task_id,
                    link.child_task_id,
                ),
            )
        ),
    )


def _required_handoffs(snapshot: SourceSnapshot) -> dict[tuple[str, str], ChunkHandoff]:
    by_parent: dict[str, list[ChunkHandoff]] = {}
    for handoff in snapshot.handoffs:
        by_parent.setdefault(handoff.chunk_id, []).append(handoff)
    result: dict[tuple[str, str], ChunkHandoff] = {}
    for edge in snapshot.edges:
        candidates = by_parent.get(edge.parent_id, [])
        source = f"graph:{edge.parent_id}->{edge.child_id}"
        if not candidates:
            raise InvalidCoreError(source, "required parent completed-run handoff is missing")
        if len(candidates) != 1:
            raise InvalidCoreError(
                source, "required parent completed-run handoff is multiple or contradictory"
            )
        handoff = candidates[0]
        if handoff.occurred_at is None:
            raise InvalidCoreError(handoff.id, "completed-run handoff has no completion timestamp")
        result[(edge.parent_id, edge.child_id)] = handoff
    return result


def _facts_by_ref(
    pull_requests: Iterable[PullRequestFact],
) -> dict[PullRequestRef, PullRequestFact]:
    facts: dict[PullRequestRef, PullRequestFact] = {}
    for fact in pull_requests:
        ref = fact.ref
        if ref in facts:
            raise InvalidCoreError("github", f"contradictory duplicate PR facts for {ref.url!r}")
        facts[ref] = fact
    return facts


def _required_fact(
    handoff: ChunkHandoff,
    facts: dict[PullRequestRef, PullRequestFact],
) -> PullRequestFact:
    try:
        ref = PullRequestRef.parse(handoff.pr)
    except ValueError as error:
        raise InvalidCoreError(
            handoff.id, f"forge.chunk.v1 pr is not a canonical PR URL: {error}"
        ) from error
    try:
        return facts[ref]
    except KeyError as error:
        raise InvalidCoreError(
            handoff.id, f"required GitHub PR evidence is missing for {handoff.pr!r}"
        ) from error


def _handoff_time(handoff: ChunkHandoff):
    if handoff.occurred_at is None:
        raise InvalidCoreError(handoff.id, "completed-run handoff has no completion timestamp")
    return handoff.occurred_at


def _classify_runs(runs, merged_at):
    findings: list[DependencyRunFinding] = []
    for run in runs:
        if merged_at is None:
            classification = "parent_unmerged"
        elif run.started_at < merged_at:
            classification = "before_merge"
        elif run.started_at > merged_at:
            classification = "after_merge"
        else:
            classification = "indeterminate"
        findings.append(
            DependencyRunFinding(
                run_id=run.evidence_id,
                started_at=run.started_at,
                classification=classification,
            )
        )
    return tuple(sorted(findings, key=lambda finding: (finding.started_at, finding.run_id)))


def _classify_waits(snapshot: SourceSnapshot, child_id: str) -> tuple[DependencyWaitFinding, ...]:
    relevant = tuple(
        block
        for block in snapshot.blocks
        if block.chunk_id == child_id and snapshot.request.contains(block.occurred_at)
    )
    findings: list[DependencyWaitFinding] = []
    for block in relevant:
        if block.reason_class == "failing-prereq":
            findings.append(_observed_wait(snapshot, block))
        elif block.reason_class is None:
            findings.append(
                DependencyWaitFinding(
                    wait_id=block.id,
                    wait_at=block.occurred_at,
                    status="unclassified",
                    retry_run_id=None,
                    retry_started_at=None,
                    operator_comment_id=None,
                    operator_comment_at=None,
                    intervention="not_observed",
                )
            )
    if not findings:
        return (
            DependencyWaitFinding(
                wait_id=None,
                wait_at=None,
                status="not_observed",
                retry_run_id=None,
                retry_started_at=None,
                operator_comment_id=None,
                operator_comment_at=None,
                intervention="not_observed",
            ),
        )
    return tuple(sorted(findings, key=lambda finding: (finding.wait_at, finding.wait_id or "")))


def _observed_wait(snapshot: SourceSnapshot, wait: BlockOccurrence) -> DependencyWaitFinding:
    tied_run = next(
        (
            run
            for run in snapshot.runs
            if run.chunk_id == wait.chunk_id
            and run.status == "done"
            and run.outcome == "completed"
            and run.started_at == wait.occurred_at
        ),
        None,
    )
    retries = tuple(
        run
        for run in snapshot.runs
        if run.chunk_id == wait.chunk_id
        and run.status == "done"
        and run.outcome == "completed"
        and run.started_at > wait.occurred_at
    )
    retry = min(retries, key=lambda run: (run.started_at, run.id), default=None)
    if retry is None:
        return DependencyWaitFinding(
            wait_id=wait.id,
            wait_at=wait.occurred_at,
            status="observed",
            retry_run_id=None,
            retry_started_at=None,
            operator_comment_id=None,
            operator_comment_at=None,
            intervention="not_observed",
        )
    comments = tuple(
        comment
        for comment in snapshot.comments
        if comment.chunk_id == wait.chunk_id and comment.actor_class is ActorClass.OPERATOR
    )
    between = tuple(
        comment for comment in comments if wait.occurred_at < comment.occurred_at < retry.started_at
    )
    if tied_run is not None:
        comment = None
        intervention = "indeterminate"
    elif between:
        comment = min(between, key=lambda item: (item.occurred_at, item.id))
        intervention = "before_retry"
    else:
        tied = next(
            (
                comment
                for comment in comments
                if comment.occurred_at in {wait.occurred_at, retry.started_at}
            ),
            None,
        )
        comment = tied
        intervention = "indeterminate" if tied else "not_observed"
    return DependencyWaitFinding(
        wait_id=wait.id,
        wait_at=wait.occurred_at,
        status="observed",
        retry_run_id=retry.evidence_id,
        retry_started_at=retry.started_at,
        operator_comment_id=comment.evidence_id if comment else None,
        operator_comment_at=comment.occurred_at if comment else None,
        intervention=intervention,
    )


def _ref_key(ref: PullRequestRef) -> tuple[str, str, str, int]:
    return (ref.host, ref.owner, ref.repository, ref.number)
