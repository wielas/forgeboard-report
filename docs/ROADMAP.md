# Forgeboard Report — Implementation Roadmap

**Status:** machine-authored from signed requirements, architecture, and ADR-0001 through ADR-0006 · 2026-07-29

## Execution contract

- This is the smallest complete implementation slice: six unattended chunks, each bounded to at most six likely files and at most five BDD scenarios.
- Every chunk uses the confirmed `forge-codex-lane` Hermes profile. No chunk requires unresolved human taste.
- Forge ADR-0008's integration gate applies throughout: a child starts only after every declared parent PR is merged to `main`, branches from that resulting `main`, and never relies on a stacked or unmerged parent branch.
- Each merge must leave `make check` green. A later chunk may extend a predecessor's types but may not weaken its Then-clauses or signed semantics.
- New runtime dependencies are unnecessary; introducing one would require an ADR in that implementation branch and is outside this roadmap's intended solution.

## Milestones

## M1 — De-risk source contracts

Fix the public request/graph boundary, then prove Hermes 0.19 can be captured without touching the live board.

### CHUNK-1: Define validated request and graph contracts
- **Goal:** Establish immutable request, evidence, error, and graph types that reject misleading inputs before any external acquisition.
- **Milestone:** M1  ·  **Depends on:** none
- **Serves:** `FR-1`, `FR-6`, `FR-7`, `FR-9`, `NFR-2`, `NFR-5`  ·  **Relevant ADRs:** `0002`, `0005`, `0006`
- **Touches:** `src/forgeboard_report/domain.py`, `src/forgeboard_report/errors.py`, `src/forgeboard_report/graph.py`, `tests/features/request_and_graph.feature`, `tests/steps/test_request_and_graph_steps.py`, `tests/test_graph.py`
- **Contract decisions:**
  - The graph contract is one JSON array whose objects have exactly `id`, `lane`, and `depends_on`; all three are required, ids and lanes are nonempty case-sensitive strings, dependencies are unique strings, and unknown keys are rejected. `lane` is retained as resolved-input evidence but never used as card identity.
  - Each `depends_on` entry creates a parent-to-child edge. Reject duplicate chunk ids, duplicate dependencies, missing endpoints, self-edges, non-array input, and cycles. Normalized chunks sort by id and edges by `(parent_id, child_id)`; the source fingerprint is SHA-256 of the exact graph bytes.
  - A board slug must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`. At least one `--operator` value is required; each value must be nonempty and already equal to its surrounding-whitespace-trimmed form, and duplicates are usage errors. Preserve exact case, then sort the accepted identities.
  - Parse both interval bounds as timezone-aware ISO-8601 values, require `from < to`, preserve the original resolved strings, normalize comparisons to UTC, and expose one half-open membership predicate `from <= occurrence < to`.
  - Use frozen dataclasses and tuples for domain collections. Define specific usage, source-unavailable/inconsistent, invalid-core/schema, and publication exceptions now; do not add a catch-all or renderer/adapter behavior.
- **Scenarios:**
  - Given a valid board, exact operator identities, aware bounds, and an acyclic graph, when inputs are resolved, then originals, UTC bounds, graph hash, sorted chunks, and sorted edges are retained and the lower bound is included while the upper bound is excluded.
  - Given a naïve, equal, or reversed interval, a blank/trimmed/duplicate operator, no operator, or a traversal-shaped board slug, when inputs are resolved, then a specific usage error identifies the invalid field.
  - Given duplicate chunks, duplicate dependencies, a missing endpoint, a self-edge, or a cycle, when the graph is loaded, then a specific invalid-core error is raised before source acquisition.
  - Given equivalent valid graph records in different input orders, when they are normalized, then chunk and edge iteration order is identical while each exact source byte sequence keeps its own fingerprint.
- **Out of scope:** Hermes or GitHub access, metadata decoding, metric calculation, report rendering, publication, and a runnable console entry point.
- **Done when:** `make check` is green; all listed BDD scenarios and focused graph tests pass; the repository coverage floor holds; no runtime dependency is added; implementation notes are updated without changing signed requirements, architecture, or ADRs.
- **Integration gate:** Branch from current `main`; this root chunk may merge only with `make check` green and becomes the sole parent for CHUNK-2.
- **Lane:** forge-codex-lane  ·  **Risk:** med

### CHUNK-2: Capture a stable read-only Hermes snapshot
- **Goal:** Acquire one internally coherent Hermes 0.19 board snapshot through read-only file handles while preserving every stable row id required downstream.
- **Milestone:** M1  ·  **Depends on:** `CHUNK-1`
- **Serves:** `FR-1`, `FR-2`, `FR-3`, `FR-4`, `FR-5`, `FR-6`, `FR-7`, `FR-9`, `NFR-1`, `NFR-3`, `NFR-4`, `NFR-5`  ·  **Relevant ADRs:** `0001`, `0002`, `0005`, `0006`
- **Touches:** `src/forgeboard_report/domain.py`, `src/forgeboard_report/hermes.py`, `tests/features/hermes_snapshot.feature`, `tests/steps/test_hermes_snapshot_steps.py`, `tests/fixtures/hermes_019.py`, `tests/test_hermes.py`
- **Contract decisions:**
  - Keep Hermes compatibility behind a `Hermes019Layout` resolver and inject it into the adapter. The production resolver honors `HERMES_KANBAN_HOME` before the standard Hermes root, confines the validated slug to that root, and rejects missing, escaping, or symlink-escaping database paths; it never invokes `hermes kanban`.
  - A capture attempt fingerprints the main SQLite file and the current `-wal` or `-journal` sidecar set, copies each through binary read-only handles into a private temporary directory, fingerprints the same source membership and bytes again, and accepts only an exact match. Retry at most three total attempts; exhaustion is a source-inconsistent error.
  - Open only the private copy with stdlib `sqlite3`, start one read transaction there, validate the Hermes 0.19 tables/columns needed for the Architecture data model, and extract board cards, parent links, runs, events, and comments including opaque task ids and stable row ids. Never open the live database through SQLite.
  - The raw adapter preserves exact stored timestamps, payload/metadata bytes or decoded JSON values plus their source ids, task idempotency keys across every status including archived, and source fingerprints/version. Timestamp and canonical-schema interpretation remain the normalizer's job.
  - The 0.19 fixture builder must create opaque `t_*` task ids, exact bootstrap idempotency keys, archived rows, links, run metadata, events, comments, and journal/change-during-copy cases. Pair its representative rows with recorded public `show --json` values only as a compatibility oracle; ordinary tests require no Hermes executable.
- **Scenarios:**
  - Given a stable Hermes 0.19 database and sidecar set, when the board is captured, then the private snapshot contains all required rows and stable ids, agrees with the recorded public JSON values, and the live source bytes are unchanged.
  - Given source membership or bytes change during capture, when three attempts cannot obtain identical before/after fingerprints, then capture fails as inconsistent and exposes no partial raw snapshot.
  - Given a graph card key that exists only on an archived task plus unrelated opaque task ids, when rows are captured, then all rows are preserved without title/body searching or graph-id-to-task-id substitution.
  - Given an unknown board, confined-path violation, missing database, or incompatible required 0.19 column, when capture is attempted, then a specific actionable source or schema error is raised without creating files under the live Hermes root.
- **Out of scope:** Graph-to-card cardinality enforcement, canonical metadata decoding, GitHub calls, metrics, renderers, publication, and use of mutating Hermes JSON commands.
- **Done when:** `make check` is green; the listed snapshot, compatibility, mutation, and bounded-retry scenarios pass; fixtures work without Hermes installed; changed code stays within the six listed paths; implementation docs are reconciled without altering signed inputs.
- **Integration gate:** Do not start until CHUNK-1 is merged to `main`; branch from that merged state, never from CHUNK-1's unmerged branch, and leave `make check` green for CHUNK-3.
- **Lane:** forge-codex-lane  ·  **Risk:** high · **Execution:** docker backend

## M2 — Prove lifecycle semantics

Normalize canonical evidence and calculate the signed lifecycle and merged-parent metrics from immutable snapshots.

### CHUNK-3: Normalize evidence and calculate lifecycle metrics
- **Goal:** Convert graph and Hermes records into an immutable canonical snapshot and purely calculate bounce, quality, block, and intervention findings with traceable event-time evidence.
- **Milestone:** M2  ·  **Depends on:** `CHUNK-2`
- **Serves:** `FR-1`, `FR-2`, `FR-3`, `FR-4`, `FR-5`, `FR-7`, `FR-9`, `NFR-2`, `NFR-3`, `NFR-4`, `NFR-5`  ·  **Relevant ADRs:** `0002`, `0003`, `0005`, `0006`
- **Touches:** `src/forgeboard_report/domain.py`, `src/forgeboard_report/normalize.py`, `src/forgeboard_report/metrics.py`, `tests/features/lifecycle_metrics.feature`, `tests/steps/test_lifecycle_metrics_steps.py`, `tests/test_metrics.py`, `tests/fixtures/normalize.py`, `tests/fixtures/hermes_019.py` (grew from 6 to 8 files because the judge bounce required both canonical fixture sources)
- **Contract decisions:**
  - For graph id `G` and resolved board `B`, require exactly one task across all statuses whose idempotency key is the exact case-sensitive string `B-G`; zero or multiple matches are invalid core data. Retain that task's opaque id for every run/event/comment/link join and never fall back to prose or metadata.
  - The metadata decoder registry accepts structured, additive-compatible envelopes. Current `forge.judge.v1` envelopes follow the authoritative `rubrics/judge-rubric.md` shape: `verdict` is `approve`, `approve-with-nits`, or `bounce` and all six rubric dimensions are present; this report consumes the named three finite decimal dimensions (`spec_fidelity`, `scenario_integrity`, and `architectural_conformance`) and ignores additive unknown fields. `forge.block.v1` requires a nonempty exact `reason_class`, and `forge.chunk.v1` requires exact `chunk_id` and complete `pr`; additive fields are ignored. A declared envelope missing or invalid required fields fails; an unknown schema is a typed warning.
  - Join a block event and blocked run sharing task/run id into one occurrence retaining every source ref. Only valid `forge.block.v1` metadata supplies `reason_class`; native block kind and prose remain supplemental evidence. Conflicting canonical reasons for one occurrence fail core validation, and all other block occurrences are explicitly unclassified.
  - Classify exact comment authors with operator identities taking precedence, then `forge-codex-lane` or `builder` as worker, `forge-prejudge` as prejudge, and `forge-orchestrator` or `forge-digest` as automated; every other author is unclassified. Do not inspect comment text or case-fold.
  - Apply ADR-0005 occurrence times exactly: completion timestamp for denominator membership, containing finished-run end for verdicts, block event time or blocked-run end fallback, comment creation time, and strict causal comparisons. Boundary dispatch/terminal/next-claim records may be context but never aggregate contributions; equal causal timestamps are indeterminate.
  - Calculate bounce as unique in-period-bounced completed chunks over in-period-completed chunks, list every contributing in-period verdict, and list completed chunks lacking an in-period canonical verdict. Calculate three independent Decimal means across every mapped in-period canonical verdict regardless of card completion, with sum/count/raw scores and unavailable/null semantics for no verdicts. Count qualifying operator comments and count each needed-to-execute chunk once.
  - The normalizer and metrics engine have no filesystem, subprocess, clock, environment, network, or rendering access. Every output row carries stable namespaced evidence ids and relevant UTC timestamps, with explicit sort keys from the Architecture.
- **Scenarios:**
  - Given completed chunks with multiple in-period canonical verdicts, when lifecycle metrics are calculated, then each bounced chunk enters the numerator once, all verdict ids remain evidence, and each of the three score dimensions has its own exact Decimal mean.
  - Given no canonical verdicts or a completed chunk without one, when metrics are calculated, then quality and any zero-denominator bounce value are unavailable rather than zero and missing canonical verdict coverage is listed.
  - Given paired block run/event evidence, exact canonical reasons, ordinary Hermes block kinds, an unknown schema, and conflicting canonical reasons, when normalization runs, then paired evidence is deduplicated, exact reasons are counted, noncanonical evidence is warned/unclassified, and the conflict fails closed.
  - Given exact operator, worker, prejudge, automated, and unknown authors around dispatch, block, retry, and terminal events, when intervention is calculated, then only strict post-dispatch/pre-terminal operator comments count and a block-comment-next-claim chain counts its chunk once.
  - Given records at both period bounds and equal causal timestamps, when metrics are calculated, then the lower bound contributes, the upper bound does not, minimal boundary history is context only, and timestamp ties are indeterminate rather than ordered.
- **Out of scope:** Pull-request parsing or calls, dependency-edge gate calculation, JSON/Markdown formatting, atomic publication, CLI orchestration, and prose-derived canonical values.
- **Done when:** `make check` is green; all five BDD scenarios and focused pure-core tests pass from in-memory/recorded fixtures without Hermes or GitHub; stable ordering and the 85% coverage floor hold; signed semantics remain unchanged.
- **Integration gate:** Do not start until CHUNK-2 is merged to `main`; branch from that merged state, never stack on the open adapter branch, and merge only with the full repository green for CHUNK-4.
- **Lane:** forge-codex-lane  ·  **Risk:** med

### CHUNK-4: Audit dependency gates through read-only GitHub queries
- **Goal:** Resolve canonical parent handoffs, batch read GitHub merge facts, and purely audit every declared and unexpected board edge including waits, retries, and interventions.
- **Milestone:** M2  ·  **Depends on:** `CHUNK-3`
- **Serves:** `FR-6`, `FR-7`, `FR-9`, `NFR-1`, `NFR-2`, `NFR-3`, `NFR-4`, `NFR-5`  ·  **Relevant ADRs:** `0002`, `0004`, `0005`, `0006`
- **Touches:** `src/forgeboard_report/domain.py`, `src/forgeboard_report/normalize.py`, `src/forgeboard_report/github.py`, `src/forgeboard_report/dependencies.py`, `tests/features/dependency_audit.feature`, `tests/steps/test_dependency_audit_steps.py`
- **Contract decisions:**
  - Decode a parent's handoff only from the completed run referenced by its completion event. The exact envelope is `{schema: "forge.chunk.v1", chunk_id, pr}`; `chunk_id` must exactly equal the graph parent and `pr` must be one complete HTTPS URL shaped `https://<host>/<owner>/<repo>/pull/<positive-int>` with no query or fragment. Missing, multiple, contradictory, or malformed required handoffs fail core validation.
  - Validate `gh --version` as 2.96 or later, then group unique PR refs deterministically by host/repository and issue fixed query-only `gh api graphql` operations with at most 50 aliases. Use argument arrays, `shell=False`, inherited CLI authentication, bounded output, and a five-second timeout; never invoke login, cache, REST mutation, or a shell.
  - Normalize each response to node id, host/repository/number, canonical URL, state, and optional `mergedAt`. Missing/inaccessible/malformed/contradictory PR evidence or command failure is source-unavailable/invalid core; a reachable PR with null `mergedAt` is valid parent-unmerged evidence.
  - Audit every graph edge against opaque-id board links as attached or missing and emit every board link absent from the graph as unexpected, including links with a non-graph endpoint. Resolve required parent handoff and PR evidence for every declared edge even when it has no in-period child run; structure alone may be context and does not add a lifecycle aggregate.
  - For every in-period child run start, compare strictly with the parent merge time and classify before_merge, after_merge, indeterminate, or parent_unmerged. With no in-period child run the edge is not_observed, never passed. Exact `forge.block.v1.reason_class == "failing-prereq"` is observed; a relevant noncanonical block is unclassified; absence is not_observed. Because `forge.block.v1` carries no parent id, a child-scoped failing-prereq occurrence is reported against every declared incoming edge for that child; never infer a narrower parent from prose.
  - For each failing-prereq wait, inspect the first strictly later child run with canonical completed outcome and report whether an exact operator comment falls strictly between wait and retry start. Retain wait, comment, retry, parent handoff, PR, link, and run evidence ids/timestamps; ties remain indeterminate.
- **Scenarios:**
  - Given declared edges plus matching, missing, and extra opaque-id board links, when dependency audit runs, then every declared edge is attached or missing and every undeclared attached link is reported as unexpected with stable evidence.
  - Given merged and reachable-unmerged parent PRs plus child starts before, after, equal to, or absent around the gate, when edges are audited, then runs are classified before_merge, after_merge, indeterminate, parent_unmerged, or not_observed exactly.
  - Given canonical failing-prereq waits, ordinary unclassified blocks, later completed retries, and operator comments around retry start, when causality is audited, then observed/unclassified/not_observed waits and intervention-before-retry results follow strict timestamps without prose mapping.
  - Given valid handoff URLs across repositories and hosts, when PR state is fetched, then GitHub CLI 2.96+ receives deterministic read-only GraphQL batches of at most 50 aliases and normalized facts sort by host, repository, and number.
  - Given a missing command, old CLI, timeout, malformed response, inaccessible PR, or missing/contradictory required handoff, when acquisition or audit is attempted, then a specific nonzero-boundary error is produced and no partial dependency result is returned.
- **Out of scope:** GitHub authentication management, mutations, per-PR `gh pr view` loops, semantic dependencies absent from the graph, lifecycle metric recalculation, renderers, publication, and CLI exit mapping.
- **Done when:** `make check` is green; all dependency, batching, version, timeout, query-only, and causal scenarios pass using a fake command runner; at most the six listed paths change; no runtime dependency or credential handling is added; docs are reconciled without semantic drift.
- **Integration gate:** Do not start until CHUNK-3 is merged to `main`; branch from the merged pure-core contract, never from an unmerged parent, and leave all prior scenarios green for CHUNK-5.
- **Lane:** forge-codex-lane  ·  **Risk:** high · **Execution:** docker backend

## M3 — Publish one deterministic report

Project one canonical report model into byte-stable JSON and Markdown and publish the pair atomically.

### CHUNK-5: Render and atomically publish the canonical report
- **Goal:** Build one immutable `forgeboard.report.v1` model and deterministically render and atomically publish its JSON and paste-ready Markdown projections.
- **Milestone:** M3  ·  **Depends on:** `CHUNK-4`
- **Serves:** `FR-1`, `FR-2`, `FR-3`, `FR-4`, `FR-5`, `FR-6`, `FR-7`, `FR-8`, `FR-9`, `NFR-1`, `NFR-2`, `NFR-5`  ·  **Relevant ADRs:** `0002`, `0003`, `0005`
- **Touches:** `src/forgeboard_report/domain.py`, `src/forgeboard_report/render.py`, `src/forgeboard_report/publish.py`, `tests/features/report_output.feature`, `tests/steps/test_report_output_steps.py`, `tests/test_render.py`
- **Contract decisions:**
  - Assemble exactly one immutable Report; renderers may project it but may not recalculate or parse one another. JSON top-level field order is `schema_version`, `inputs`, `sources`, `metrics`, `dependency_audit`, `warnings`, `evidence`; `schema_version` is exactly `forgeboard.report.v1`.
  - Within `metrics`, emit `bounce_rate`, `judge_quality`, `blocked_work`, and `operator_intervention` in that order. Preserve numerator/denominator and sum/count alongside displayed values, raw contributions, unavailable status, warnings, and evidence references. Decimal display values are canonical JSON number lexemes rounded once to at most six fractional digits using half-even with trailing fractional zeroes removed; unavailable values are JSON null.
  - Use UTF-8, LF, one final newline, schema field order, and the Architecture sort keys for every list. Escape arbitrary source text safely. Exclude output path, current time, temp paths, pid, locale, timezone, hash iteration, and diagnostics from both artifacts.
  - Markdown section order is title/schema, Resolved inputs, Canonical metrics, Operator intervention, Dependency audit, Warnings and unclassified evidence, then Evidence index. It must show unavailable explicitly and carry the same values and stable ids as JSON; it is paste-ready and contains no generated-at clock.
  - Require a nonexistent destination whose parent already exists. Finish validation and both renders in memory, create a private sibling staging directory, write and flush exactly `report.json` and `report.md`, then rename the directory into place. On failure, best-effort remove only that private staging directory and never touch sources or create a plausible destination.
  - Use specific publication errors for write, flush, and rename failures. Existing destination is a usage error; renderer/schema defects are invalid-core errors. Boundary diagnostics are not report content.
- **Scenarios:**
  - Given one report containing all metric, dependency, warning, unavailable, and evidence variants, when rendered, then JSON has the exact v1 field order and Markdown has the fixed section order with matching values and stable ids.
  - Given repeating decimals, finite raw scores, a zero denominator, and an empty score set, when rendered, then half-even rounding occurs once at six places, trailing zeroes are removed, and unavailable values are explicit/null rather than zero.
  - Given identical reports with shuffled source insertion under different locale, timezone, and hash-seed conditions, when rendered repeatedly, then JSON and Markdown bytes are identical UTF-8/LF with one final newline.
  - Given a valid report and a new destination, when published, then the destination appears atomically and contains exactly complete `report.json` and `report.md` files while all input/planning bytes remain unchanged.
  - Given an existing destination or injected render, write, flush, or rename failure, when publication is attempted, then a specific error is raised, no plausible destination is emitted, and only the private staging directory is eligible for cleanup.
- **Out of scope:** CLI argument parsing/orchestration, source acquisition, metric changes, stdout multiplexing, two arbitrary output paths, editing retro/planning documents, and persistent caches or logs.
- **Done when:** `make check` is green; rendering, byte-determinism, escaping, atomic-success, and every injected-failure scenario pass; JSON and Markdown are proven projections of one model; the coverage floor holds and no signed input changes.
- **Integration gate:** Do not start until CHUNK-4 is merged to `main`; branch from that merged state, never stack on its open PR, and preserve all adapter and metric tests when handing a green main to CHUNK-6.
- **Lane:** forge-codex-lane  ·  **Risk:** med

## M4 — Deliver the bounded command

Wire the adapters, pure core, renderers, exit policy, mutation proof, compatibility proof, and performance gates into the console command.

### CHUNK-6: Wire the end-to-end report command and gates
- **Goal:** Deliver the signed console command by orchestrating validated inputs, read-only acquisition, normalization, pure metrics, rendering, and atomic publication with final mutation, compatibility, performance, and traceability proofs.
- **Milestone:** M4  ·  **Depends on:** `CHUNK-5`
- **Serves:** `FR-1`, `FR-2`, `FR-3`, `FR-4`, `FR-5`, `FR-6`, `FR-7`, `FR-8`, `FR-9`, `NFR-1`, `NFR-2`, `NFR-3`, `NFR-4`, `NFR-5`  ·  **Relevant ADRs:** `0001`, `0002`, `0003`, `0004`, `0005`, `0006`
- **Touches:** `pyproject.toml`, `src/forgeboard_report/cli.py`, `tests/features/report_command.feature`, `tests/steps/test_report_command_steps.py`, `tests/test_performance.py`, `tests/test_read_only.py`
- **Contract decisions:**
  - Expose exactly `forgeboard-report --board <slug> --graph <path> --from <aware-iso> --to <aware-iso> --operator <exact-id> [--operator ...] --output <new-directory>` through the existing package entry-point mechanism; fixture injection remains an application/test port, not a public flag.
  - Orchestrate in this order: validate request/destination, load/hash/validate graph, capture one stable Hermes snapshot, normalize/join/decode and extract parent PR refs, version-check and batch-fetch GitHub facts, calculate the complete report, render both artifacts in memory, then publish atomically. Do not create the requested output before every prior phase succeeds.
  - Translate specific errors once at the CLI boundary: usage/input to exit 2, unavailable/inconsistent source to 3, invalid core/schema to 4, and publication to 5. Diagnostics go to stderr, name the actionable source/id when safe, omit bodies/secrets, and never turn corruption into warnings or emit a partial aggregate.
  - The live path owns no auth and performs no board/GitHub mutations. Acceptance tests hash graph, database and sidecars, requirements, roadmap, decision log, and retro metrics before/after; the fake subprocess runner accepts only `gh --version` and query-only `gh api graphql` argument arrays.
  - Trace every FR to at least one named BDD scenario across the six feature files and keep the configured branch coverage at or above 85%. The console scenario must assert resolved inputs/fingerprints, representative metrics, dependency rows, warnings, and stable ids in both artifacts rather than merely checking file existence.
  - Use generated scale fixtures with 100 chunks, 300 runs, and 1,000 events to keep normalization plus metrics under one second on Python 3.12. Use an injected runner/monotonic clock, without real sleeps, to prove a 100-card live orchestration stays under 30 seconds when each bounded external response consumes at most five seconds.
  - Verify Hermes 0.19 fixture/public-JSON agreement and GitHub CLI 2.96+ response contracts in the final gate while ordinary tests remain executable with neither program installed. `make check` is the only release proof.
- **Scenarios:**
  - Given a valid recorded board, graph, exact operators, aware period, and fake merged/unmerged PR facts, when the public command runs, then exactly two complete artifacts contain resolved fingerprints, all canonical metrics, dependency findings, warnings, and traceable stable ids.
  - Given invalid input, an unknown/changing board, cyclic graph, unavailable/old GitHub CLI, malformed canonical evidence, or publication failure, when the command runs, then it exits 2, 3, 4, or 5 by boundary and never emits a plausible report directory.
  - Given before/after hashes and an observing fake command runner, when a successful and each failing command path run, then board/graph/planning inputs are byte-identical and every external operation is a bounded read-only query.
  - Given identical normalized inputs across shuffled records, locale, timezone, and hash-seed variations, when the command is repeated, then both artifact byte streams are identical.
  - Given the signed 100-chunk/300-run/1,000-event fixture and bounded fake external timings, when performance gates run on Python 3.12, then the pure core completes under one second and the modeled live command completes under 30 seconds.
- **Out of scope:** Additional flags or modes, multi-board aggregation, dashboard/service/scheduler work, caches, auth management, board or GitHub mutation, historical repair, board bootstrap, PR operations, and edits to signed/planning/retro source documents.
- **Done when:** `make check` is green with every FR mapped to BDD, coverage at or above 85%, all source compatibility/read-only/determinism/performance gates passing, exactly the signed console surface installed, and no new runtime dependency or semantic documentation change.
- **Integration gate:** Do not start until CHUNK-5 is merged to `main`; branch from that merged state, never from an unmerged parent, and merge only after the entire repository—including all five predecessor scenario suites—is green.
- **Lane:** forge-codex-lane  ·  **Risk:** med
