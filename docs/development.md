# Development guide

Read [AGENTS.md](../AGENTS.md) for repository rules and [architecture.md](architecture.md) for module ownership. For method-level call chains and Python implementation details, see [project-explainer.md](project-explainer.md).

## Development setup

Use the [README setup](../README.md#quick-start), then run `python -m unittest discover -s tests -t .`.

The core runtime uses only the standard library. Optional host tracing uses `langfuse==5.1.0` on Python 3.13 (`pip install -e '.[tracing]'`); there are no required test dependencies; the tests use the standard `unittest` module. The tests need no network, no model and no GitHub.

Python 3.13 is the application baseline on the host, worker and review VM. Before sending a change, run `python3.13 -m unittest discover -s tests -t .`. There is no CI yet.

`tests/test_native_claude_login.py` uses a fake native executable to check subscription-status gating, environment filtering, both host review flows, error privacy and credential-free preflight/dry-run. It never reads the operator's real Claude login or calls a model.

## Configuration and execution checks

`reviewer review example/project#1 --provider claude --preflight` reports configuration errors without external commands, target reads, model calls or credential prompts. Exit 1 indicates missing/unsupported configuration. A ready offline configuration does not establish worker or authentication readiness; `runtime_checked: false` states that the backend has not run. Explicit `--no-sandbox --preflight` reports host configuration without target/credential activity. `doctor` reports offline configuration status; use `sandbox check` for no-key deployment checks. Explicit host dispatch is implemented via `--no-sandbox` or trusted `sandbox = false`.

The REPL/replay are parked under `src/code_reviewer/parked/`: their modules and regression tests remain, but bare `reviewer` only prints help and does not start a session. New end-to-end work targets the one-shot CLI.

`tests/test_security_boundary.py` exercises the actual public frontdoors, operator configuration and artifact staging. It must not patch away backend admission checks. Presentation tests use `tests.helpers.synthetic_frontend` to supply fake-runner results without claiming release readiness; direct `Engine` tests still exercise both algorithms, their prompt bytes and agreement/accounting behavior. Core flow APIs are shared building blocks for the implemented explicit-host and default-VM execution paths.

Operator TOML goes outside Git working trees (normally `~/.code-reviewer/review.toml`, recommend 0600). The loader ignores current-directory configuration and rejects linked, non-regular, oversized or foreign-writable files.

## Tests

```bash
python -m unittest discover -s tests -t .                 # everything
python -m unittest tests.test_stop_policy -v              # one module
```

| Test module | Covers |
|---|---|
| `test_diffview`, `test_criteria`, `test_findings`, `test_master`, `test_stop_policy` | Pure logic: diff parsing, criteria discovery and validation, grounding, merging, stop rules |
| `test_claude_cli_and_templates` | Envelope parsing, command building, prompt rendering and escaping, config validation |
| `test_summary`, `test_welcome`, `test_review_banner`, `test_options`, `test_stats` | The shareable summary and the comment built from it, the prompt's welcome banner and the one-shot review banner (a test fails if a REPL command or a `review` option is missing from its banner), option parsing and help text, token budget accounting |
| `test_redact`, `test_replay` | Email redaction (patterns, nothing leaks to the model or to disk, can be turned off, the diff is not redacted) and parked comparator-replay helpers |
| `test_isolation_and_dry_run` | The checkout lives outside the run directory and is always deleted, the saved prompts equal what the model received, a defaults-only dry run matches equivalent review inputs and calls no model, and only an allow-list of PR fields is fetched from GitHub |
| `test_live_envelope` | Parsing of a recorded Claude result (`tests/fixtures/live_envelope_haiku.json`); no live call |
| `test_runner_plumbing` | `ClaudeRunner` against a fake `claude` executable: stdin, flags, working directory, timeout/process-tree cleanup, exit codes |
| `test_persona_criteria` | Static assignment coverage, unique/shared rule identities, reporting sections and shared-parser rendering/disabling |
| `test_host_execution` | Private run/cache creation, actual fake-native subprocess integration, invalid manifest handling and the actual one-shot host dispatcher, both adapters/strategies with simulated model transport, real temporary Git, credential-free preflight/dry-run, validation, secret exclusion and no VM-to-host fallback |
| `test_security_boundary` | invalid VM setup, unsupported diagnostics and parked replay refusal before target/model/credential activity, trusted config discovery/descriptor opens, and staged-byte artifact pinning |
| `test_connected_sandbox` | Checked archive failures, raw scan-IO budgets, bounded capsules without host Git/config/hooks, clean guest Git/export, frame limits, key-release ordering, artifact/key refusal, proxy/firewall policy and both guest flow report paths with fake API replies |
| `test_openai_backend` | Simulated Responses continuations, reasoning/tool items, costs/refusals/unknown usage/cancellation, bounded local read-tool authority, fixed HTTP endpoint and both full flows |
| `test_sandbox_prototype` | Stable bounded trusted-source bundles, artifact digests, no-network boot config, both real flows using fake headless processes, result framing, output limits, child-process timeout cleanup, fresh runtime-probe environments and rejection of unexpected authentication |
| `test_persona_parallel` | Overlapping Reviewer calls, concurrency limits, round barriers and ordered merges, isolated budget pools, proportional allocations, large-prompt delivery to slow stdin readers, and subprocess-group cancellation |
| `test_persona_flow` | Configured round-robin counts, independent frozen prompts, classification/fallback, source-aware agreement, conflicts/remapping, advisory verification/grounding, budgets/failures/cancellation, CLI/REPL and dry runs |
| `test_flow_selection` | Default/explicit mode selection, asset isolation, shared dependency boundaries and byte-identical relocated prompts/schemas/criteria |
| `test_tracing` | Closed metadata/privacy, disabled/preflight behavior, saturation, concurrent parentage, both flows, actual guest stats transfer, nonfatal export and optional SDK timestamp/usage mapping |
| `test_engine` | End to end with a throwaway git repository and a scripted `FakeRunner`: convergence, retries, failures, budget, criteria from the base branch, prompt-injection escaping, reviewer prompt properties (identical in every pass, thorough-review instructions), REPL commands, git helpers |

Things to know when writing tests:

- `tests/helpers.py` has `make_repo` (a base commit and a feature commit in a temp repo) and `FakeRunner`. `FakeRunner` tells the roles apart by phrases in their **system prompts** ("identify which files", "deduplicate code-review findings"). If you reword those phrases in the flow's prompts, update `FakeRunner`.
- Tests render the real prompt templates, so adding a `{{variable}}` to a prompt without supplying it in `roles.py` fails the tests.
- Nothing in the suite touches the network. Keep it that way; authenticated deployment checks need a qualified sandbox path and explicit user authorization.

## Common changes

| To... | Do this |
|---|---|
| Change what a role is asked | Edit the selected flow's `prompts/` ([prompt contracts](prompts.md) lists the contracts and variables). Prompt hashes are stored in each run's `config.json`, so runs stay comparable |
| Change output structure | Edit the flow's `prompts/schemas/`, `shared/findings.py` or the flow's `master.py`, and its report |
| Tune single-agent duplicate grouping | Edit its Comparator prompt or model and compare authorized before/after reviews. Comparator replay is parked; its offline helpers/tests remain, but its CLI performs no IO or model calls. |
| Add or change default rules | Edit the selected flow's `criteria/*.md` using ID-bearing headings (`GEN-*` for single-agent, persona-prefixed IDs for personas). Persona allocation also lives in `personas.json`; shared rules retain one canonical definition. Tests check identity and coverage |
| Add a setting | Add a field with a default to `Config` in `config.py` (the type is inferred from the default), validate it if needed, document it in `review.example.toml` and the [configuration reference](usage.md#configuration). For algorithm settings used in VM jobs, explicitly add the field to `sandbox/settings.py:CORE` and test round-trip transport; keep host paths, executables and credentials out of that allowlist |
| Add a progress message | Emit an event from the flow with `self.emit(...)`, handle it in `progress.py`. VM-visible events also need the closed projection/validation in `sandbox/progress.py` and protocol tests; raw event data must not be forwarded |
| Add an LLM role | Add templates/schemas and a role contract inside the owning flow; use shared `CallSpec`/`ModelCaller` for validated calls and wire the role in that flow's `flow.py`. Keep shared criteria discovery in `shared/resolver.py` |
| Change the stop rules | `flows/single_agent/stop_policy.py` and `tests/test_stop_policy.py` |

## Code conventions

- Core runtime uses the standard library; the optional host tracing extra requires Python 3.13. Use Python 3.13 syntax and standard-library APIs; start modules with `from __future__ import annotations`.
- LLMs propose, code decides: validate every model output in code and never execute it. All files are written by the harness.
- Modules do one job; keep functions short and named for what they do. Put new logic in the module that owns the concept and keep each flow's `flow.py` a readable list of steps. `engine.py` only selects and delegates.
- Keep prompts language-agnostic. Project- or language-specific guidance belongs in criteria, not in prompts.
- Add a test for every behavior change. Keep the README and [prompt contracts](prompts.md) in sync with changes to usage or prompts, and update [proposal.md](proposal.md) if a design decision changes.

## Debugging a review

Host run directories retain the detailed artifacts below. VM runs export only `report.md`, `summary.md` and `stats.json`; guest prompts/raw replies remain ephemeral. Do not expect host-only logs or `--keep-checkout` from a VM run. Useful host files:

| File | Question it answers |
|---|---|
| `pass-NN/reviewer.raw.json` | What did the selected adapter retain for every attempt, including safe errors? |
| `pass-NN/findings.json` | Which findings were kept or dropped, and why (`status` and `reason`)? |
| `pass-NN/comparator.raw.json`, `pass-NN/mapping.json` | How were findings matched to earlier ones? `mapping.json` has each decision and `assigned`: which master finding each new finding was merged into |
| `input/personas/<persona>/reviewer.*.md`, `settings.json` | Persona mode: exact frozen prompts, hashes, model/schema/tool settings |
| `verification/C-*/verifier.raw.*.json` | Persona mode: conditional verification inputs/replies; annotations also live in `master.json` |
| `input/reviewer.system.md`, `input/reviewer.user.md`, `input/pr.json` | Single-agent frozen prompts; both modes save PR metadata after configured email redaction |
| `input/diff.txt`, `input/criteria_block.md`, `input/criteria_index.json` | The diff and criteria the Reviewer was given |
| `input/criteria_manifest.json`, `input/resolver.raw.json` | Which project criteria files were chosen, and what was ignored |
| `*.raw.requests.json` (persona mode) | Exact request text/schema/settings for each actual attempt, including validation-retry feedback. Reviewer logs stay outside the checkout |
| `config.json` | Settings, selected adapter version, prompt hashes |
| `stats.json` | Every call with tokens, cost, duration; API HTTP failures also include safe codes, limit/reset headers and retry decisions |

For an explicitly selected host review, re-run with `--no-sandbox --keep-checkout` to keep the exact checkout the Reviewer saw (its location is printed).

## Verifying against the real Claude CLI

The offline tests cannot prove how Claude Code behaves. Use the implemented normal VM path with explicit authorization after Claude Code upgrades, and check the runtime behavior tracked by the [qualification checklist](proposal.md#checklist): flags accepted together, isolation canaries, JSON envelope fields, budget and timeout behavior. These consume API/subscription allowance; agents need the user's request before running them. If the envelope shape differs from what `backends.claude.parse_envelope` expects, fix the parser and add a test using the real shape.

## VM deployment checks

The [sandbox runbook](../sandbox/README.md) separates ordinary offline unit tests from explicit Linux/KVM deployment checks. No VM boots, infrastructure changes or paid calls are part of `unittest discover`. The [architecture](sandbox-architecture.md) describes the one-VM-per-review boundary and enforced controls.

The [VM architecture](sandbox-architecture.md) separates actual reviews from synthetic automated tests/canaries. Actual review input needs no authored sample project. Keep target code, identifiers, keys and findings out of fixtures. Routine documentation checks do not boot a VM or authorize a live call; deployment evidence is recorded in the [VM checklist](proposal.md#qualification-evidence).

The [connected runbook](sandbox-architecture.md) separates root Linux infrastructure checks from the standard test suite. Tests never install tools, alter host networking, boot a VM, read a real key or call a model. Deployment checks have exercised actual proxy/firewall/VM startup, no-key denial probes, cancellation before key release and short-deadline cleanup.

Use the repository-owned [sandbox check command](sandbox-check.md) for repeatable Mac deployment checks . Its unit regressions are `tests/test_sandbox_check.py`; ordinary unittest discovery never starts Lima. Real deployment checks build the current source, run offline flow and connected no-key workloads sequentially, and stop the dedicated worker even on handled failure. The tool does not provision a missing worker or download runtime artifacts.

`tests/test_vm_confinement.py` covers the shared jailer/systemd command, partial-launch cleanup, interruption, refusal of unconfirmed shutdown, host observation of confinement, snapshot setup and both flows reusing an externally managed checkout without a second export/deletion. Unit tests mock mounts/UID changes/systemd; actual native/host observations belong to the separately authorized no-key VM check. A guest probe result alone is not host containment evidence.

`tests/test_sandbox_preparation.py` covers image reuse/invalidation, corrupt-cache refusal, partial-build/OS-update failures, runtime fingerprints, cache result paths and scoped recovery. Ordinary unittest discovery never invokes real recovery or SIGKILL. The actual `sandbox check --lifecycle` deployment check intentionally cancels a no-key VM and kills a supervisor subprocess, observes the independent 30-second service deadline, then cleans up. The launcher uses immediate cgroup kill and mountinfo-based detection. Repeat unchanged checks to confirm image-cache hits; never infer enforcement from a faster run alone.

Public acquisition regressions in `tests/test_sandbox_acquisition.py` use synthetic metadata and local throwaway Git, never GitHub or a model. They cover strict URLs/pins, bounded metadata/storage, credential-free transport options, helper reaping, unprivileged clean setup, real tree admission/export, one-way network phases and refusal before credential/result release. `sandbox check --pr` is a separate opt-in real deployment check, not part of the automated suite; it fetches only inside the review VM and performs no inference.

`tests/test_openai_http_errors.py` covers bounded error-body parsing without raw-body logging, closed error codes, validated headers, seconds/date retry hints, same-request recovery across both flows/tool turns, retry caps without outer-loop multiplication, non-retryable quota/authentication, deadline deferral and cancellation. Transport and waits are simulated; these tests use no credentials or network. See [API errors and retries](usage.md#api-errors-and-retries).

## LLM adapters

Review algorithms and shared call/accounting code import `LLMResult`, `RunnerError` and the `Runner` protocol from `backends/contract.py`, not a native CLI module. Both host and guest create adapters through `backends.factory`; use an explicitly supplied private credential in the guest, not a read of ambient host auth. `auth_mode` is independent of provider/location. See [adapter support and authentication](llm.md).

`tests/test_llm_interface.py` checks neutral dependency direction, selected-only credentials, API/subscription isolation, unsupported-combination refusal before credential access, CLI independence, VM roundtrip mismatch rejection and old-setting migration. Existing both-flow transport/native tests cover common factory wiring. Parked frontend tests cover retained inactive helpers. Canonical prompt/schema/criteria hashes still guard their unchanged bytes.

`tests/test_chatgpt.py` verifies real synthetic RSA signatures with the system OpenSSL binary, OAuth callback state/PKCE, scope and identity checks, private atomic storage, serialized rotating refresh, terminal/temporary errors, SSE completion/failure, namespaced read-tool continuation, token pools and both one-shot flows. Authentication/model transports are simulated; no real account state is read and no network/inference occurs.
