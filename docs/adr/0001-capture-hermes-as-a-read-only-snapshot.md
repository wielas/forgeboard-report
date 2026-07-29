# ADR-0001: Capture Hermes as a read-only SQLite snapshot

**Status:** proposed · 2026-07-29

## Context

The report must inspect a live Hermes 0.19.0 board without changing it, retain
stable event/comment/run identifiers, finish a 100-card report within 30
seconds, and remain testable without Hermes installed. The public JSON commands
are useful compatibility oracles, but they are not a sufficient live
acquisition boundary:

- `hermes kanban list --json` calls readiness recomputation in Hermes 0.19.0,
  so a command that looks like a read can change task state;
- `hermes kanban show --json` requires one process per card and omits the
  database row ids for comments and events; and
- a caller-provided export would be safely immutable but is not among FR-1's
  live report inputs.

SQLite WAL and rollback-journal sidecars make a naïve copy unsafe while a
writer is active. Opening the source database through SQLite can also create a
shared-memory sidecar in some WAL configurations.

## Decision

The live Hermes adapter resolves the selected board's versioned Hermes 0.19
database location, then captures it using filesystem reads only:

1. validate and confine the board slug before resolving the database path;
2. fingerprint the main database and any `-wal` or `-journal` sidecars;
3. copy those files to a private temporary directory using read-only source
   handles;
4. fingerprint the source set again, retrying a bounded number of times if its
   membership or bytes changed; and
5. open only the private copy with Python's standard-library `sqlite3`,
   allowing SQLite to recover or create transient sidecars in the temporary
   directory.

Failure to obtain a stable copy is an inconsistent-source error, not permission
to calculate from a torn snapshot. The adapter validates the required Hermes
0.19 tables and columns before reading them. It neither imports Hermes internals
nor invokes a board command.

Recorded compatibility fixtures pair raw 0.19 database snapshots with the
corresponding public `show --json` records. Contract tests prove that the
adapter's normalized values agree with those public records while retaining
the stable row ids that JSON omits.

## Consequences

- Board acquisition has no write-capable handle to the live board and can be
  checked byte-for-byte for NFR-1.
- One bounded local snapshot replaces O(card count) Hermes subprocesses and
  preserves all identifiers needed by FR-7.
- The adapter is intentionally coupled to the Hermes 0.19 on-disk schema. A
  missing or changed required column fails closed until a new compatibility
  fixture and decoder are added.
- Snapshot cost is linear in the small board database and its live journal,
  but no historical cache or second database persists after the process exits.
- The database path resolver is versioned adapter code, not domain logic.

## Options considered

1. **Read-only filesystem snapshot of the Hermes SQLite board** — chosen because
   it meets the mutation, identifier, fixture, and latency constraints
   together.
2. **Public `list/show --json` commands** — rejected for live collection because
   `list` may mutate readiness, `show` loses row ids, and per-card processes
   threaten NFR-3.
3. **Require a recorded export from the operator** — rejected because it adds a
   required input and removes the live-board behavior signed off in FR-1.
