# Reviewer plan and decisions

One reviewer supports runtime-selected providers through a common LLM interface. VM execution is the default, with an explicit trusted-host option. Review quality is judged manually on real changes; no eval framework is maintained.

## Checklist

- [x] Single-agent and four-persona review algorithms, canonical criteria/prompts and deterministic evidence/output validation.
- [x] VM-default and explicit-host dispatch, public in-VM acquisition, private reports and explicit posting.
- [x] Jailed lifecycle, read-only admitted code, phase-scoped external networking, deadlines, recovery and clean image reuse.
- [x] Persistent deployment setup, binary omission policy and safe bounded HTTP diagnostics/retries.
- [x] Flow-owned implementations/assets, shared mechanics, concern-owned tracing package and isolated inactive frontends.
- [x] Provider-neutral runner/result/error contract and one adapter factory for host/guest execution.
- [x] Explicit API-key versus subscription selection; Claude subscription token isolation with no automatic API-key fallback.
- [x] Reviewer-owned ChatGPT consent, protected account storage/refresh, streaming transport and access-token-only VM handoff.
- [x] Complete a ChatGPT-backed VM review (user-confirmed).
- [x] Optional bounded metadata tracing in its own package, host-only Langfuse exporter and validated VM stats handoff; local persistence precedes export.
- [x] Local Langfuse on port 4000, synthetic review ingestion and stored-trace hierarchy/usage/privacy audit.
- [ ] Qualify tracing through a real VM workload and authenticated provider usage; see [qualification status](langfuse-plan.md#qualification-status).
- [ ] Qualify native Claude tools, live persona coverage, long-running refresh and additional failure paths.
- [ ] Compare review strategies/models manually; improve qualifications and criterion attribution described in §10.2.

Unit tests use synthetic inputs and simulated endpoints. Broader provider/mode/refresh qualification remains a separate step; offline tests do not establish account eligibility or review quality.

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

VM exports report/summary/stats only; internal prompts/replies are ephemeral. Host runs retain detailed input/call artifacts. Reports show findings, agreement, criteria, malformed/dropped data, omissions and usage. API costs use estimated dollar allowances. Subscription calls use token allowances and show API cost as N/A. Neither is a provider-enforced cap or a transferable subscription balance. HTTP error codes/validated headers survive in stats; raw error bodies do not.

## 8. Ceilings (defaults, tune from logs)

Configured passes, tokens, timeouts, prompt/input/result sizes and concurrency bound work. Persona pools allocate criteria 10%, Comparator 10%, Verifier 13⅓%, with the remainder equally divided among personas; no borrowing. Unknown-cost API calls and incomplete-usage subscription calls consume their reserved allowance. Temporary HTTP failures retry the current request with bounded attempts/delays/deadline; quota/auth failures stop. See [usage](usage.md#persona-budget-pools).

## 9. Review-quality experiments

Canonical prompt changes require authorized before/after comparisons and honest results here. No prompt/schema/criteria changes or new paid quality experiments accompany the subscription adapter. Offline protocol tests establish harness behavior only. Prior observations are retained once in [historical evidence](history.md).

Compare single-pass versus repeated review, self-check on/off, models and the two strategies on manually judged PRs. Inspect discovery curves, overlap, false positives, missed findings, attribution and duplicate merges. Agreement measures sightings, not correctness; manual labels do not turn it into a calibrated probability. The retained replay helpers have no supported CLI route.

## 10. Roadmap

| Stage | Work |
|---|---|
| Now | Qualify [VM/authenticated tracing](langfuse-plan.md#qualification-status); qualify native Claude tools, live persona coverage and long-running token refresh |
| Next | Manually compare findings and address demonstrated report/criteria issues |
| Later, if needed | Requalify parked frontends, private in-VM source authentication and scoped extensions below |

### 10.1 Possible future improvement: pluggable review skills

**Optional later extension.** Separate reusable review expertise from reviewer execution. Package focus, criteria, investigation procedures and supporting references as review skills; keep fresh sessions, pass counts, parallel rounds, budgets, validation, consolidation and agreement under the Python harness. The current four personas remain the default configuration.

Projects could attach skills to existing personas—for example, PostgreSQL migration/transaction checks to production safety, or project-specific API/event contract guidance to correctness. Private project skills could stay in that project's repository. A later extension could let a project request a dedicated reviewer for a skill, with its own passes and budget. Attaching expertise should not automatically create another reviewer: additional sessions increase cost and overlapping findings. Skills should describe how to investigate and what constitutes evidence, beyond listing criteria.

Prefer explicit project configuration and deterministic harness loading of selected skills over relying on model-triggered discovery for required coverage. Freeze/log the actual skill instructions, references and versions/hashes used by each reviewer; retain complete project-rule assignment, stable rule identities, common finding schemas and read-only permissions. Native skill scripts, hooks or tools must not implicitly expand execution privileges. Whether to adopt the standard `SKILL.md` format or a narrower review-specific format remains open.

Suggested sequence if pursued: first attach skills to the existing four personas; then consider configurable reviewer definitions. The latter requires generalizing the four IDs currently embedded in routing, scheduling, budget allocation, categories and validation. Compare review quality, coverage and cost before replacing the existing defaults. This could complement the project context pack; it does not replace that work.

### 10.2 Review-report follow-ups: qualifications and criteria traceability

**Improvement backlog.** Review-report presentation and criteria traceability need manual evaluation. Private code, identifiers, findings and run paths stay outside this repository.

- **Make verification qualifications visible beside findings.** Show each applicable claim status (such as supported, conditional or refuted) with a link to its conflict annotation in both full reports and shareable summaries. An unconditional finding should not hide a later qualification. Preserve all applicable annotations without changing finding text, severity, votes or suppressing claims; distinguish an unverified finding from a verified one.
- **Separate defect support from remedy support.** A Verifier can support the failure sequence while finding a proposed fix unverified. Make that distinction apparent rather than presenting the suggested fix as established. Schema/prompt changes, if needed, require separate design and evaluation.
- **Link related defects and regression-test gaps.** Keep distinct concerns available, but help readers see when a test-gap finding covers the same failure as an implementation finding. Related findings are not automatically duplicates, and their count is not independent evidence of review breadth. Suggested tests must check intended recovery, not merely preserve the defective behavior being reported elsewhere.
- **Improve criterion attribution.** Investigate missing rule citations even when a finding invokes project conventions. Distinguish omitted/incorrect IDs, unavailable assigned rules and legitimate findings outside a persona's focus. Improve traceability without inventing rule matches or dropping otherwise evidenced findings.
- **Represent authority, scope and conflicting instructions in context.** The project context pack should distinguish repository contracts/conventions from planning claims and retain source paths and applicability. Do not treat conflicting general/specific guidance as an arbitrary strategy choice, or invent precedence when the repository does not establish it. Record unresolved ambiguity visibly.
- **Measure specialization and verification cost.** Track discovery persona separately from finding category; cross-focus findings are allowed, but do not by themselves prove specialization helps. Watch Verifier pool headroom across further runs before changing allocations. Compare useful findings, false positives and cost rather than raw finding counts alone.

Suggested immediate follow-up: make existing verification qualifications visible at each finding, then investigate criteria/context traceability. Continue manual quality comparisons; the project context pack remains the next substantial feature. Pluggable skills (§10.1), broader verification and an automated eval framework are not prerequisites. This entry records work to revisit; it does not authorize prompt changes, new paid reviews or automatic finding suppression.

## 11. Assumptions and open questions

- Claude enterprise access must include Claude Code and permit the intended automation; entitlement is not inferred from owning a subscription.
- ChatGPT plan usage follows a documented preview protocol. Account eligibility, real consent/refresh and model access need live qualification.
- Native Claude tools and broader mode/refresh qualification remain open. Unit tests do not establish live behavior.
- Local metadata tracing is implemented; deployment/trace qualification is tracked in [the Langfuse integration plan](langfuse-plan.md). LSP, project context packs, full-content tracing and generalized review skills remain optional later milestones, not prerequisites for the current runner interface.

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

Both flows retain identical per-pass inputs within each reviewer/persona. Host runs save input and role-call artifacts; VM export intentionally omits them. Optional metadata tracing respects the same privacy/size boundaries. Keep per-persona source agreement, conflict annotations and incomplete verification visible.

### 12.7 Implementation and manual comparison

The persona loop has synthetic regression coverage. Compare against single-agent on selected projects after authentication qualification. Finding quality remains unmeasured; canonical prompt changes require authorized comparison evidence.
