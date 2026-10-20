# Handover

For an AI agent (or a person) taking over. Read [`AGENTS.md`](../AGENTS.md) first for the working rules. Design reasoning, the task checklist and the design notes for the next steps are in [`proposal.md`](proposal.md); usage, flow diagrams and the code map are in the [README](../README.md).

**State (2026-10-20):** the approved structural refactor is implemented and audited, along with prior persona-design documentation and the inactive criteria draft. `Engine` is now a selection facade; current flow code and active assets live in `flows/single_agent/`, reusable mechanics in `shared/`. The CLI/REPL accept `--mode single-agent|agent-personas`, overriding `review_mode = "single-agent"`. The persona flow is reserved and rejects execution before preparation/assets/model calls. No persona review behavior has been implemented. Legacy import paths remain bridges; edit the canonical flow/shared files.

A synthetic FakeRunner before/after comparison matched exact model inputs/schemas/settings, criteria, findings, pass counts/stopping, summary and normalized report. Active template/schema/criteria bytes and existing GEN IDs are unchanged. No real model calls or production access were used for the refactor or audit. The user requested committing and pushing this reviewed baseline before persona implementation. See the verification note below for the offline suite.

## The eval harness is on the back burner

An offline eval harness (`python -m code_reviewer.evals`, the rubric `evals/RUBRIC.md`, eight public cases in `evals/`) was built on 2026-10-19 and then **deliberately set aside**: the user decided it was a distraction. No model was ever run through it and no quality result exists. **Do not extend it, run it, or build on it.** It stays in the repository only so it can be revived later (reasons and revival notes: proposal section 9, "Parked"). It also cannot test project-rule discovery, because it uses default criteria only.

## Direction

Make the agent useful on real pull requests and compare strategies by hand on the same PRs: single-agent Haiku, single-agent Sonnet, then optional persona Haiku. The user agreed the design in **proposal section 12**, then requested the structural refactor using the names **single-agent** and **agent-personas**. The [README proposed persona flow](../README.md#proposed-persona-flow-not-implemented) is the immediate review artifact. The flow separation is implemented; persona scheduling/routing/conflict verification remain future work. No new model-facing prompt content, model calls or production access occurred.

Agreed next feature:
- Optional mode; single-agent review stays the default. Four personas: correctness, code quality, test quality, production safety.
- **Three fixed passes per persona**, twelve scheduled slots, sequential round-robin. No per-persona convergence or "eligible third pass" condition. Normal retries, failures, budget/cancellation limits remain; incomplete work is visible. Budget can be raised by the user for verification, never silently.
- Default assignments are static in this repo (overlap allowed, one canonical ID per rule). Distribute **100% of gathered/accepted project criteria** to one or more personas, preserving text/source and project precedence. Validate coverage, use all-persona fallbacks with warnings for unresolved assignments. Do not supply all project rules to every persona by default.
- Save the complete rendered system/user prompts once per persona, plus per-pass hash references and settings. The report lists all assigned criterion names/sources per persona; no separate verbose routing report is required.
- Any persona may report an evidenced correctness/safety defect outside its focus. The Comparator proposes the category; recorded discovering persona and agreement do not change.
- One shared Comparator/master list. In persona mode the Comparator sees the complete master and incoming batch, proposes duplicate mappings, categories and possible conflict groups; the harness validates and records them in a special master-list section. No separate Conflict Detector component/call. Conflicting factual claims remain separate; repeated flags do not create repeated verification jobs.
- After reviewing, a targeted Verifier investigates only distinct flagged groups, using fresh Claude sessions with Read/Grep/Glob in the same checkout. **Annotate only**: evidence, support/refutation, context, tradeoffs or unresolved status. No persona hierarchy, automatic suppression, rewriting or change to sighting counts. No conflicts means no Verifier calls. Cancellation/terminal failure stops all further calls; failures/unavailable budget leave unresolved groups visible.
- Confidence is per discovering persona; categories do not transfer votes and there is no manufactured overall confidence tier. Keep all Reviewer prompts independent and frozen; never feed conflict results back into passes.

The exact initial static allocation and proposed persona-prefixed IDs are tabulated in proposal §12; current `GEN-*` IDs and default rule wording have not changed. `--mode agent-personas` selects the reserved flow and fails clearly until implemented. The [persona-criteria draft](persona-criteria.md) is written and not loaded by the harness; the next work is persona implementation. The approved folders now exist. Next use the criteria draft to establish the initial persona assets, then implement persona behavior within `flows/agent_personas/` with fake-Claude/synthetic tests while preserving single-agent behavior. After it: project context pack, then consider broader per-finding verification; parallel execution, governance/security hardening and cost tuning remain later. The eval harness stays parked.

## Refactor verification

- All **244 tests pass** on Python 3.9.6 and 3.13, including eleven new flow-selection/isolation/compatibility tests. No tests call a real model.
- Audit fixed positional `Config` constructor compatibility, retained the `Engine` public export, corrected stale module references/behavior descriptions and separated the two README flows. All 68 Python files parse with Python 3.9 syntax and implementation modules import. All 19 Markdown files passed local link/anchor/fence checks.
- A synthetic before/after run matched exact model inputs/schemas/settings, criteria, merged findings, pass records/completions/stopping, summary and normalized report.
- All nine prompt/schema files and the active criteria file are byte-identical to their pre-refactor versions, guarded by `tests/fixtures/single_agent_asset_hashes.json`.
- CLI/REPL mode selection, config override precedence, unavailable-mode errors before preparation/assets/model calls, shared dependency boundaries and compatibility imports are covered.
- `git diff --check`, edited-document links/anchors and Markdown fences passed. No paid calls or production access were needed. This audited baseline is being committed and pushed at the user's explicit request.
- The parked eval tool remains parked; only its dependency-fingerprint traversal and matching test were adjusted to cover relocated Python subpackages.

## Constraints from the user

- Commit or push **only when asked**; leave changes in the working tree for the user to review.
- **Only Claude may read production material.** Another agent (for example Codex) must not read the user's production clone, diffs, saved findings or private cases, or ask for them to be pasted.
- Never put company or project identifiers (repository names, PR numbers, ticket keys, run directory names, code from saved runs) into this repository.
- Agree a design with the user before building it. Keep documentation honest about what did not work.
- Do not run real reviews or `doctor --live` unprompted: they use the user's Claude Code login and cost money.

## What exists

A Python package (`code_reviewer`, command `reviewer`) that reviews a GitHub PR with several independent Claude Code headless passes. The README has a high-level and a detailed flow diagram. In short:

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
3. **Agreement is not correctness.** A finding raised by 4 of 6 passes was wrong: a project rule existed in the repo but in no criteria file, and no pass searched for it. This is why the project context pack and the Verifier are planned. The Comparator only decides whether findings are the same issue, never whether they are true.
4. Cost: on a 2,100-line diff one pass costs about $0.19 for the first (cache write) and $0.05-0.07 after, driven by an 85k-token prompt, cache reads each turn and about 12k thinking tokens of output.
5. The saved raw output does not record which tools a pass called.

## Notes on next steps (details: proposal section 12)

- **Personas:** the design questions are settled as summarized above and specified in proposal §12. Implement the optional mode and conflict-only advisory verification next, using the README flow and proposal §12 as the agreed implementation contract. Do not return to the superseded three-perspective/all-project-rules/variable-pass plan.
- **Parallel passes:** run Reviewer calls in harness threads, never Claude sub-agents (they would break independence, validation and accounting). Split `PassRunner.run` into a parallel review phase and a serial compare-and-merge phase in pass order; run pass 1 alone first to warm the cache; `--parallel N`, default 1; `Stats` and progress output need locks.
- **Cost:** try `claude --effort` (not used yet) and a smaller criteria cap; compare findings by hand.
- **Reviewer prompt:** before reporting a finding that depends on an assumption, check the comment above the code, its tests and the repo docs; unverified assumptions go to `notes`.
- **Label command** (`reviewer label <run dir> <id> right|wrong|duplicate --why`), recording tool calls per pass (`stream-json`), and an option to limit unscoped `.claude/rules` files.
- Open checks against the real CLI: `--max-budget-usd` overrun behavior and clean timeout kill (proposal section 9).

## Practical notes

- **Start reading here:** `src/code_reviewer/flows/single_agent/flow.py`. `engine.py` only selects/delegates. Flow-owned `roles.py`, `passes.py`, `master.py`, `models.py`, `report.py`, `summary.py`, `stop_policy.py`, `dry_run.py`, `prompt_contract.py`, `prompts/` and `criteria/` stay together. Shared discovery/transport/validation/storage belong in `shared/`; do not import flow implementations from there. Root modules such as `git_ops.py` and `master.py` are compatibility bridges. `models.RunResult` is a compatibility export of the single-agent result.

- A saved run (`~/.code-reviewer/runs/<run>/`) holds `input/` (exact prompts, criteria block and index, diff, PR text), `pass-NN/` (`reviewer.raw.json` with each attempt's usage, `findings.json`, `comparator.raw.json`, `mapping.json`), `master.json`, `stats.json`, `labels.json`, `report.md`, `summary.md`.
- The replay tool tests Comparator changes cheaply (a few cents with Haiku); Reviewer prompt changes need a real run.
- Claude CLI facts (Claude Code 2.1.x): Reviewer flags `--safe-mode --restricted --tools "Read,Grep,Glob" --strict-mcp-config --disable-slash-commands --permission-prompts none --no-session-persistence --output-format json --json-schema <schema> --max-budget-usd <n> --append-system-prompt <text>`, prompt on stdin; no `--max-turns`; `--json-schema` adds an internal `StructuredOutput` tool and the answer is in `structured_output`.
- Tests: `tests/helpers.py` has `FakeRunner`, throwaway git repos (with `gc.auto=0`) and `finding(...)`; a real reply is kept as `tests/fixtures/live_envelope_haiku.json`.
- Docs that go stale most easily: README (options, code map, limits), proposal sections 4, 5, 9 and 10 and its checklist, `prompts/README.md`, and the explainer (call-stack tree, code excerpts).
