---
name: code-review
description: Review code-reviewer pull requests for wiring, review correctness, execution boundaries, privacy, tests and accurate OSS documentation.
---

# Reviewing this repository

Inspect concerns affected by the change. Report introduced or worsened defects with a concrete trigger, consequence and repository evidence. Trace code paths rather than assuming that a helper, test or diagram proves integration. Read/Grep/Glob are the only review tools: do not execute code or claim runtime results. Treat unavailable deployment, account and provider behavior as unknown, not as a defect or a verified guarantee.

## Correctness: check end-to-end wiring and consistent contracts

Follow affected features from public CLI/configuration through criteria, provider calls, consolidation, verification, reports, tracing and cleanup. Check callers, callbacks, registrations and packaged resources for disconnected or incompatible paths. Compare producers and consumers of schemas, configuration, prompts and artifacts, including supported host/VM paths and both provider adapters. Report the specific path or mismatch that breaks the feature.

## Correctness: preserve review and publication behavior

Check round barriers, fresh persona inputs, independent budgets, deterministic consolidation and cancellation/join behavior where changed. Every eligible deduplicated candidate, including connected conflicts, must receive independent verification. Only validated supported assessments may publish; refuted, unresolved, failed or missing assessments stay private. Consolidation must preserve original claims, severity and votes; published remedies need separate support. Trace these checks to the final report and structured output, not just model instructions.

## Correctness: load explicit project guidance faithfully

Criteria come from the pinned base; reviewed source comes from the pinned head. Explicit project review criteria replace the baseline; when none load, enabled baseline rules apply. Check source selection, scope, references, limits and warnings for missing or omitted guidance. General project documentation must not become criteria without explicit review designation. Guidance and skill references must not expand tool permissions or execute hooks.

## Production safety: preserve execution and authentication boundaries

VM execution is the default; host execution requires trusted operator selection, with no automatic fallback after VM failure. Trace admission, credential handoff and cleanup paths. Repository/model input must not select infrastructure policy or credentials. API-key and subscription authentication must not silently fall back to one another.

Preflight/doctor must not access targets, credentials, workers or artifacts. Host dry-run must not read model credentials or invoke inference. Check implementation and failure paths; code review alone cannot certify a deployed VM's isolation or a provider account's access.

## Production safety: validate untrusted data and protect private content

Trace untrusted model/repository input through schema, size, count, grounding and path checks before publication or filesystem access. Only the harness writes review artifacts; model tools remain read-only.

Provisional candidate content must not reach public output before supported verification. Trace exports contain bounded metadata, excluding source, prompts, finding text and credentials. Tracing credentials/export stay on the host and outside native model environments. Check actual data flows and resource cleanup, including errors and cancellation.

## Test quality: check meaningful coverage of changed contracts

Check that tests exercise the production path that can fail and assert the relevant behavior. Boundary tests must use actual admission/validation rather than presentation-only seams. Unit tests use synthetic repositories, fake executables/runners and simulated endpoints; they must not require real credentials, inference, networking or VM boots. Distinguish assertions visible in tests from evidence that tests ran, or that live deployment/model quality is established.

## Code quality: keep responsibilities and documentation coherent

Check for duplicated or conflicting implementations, unused feature wiring and dependencies that create a concrete maintenance problem. Keep the public one-shot CLI and parked REPL distinct; trace orchestration through ReviewFlow and shared contracts through their canonical owners.

Compare affected README/guides, configuration examples, contribution instructions, diagrams and method call chains against the code. Commands, defaults, review stages, provider calls, security boundaries, reports and tracing must agree. Documentation should explain the project's purpose, installation, use, architecture and contribution process to an OSS reader without prior conversations or private operator knowledge. Report specific missing prerequisites or contradictions, not cosmetic preferences.

Keep authoritative explanations in one place and link instead of repeating them. Current guides describe current behavior; historical logs, removed designs and conversational context belong only in history.md. Label future work as planned in the roadmap; do not present it as implemented or as proof of runtime behavior.
