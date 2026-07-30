"""BDD coverage for canonical report projections."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from forgeboard_report.domain import Availability, NormalizationWarning
from forgeboard_report.errors import (
    PublicationFlushError,
    PublicationRenameError,
    PublicationWriteError,
)
from forgeboard_report.publish import publish
from forgeboard_report.render import render
from test_render import _report

scenarios("../features/report_output.feature")


@given("a complete canonical report model", target_fixture="report_case")
def complete_report_model():
    report = _report()
    metrics = replace(
        report.metrics,
        bounce=replace(
            report.metrics.bounce,
            status=Availability.UNAVAILABLE,
            numerator=0,
            denominator=0,
            rate=None,
        ),
    )
    return {"report": replace(report, metrics=metrics), "rendered": None}


@when("its projections are rendered")
def render_projections(report_case) -> None:
    report_case["rendered"] = render(report_case["report"])


@then("the JSON schema order and Markdown section order are stable")
def stable_order(report_case) -> None:
    rendered = report_case["rendered"]
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
    markdown = rendered.markdown.decode("utf-8")
    assert markdown.index("## Canonical metrics") < markdown.index("## Operator intervention")
    assert markdown.index("## Operator intervention") < markdown.index("## Dependency audit")


@then("unavailable values remain explicit in the projections")
def explicit_unavailable(report_case) -> None:
    rendered = report_case["rendered"]
    assert json.loads(rendered.json)["metrics"]["bounce_rate"]["value"] is None
    assert "unavailable" in rendered.markdown.decode("utf-8")


@then("Markdown carries the matching projected JSON values and stable IDs")
def matching_projected_values(report_case) -> None:
    data = json.loads(report_case["rendered"].json)
    markdown = report_case["rendered"].markdown.decode("utf-8")
    projected_values = (
        data["schema_version"],
        data["inputs"]["board_slug"],
        *data["inputs"]["operator_ids"],
        *data["metrics"]["bounce_rate"]["completed_chunk_ids"],
        *data["metrics"]["bounce_rate"]["bounced_chunk_ids"],
        *(item["evidence_id"] for item in data["metrics"]["bounce_rate"]["verdicts"]),
        *(item["id"] for item in data["metrics"]["blocked_work"]["occurrences"]),
        *(item["evidence_id"] for item in data["warnings"]),
        *(item["id"] for item in data["evidence"]),
    )
    assert all(value in markdown for value in projected_values)


@when("its projections are rendered twice")
def render_twice(report_case) -> None:
    report_case["first"] = render(report_case["report"])
    report_case["second"] = render(report_case["report"])


@then("both renderings are byte-identical")
def repeated_rendering_is_identical(report_case) -> None:
    assert report_case["first"] == report_case["second"]


@given(
    "equivalent reports with shuffled source insertion under varied process settings",
    target_fixture="deterministic_cases",
)
def shuffled_reports(monkeypatch: pytest.MonkeyPatch):
    report = _report()
    warnings = (
        NormalizationWarning("z-warning", "evidence:z", "v1", "z detail"),
        *report.warnings,
        NormalizationWarning("a-warning", "evidence:a", "v1", "a detail"),
    )
    first = replace(report, warnings=warnings)
    second = replace(report, warnings=tuple(reversed(warnings)))
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("TZ", "UTC0")
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    return {"first": first, "second": second, "rendered": None}


@when("their projections are rendered")
def render_shuffled_reports(deterministic_cases) -> None:
    deterministic_cases["rendered"] = (
        render(deterministic_cases["first"]),
        render(deterministic_cases["second"]),
    )


@then("their JSON and Markdown bytes are identical UTF-8 LF output")
def deterministic_output(deterministic_cases) -> None:
    first, second = deterministic_cases["rendered"]
    assert first == second
    for artifact in (first.json, first.markdown):
        assert artifact.endswith(b"\n") and not artifact.endswith(b"\n\n")
        assert b"\r" not in artifact
        artifact.decode("utf-8")

    script = (
        "import sys; "
        "sys.path.insert(0, 'tests'); "
        "from forgeboard_report.render import render; "
        "from test_render import _report; "
        "result = render(_report()); "
        "sys.stdout.buffer.write(result.json + result.markdown)"
    )
    outputs = []
    for hash_seed in ("1", "777"):
        environment = os.environ | {"PYTHONHASHSEED": hash_seed, "TZ": "UTC0", "LC_ALL": "C"}
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=environment,
        )
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


@given(
    "a report with immutable source bytes and their fingerprint",
    target_fixture="source_byte_case",
)
def source_byte_report(tmp_path: Path):
    source_bytes = b'{"ordered":"source bytes","keep":"exact"}\n'
    source_path = tmp_path / "source.json"
    source_path.write_bytes(source_bytes)
    report = _report()
    report = replace(
        report,
        sources=replace(
            report.sources,
            graph=replace(report.sources.graph, sha256=hashlib.sha256(source_bytes).hexdigest()),
        ),
    )
    return {
        "report": report,
        "source_path": source_path,
        "source_bytes": source_bytes,
        "destination": tmp_path / "published",
        "rendered": None,
    }


@when("its projections are rendered and published")
def render_and_publish(source_byte_case) -> None:
    source_byte_case["rendered"] = render(source_byte_case["report"])
    publish(source_byte_case["destination"], source_byte_case["rendered"])


@then("the source bytes and their fingerprint are preserved in the output projections")
def source_bytes_preserved(source_byte_case) -> None:
    source_bytes = source_byte_case["source_bytes"]
    digest = hashlib.sha256(source_bytes).hexdigest()
    assert source_byte_case["source_path"].read_bytes() == source_bytes
    assert json.loads(source_byte_case["rendered"].json)["sources"]["graph"]["sha256"] == digest
    assert digest in source_byte_case["rendered"].markdown.decode("utf-8")


@given(parsers.parse("an injected {stage} publication failure"), target_fixture="publication_case")
def publication_failure(stage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_case):
    import forgeboard_report.publish as publisher

    destination = tmp_path / "published"
    if stage == "write":
        monkeypatch.setattr(
            publisher,
            "_write_artifact",
            lambda *_args: (_ for _ in ()).throw(OSError("write failed")),
        )
        expected = PublicationWriteError
    elif stage == "flush":
        monkeypatch.setattr(
            publisher.os,
            "fsync",
            lambda _fd: (_ for _ in ()).throw(OSError("flush failed")),
        )
        expected = PublicationFlushError
    else:
        monkeypatch.setattr(
            publisher.os,
            "replace",
            lambda *_args: (_ for _ in ()).throw(OSError("rename failed")),
        )
        expected = PublicationRenameError
    return {
        "destination": destination,
        "expected": expected,
        "error": None,
        "report": report_case["report"],
    }


@when("the projections are published")
def publish_projections(publication_case) -> None:
    with pytest.raises(publication_case["expected"]) as raised:
        publish(publication_case["destination"], render(publication_case["report"]))
    publication_case["error"] = raised.value


@then(parsers.parse("the {stage} failure is typed and only private staging was cleaned up"))
def typed_failure_cleanup(stage: str, publication_case) -> None:
    assert publication_case["error"].stage == stage
    assert not publication_case["destination"].exists()
    assert not list(publication_case["destination"].parent.glob(".published.*"))
