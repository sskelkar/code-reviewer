# Development

Use Python 3.13 and the [README setup](../README.md#quick-start). The core runtime uses the standard library. Optional host tracing uses `langfuse==5.1.0`; no other runtime or test dependencies are required.

## Tests

```bash
python -m unittest discover -s tests -t .
python -m unittest tests.test_persona_flow tests.test_persona_parallel
```

Tests use synthetic repositories, FakeRunners, fake native executables and simulated HTTP/SSE. They must not read real credentials, make model/network calls or boot VMs. Run the whole suite before finishing a change. The repository has no CI workflow; run these checks locally.

| Tests | Coverage |
|---|---|
| `test_persona_flow`, `test_persona_parallel`, `test_persona_criteria` | Criteria distribution, fixed inputs, concurrent rounds, ordered consolidation, per-persona agreement, conflicts, cancellation, independent budgets and category-grouped reports |
| `test_findings`, `test_diffview`, `test_criteria`, `test_binary_omissions` | Grounding, scope, malformed outputs, repository criteria and binary handling |
| `test_host_execution`, `test_isolation_and_dry_run`, `test_redact` | Real temporary Git, both adapters, saved persona inputs, checkout lifecycle, privacy and credential-free dry-run |
| `test_native_claude_login`, `test_runner_plumbing`, `test_claude_cli_and_templates`, `test_live_envelope` | Native login environment, subprocess flags/cleanup, templates and recorded JSON envelope parsing |
| `test_openai_backend`, `test_openai_http_errors`, `test_chatgpt` | Transport, bounded read tools, safe retry diagnostics, streaming completion, protected OAuth storage and refresh |
| `test_security_boundary`, `test_vm_review`, `test_connected_sandbox`, `test_sandbox_acquisition` | Actual frontdoors/admission, credential gates, bounded private frames, fixed egress and no VM fallback |
| `test_sandbox_prototype`, `test_sandbox_check`, `test_sandbox_preparation`, `test_vm_confinement` | Synthetic runtime/protocol checks, artifact fingerprints, clean image reuse, confinement and cleanup logic |
| `test_tracing`, `test_live_progress` | Metadata privacy/parentage/usage, export failures, closed progress schemas, early count updates and terminal safety |
| `test_verification` | Evidence references, absence/contradiction handling, remedy support, private audit and supported-only publication |
| `test_options`, `test_review_banner`, `test_welcome`, `test_stats`, `test_llm_interface` | CLI/configuration, retained parked frontend, accounting and provider-neutral ownership |

Boundary tests must exercise real admission rather than use `tests.helpers.synthetic_frontend`, which is only for presentation tests. Preserving coverage means porting shared behavior when implementation ownership changes, not replacing boundary assertions with test-only success paths.

## Configuration and execution checks

Bare `reviewer` prints help without loading configuration. `doctor` and `review --preflight` are offline configuration reports: no artifact, worker, credential, target or model activity. `runtime_checked: false` is not deployment certification. Host dry-run acquires a pinned diff and saves defaults-only persona prompts without model credentials, calls or a checkout.

Operator TOML belongs outside Git, normally `~/.code-reviewer/review.toml`, mode 0600. Unknown fields fail validation. The one-shot CLI is the public interface; the retained REPL under `parked/` is inactive.

## Changing code

| Change | Required wiring |
|---|---|
| Review scheduling | `review/flow.py`; preserve round barriers, deterministic consolidation and joined workers |
| Role or prompt | `review/roles.py`, `prompt_contract.py`, `prompts/`; use validated `CallSpec`/`PersonaCaller` |
| Criteria | `review/criteria/`, `personas.json`; retain canonical identity and complete rule coverage |
| Settings | `Config`, validation, example TOML and usage. Algorithm settings crossing the VM also require `sandbox/settings.py:CORE` and round-trip tests; infrastructure paths and credentials stay out |
| Progress | Engine event, `progress.py`, closed `sandbox/progress.py` projection/validation and protocol tests |
| Reports | `review/report.py`; preserve sightings and categories; enforce the shared supported-only publication predicate |
| Adapter | `backends/contract.py` and factory; no implicit provider, model, billing or execution fallback |

Canonical prompt/schema/criteria changes require separately authorized before/after comparisons under [the comparison policy](proposal.md#review-quality-experiments). Module moves that preserve their bytes do not constitute prompt changes.

## Debugging

Host runs retain `input/personas/` frozen prompts, accepted criteria, actual per-attempt requests/replies, `pass-*/slot.json`, mappings and candidate assessments. The VM exports `report.md`, `summary.md`, `stats.json` and private `findings.json`; internal artifact paths refer to destroyed guest data. Keep private code, identifiers, findings and credentials out of fixtures, issues and Git.

Use explicit host `--keep-checkout` to inspect an exported head snapshot. It has read-only model tools, not a read-only filesystem mount. No target tests/builds/hooks execute. Read [reports and omissions](usage.md#reading-the-report) and [tracing](tracing.md) before interpreting zero findings, failed calls or missing traces.

## Verifying against the real Claude CLI

Offline tests do not establish native authentication entitlement, tools or review quality. Real inference requires explicit authorization for the target and provider. Qualify flags, envelopes, read-tool boundaries, usage/budgets and cleanup through the selected supported execution path. Outstanding evidence belongs in [the roadmap](proposal.md#qualification-evidence).

## VM deployment checks

Follow [sandbox setup/checks/recovery](sandbox-check.md). `reviewer sandbox check` exercises all four perspectives with simulated replies and checks boundaries/cleanup without provider credentials. `--lifecycle` also deliberately interrupts owned workloads and tests supervisor loss. These are separate deployment operations, never ordinary unittest side effects.

Clean image caches contain no target code, credentials or findings. Source, builder, native artifact and OS/Python runtime changes invalidate cache identities; corrupt cache entries fail closed. See [VM architecture](sandbox-architecture.md) for private transports and external network policy.
