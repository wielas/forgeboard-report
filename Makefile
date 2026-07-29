# Single command vocabulary. `make check` is THE definition of green (ADR-0003).
.PHONY: setup fmt lint test check hooks protect

setup:            ## install deps + git hooks
	uv sync
	@git rev-parse --git-dir >/dev/null 2>&1 || { \
	  echo "not a git repo — run 'git init' first; hooks cannot be installed" >&2; exit 1; }
	uv run lefthook install
	@test -f "$$(git rev-parse --git-path hooks/pre-push)" || { \
	  echo "lefthook install reported success but no pre-push hook exists" >&2; exit 1; }

fmt:              ## auto-format
	uv run ruff format .
	uv run ruff check --fix .

# --no-cache is load-bearing, not tidiness. `make check` is the verdict the
# lane trusts instead of Codex's word (forge-lane §5), and a verdict read from
# a cache is not a verdict. Measured 2026-07-28 in a lane worktree: `ruff check`
# answered "All checks passed!", the same bytes in a cold clone and in CI
# answered "Found 1 error", and deleting .ruff_cache in that very directory
# flipped it to the error. A chunk shipped green and CI caught it — which is
# the wrong way round, because §5 exists so CI never has to.
# CI is always cold, so this is also what makes local and CI the same command
# in fact and not just in spelling. Ruff is milliseconds here; the cache buys
# nothing worth a false green. `fmt` keeps its cache — it mutates, it does not
# judge.
lint:             ## no mutations — and no cached verdicts
	uv run ruff format --check --no-cache .
	uv run ruff check --no-cache .

test:             ## full suite incl. BDD features, coverage floor enforced
	uv run pytest

check: lint test  ## THE green proof — CI runs exactly this
	@echo "FORGE CHECK: GREEN"

# CI is not a merge gate on its own — without branch protection a red PR can be
# merged, and the local pre-push guard is bypassable (`--no-verify`, or
# `git push origin HEAD:main` from a feature branch, which lefthook cannot see:
# it forwards the hook's args but NOT git's stdin, where the destination refs
# are). The only gate that holds off the host is this one. Run it once, after
# the GitHub repo exists.
# required_approving_review_count is 0 ON PURPOSE. The lane pushes as the
# operator's own GitHub account, and GitHub forbids approving your own PR — so
# a count of 1 makes every agent-authored PR permanently unmergeable, by the
# only human who could approve it. Measured 2026-07-28: "the base branch policy
# prohibits the merge", with enforce_admins:true blocking the override too.
# Human review still happens — it is the /judge step (ADR-0007 tier 2), off
# GitHub. What this gate must enforce is what a human cannot fake: a PR exists,
# CI is green on the merge commit, and main is never force-pushed or deleted.
protect:          ## make CI a real merge gate (run once, needs admin on the repo)
	@repo=$$(gh repo view --json nameWithOwner -q .nameWithOwner) || \
	  { echo "no GitHub repo yet — 'gh repo create' first" >&2; exit 1; }; \
	printf '%s' '{"required_status_checks":{"strict":true,"contexts":["check"]},' \
	  '"enforce_admins":true,' \
	  '"required_pull_request_reviews":{"required_approving_review_count":0},' \
	  '"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}' \
	  | gh api -X PUT "repos/$$repo/branches/main/protection" --input - >/dev/null || \
	  { echo "branch protection FAILED (admin rights? private repo on a free plan?)" >&2; exit 1; }; \
	gh api "repos/$$repo/branches/main/protection" >/dev/null && \
	  echo "branch protection active on $$repo main: PR + green 'check' required, no force-push, no deletion"
