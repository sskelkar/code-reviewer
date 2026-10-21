# Handover

For an AI agent (or a person) taking over. Read [`AGENTS.md`](../AGENTS.md) first for the working rules. Design reasoning and the task checklist are in [`proposal.md`](proposal.md). The [README](../README.md) gives the quick start and both high-level flows; [usage](usage.md), [architecture](architecture.md) and [development](development.md) hold the detailed references.

**User-run execution check (2026-10-21):** after the stdin fix, a user-run one-round persona review completed all four slots with no retries, failed calls, model-reply validation errors or unknown-cost calls. The report contained eleven consolidated findings and two annotated conflict groups. Reported cost was about $0.903: criteria $0.029, Comparator $0.013, Reviewers $0.499 and Verifier $0.361. Criteria usage was consistent with the earlier successful run; the timeout warning did not justify raising its allocation. The Verifier used about 90% of its $0.40 pool, so watch its headroom in further runs; ratios remain unchanged. This is execution/accounting evidence only, not a finding-quality assessment or measured latency comparison. Only user-authorized report accounting/completion sections were assessed; no private code, identifiers or prompt content were copied into this repository. Codex made no paid model calls. The user authorized committing and pushing the transport/reporting fixes with these documentation updates.

**Persona stdin hang fix (2026-10-21, after integration):** a user-authorized accounting inspection of a failed live run showed a successful inexpensive Resolver, a Classifier timeout at 600 seconds, and unknown-cost reservations consuming the criteria pool; it did not establish actual overspending. Reproduced a transport defect with a synthetic slow reader and a large Unicode prompt: after the first 100 ms timeout, repeated `communicate(input=None)` calls stopped sending pending stdin bytes. Persona transport now supplies the complete UTF-8 prompt through an anonymous temporary stdin file before polling; cancellation/process-group cleanup remains. A regression fails before the fix and passes afterward. Corrected shared usage reporting so a timed-out call without cost marks `cost_known_for_all_calls` false even when no tokens are reported. No private code or prompt content was used in the fix or fixtures; no paid calls were run by Codex. These fixes are included in this commit; restart an existing REPL to load them.

Validation for the stdin/cost-certainty fix: **296 tests pass** on Python 3.9.6 and 3.13; the single-agent synthetic baseline still matches, and Markdown links/anchors/fences and whitespace checks pass.

**Integration status (2026-10-21):** the user authorized committing and merging the accumulated changes. The checkout was already on `main`, so this batch is committed directly there; historical statements below about uncommitted work describe the earlier handover state. The batch includes the persona runtime/defaults, configurable passes, parallel rounds and budget pools, isolated fetch refs, and documentation reorganization. No live model review was performed.

**Parallel persona rounds and budget pools (2026-10-21, current contract):** implemented the user's approved round-barrier design, not independent persona queues. Pin head/base and export one checkout; gather/classify criteria and freeze prompts; run up to four Reviewers concurrently within each round; wait for every round outcome; validate/consolidate serially in fixed persona/pass order; then start the next round. `persona_parallel = 4`, persona-only `--parallel N` accepts 1–4; `--parallel 1` serializes reviews but retains barriers. No cache-warmup model calls. Single-agent scheduling/caller remain unchanged.

The dollar cap now allocates weights 3/3/4 out of 30 to criteria (Resolver + Classifier), Comparator and Verifier; divide the remaining 20/30 equally across personas. At $3: $0.30/$0.30/$0.40 and $0.50 each. Shared pools round down to cents; persona pennies are distributed in stable order, with at most one cent difference. Every attempt/retry reserves atomically then reconciles reported cost; unknown costs consume the allowance conservatively and increment a counter. No borrowing or automatic budget increase. Global tokens count reported input/output/cache writes; in-flight calls can overshoot. See `budget_pools` in stats/report and startup/dry-run allocation output.

Each persona has separate Caller/request logs. Call statistics writes are locked; the parent reads them after barriers/shutdown. Budget/failure streaks are per persona; exhaustion skips only that persona's future slots. Criteria exhaustion falls back with warnings. Comparator exhaustion stops new rounds, preserving raw unconsolidated replies without votes; slots are failed/unmerged/skipped honestly. Verifier exhaustion leaves flags pending. `incomplete` indicates partial persona completion and returns nonzero from the CLI. Global failures/cancellation stop the run. `runner.py` polls cancellation/timeouts, terminates owned subprocess groups on POSIX and waits before checkout cleanup. Partial/unknown usage is not represented as zero-cost certainty.

New implementation files: `flows/agent_personas/budget.py` and `runner.py`; `flow.py` owns the round barrier and single writer. Model-facing prompts/schema/defaults are unchanged. Real parallel reviews and pool sizing still need user/Claude validation; no production material, saved runs or paid calls were accessed.


Validation: **294 offline tests pass** on Python 3.9.6 and 3.13. Synthetic tests prove overlapping calls, concurrency limits, round barriers, fixed merge order, pool isolation and POSIX subprocess-group cancellation. The single-agent synthetic baseline still matches; all 37 Markdown files pass local link/anchor/fence checks. No live reviews, commits or pushes were performed. Restart an existing REPL before testing the new options.

**Fetch ref isolation (2026-10-21):** a reported `cannot lock ref ... is at ... but expected ...` is consistent with a concurrent ref update, not a persistent base-commit pin. Found that explicit fetch destinations still permit configured remote-tracking updates. Added `--refmap= --no-tags` so review fetches update only explicit private refs. A synthetic real-Git regression failed before the fix and passed after it, with a tracking-ref lock held in normal and custom-mapped bare clones; repeated fetches observe advancing base tips while leaving user refs, HEAD/worktree and new remote tags unchanged. Per-run pinning and merge-base-to-head diff semantics remain. No production clone or saved runs were accessed.

Validation after the fetch fix: **287 tests pass** on Python 3.9.6 and 3.13; documentation links and whitespace checks pass. No live reviews, paid model calls, commits or pushes were performed.

**Configurable persona passes (2026-10-21, current contract):** the user replaced the fixed-three restriction with a positive, configurable count. `--mode agent-personas --passes N` schedules N passes per persona (4 × N total); `persona_passes = 3` in TOML supplies the default. `--passes` overrides only the selected flow, preserving single-agent min/max semantics. No quiet-pass stopping, independence, budgets/retries, advisory verification and source-persona agreement remain unchanged. Progress, dry runs, reports and saved configuration use the chosen count; one completed pass per persona is unrated. This supersedes the earlier rejection described below. Restart any running REPL to load the change. No model-facing prompt content changed.

Validation for this change: **286 tests pass** on Python 3.9.6 and 3.13. New coverage exercises 1/2/5 rounds, dynamic progress/completion totals, source agreement/unrated output, budget interruption, invalid config values/types, CLI/REPL overrides and config immutability. The real editable-install TOML loader accepts the setting. Single-agent synthetic baseline behavior still matches; all 77 source modules parse/import and documentation links pass. No paid calls, production access, commits or pushes were used.


**Documentation update (2026-10-21):** shortened the README from 693 to 167 lines, retaining separate single-agent/persona diagrams. Commands/settings/report guidance moved to `docs/usage.md`, module maps/detailed diagrams to `docs/architecture.md`, and contributor/debugging/replay material to `docs/development.md`. Removed repeated explanations and updated navigation. This is a documentation-only change; runtime code, prompts, pilot status and priorities are unchanged. The changes remain uncommitted.

Validation: all 280 offline tests pass on Python 3.9.6 and 3.13; local links/anchors/fences pass across 37 Markdown files. No live reviews, paid calls or production access were used.

**Historical REPL traceback (2026-10-21, before configurable passes):** a user saw missing `prompts/resolver.system.md` with `--passes 1 --mode agent-personas`. Fresh CLI and REPL subprocesses from the existing editable `.venv` reject that combination cleanly before preparation. Current `engine.py` is 34 lines; the traceback references its pre-refactor constructor at line 50 and the old root template loader. This strongly suggests an already-running REPL retained old Python modules while files/assets moved; the original process was not inspected. Restarting remains necessary for stale modules; the subsequent configurable-pass change below supersedes the old rejection. The initial frontend regression guarded rejection before Engine construction and a usage troubleshooting note. No runtime/model-facing changes or real reviews were needed.

After this regression addition, all **281 tests pass** on Python 3.9.6 and 3.13; documentation links and whitespace checks pass. Changes remain uncommitted.

**State (2026-10-20):** the structural refactor is pushed as `da34aaf`. The criteria and optional persona runtime are now implemented in the working tree and remain uncommitted. `--mode single-agent|agent-personas` selects one flow, with single-agent still the default. Persona mode is covered offline; no live persona review or model-quality comparison has run. Its report includes per-persona agreement, every assigned criterion, scheduling completeness and conflict annotations.

`Engine` selects/delegates. Each flow owns orchestration, role contracts, result/master and presentation. Shared mechanics live in `shared/`; `presentation.py` dispatches CLI/REPL/posting output. Legacy root imports remain bridges. Single-agent prompts/schemas/defaults retain their original bytes and GEN IDs; its synthetic before/after behavior still matches. No private saved runs, production access, paid model calls, new commits or pushes were used for persona implementation.

## Since the previous Claude handover

The structural refactor was finished, audited, committed and pushed as `da34aaf` at the user's request. Everything below was then added locally and is **not in that pushed commit**. Continue in this working tree; do not reset it or assume a fresh clone contains persona mode.

1. Defined thirteen canonical persona rules and fourteen static assignments. The ten existing concerns are retained; additions cover entry-point/artifact wiring, test discovery/failure propagation, and query/index compatibility. Shared `COR-002` has one definition. Single-agent defaults and IDs remain unchanged.
2. Implemented the complete optional persona flow: project-rule classification with coverage/fallback, frozen persona prompts, configured round-robin slots (twelve by default), one master with source-persona agreement, Comparator categories/conflict flags, and conflict-only advisory verification. This is no longer a proposed/stub flow.
3. Connected CLI, REPL, manual labels/drop, summary/post formatting, dry runs and selected-mode doctor. Added exact request/retry logging, per-attempt shared-budget checks and a visible prompt-size limit. Comparator replay remains single-agent-only; persona runs are explicitly rejected before calls.
4. Reviewed the code and docs again before this handover. Fixed cancellation during consolidation so an incomplete slot cannot leave votes/conflicts, fixed the scheduled-pass Markdown table, and corrected stale documentation and the persona diagram's optional calls/verification loop. Synthetic regression tests cover the fixes.

## Claude's next session: controlled live comparison

**Next action is a user-authorized local pilot, not another feature build.** Only Claude and the user may inspect the production clone, PR, prompts and saved findings. Codex has not accessed them. Nothing in this handover independently authorizes paid calls or production access; follow the user's instructions in the next session.

When the user asks to run the pilot, use the editable installation from this working tree. In the reviewed project's local clone, select persona mode explicitly:

```bash
reviewer review <PR> --mode agent-personas --model haiku --dry-run
reviewer review <PR> --mode agent-personas --model haiku
```

Replace `<PR>` with the user's PR reference. Add `--passes N` to choose passes per persona (4 × N total), or omit it to use `persona_passes` from TOML (default 3). The dry run has defaults only and does **not** validate actual project-rule discovery/classification. Use the existing approved budget/settings; if they stop the pilot early, inspect the completion counts and discuss an increase with the user rather than silently raising limits. Do not post a comment unless the user confirms the preview.

Inspect these locally with the user:

- `input/criteria_index.json`, `input/classification.json` (when project rules exist), and `input/personas/`: accepted rules, their coverage and whether the assignments make sense. Coverage validation cannot establish semantic routing quality.
- `stats.json`, `pass-*/slot.json`, `report.md`: 4 × N scheduled slots (twelve by default), actual completions per persona, failures/warnings, dollar/token use and honest agreement denominators.
- `master.json` and `verification/` (if conflicts were flagged): duplicate quality, distinct opposing claims, grounded annotations and unresolved groups. No flags legitimately means no Verifier call; it does not prove there were no conflicts.
- Finding usefulness: real defects, false positives, missing context and duplicates. A grounded quote or high agreement is not proof of correctness; annotations do not suppress even refuted findings.

Compare the same pinned PR head/base with single-agent Haiku and single-agent Sonnet, using existing runs only if they match the pinned commits/settings. `--model` changes **all roles**, not just the Reviewer. Keep production evidence outside this repository. Report only generic implementation lessons here, with no company/project/PR identifiers or copied findings.

The offline audit supports proceeding to this controlled pilot. New role/schema behavior against the real CLI, model judgment quality, semantic classification, and the cost of completing the configured slots plus verification still need live evidence. The next separate feature after that comparison remains the **project context pack**, followed by possible broader verification. Evals stay parked; single-agent parallelism and hardening are later work.

## The eval harness is on the back burner

An offline eval harness (`python -m code_reviewer.evals`, the rubric `evals/RUBRIC.md`, eight public cases in `evals/`) was built on 2026-10-19 and then **deliberately set aside**: the user decided it was a distraction. No model was ever run through it and no quality result exists. **Do not extend it, run it, or build on it.** It stays in the repository only so it can be revived later (reasons and revival notes: proposal section 9, "Parked"). It also cannot test project-rule discovery, because it uses default criteria only.

## Direction

Make the agent useful on real pull requests and compare strategies by hand on the same PRs: single-agent Haiku, single-agent Sonnet, then optional persona Haiku. The user agreed the design in **proposal section 12**, then requested the structural refactor using the names **single-agent** and **agent-personas**. The [README persona flow](../README.md#agent-personas) describes the implemented behavior. The final offline code/docs audit is complete; authorized manual comparisons on real PRs are next. The project context pack remains the next separate feature. Single-agent model inputs are unchanged and no live persona calls were made.

Implemented persona contract:
- Optional mode; single-agent review stays the default. Four personas: correctness, code quality, test quality, production safety.
- **N scheduled passes per persona**, default three; `persona_passes` in TOML or `--passes N` for a run. There are 4 × N slots in sequential round-robin. No per-persona convergence or "eligible third pass" condition. Normal retries, failures, budget/cancellation limits remain; incomplete work is visible. Budget can be raised by the user for verification, never silently.
- Default assignments are static in this repo (overlap allowed, one canonical ID per rule). Distribute **100% of gathered/accepted project criteria** to one or more personas, preserving text/source and project precedence. Validate coverage, use all-persona fallbacks with warnings for unresolved assignments. Do not supply all project rules to every persona by default.
- Save the complete rendered system/user prompts once per persona, plus per-pass hash references and settings. The report lists all assigned criterion names/sources per persona; no separate verbose routing report is required.
- Any persona may report an evidenced correctness/safety defect outside its focus. The Comparator proposes the category; recorded discovering persona and agreement do not change.
- One shared Comparator/master list. In persona mode the Comparator sees the complete master and incoming batch, proposes duplicate mappings, categories and possible conflict groups; the harness validates and records them in a special master-list section. No separate Conflict Detector component/call. Conflicting factual claims remain separate; repeated flags do not create repeated verification jobs.
- After reviewing, a targeted Verifier investigates only distinct flagged groups, using fresh Claude sessions with Read/Grep/Glob in the same checkout. **Annotate only**: evidence, support/refutation, context, tradeoffs or unresolved status. No persona hierarchy, automatic suppression, rewriting or change to sighting counts. No conflicts means no Verifier calls. Cancellation/terminal failure stops all further calls; failures/unavailable budget leave unresolved groups visible.
- Confidence is per discovering persona; categories do not transfer votes and there is no manufactured overall confidence tier. Keep all Reviewer prompts independent and frozen; never feed conflict results back into passes.

The exact static allocation and persona-prefixed IDs are tabulated in proposal §12; current `GEN-*` IDs and default rule wording have not changed. `--mode agent-personas` selects the implemented flow; `--passes N` sets the scheduled count per persona, default three. The [persona criteria guide](persona-criteria.md) now indexes thirteen canonical rules under `flows/agent_personas/criteria/`, four focus/assignment definitions in `personas.json` and `review-standard.md`. All ten existing concerns are retained; `COR-003` adds entry-point/registration/artifact wiring and `TEST-003` adds test discovery/failure propagation. `SAFE-005` adds query/index compatibility to production safety, using repository schemas/migrations without claiming actual planner choices, deployed index state or measured performance. `COR-002` is authored once and assigned to correctness and safety. Asset loading, project-rule distribution, frozen prompts and the persona workflow are implemented with synthetic tests. Next compare real reviews manually through Claude when authorized. After it: project context pack, then consider broader per-finding verification; parallel single-agent execution, governance/security hardening and cost tuning remain later. The eval harness stays parked.

## Refactor baseline verification (before persona runtime)

- All **244 tests pass** on Python 3.9.6 and 3.13, including eleven new flow-selection/isolation/compatibility tests. No tests call a real model.
- Audit fixed positional `Config` constructor compatibility, retained the `Engine` public export, corrected stale module references/behavior descriptions and separated the two README flows. All 68 Python files parse with Python 3.9 syntax and implementation modules import. All 19 Markdown files passed local link/anchor/fence checks.
- A synthetic before/after run matched exact model inputs/schemas/settings, criteria, merged findings, pass records/completions/stopping, summary and normalized report.
- All nine prompt/schema files and the active criteria file are byte-identical to their pre-refactor versions, guarded by `tests/fixtures/single_agent_asset_hashes.json`.
- CLI/REPL mode selection, config override precedence, unavailable-mode errors before preparation/assets/model calls, shared dependency boundaries and compatibility imports are covered.
- `git diff --check`, edited-document links/anchors and Markdown fences passed. No paid calls or production access were needed. The audited baseline was committed and pushed at the user's explicit request.
- The parked eval tool remains parked; only its dependency-fingerprint traversal and matching test were adjusted to cover relocated Python subpackages.

## Persona criteria asset verification

Three new offline tests check complete static allocation, canonical sharing, reporting sections and shared-parser loading/rendering/disabling, including exclusive production-safety assignment of `SAFE-005`. Runtime tests now also exercise project-rule routing and the review loop; neither test set establishes semantic classification/review quality. Existing single-agent assets remain byte-identical. The criteria-only step passed 247 tests on Python 3.9.6 and 3.13. Current full-suite results follow below. No paid calls or production access were used. Criteria assets, tests and accompanying documentation remain uncommitted for review.

## Runtime wiring and limits

- `flows/agent_personas/flow.py` is the composition root; `criteria.py` owns static allocation and coverage checks, `roles.py` model calls/frozen inputs, `contracts.py` untrusted output validation, `master.py` source sightings/conflicts, `caller.py` per-attempt budgets/request logs, `report.py` presentation and `models.py` results.
- `model_verifier = "haiku"`; classification reuses `model_resolver`. `--model` changes every role. `--passes N` selects maximum attempts in single-agent or scheduled passes per persona in persona mode. Persona mode otherwise uses `persona_passes = 3`, ignoring single-agent min/max/quiet settings. Failures/retries do not create extra replacement slots.
- Persona budgets are checked before every invocation/retry, with call dollars capped to remaining cents; no automatic increases or transfers; Verifier has its own proportional pool. `persona_prompt_max_chars = 500000` stops oversized inputs visibly rather than truncating them. Incomplete work stays in reports.
- Every actual request/retry is logged in `*.raw.requests.json`; `invoked` is false for a blocked oversized retry. Per-persona frozen prompts/settings live under `input/personas/`, and pass slots reference their hashes. Model outputs and all artifacts remain outside the read-only checkout.
- Comparator sees full master/batch text and proposes mappings/categories/conflicts. Canonical aliases are checked before writes and duplicate groups are verified once. Verification receives all accepted criteria (not just citations), the diff and same head checkout. Non-unresolved annotations require quotes grounded at their head-file line. Removed-only evidence may remain unresolved. Annotations never remove findings/change severity/votes; validated category corrections are allowed.
- Doctor checks the selected flow's assets. The active Comparator replay command explicitly rejects persona run directories before model calls; it remains single-agent-only. Dry runs produce four defaults-only persona prompts and no model/version calls/checkout. CLI/REPL/show/summary/post format dispatch to the owning renderer, preserving manual labels/drop behavior. The new flow has no imports of single-agent implementation code.
- Shared file grounding now handles malformed/unreadable model paths as ungrounded evidence instead of raising filesystem exceptions.

## Persona runtime verification

- **280 tests pass** on Python 3.9.6 and 3.13, including thirty-three synthetic persona-flow tests, three static asset tests and the existing single-agent regressions. Tests never call a real model.
- Single-agent before/after comparison still matches exact model inputs/schemas/settings, criteria, findings, pass accounting/stopping, normalized report and summary. Its nine template/schema files and default criteria retain the pinned SHA-256 hashes.
- All 77 Python modules parse with Python 3.9 syntax and import. All persona schema JSON loads. All 34 Markdown files pass local links/anchors/fence checks; tracked and untracked-file whitespace checks pass.
- Covered: round-robin twelve slots despite quiet passes; frozen prompts/source isolation; project routing/retries/fallback; independent agreement and unrated/filtered views; full-master and within-batch conflicts; canonical remapping/dedup; invalid/rejected conflict refs; advisory grounded verification; cancellation, terminal failures, dollar/token/prompt limits; malformed model paths; selected-mode doctor; CLI/REPL/manual marks/drop/post formatting; no-call dry runs; single-agent-only replay guard.
- Final audit additionally covers interruption while saving consolidation or emitting completion: only the interrupted slot's new claims, votes and conflict flags are rolled back. Earlier completed slots remain. The scheduled-pass report table is one contiguous Markdown table, including when an error spans lines. README diagrams were checked against control flow; local Markdown checks validate links/anchors/fences, not rendered Mermaid layout.
- No live persona quality evidence exists. Invalid model citations do not drop supported findings; failed verification never removes/refutes a claim by default. Real manual comparisons require user authorization and stay with Claude/the user for production material.
- All persona criteria/runtime/docs changes remain uncommitted. No paid reviews, private saved-run reads, production access or eval experiments occurred.

## Constraints from the user

- Commit or push **only when asked**; leave changes in the working tree for the user to review.
- **Only Claude may read production material.** Another agent (for example Codex) must not read the user's production clone, diffs, saved findings or private cases, or ask for them to be pasted.
- Never put company or project identifiers (repository names, PR numbers, ticket keys, run directory names, code from saved runs) into this repository.
- Agree a design with the user before building it. Keep documentation honest about what did not work.
- Do not run real reviews or `doctor --live` unprompted: they use the user's Claude Code login and cost money.

## What exists

A Python package (`code_reviewer`, command `reviewer`) that reviews a GitHub PR with several independent Claude Code headless passes. The README has separate single-agent and persona flow diagrams. The default single-agent path works as follows; the implemented persona variant is described above:

1. Pin the PR, redact emails in the PR text, load the diff (files matching `exclude_patterns` or `extra_exclude_patterns` are left out but named), export the PR head to a temporary read-only checkout.
2. Build the criteria: ten defaults (`GEN-001` to `GEN-010`, `src/code_reviewer/flows/single_agent/criteria/general.md`), plus project rules from the root `AGENTS.md` (chosen by a Resolver model, read from the base branch), plus nested `AGENTS.md` and `.claude/rules` files that cover the changed paths (added by location, no model call).
3. Loop (min 3, max 6 passes, stop after 2 quiet passes): a fresh **Reviewer** session with Read, Grep and Glob over the checkout, with a byte-identical prompt every pass; the harness drops findings in excluded files, checks the quoted evidence, repairs rule IDs and applies the self-check; a **Comparator** model maps findings onto the master list; the harness validates and merges.
4. Output: a shareable summary (also the PR comment, posted only after confirmation) and `report.md`, naming each finding's criterion by name and source, never by its internal ID; confidence = share of passes that found a finding; tokens and cost; every input and raw reply saved under `~/.code-reviewer/runs/<run>/`.

Interfaces: the interactive prompt `reviewer`, the one-shot `reviewer review <pr>`, `reviewer doctor [--live]`, and the developer tool `python -m code_reviewer.replay <run dir>` (re-runs only the Comparator on saved findings).

## Decisions to keep

| Decision | Why |
|---|---|
| Models propose, code decides | Every model answer is validated; nothing the model says is written or posted unchecked |
| Passes are independent and identical | Agreement only means something if passes cannot influence each other; no pass number in the prompt, so they also share the prompt cache |
| Claude Code headless with the user's login | Only an enterprise login is available; `--bare` is unusable with it |
| Haiku for all roles by default | Cheap. Sonnet merges more consistently but more aggressively, about 15 times the comparator cost |
| Criteria read from the base branch | A PR cannot edit the rules it is judged by |
| A bad rule ID never drops a finding | An early version did and silently lost a third of one PR's findings |
| Planning documents (PRDs, plans) stay out of the prompt | They are the author's claims, may drift from the code, can suppress true findings or create drift noise, and cost tokens; listed per project in `extra_exclude_patterns` |
| Show all confidence tiers for now | Better to see Low findings while learning what is useful |
| Security loosened for v0 | Controlled learning environment; backlog in the proposal checklist |

## What the real runs taught us

1. A first prompt made every pass report one finding, so everything scored Low. The prompt now asks for a thorough review (findings per pass rose from about 1 to 2-7).
2. Two defects hid agreement on a larger PR: findings dropped for badly written rule IDs, and one issue split into three findings. Both fixed.
3. **Agreement is not correctness.** A finding raised by 4 of 6 passes was wrong: a project rule existed in the repo but in no criteria file, and no pass searched for it. This is why the project context pack remains next and broader verification may follow. Persona mode now has a conflict-only advisory Verifier; repeated wrong claims without a flagged conflict still receive no verification. Neither Comparator establishes truth; persona mode additionally categorizes and flags possible conflicts.
4. Cost: on a 2,100-line diff one pass costs about $0.19 for the first (cache write) and $0.05-0.07 after, driven by an 85k-token prompt, cache reads each turn and about 12k thinking tokens of output.
5. The saved raw output does not record which tools a pass called.

## Notes on next steps (details: proposal section 12)

- **Personas:** the initial workflow is implemented and audited as specified in proposal §12; make authorized manual comparisons next. Do not return to the superseded three-perspective/all-project-rules/quiet-pass scheduling plan.
- **Parallel passes:** persona rounds are now implemented (default 4, no extra warmup call); single-agent parallelism remains deferred. Do not reintroduce independent queues or round-to-round feedback. See the current contract above.
- **Cost:** try `claude --effort` (not used yet) and a smaller criteria cap; compare findings by hand.
- **Reviewer prompt:** before reporting a finding that depends on an assumption, check the comment above the code, its tests and the repo docs; unverified assumptions go to `notes`.
- **Label command** (`reviewer label <run dir> <id> right|wrong|duplicate --why`), recording tool calls per pass (`stream-json`), and an option to limit unscoped `.claude/rules` files.
- Open checks against the real CLI: `--max-budget-usd` overrun behavior and clean timeout kill (proposal section 9).

## Practical notes

- **Start reading here for the pilot:** `src/code_reviewer/flows/agent_personas/flow.py`, then its `roles.py`, `contracts.py`, `master.py` and `caller.py`. The baseline is `flows/single_agent/flow.py`; its roles, passes, master/result, presentation and assets stay together. `engine.py` only selects/delegates. Shared discovery/transport/validation/storage belong in `shared/`; do not import flow implementations from there. Root modules such as `git_ops.py` and `master.py` are compatibility bridges. `models.RunResult` is a compatibility export of the single-agent result.

- A saved run (`~/.code-reviewer/runs/<run>/`) holds `input/` (exact prompts, criteria block and index, diff, PR text), `pass-NN/` (`reviewer.raw.json` with each attempt's usage, `findings.json`, `comparator.raw.json`, `mapping.json`), `master.json`, `stats.json`, `labels.json`, `report.md`, `summary.md`.
- The replay tool tests Comparator changes cheaply (a few cents with Haiku); Reviewer prompt changes need a real run.
- Claude CLI facts (Claude Code 2.1.x): Reviewer flags `--safe-mode --restricted --tools "Read,Grep,Glob" --strict-mcp-config --disable-slash-commands --permission-prompts none --no-session-persistence --output-format json --json-schema <schema> --max-budget-usd <n> --append-system-prompt <text>`, prompt on stdin; no `--max-turns`; `--json-schema` adds an internal `StructuredOutput` tool and the answer is in `structured_output`.
- Tests: `tests/helpers.py` has `FakeRunner`, throwaway git repos (with `gc.auto=0`) and `finding(...)`; a real reply is kept as `tests/fixtures/live_envelope_haiku.json`.
- Docs that go stale most easily: README (quick start/flows), `docs/usage.md` (options/settings/limits), `docs/architecture.md` (code map/diagrams), `docs/development.md` (checks/debugging), proposal sections 4, 5, 9 and 10 and its checklist, `prompts/README.md`, and the explainer (call-stack tree, code excerpts).
