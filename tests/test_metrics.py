"""Focused tests for canonical normalization and the pure lifecycle core."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from decimal import Decimal, localcontext

import pytest

from fixtures.normalize import (
    BOARD,
    at,
    block_metadata,
    card,
    chunk_metadata,
    comment,
    event,
    graph,
    judge_metadata,
    normalize_fixture,
    raw_snapshot,
    request,
    run,
)
from forgeboard_report.domain import (
    ActorClass,
    Availability,
    EvidenceRole,
    RawHermesLink,
)
from forgeboard_report.errors import InvalidCoreError, InvalidSchemaError
from forgeboard_report.metrics import calculate, calculate_metrics
from forgeboard_report.normalize import classify_author, normalize, normalize_sources


def test_normalized_snapshot_and_metric_results_are_frozen_and_stably_sorted() -> None:
    snapshot = normalize_fixture(
        ("B", "A"),
        cards=(card("B", completed_at=at(41)), card("A", completed_at=at(40))),
        runs=(
            run(2, "B", started_at=at(12), ended_at=at(22), metadata=judge_metadata()),
            run(1, "A", started_at=at(11), ended_at=at(21), metadata=judge_metadata()),
        ),
    )
    metrics = calculate(snapshot)

    assert tuple(card.chunk_id for card in snapshot.cards) == ("A", "B")
    assert tuple(verdict.id for verdict in snapshot.verdicts) == (
        "hermes:run:1",
        "hermes:run:2",
    )
    assert tuple(ref.id for ref in snapshot.evidence) == tuple(
        sorted(ref.id for ref in snapshot.evidence)
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.cards = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        metrics.bounce.numerator = 7  # type: ignore[misc]


@pytest.mark.parametrize(
    "cards",
    [
        (card("A", key="forge-board-a"),),
        (
            card("A", task="task-one"),
            card("A", task="task-two"),
        ),
    ],
)
def test_bootstrap_join_requires_one_exact_case_sensitive_match(cards) -> None:
    with pytest.raises(InvalidCoreError, match="expected exactly one"):
        normalize_sources(raw_snapshot(cards=cards), graph(("A",)), request())


def test_one_opaque_task_cannot_map_to_two_graph_chunks() -> None:
    cards = (
        card("A", task="same-task"),
        card("B", task="same-task"),
    )
    with pytest.raises(InvalidCoreError, match="multiple graph chunks"):
        normalize_sources(raw_snapshot(cards=cards), graph(("A", "B")), request())


def test_only_exact_mapped_task_ids_drive_all_later_joins() -> None:
    misleading = card("OTHER", task="opaque-other", key="unrelated")
    misleading = replace(
        misleading,
        body=f"mentions {BOARD}-A and task-A",
    )
    raw = raw_snapshot(
        cards=(card("A"), card("B"), misleading),
        runs=(
            run(1, "A", ended_at=at(20)),
            run(2, "OTHER", task="opaque-other", ended_at=at(21)),
        ),
        events=(
            event(1, "A", "claimed", at(11)),
            event(2, "OTHER", "claimed", at(12), task="opaque-other"),
        ),
        comments=(
            comment(1, "A", "operator", at(13)),
            comment(2, "OTHER", "operator", at(14), task="opaque-other"),
        ),
        links=(
            RawHermesLink("task-A", "task-B"),
            RawHermesLink("opaque-other", "task-A"),
        ),
    )
    snapshot = normalize(raw, graph(("A", "B"), edges=(("A", "B"),)), request())

    assert tuple(run.id for run in snapshot.runs) == (1,)
    assert tuple(event.id for event in snapshot.events) == (1,)
    assert tuple(comment.id for comment in snapshot.comments) == (1,)
    assert snapshot.links[0].evidence_id == "hermes:link:task-A->task-B"
    evidence_ids = {ref.id for ref in snapshot.evidence}
    assert "graph:A->B" in evidence_ids
    assert "hermes:link:task-A->task-B" in evidence_ids
    assert snapshot.hermes_source_files == ()


def test_timestamp_inputs_normalize_to_utc_and_block_run_end_is_fallback() -> None:
    ended_epoch = int(at(20).timestamp())
    snapshot = normalize_fixture(
        ("A",),
        cards=(replace(card("A", status="blocked"), created_at=ended_epoch - 1200),),
        runs=(
            run(
                1,
                "A",
                status="blocked",
                outcome="blocked",
                started_at="2026-07-29T01:10:00+01:00",
                ended_at=ended_epoch,
                metadata=block_metadata("capacity"),
            ),
        ),
    )

    assert snapshot.runs[0].started_at == at(10)
    assert snapshot.blocks[0].occurred_at == at(20)
    assert snapshot.blocks[0].evidence_ids == ("hermes:run:1",)
    assert snapshot.evidence[-1].role is EvidenceRole.CONTRIBUTING


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ('{"schema":"forge.judge.v1","outcome":"pass"}', "missing keys"),
        (
            '{"schema":"forge.judge.v1","outcome":"pass","scores":{},"extra":1}',
            "unknown keys",
        ),
        (
            '{"schema":"forge.judge.v1","outcome":"approve","scores":'
            '{"spec_fidelity":"1","scenario_integrity":"2",'
            '"architectural_conformance":"3"}}',
            "outcome",
        ),
        (
            '{"schema":"forge.judge.v1","outcome":"pass","scores":[]}',
            "scores must be an object",
        ),
        (
            '{"schema":"forge.judge.v1","outcome":"pass","scores":'
            '{"spec_fidelity":"1","scenario_integrity":"2"}}',
            "missing keys",
        ),
        (
            '{"schema":"forge.judge.v1","outcome":"pass","scores":'
            '{"spec_fidelity":"NaN","scenario_integrity":"2",'
            '"architectural_conformance":"3"}}',
            "finite Decimal",
        ),
        (
            '{"schema":"forge.judge.v1","outcome":"pass","scores":'
            '{"spec_fidelity":true,"scenario_integrity":"2",'
            '"architectural_conformance":"3"}}',
            "finite Decimal",
        ),
        ('{"schema":"forge.block.v1","reason_class":""}', "nonempty exact string"),
        (
            '{"schema":"forge.chunk.v1","chunk_id":"A","pr":"/relative"}',
            "complete URL",
        ),
    ],
)
def test_declared_malformed_envelopes_fail_schema_validation(
    metadata: str,
    message: str,
) -> None:
    with pytest.raises(InvalidSchemaError, match=message):
        normalize_fixture(
            ("A",),
            runs=(run(1, "A", ended_at=at(20), metadata=metadata),),
        )


def test_numeric_decimal_scores_and_complete_chunk_handoffs_decode() -> None:
    numeric_scores = (
        '{"schema":"forge.judge.v1","outcome":"pass","scores":'
        '{"spec_fidelity":1,"scenario_integrity":2.5,'
        '"architectural_conformance":3}}'
    )
    snapshot = normalize_fixture(
        ("A",),
        runs=(
            run(1, "A", ended_at=at(20), metadata=numeric_scores),
            run(2, "A", ended_at=at(21), metadata=chunk_metadata("A")),
        ),
    )

    assert snapshot.verdicts[0].scores.scenario_integrity == Decimal("2.5")
    assert snapshot.handoffs[0].pr == "https://github.com/acme/repo/pull/1"


def test_chunk_handoff_id_mismatch_and_unfinished_judge_fail_core_validation() -> None:
    with pytest.raises(InvalidCoreError, match="does not match mapped chunk"):
        normalize_fixture(
            ("A",),
            runs=(run(1, "A", ended_at=at(20), metadata=chunk_metadata("B")),),
        )
    with pytest.raises(InvalidCoreError, match="finished run ended_at"):
        normalize_fixture(
            ("A",),
            runs=(run(1, "A", metadata=judge_metadata()),),
        )


def test_noncanonical_metadata_forms_become_stably_sorted_typed_warnings() -> None:
    snapshot = normalize_fixture(
        ("A",),
        runs=(
            run(3, "A", ended_at=at(23), metadata=b"\xff"),
            run(2, "A", ended_at=at(22), metadata="[]"),
            run(1, "A", ended_at=at(21), metadata='{"value":"no schema"}'),
            run(4, "A", ended_at=at(24), metadata='{"schema":"future.v9"}'),
        ),
    )

    assert tuple(warning.evidence_id for warning in snapshot.warnings) == (
        "hermes:run:1",
        "hermes:run:2",
        "hermes:run:3",
        "hermes:run:4",
    )
    assert snapshot.warnings[-1].code == "unknown-metadata-schema"
    assert snapshot.warnings[-1].schema == "future.v9"


@pytest.mark.parametrize(
    "metadata",
    [
        '{"schema":"forge.block.v1","schema":"forge.block.v1","reason_class":"x"}',
        '{"schema":"forge.block.v1","reason_class":NaN}',
    ],
)
def test_invalid_nondecodable_json_is_noncanonical_warning(metadata: str) -> None:
    snapshot = normalize_fixture(
        ("A",),
        runs=(run(1, "A", ended_at=at(20), metadata=metadata),),
    )
    assert snapshot.warnings[0].code == "noncanonical-metadata"


def test_paired_block_uses_earliest_event_and_retains_all_source_refs() -> None:
    snapshot = normalize_fixture(
        ("A",),
        cards=(card("A", status="blocked", block_kind="dependency"),),
        runs=(
            run(
                1,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(25),
                metadata=block_metadata("exact"),
            ),
        ),
        events=(
            event(2, "A", "blocked", at(22), run_id=1, payload=b"not-json"),
            event(1, "A", "blocked", at(20), run_id=1, payload='{"kind":"native"}'),
        ),
    )
    block = snapshot.blocks[0]

    assert block.occurred_at == at(20)
    assert block.native_kinds == ("dependency", "native")
    assert block.evidence_ids == (
        "hermes:card:task-A",
        "hermes:event:1",
        "hermes:event:2",
        "hermes:run:1",
    )
    assert calculate_metrics(snapshot).blocks.reasons[0].occurrence_ids == (block.id,)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            raw_snapshot(
                cards=(card("A", status="blocked"),),
                runs=(run(1, "A", status="blocked", outcome="blocked"),),
            ),
            "ended_at fallback",
        ),
        (
            raw_snapshot(
                cards=(card("A"),),
                runs=(
                    run(
                        1,
                        "A",
                        started_at=at(30),
                        ended_at=at(20),
                    ),
                ),
            ),
            "precedes started_at",
        ),
        (
            raw_snapshot(cards=(card("A", status="done"),)),
            "requires completed_at",
        ),
        (
            raw_snapshot(cards=(card("A", status="ready", completed_at=at(20)),)),
            "contradicts completed_at",
        ),
    ],
)
def test_contradictory_lifecycle_records_fail_core(raw, message: str) -> None:
    with pytest.raises(InvalidCoreError, match=message):
        normalize_sources(raw, graph(("A",)), request())


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            raw_snapshot(
                cards=(card("A"),),
                events=(
                    event(1, "A", "claimed", at(20)),
                    event(1, "A", "claimed", at(21)),
                ),
            ),
            "duplicate event id",
        ),
        (
            raw_snapshot(
                cards=(card("A"),),
                comments=(
                    comment(1, "A", "operator", at(20)),
                    comment(1, "A", "operator", at(21)),
                ),
            ),
            "duplicate comment id",
        ),
        (
            raw_snapshot(
                cards=(card("A"),),
                runs=(
                    run(1, "A", ended_at=at(20)),
                    run(1, "A", ended_at=at(21)),
                ),
            ),
            "duplicate run id",
        ),
        (
            raw_snapshot(
                cards=(card("A"), card("B")),
                links=(
                    RawHermesLink("task-A", "task-B"),
                    RawHermesLink("task-A", "task-B"),
                ),
            ),
            "duplicate mapped board link",
        ),
    ],
)
def test_duplicate_stable_source_ids_fail_closed(raw, message: str) -> None:
    chunk_ids = ("A", "B") if len(raw.cards) == 2 else ("A",)
    with pytest.raises(InvalidCoreError, match=message):
        normalize_sources(raw, graph(chunk_ids), request())


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (datetime(2026, 7, 29), "timezone-aware"),
        ("not-a-time", "aware ISO-8601"),
        (False, "aware timestamp"),
        (object(), "aware timestamp"),
    ],
)
def test_invalid_required_timestamps_fail_core(value: object, message: str) -> None:
    raw = raw_snapshot(cards=(replace(card("A"), created_at=value),))
    with pytest.raises(InvalidCoreError, match=message):
        normalize_sources(raw, graph(("A",)), request())


def test_resolved_board_must_match_request_after_request_case_normalization() -> None:
    uppercase_request = request(board_slug="FORGE-BOARD")
    snapshot = normalize_sources(
        raw_snapshot(cards=(card("A"),)),
        graph(("A",)),
        uppercase_request,
    )
    assert snapshot.request.board_slug == "FORGE-BOARD"

    with pytest.raises(InvalidCoreError, match="does not match request"):
        normalize_sources(
            raw_snapshot(cards=(card("A"),), board_slug="other"),
            graph(("A",)),
            request(),
        )


@pytest.mark.parametrize(
    ("author", "operators", "expected"),
    [
        ("operator", ("operator",), ActorClass.OPERATOR),
        ("builder", ("builder",), ActorClass.OPERATOR),
        ("builder", ("operator",), ActorClass.WORKER),
        ("forge-codex-lane", ("operator",), ActorClass.WORKER),
        ("forge-prejudge", ("operator",), ActorClass.PREJUDGE),
        ("forge-digest", ("operator",), ActorClass.AUTOMATED),
        ("forge-orchestrator", ("operator",), ActorClass.AUTOMATED),
        ("Builder", ("operator",), ActorClass.UNCLASSIFIED),
    ],
)
def test_author_classification_is_exact_with_operator_precedence(
    author: str,
    operators: tuple[str, ...],
    expected: ActorClass,
) -> None:
    assert classify_author(author, operators) is expected


def test_quality_includes_in_period_verdict_without_card_completion() -> None:
    snapshot = normalize_fixture(
        ("A",),
        runs=(
            run(
                1,
                "A",
                ended_at=at(20),
                metadata=judge_metadata("bounce", ("4", "5", "6")),
            ),
        ),
    )
    metrics = calculate_metrics(snapshot)

    assert metrics.bounce.status is Availability.UNAVAILABLE
    assert metrics.bounce.verdicts == ()
    assert metrics.quality.spec_fidelity.mean == Decimal("4")


def test_decimal_division_is_independent_of_the_callers_decimal_context() -> None:
    snapshot = normalize_fixture(
        ("A", "B", "C"),
        cards=tuple(card(chunk, completed_at=at(40)) for chunk in ("A", "B", "C")),
        runs=(run(1, "A", ended_at=at(20), metadata=judge_metadata("bounce")),),
    )
    with localcontext() as caller_context:
        caller_context.prec = 3
        rate = calculate_metrics(snapshot).bounce.rate

    assert rate == Decimal("0.3333333333333333333333333333")


def test_intervention_dispositions_cover_no_dispatch_predispatch_and_dispatch_tie() -> None:
    snapshot = normalize_fixture(
        ("NO-DISPATCH", "ORDERED"),
        cards=(
            card("NO-DISPATCH"),
            card("ORDERED", completed_at=at(45)),
        ),
        events=(
            event(1, "ORDERED", "claimed", at(20)),
            event(2, "ORDERED", "completed", at(45)),
        ),
        comments=(
            comment(1, "NO-DISPATCH", "operator", at(20)),
            comment(2, "ORDERED", "operator", at(10)),
            comment(3, "ORDERED", "operator", at(20)),
        ),
    )
    intervention = calculate_metrics(snapshot).intervention
    dispositions = {finding.evidence_id: finding.disposition for finding in intervention.comments}

    assert dispositions == {
        "hermes:comment:1": "no_dispatch",
        "hermes:comment:2": "pre_dispatch",
        "hermes:comment:3": "indeterminate",
    }
    assert intervention.indeterminate[0].relation == "dispatch_comment"


def test_equal_block_claim_and_comment_claim_times_are_indeterminate() -> None:
    snapshot = normalize_fixture(
        ("BLOCK-CLAIM", "COMMENT-CLAIM"),
        cards=(
            card("BLOCK-CLAIM", completed_at=at(45)),
            card("COMMENT-CLAIM", completed_at=at(45)),
        ),
        runs=(
            run(
                1,
                "BLOCK-CLAIM",
                status="blocked",
                outcome="blocked",
                ended_at=at(16),
            ),
            run(
                2,
                "COMMENT-CLAIM",
                status="blocked",
                outcome="blocked",
                ended_at=at(16),
            ),
        ),
        events=(
            event(1, "BLOCK-CLAIM", "claimed", at(5)),
            event(2, "BLOCK-CLAIM", "blocked", at(15), run_id=1),
            event(3, "BLOCK-CLAIM", "claimed", at(15)),
            event(4, "COMMENT-CLAIM", "claimed", at(5)),
            event(5, "COMMENT-CLAIM", "blocked", at(15), run_id=2),
            event(6, "COMMENT-CLAIM", "claimed", at(30)),
        ),
        comments=(
            comment(1, "BLOCK-CLAIM", "operator", at(20)),
            comment(2, "COMMENT-CLAIM", "operator", at(30)),
        ),
    )
    intervention = calculate_metrics(snapshot).intervention

    assert intervention.needed_to_execute_chunk_count == 0
    assert {tie.relation for tie in intervention.indeterminate} == {
        "block_next_claim",
        "comment_next_claim",
    }
