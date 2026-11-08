# Reviewer roadmap

Current behavior belongs in the [architecture](architecture.md), [usage](usage.md), [LLM](llm.md), [sandbox](sandbox-architecture.md) and [tracing](tracing.md) guides. This document tracks outstanding work and review-quality experiments.

## Checklist

- [ ] Handle post-call token overruns without discarding otherwise valid completed output.
- [ ] Complete native Claude review qualification after fixing overrun handling, and qualify authentication/Read/Grep/Glob through the VM.
- [ ] Evaluate persona specialization and qualify long-running subscription refresh and additional failure paths.
- [ ] Compare authenticated provider usage/cache fields against report and trace totals.
- [ ] Measure real-model precision and genuine findings suppressed using a manually adjudicated before/after comparison set.
- [ ] Improve criterion attribution and repository context after manually checking findings.
- [ ] Explore read-only MCP access to explicitly referenced Jira tickets for requirements and acceptance criteria, with source provenance and visible access gaps.
- [ ] Compare models and pass counts manually before changing review orchestration or canonical prompts.

## Qualification evidence

Offline regressions cover accepted-criteria accounting separately from discovery resource bounds, bounded Git source reads, refusal on overflow without inference, and actionable host/guest failure reports. These checks establish input handling, not live model quality.

Unit tests use synthetic inputs and simulated endpoints; they do not establish account eligibility or review quality. No-key deployment checks cover all four simulated perspectives, public acquisition/revocation, jailer/native confinement, resource limits, cancellation/supervisor loss, cleanup and worker recovery. Recorded end-to-end checks cover ChatGPT subscription access and local VM trace export. Native Claude host subscription authentication and inference returned structured output, but the post-call token allowance check rejected it before shared validation and grounding. A completed Claude review, native Claude VM tools, long-running refresh and provider usage/cache reconciliation remain unqualified.

Local synthetic Langfuse ingestion covers both adapters and retry/quota failures. Stored observations are checked for one root, complete parent links, original timing, matching usage and absence of source/secret canaries. Native Claude traces expose aggregate invocation usage, not intermediate tool events.

An authenticated OpenAI subscription VM review completed two rounds across all four perspectives and exercised Read/Grep/Glob after admission under the 10,000-entry / 64 MiB text-tree caps. It returned no candidates, so this run does not qualify live candidate verification or establish finding precision/recall.

The supported-only pipeline passes the actual no-key sandbox check with simulated model replies, including the additional Verifier call, connected admission/file-tool/egress probes, cleanup and worker shutdown. Offline tests cover the four-file result allowlist and private audit export limits. This does not qualify live Verifier judgment or prove improved precision.

## Review-quality experiments

Canonical prompt/schema/criteria changes require separately authorized before/after comparisons and honest results here. Required prior observations are retained once in [experiment evidence](history.md); they are not proof of current quality.

Compare one versus multiple passes per persona, self-check on/off, models and pass counts on manually judged PRs. Inspect useful discoveries, false positives, misses, rule attribution and duplicate merges. Agreement measures repeated sightings, not correctness.

## Roadmap

| Stage | Work |
|---|---|
| Now | Compare real-model verification quality and candidate coverage under the existing Verifier budget; fix token-overrun handling where it prevents review qualification |
| Next | Continue provider/deployment qualification and criterion/context traceability based on observed failures |

### Verification milestone: supported-only publication

The [verification contract](verification.md) describes the implemented shared JSON evidence, independent investigation and deterministic publication filter. Reviewer/Verifier prompts and schemas now require structured arguments and grounded references. Every eligible deduplicated candidate is investigated, with connected conflicts assessed together; only supported candidates publish. Private audit artifacts preserve withheld claims, and suggested remedies need separate support.

**Comparison evidence and limits.** The reference revision is `01944a4`: its report admitted grounded scored candidates without requiring an independent supported assessment. Deterministic synthetic comparisons cover supported/refuted/unresolved and incomplete investigations, plus absent-claim evidence, schema provenance, overlapping conflicts and remedy gating. The prior policy would expose refuted/unresolved scored candidates; the new publication predicate withholds them. Both provider adapters' offline smoke flows now include a Verifier call. These checks establish contract enforcement and filtering, not the quality of an LLM's judgments. No paid or subscription model comparison has run for these prompt changes; there is no measured precision/recall improvement claim.

Remaining comparison work: independently adjudicate a small public/synthetic set, including genuine defects, existing caller guarantees, uncertain database-performance claims, ineffective tests, preference-only requests and correct defects with unsafe suggested fixes. Run the reference and updated prompts against the same pinned inputs under explicit inference authorization, recording false positives removed, genuine findings suppressed/unresolved, cost, latency and completion. Assess review misses using independently identified defects; candidate-only sets cannot establish recall. Keep private material outside this repository. Do not build a general eval framework for this milestone.

### Possible future improvement: referenced Jira requirements through MCP

When a PR or applicable project guidance explicitly references Jira tickets, optionally retrieve their requirements and acceptance criteria through an operator-configured, authenticated MCP connection. This supplements project context; it does not replace the baseline review rules or explicit project review instructions. Limit retrieval to referenced tickets rather than searching unrelated project issues.

Use read-only host-side acquisition and pass bounded, frozen ticket context to reviewers and the Verifier. Record ticket identity, source URL, retrieval time and available update/version metadata so requirement-based findings can cite the exact context used. Keep credentials and MCP access outside the guest and model tools; preserve existing execution and privacy boundaries.

Distinguish stated requirements from proposals and discussion. Ticket text is external evidence, not authority to execute commands or change reviewer policy. Surface contradictions with repository contracts or PR intent rather than inventing precedence. Missing authentication, inaccessible tickets or ambiguous references must be reported as context gaps; do not invent their requirements. Reviews can continue with available context and baseline rules, without claiming ticket requirements were checked. Keep private ticket content outside this repository and metadata-only traces.

Qualification should cover explicit reference resolution, read-only access, bounded content, requirement citations, conflicting/stale requirements and unavailable tickets using synthetic fixtures before separately authorized live integration checks.

### Possible future improvement: pluggable review skills

Projects could attach reusable review expertise—focus, criteria, investigation procedures and references—to the existing personas. Keep scheduling, validation, budgets and reporting in the harness. Additional skills should not automatically create more model sessions or expand tools; native scripts, hooks and MCP integrations remain outside the read-only review boundary.

Start with deterministic loading and complete assignment to the existing four personas. Freeze the selected instructions and versions alongside other review inputs. Dedicated project-defined personas would require generalizing IDs embedded in routing, scheduling, budgets, categories and validation; compare useful findings, false positives and cost before considering that extension. The skill format remains undecided.

### Review-report follow-ups: qualifications and criteria traceability

**Improvement backlog.** Evaluate the supported-only presentation and its separate remedy assessments before changing their contracts. Improve criteria traceability from observed findings. Private code, identifiers, findings and run paths stay outside this repository.

- **Link related defects and regression-test gaps.** Keep distinct concerns available, but help readers see when a test-gap finding covers the same failure as an implementation finding. Related findings are not automatically duplicates, and their count is not independent evidence of review breadth. Suggested tests must check intended recovery, not merely preserve the defective behavior being reported elsewhere.
- **Improve criterion attribution.** Investigate missing rule citations even when a finding invokes project conventions. Distinguish omitted/incorrect IDs, unavailable assigned rules and legitimate findings outside a persona's focus. Improve traceability without inventing rule matches or dropping otherwise evidenced findings.
- **Represent authority, scope and conflicting instructions in context.** The project context pack should distinguish repository contracts/conventions from planning claims and retain source paths and applicability. Do not treat conflicting general/specific guidance as an arbitrary strategy choice, or invent precedence when the repository does not establish it. Record unresolved ambiguity visibly.
- **Measure specialization and verification cost.** Track discovery persona separately from finding category; cross-focus findings are allowed, but do not by themselves prove specialization helps. Watch Verifier pool headroom across further runs before changing allocations. Compare useful findings, false positives and cost rather than raw finding counts alone.

Measuring the implemented verification filter is the immediate quality priority. Criterion/context traceability remains follow-up work; pluggable skills and an automated eval framework are not prerequisites. Prompt changes and paid comparisons require separate review and authorization.
