# forgeboard-report — agent context (single source of truth)

A Forge-managed project.

Hand-curated. Keep under ~120 lines; every line costs every session context.
(Research consensus: human-curated context helps; auto-generated bloat hurts.)

## Commands
- `make setup` — install deps + hooks · `make check` — THE green proof
- `make fmt` / `make lint` / `make test`

## Workflow (Forge)
Foundation docs: `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/adr/`,
`docs/ROADMAP.md`, chunk contracts in `docs/chunks/`, running notes in
`docs/decision-log.md`.

**If you were handed one chunk contract, implement exactly it and stop.** Do
not push, open a PR, or touch a kanban board — whoever invoked you owns those.

The ceremony skills (/scope, /architect, /roadmap, /start-chunk, /end-chunk,
/judge) are for an **interactive operator driving this repo directly**. They
are not instructions for a sub-agent working a contract: they describe the
calling agent's lifecycle, and following them from inside a worktree produces
duplicate pushes and double-completed cards. Measured 2026-07-28.

## Hard constraints (gates enforce these; listed here for orientation)
- No direct pushes to main; one chunk = one branch `chunk/<id>-<slug>` = one PR.
- `make check` green before any PR; never bypass hooks (`--no-verify` is a
  fireable offense for agents).
- New runtime dependency ⇒ new/updated ADR in the same branch.
- Scenarios (`tests/features/`) are the living spec — never weaken a Then-clause
  to make it pass; escalate instead.

## Conventions
- Python 3.12, pinned in `.python-version` so this machine and
  CI agree on the minor version rather than each resolving the newest they can
  find (measured: 3.14 local vs 3.12 in CI before the pin). Patch versions may
  still differ. uv-managed. src layout: `src/forgeboard_report/`.
- Commits: `type(<chunk-id>): imperative message`, granular.
- Errors: raise specific exceptions; no bare except; log at boundaries only.

## Gotchas
(append via /end-chunk doc reconciliation — newest first)
