"""Read-only, bounded GitHub CLI acquisition for canonical PR references."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from forgeboard_report.domain import PullRequestFact, PullRequestRef
from forgeboard_report.errors import InvalidCoreError, SourceUnavailableError

_MINIMUM_GH_VERSION = (2, 96, 0)
_MAX_BATCH_SIZE = 50
_TIMEOUT_SECONDS = 5
_VERSION_PATTERN = re.compile(r"^gh version (\d+)\.(\d+)(?:\.(\d+))?", re.MULTILINE)
_Runner = Callable[..., subprocess.CompletedProcess[str]]


def fetch(
    pr_refs: Iterable[PullRequestRef], runner: _Runner = subprocess.run
) -> tuple[PullRequestFact, ...]:
    """Validate ``gh`` and fetch every unique PR through query-only GraphQL."""
    return fetch_pull_request_facts(pr_refs, runner=runner)


def fetch_pull_request_facts(
    pr_refs: Iterable[PullRequestRef],
    runner: _Runner = subprocess.run,
) -> tuple[PullRequestFact, ...]:
    """Return sorted verified facts, or fail before yielding a partial result."""
    refs = tuple(sorted(set(pr_refs), key=_ref_key))
    if not refs:
        return ()
    _validate_gh_version(runner)
    facts: list[PullRequestFact] = []
    grouped: dict[tuple[str, str, str], list[PullRequestRef]] = {}
    for ref in refs:
        grouped.setdefault((ref.host, ref.owner, ref.repository), []).append(ref)
    for group_key in sorted(grouped):
        group = grouped[group_key]
        for start in range(0, len(group), _MAX_BATCH_SIZE):
            facts.extend(_fetch_batch(group[start : start + _MAX_BATCH_SIZE], runner))
    facts_tuple = tuple(sorted(facts, key=_fact_key))
    if len(facts_tuple) != len(refs):
        raise InvalidCoreError("github", "response did not yield exactly one fact per requested PR")
    if len({fact.node_id for fact in facts_tuple}) != len(facts_tuple):
        raise InvalidCoreError("github", "response assigns one node id to multiple pull requests")
    return facts_tuple


def _validate_gh_version(runner: _Runner) -> None:
    result = _run(("gh", "--version"), runner)
    if result.returncode != 0:
        raise SourceUnavailableError("github cli", _command_failure(result))
    match = _VERSION_PATTERN.search(_output(result.stdout))
    if match is None:
        raise SourceUnavailableError("github cli", "could not parse gh --version output")
    version = tuple(int(part or 0) for part in match.groups())
    if version < _MINIMUM_GH_VERSION:
        required = ".".join(str(part) for part in _MINIMUM_GH_VERSION[:2])
        found = ".".join(str(part) for part in version)
        raise SourceUnavailableError(
            "github cli", f"requires gh {required} or later; found {found}"
        )


def _fetch_batch(refs: list[PullRequestRef], runner: _Runner) -> list[PullRequestFact]:
    if not refs or len(refs) > _MAX_BATCH_SIZE:
        raise ValueError("a GitHub GraphQL batch must contain 1 through 50 references")
    host = refs[0].host
    owner = refs[0].owner
    repository = refs[0].repository
    if any((ref.host, ref.owner, ref.repository) != (host, owner, repository) for ref in refs):
        raise ValueError("a GitHub GraphQL batch must address one repository")
    query = _query(owner, repository, refs)
    result = _run(
        ("gh", "api", "graphql", "--hostname", host, "-f", f"query={query}"),
        runner,
    )
    if result.returncode != 0:
        raise SourceUnavailableError("github", _command_failure(result))
    return _decode_batch(_output(result.stdout), refs)


def _query(owner: str, repository: str, refs: list[PullRequestRef]) -> str:
    fields = "id url number state mergedAt"
    aliases = " ".join(
        f"pr{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(repository)}) "
        f"{{ pullRequest(number: {ref.number}) {{ {fields} }} }}"
        for index, ref in enumerate(refs)
    )
    return f"query ForgeboardPullRequests {{ {aliases} }}"


def _decode_batch(output: str, refs: list[PullRequestRef]) -> list[PullRequestFact]:
    try:
        response = json.loads(output)
    except json.JSONDecodeError as error:
        raise InvalidCoreError("github", f"malformed GraphQL JSON: {error}") from error
    if not isinstance(response, dict) or set(response) - {"data", "errors"}:
        raise InvalidCoreError(
            "github", "GraphQL response must contain only data and optional errors"
        )
    if response.get("errors"):
        raise SourceUnavailableError(
            "github", "GraphQL reported inaccessible or missing pull-request evidence"
        )
    data = response.get("data")
    if not isinstance(data, dict) or set(data) != {f"pr{index}" for index in range(len(refs))}:
        raise InvalidCoreError(
            "github", "GraphQL response aliases do not match requested pull requests"
        )
    return [_decode_fact(data[f"pr{index}"], ref) for index, ref in enumerate(refs)]


def _decode_fact(value: Any, expected: PullRequestRef) -> PullRequestFact:
    if value is None:
        raise SourceUnavailableError("github", f"PR {expected.url!r} is missing or inaccessible")
    if not isinstance(value, dict) or set(value) != {"pullRequest"}:
        raise InvalidCoreError("github", f"PR {expected.url!r} has malformed repository evidence")
    pull_request = value["pullRequest"]
    if pull_request is None:
        raise SourceUnavailableError("github", f"PR {expected.url!r} is missing or inaccessible")
    if not isinstance(pull_request, dict) or set(pull_request) != {
        "id",
        "url",
        "number",
        "state",
        "mergedAt",
    }:
        raise InvalidCoreError("github", f"PR {expected.url!r} has malformed field evidence")
    node_id = pull_request["id"]
    url = pull_request["url"]
    number = pull_request["number"]
    state = pull_request["state"]
    merged_at = pull_request["mergedAt"]
    if not isinstance(node_id, str) or not node_id:
        raise InvalidCoreError("github", f"PR {expected.url!r} has no node id")
    if not isinstance(url, str) or not isinstance(number, int) or isinstance(number, bool):
        raise InvalidCoreError("github", f"PR {expected.url!r} has malformed identity fields")
    try:
        actual = PullRequestRef.parse(url)
    except ValueError as error:
        raise InvalidCoreError(
            "github", f"PR {expected.url!r} returned malformed canonical URL"
        ) from error
    if actual != expected or number != expected.number:
        raise InvalidCoreError(
            "github", f"PR {expected.url!r} returned contradictory identity evidence"
        )
    if not isinstance(state, str) or not state:
        raise InvalidCoreError("github", f"PR {expected.url!r} has no state")
    parsed_merged_at = _parse_merged_at(merged_at, expected)
    return PullRequestFact(
        node_id=node_id,
        host=expected.host,
        owner=expected.owner,
        repository=expected.repository,
        number=expected.number,
        url=expected.url,
        state=state,
        merged_at=parsed_merged_at,
        evidence_id=f"github:node:{node_id}",
    )


def _parse_merged_at(value: object, expected: PullRequestRef) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidCoreError("github", f"PR {expected.url!r} has malformed mergedAt")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidCoreError("github", f"PR {expected.url!r} has malformed mergedAt") from error
    if parsed.tzinfo is None:
        raise InvalidCoreError("github", f"PR {expected.url!r} has timezone-naive mergedAt")
    return parsed.astimezone(UTC)


def _run(args: tuple[str, ...], runner: _Runner) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(args),
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise SourceUnavailableError("github cli", "gh command is unavailable") from error
    except subprocess.TimeoutExpired as error:
        raise SourceUnavailableError("github", "command timed out after five seconds") from error
    except OSError as error:
        raise SourceUnavailableError("github", f"could not run gh: {error}") from error


def _output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[:65536]


def _command_failure(result: subprocess.CompletedProcess[str]) -> str:
    detail = _output(result.stderr).strip() or _output(result.stdout).strip() or "no command output"
    return f"gh exited {result.returncode}: {detail}"


def _ref_key(ref: PullRequestRef) -> tuple[str, str, str, int]:
    return (ref.host, ref.owner, ref.repository, ref.number)


def _fact_key(fact: PullRequestFact) -> tuple[str, str, str, int]:
    return (fact.host, fact.owner, fact.repository, fact.number)
