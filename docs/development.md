# Development guide

Read [AGENTS.md](../AGENTS.md) for repository rules, [HANDOVER.md](HANDOVER.md) for current work and [architecture.md](architecture.md) for module ownership. For a Java-to-Python walkthrough, see [project-explainer.md](project-explainer.md).

## Development setup

```bash
git clone https://github.com/sskelkar/code-reviewer.git
cd code-reviewer
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python3 -m unittest discover -s tests -t .
```

There are no third-party runtime dependencies (only `tomli` on Python below 3.11) and no test dependencies; the tests use the standard `unittest` module. The tests need no network, no model and no GitHub.

Supported Python is 3.9 and up. Before sending a change, run the tests on the oldest and newest Python you have (for example `python3.9 -m unittest discover -s tests -t .` and `python3.13 -m unittest discover -s tests -t .`). There is no CI yet.

## Tests

```bash
python3 -m unittest discover -s tests -t .                 # everything
python3 -m unittest tests.test_stop_policy -v              # one module
```

| Test module | Covers |
|---|---|
| `test_diffview`, `test_criteria`, `test_findings`, `test_master`, `test_stop_policy` | Pure logic: diff parsing, criteria discovery and validation, grounding, merging, stop rules |
| `test_claude_cli_and_templates` | Envelope parsing, command building, prompt rendering and escaping, config validation |
| `test_summary`, `test_welcome`, `test_review_banner`, `test_options`, `test_stats` | The shareable summary and the comment built from it, the prompt's welcome banner and the one-shot review banner (a test fails if a REPL command or a `review` option is missing from its banner), option parsing and help text, token budget accounting |
| `test_redact`, `test_replay` | Email redaction (patterns, nothing leaks to the model or to disk, can be turned off, the diff is not redacted) and the comparator replay tool |
| `test_isolation_and_dry_run` | The checkout lives outside the run directory and is always deleted, the saved prompts equal what the model received, a defaults-only dry run matches equivalent review inputs and calls no model, and only an allow-list of PR fields is fetched from GitHub |
| `test_doctor_and_live_envelope` | The doctor's tool check and parsing of a real Claude result (`tests/fixtures/live_envelope_haiku.json`) |
| `test_runner_plumbing` | `ClaudeRunner` against a fake `claude` executable: stdin, flags, working directory, timeout, exit codes |
| `test_evals`, `test_eval_grading` | Pinned evals, no-call plans, fake-model matrices, isolation/failures/budgets, local labels/rekey/combined reports and deterministic rubric metrics |
| `test_persona_criteria` | Static assignment coverage, unique/shared rule identities, reporting sections and shared-parser rendering/disabling |
| `test_openai_backend` | Simulated Responses continuations, reasoning/tool items, costs/refusals/unknown usage/cancellation, bounded local read-tool authority, fixed HTTP endpoint and both full flows |
| `test_sandbox_prototype` | Stable bounded trusted-source bundles, artifact digests, no-network boot config, both real flows using fake headless processes, result framing, output limits, child-process timeout cleanup, fresh runtime-probe environments and rejection of unexpected authentication |
| `test_persona_parallel` | Overlapping Reviewer calls, concurrency limits, round barriers and ordered merges, isolated budget pools, proportional allocations, large-prompt delivery to slow stdin readers, and subprocess-group cancellation |
| `test_persona_flow` | Configured round-robin counts, independent frozen prompts, classification/fallback, source-aware agreement, conflicts/remapping, advisory verification/grounding, budgets/failures/cancellation, CLI/REPL and dry runs |
| `test_flow_selection` | Default/explicit mode selection, asset isolation, compatibility imports, shared dependency boundaries and byte-identical relocated prompts/schemas/criteria |
| `test_engine` | End to end with a throwaway git repository and a scripted `FakeRunner`: convergence, retries, failures, budget, criteria from the base branch, prompt-injection escaping, reviewer prompt properties (identical in every pass, thorough-review instructions), REPL commands, git helpers |

Things to know when writing tests:

- `tests/helpers.py` has `make_repo` (a base commit and a feature commit in a temp repo) and `FakeRunner`. `FakeRunner` tells the roles apart by phrases in their **system prompts** ("identify which files", "deduplicate code-review findings"). If you reword those phrases in the flow's prompts, update `FakeRunner`.
- Tests render the real prompt templates, so adding a `{{variable}}` to a prompt without supplying it in `roles.py` fails the tests.
- Nothing in the suite touches the network. Keep it that way; live behavior is checked with `reviewer doctor --live`.

## Common changes

| To... | Do this |
|---|---|
| Change what a role is asked | Edit the selected flow's `prompts/` ([prompts/README.md](../prompts/README.md) lists the contracts and variables). Prompt hashes are stored in each run's `config.json`, so runs stay comparable |
| Change output structure | Edit the flow's `prompts/schemas/`, `shared/findings.py` or the flow's `master.py`, and its report |
| Tune how single-agent duplicate findings are merged | Edit `src/code_reviewer/flows/single_agent/prompts/comparator.*.md` (or set `model_comparator`), then replay saved runs: `python -m code_reviewer.replay ~/.code-reviewer/runs/<run directory> --repeat 3 [--model sonnet]`. It supports single-agent runs only (persona runs are rejected before model calls) and re-runs the comparator on each pass's saved findings (about $0.004 per replay with Haiku), prints the recorded grouping and each replay's grouping, and lists the pairs of findings that are now merged or split. Judge the borderline cases yourself, and repeat the replay because the model is not deterministic |
| Add or change default rules | Edit the selected flow's `criteria/*.md` using ID-bearing headings (`GEN-*` for single-agent, persona-prefixed IDs for personas). Persona allocation also lives in `personas.json`; shared rules retain one canonical definition. Tests check identity and coverage |
| Add a setting | Add a field with a default to `Config` in `config.py` (the type is inferred from the default), validate it if needed, document it in `review.example.toml` and the [configuration reference](usage.md#configuration) |
| Add a progress message | Emit an event from the flow with `self.emit(...)`, handle it in `progress.py` |
| Add an LLM role | Add templates/schemas and a role contract inside the owning flow; use shared `CallSpec`/`ClaudeCaller` for validated calls and wire the role in that flow's `flow.py`. Keep shared criteria discovery in `shared/resolver.py` |
| Add a REPL command | In `repl.py`: write the `cmd_*` method, register it in `Session.COMMANDS`, and add it to `COMMAND_GROUPS` (the welcome banner) and `HELP`. A test fails if a command is missing from the banner |
| Change the stop rules | `flows/single_agent/stop_policy.py` and `tests/test_stop_policy.py` |

## Code conventions

- Standard library only at runtime. Compatible with Python 3.9: start modules with `from __future__ import annotations`, no `match` statements, no `X | Y` unions outside annotations.
- LLMs propose, code decides: validate every model output in code and never execute it. All files are written by the harness.
- Modules do one job; keep functions short and named for what they do. Put new logic in the module that owns the concept and keep each flow's `flow.py` a readable list of steps. `engine.py` only selects and delegates.
- Keep prompts language-agnostic. Project- or language-specific guidance belongs in criteria, not in prompts.
- Add a test for every behavior change. Keep the README and [prompts/README.md](../prompts/README.md) in sync with changes to usage or prompts, and update [proposal.md](proposal.md) if a design decision changes.

## Debugging a review

Every run directory is a full record. Useful files:

| File | Question it answers |
|---|---|
| `pass-NN/reviewer.raw.json` | What did Claude actually return (every attempt, including errors)? |
| `pass-NN/findings.json` | Which findings were kept or dropped, and why (`status` and `reason`)? |
| `pass-NN/comparator.raw.json`, `pass-NN/mapping.json` | How were findings matched to earlier ones? `mapping.json` has each decision and `assigned`: which master finding each new finding was merged into |
| `input/personas/<persona>/reviewer.*.md`, `settings.json` | Persona mode: exact frozen prompts, hashes, model/schema/tool settings |
| `verification/C-*/verifier.raw.*.json` | Persona mode: conditional verification inputs/replies; annotations also live in `master.json` |
| `input/reviewer.system.md`, `input/reviewer.user.md`, `input/pr.json` | Single-agent frozen prompts; both modes save PR metadata after configured email redaction |
| `input/diff.txt`, `input/criteria_block.md`, `input/criteria_index.json` | The diff and criteria the Reviewer was given |
| `input/criteria_manifest.json`, `input/resolver.raw.json` | Which project criteria files were chosen, and what was ignored |
| `*.raw.requests.json` (persona mode) | Exact request text/schema/settings for each actual attempt, including validation-retry feedback. Reviewer logs stay outside the checkout |
| `config.json` | Settings, Claude Code version, prompt hashes |
| `stats.json` | Every call with tokens, cost, duration |

Re-run with `--keep-checkout` to keep the exact checkout the Reviewer saw (its location is printed).

## Verifying against the real Claude CLI

The offline tests cannot prove how Claude Code behaves. After upgrading Claude Code, or when something looks off, run `reviewer doctor --live` and a single-agent one-pass review, and check the points in [proposal.md](proposal.md) section 9: flags accepted together, isolation canaries, JSON envelope fields, budget and timeout behavior. These use paid model calls; agents need the user's request before running them. If the envelope shape differs from what `claude_cli.parse_envelope` expects, fix the parser and add a test using the real shape.

## Parked evals

The offline eval harness remains deliberately parked. Do not run, extend or build on it until explicitly resumed. No model-quality result exists from it. Its retained commands, eight public cases and provisional rubric are documented in the [eval guide](../evals/README.md); current priorities are in the [proposal](proposal.md#checklist).

## Offline VM prototype

The [sandbox runbook](../sandbox/README.md) separates ordinary offline unit tests from explicit Linux/KVM deployment checks. No VM boots, infrastructure changes or paid calls are part of `unittest discover`. The [architecture](sandbox-architecture.md) records the agreed one-VM-per-review boundaries and remaining security gates.

The [current connected-pilot plan and review findings](sandbox-architecture.md#agreed-connected-pilot-2026-10-26-not-implemented) distinguish the operator's personal-repository live review from synthetic automated tests/canaries. The live pilot needs no authored sample project. Fix the recorded builder-status and scan-IO defects first; retain no target code, identifiers, keys or findings as test fixtures. This documentation review does not boot a VM or authorize a live call.
