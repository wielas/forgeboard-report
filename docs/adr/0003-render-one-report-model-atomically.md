# ADR-0003: Render one report model atomically

**Status:** accepted · 2026-07-29

## Context

FR-8 requires schema-versioned JSON and a paste-ready Markdown summary of the
same report. FR-7 requires both to retain traceable evidence, NFR-2 requires
byte-identical output, and FR-9 forbids a plausible partial aggregate on
failure. Independent aggregation paths can drift; two unrelated output writes
can leave one apparently valid artifact behind.

Rates and means also require an explicit numeric policy. Binary floating-point,
locale-dependent formatting, and unspecified rounding would weaken byte-level
determinism.

## Decision

The metrics engine builds one immutable `Report` with schema version
`forgeboard.report.v1`. JSON and Markdown are projections of that model; neither
renderer recalculates metrics or parses the other renderer's output.

The model retains numerator/denominator or sum/count alongside displayed
values. Calculations use `Decimal`. Displayed divisions are rounded once to at
most six fractional digits using round-half-even; trailing fractional zeroes
are removed. An undefined denominator or empty score set is represented by a
typed `unavailable` status and JSON `null`, never zero.

Canonical output rules are UTF-8, LF newlines, one final newline, schema-defined
object field order, explicit stable list sort keys, and no clock-derived report
timestamp. The resolved interval, source versions, and source fingerprints
provide reproducibility evidence without injecting volatile acquisition time.

The command accepts one output directory that must not already exist. It writes
`report.json` and `report.md` completely inside a sibling staging directory,
flushes them, and renames the directory into place. Validation and both renders
finish before publication. A failure removes only the private staging
directory; it never touches the requested destination or source inputs.

## Consequences

- Human and machine outputs cannot disagree on metric values or evidence.
- Golden-file tests can assert exact bytes across locale, timezone, and hash
  seed changes.
- Consumers receive a directory rather than two freely chosen paths, trading
  some CLI flexibility for all-or-nothing publication.
- Six-place rounding is a presentation rule; exact contributing scores and
  integer sums/counts remain auditable.
- Schema field order is part of the v1 output contract even though JSON object
  semantics do not require it.

## Options considered

1. **One report model, two renderers, atomic output directory** — chosen because
   it prevents semantic and publication drift.
2. **Independent JSON and Markdown aggregation/rendering** — rejected because
   duplicated metric logic will eventually diverge.
3. **Render Markdown by parsing emitted JSON** — rejected because it introduces
   a lossy serialization round trip and makes human rendering depend on output
   encoding details.
4. **Write two caller-selected paths** — rejected because ordinary filesystem
   APIs cannot publish that pair atomically.
