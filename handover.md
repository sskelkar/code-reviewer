# Handover

## Session goal
Understand how the reviewer talks to Claude after the VM sandbox and tracing work, then diagnose a failed host-mode review of a pull request in a large production service. That review used the Claude provider with subscription auth and produced no findings.

## How the reviewer calls Claude
- Each model call runs `claude -p` as a headless subprocess (`backends/claude.py`) with a JSON schema, read-only tools and isolation flags. The prompt goes in on stdin and the JSON envelope is parsed and validated.
- Subscription mode drops `--max-budget-usd`. The harness enforces its own token allowances instead: `call_budget_tokens` (200,000 per call) and `run_budget_tokens` (2,000,000 per run). Both count cache reads.

## What went wrong in the production-service run
- The criteria resolver call succeeded.
- The reviewer call also succeeded and returned valid JSON with one low-severity finding.
- It used about 205k tokens, mostly cache reads of a large prompt re-read on each tool turn. That exceeded the 200k per-call allowance by about 3%.
- The allowance check runs after the process exits, marks the call non-retryable, and the pass fails. The report then shows "0 findings", which hides the fact that a usable answer was discarded.

## Observations
- **Criteria size drives cost:** the service has many criteria files, which resolved to about 140 rules and a criteria block of about 114 KB. The diff was only about 10 KB. `criteria_max_chars` (200,000) was never reached, so it did not help.
- **Allowance is not a safeguard:** it cannot interrupt a call, so the tokens were already spent when the result was thrown away.
- **Possible follow-ups (not done):**
  - Count cache reads at a discount, or treat an over-allowance call whose output validates as a warning.
  - Say in the report when a completed answer was discarded.
- **Criteria sources:** defaults come from `criteria/`. Project rules are read from the base branch. A resolver model picks files from a deterministic candidate list, and scoped files are then added deterministically.
- **`criteria_manifest`:** this is a host-only JSON file that replaces the resolver's answer. It is not a criteria mode. Valid `criteria_mode` values are `merge`, `project_only` and `defaults_only`.

## Change made
- Added `--criteria {merge,project_only,defaults_only}` in `review_options.py`. It overrides `criteria_mode` for one run, is listed in the banner, and has a row in `docs/usage.md`.
- `defaults_only` skips project criteria resolution, so the resolver call is also skipped.
- Nothing is committed.

## Test status
- Baseline (without the change): all 500 tests passed.
- With the change, one full run had 2 errors, in the VM smoke and live-progress tests. Those tests pass when run alone, and the banner/option/criteria tests pass (57 of 57). They look like load flakiness, but the full suite has not been re-run since.

## Open items
1. Re-run the full suite once to confirm it is clean.
2. Add a unit test that `--criteria` overrides `criteria_mode`.
3. Decide whether to change the over-allowance behavior described above.
