# Reading the reviewer code

A walkthrough for a Java developer learning this Python project. The runnable product is a one-shot CLI; the parked REPL is not the entry point. Start with the [architecture](architecture.md) for boundaries and [LLM guide](llm.md) for provider/authentication support.

## Complete review call chain

Start with `reviewer review <PR URL>`. The installed `reviewer` entry point in [pyproject.toml](../pyproject.toml) calls `code_reviewer.cli.main()`. The diagrams below expand that path in layers: dispatch, the VM boundary, the selected review algorithm and each model invocation. Labels name actual functions or methods; arrows show execution order, including returns where marked. They omit standard-library internals and small parsing/formatting helpers.

### 1. CLI, execution selection and final output

```mermaid
flowchart TD
    START["reviewer review PR"] --> CLI["cli.main() → build_parser()"]
    CLI --> CONFIG["config.load_config()"]
    CONFIG --> OPTIONS["git_ops.parse_pr_ref()<br/>review_options.apply_run_options()"]
    OPTIONS --> DISPATCH["review_flow.run_review()"]
    DISPATCH --> READY["security.readiness()"]
    READY --> PREFLIGHT{"--preflight?"}
    PREFLIGHT -->|yes| OFFLINE["Return configuration report<br/>cli.main() prints JSON; exit"]
    PREFLIGHT -->|no| GATE["Validate effective execution/provider settings"]
    GATE --> EXECUTE["tracing.review.review_trace() wrapper<br/>review_flow._execute_review()"]
    EXECUTE --> LOCATION{"cfg.sandbox?"}
    LOCATION -->|yes: default| VM["sandbox.review.run()<br/>Expand in diagram 2"]
    LOCATION -->|no: explicit host| DRY{"--dry-run?"}
    DRY -->|no| ADAPTER["backends.factory.create_runner()"]
    DRY -->|yes: no credential| ENGINE["Engine.__init__() → flows.create_flow()"]
    ADAPTER --> ENGINE
    ENGINE --> PREPARE["Engine.prepare() → selected flow.prepare()<br/>pr_source.load_pr_context()"]
    PREPARE --> HOSTINPUT["gh_pr_view() → _object_store()<br/>fetch_pr_refs() → merge_base()<br/>commit_messages() → PRContext"]
    HOSTINPUT --> KIND{"--dry-run?"}
    KIND -->|yes| PREVIEW["Engine.dry_run() → selected flow.dry_run()<br/>presentation.render_dry_run(); exit"]
    KIND -->|no| REVIEW["Engine.review() → selected flow.review()<br/>Expand in diagram 3"]
    REVIEW --> OUTCOME["Return ReviewOutcome.result"]
    VM --> OUTCOME
    OUTCOME --> TRACE["If tracing: tracing.review.finish()<br/>validate guest metadata, save stats,<br/>bounded host export to localhost:4000"]
    TRACE --> DISPLAY["cli.main()<br/>presentation.render_summary() + run_line()<br/>print report_path"]
    DISPLAY --> POST{"--post?"}
    POST -->|yes| GITHUB["post.format_comment() → post.post_comment()"]
    POST -->|no| EXIT["Return CLI exit status"]
    GITHUB --> EXIT
```

`_object_store()` selects a matching local clone or calls `git_ops.ensure_cache_clone()` for a cached bare clone. Fetches pin base/head into private refs; they do not switch the operator's working branch. A VM result is an `ExportedReview`: presentation uses its exported summary rather than reconstructing the guest's master list. Configuration errors stop before acquisition or credential access; VM failures have no edge to host execution.

### 2. VM execution and process boundaries

```mermaid
sequenceDiagram
    participant CLI as macOS: sandbox.review
    participant Worker as Lima Linux: review_worker / connected
    participant VM as Firecracker: guest
    participant Core as Selected flow + adapter (inside Firecracker)
    CLI->>CLI: run() → configuration_errors() → runtime_inputs()
    CLI->>CLI: stage_inputs() → build_bundle()
    CLI->>Worker: Worker.session() starts dedicated worker
    CLI->>Worker: prepare_worker() stages verified runtime / selects cached image
    CLI->>Worker: execute() launches review_worker.main() over private SSH pipes
    CLI->>Worker: converse(): send allowlisted settings + PR URL
    Worker->>Worker: dispatch() → connected.run()
    Worker->>Worker: Network.__enter__() → VirtualMachine.__enter__()
    Worker->>Worker: connect_bootstrap() → vm.confinement()
    Worker->>VM: connected.exchange(): settings + acquisition request over vsock
    VM->>VM: guest.main() → run_job() → validate_settings()
    Note over VM: Optional guest Recorder wraps _run_job()
    VM->>VM: acquire.in_guest() pins PR commits
    VM->>Worker: Send acquired identity, wait for source-access revocation
    Worker->>Worker: Network.inference()
    Worker->>VM: Acknowledge source-access revocation
    VM->>VM: snapshot.prepare() seals code / drops privileges, boundary probes
    VM->>Worker: Send ready checks
    Worker->>Worker: connected.ready() validates checks
    Worker->>CLI: dispatch.read_key() requests selected credential
    CLI->>CLI: converse() calls key_reader only now
    CLI->>Worker: Send selected credential through private SSH pipe
    Worker->>VM: Forward credential through vsock
    VM->>Core: factory.create_runner() → Engine(...).review(ctx, prepared_checkout=snapshot)
    Note over Core: Diagram 3, then diagram 4 for each model call
    Core-->>VM: RunResult with report, summary and stats
    VM->>VM: _run_job(): collect_artifacts() bounds / validates export
    VM->>VM: run_job(): close Recorder, attach bounded metadata if enabled
    VM-->>Worker: Result envelope + allowed artifacts
    Worker->>Worker: export_result(), VirtualMachine / Network context cleanup
    Worker->>Worker: dispatch() → verify_idle() → collect_artifacts()
    Worker-->>CLI: Finished envelope confirms cleanup
    CLI->>CLI: converse() validates pins / checks, remote scratch cleanup
    CLI->>Worker: Worker.session() shuts dedicated worker down
    CLI->>CLI: export_result() → ExportedReview
```

The guest receives an already pinned `PRContext` from acquisition and calls `Engine.review()` directly: it does **not** call host `Engine.prepare()` again. Code acquisition uses temporary source-network access; model work uses the inference-only policy. The guest adapter shares the same flow implementations as the host adapter. See [sandbox architecture](sandbox-architecture.md) for enforcement details and failure/cleanup boundaries.

### 3. The two review algorithms

Both begin with `InputBuilder.redact()` and `InputBuilder.load_diff()`, which parse the merge-base diff, record binary omissions, apply exclusions and enforce diff limits. `RunStore.create()` owns artifacts. Host flows use `RunStore.prepare_checkout()` and `git_ops.export_tree()`; the VM supplies its sealed checkout instead.

`InputBuilder.resolve_criteria()` loads defaults and, unless disabled, calls `_resolve_project_criteria()` → `criteria.resolve_project()`. That function may invoke `CriteriaResolver.__call__()` → `ModelCaller.call()` to select project criteria from the pinned base. `criteria.assemble()` produces the effective rules. Discovery can avoid model calls when no resolution is needed or an explicit manifest supplies the selection.

```mermaid
flowchart TD
    SELECT["Engine.review()"] --> MODE{"flows.create_flow() selected"}
    MODE --> SINGLE["SingleAgentFlow.review()"]
    MODE --> PERSONAS["AgentPersonasFlow.review()"]

    subgraph Single["single-agent"]
        SINGLE --> SI["InputBuilder + RunStore preparation<br/>resolve_criteria() → review_input()"]
        SI --> SP["_run_passes() → StopPolicy.before_pass()"]
        SP --> PASS["PassRunner.run()"]
        PASS --> SR["_review() → Reviewer.review()<br/>ModelCaller.call(): diagram 4"]
        SR --> SG["FindingsPreparer.prepare()<br/>normalize, scope, self-check, ground evidence"]
        SG --> SC["_compare() → MasterList.candidates()<br/>Comparator.is_trivial()? all_new() : compare()"]
        SC --> SM["single_agent.MasterList.apply() → PassRunner._record()"]
        SM --> SS{"StopPolicy.after_success() / after_failure()<br/>More passes allowed?"}
        SS -->|yes| SP
        SS -->|no| SO["RunStore.save_outputs()<br/>report.render_report() + summary.render_summary()<br/>save_binary_omissions()"]
        SO --> SF["finally: cleanup_checkout() unless explicitly kept<br/>Return RunResult"]
    end

    subgraph Personas["agent-personas"]
        PERSONAS --> PI["InputBuilder + RunStore preparation<br/>Budget, ParallelRunner, PersonaCaller, Roles<br/>resolve_criteria()"]
        PI --> ROUTE["Roles.classify() → Catalog.allocate()<br/>_freeze() → freeze() + save_frozen()"]
        ROUTE --> ROUNDS["_passes(): ThreadPoolExecutor.submit()<br/>Roles.review() once per eligible persona this round"]
        ROUNDS --> BARRIER["as_completed(): collect all results<br/>Barrier before consolidation"]
        BARRIER --> MERGE["_consolidate() in fixed persona order<br/>FindingsPreparer.prepare() → Roles.compare()<br/>agent_personas.MasterList.apply() on staged copy"]
        MERGE --> NEXT{"More configured rounds?"}
        NEXT -->|yes| ROUNDS
        NEXT -->|no| JOIN["finally: cancel signal + executor.shutdown(wait=True)<br/>Join all workers"]
        JOIN --> VERIFY["_verify() → Roles.verify() for flagged conflicts<br/>Advisory annotation; no vote/severity suppression"]
        VERIFY --> PO["finally: RunStore.save_inputs() + save_outputs()<br/>report.render_report() + render_summary()<br/>save_binary_omissions()"]
        PO --> PF["finally: cleanup_checkout() unless explicitly kept<br/>Return RunResult"]
    end
```

The persona loop disables a persona after its own budget/failure limit while other personas continue. Global stop/cancellation can terminate the loop early; workers are joined before checkout cleanup. Verification runs after a completed/incomplete loop, only for conflict groups. The single-agent loop can stop on convergence; persona mode uses the configured number of rounds. Exceptions follow each flow's `except`/`finally` blocks rather than every normal-path arrow above.

### 4. Every model call, tools and validation

```mermaid
flowchart TD
    ROLE["Reviewer.review() / Comparator.compare()<br/>CriteriaResolver.__call__() / persona Roles.call()"] --> SPEC["Build CallSpec + role-specific validator"]
    SPEC --> PC{"Persona flow?"}
    PC -->|yes| PERSONA["PersonaCaller.call()<br/>select pool, validate prompt bounds, log request"]
    PC -->|no| CALL["ModelCaller.call() → _attempts()"]
    PERSONA --> CALL
    CALL --> GUARD{"Persona flow?"}
    GUARD -->|yes| BUDGET["BudgetRunner.run()<br/>Budget.check() → reserve()<br/>ParallelRunner.run() forwards cancellation"]
    GUARD -->|no| RUNNER{"Selected Runner"}
    BUDGET --> RUNNER
    RUNNER -->|Claude| CLAUDE["ClaudeRunner.run() → run_cancellable()<br/>build_command() → managed native process<br/>parse_envelope()"]
    RUNNER -->|OpenAI| OPENAI["OpenAIRunner.run() → run_cancellable()<br/>_request() → request_response()"]
    OPENAI --> TOOL{"Response contains permitted tool calls?"}
    TOOL -->|yes| FILE["ReadOnlyTools.execute()<br/>bounded Read / Grep / Glob in checkout"]
    FILE --> CONTINUE["Append tool output to history<br/>next bounded response turn"]
    CONTINUE --> OPENAI
    TOOL -->|no| RESULT["LLMResult"]
    CLAUDE --> RESULT
    RESULT --> SETTLE["Persona only: Budget.settle()<br/>ModelCaller: Stats.record() + raw log"]
    SETTLE --> VALIDATE["Run supplied output validator"]
    VALIDATE --> RETRY{"Invalid and retry permitted?"}
    RETRY -->|yes: at most one shared retry| CALL
    RETRY -->|no: valid| RETURN["Return validated LLMResult to role"]
    RETRY -->|no: failure| FAIL["Raise PassFailed"]
```

Claude's native CLI implements its permitted read tools; `ReadOnlyTools` is the Python implementation used by the OpenAI adapter. Reviewers and the persona Verifier receive file tools; Resolver, Classifier and Comparator do not. Adapter transport/parsing checks precede the role-specific validator. Finding grounding and deterministic master-list updates happen after that validator, in diagram 3. HTTP request retries live inside `OpenAIRunner._request()` and do not add another review pass.

### Following the diagrams in code

| Layer | Source entry points |
|---|---|
| Command and boundary selection | [cli.py](../src/code_reviewer/cli.py), [review_flow.py](../src/code_reviewer/review_flow.py), [security.py](../src/code_reviewer/security.py) |
| Host pinning and flow selection | [pr_source.py](../src/code_reviewer/shared/pr_source.py), [engine.py](../src/code_reviewer/engine.py), [flows/__init__.py](../src/code_reviewer/flows/__init__.py) |
| VM launcher → Linux supervisor → guest | [review.py](../src/code_reviewer/sandbox/review.py), [review_worker.py](../src/code_reviewer/sandbox/review_worker.py), [connected.py](../src/code_reviewer/sandbox/connected.py), [guest.py](../src/code_reviewer/sandbox/guest.py) |
| Single-agent orchestration and pass | [single_agent/flow.py](../src/code_reviewer/flows/single_agent/flow.py), [passes.py](../src/code_reviewer/flows/single_agent/passes.py), [roles.py](../src/code_reviewer/flows/single_agent/roles.py) |
| Persona orchestration and call guards | [agent_personas/flow.py](../src/code_reviewer/flows/agent_personas/flow.py), [roles.py](../src/code_reviewer/flows/agent_personas/roles.py), [caller.py](../src/code_reviewer/flows/agent_personas/caller.py), [runner.py](../src/code_reviewer/flows/agent_personas/runner.py) |
| Shared input, calls, evidence and storage | [review_inputs.py](../src/code_reviewer/shared/review_inputs.py), [llm.py](../src/code_reviewer/shared/llm.py), [findings.py](../src/code_reviewer/shared/findings.py), [run_store.py](../src/code_reviewer/shared/run_store.py) |
| Adapter creation and implementations | [factory.py](../src/code_reviewer/backends/factory.py), [claude.py](../src/code_reviewer/backends/claude.py), [openai.py](../src/code_reviewer/backends/openai.py), [read_tools.py](../src/code_reviewer/backends/read_tools.py) |

### Using this map to check for unused code

Trace reachability from `cli.main()` and the separately launched worker/guest `main()` functions. Search both direct calls and references passed as callbacks, then check tests and supported deployment commands before removing a symbol. A method absent from a literal `name()` search can still execute through `CriteriaResolver.__call__()`, `PersonaCaller` inheritance, `Engine.__getattr__()`, a context manager's `__enter__()`/`__exit__()`, an executor task or a subprocess module entry point.

The diagrams cover the review command. `sandbox check/recover` have separate supported entry paths; the retained `parked/` frontend/replay helpers are deliberately outside this path. Neither omission alone establishes dead code. Authentication is expanded below; offline preflight does not enter that path.

### 5. ChatGPT authentication within the call chain

`cli.main()` routes explicit `auth` commands to `backends.chatgpt.login.cli()`. These commands do not acquire a PR. Normal review dispatch enters credential handling only after validated configuration (host) or confirmed VM admission (guest workload).

```mermaid
flowchart TD
    AUTH["reviewer auth login"] --> LOGIN["login.cli() → login()"]
    LOGIN --> PREP["Store.prepare(runtime_key(cfg), account)<br/>persist stable runtime host ID"]
    PREP --> DISC["oidc.discovery() → authorize()<br/>loopback listener + browser + state/nonce/PKCE"]
    DISC --> CALLBACK["read_request() → callback()<br/>validate state and issued client ID"]
    CALLBACK --> EXCHANGE["oidc.request(): exchange authorization code<br/>token_record() → oidc.verify_id()<br/>system OpenSSL checks signed identity"]
    EXCHANGE --> SAVE["Store.install(): atomic private save"]
    REVIEW["Host factory.create_runner()<br/>or VM review.converse() credential gate"] --> ACCESS["factory.credential() → Store.access()<br/>select account + runtime; acquire refresh lock"]
    ACCESS --> EXPIRY{"Token covers bounded call/job?"}
    EXPIRY -->|no| REFRESH["oidc.request(): rotating refresh<br/>token_record() → Store.save()"]
    REFRESH --> TOKEN["Return access token only"]
    EXPIRY -->|yes| TOKEN
    TOKEN --> RUN["Host: OpenAIRunner.token_source before requests<br/>VM: private handoff → guest factory; no guest refresh"]
    RUN --> STREAM["request_response() → openai_stream.read_stream()<br/>collect finalized output_item.done items; require response.completed before accepting tools/output"]
```

`auth status` uses `Store.records()` without network/refresh. `auth models` calls `Store.access()` then `models.catalog()`. `auth logout` calls `Store.logout()` to attempt revocation and clear local tokens. Identity/refresh credentials stay on the host; host and VM registrations use separate runtime identities. See [LLM access](llm.md#chatgpt-sign-in) for exact limits and the outstanding live qualification.

### 6. Optional tracing across the VM boundary

`run_review()` validates configuration and handles preflight before calling the decorated `_execute_review()`. `tracing.review.review_trace()` creates a `tracing.Recorder` for real reviews by default; `tracing = false` opts out. `tracing.observation()` and `traced()` record closed metadata, not method arguments. `ModelCaller.call()` creates role agents; OpenAI records individual turns/tools, while Claude records aggregate native generations. Persona executor submissions use `copy_context().run` to preserve parentage.

```mermaid
flowchart LR
    ROOT["Host review_trace() → Recorder"] --> JOB["settings.job_settings()<br/>enable flag + trace/parent IDs"]
    JOB --> GUEST["guest.run_job() → activate guest Recorder<br/>_run_job() → same Engine/flow"]
    GUEST --> STATS["attach bounded snapshot to stats.json<br/>private result channel + cleanup"]
    STATS --> FINISH["Host finish()<br/>validate → merge → persist"]
    ROOT -->|"Host mode: no guest handoff"| FINISH
    FINISH --> CHILD["tracing.langfuse.export() subprocess<br/>send(): public OTel timestamps + Langfuse wrappers"]
    CHILD --> LOCAL["Local Langfuse :4000"]
```

The exporter is optional, host-only and deadline-bounded. It has no provider credentials or guest network route. `finish()` also runs on failure, preserving available host metadata; an unavailable exporter cannot replace a review exception or result. Preflight/dry-run bypass the recorder/export lifecycle. See [trace configuration and limits](tracing.md).

## Where to start reading

| Module | Question it answers |
|---|---|
| `cli.py`, `review_options.py` | Which commands/options exist and how are overrides applied? |
| `config.py`, `security.py`, `review_flow.py` | What is trusted configuration and when can target/credential activity begin? |
| `engine.py`, `flows/__init__.py` | Which review algorithm runs? |
| `flows/single_agent/flow.py` | What is the whole single-agent review sequence? |
| `flows/agent_personas/flow.py` | How do concurrent rounds, fixed-order merges and advisory verification work? |
| `shared/review_inputs.py`, `criteria.py`, `resolver.py` | How are PR input and project rules assembled? |
| `shared/diffview.py`, `findings.py` | How is a diff represented and evidence checked? |
| `backends/contract.py`, `factory.py` | What does any LLM adapter promise and how is it selected? |
| `backends/claude.py`, `openai.py` | How do native CLI and HTTPS calls satisfy that contract? |
| `shared/llm.py`, `stats.py`, `run_store.py` | Who validates replies, accounts attempts and writes artifacts? |
| `sandbox/review.py`, `review_worker.py`, `guest.py` | How does a normal review cross the VM boundary? |
| `sandbox/vm.py`, `network.py`, `snapshot.py` | Which OS/network controls contain the job? |
| `parked/` | Retained inactive frontend/replay helpers; no normal CLI dependency |

Tests and callers import the module that owns the implementation. `Engine` remains a small facade over one selected flow; it does not contain a second copy of either algorithm.

## Python concepts used here

### Dataclasses are ordinary data objects

`PRContext`, `CallSpec` and `LLMResult` use `@dataclass`. Like a Java record or a small POJO, their fields describe data. Python generates the constructor and useful representations. `field(default_factory=list)` gives each instance its own list; a shared mutable default would leak state between runs.

`dataclasses.replace(cfg, ...)` creates an option-adjusted copy. It prevents one review's CLI overrides from mutating configuration reused elsewhere. Paths use `pathlib.Path` rather than scattered string concatenation.

### A protocol describes the dependency

`Runner` is a `typing.Protocol`: code uses the runner's supported methods without depending on a concrete provider class. It is a static structural contract, not runtime validation. Claude and OpenAI adapters return `LLMResult`; FakeRunners in tests use the same result type. Runtime reply/schema/evidence checks remain mandatory.

The interface carries system/user text, schema, model, allowed tools, cwd, estimated allowance and timeout. It returns structured output or failure, token/cost metadata, duration, retryability and billing mode. The adapter does not decide review criteria, merges, votes or posting.

### Functions and callbacks connect the pieces

The harness passes event sinks, validators, transport functions and fixture runners as ordinary function values. This resembles injecting Java functional interfaces. `emit(Event(...))` is progress output, not the review state. The master list, pass records and validators determine the result.

Dependency injection keeps tests offline: a fixture can supply scripted provider responses without reading credentials, networking or starting a VM. Boundary tests exercise the real dispatcher with controlled infrastructure fakes rather than bypassing the boundary through a presentation seam.

### Exceptions describe failure boundaries

- `ConfigError`: invalid trusted operator settings or unsupported access selection.
- `RunnerError`: adapter startup/use failure; callers never choose a fallback provider.
- `PassFailed`: a call could not produce validated output after permitted retries.
- `ReviewRefused`: no acceptable review input or unsupported execution request.
- Persona `RunStopped`: a particular pool/global limit prevents more work.

`try/finally` ensures cleanup even when these exceptions or cancellation interrupt execution. Context managers (`with`) close files/sockets/temporary state at the boundary they own. Never assume a temporary directory itself provides OS isolation.

### Concurrency is bounded work, not extra agents created dynamically

Persona mode uses a `ThreadPoolExecutor`: each round dispatches at most `persona_parallel` calls. Each active reviewer has separate caller/log state and an independent budget pool. Shared accounting/reservations are protected by locks. The harness waits for the round, merges in fixed order and only then starts another round. Cancellation reaches native process groups or API retry waits; join work before removing its checkout.

## Input and criteria

Each run pins exact base/head/merge-base commits. Criteria come from the base tip so a PR cannot edit its own rules. The merge-base-to-head diff defines changed scope. Lockfiles/generated/configured exclusions are named but not scored. Binary detection uses an 8000-byte NUL/invalid-UTF-8 prefix heuristic; omitted contents are not certified safe. Reports/stats include omission metadata.

The VM fetches and parses public input unprivileged, applies text/count/object limits, exports text blobs, revokes source networking and seals code read-only before model work. Host acquisition reuses Git/GitHub access and exports a separate checkout. No target build/test/install command is run by either review flow.

## One model call and one pass

`ModelCaller` supplies a role's `CallSpec` to the selected `Runner`, records attempts and validates the result. An invalid structured answer can receive one feedback retry. OpenAI temporary HTTP retries repeat the same request with a deadline and server/backoff delay; they do not restart the whole tool conversation or multiply through the output retry. Safe codes/headers survive; raw error bodies do not.

The Reviewer proposes findings. `FindingsPreparer` validates shape/rule references and grounds quotes/anchors in admitted code/diff. A Comparator proposes duplicate mappings; deterministic code updates the master. Single-agent stopping uses successful completions and new findings. Persona stopping uses configured rounds and independent pools; flagged conflicts may receive advisory verification. Agreement measures repeated sightings, not truth.

## Storage and billing

`RunStore` owns private files and a separate temporary checkout. Host runs retain prompts, replies and mappings; normal VM runs export only bounded report/summary/stats after confirmed cleanup. Internal artifact paths in a VM report refer to destroyed guest files. `--post` explicitly publishes the selected summary through the operator's GitHub CLI.

API keys and subscription credentials are separate access modes. The common factory selects exactly one and strips unselected native credentials. API-key calls use estimated dollar allowances. Subscription calls use token allowances and report API cost as N/A. Unknown/incomplete subscription usage consumes the reserved allowance conservatively. Neither is a hard provider cap.

## What is unfinished

Both providers support explicit API-key/subscription access. ChatGPT-backed VM review is user-confirmed. Native Claude read-tool behavior, live persona coverage and long-running token refresh still need qualification. Optional metadata tracing is implemented in `tracing/`. REPL/replay restoration, LSP, context packs and full-content tracing remain later work.

## Development checks

Use Python 3.9+ with the standard library and `tomli` on older Python. Every module starts with `from __future__ import annotations`; avoid `match` and runtime `X | Y` types. Run the full suite on the oldest and newest available supported Python. Unit tests use temporary synthetic repositories, fake processes and simulated responses; they never read real keys or boot VMs. See [development](development.md), [the VM checklist](sandbox-todo.md) and [the current plan](proposal.md#checklist).
