# ADR-0004: Query PR state through batched GitHub CLI GraphQL reads

**Status:** accepted · 2026-07-29

## Context

Dependency auditing needs stable PR identifiers and merge timestamps. The
project must work with GitHub CLI 2.96 or later, must not manage
authentication, must send only read operations, and must finish a live
100-card report within 30 seconds when external commands respond within five
seconds.

One `gh pr view` process per PR is straightforward but cannot provide a useful
worst-case latency bound. A direct HTTP client would duplicate GitHub CLI
authentication and add runtime/library policy.

## Decision

The GitHub adapter:

1. validates `gh --version` as 2.96 or later before acquisition;
2. derives repository and PR number only from structured canonical handoff
   references;
3. groups references deterministically by GitHub host and repository;
4. invokes `gh api graphql` with query operations containing at most 50
   pull-request aliases per batch; and
5. requests only stable identity, URL, number, state, and `mergedAt`.

The GraphQL document is statically generated from validated integers and fixed
field selections. It contains no mutation operation. Arguments are passed
without a shell, inherited GitHub CLI authentication is used as-is, command
cache options are not enabled, and every subprocess has a five-second timeout.

A missing PR, inaccessible repository, malformed response, absent required
field, command timeout, or authentication failure makes dependency evidence
unreadable and fails the report. A present unmerged PR with `mergedAt: null` is
valid evidence that the parent gate is not satisfied. The adapter does not log
in, refresh credentials, or change GitHub state.

## Consequences

- At the stated scale, PR evidence requires a small bounded number of external
  calls rather than one call per edge.
- Existing GitHub CLI host and credential handling remain outside this
  application's scope.
- Tests can inspect the generated GraphQL abstract operation and recorded JSON
  without network access.
- GraphQL uses HTTP POST transport, but the only accepted operation is a
  side-effect-free query; NFR-1 tests assert that no mutation appears.
- GitHub Enterprise hosts remain possible when the canonical PR URL and
  existing `gh` configuration agree.

## Options considered

1. **Batched `gh api graphql` queries** — chosen for bounded latency, explicit
   read semantics, and reuse of supported CLI authentication.
2. **One `gh pr view --json` call per PR** — rejected because process count and
   worst-case latency scale with the graph.
3. **Direct REST/GraphQL client library** — rejected because it adds a runtime
   dependency and takes ownership of authentication that is out of scope.
4. **Trust merge facts embedded in run metadata** — rejected because FR-6 asks
   for PR merge state, not an unverified worker assertion.
