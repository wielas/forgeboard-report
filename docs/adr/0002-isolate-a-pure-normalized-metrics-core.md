# ADR-0002: Isolate a pure normalized metrics core

**Status:** accepted · 2026-07-29

## Context

Hermes rows, graph JSON, and GitHub responses use different identifiers,
timestamp encodings, optional fields, and lifecycle vocabulary. The report must
produce deterministic metrics from recorded fixtures without either executable
installed, while refusing unreadable core evidence and surfacing optional
noncanonical history rather than guessing.

Calculating directly from adapter dictionaries would mix source compatibility,
period filtering, causal rules, and presentation. That makes BDD scenarios hard
to isolate and lets source-specific quirks silently change metric semantics.

## Decision

All external adapters produce one immutable, typed `SourceSnapshot`. A
normalization and validation layer converts raw values into domain records
before the pure metrics engine runs. The engine has no filesystem, subprocess,
clock, environment, network, or rendering access.

The normalized boundary includes graph chunks and edges, board cards and
links, runs, events, comments, canonical judge verdicts, block occurrences,
operator identities, and pull-request merge facts. Every record carries a
stable source id and its relevant UTC timestamp.

A schema decoder registry is the only place allowed to recognize canonical
metadata. From structured run metadata it accepts `forge.judge.v1` verdicts,
`forge.block.v1` reason classes, and `forge.chunk.v1` PR handoffs. A native
Hermes block kind remains evidence but is not translated into a Forge
`reason_class`. The registry never derives canonical values by searching
free-form bodies, summaries, errors, reasons, or comments.

Validation distinguishes three cases:

- unreadable or contradictory core records fail the entire report;
- valid absence is represented explicitly, such as no canonical judge verdicts
  producing an unavailable quality mean; and
- optional or noncanonical evidence becomes a typed warning plus unclassified
  evidence, never a guessed metric value.

Metrics return a complete typed `Report`, including evidence references, rather
than loose aggregates.

## Consequences

- Unit and BDD tests can exercise every metric from small in-memory fixtures.
- Live and recorded adapters must satisfy the same contract, preventing a
  second set of metric semantics.
- Adding support for a later source schema requires an explicit decoder and
  contract fixtures but does not change the metrics engine.
- Strict validation will reject some historical boards that a prose-scraping
  tool might appear to summarize; that is the intended FR-9 behavior.
- The model contains more explicit record types, but this cost is paid once at
  the boundary rather than throughout the system.

## Options considered

1. **Typed normalized snapshot plus pure metrics engine** — chosen for
   deterministic, fixture-first enforcement and explicit evidence policy.
2. **Calculate directly from raw JSON/SQLite dictionaries** — rejected because
   adapter drift and metric logic would be inseparable.
3. **Persist an intermediate reporting database** — rejected as unnecessary
   operational state and directly out of scope.
