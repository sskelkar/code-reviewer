# Reviewer plan and decisions

One reviewer supports runtime-selected providers through a common LLM interface. VM execution is the default, with an explicit trusted-host option. Review quality is judged manually on real changes; no eval framework is maintained.

## Checklist

- [x] Single-agent and four-persona review algorithms, canonical criteria/prompts and deterministic evidence/output validation.
- [x] VM-default and explicit-host dispatch, public in-VM acquisition, private reports and explicit posting.
- [x] Jailed lifecycle, read-only admitted code, phase-scoped external networking, deadlines, recovery and clean image reuse.
- [x] Persistent deployment setup, binary omission policy and safe bounded HTTP diagnostics/retries.
- [x] Remove obsolete forwarding imports; isolate parked frontends and consolidate provider/explainer documentation.
- [x] Provider-neutral runner/result/error contract and one adapter factory for host/guest execution.
- [x] Explicit API-key versus subscription selection; Claude subscription token isolation with no automatic API-key fallback.
- [ ] Implement reviewer-owned ChatGPT subscription consent, protected credential lifecycle and streaming transport.
- [ ] Qualify successful subscription-backed inference, read tools, limits, private reports and cleanup through the actual VM.
- [ ] Compare review strategies/models manually; improve qualifications and criterion attribution described in §10.2.

No credential or live inference is required to refactor adapters or run unit tests. Authenticated requests reached the OpenAI endpoint and returned account/authentication errors; this is transport evidence, not successful inference or review-quality evidence.

## 1. Architecture

CLI → trusted configuration/preflight → VM or explicit host → selected flow → ModelCaller → Runner adapter → validated findings/accounting/reports. Acquisition, execution policy and credential handoff belong outside the LLM adapters. Adapters do not choose flow, criteria, scope or posting policy. See [architecture](architecture.md) and [LLM contract](llm.md).

### Loop

Single-agent: repeat fresh blind Reviewer passes, ground evidence and merge duplicates through a Comparator. Persona mode: run active Reviewers concurrently within each round, consolidate serially in fixed order, then run advisory verification for flagged conflicts. No per-persona VMs or separate conflict-detection agent.

## 2. Criteria (layered)

The ten `GEN-*` rules remain the single-agent defaults. Persona defaults have static assignments documented in [the criteria index](persona-criteria.md). Resolve project rules from the pinned base branch, never the PR's edited head. Gather linked in-repository review rules and scoped AGENTS/.claude rules, retain source identity and project precedence, then distribute every accepted project rule across personas with deterministic coverage checks and fallback. Canonical wording lives in flow assets, not duplicated here.

## 3. What the Reviewer receives

Pinned merge-base diff, delimited untrusted PR title/body/commit text, resolved criteria and a separate head snapshot. Binary contents are omitted and reported. Planning claims cannot override code evidence. Configured exclusions stay visible; findings in excluded files are dropped and counted. Every pass within a reviewer/persona gets the same frozen prompt and never receives earlier findings.

## 4. Findings and grounding

Findings cite rules and either exact quotes or an absent-behavior anchor overlapping a changed hunk. The harness validates JSON, paths, quotes, anchors and IDs; rejected data is counted. Self-check rejection is retained separately. Comparators propose duplicate mappings; Python validates references and applies them. Agreement is repeated sightings, not correctness. One completed single-agent pass is unrated; persona agreement stays per discovering persona.

## 5. Safety and execution scope

Models have Read/Grep/Glob only, with no test/build/script execution. The VM supplies OS/network containment beyond read-only tools. Host execution requires explicit operator selection. Never fall back to a provider, authentication mode or host path on failure. Runtime credentials are sensitive even in a disposable VM. See [the security contract](security-contract.md).

## 6. Interface

One-shot `reviewer review`, `doctor`/preflight and supported sandbox check/recover. The REPL and comparator replay are retained under `parked/` without a CLI route. Provider, authentication, mode, model and execution location are trusted operator choices. See [usage](usage.md).

## 7. Report, confidence, cost

VM exports report/summary/stats only; internal prompts/replies are ephemeral. Host runs retain detailed input/call artifacts. Reports show findings, agreement, criteria, malformed/dropped data, omissions and usage. API costs and native subscription-equivalent values are estimates or allocation units, never a provider-enforced cap or a transferable subscription balance. HTTP error codes/validated headers survive in stats; raw error bodies do not.

## 8. Ceilings (defaults, tune from logs)

Configured passes, tokens, timeouts, prompt/input/result sizes and concurrency bound work. Persona pools allocate criteria 10%, Comparator 10%, Verifier 13⅓%, with the remainder equally divided among personas; no borrowing. Unknown-cost calls consume their reserved allowance. Temporary HTTP failures retry the current request with bounded attempts/delays/deadline; quota/auth failures stop. See [usage](usage.md#persona-budget-pools).

## 9. Experiment plan and v0 checks

Historical observations below are evidence for particular changes, not current commands or deployment certification. The host live doctor and paid replay are parked/removed; use current usage and VM docs.

**Runs to compare:** single pass vs full loop · `self_check` on vs off · Haiku vs Sonnet · general loop vs personas (§12). Judge across several runs per PR (results vary).

**Metrics (manual in v0):** discovery curve · **pass-to-pass overlap** (near-identical passes make every finding "High" and the signal meaningless) · precision per tier (`mark`) · approximate recall from `missed` (real found ÷ (real found + missed)) · cost per pass · spot-check of Comparator merges/splits.

**Early results (a documentation-only PR in a real repository, Haiku, first prompt version).** Three runs: a single pass found 1 issue; a second run found 2; a six-pass run found 4 distinct issues in total, but each pass raised exactly one finding (the sixth none) and each picked a different one, so every finding scored Low. A real issue found in two earlier runs was never raised in the six-pass run: two passes noted that they had not opened the relevant controller. Nothing was lost by grounding, rule checks or self-check (0 dropped); the model simply stopped after one finding. Lessons: (1) agreement-based confidence only works if each pass reports everything it believes, so the Reviewer prompt now asks for thorough, complete reviews (and no longer suggests a tool-call count); (2) whether an issue appears depends on which code paths a pass happened to open, so coverage varies; (3) independent passes behave as complementary finders, which makes **verification** (roadmap P2: fresh sessions that check each candidate finding against the code) a better confidence signal than frequency. Re-run the same PR after prompt changes and compare findings per pass and the size of the union.

**Second finding: two defects that hid agreement.** After the prompt change, a six-pass run on a larger PR raised 30 findings, but 11 (about 37%) were dropped because the model wrote rule IDs with brackets (`[GEN-010]`), and the reported list fragmented one legacy-test gap into three findings. Both lowered confidence artificially: an issue seen in 5 of 6 passes showed up as scattered Low entries. Fixes: rule citations are repaired or set to `none` instead of dropping the finding, and the Comparator prompt treats a broader and a narrower statement of the same gap as one finding. Replaying the recorded findings (the then-existing replay helper (now parked)) with Haiku twice gave identical groupings, with the merged test gap and a stale-requirements finding both at 6/6 passes. Lesson: audit dropped-finding counters on every new kind of PR, not just on the one used to tune the prompts.

**Comparator experiment (replaying saved runs with the then-existing replay helper (now parked)).** The comparator decides which findings of different passes are the same issue. In the six-pass run with thorough reviewers, three findings about one block (an API example that omits one of two exception types handled by the same code path) were left as separate or partly merged findings. Replaying that run: the clear duplicates (three issues found in 5-6 passes) grouped identically every time; the three borderline findings did not. With Haiku, the same prompt merged them in one replay and not in another, and a rewritten rule ("same finding = one change would resolve both; do not choose `new` merely because wording differs") did not measurably change that (2 of 4 replays identical to the original, 2 partial merges). With Sonnet all 4 replays merged the cluster into one finding found in 3 passes and kept the genuinely different finding separate. On an older run Sonnet also merged three findings about one example block into one, including one whose claim was about the actual status code, which a careful reader might keep separate, so it merges more aggressively. Sonnet costs about 15 times more for this step (about $0.0095 versus $0.0006 per call). Decision: keep the clearer rule, keep Haiku as the default comparator, and leave `model_comparator` as a setting. Borderline merges are a judgment call; the recorded `assigned` mapping and the replay tool make them auditable.

**Default criteria revision (2026-10-18; not yet tried live).** Replaced the twelve broad defaults with ten repository-assessable criteria, each with inspection scope, reporting conditions and exclusions. Input bounds, related database writes requiring atomicity, contract-document updates, test levels and unnecessary or misleading mocking are explicit. Missing test-file changes, a fixed test-pyramid ratio, multiple writes alone, or mocking alone are not findings. Tests check loading and delivery through the real prompt path, not review quality; no before/after experiment exists, so no improvement is claimed. Rule IDs were renumbered, so IDs in older saved runs mean different topics: compare runs by their saved criteria blocks.

**Removed: local eval framework (2026-10-28).** The unused experiment runner, grading/label commands, rubric, cases and framework-specific tests were deleted at the user's request. No model-quality result was established by that framework. Judge real reviews manually for now.

**Third finding: agreement is not correctness.** A finding raised by 4 of 6 passes was wrong. The project had a data-access rule the reviewer did not know; the rule existed in the repository, but only in files that were not review criteria and that nothing linked to, and no pass searched for it. Correlated mistakes across passes are the main weakness of agreement-based confidence. Responses: nested `AGENTS.md` and `.claude/rules` files are now added by location (§2), and the project context pack and Verifier are planned (§12). The saved raw output does not record which tools a pass called, so what each pass searched is unknown.

**Flow ownership refactor (2026-10-20; historical step before persona runtime, no model-quality result).** Moved current orchestration, Reviewer/Comparator, master/result, reports, prompt loader and unchanged assets into `flows/single_agent/`. Shared Claude/git/diff/grounding/criteria-discovery/storage/statistics code lives in `shared/`. `Engine` preserves its API and selects one flow; CLI/REPL `--mode` overrides `review_mode` (default `single-agent`). Reserved `agent-personas` fails without loading single-agent assets, preparing a PR or calling Claude. At that historical stage, legacy imports remained bridges; the current cleanup removes those forwarding modules. A synthetic FakeRunner comparison before/after matched exact model inputs/schemas/settings, criteria, merged findings, pass accounting/stopping, summary and normalized report. Pinned SHA-256 fixtures guard unchanged prompt/schema/criteria bytes. Audit preserved the original positional `Config` model argument and the `Engine` public export; these compatibility contracts are tested. All 244 offline tests pass on Python 3.9 and 3.13, including eleven new flow-selection/isolation/compatibility tests; documentation links/anchors and `git diff --check` pass. No real reviews or production material were accessed.

**Persona criteria assets (2026-10-20; initial asset step, before runtime wiring).** Authored thirteen canonical rules, common review instructions and four static focus/assignment definitions under `flows/agent_personas/`. Added entry-point/artifact wiring, test discovery/failure propagation and query/index compatibility to the ten existing concerns, with repository-evidence/reporting boundaries. Three offline asset-contract tests check complete static coverage, unique/shared identities and shared-parser rendering/disabling. The single-agent asset hash checks remain unchanged. All 247 tests pass on Python 3.9 and 3.13; all 24 Markdown files pass local link/anchor/fence checks. No saved production runs, paid model calls or eval experiments were used; no review-quality improvement is claimed.

**Persona runtime wiring (2026-10-20; no quality result).** The optional flow now loads its own assets, classifies accepted project rules with coverage-validated fallback, freezes four Reviewer inputs, runs twelve fixed slots, merges/categories/flags conflicts, and conditionally annotates flagged groups with grounded evidence. All calls and retry attempts check budget. Synthetic tests exercise the new contracts and frontends; single-agent prompt/schema/criteria bytes and synthetic baseline behavior remain unchanged. Invalid model file paths now fail deterministic grounding instead of crashing either flow. No actual persona model calls, production material or eval runs were used. All 278 offline tests pass on Python 3.9 and 3.13, including thirty-one persona-flow tests; 77 Python modules parse/import, and 34 Markdown files pass local link/anchor/fence checks. The single-agent synthetic baseline matches. Current containment evidence is recorded in the [VM checklist](sandbox-todo.md).

**Final pre-pilot audit (2026-10-20; no quality result).** Found and fixed retained sightings from a slot interrupted during consolidation: staged master changes now roll back without changing earlier completed slots. Also repaired the scheduled-pass Markdown table and stale docs/README diagram branches. Two added synthetic regressions bring the suite to 280 passing tests on Python 3.9 and 3.13 (thirty-three persona-flow tests). The single-agent synthetic baseline still matches. No model-facing prompt edits, live calls, private saved-run reads, production access, commits or pushes were needed. This supports a controlled Claude/user pilot, not a claim of model accuracy; the project context pack remains the next separate feature after manual comparison.

**Configurable persona counts (2026-10-21; no quality result).** At the user's request, replaced the fixed-three restriction with `persona_passes = 3` and per-run `--passes N` (N per persona). The configured count determines round-robin slots, progress, dry runs and completion totals; agreement still uses actual successful completions, with one unrated. Budget/failure policies and model-facing prompts remain unchanged. All 286 offline tests pass on Python 3.9 and 3.13, including configured 1/2/5-round schedules, interrupted completion accounting, validation and frontend/config overrides. The single-agent synthetic baseline still matches. No real reviews, paid calls or private material were used; this establishes harness behavior only.

**Parallel persona rounds and budget pools (2026-10-21; harness evidence only).** Implemented concurrent Reviewers within each round, a barrier before deterministic serial consolidation, and proportional isolated budget pools (criteria/Comparator/Verifier weights 3/3/4 out of 30, with the remainder split among personas). Per-persona exhaustion leaves other personas running; no borrowing or warmup calls. Atomic reservations, separate request logs, locked statistics and cancellable subprocess groups protect concurrency. Seven new synthetic tests cover overlap, concurrency limits, ordered merges/barriers, allocation scaling, reservations/unknown usage, isolated exhaustion and cancellation. All 294 offline tests pass on Python 3.9 and 3.13; the single-agent synthetic baseline still matches and documentation links pass. Model-facing prompts are unchanged. No production material, private saved runs or paid calls were used; real latency, quality and allocation suitability remain unmeasured.

**Persona transport correction (2026-10-21; synthetic reproduction).** A user-provided live-run report showed a Classifier timeout, followed by conservative consumption of its unknown-cost allowance. This was not evidence that the 10% criteria allocation was too small. Reproduced a large-prompt stdin stall: a slow synthetic child cannot finish reading when repeated timeout polls stop registering pending pipe writes. Supplying complete UTF-8 stdin via an anonymous temporary file fixes delivery while preserving cancellation. The regression fails on the old transport and passes on the fix. Shared cost certainty now includes failed calls with unknown cost. No model-facing prompt changes, private code fixtures or new paid calls were used; allocation ratios are unchanged.

Validation for the stdin/cost-certainty fix: **296 tests pass** on Python 3.9.6 and 3.13; the single-agent synthetic baseline still matches, and Markdown links/anchors/fences and whitespace checks pass.

**User-run execution check (2026-10-21):** after the stdin fix, a user-run one-round persona review completed all four slots with no retries, failed calls, model-reply validation errors or unknown-cost calls. The report contained eleven consolidated findings and two annotated conflict groups. Reported cost was about $0.903: criteria $0.029, Comparator $0.013, Reviewers $0.499 and Verifier $0.361. Criteria usage was consistent with the earlier successful run; the timeout warning did not justify raising its allocation. The Verifier used about 90% of its $0.40 pool, so watch its headroom in further runs; ratios remain unchanged. This is execution/accounting evidence only, not a finding-quality assessment or measured latency comparison. Only user-authorized report accounting/completion sections were assessed; no private code, identifiers or prompt content were copied into this repository. Codex made no paid model calls. The user authorized committing and pushing the transport/reporting fixes with these documentation updates.

**Proposed success bar (adjust after first runs):** High-tier precision clearly above Low-tier; the loop finds at least one real issue a single pass missed on roughly 1 in 3 PRs; cost within a small multiple of a single pass.

**Verify against the real CLI** (checked with `reviewer doctor --live` on Claude Code 2.1.301 and Haiku):
1. ✅ The Reviewer flag set is accepted together and works with an enterprise login (`--safe-mode`, `--restricted`).
2. ✅ Isolation canary: a planted `CLAUDE.md` is ignored and a planted `.claude/settings.json` hook doesn't run. Tools are Read, Grep and Glob plus Claude Code's internal `StructuredOutput` tool, which `--json-schema` adds to return the answer.
3. ✅ Reads work in the checkout. Reads outside it are refused: with a canary file planted next to the working directory, `doctor --live` saw the model's relative and absolute `Read` and its `Grep`/`Glob` searches all denied (3 denied tool calls recorded, canary text never returned). The `--add-dir` input dir is not used because diff and criteria are inline in the prompt. Verified on Claude Code 2.1.301 with `--safe-mode --restricted`; re-run `doctor --live` after upgrading Claude Code.
4. ✅ JSON envelope: the schema-conforming answer is in `structured_output` (also as JSON text in `result`), with `usage` (input, output, cache write/read tokens), `total_cost_usd`, `modelUsage`, `num_turns`, `duration_ms`. A real reply is kept as a test fixture. How `--json-schema` failures surface is not yet seen.
5. ⬜ `--max-budget-usd` behavior when exceeded; timeout kills cleanly (the timeout path is covered by a fake-binary test only).
6. ✅ `--model haiku` resolves to `claude-haiku-5-5` (recorded from `modelUsage`). ⬜ `stream-json` for a live `--verbose` mode is not implemented.
7. ⬜ Run one tiny prompt 3× to eyeball pass-to-pass variation.

The VM-default dispatcher now shares worker/image preparation and jailed lifecycle with deployment checks. It privately carries complete review settings, the selected runtime credential and bounded reports. Simulated tests cover both providers/strategies and native parallel environment propagation. A real no-key rerun exposed a provider TLS-probe variable regression; after fixing it, both simulated flows, network/native/file checks, cleanup and worker shutdown passed. No live authentication/inference result is claimed.

## 10. Roadmap

| Stage | Work |
|---|---|
| Now | Complete subscription access behind the common LLM contract, then qualify the selected account/provider in the VM |
| Next | Manually compare findings and address demonstrated report/criteria issues |
| Later, if needed | Requalify parked frontends, private in-VM source authentication and scoped extensions below |

### 10.1 Possible future improvement: pluggable review skills

**Idea recorded 2026-10-21; exploratory, not an implementation commitment or a change in priorities.** Separate reusable review expertise from reviewer execution. Package focus, criteria, investigation procedures and supporting references as review skills; keep fresh sessions, pass counts, parallel rounds, budgets, validation, consolidation and agreement under the Python harness. The current four personas remain the default configuration.

Projects could attach skills to existing personas—for example, PostgreSQL migration/transaction checks to production safety, or project-specific API/event contract guidance to correctness. Private project skills could stay in that project's repository. A later extension could let a project request a dedicated reviewer for a skill, with its own passes and budget. Attaching expertise should not automatically create another reviewer: additional sessions increase cost and overlapping findings. Skills should describe how to investigate and what constitutes evidence, beyond listing criteria.

Prefer explicit project configuration and deterministic harness loading of selected skills over relying on model-triggered discovery for required coverage. Freeze/log the actual skill instructions, references and versions/hashes used by each reviewer; retain complete project-rule assignment, stable rule identities, common finding schemas and read-only permissions. Native skill scripts, hooks or tools must not implicitly expand execution privileges. Whether to adopt the standard `SKILL.md` format or a narrower review-specific format remains open.

Suggested sequence if pursued: first attach skills to the existing four personas; then consider configurable reviewer definitions. The latter requires generalizing the four IDs currently embedded in routing, scheduling, budget allocation, categories and validation. Compare review quality, coverage and cost before replacing the existing defaults. This could complement the project context pack; it does not replace that work.

### 10.2 Review-report follow-ups: qualifications and criteria traceability

**Recorded 2026-10-25 at the user's request; improvement backlog, not implemented.** Report-level inspection of a user-run review suggested the following generic improvements. These observations do not independently establish the underlying findings' correctness; production code, identifiers, findings and run paths must remain outside this repository.

- **Make verification qualifications visible beside findings.** Show each applicable claim status (such as supported, conditional or refuted) with a link to its conflict annotation in both full reports and shareable summaries. An unconditional finding should not hide a later qualification. Preserve all applicable annotations without changing finding text, severity, votes or suppressing claims; distinguish an unverified finding from a verified one.
- **Separate defect support from remedy support.** A Verifier can support the failure sequence while finding a proposed fix unverified. Make that distinction apparent rather than presenting the suggested fix as established. Schema/prompt changes, if needed, require separate design and evaluation.
- **Link related defects and regression-test gaps.** Keep distinct concerns available, but help readers see when a test-gap finding covers the same failure as an implementation finding. Related findings are not automatically duplicates, and their count is not independent evidence of review breadth. Suggested tests must check intended recovery, not merely preserve the defective behavior being reported elsewhere.
- **Improve criterion attribution.** Investigate missing rule citations even when a finding invokes project conventions. Distinguish omitted/incorrect IDs, unavailable assigned rules and legitimate findings outside a persona's focus. Improve traceability without inventing rule matches or dropping otherwise evidenced findings.
- **Represent authority, scope and conflicting instructions in context.** The project context pack should distinguish repository contracts/conventions from planning claims and retain source paths and applicability. Do not treat conflicting general/specific guidance as an arbitrary strategy choice, or invent precedence when the repository does not establish it. Record unresolved ambiguity visibly.
- **Measure specialization and verification cost.** Track discovery persona separately from finding category; cross-focus findings are allowed, but do not by themselves prove specialization helps. Watch Verifier pool headroom across further runs before changing allocations. Compare useful findings, false positives and cost rather than raw finding counts alone.

Suggested immediate follow-up: make existing verification qualifications visible at each finding, then investigate criteria/context traceability. Continue manual quality comparisons; the project context pack remains the next substantial feature. Pluggable skills (§10.1), broader verification and an automated eval framework are not prerequisites. This entry records work to revisit; it does not authorize prompt changes, new paid reviews or automatic finding suppression.

## 11. Assumptions and open questions

- Claude enterprise access must include Claude Code and permit the intended automation; entitlement is not inferred from owning a subscription.
- ChatGPT plan usage has a documented preview protocol; implement consent, stream completion/failures, model availability and token lifecycle before claiming support.
- No successful live inference in the current VM configuration has been recorded. Unit tests and provider/account errors do not establish it.
- LSP, project context packs, full agentic traces and generalized review skills are optional later milestones, not prerequisites for the current runner interface.

## 12. Agreed persona design and next steps

### 12.1 Responsibilities and static defaults

Correctness, code quality, test quality and production safety. Defaults are statically routed and may overlap; keep canonical rule identities. A reviewer can report an evidenced defect outside its focus. Categorization does not invent a sighting by another persona.

### 12.2 Gather and distribute project criteria once

Resolve base-commit rules, preserve all accepted text/source and validate 100% assignment. Unresolved routing falls back visibly to all personas. Save exact per-persona prompts and settings; reports list assigned rules. Model classification is unnecessary for defaults.

### 12.3 Configured review loop

Three passes per persona by default; `--passes N` sets rounds. No quiet-pass convergence. Reviewers run concurrently within a round, followed by serial consolidation in fixed order. Independent budgets, bounded retries, failure limits and cancellation can leave slots incomplete; report scheduled/completed/failed slots honestly. Join/cancel workers before checkout cleanup.

### 12.4 One Comparator, one master list, incremental conflict flags

Comparator sees incoming findings and the full master, proposes duplicate mappings, categories and possible conflicts. Python validates them and stages changes atomically. There is no separate conflict-detector component.

### 12.5 Targeted, advisory Verifier

Only flagged groups receive fresh verification with read tools. Ground non-unresolved claims in repository evidence. Annotations can explain factual support, context and tradeoffs; they do not suppress findings or alter original severity/votes. Terminal failure or cancellation stops further calls; incomplete groups remain pending/unresolved.

### 12.6 Report and exact prompt logging

Both flows retain identical per-pass inputs within each reviewer/persona. Host runs save input and role-call artifacts; VM export intentionally omits them. Any future tracing must respect the same privacy/size boundaries. Keep per-persona source agreement, conflict annotations and incomplete verification visible.

### 12.7 Implementation and manual comparison

The persona loop is implemented and synthetically tested. A historical user-run execution check completed; finding quality remains unmeasured. Compare against single-agent on actual selected projects after provider/authentication qualification. Do not rebuild the removed eval framework or change canonical prompts without authorized comparison evidence.

## Current cleanup and adapter evidence

The current cleanup removes 21 forwarding modules, moves retained inactive frontends to `parked/`, consolidates duplicated provider runbooks into `llm.md` and replaces the stale walkthrough with the actual execution/code map. Both algorithms now use `LLMResult`/`RunnerError` and `ModelCaller`; host and guest use one factory. API-key and subscription selection is explicit. ChatGPT login/streaming remains a separate unfinished milestone, not a working adapter selection. Canonical prompt/schema/criteria bytes are unchanged. Validation uses synthetic repositories/providers only, with no credential access or paid calls.

Cleanup/interface validation: all 407 tests pass on Python 3.9 and 3.13. Synthetic native subprocesses exercise subscription-selected host calls through both flows; guest tests exercise both Claude access modes and OpenAI API-key mode through the common factory. Documentation links/anchors/fences and whitespace checks pass. Canonical asset hashes remain unchanged. No real credential, networking, VM boot or model call was used for this refactor.
