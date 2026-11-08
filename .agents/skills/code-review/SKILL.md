---
name: code-review
description: Review code-reviewer pull requests for pipeline correctness, evidence validation, provider parity, execution boundaries, privacy and meaningful test coverage.
---

# Reviewing this repository

Use this skill when reviewing pull requests to code-reviewer. Apply the built-in baseline alongside these explicit project requirements. Inspect only concerns affected by the change; report introduced or worsened defects with a concrete trigger, consequence and repository evidence. These instructions do not authorize live inference, posting, credential access, commands, skill scripts or additional tools. The reviewing model uses Read/Grep/Glob only.

## Correctness: preserve the shared review and publication contract

Trace affected paths through Engine, ReviewFlow, shared validation and report generation. Both provider adapters must preserve the same provider-neutral result/error contract and review semantics. Provider, authentication mode and execution location are independent operator choices.

Check round barriers, fresh persona inputs, independent budgets, deterministic consolidation and cancellation/join behavior where changed. Every eligible deduplicated candidate, including connected conflicts, must receive independent verification. Only validated supported assessments may publish; unresolved, refuted, failed or missing assessments remain private. Original claims, severity and votes must survive consolidation; suggested remedies require separate support. Report a concrete path that violates these contracts, rather than requesting an orchestration redesign.

## Production safety: preserve execution and authentication boundaries

VM execution is the default. Direct-host execution requires explicit trusted operator selection; a VM failure must not fall back to host execution. Repository text and model replies cannot change execution location, credential selection, admission or infrastructure policy. Follow real admission, credential handoff and cleanup paths when these boundaries change.

Preflight and doctor remain offline configuration reports without target, credential, worker or artifact activity. Host dry-run must not read model credentials or invoke inference. Authentication modes must not silently fall back between API-key and subscription access. Do not treat cloud/Codex credentials as authorization for reviewer inference.

## Production safety: preserve privacy and bounded evidence handling

Models propose; deterministic code validates and writes artifacts. Trace schema, size, count, grounding and path checks from untrusted model/target input to publication or filesystem access. Review tools cannot run target code, tests, builds or arbitrary commands.

Provisional candidate details must remain private until publication is supported. Tracing is bounded metadata only; source, prompts, findings text and credentials must not enter trace exports. Tracing credentials and host export stay outside the guest/native model environment. Check actual data flows and cleanup ownership, not merely the presence of a helper or a redaction function.

## Correctness: preserve explicit project review guidance

Project review instructions come from the pinned base commit; reviewed source comes from the pinned head. Do not infer project requirements from general architecture, setup or planning material. Load explicit review guidance with its scope and source; if none is loaded, use enabled baseline criteria. Skill instructions and references must not expand tool permissions or execute hooks. Check that modified guidance, missing references and omitted content are represented honestly.

## Test quality: exercise the contract that can fail

Inspect relevant existing and changed tests for synthetic repositories, fake runners/executables and simulated endpoints. Boundary tests must exercise actual admission/validation; presentation-only seams cannot establish boundary protection. Ordinary unit tests must not invoke real models, credentials, network services or VMs.

Ask whether the assertion would fail for the specific regression and whether the fixture exercises the affected production path. Distinguish offline contract coverage from live provider entitlement, deployment qualification and model quality. Never claim tests ran or passed without recorded evidence; the reviewing model cannot execute them.

## Code quality: retain ownership and accurate public contracts

Keep the one-shot CLI as the public interface and the parked REPL inactive. ReviewFlow owns orchestration; shared mechanics and provider-neutral contracts retain their canonical owners. Report an ownership problem only when it creates a concrete dependency or maintenance consequence.

When a change alters public configuration, CLI behavior, prompts or result contracts, check the corresponding existing documentation, examples and tests for contradictions. Roadmap claims do not prove runtime behavior. Canonical prompt/criteria changes require the separately authorized comparison evidence described by project policy; absent live evidence must not be presented as measured quality improvement.
