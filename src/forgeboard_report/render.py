"""Deterministic projections of the immutable Forgeboard report model."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from forgeboard_report.domain import (
    BlockOccurrence,
    DependencyAudit,
    DependencyEdgeFinding,
    DependencyRunFinding,
    DependencyWaitFinding,
    EvidenceRef,
    LifecycleMetrics,
    NormalizationWarning,
    Report,
    ReportSources,
    ResolvedInputs,
    ScoreFinding,
    SourceFileFingerprint,
    SourceFingerprint,
    SourceSnapshot,
    UnexpectedBoardLinkFinding,
)
from forgeboard_report.errors import InvalidCoreError

SCHEMA_VERSION = "forgeboard.report.v1"
_SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class RenderedReport:
    """Complete in-memory artifact pair ready for atomic publication."""

    json: bytes
    markdown: bytes


def build_report(
    snapshot: SourceSnapshot,
    metrics: LifecycleMetrics,
    dependency_audit: DependencyAudit,
) -> Report:
    """Assemble the sole report model from already-normalized, pure findings."""
    report = Report(
        schema_version=SCHEMA_VERSION,
        inputs=ResolvedInputs(
            board_slug=snapshot.request.board_slug,
            from_original=snapshot.request.from_original,
            to_original=snapshot.request.to_original,
            from_utc=snapshot.request.from_utc,
            to_utc=snapshot.request.to_utc,
            operator_ids=tuple(sorted(snapshot.request.operator_ids)),
        ),
        sources=ReportSources(
            graph=snapshot.graph_fingerprint,
            hermes=snapshot.hermes_fingerprint,
            hermes_files=tuple(sorted(snapshot.hermes_source_files, key=lambda item: item.name)),
        ),
        metrics=metrics,
        dependency_audit=dependency_audit,
        warnings=tuple(sorted(snapshot.warnings, key=lambda item: (item.code, item.evidence_id))),
        evidence=tuple(sorted(snapshot.evidence, key=_evidence_key)),
    )
    _validate_report(report)
    return report


def render(report: Report) -> RenderedReport:
    """Render both direct projections after validating the common model once."""
    _validate_report(report)
    return RenderedReport(json=render_json(report), markdown=render_markdown(report))


def render_json(report: Report) -> bytes:
    """Render v1 JSON with its contractually significant object field ordering."""
    _validate_report(report)
    return _encode_json(_report_data(report))


def render_markdown(report: Report) -> bytes:
    """Render a paste-ready Markdown projection without parsing JSON output."""
    _validate_report(report)
    data = _report_data(report)
    metrics = data["metrics"]
    bounce = metrics["bounce_rate"]
    blocked = metrics["blocked_work"]
    intervention = metrics["operator_intervention"]
    verdict_ids = [item["evidence_id"] for item in bounce["verdicts"]]
    unclassified_ids = [item["id"] for item in blocked["unclassified"]]
    qualifying_ids = [item["evidence_id"] for item in intervention["qualifying_comments"]]
    needed_chunks = [item["chunk_id"] for item in intervention["needed_to_execute"]]
    comment_ids = [item["evidence_id"] for item in intervention["comments"]]
    indeterminate_ids = _flatten_ids(intervention["indeterminate"])
    qualifying_line = (
        f"- Qualifying comments: `{intervention['qualifying_comment_count']}` "
        f"({_ids(qualifying_ids)})"
    )
    lines = [
        "# Forgeboard report",
        "",
        f"Schema: `{report.schema_version}`",
        "",
        "## Resolved inputs",
        "",
        f"- Board: `{data['inputs']['board_slug']}`",
        f"- Period: `{data['inputs']['from_utc']}` to `{data['inputs']['to_utc']}`",
        f"- Original bounds: `{data['inputs']['from']}` to `{data['inputs']['to']}`",
        f"- Operators: {_ids(data['inputs']['operator_ids'])}",
        f"- Graph source: `{data['sources']['graph']['kind']}` "
        f"`{data['sources']['graph']['sha256']}`",
        f"- Hermes source: `{data['sources']['hermes']['kind']}` "
        f"`{data['sources']['hermes']['sha256']}`",
        "",
        "## Canonical metrics",
        "",
        "### Bounce rate",
        "",
        _metric_line(bounce, "value", "numerator", "denominator"),
        f"- Completed chunks: {_ids(bounce['completed_chunk_ids'])}",
        f"- Bounced chunks: {_ids(bounce['bounced_chunk_ids'])}",
        f"- Verdict evidence: {_ids(verdict_ids)}",
        "",
        "### Judge quality",
        "",
    ]
    for name, finding in metrics["judge_quality"].items():
        lines.extend(
            (
                f"- `{name}`: {_status_value(finding['status'], finding['value'])} "
                f"(sum `{finding['sum']}`, count `{finding['count']}`)",
                f"  - Score evidence: {_ids([item['evidence_id'] for item in finding['scores']])}",
            )
        )
        for contribution in finding["scores"]:
            lines.append(
                f"  - `{contribution['evidence_id']}` / `{contribution['chunk_id']}`: "
                f"`{_decimal_lexeme(contribution['score'])}` at `{contribution['occurred_at']}`"
            )
    lines.extend(
        (
            "",
            "### Blocked work",
            "",
            f"- Total occurrences: `{blocked['total']}`",
            f"- Reason buckets: {_reason_buckets(blocked['reasons'])}",
            f"- Unclassified: {_ids(unclassified_ids)}",
        )
    )
    for occurrence in blocked["occurrences"]:
        reason = occurrence["reason_class"] or "unavailable"
        lines.append(
            f"- `{occurrence['id']}` / `{occurrence['chunk_id']}`: "
            f"reason `{reason}` at `{occurrence['occurred_at']}`; "
            f"evidence {_ids(occurrence['evidence_ids'])}"
        )
    lines.extend(
        (
            "",
            "## Operator intervention",
            "",
            qualifying_line,
            f"- Chunks needing intervention: `{intervention['needed_to_execute_chunk_count']}` "
            f"({_ids(needed_chunks)})",
            f"- All comment evidence: {_ids(comment_ids)}",
            f"- Indeterminate evidence: {_ids(indeterminate_ids)}",
        )
    )
    for finding in intervention["comments"]:
        lines.append(
            f"- `{finding['evidence_id']}` / `{finding['chunk_id']}`: `{finding['actor_class']}` "
            f"`{finding['disposition']}` at `{finding['occurred_at']}`"
        )
    for finding in intervention["needed_to_execute"]:
        chunk_id = finding["chunk_id"]
        block_id = finding["block_id"]
        block_at = finding["block_at"]
        lines.append(
            f"- Needed `{chunk_id}`: block `{block_id}` at `{block_at}`, "
            f"comment `{finding['comment_id']}` at `{finding['comment_at']}`, "
            f"claim `{finding['next_claim_id']}` at `{finding['next_claim_at']}`"
        )
    lines.extend(("", "## Dependency audit", ""))
    for edge in data["dependency_audit"]["edges"]:
        run_ids = [item["run_id"] for item in edge["runs"]]
        wait_ids = [item["wait_id"] for item in edge["waits"] if item["wait_id"] is not None]
        lines.extend(
            (
                f"### `{edge['parent_id']} -> {edge['child_id']}`",
                "",
                f"- Attachment: `{edge['attachment']}`; observation: `{edge['observation']}`",
                f"- Handoff: `{edge['handoff_id']}` at `{edge['handoff_at']}`",
                f"- Pull request: `{edge['pull_request_id']}` ({edge['pull_request_url']})",
                f"- Runs: {_ids(run_ids)}",
                f"- Waits: {_ids(wait_ids)}",
                "",
            )
        )
        for run in edge["runs"]:
            lines.append(
                f"  - Run `{run['run_id']}` at `{run['started_at']}`: `{run['classification']}`"
            )
        for wait in edge["waits"]:
            lines.append(
                f"  - Wait `{wait['wait_id'] or 'unavailable'}` at "
                f"`{wait['wait_at'] or 'unavailable'}`: `{wait['status']}`, "
                f"retry `{wait['retry_run_id'] or 'unavailable'}`, "
                f"operator `{wait['operator_comment_id'] or 'unavailable'}`, "
                f"intervention `{wait['intervention']}`"
            )
    unexpected_ids = [item["evidence_id"] for item in data["dependency_audit"]["unexpected_links"]]
    lines.extend((f"- Unexpected links: {_ids(unexpected_ids)}", ""))
    lines.extend(("## Warnings and unclassified evidence", ""))
    for warning in data["warnings"]:
        detail = _markdown_text(warning["detail"])
        schema = warning["schema"] or "unavailable"
        lines.append(
            f"- `{warning['code']}` — `{warning['evidence_id']}` (schema `{schema}`): {detail}"
        )
    for occurrence in metrics["blocked_work"]["unclassified"]:
        lines.append(
            f"- Unclassified block `{occurrence['id']}` for `{occurrence['chunk_id']}` "
            f"at `{occurrence['occurred_at']}`"
        )
    if not data["warnings"] and not metrics["blocked_work"]["unclassified"]:
        lines.append("- None")
    lines.extend(("", "## Evidence index", ""))
    for evidence in data["evidence"]:
        occurred_at = evidence["occurred_at"] or "unavailable"
        lines.append(
            f"- `{evidence['id']}` — source `{evidence['source_id']}`, "
            f"{evidence['role']}, `{occurred_at}`"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _report_data(report: Report) -> dict[str, Any]:
    """Return ordered JSON-compatible data; no value is calculated here."""
    return {
        "schema_version": report.schema_version,
        "inputs": _inputs_data(report.inputs),
        "sources": _sources_data(report.sources),
        "metrics": _metrics_data(report.metrics),
        "dependency_audit": _audit_data(report.dependency_audit),
        "warnings": [
            _warning_data(item)
            for item in sorted(report.warnings, key=lambda item: (item.code, item.evidence_id))
        ],
        "evidence": [_evidence_data(item) for item in sorted(report.evidence, key=_evidence_key)],
    }


def _inputs_data(inputs: ResolvedInputs) -> dict[str, Any]:
    return {
        "board_slug": inputs.board_slug,
        "from": inputs.from_original,
        "to": inputs.to_original,
        "from_utc": _timestamp(inputs.from_utc),
        "to_utc": _timestamp(inputs.to_utc),
        "operator_ids": list(sorted(inputs.operator_ids)),
    }


def _sources_data(sources: ReportSources) -> dict[str, Any]:
    return {
        "graph": _fingerprint_data(sources.graph),
        "hermes": _fingerprint_data(sources.hermes),
        "hermes_files": [
            _file_fingerprint_data(item)
            for item in sorted(sources.hermes_files, key=lambda item: item.name)
        ],
    }


def _fingerprint_data(value: SourceFingerprint) -> dict[str, Any]:
    return {"kind": value.kind, "sha256": value.sha256, "version": value.version}


def _file_fingerprint_data(value: SourceFileFingerprint) -> dict[str, Any]:
    return {"name": value.name, "size": value.size, "sha256": value.sha256}


def _metrics_data(metrics: LifecycleMetrics) -> dict[str, Any]:
    return {
        "bounce_rate": {
            "status": metrics.bounce.status.value,
            "value": _display(metrics.bounce.rate),
            "numerator": metrics.bounce.numerator,
            "denominator": metrics.bounce.denominator,
            "completed_chunk_ids": sorted(metrics.bounce.completed_chunk_ids),
            "bounced_chunk_ids": sorted(metrics.bounce.bounced_chunk_ids),
            "verdicts": [
                {
                    "evidence_id": item.evidence_id,
                    "chunk_id": item.chunk_id,
                    "outcome": item.outcome.value,
                    "occurred_at": _timestamp(item.occurred_at),
                }
                for item in sorted(
                    metrics.bounce.verdicts, key=lambda item: (item.occurred_at, item.evidence_id)
                )
            ],
            "completed_without_canonical_verdict": sorted(
                metrics.bounce.completed_without_canonical_verdict
            ),
        },
        "judge_quality": {
            "spec_fidelity": _score_data(metrics.quality.spec_fidelity),
            "scenario_integrity": _score_data(metrics.quality.scenario_integrity),
            "architectural_conformance": _score_data(metrics.quality.architectural_conformance),
        },
        "blocked_work": {
            "total": metrics.blocks.total,
            "reasons": [
                {
                    "reason_class": item.reason_class,
                    "count": item.count,
                    "occurrence_ids": sorted(item.occurrence_ids),
                }
                for item in sorted(metrics.blocks.reasons, key=lambda item: item.reason_class)
            ],
            "unclassified": [
                _occurrence_data(item)
                for item in sorted(metrics.blocks.unclassified, key=_occurrence_key)
            ],
            "occurrences": [
                _occurrence_data(item)
                for item in sorted(metrics.blocks.occurrences, key=_occurrence_key)
            ],
        },
        "operator_intervention": {
            "qualifying_comment_count": metrics.intervention.qualifying_comment_count,
            "qualifying_comments": [
                _comment_data(item)
                for item in sorted(metrics.intervention.qualifying_comments, key=_comment_key)
            ],
            "comments": [
                _comment_data(item)
                for item in sorted(metrics.intervention.comments, key=_comment_key)
            ],
            "needed_to_execute_chunk_count": metrics.intervention.needed_to_execute_chunk_count,
            "needed_to_execute": [
                {
                    "chunk_id": item.chunk_id,
                    "block_id": item.block_id,
                    "comment_id": item.comment_id,
                    "next_claim_id": item.next_claim_id,
                    "block_at": _timestamp(item.block_at),
                    "comment_at": _timestamp(item.comment_at),
                    "next_claim_at": _timestamp(item.next_claim_at),
                }
                for item in sorted(
                    metrics.intervention.needed_to_execute,
                    key=lambda item: (item.chunk_id, item.block_id, item.comment_id),
                )
            ],
            "indeterminate": [
                {
                    "chunk_id": item.chunk_id,
                    "relation": item.relation,
                    "occurred_at": _timestamp(item.occurred_at),
                    "evidence_ids": sorted(item.evidence_ids),
                }
                for item in sorted(
                    metrics.intervention.indeterminate,
                    key=lambda item: (
                        item.occurred_at,
                        item.chunk_id,
                        item.relation,
                        item.evidence_ids,
                    ),
                )
            ],
        },
    }


def _score_data(finding: ScoreFinding) -> dict[str, Any]:
    return {
        "status": finding.status.value,
        "value": _display(finding.mean),
        "sum": _raw_decimal(finding.total),
        "count": finding.count,
        "scores": [
            {
                "evidence_id": item.evidence_id,
                "chunk_id": item.chunk_id,
                "occurred_at": _timestamp(item.occurred_at),
                "score": _raw_decimal(item.score),
            }
            for item in sorted(
                finding.scores, key=lambda item: (item.occurred_at, item.evidence_id)
            )
        ],
    }


def _occurrence_data(item: BlockOccurrence) -> dict[str, Any]:
    return {
        "id": item.id,
        "chunk_id": item.chunk_id,
        "task_id": item.task_id,
        "run_id": item.run_id,
        "reason_class": item.reason_class,
        "native_kinds": sorted(item.native_kinds),
        "occurred_at": _timestamp(item.occurred_at),
        "evidence_ids": sorted(item.evidence_ids),
    }


def _comment_data(item: Any) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "chunk_id": item.chunk_id,
        "author": item.author,
        "actor_class": item.actor_class.value,
        "occurred_at": _timestamp(item.occurred_at),
        "disposition": item.disposition,
    }


def _audit_data(audit: DependencyAudit) -> dict[str, Any]:
    return {
        "edges": [
            _edge_data(item)
            for item in sorted(audit.edges, key=lambda item: (item.parent_id, item.child_id))
        ],
        "unexpected_links": [
            _unexpected_link_data(item)
            for item in sorted(
                audit.unexpected_links,
                key=lambda item: (
                    item.parent_chunk_id or "",
                    item.child_chunk_id or "",
                    item.parent_task_id,
                    item.child_task_id,
                ),
            )
        ],
    }


def _edge_data(item: DependencyEdgeFinding) -> dict[str, Any]:
    return {
        "parent_id": item.parent_id,
        "child_id": item.child_id,
        "attachment": item.attachment,
        "link_evidence_ids": sorted(item.link_evidence_ids),
        "handoff_id": item.handoff_id,
        "handoff_at": _timestamp(item.handoff_at),
        "pull_request_id": item.pull_request_id,
        "pull_request_url": item.pull_request_url,
        "merged_at": _timestamp_or_none(item.merged_at),
        "observation": item.observation,
        "runs": [
            _run_data(run)
            for run in sorted(item.runs, key=lambda run: (run.started_at, run.run_id))
        ],
        "waits": [
            _wait_data(wait)
            for wait in sorted(
                item.waits,
                key=lambda wait: (
                    wait.wait_at or datetime.min.replace(tzinfo=UTC),
                    wait.wait_id or "",
                ),
            )
        ],
    }


def _run_data(item: DependencyRunFinding) -> dict[str, Any]:
    return {
        "run_id": item.run_id,
        "started_at": _timestamp(item.started_at),
        "classification": item.classification,
    }


def _wait_data(item: DependencyWaitFinding) -> dict[str, Any]:
    return {
        "wait_id": item.wait_id,
        "wait_at": _timestamp_or_none(item.wait_at),
        "status": item.status,
        "retry_run_id": item.retry_run_id,
        "retry_started_at": _timestamp_or_none(item.retry_started_at),
        "operator_comment_id": item.operator_comment_id,
        "operator_comment_at": _timestamp_or_none(item.operator_comment_at),
        "intervention": item.intervention,
    }


def _unexpected_link_data(item: UnexpectedBoardLinkFinding) -> dict[str, Any]:
    return {
        "parent_chunk_id": item.parent_chunk_id,
        "child_chunk_id": item.child_chunk_id,
        "parent_task_id": item.parent_task_id,
        "child_task_id": item.child_task_id,
        "evidence_id": item.evidence_id,
    }


def _warning_data(item: NormalizationWarning) -> dict[str, Any]:
    return {
        "code": item.code,
        "evidence_id": item.evidence_id,
        "schema": item.schema,
        "detail": item.detail,
    }


def _evidence_data(item: EvidenceRef) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_id": item.source_id,
        "occurred_at": _timestamp_or_none(item.occurred_at),
        "role": item.role.value,
    }


def _display(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite():
        raise InvalidCoreError("report", "display decimal must be finite")
    return value.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)


def _raw_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise InvalidCoreError("report", "raw decimal must be finite")
    return value


def _timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise InvalidCoreError("report", "timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp_or_none(value: datetime | None) -> str | None:
    return _timestamp(value) if value is not None else None


def _encode_json(value: Any) -> bytes:
    return (_json_lexeme(value) + "\n").encode("utf-8")


def _json_lexeme(value: Any) -> str:
    if value is None or isinstance(value, (bool, int, str)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if isinstance(value, Decimal):
        return _decimal_lexeme(value)
    if isinstance(value, Mapping):
        return (
            "{"
            + ",".join(
                f"{_json_lexeme(str(key))}:{_json_lexeme(item)}" for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ",".join(_json_lexeme(item) for item in value) + "]"
    raise InvalidCoreError("report", f"unsupported JSON value {type(value).__name__}")


def _decimal_lexeme(value: Decimal) -> str:
    if not value.is_finite():
        raise InvalidCoreError("report", "decimal must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _validate_report(report: Report) -> None:
    if not isinstance(report, Report) or report.schema_version != SCHEMA_VERSION:
        raise InvalidCoreError("report", f"schema_version must be {SCHEMA_VERSION!r}")
    if (
        report.metrics.bounce.status.value == "unavailable"
        and report.metrics.bounce.rate is not None
    ):
        raise InvalidCoreError("report", "unavailable bounce rate must be null")
    for score in (
        report.metrics.quality.spec_fidelity,
        report.metrics.quality.scenario_integrity,
        report.metrics.quality.architectural_conformance,
    ):
        if score.status.value == "unavailable" and score.mean is not None:
            raise InvalidCoreError("report", "unavailable score mean must be null")


def _evidence_key(item: EvidenceRef) -> tuple[datetime, str]:
    return (item.occurred_at or datetime.min.replace(tzinfo=UTC), item.id)


def _occurrence_key(item: BlockOccurrence) -> tuple[datetime, str]:
    return (item.occurred_at, item.id)


def _comment_key(item: Any) -> tuple[datetime, str]:
    return (item.occurred_at, item.evidence_id)


def _ids(values: Sequence[Any]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "none"


def _flatten_ids(items: Sequence[Mapping[str, Any]]) -> list[str]:
    return [evidence_id for item in items for evidence_id in item["evidence_ids"]]


def _reason_buckets(reasons: Sequence[Mapping[str, Any]]) -> str:
    return ", ".join(f"`{item['reason_class']}`: `{item['count']}`" for item in reasons) or "none"


def _metric_line(value: Mapping[str, Any], display: str, numerator: str, denominator: str) -> str:
    result = _status_value(value["status"], value[display])
    return f"- {result} (`{value[numerator]}` / `{value[denominator]}`)"


def _status_value(status: str, value: Any) -> str:
    if status == "unavailable":
        return "unavailable"
    if isinstance(value, Decimal):
        return f"`{_decimal_lexeme(value)}`"
    return f"`{value}`"


def _markdown_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;")
    for character in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(character, f"\\{character}")
    return text
