# ADR-0006: Join graph chunks by bootstrap idempotency key

**Status:** proposed · 2026-07-29

## Context

Graph chunk ids such as `CHUNK-C1` are Forge contract ids, not Hermes task ids.
Hermes 0.19 generates opaque task ids shaped like `t_<random-hex>`. The
authoritative Forge bootstrap creates each graph card with idempotency key
`<board>-<chunk-id>`, reads the returned opaque task id, and uses that opaque id
for parent links.

Hermes persists both values in `tasks`, but its index on `idempotency_key` is
not unique. Archived-key reuse and malformed or historical data can therefore
leave more than one row with the same key. Joining by task id cannot work, and
searching a title, body, result, summary, or comment would turn mutable prose
into identity.

## Decision

For resolved board slug `B` and graph chunk id `G`, the report derives the
literal bootstrap key `B-G` and compares it to `tasks.idempotency_key` with
exact, case-sensitive equality.

The captured board must contain exactly one matching task row across all
statuses, including archived rows. No match or multiple matches is invalid core
data and fails the report. The matched row's `tasks.id` is retained as an
opaque source id and is the only value used to join that card to
`task_links`, `task_runs`, `task_events`, and `task_comments`.

Titles, bodies, results, summaries, errors, comments, and metadata such as
`chunk_id` are never fallback identity sources.

## Consequences

- The graph-to-card join matches the bootstrap contract while keeping Forge
  contract ids distinct from Hermes storage ids.
- Case drift, a board/key mismatch, archived-key reuse, or any duplicate fails
  closed instead of silently selecting a plausible card.
- Fixtures must preserve both the exact idempotency key and an unrelated opaque
  task id, and must cover missing, duplicate, and case-mismatched keys.
- All later board relationships operate on opaque task ids only after the
  one-to-one graph mapping has been validated.
- Supporting a different card bootstrap requires an explicit versioned join
  strategy rather than a prose heuristic.

## Options considered

1. **Exact `<board>-<chunk-id>` idempotency-key join** — chosen because it is
   the structured, retry-stable identity written by the authoritative
   bootstrap.
2. **Treat the graph id as `tasks.id`** — rejected because Hermes generates
   opaque `t_*` ids and the bootstrap explicitly reads them back.
3. **Parse task titles or bodies** — rejected because prose is mutable,
   non-unique, and not a schema.
4. **Use handoff metadata `chunk_id` as the card identity** — rejected because
   it is written only by later lifecycle handoffs, can be absent before
   completion, and is validation evidence rather than bootstrap identity.
