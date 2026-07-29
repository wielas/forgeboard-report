"""BDD coverage for canonical lifecycle normalization and pure metrics."""

from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from fixtures.normalize import (
    at,
    block_snapshot,
    boundary_snapshot,
    conflicting_block_raw,
    intervention_snapshot,
    multiple_verdict_snapshot,
    no_verdict_snapshots,
)
from forgeboard_report.domain import ActorClass, Availability, EvidenceRole
from forgeboard_report.errors import InvalidCoreError
from forgeboard_report.metrics import calculate_metrics
from forgeboard_report.normalize import normalize_sources

scenarios("../features/lifecycle_metrics.feature")


@given(
    "completed chunks with multiple in-period canonical verdicts",
    target_fixture="lifecycle_case",
)
def multiple_verdict_case():
    return {"snapshots": (multiple_verdict_snapshot(),), "results": ()}


@when("lifecycle metrics are calculated")
def calculate_lifecycle_case(lifecycle_case) -> None:
    lifecycle_case["results"] = tuple(
        calculate_metrics(snapshot) for snapshot in lifecycle_case["snapshots"]
    )


@then("each bounced chunk enters the numerator once and all verdict ids remain evidence")
def assert_bounce_evidence(lifecycle_case) -> None:
    bounce = lifecycle_case["results"][0].bounce
    assert (bounce.numerator, bounce.denominator, bounce.rate) == (1, 2, Decimal("0.5"))
    assert bounce.bounced_chunk_ids == ("A",)
    assert tuple(verdict.evidence_id for verdict in bounce.verdicts) == (
        "hermes:run:1",
        "hermes:run:2",
        "hermes:run:3",
    )


@then("each score dimension has its own exact Decimal mean")
def assert_dimension_means(lifecycle_case) -> None:
    quality = lifecycle_case["results"][0].quality
    assert quality.spec_fidelity.mean == Decimal("6")
    assert quality.scenario_integrity.mean == Decimal("5")
    assert quality.architectural_conformance.mean == Decimal("5")
    assert quality.scenario_integrity.total == Decimal("15")
    assert quality.scenario_integrity.count == 3


@given(
    "no canonical verdicts and a completed chunk without one",
    target_fixture="lifecycle_case",
)
def no_verdict_case():
    return {"snapshots": no_verdict_snapshots(), "results": ()}


@when("absent-verdict lifecycle metrics are calculated")
def calculate_absent_verdict_case(lifecycle_case) -> None:
    calculate_lifecycle_case(lifecycle_case)


@then("quality and a zero-denominator bounce value are unavailable")
def assert_absent_values(lifecycle_case) -> None:
    missing, empty = lifecycle_case["results"]
    for dimension in (
        missing.quality.spec_fidelity,
        missing.quality.scenario_integrity,
        missing.quality.architectural_conformance,
    ):
        assert dimension.status is Availability.UNAVAILABLE
        assert dimension.mean is None
        assert dimension.total == Decimal(0)
        assert dimension.count == 0
    assert empty.bounce.status is Availability.UNAVAILABLE
    assert empty.bounce.rate is None


@then("missing canonical verdict coverage is listed")
def assert_missing_coverage(lifecycle_case) -> None:
    missing, empty = lifecycle_case["results"]
    assert missing.bounce.completed_without_canonical_verdict == ("A",)
    assert empty.bounce.completed_without_canonical_verdict == ()


@given(
    "paired block evidence, exact reasons, native kinds, an unknown schema, and a conflict",
    target_fixture="block_case",
)
def canonical_block_case():
    return {
        "snapshot": block_snapshot(),
        "conflict_sources": conflicting_block_raw(),
        "metrics": None,
        "error": None,
    }


@when("block evidence is normalized and measured")
def normalize_and_measure_blocks(block_case) -> None:
    block_case["metrics"] = calculate_metrics(block_case["snapshot"])
    raw, graph, request = block_case["conflict_sources"]
    try:
        normalize_sources(raw, graph, request)
    except InvalidCoreError as error:
        block_case["error"] = error


@then("paired block evidence is deduplicated and exact reasons are counted")
def assert_block_deduplication(block_case) -> None:
    snapshot = block_case["snapshot"]
    assert len(snapshot.blocks) == 3
    paired = next(block for block in snapshot.blocks if block.run_id == 10)
    assert paired.evidence_ids == (
        "hermes:card:task-A",
        "hermes:event:20",
        "hermes:run:10",
    )
    block_metrics = block_case["metrics"].blocks
    assert block_metrics.total == 3
    assert [(reason.reason_class, reason.count) for reason in block_metrics.reasons] == [
        ("needs-human", 1)
    ]


@then("noncanonical block evidence is warned and unclassified")
def assert_noncanonical_blocks(block_case) -> None:
    snapshot = block_case["snapshot"]
    assert snapshot.warnings[0].code == "unknown-metadata-schema"
    assert snapshot.warnings[0].schema == "future.block.v2"
    assert len(block_case["metrics"].blocks.unclassified) == 2
    assert {kind for block in snapshot.blocks for kind in block.native_kinds} == {
        "dependency",
        "needs_input",
        "transient",
    }


@then("conflicting canonical block reasons fail closed")
def assert_block_conflict(block_case) -> None:
    assert isinstance(block_case["error"], InvalidCoreError)
    assert "conflicting canonical reason_class" in str(block_case["error"])


@given(
    "exact operator, worker, prejudge, automated, and unknown authors around lifecycle events",
    target_fixture="lifecycle_case",
)
def intervention_case():
    return {"snapshots": (intervention_snapshot(),), "results": ()}


@when("intervention metrics are calculated")
def calculate_intervention_case(lifecycle_case) -> None:
    calculate_lifecycle_case(lifecycle_case)


@then("only strict post-dispatch pre-terminal operator comments count")
def assert_strict_comments(lifecycle_case) -> None:
    intervention = lifecycle_case["results"][0].intervention
    assert intervention.qualifying_comment_count == 2
    assert tuple(comment.evidence_id for comment in intervention.qualifying_comments) == (
        "hermes:comment:30",
        "hermes:comment:35",
    )
    assert {comment.actor_class for comment in intervention.comments} == set(ActorClass)
    dispositions = {comment.evidence_id: comment.disposition for comment in intervention.comments}
    assert dispositions["hermes:comment:36"] == "indeterminate"
    assert dispositions["hermes:comment:37"] == "post_terminal"


@then("a block-comment-next-claim chain counts its chunk once")
def assert_needed_chain(lifecycle_case) -> None:
    intervention = lifecycle_case["results"][0].intervention
    assert intervention.needed_to_execute_chunk_count == 1
    assert intervention.needed_to_execute[0].chunk_id == "A"
    assert intervention.needed_to_execute[0].comment_id == "hermes:comment:35"


@given(
    "records at both period bounds and equal causal timestamps",
    target_fixture="lifecycle_case",
)
def period_boundary_case():
    return {"snapshots": (boundary_snapshot(),), "results": ()}


@when("boundary lifecycle metrics are calculated")
def calculate_boundary_case(lifecycle_case) -> None:
    calculate_lifecycle_case(lifecycle_case)


@then("the lower bound contributes and the upper bound does not")
def assert_period_bounds(lifecycle_case) -> None:
    metrics = lifecycle_case["results"][0]
    assert metrics.bounce.completed_chunk_ids == ("LOWER", "TIE")
    assert tuple(score.evidence_id for score in metrics.quality.spec_fidelity.scores) == (
        "hermes:run:40",
    )
    assert metrics.quality.spec_fidelity.mean == Decimal("1")


@then("minimal boundary history is context only")
def assert_boundary_context(lifecycle_case) -> None:
    snapshot = lifecycle_case["snapshots"][0]
    refs = {ref.id: ref for ref in snapshot.evidence}
    assert refs["hermes:event:40"].role is EvidenceRole.CONTEXT
    assert refs["hermes:event:42"].occurred_at == at(50)
    assert refs["hermes:event:42"].role is EvidenceRole.CONTEXT
    chain = lifecycle_case["results"][0].intervention.needed_to_execute[0]
    assert chain.next_claim_id == "hermes:event:42"


@then("timestamp ties are indeterminate rather than ordered")
def assert_timestamp_ties(lifecycle_case) -> None:
    intervention = lifecycle_case["results"][0].intervention
    assert any(
        tie.relation == "block_comment" and tie.chunk_id == "TIE"
        for tie in intervention.indeterminate
    )
    assert intervention.needed_to_execute_chunk_count == 1
