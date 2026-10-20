# AGENTS.md

Guidance for AI agents working on this repository. Start here, then read [`docs/HANDOVER.md`](docs/HANDOVER.md) for the current state and what to do next.

## What this project is

`code_reviewer` is a multi-pass pull request review agent. A deterministic Python harness runs several independent, fresh-context Claude Code headless (`claude -p`) review passes over a GitHub PR, merges the findings into a master list, scores each by how many passes found it, and prints a report and a shareable summary. It is a learning project for a controlled environment.

**Central design rule: language models only propose; plain code validates and decides.** Every model reply goes through a validator, and the harness writes every file itself.

## Where to read

| Need | File |
|---|---|
| Current state, decisions, next steps | [`docs/HANDOVER.md`](docs/HANDOVER.md) |
| Design reasoning, roadmap, experiment results | [`docs/proposal.md`](docs/proposal.md) |
| How to use it, configuration, code map, tests | [`README.md`](README.md) |
| Prompts and what the harness enforces around them | [`prompts/README.md`](prompts/README.md) |
| Python walkthrough for Java developers | [`docs/project-explainer.md`](docs/project-explainer.md) |
| Archived long proposal (history only) | [`docs/proposal-full.md`](docs/proposal-full.md) |

## Setup and tests

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip       # Python 3.9's bundled pip cannot do an editable install
pip install -e .
python3 -m unittest discover -s tests -t .
```

- Supported Python: **3.9 and up**. Run the tests on both the oldest and newest Python you have before finishing (the system `/usr/bin/python3` is 3.9: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -t .`).
- Standard library only (plus `tomli` below Python 3.11). No test dependencies: `unittest`, a `FakeRunner`, throwaway git repos and a fake `claude` binary. Tests never call a real model.
- `reviewer doctor --live` checks the real Claude Code setup (costs a few cents). Do not run it or any real review without the user asking.

## Code conventions

- Every module starts with `from __future__ import annotations`. No `match` statements and no `X | Y` types at runtime (Python 3.9).
- Keep modules small and single-purpose; see the code map in the README. `Engine` is a stable mode-selection facade. `flows/single_agent/flow.py:SingleAgentFlow.review` is the current composition root. Reviewer/Comparator live in that flow's `roles.py`; the shared Resolver is in `shared/resolver.py`. The `agent_personas` flow is reserved and not implemented; common mechanics belong in `shared/`. Package-root compatibility modules are not the implementation to edit.
- Match the surrounding code's naming, comment density and idiom. Docstrings explain why, not what.
- A model's output is never trusted: validate it, count what you drop, and show the counts in the report.
- Do not add dependencies.

## Rules for working here

1. **Do not commit or push unless the user explicitly asks.** Leave changes in the working tree so the user can review `git diff`. State what changed and which files to look at.
2. **Keep code, prompts and docs in sync in the same change.** If behavior changes, update the README, `docs/proposal.md` and `prompts/README.md` where they describe it, and the explainer if it mentions it.
3. **No company or PR identifiers in the repository.** The saved runs under `~/.code-reviewer/runs/` contain code and PR text from private repositories. Never copy repository names, PR numbers, ticket IDs, run directory names or code from them into files here. Describe them generically ("a larger PR in a real repository").
4. **No PII or secrets** in code, tests, fixtures or docs. Use made-up data.
5. **Prompt changes need evidence.** Compare before and after on saved runs with `python -m code_reviewer.replay <run dir> [--model] [--repeat]` (see the README) and record honest results in `docs/proposal.md` section 9, including when a change did not help.
6. **Tests with every change**, and run the whole suite, not just the module you touched.
7. Security was deliberately loosened for v0 (controlled environment). The hardening backlog is `docs/proposal.md` section 5. Do not silently widen what the reviewer can read or run: it has only Read, Grep and Glob inside a temporary checkout.

## Roles and ownership of decisions

The three model roles are the **Criteria Resolver** (picks the project's criteria files from `AGENTS.md`), the **Reviewer** (one fresh session per pass, blind to other passes) and the **Comparator** (maps each pass's findings onto a master list). The harness decides everything else: grounding checks, rule IDs, confidence tiers, when to stop, what is posted.
