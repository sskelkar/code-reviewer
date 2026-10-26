# Architecture

The [README](../README.md#review-flows) explains both flows at a glance. This guide maps them to the implementation and expands the single-agent path.

## Ownership

`Engine` selects one flow before PR preparation and asset loading. Each flow owns orchestration, model contracts, master/result types and presentation. `shared/` owns transport, git/diff handling, criteria discovery, grounding, storage and accounting; it imports neither flow. Frontends dispatch rendering through `presentation.py`.

Only Reviewers and the persona Verifier receive Read/Grep/Glob inside the temporary head checkout. Resolver, Classifier and Comparator calls have no code tools. For normal CLI reviews, every call is a fresh headless Claude session. The connected pilot injects an OpenAI runner with fresh per-call conversation state instead. Models propose; Python validates, accounts and writes state.

## Sandbox deployment

The [security contract](security-contract.md) governs the final product for every repository/provider. Existing components below are not yet production-qualified, and temporary personal-pilot launch scripts are withdrawn.

The [sandbox architecture](sandbox-architecture.md) wraps the whole job in one disposable Firecracker VM. The [connected pilot](sandbox-connected.md) is a separate Linux-only entry point: `capsule` prepares bounded Git objects without host Git execution; `connected` installs external network policy, launches the VM and transfers inputs privately; `guest` reconstructs pinned commits and injects `OpenAIRunner` into the selected `Engine` flow. Only after admission and boundary checks does the host request and deliver the disposable key. Fixed bounded report files return over vsock. The regular CLI still constructs `ClaudeRunner` and does not select the sandbox.

Three separate offline workloads exercise fake-Claude review, simulated-OpenAI review, and real-Claude startup/auth refusal. No-key connected deployment checks also pass; actual authenticated inference and personal-repository compatibility remain pending. Production jailer, immutable snapshot, guest execution/syscall policy and authenticated Claude qualification remain future gates. See the [offline runbook](../sandbox/README.md) and connected runbook for their distinct interfaces.

## Code map

Start with [flows/single_agent/flow.py](../src/code_reviewer/flows/single_agent/flow.py): `SingleAgentFlow.review` is the composition root. [engine.py](../src/code_reviewer/engine.py) only delegates to the selected flow.

| Location | Responsibility |
|---|---|
| `engine.py`, `flows/__init__.py` | Stable API and one-time mode selection |
| `flows/single_agent/flow.py` | Complete current review sequence and pass loop |
| `flows/single_agent/passes.py`, `stop_policy.py` | One pass and convergence/failure/budget rules |
| `flows/single_agent/roles.py`, `prompt_contract.py`, `prompts/` | Reviewer/Comparator contracts, role-specific loading, templates and schemas |
| `flows/single_agent/master.py`, `models.py` | Merged sightings, comparator validation, agreement and flow result |
| `flows/single_agent/report.py`, `summary.py`, `dry_run.py` | Detailed report, shareable summary and dry-run presentation |
| `flows/single_agent/criteria/` | Existing ten defaults, unchanged |
| `flows/agent_personas/` | Parallel review rounds, ordered serial consolidation, proportional budget pools, rule distribution, own role contracts/master/result/report, per-call budget guard and conflict-only advisory verification |
| `shared/review_inputs.py`, `criteria.py`, `resolver.py` | PR redaction/diff inputs, criteria parsing/discovery and shared Resolver role |
| `shared/claude_cli.py`, `llm.py`, `templates.py` | Claude subprocesses/retries, accounting/logging and generic template rendering |
| `shared/git_ops.py`, `pr_source.py`, `diffview.py`, `findings.py` | Pinned PR input, diff parsing/exclusions and evidence validation |
| `shared/run_store.py`, `stats.py`, `events.py`, `models.py`, `redact.py` | Storage/checkout lifecycle, usage, events, common data and email redaction |
| `cli.py`, `repl.py`, `review_flow.py`, `presentation.py`, `review_options.py`, `repl_help.py` | User entry points, common launch sequence, options and help |
| `progress.py`, `post.py`, `doctor.py`, `replay.py` | Progress, confirmed posting, environment checks and Comparator replay |
| `backends/openai.py`, `backends/read_tools.py` | Optional stdlib Responses adapter and bounded descriptor-based Read/Grep/Glob; injected only by separate sandbox workloads |
| `sandbox/connected.py`, `guest.py`, `wire.py` | Linux supervisor, guest review entry point and bounded private bootstrap/result protocol |
| `sandbox/capsule.py`, `git_input.py`, `network.py`, `probes.py` | File-only target transfer, in-guest Git admission/export, external network enforcement and harmless probes |
| `sandbox/bundle.py`, `image.py`, `launch.py`, `*_smoke.py`, `runtime_probe.py` | Trusted image inputs/build stages and separate offline deployment checks |
| Legacy package-root modules | Small compatibility exports; edit canonical flow/shared files instead |

Shared code imports no workflow. Flow-owned result types stay inside the flow; `code_reviewer.models.RunResult` remains a compatibility export of the single-agent result.

## Single-agent implementation

The flow below follows one `reviewer review <pr>` (or `review` inside the interactive session) step by step, with the module that does each.

```mermaid
flowchart TD
    U["<b>You</b><br/>reviewer review PR<br/><i>cli.py or repl.py</i>"] --> RF["<b>review_flow.run_review</b><br/>shared by CLI and REPL"]
    RF --> SELECT["<b>Engine</b><br/>select single-agent"]
    SELECT --> PREP["<b>SingleAgentFlow.prepare</b> (pr_source, git_ops)<br/>gh + git: fetch PR head and base,<br/>pin exact commit SHAs"]
    PREP --> RED["<b>InputBuilder.redact</b><br/>replace email addresses in PR text"]
    RED --> DIFF["<b>InputBuilder.load_diff</b> (diffview)<br/>merge-base diff, drop lockfiles and generated files,<br/>refuse empty or oversized PRs"]
    DIFF --> CO["<b>RunStore + git_ops.export_tree</b><br/>read-only copy of the PR head<br/>in a temporary directory"]
    CO --> CRIT

    subgraph CRIT["Criteria: InputBuilder.resolve_criteria (criteria.py)"]
        direction TB
        C1["Load the 10 default criteria<br/>src/code_reviewer/flows/single_agent/criteria/general.md"]
        C2["Read root AGENTS.md from the BASE commit<br/>extract in-repo paths and links it mentions"]
        C3["<b>Criteria Resolver</b> (model, no tools)<br/>which of those files hold review rules?"]
        C4["Validate: regular in-repo files only<br/>split into one rule per heading, assign rule IDs"]
        C5["Add by location, no model:<br/>nested AGENTS.md near changed files,<br/>.claude/rules files that apply"]
        C6["Merge: project rules win over defaults<br/>size cap"]
        C1 --> C6
        C2 --> C3 --> C4 --> C6
        C5 --> C6
    end

    CRIT --> SAVE["<b>RunStore.save_inputs / save_prompts</b><br/>saves exactly what the Reviewer will see"]
    SAVE --> LOOP

    subgraph LOOP["Pass loop: SingleAgentFlow._run_passes"]
        direction TB
        SP1["<b>StopPolicy.before_pass</b><br/>budget, max passes, failures?"]
        PASS["<b>PassRunner.run</b><br/>one pass, see next diagram"]
        SP2["<b>StopPolicy.after_success</b><br/>stop after K quiet passes<br/>(defaults: min 3 completions,<br/>max 6 scheduled attempts)"]
        SP1 -->|continue| PASS --> SP2 -->|more passes| SP1
    end

    SP1 -->|stop| OUT
    SP2 -->|converged or limit| OUT
    OUT["<b>report.render_report + summary.render_summary</b><br/>confidence = share of passes that found each finding<br/>RunStore writes report.md, summary.md, master.json, stats.json"]
    OUT --> SHOW["Terminal: shareable summary<br/>and cost line"]
    SHOW --> REPL["REPL only: show / drop / mark / missed"]
    REPL --> POST["<b>post</b>: preview the comment, ask y/N,<br/>then gh posts it to the PR"]

    classDef model fill:#dbeafe,stroke:#2563eb,color:#111827
    class C3 model
```

### Inside one pass

```mermaid
flowchart TD
    A["<b>Reviewer</b> (model, fresh session)<br/>tools: Read, Grep, Glob in the checkout only<br/>input: PR text, diff, criteria. Returns findings as JSON<br/>(each marked confirmed or rejected by its own self-check)"]
    B["<b>FindingsPreparer</b> (findings.py)<br/>shape check; normalized quote substrings must match code<br/>(or anchor over a changed hunk); fix rule ID;<br/>bad findings are counted, not scored"]
    C["<b>MasterList.candidates</b><br/>earlier findings that might be the same issue"]
    D{"Anything to compare<br/>with?"}
    E["<b>Comparator</b> (model, no tools)<br/>for each new finding: same as F-nnn, or new"]
    F["All findings are new<br/>(no model call)"]
    G["<b>Validate decisions</b> (roles.py)<br/>unknown IDs or missing answers are rejected"]
    H["<b>MasterList.apply</b> (master.py)<br/>merge sightings, record which passes saw each finding;<br/>self-rejected sightings are kept apart and not scored"]
    I["<b>PassRecord</b> + saved pass files<br/>findings.json, mapping.json, raw model replies"]
    A --> B --> C --> D
    D -->|yes| E --> G --> H
    D -->|no| F --> H
    H --> I

    classDef model fill:#dbeafe,stroke:#2563eb,color:#111827
    class A,E model
```

What is checked, and by whom, today:

| Check | Done by | Where |
|---|---|---|
| The whitespace-normalized quote matches code substrings (or its anchor covers a changed hunk) | Harness, deterministic | `findings.FindingsPreparer` |
| The cited rule is a real one (formatting is repaired; unknown becomes "none") | Harness | `findings.resolve_rule_id` |
| The Reviewer tries to refute its own finding after writing it (**self-check**) | The Reviewer model, in the same session | second phase of `src/code_reviewer/flows/single_agent/prompts/reviewer.system.md` |
| Are two findings the same issue? (finding vs. earlier findings; text only, no code access) | Comparator model, answer validated by the harness | `roles.Comparator` |
| Confidence, severity ordering, what is posted | Harness arithmetic | `master.rate`, `summary.py` |
| A separate **Verifier** that investigates flagged conflicts and annotates them | Available in agent-personas only; single-agent has no separate Verifier | `flows/agent_personas/roles.py` |

Numbered steps, with the module that does each:

1. `pr_source` fetches the PR head and base into `refs/code-reviewer/` and pins SHAs.
2. `InputBuilder.redact` replaces email addresses in the PR text; `InputBuilder.load_diff` parses the merge-base diff, drops excluded files and refuses empty or oversized PRs.
3. `git_ops.export_tree` exports the PR head to a checkout in its own temporary directory (symlinks skipped).
4. `InputBuilder.resolve_criteria` loads default rules, then resolves project criteria from the **base** commit: the harness extracts candidate paths from `AGENTS.md`, the `CriteriaResolver` picks files, the harness validates (in-repo regular files only), adds the nested `AGENTS.md` and `.claude/rules` files that cover the changed paths (`criteria.scoped_files`, no model call), and rule IDs are assigned.
5. `SingleAgentFlow._run_passes` loops. Each pass is `PassRunner.run`: `Reviewer` → `FindingsPreparer` (validate and ground) → `Comparator` → `MasterList.apply`. The Comparator is skipped when there is nothing to compare (for example a single finding in the first pass).
6. `StopPolicy` decides after every pass whether to stop.
7. `report.render_report` writes the report; `RunStore` writes all artifacts. Criteria appear in the report by name and source, never by their internal IDs.

## Persona implementation

Start at [agent_personas/flow.py](../src/code_reviewer/flows/agent_personas/flow.py). The [flow guide](../src/code_reviewer/flows/agent_personas/README.md) maps its modules and assets; [proposal section 12](proposal.md#12-agreed-persona-design-and-next-steps) records the complete agreed contract.

Project discovery reuses the base-commit Resolver. A persona-only Classifier distributes all accepted project rules, with deterministic coverage checks and visible fallback. Defaults already have static assignments. The flow freezes four Reviewer prompts, then schedules N rounds, running correctness, code quality, test quality and production safety concurrently within each round (`persona_passes`, default 3; overridden by `--passes N`). There is no quiet-pass stop; incomplete slots remain visible.

After the round barrier, usable batches are consolidated serially in fixed persona order. For each batch, the Comparator sees the full master and incoming findings. The harness validates mappings, categories and conflict references, then stages consolidation so interruption cannot retain an incomplete slot's changes. Sightings stay attached to their discovering persona, regardless of category.

After reviewing, one fresh Verifier session per distinct flagged group examines code and accepted criteria while budget/run status permit. Validated annotations may explain support/refutation, conditions or tradeoffs and correct categories. They never rewrite/suppress findings or change severity/votes. No flags means no Verifier calls; failed or skipped verification stays unresolved/pending. Reviewers never receive findings or annotations from earlier calls.

The persona-only `budget.py` owns atomic pool reservations, reconciliation and cancellation; `runner.py` runs cancellable subprocess groups, supplying complete UTF-8 prompts through anonymous temporary stdin files so timeout polling cannot strand partially written pipe input. Each persona has its own Caller/request-log state. Workers append call accounting, and presentation/merging reads it only after the round barrier or worker shutdown; budget decisions use the locked ledger. No worker mutates the master. `--parallel 1` serializes reviewers but retains the round barrier. The Verifier runs serially after review rounds. See [pool behavior](usage.md#persona-budget-pools).
