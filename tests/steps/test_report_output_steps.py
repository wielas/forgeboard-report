"""BDD coverage for canonical report projections."""

from __future__ import annotations

import json
from dataclasses import replace

from pytest_bdd import given, scenarios, then, when

from forgeboard_report.domain import Availability
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
