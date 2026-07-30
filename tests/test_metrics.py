"""Focused tests for canonical normalization and the pure lifecycle core."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
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
    JudgeOutcome,
    RawHermesLink,
)
from forgeboard_report.errors import InvalidCoreError, InvalidSchemaError
from forgeboard_report.metrics import calculate, calculate_metrics
from forgeboard_report.normalize import classify_author, normalize, normalize_sources


def _judge_metadata_with(**overrides: object) -> str:
    metadata = json.loads(judge_metadata())
    metadata.update(overrides)
    return json.dumps(metadata)


def _judge_metadata_without(*keys: str) -> str:
    metadata = json.loads(judge_metadata())
    for key in keys:
        del metadata[key]
    return json.dumps(metadata)


def _judge_finding_with(**overrides: object) -> dict[str, object]:
    finding: dict[str, object] = {
        "dimension": "spec_fidelity",
        "severity": "fix",
        "evidence": "src/forgeboard_report/normalize.py: judge validation",
        "action": "Correct the judge envelope.",
    }
    finding.update(overrides)
    return finding


def _judge_scores_with(**overrides: object) -> dict[str, object]:
    scores = json.loads(judge_metadata())["scores"]
    scores.update(overrides)
    return scores


def test_normalized_snapshot_and_metric_results_are_frozen_and_stably_sorted() -> None:
    snapshot = normalize_fixture(
        ("B", "A"),
        cards=(card("B", completed_at=at(41)), card("A", completed_at=at(40))),
        runs=(
            run(
                2,
                "B",
                started_at=at(12),
                ended_at=at(22),
                metadata=judge_metadata(chunk_id="B"),
            ),
            run(1, "A", started_at=at(11), ended_at=at(21), metadata=judge_metadata()),
        ),
    )
    metrics = calculate(snapshot)

    assert tuple(card.chunk_id for card in snapshot.cards) == ("A", "B")
    assert tuple(verdict.id for verdict in snapshot.verdicts) == (
        "hermes:run:1",
        "hermes:run:2",
    )
    assert tuple(
        (ref.occurred_at or datetime.min.replace(tzinfo=UTC), ref.id) for ref in snapshot.evidence
    ) == tuple(
        sorted(
            (
                ref.occurred_at or datetime.min.replace(tzinfo=UTC),
                ref.id,
            )
            for ref in snapshot.evidence
        )
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
        (
            '{"schema":"forge.judge.v1","scores":'
            '{"spec_fidelity":"1","scenario_integrity":"2",'
            '"architectural_conformance":"3"}}',
            "missing keys",
        ),
        (
            _judge_metadata_with(verdict="pass"),
            "verdict",
        ),
        (
            _judge_metadata_with(verdict="APPROVE"),
            "verdict",
        ),
        (
            _judge_metadata_with(scores=[]),
            "scores must be an object",
        ),
        (
            _judge_metadata_with(pr="/relative/path"),
            "complete URL",
        ),
        (
            _judge_metadata_with(pr="not-a-url"),
            "complete URL",
        ),
        (
            _judge_metadata_with(findings="not-a-list"),
            "must be a list",
        ),
        (
            _judge_metadata_with(findings=None),
            "must be a list",
        ),
        (
            _judge_metadata_with(findings=[None]),
            "must be an object",
        ),
        (
            _judge_metadata_with(findings=[{"dimension": "spec_fidelity"}]),
            "missing keys",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(dimension="unknown_dim")]),
            "must be one of",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(severity="critical")]),
            "must be one of",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(evidence="")]),
            "nonempty string",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(evidence=42)]),
            "nonempty string",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(action="")]),
            "nonempty string",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(action=42)]),
            "nonempty string",
        ),
        (
            _judge_metadata_with(nits_as_cards="not-a-list"),
            "must be a list",
        ),
        (
            _judge_metadata_with(nits_as_cards=[42]),
            "must be a string",
        ),
        (
            _judge_metadata_with(spot_check_suggestion=42),
            "must be a string",
        ),
        (
            _judge_metadata_with(judge_model=42),
            "must be a string",
        ),
        (
            _judge_metadata_with(tokens_estimate=-1),
            "nonnegative integer",
        ),
        (
            _judge_metadata_with(tokens_estimate=1.5),
            "nonnegative integer",
        ),
        (
            _judge_metadata_with(tokens_estimate="string"),
            "nonnegative integer",
        ),
        (
            _judge_metadata_with(unknown_example="rejected"),
            "unknown",
        ),
        (
            _judge_metadata_with(scores=_judge_scores_with(unknown_score=1)),
            "unknown",
        ),
        (
            _judge_metadata_with(findings=[_judge_finding_with(unknown_field="x")]),
            "unknown",
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


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            _judge_metadata_with(
                scores={
                    "spec_fidelity": "2",
                    "scenario_integrity": 3,
                    "architectural_conformance": 1,
                    "scope_discipline": 3,
                    "debt_honesty": 2,
                    "doc_reconciliation": 2,
                }
            ),
            "must be an integer",
        ),
        (
            _judge_metadata_with(
                scores={
                    "spec_fidelity": 2,
                    "scenario_integrity": 1.5,
                    "architectural_conformance": 1,
                    "scope_discipline": 3,
                    "debt_honesty": 2,
                    "doc_reconciliation": 2,
                }
            ),
            "must be an integer",
        ),
        (
            _judge_metadata_with(
                scores={
                    "spec_fidelity": 2,
                    "scenario_integrity": 3,
                    "architectural_conformance": 1,
                }
            ),
            "missing keys",
        ),
        (_judge_metadata_with(chunk_id="B"), "does not match mapped chunk"),
        (_judge_metadata_without("chunk_id"), "missing keys"),
    ],
)
def test_judge_envelopes_reject_noninteger_incomplete_or_wrong_chunk_data(
    metadata: str,
    message: str,
) -> None:
    with pytest.raises(InvalidSchemaError, match=message):
        normalize_fixture(
            ("A",),
            runs=(run(1, "A", ended_at=at(20), metadata=metadata),),
        )


@pytest.mark.parametrize(
    "score",
    [-1, 4],
)
def test_judge_scores_reject_integers_outside_zero_to_three(score: int) -> None:
    metadata = json.loads(judge_metadata())
    metadata["scores"]["spec_fidelity"] = score

    with pytest.raises(InvalidSchemaError, match="0-3 range"):
        normalize_fixture(
            ("A",),
            runs=(
                run(
                    1,
                    "A",
                    ended_at=at(20),
                    metadata=json.dumps(metadata),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("approve", JudgeOutcome.PASS),
        ("approve-with-nits", JudgeOutcome.PASS),
        ("bounce", JudgeOutcome.BOUNCE),
    ],
)
def test_declared_valid_judge_envelopes_map_rubric_verdicts(
    verdict: str,
    expected: JudgeOutcome,
) -> None:
    snapshot = normalize_fixture(
        ("A",),
        runs=(
            run(
                1,
                "A",
                ended_at=at(20),
                metadata=judge_metadata(verdict),
            ),
        ),
    )

    assert snapshot.verdicts[0].outcome is expected


def test_integer_scores_and_complete_chunk_handoffs_decode() -> None:
    integer_scores = judge_metadata(scores=(0, 2, 3, 3, 2, 2))
    snapshot = normalize_fixture(
        ("A",),
        cards=(card("A", completed_at=at(21)),),
        runs=(
            run(1, "A", ended_at=at(20), metadata=integer_scores),
            run(2, "A", ended_at=at(21), metadata=chunk_metadata("A")),
        ),
        events=(event(1, "A", "completed", at(21), run_id=2),),
    )

    assert snapshot.verdicts[0].scores.spec_fidelity == Decimal("0")
    assert snapshot.verdicts[0].scores.scenario_integrity == Decimal("2")
    assert snapshot.verdicts[0].scores.architectural_conformance == Decimal("3")
    assert snapshot.handoffs[0].pr == "https://github.com/acme/repo/pull/1"


def test_full_rubric_judge_envelope_decodes_report_dimensions() -> None:
    metadata = json.dumps(
        {
            "schema": "forge.judge.v1",
            "chunk_id": "A",
            "pr": "https://github.com/acme/repo/pull/4",
            "verdict": "approve-with-nits",
            "scores": {
                "spec_fidelity": 3,
                "scenario_integrity": 2,
                "architectural_conformance": 3,
                "scope_discipline": 3,
                "debt_honesty": 2,
                "doc_reconciliation": 2,
            },
            "findings": [
                {
                    "dimension": "scenario_integrity",
                    "severity": "nit",
                    "evidence": "tests/test_metrics.py: full envelope",
                    "action": "Keep the regression envelope complete.",
                }
            ],
            "nits_as_cards": ["CARD?: preserve additive judge metadata"],
            "spot_check_suggestion": "Inspect normalize.py decoder dispatch.",
            "judge_model": "fixture-judge",
            "tokens_estimate": 1234,
        }
    )

    snapshot = normalize_fixture(
        ("A",),
        runs=(run(1, "A", ended_at=at(20), metadata=metadata),),
    )
    verdict = snapshot.verdicts[0]

    assert verdict.outcome is JudgeOutcome.PASS
    assert verdict.scores.spec_fidelity == Decimal("3")
    assert verdict.scores.scenario_integrity == Decimal("2")
    assert verdict.scores.architectural_conformance == Decimal("3")


def test_additive_block_and_chunk_envelopes_decode() -> None:
    block = json.dumps(
        {
            "schema": "forge.block.v1",
            "chunk_id": "A",
            "reason_class": "failing-prereq",
            "reason": "Parent PR is not merged.",
            "needs": "human decision: merged parent PR",
            "state": "blocked",
            "unknown_example": "accepted",
        }
    )
    handoff = json.dumps(
        {
            "schema": "forge.chunk.v1",
            "chunk_id": "A",
            "pr": "https://github.com/acme/repo/pull/2",
            "project": "forgeboard-report",
            "branch": "chunk/3-lifecycle-metrics",
            "lane": "forge-codex-lane",
            "scenarios": {
                "added": 1,
                "passing": 1,
                "feature_files": ["tests/features/lifecycle_metrics.feature"],
            },
            "check": {"green": True, "coverage_pct": 95.41},
            "files_changed": 8,
            "lines_changed": 312,
            "decisions": ["Decode canonical metadata with additive compatibility."],
            "debt": [],
            "card_proposals": [],
            "docs_reconciled": ["docs/chunks/CHUNK-3.md", "docs/ROADMAP.md"],
            "duration_min": 23,
            "worker": "codex/gpt-5",
            "unknown_example": "accepted",
        }
    )
    snapshot = normalize_fixture(
        ("A",),
        cards=(card("A", completed_at=at(21)),),
        runs=(
            run(
                1,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(20),
                metadata=block,
            ),
            run(2, "A", ended_at=at(21), metadata=handoff),
        ),
        events=(
            event(1, "A", "blocked", at(20), run_id=1),
            event(2, "A", "completed", at(21), run_id=2),
        ),
    )

    assert snapshot.blocks[0].reason_class == "failing-prereq"
    assert snapshot.handoffs[0].pr == "https://github.com/acme/repo/pull/2"


def test_chunk_handoff_comes_only_from_completion_event_run() -> None:
    snapshot = normalize_fixture(
        ("A",),
        cards=(card("A", completed_at=at(30)),),
        runs=(
            run(1, "A", ended_at=at(20), metadata=chunk_metadata("B")),
            run(
                2,
                "A",
                ended_at=at(30),
                metadata=chunk_metadata("A", "https://github.com/acme/repo/pull/2"),
            ),
        ),
        events=(event(1, "A", "completed", at(30), run_id=2),),
    )

    assert tuple(handoff.run_id for handoff in snapshot.handoffs) == (2,)


def test_chunk_handoff_id_mismatch_and_unfinished_judge_fail_core_validation() -> None:
    with pytest.raises(InvalidCoreError, match="does not match mapped chunk"):
        normalize_fixture(
            ("A",),
            cards=(card("A", completed_at=at(20)),),
            runs=(run(1, "A", ended_at=at(20), metadata=chunk_metadata("B")),),
            events=(event(1, "A", "completed", at(20), run_id=1),),
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
                metadata=judge_metadata("bounce", (1, 2, 1, 3, 2, 2)),
            ),
        ),
    )
    metrics = calculate_metrics(snapshot)

    assert metrics.bounce.status is Availability.UNAVAILABLE
    assert metrics.bounce.verdicts == ()
    assert metrics.quality.spec_fidelity.mean == Decimal("1")


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
