# AGENTS.md

Guidance for agents working on this repository. Create a handover document only when the user explicitly requests one.

## Product direction

One pull-request reviewer supports Claude and OpenAI as runtime-selected adapters. Both use the same review algorithms, criteria, validation, accounting and reports. `backends/contract.py` owns provider-neutral Runner/LLMResult/RunnerError; host and guest use one factory. Repository ownership, visibility or labels such as “production” and “personal” do not select behavior or providers.

Execution location is a separate operator choice: **VM by default**, with an explicit **direct-host option** for trusted use without VM startup overhead. The flag is `--no-sandbox`; trusted operator config may also select `sandbox = false`. Host acquisition is a supported feature. A failed VM request must never silently fall back to the host. Repository files and model text cannot select execution location or infrastructure policy.

The supported VM backend uses the shared jailed lifecycle. Do not add a separate raw-Firecracker launcher.

**Models propose; deterministic code validates and decides.** Every model reply is validated and the harness writes every artifact. Read/Grep/Glob are the only model code tools; no tests, builds, scripts or arbitrary model commands execute.

## Working scope and references

Focus on the one-shot CLI. Bare `reviewer` prints help without loading configuration. Preserve the parked `parked/repl.py`, `repl_help.py`, replay helpers and their tests; do not wire new features through them or restore forwarding wrappers.

Read [architecture](docs/architecture.md) for ownership, [the roadmap](docs/proposal.md#checklist) for work and qualification evidence, [VM architecture](docs/sandbox-architecture.md) for controls and [VM setup](docs/sandbox-check.md) for deployment checks. Use [usage](docs/usage.md) for configuration/reports, [LLM](docs/llm.md) for authentication, [tracing](docs/tracing.md) for observability, [prompts](docs/prompts.md) for model contracts and [the walkthrough](docs/project-explainer.md) for method-level call chains.

Operator configuration and credentials remain separate from repository criteria. Preflight/doctor are offline configuration reports, not deployment certification: no target, credential, worker or artifact activity. Host dry-run reads no credentials. Real VM jobs enforce worker/artifact/admission/confinement checks before selected-credential handoff. Public in-VM acquisition revokes source access before inference; private in-VM source authentication is unsupported. Clean images contain no target inputs, findings or credentials. A VM cannot revoke copied credentials or enforce hard provider spending caps.

Authentication is explicitly API-key or subscription; never infer or fall back between them. Claude VM subscription uses explicit `CLAUDE_CODE_OAUTH_TOKEN`. Explicit host subscription mode prefers that environment token; if absent, check the native CLI reports `loggedIn: true` / `authMethod: claude.ai` and let Claude manage its login. Never infer subscription access from a Console/API-key or unknown native method; do not add a Claude credential store or copy login files. ChatGPT uses reviewer-owned consent/private refresh storage and access-token-only VM handoff. OpenAI requires one explicit model for all roles and, for API-key access, three positive operator token rates; the key comes only from OPENAI_API_KEY.

VM progress is advisory over existing private frames: validate closed schemas and size/count budgets on both hops, never treat progress as admission/credential/cleanup authority. Bounded unchecked titles/locations may appear as reviewers return, followed by provisional consolidated previews; never add them to Langfuse, prompts or automatic posting. Keep terminal text safe and join heartbeat/worker threads on exit.

Tracing defaults on, with trusted `tracing = false` opt-out. Record bounded metadata in the core/guest; persist locally before optional host-only Langfuse export. Do not add guest egress, tracing keys in the VM/native model environment, full-content tracing or credential reads during preflight/dry-run. Qualification evidence belongs only in the roadmap.

## Setup and checks

```bash
python3.13 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[tracing]'
python -m unittest discover -s tests -t .
```

Support Python **3.13**. Run the whole suite on Python 3.13 before finishing a change. The core and VM recorder use the standard library. The approved optional host tracing extra uses Langfuse 5.1.0 on Python 3.13; do not add other dependencies. Tests use FakeRunners, fake executables and throwaway repositories: no real models, credentials, networking or VM boots. Real inference requires the user's request. Check deployment separately from unit tests.

## Code and working rules

- Every module starts with `from __future__ import annotations`. Use Python 3.13 syntax and standard-library APIs.
- Keep modules focused. `Engine` selects a flow; `flows/single_agent/flow.py` and `flows/agent_personas/flow.py` own their algorithms. Common mechanics live in `shared/`. Import implementations from their canonical owner. Retained REPL/replay code lives under `parked/`; do not restore forwarding wrappers.
- Persona Reviewers run concurrently within each round, then consolidate serially in fixed order. Configured passes default to three per persona. Budgets remain independent: criteria 10%, Comparator 10%, Verifier 13⅓%, remainder divided among personas; no borrowing. Verification annotates flagged conflicts without suppressing findings or changing votes/severity. Cancel/join workers before checkout cleanup.
- Validate model output, count dropped data and report the counts. Operator settings remain separate from repository review criteria.
- Do not commit/push unless explicitly requested. Leave changes reviewable in the working tree.
- Keep relevant code, prompts, README, proposal and explainers consistent. No automatic handover maintenance.
- Never copy private source, saved-run contents, company/PR identifiers, PII or credentials into this repository. Use synthetic fixtures. Do not inspect previously restricted private material merely to develop the reviewer.
- Canonical prompt changes require authorized before/after comparison evidence and honest results under the [review-quality comparison policy](docs/proposal.md#review-quality-experiments). Paid comparator replay is parked; do not present its CLI as working. Infrastructure changes do not justify paid prompt calls.
- Preserve no-target-IO/credential-free preflight and concrete VM admission checks; host execution requires the explicit operator selection. Frontend presentation tests may use `tests.helpers.synthetic_frontend`; actual boundary tests must not use that seam.
