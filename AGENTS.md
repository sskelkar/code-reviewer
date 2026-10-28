# AGENTS.md

Guidance for agents working on this repository. Read the [proposal checklist](docs/proposal.md#checklist), [architecture](docs/architecture.md) and [VM checklist](docs/sandbox-todo.md) for current work. Create a handover document only when the user explicitly requests one.

## Product direction

One pull-request reviewer supports Claude and OpenAI as runtime-selected adapters. Both use the same review algorithms, criteria, validation, accounting and reports. Repository ownership, visibility or labels such as “production” and “personal” do not select behavior or providers.

Execution location is a separate operator choice: **VM by default**, with an explicit **direct-host option** for trusted use without VM startup overhead. The proposed flag is `--no-sandbox`; it is not implemented yet. Host acquisition is an intended supported feature: preserve it while wiring the direct flow. A failed VM request must never silently fall back to the host. Repository files and model text cannot select execution location or infrastructure policy.

The standalone raw-Firecracker experiment was extracted into an independent playground outside this repository. Do not restore that launcher here. The supported VM backend uses the shared jailed lifecycle.

**Models propose; deterministic code validates and decides.** Every model reply is validated and the harness writes every artifact. Read/Grep/Glob are the only model code tools; no tests, builds, scripts or arbitrary model commands execute.

## Current implementation

Both single-agent and four-persona algorithms are implemented. The host acquisition implementation and both provider adapters exist. Public CLI/REPL currently provide preflight/refusal only: neither direct-host dispatch nor authenticated VM dispatch is integrated. Do not claim a planned flag works or enable execution just by changing readiness rows.

The supported `reviewer sandbox check` uses a dedicated mountless Lima Linux worker on macOS and one jailed Firecracker VM per workload. Checks cover simulated model execution in both flows, bounded input/result transfer, external phase-scoped network policy, non-root/no-new-privileges, read-only code, host-observed confinement/resource limits and cleanup. `--pr` fetches public GitHub input inside the review VM and revokes source access before inference. Optional capsule input remains; private in-VM source authentication is unsupported. `--lifecycle` tests cancellation/supervisor loss; `sandbox recover` resets abandoned dedicated-worker state. Clean cached images contain no target inputs, findings or credentials.

Actual no-key VM checks passed. Authenticated adapter/VM integration remains unfinished. The existing connected transport's implemented inference route is currently specific to one adapter; complete endpoint/authentication support for both without creating separate review products. Custom guest-wide syscall filtering and a custom credential broker are deferred. A VM does not revoke a copied provider credential or enforce a hard provider spending cap.

The unused eval framework and host-based doctor implementation have been removed. `doctor` is an offline status report; `doctor --live` refuses. The recorded-envelope parser regressions remain.

## Where to read

| Need | File |
|---|---|
| Current plan and experiments | [docs/proposal.md](docs/proposal.md) |
| Architecture, execution choices and module ownership | [docs/architecture.md](docs/architecture.md) |
| VM design and controls | [docs/sandbox-architecture.md](docs/sandbox-architecture.md) |
| Supported no-key deployment command | [docs/sandbox-check.md](docs/sandbox-check.md) |
| Remaining VM integration work | [docs/sandbox-todo.md](docs/sandbox-todo.md) |
| Shared requirements and execution boundaries | [docs/security-contract.md](docs/security-contract.md) |
| Commands/configuration/reports | [docs/usage.md](docs/usage.md) |
| Tests/debugging | [docs/development.md](docs/development.md) |
| Model-facing contracts | [prompts/README.md](prompts/README.md) |
| Python walkthrough | [docs/project-explainer.md](docs/project-explainer.md) |

## Setup and checks

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python3 -m unittest discover -s tests -t .
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -t .
```

Support Python **3.9+**. Run the whole suite on the oldest and newest Python available before finishing a change. Standard library only, plus `tomli` below Python 3.11; do not add dependencies. Tests use FakeRunners, fake executables and throwaway repositories: no real models, credentials, networking or VM boots. Real inference requires the user's request. Check deployment separately from unit tests.

## Code and working rules

- Every module starts with `from __future__ import annotations`; no `match` statements or runtime `X | Y` types.
- Keep modules focused. `Engine` selects a flow; `flows/single_agent/flow.py` and `flows/agent_personas/flow.py` own their algorithms. Common mechanics live in `shared/`. Root compatibility modules are forwarding imports, not implementation files.
- Persona Reviewers run concurrently within each round, then consolidate serially in fixed order. Configured passes default to three per persona. Budgets remain independent: criteria 10%, Comparator 10%, Verifier 13⅓%, remainder divided among personas; no borrowing. Verification annotates flagged conflicts without suppressing findings or changing votes/severity. Cancel/join workers before checkout cleanup.
- Validate model output, count dropped data and report the counts. Operator settings remain separate from repository review criteria.
- Do not commit/push unless explicitly requested. Leave changes reviewable in the working tree.
- Keep relevant code, prompts, README, proposal and explainers consistent. No automatic handover maintenance.
- Never copy private source, saved-run contents, company/PR identifiers, PII or credentials into this repository. Use synthetic fixtures. Do not inspect previously restricted private material merely to develop the reviewer.
- Canonical prompt changes require authorized before/after replay evidence and honest results in proposal section 9. Infrastructure changes do not justify paid prompt calls.
- Preserve no-target-IO preflight and refusal while dispatch is incomplete. Frontend presentation tests may use `tests.helpers.synthetic_frontend`; actual boundary tests must not use that seam.
