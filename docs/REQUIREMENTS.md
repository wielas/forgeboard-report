# Forgeboard Report — Requirements

**Status:** signed off by human · 2026-07-29

## Mission

Forgeboard Report gives the Forge operator a reproducible, read-only account of
what happened on one Hermes project board during a bounded period. It replaces
hand-counted retro rows with deterministic numbers and preserves enough evidence
to audit contract bounces, operator interventions, and whether the dependency
graph and merged-PR gate matched the lifecycle that actually ran.

## Definitions

- A **period** is a half-open interval `[from, to)` expressed by timezone-aware
  ISO-8601 timestamps.
- A **chunk** is an entry in the supplied `docs/chunks/graph.json`; its board
  card is the card whose contract id matches that graph entry.
- A **canonical judge verdict** is run metadata with schema
  `forge.judge.v1`.
- **Dispatch** begins at a chunk card's first `claimed` event.
- An **operator comment** is a post-dispatch card comment whose author matches
  one of the operator identities supplied for the report.
- A chunk **needed an operator comment to become executable** when an operator
  comment occurs after its blocking event and before its next `claimed` event.
- A **declared edge** is a `depends_on` relationship in the supplied graph.
  An **attached edge** is the corresponding parent relationship observed on the
  board.

## Functional requirements

### FR-1 — Select one lifecycle period

Given a board slug, graph file, and timezone-aware `from` and `to` timestamps,
when the operator requests a report, then only matching lifecycle records from
that board in `[from, to)` contribute to the report and the resolved inputs are
echoed in its evidence.

### FR-2 — Measure machine-authored contract bounce rate

Given completed graph chunks and their canonical judge verdicts in the period,
when the report is produced, then it reports `bounced chunks / completed
chunks`, counting a chunk once in the numerator if any verdict for it is
`bounce`, and lists the contributing chunk and verdict identifiers.

### FR-3 — Measure judge quality scores

Given canonical judge verdicts in the period, when the report is produced, then
it reports the arithmetic mean of `spec_fidelity`, `scenario_integrity`, and
`architectural_conformance` across those verdicts and lists every contributing
score; if none exist, the value is explicitly unavailable rather than zero.

### FR-4 — Classify blocked work

Given blocked runs or block events in the period, when the report is produced,
then it reports the distribution of the canonical `reason_class` values and
lists unclassified blocked evidence separately instead of silently discarding
it or guessing a class.

### FR-5 — Count operator intervention

Given the operator identity set and chunk-card event histories, when a
post-dispatch operator comment occurs before that chunk reaches its terminal
state, then the report counts the comment, identifies the affected chunk and
timestamp, and distinguishes it from worker, prejudge, and automated comments;
it separately counts chunks meeting the definition of “needed an operator
comment to become executable.”

### FR-6 — Audit dependency edges and the merged-parent gate

Given the supplied graph, board parent relationships, chunk runs, parent PR
handoffs, and PR merge state, when the report is produced, then each declared
edge is classified as attached or missing and carries evidence showing whether
the child ran before or after the parent merge, whether it recorded a
`failing-prereq` wait, and whether an operator intervention preceded its
successful retry; attached board edges absent from the graph are reported as
unexpected.

### FR-7 — Preserve timing and raw evidence references

Given any contributing chunk, verdict, block, comment, dependency edge, or PR,
when it appears in the report, then the report includes its stable identifier
and relevant timestamps so a reviewer can trace every aggregate back to the
source record.

### FR-8 — Emit machine and human output without mutation

Given a valid report, when output is requested, then the command emits
schema-versioned JSON and a paste-ready Markdown summary containing the
canonical retro metrics plus intervention and dependency findings, without
editing the board, graph, requirements, roadmap, decision log, or
`docs/retro-metrics.md`.

### FR-9 — Refuse misleading reports

Given an unknown board, invalid or cyclic graph, invalid period, unavailable
required command, or unreadable core data, when reporting is attempted, then
the command exits non-zero with an actionable error and does not emit a
plausible partial aggregate; noncanonical optional evidence is instead surfaced
as a warning as required by FR-4.

## Non-functional requirements

### NFR-1 — Read-only operation

For every acceptance test, all external integrations are observed to receive
read operations only, and a before/after snapshot of the board and input files
is byte-for-byte unchanged.

### NFR-2 — Determinism

For identical normalized source records and arguments, repeated executions
produce byte-identical JSON and Markdown, including stable ordering of chunks,
edges, reasons, warnings, and evidence.

### NFR-3 — Performance

On the project’s Python 3.12 environment, report computation over a fixture of
100 chunks, 300 runs, and 1,000 events completes in under one second; a live
report over a 100-card board completes in under 30 seconds when each external
command responds within five seconds.

### NFR-4 — Compatibility

The live adapter is verified against Hermes 0.19.0 JSON output and GitHub CLI
2.96 or later, while the metrics engine remains testable from recorded fixtures
without either executable installed.

### NFR-5 — Verification

`make check` passes with every functional requirement mapped to at least one BDD
scenario and project coverage at or above the repository’s configured 85%
floor.

## Out of scope

1. Aggregating multiple boards into one report.
2. Editing `docs/retro-metrics.md`, any planning document, or any board record.
3. A daemon, scheduler, web dashboard, or hosted service.
4. Repairing, backfilling, or rewriting malformed historical metadata.
5. Inventing semantic dependencies not declared in the graph; the report
   exposes evidence for the operator to judge.
6. Persisting a second historical database or caching board state between runs.
7. Managing Hermes or GitHub authentication, starting the gateway, merging PRs,
   or unblocking cards.
8. General-purpose kanban analytics unrelated to the Forge lifecycle schemas.

## Open questions

None for product scope.

## Notes for architect

- Decide between public CLI adapters, direct database access, and recorded
  exports without weakening NFR-1 or NFR-4.
- Decide how to normalize Hermes task/event/run records and GitHub PR merge
  state behind a fixture-friendly boundary.
- Decide whether JSON and Markdown share one report model or are independently
  rendered.
- Keep noncanonical historical evidence explicit; do not make prose scraping a
  silent source of canonical metrics.
