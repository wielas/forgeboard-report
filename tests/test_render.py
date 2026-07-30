"""Tests for the one-model deterministic render and atomic publish boundary."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from fixtures.normalize import (
    at,
    block_metadata,
    card,
    comment,
    event,
    judge_metadata,
    normalize_fixture,
    run,
)
from forgeboard_report.domain import (
    Availability,
    DependencyAudit,
    DependencyEdgeFinding,
    DependencyRunFinding,
    DependencyWaitFinding,
    NormalizationWarning,
    ScoreFinding,
)
from forgeboard_report.errors import (
    InvalidCoreError,
    PublicationFlushError,
    PublicationRenameError,
    PublicationWriteError,
    UsageError,
)
from forgeboard_report.metrics import calculate
from forgeboard_report.publish import publish, publish_report
from forgeboard_report.render import (
    RenderedReport,
    _ids,
    _markdown_text,
    _metric_line,
    _reason_buckets,
    _status_value,
    build_report,
    render,
    render_json,
    render_markdown,
)


def _report():
    snapshot = normalize_fixture(
        ("A",),
        cards=(card("A", completed_at=at(40)),),
        runs=(
            run(
                1,
                "A",
                ended_at=at(20),
                metadata=judge_metadata(verdict="bounce", scores=(0, 0, 0, 0, 0, 0)),
            ),
            run(
                2,
                "A",
                status="blocked",
                outcome="blocked",
                ended_at=at(21),
                metadata=block_metadata("failing-prereq"),
            ),
        ),
        events=(event(1, "A", "claimed", at(10)), event(2, "A", "blocked", at(21), run_id=2)),
        comments=(comment(1, "A", "operator", at(15)),),
    )
    audit = DependencyAudit(
        edges=(
            DependencyEdgeFinding(
                parent_id="A",
                child_id="B",
                attachment="missing",
                link_evidence_ids=("hermes:link:a->b",),
                handoff_id="hermes:run:1",
                handoff_at=at(20),
                pull_request_id="github:node:1",
                pull_request_url="https://github.example/acme/repo/pull/1",
                merged_at=None,
                observation="observed",
                runs=(DependencyRunFinding("hermes:run:3", at(22), "parent_unmerged"),),
                waits=(
                    DependencyWaitFinding(
                        wait_id="hermes:block:2",
                        wait_at=at(21),
                        status="observed",
                        retry_run_id="hermes:run:3",
                        retry_started_at=at(22),
                        operator_comment_id="hermes:comment:1",
                        operator_comment_at=at(15),
                        intervention="before_retry",
                    ),
                ),
            ),
        ),
        unexpected_links=(),
    )
    report = build_report(snapshot, calculate(snapshot), audit)
    return replace(
        report,
        warnings=(
            NormalizationWarning(
                code="unknown_schema",
                evidence_id="hermes:run:99",
                schema="future.v2",
                detail="untrusted `text`\nkept as evidence",
            ),
        ),
    )


def test_rendered_projections_have_fixed_order_rounding_and_traceable_ids() -> None:
    report = _report()
    rendered = render(report)
    data = json.loads(rendered.json)

    assert list(data) == [
        "schema_version",
        "inputs",
        "sources",
        "metrics",
        "dependency_audit",
        "warnings",
        "evidence",
    ]
    assert list(data["metrics"]) == [
        "bounce_rate",
        "judge_quality",
        "blocked_work",
        "operator_intervention",
    ]
    assert data["schema_version"] == "forgeboard.report.v1"
    assert data["metrics"]["bounce_rate"]["value"] == 1
    assert data["metrics"]["judge_quality"]["spec_fidelity"]["value"] == 0
    assert data["dependency_audit"]["edges"][0]["handoff_id"] == "hermes:run:1"
    assert data["warnings"][0]["detail"] == "untrusted `text`\nkept as evidence"
    assert rendered.json.endswith(b"\n") and b"\r" not in rendered.json
    assert rendered.markdown.endswith(b"\n") and b"\r" not in rendered.markdown

    markdown = rendered.markdown.decode()
    sections = [
        "# Forgeboard report",
        "## Resolved inputs",
        "## Canonical metrics",
        "## Operator intervention",
        "## Dependency audit",
        "## Warnings and unclassified evidence",
        "## Evidence index",
    ]
    assert [markdown.index(section) for section in sections] == sorted(
        markdown.index(section) for section in sections
    )
    assert "hermes:run:1" in markdown
    assert "github:node:1" in markdown


def test_display_rounds_half_even_once_and_unavailable_is_null() -> None:
    report = _report()
    quality = report.metrics.quality
    score = replace(
        quality.spec_fidelity,
        total=Decimal("2.3456785"),
        count=1,
        mean=Decimal("2.3456785"),
    )
    unavailable = ScoreFinding(
        name="scenario_integrity",
        status=Availability.UNAVAILABLE,
        total=Decimal(0),
        count=0,
        mean=None,
        scores=(),
    )
    metrics = replace(
        report.metrics,
        bounce=replace(
            report.metrics.bounce,
            status=Availability.UNAVAILABLE,
            numerator=0,
            denominator=0,
            rate=None,
        ),
        quality=replace(quality, spec_fidelity=score, scenario_integrity=unavailable),
    )
    data = json.loads(render_json(replace(report, metrics=metrics)))

    assert data["metrics"]["bounce_rate"]["value"] is None
    assert data["metrics"]["judge_quality"]["spec_fidelity"]["value"] == 2.345678
    assert data["metrics"]["judge_quality"]["scenario_integrity"]["value"] is None
    assert "unavailable" in render_markdown(replace(report, metrics=metrics)).decode()


def test_rendering_is_stable_and_rejects_an_invalid_core_model() -> None:
    report = _report()
    assert render(report) == render(report)
    invalid = replace(report, schema_version="not-v1")
    with pytest.raises(InvalidCoreError, match="schema_version"):
        render_json(invalid)


def test_markdown_source_values_are_escaped_and_lf_normalized() -> None:
    source_value = "source`id\r\nnext\rline"

    assert _ids((source_value,)) == "`source\\`id next line`"
    assert _reason_buckets(({"reason_class": source_value, "count": 1},)) == (
        "`source\\`id next line`: `1`"
    )
    assert _status_value("available", source_value) == "`source\\`id next line`"
    assert (
        _metric_line(
            {"status": "available", "value": source_value, "numerator": 1, "denominator": 2},
            "value",
            "numerator",
            "denominator",
        )
        == "- `source\\`id next line` (`1` / `2`)"
    )
    assert _markdown_text(source_value) == "source\\\\\\`id next line"

    report = _report()
    rendered = render(report)
    assert (
        json.loads(rendered.json)["warnings"][0]["detail"] == "untrusted `text`\nkept as evidence"
    )
    assert b"\r" not in rendered.markdown


def test_publish_creates_only_the_complete_pair(tmp_path: Path) -> None:
    destination = tmp_path / "published"
    published = publish(destination, render(_report()))

    assert published.destination == destination
    assert sorted(path.name for path in destination.iterdir()) == ["report.json", "report.md"]
    assert (destination / "report.json").read_bytes().endswith(b"\n")


def test_publish_rejects_existing_or_parentless_destinations(tmp_path: Path) -> None:
    artifacts = render(_report())
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(UsageError, match="already exist"):
        publish(existing, artifacts)
    with pytest.raises(UsageError, match="parent"):
        publish(tmp_path / "missing" / "report", artifacts)


def test_publish_converts_renderer_failure_to_invalid_core(tmp_path: Path) -> None:
    destination = tmp_path / "failed"

    def broken_renderer(_report):
        raise RuntimeError("broken renderer")

    with pytest.raises(InvalidCoreError, match="renderer failed"):
        publish_report(destination, _report(), renderer=broken_renderer)
    assert not destination.exists()


@pytest.mark.parametrize(
    ("patch_target", "failure", "expected"),
    [
        ("write", OSError("write failed"), PublicationWriteError),
        ("flush", OSError("flush failed"), PublicationFlushError),
        ("rename", OSError("rename failed"), PublicationRenameError),
    ],
)
def test_publish_failure_leaves_no_plausible_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch_target: str,
    failure: OSError,
    expected: type[Exception],
) -> None:
    import forgeboard_report.publish as publisher

    destination = tmp_path / patch_target
    if patch_target == "write":
        monkeypatch.setattr(
            publisher, "_write_artifact", lambda *_args: (_ for _ in ()).throw(failure)
        )
    elif patch_target == "flush":
        monkeypatch.setattr(publisher.os, "fsync", lambda _fd: (_ for _ in ()).throw(failure))
    else:
        monkeypatch.setattr(publisher.os, "replace", lambda *_args: (_ for _ in ()).throw(failure))

    with pytest.raises(expected):
        publish(destination, render(_report()))
    assert not destination.exists()
    assert not list(tmp_path.glob(f".{destination.name}.*"))


def test_publish_rejects_invalid_artifacts(tmp_path: Path) -> None:
    with pytest.raises(InvalidCoreError, match="final newline"):
        publish(tmp_path / "invalid", RenderedReport(json=b"{}", markdown=b"# report\n"))


def test_timestamp_rendering_is_utc_even_if_supplied_with_an_offset() -> None:
    report = _report()
    shifted = replace(
        report,
        inputs=replace(
            report.inputs,
            from_utc=datetime(2026, 7, 29, 2, tzinfo=UTC),
        ),
    )
    assert '"from_utc":"2026-07-29T02:00:00.000000Z"' in render_json(shifted).decode()
