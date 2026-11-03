# Reviewer roadmap

Current behavior belongs in the [architecture](architecture.md), [usage](usage.md), [LLM](llm.md), [sandbox](sandbox-architecture.md) and [tracing](tracing.md) guides. This document tracks outstanding work and review-quality experiments.

## Checklist

- [x] Group persona findings by correctness, production safety, test quality and code quality, using descriptive titles.
- [ ] Handle post-call token overruns without discarding otherwise valid completed output; distinguish failed/discarded reviews from zero findings.
- [ ] Complete native Claude review qualification after fixing overrun handling, and qualify authentication/Read/Grep/Glob through the VM.
- [ ] Qualify live persona coverage, long-running subscription refresh and additional failure paths.
- [ ] Compare authenticated provider usage/cache fields against report and trace totals.
- [ ] Show existing Verifier qualifications beside each finding in reports and summaries (see review-report follow-ups below).
- [ ] Improve criterion attribution and repository context after manually checking findings.
- [ ] Compare strategies/models manually before changing review algorithms or canonical prompts.

## Qualification evidence

Unit tests use synthetic inputs and simulated endpoints; they do not establish account eligibility or review quality. No-key deployment checks cover both simulated flows, public acquisition/revocation, jailer/native confinement, resource limits, cancellation/supervisor loss, cleanup and worker recovery. End-to-end execution has been exercised with ChatGPT subscription access and local VM trace export. Native Claude host subscription authentication and inference returned structured output, but the post-call token allowance check rejected it before shared validation and grounding. A completed Claude review, native VM tools, live persona coverage, long-running refresh and provider usage/cache reconciliation remain unqualified.

Local synthetic Langfuse ingestion covers both adapter paths/flows and retry/quota failures. Stored observations are checked for one root, complete parent links, original timing, matching usage and absence of source/secret canaries. Native Claude traces expose aggregate invocation usage, not intermediate tool events.

## Review-quality experiments

Canonical prompt/schema/criteria changes require separately authorized before/after comparisons and honest results here. Required prior observations are retained once in [experiment evidence](history.md); they are not proof of current quality.

Compare single-pass versus repeated review, self-check on/off, models and strategies on manually judged PRs. Inspect useful discoveries, false positives, misses, rule attribution and duplicate merges. Agreement measures repeated sightings, not correctness. Parked replay helpers have no supported CLI route.

## Roadmap

| Stage | Work |
|---|---|
| Now | Fix post-call token-overrun handling and failure reporting; complete a Claude review, then show per-finding Verifier qualifications |
| Next | Manually compare findings, improve criterion/context traceability, and qualify live persona coverage, usage/cache accounting and long-running refresh |
| Later, if needed | Single-agent presentation grouping, parked frontends, private in-VM source authentication and scoped extensions below |

### Possible future improvement: single-agent presentation

Consider grouping single-agent findings by cited review criterion, with a general-concerns section for uncited findings. Do not force persona categories onto this flow. Decide the layout using representative reports; its current confidence-based layout remains supported.

### Possible future improvement: pluggable review skills

**Optional later extension.** Separate reusable review expertise from reviewer execution. Package focus, criteria, investigation procedures and supporting references as review skills; keep fresh sessions, pass counts, parallel rounds, budgets, validation, consolidation and agreement under the Python harness. The current four personas remain the default configuration.

Projects could attach skills to existing personas—for example, PostgreSQL migration/transaction checks to production safety, or project-specific API/event contract guidance to correctness. Private project skills could stay in that project's repository. A later extension could let a project request a dedicated reviewer for a skill, with its own passes and budget. Attaching expertise should not automatically create another reviewer: additional sessions increase cost and overlapping findings. Skills should describe how to investigate and what constitutes evidence, beyond listing criteria.

Prefer explicit project configuration and deterministic harness loading of selected skills over relying on model-triggered discovery for required coverage. Freeze/log the actual skill instructions, references and versions/hashes used by each reviewer; retain complete project-rule assignment, stable rule identities, common finding schemas and read-only permissions. Native skill scripts, hooks or tools must not implicitly expand execution privileges. Whether to adopt the standard `SKILL.md` format or a narrower review-specific format remains open.

Suggested sequence if pursued: first attach skills to the existing four personas; then consider configurable reviewer definitions. The latter requires generalizing the four IDs currently embedded in routing, scheduling, budget allocation, categories and validation. Compare review quality, coverage and cost before replacing the existing defaults. This could complement the project context pack; it does not replace that work.

### Review-report follow-ups: qualifications and criteria traceability

**Improvement backlog.** Review-report presentation and criteria traceability need manual evaluation. Private code, identifiers, findings and run paths stay outside this repository.

- **Make verification qualifications visible beside findings.** Show each applicable claim status (such as supported, conditional or refuted) with a link to its conflict annotation in both full reports and shareable summaries. An unconditional finding should not hide a later qualification. Preserve all applicable annotations without changing finding text, severity, votes or suppressing claims; distinguish an unverified finding from a verified one.
- **Separate defect support from remedy support.** A Verifier can support the failure sequence while finding a proposed fix unverified. Make that distinction apparent rather than presenting the suggested fix as established. Schema/prompt changes, if needed, require separate design and evaluation.
- **Link related defects and regression-test gaps.** Keep distinct concerns available, but help readers see when a test-gap finding covers the same failure as an implementation finding. Related findings are not automatically duplicates, and their count is not independent evidence of review breadth. Suggested tests must check intended recovery, not merely preserve the defective behavior being reported elsewhere.
- **Improve criterion attribution.** Investigate missing rule citations even when a finding invokes project conventions. Distinguish omitted/incorrect IDs, unavailable assigned rules and legitimate findings outside a persona's focus. Improve traceability without inventing rule matches or dropping otherwise evidenced findings.
- **Represent authority, scope and conflicting instructions in context.** The project context pack should distinguish repository contracts/conventions from planning claims and retain source paths and applicability. Do not treat conflicting general/specific guidance as an arbitrary strategy choice, or invent precedence when the repository does not establish it. Record unresolved ambiguity visibly.
- **Measure specialization and verification cost.** Track discovery persona separately from finding category; cross-focus findings are allowed, but do not by themselves prove specialization helps. Watch Verifier pool headroom across further runs before changing allocations. Compare useful findings, false positives and cost rather than raw finding counts alone.

Suggested immediate follow-up: make existing verification qualifications visible at each finding, then investigate criteria/context traceability. Continue manual quality comparisons; the project context pack remains the next substantial feature. Pluggable skills, broader verification and an automated eval framework are not prerequisites. Prompt changes and paid comparisons require separate review and authorization.
