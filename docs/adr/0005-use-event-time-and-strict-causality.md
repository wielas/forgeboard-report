# ADR-0005: Use event time and strict causality

**Status:** accepted · 2026-07-29

## Context

The requirements define a half-open period but do not assign every metric a
specific timestamp. Comments can fall inside the period while dispatch happened
before it; PR merges can fall outside it while an in-period child run must be
classified against that merge. Hermes timestamps can have equal resolution,
making an apparent order unknowable.

Using one chunk-level inclusion timestamp for all records would either omit
valid in-period evidence or pull unrelated lifecycle history into aggregates.
Treating equal timestamps as ordered would fabricate causality.

## Decision

The report uses occurrence/event time:

- chunk completion is selected by the card's canonical terminal-completion
  timestamp;
- a judge verdict is selected by the containing finished run's `ended_at`;
- a block is selected by the block event timestamp, falling back to the
  corresponding blocked run's `ended_at` only when no canonical event exists;
- a comment is selected by its own creation timestamp; and
- a child run is selected by its `started_at`.

All timestamps are parsed as aware values and normalized to UTC. Membership is
`from <= timestamp < to`. A graph structure, current board link, or PR merge
timestamp may appear as reference context even when it is outside the period,
but it never becomes an out-of-period lifecycle contribution to an aggregate.
All declared graph edges are audited; an edge with no in-period child run is
reported as `not_observed`, not as having passed its gate.

Minimal boundary history may be read to classify in-period evidence—for
example, the first claim before `from` and the terminal event after a comment.
Those boundary records are tagged `context` and excluded from counts.

Causal predicates use strict comparisons. “Before” is `<`, “after” is `>`, and
equal timestamps produce `indeterminate` plus their evidence ids. A chunk
needed an operator comment when a canonical block precedes an operator comment
and that comment precedes the next claim; the chunk is counted once even if
several comments satisfy the predicate.

## Consequences

- Every aggregate has an explicit timestamp rule and respects the half-open
  interval.
- Boundary evidence makes post-dispatch/pre-terminal classification possible
  without counting out-of-period activity.
- Some same-resolution histories are honestly indeterminate instead of being
  forced into before/after categories.
- Structural edge audit rows can exist without an observed run outcome; JSON
  and Markdown must preserve that distinction.
- Changing these timestamp rules changes reported numbers and therefore
  requires a new ADR/schema review rather than an implementation tweak.

## Options considered

1. **Per-record event time with strict causal comparisons** — chosen because it
   follows FR-1 while retaining honest gate evidence.
2. **Include all history for chunks completed in the period** — rejected
   because out-of-period verdicts, blocks, and comments would contribute to the
   report.
3. **Include a chunk when any part of its lifecycle overlaps the period** —
   rejected because aggregates would use different hidden windows.
4. **Break timestamp ties with database ids** — rejected for causality because
   insertion order is stable evidence ordering, not proof that one real-world
   event preceded another.
