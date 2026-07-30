"""BDD coverage for the read-only GitHub dependency audit."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from datetime import datetime

import pytest
from pytest_bdd import given, scenarios, then, when

from fixtures.normalize import (
    at,
    block_metadata,
    card,
    chunk_metadata,
    comment,
    event,
    normalize_fixture,
    run,
)
from forgeboard_report.dependencies import audit_dependencies, required_pull_request_refs
from forgeboard_report.domain import PullRequestFact, PullRequestRef, RawHermesLink
from forgeboard_report.errors import InvalidCoreError, SourceUnavailableError
from forgeboard_report.github import fetch_pull_request_facts

scenarios("../features/dependency_audit.feature")


def _dependency_snapshot(*, include_wait_boundaries: bool = True):
    boundary_runs = (
        (
            run(16, "C", started_at=at(26), ended_at=at(27)),
            run(17, "C", started_at=at(28), ended_at=at(29)),
            run(
                18,
                "C",
                status="blocked",
                outcome="blocked",
                ended_at=at(27),
                metadata=block_metadata("failing-prereq"),
            ),
        )
        if include_wait_boundaries
        else ()
    )
    boundary_events = (
        (event(18, "C", "blocked", at(27), run_id=18),) if include_wait_boundaries else ()
    )
    boundary_comments = (comment(2, "C", "operator", at(30)),) if include_wait_boundaries else ()
    return normalize_fixture(
        ("P", "Q", "C", "D"),
        cards=(
            card("P", completed_at=at(10)),
            card("Q", completed_at=at(12)),
            card("C"),
            card("D"),
        ),
        runs=(
            run(1, "P", started_at=at(1), ended_at=at(10), metadata=chunk_metadata("P")),
            run(
                2,
                "Q",
                started_at=at(2),
                ended_at=at(12),
                metadata=chunk_metadata("Q", "https://github.example/acme/other/pull/2"),
            ),
            run(10, "C", started_at=at(15), ended_at=at(16)),
            run(11, "C", started_at=at(20), ended_at=at(21)),
            run(12, "C", started_at=at(25), ended_at=at(26)),
            run(
                13,
                "C",
                status="blocked",
                outcome="blocked",
                ended_at=at(26),
                metadata=block_metadata("failing-prereq"),
            ),
            run(
                14,
                "C",
                status="blocked",
                outcome="blocked",
                ended_at=at(27),
                metadata='{"schema":"future.block.v2"}',
            ),
            run(15, "C", started_at=at(30), ended_at=at(31)),
            *boundary_runs,
        ),
        events=(
            event(1, "P", "completed", at(10), run_id=1),
            event(2, "Q", "completed", at(12), run_id=2),
            event(13, "C", "blocked", at(26), run_id=13),
            event(14, "C", "blocked", at(27), run_id=14),
            *boundary_events,
        ),
        comments=(comment(1, "C", "operator", at(28)), *boundary_comments),
        links=(
            RawHermesLink("task-P", "task-C"),
            RawHermesLink("task-C", "task-P"),
            RawHermesLink("outside", "task-C"),
        ),
        edges=(("P", "C"), ("Q", "C"), ("P", "D")),
    )


def _fact(ref: PullRequestRef, merged_at: datetime | None) -> PullRequestFact:
    return PullRequestFact(
        node_id=f"NODE-{ref.host}-{ref.number}",
        host=ref.host,
        owner=ref.owner,
        repository=ref.repository,
        number=ref.number,
        url=ref.url,
        state="MERGED" if merged_at else "OPEN",
        merged_at=merged_at,
        evidence_id=f"github:node:NODE-{ref.host}-{ref.number}",
    )


def _audit_case(*, include_wait_boundaries: bool = True):
    snapshot = _dependency_snapshot(include_wait_boundaries=include_wait_boundaries)
    refs = required_pull_request_refs(snapshot)
    facts = tuple(_fact(ref, at(20) if ref.number == 1 else None) for ref in refs)
    return {"snapshot": snapshot, "facts": facts, "audit": None}


@given(
    "a normalized dependency board with matching missing and extra links",
    target_fixture="dependency_case",
)
def declared_link_case():
    return _audit_case()


@given(
    "merged and unmerged parent PR facts with boundary child starts",
    target_fixture="dependency_case",
)
def gate_case():
    return _audit_case()


@given(
    "canonical and unclassified prerequisite waits with retries and comments",
    target_fixture="dependency_case",
)
def wait_case():
    return _audit_case(include_wait_boundaries=True)


@when("dependency edges are audited")
def audit_case(dependency_case) -> None:
    dependency_case["audit"] = audit_dependencies(
        dependency_case["snapshot"], dependency_case["facts"]
    )


@then("every declared edge is attached or missing and extras are unexpected")
def assert_links(dependency_case) -> None:
    audit = dependency_case["audit"]
    assert [(edge.parent_id, edge.child_id, edge.attachment) for edge in audit.edges] == [
        ("P", "C", "attached"),
        ("P", "D", "missing"),
        ("Q", "C", "missing"),
    ]
    assert [(link.parent_chunk_id, link.child_chunk_id) for link in audit.unexpected_links] == [
        (None, "C"),
        ("C", "P"),
    ]
    assert audit.edges[0].link_evidence_ids == ("hermes:link:task-P->task-C",)


@then("child starts are before after equal unmerged or not observed exactly")
def assert_gate_classifications(dependency_case) -> None:
    edges = {(edge.parent_id, edge.child_id): edge for edge in dependency_case["audit"].edges}
    assert [run.classification for run in edges[("P", "C")].runs] == [
        "before_merge",
        "indeterminate",
        "after_merge",
        "after_merge",
        "after_merge",
        "after_merge",
    ]
    assert {run.classification for run in edges[("Q", "C")].runs} == {"parent_unmerged"}
    assert edges[("P", "D")].observation == "not_observed"


@then("waits and intervention results use exact strict timestamps")
def assert_waits(dependency_case) -> None:
    waits = dependency_case["audit"].edges[0].waits
    assert [(wait.status, wait.intervention) for wait in waits] == [
        ("observed", "indeterminate"),
        ("unclassified", "not_observed"),
        ("observed", "indeterminate"),
    ]
    assert waits[0].retry_run_id == "hermes:run:16"
    assert waits[0].operator_comment_id is None
    assert waits[2].retry_run_id == "hermes:run:17"
    assert waits[2].operator_comment_id == "hermes:comment:1"

    strict_case = _audit_case(include_wait_boundaries=False)
    strict_audit = audit_dependencies(strict_case["snapshot"], strict_case["facts"])
    strict_waits = strict_audit.edges[0].waits
    assert [(wait.status, wait.intervention) for wait in strict_waits] == [
        ("observed", "before_retry"),
        ("unclassified", "not_observed"),
    ]
    assert strict_waits[0].retry_run_id == "hermes:run:15"
    assert strict_waits[0].operator_comment_id == "hermes:comment:1"
    edges = {(edge.parent_id, edge.child_id): edge for edge in dependency_case["audit"].edges}
    assert edges[("P", "D")].waits[0].status == "not_observed"


@given(
    "canonical pull request references across hosts and repositories",
    target_fixture="github_case",
)
def github_case():
    refs = [
        PullRequestRef.parse(f"https://github.com/acme/repo/pull/{number}")
        for number in range(1, 52)
    ]
    refs.extend(
        (
            PullRequestRef.parse("https://github.example/acme/other/pull/2"),
            PullRequestRef.parse("https://github.com/zed/repo/pull/3"),
        )
    )
    return {"refs": tuple(reversed(refs)), "calls": [], "facts": ()}


@when("GitHub facts are fetched through the fake runner")
def fetch_facts(github_case) -> None:
    def runner(args, **kwargs):
        github_case["calls"].append((args, kwargs))
        if args == ["gh", "--version"]:
            return subprocess.CompletedProcess(args, 0, "gh version 2.96.1\n", "")
        query = next(value[6:] for value in args if value.startswith("query="))
        aliases = re.findall(r"pr(\d+): repository.*?pullRequest\(number: (\d+)\)", query)
        owner = re.search(r'owner: "([^"]+)"', query).group(1)
        repository = re.search(r'name: "([^"]+)"', query).group(1)
        data = {
            f"pr{alias}": {
                "pullRequest": {
                    "id": f"NODE-{args[4]}-{owner}-{repository}-{number}",
                    "url": f"https://{args[4]}/{owner}/{repository}/pull/{number}",
                    "number": int(number),
                    "state": "MERGED",
                    "mergedAt": "2026-07-29T00:20:00Z",
                }
            }
            for alias, number in aliases
        }
        return subprocess.CompletedProcess(args, 0, json.dumps({"data": data}), "")

    github_case["facts"] = fetch_pull_request_facts(github_case["refs"], runner=runner)


@then("GitHub receives version then query-only batches of at most fifty aliases")
def assert_batches(github_case) -> None:
    calls = github_case["calls"]
    assert calls[0][0] == ["gh", "--version"]
    assert all(call[1]["shell"] is False and call[1]["timeout"] == 5 for call in calls)
    queries = calls[1:]
    assert len(queries) == 4
    assert all(call[0][:3] == ["gh", "api", "graphql"] for call in queries)
    assert all(
        "mutation" not in next(item for item in call[0] if item.startswith("query=")).lower()
        for call in queries
    )
    assert all(
        len(re.findall(r"pr\d+:", next(item for item in call[0] if item.startswith("query="))))
        <= 50
        for call in queries
    )
    assert [
        (fact.host, fact.owner, fact.repository, fact.number) for fact in github_case["facts"]
    ] == sorted(
        (fact.host, fact.owner, fact.repository, fact.number) for fact in github_case["facts"]
    )


@given("dependency acquisition and handoff failures", target_fixture="failure_case")
def failure_case():
    return {"errors": []}


@when("invalid dependency evidence is audited or fetched")
def fail_closed(failure_case) -> None:
    ref = PullRequestRef.parse("https://github.com/acme/repo/pull/1")
    with pytest.raises(SourceUnavailableError) as old_version:
        fetch_pull_request_facts(
            (PullRequestRef.parse("https://github.com/acme/repo/pull/1"),),
            runner=lambda args, **kwargs: subprocess.CompletedProcess(
                args, 0, "gh version 2.95.0\n", ""
            ),
        )
    failure_case["errors"].append(old_version.value)
    with pytest.raises(SourceUnavailableError) as missing_command:
        fetch_pull_request_facts(
            (PullRequestRef.parse("https://github.com/acme/repo/pull/1"),),
            runner=lambda args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
        )
    failure_case["errors"].append(missing_command.value)
    with pytest.raises(SourceUnavailableError) as timeout:
        fetch_pull_request_facts(
            (ref,),
            runner=lambda args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(args, 5)),
        )
    failure_case["errors"].append(timeout.value)

    def oversized_runner(args, **kwargs):
        output = "gh version 2.96.0\n" if args[1] == "--version" else "x" * 65537
        return subprocess.CompletedProcess(args, 0, output, "")

    with pytest.raises(SourceUnavailableError) as oversized_output:
        fetch_pull_request_facts((ref,), runner=oversized_runner)
    failure_case["errors"].append(oversized_output.value)

    def malformed_runner(args, **kwargs):
        output = "gh version 2.96.0\n" if args[1] == "--version" else "not JSON"
        return subprocess.CompletedProcess(args, 0, output, "")

    with pytest.raises(InvalidCoreError) as malformed:
        fetch_pull_request_facts((ref,), runner=malformed_runner)
    failure_case["errors"].append(malformed.value)

    def inaccessible_runner(args, **kwargs):
        output = "gh version 2.96.0\n" if args[1] == "--version" else '{"data":{"pr0":null}}'
        return subprocess.CompletedProcess(args, 0, output, "")

    with pytest.raises(SourceUnavailableError) as inaccessible:
        fetch_pull_request_facts((ref,), runner=inaccessible_runner)
    failure_case["errors"].append(inaccessible.value)
    snapshot = _dependency_snapshot()
    with pytest.raises(InvalidCoreError) as missing_fact:
        audit_dependencies(snapshot, ())
    failure_case["errors"].append(missing_fact.value)
    with pytest.raises(ValueError):
        PullRequestRef.parse("https://github.com/acme/repo/pull/1?bad=1")
    with pytest.raises(InvalidCoreError) as duplicate_fact:
        parent_ref = required_pull_request_refs(snapshot)[0]
        audit_dependencies(snapshot, (_fact(parent_ref, at(20)), _fact(parent_ref, at(20))))
    failure_case["errors"].append(duplicate_fact.value)
    bad_snapshot = replace(snapshot, handoffs=())
    with pytest.raises(InvalidCoreError) as missing_handoff:
        required_pull_request_refs(bad_snapshot)
    failure_case["errors"].append(missing_handoff.value)
    with pytest.raises(InvalidCoreError) as multiple_handoffs:
        required_pull_request_refs(
            replace(snapshot, handoffs=(snapshot.handoffs[0], *snapshot.handoffs))
        )
    failure_case["errors"].append(multiple_handoffs.value)


@then("a specific boundary error is returned without a partial audit")
def assert_failures(failure_case) -> None:
    assert all(str(error) for error in failure_case["errors"])
    assert "requires gh 2.96" in str(failure_case["errors"][0])
    assert "unavailable" in str(failure_case["errors"][1])
    assert "timed out" in str(failure_case["errors"][2])
    assert "output exceeded 65536 bytes" in str(failure_case["errors"][3])
    assert "malformed GraphQL JSON" in str(failure_case["errors"][4])
    assert "missing or inaccessible" in str(failure_case["errors"][5])
    assert "required GitHub PR evidence is missing" in str(failure_case["errors"][6])
    assert "contradictory duplicate PR facts" in str(failure_case["errors"][7])
    assert "handoff is missing" in str(failure_case["errors"][8])
    assert "handoff is multiple" in str(failure_case["errors"][9])
