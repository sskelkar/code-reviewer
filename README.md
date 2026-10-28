# code-reviewer

A pull request review agent with runtime-selected Claude and OpenAI adapters. It runs independent review passes, merges their findings and reports how often each issue was found. You can review the results locally or explicitly post a summary to GitHub.

**Status:** v0. Both adapters share one review core. VM execution is the intended default, with explicit direct-host execution planned. Authenticated CLI dispatch is not integrated yet. Single-agent reviews have been used on real PRs. Both flows are implemented and tested offline; persona execution also has a successful user-run check. Review quality remains unmeasured. Posting has not been exercised live.

[Quick start](#quick-start) · [Review flows](#review-flows) · [Results](#reading-the-results) · [Configuration](#configuration) · [Documentation](#documentation)

## Quick start

Current preflight needs Python 3.9+ and the editable installation below; it needs no GitHub/Claude login or API key. Git is used by offline tests. Live review prerequisites will be qualified with the supported sandbox backend, rather than discovered from the target project.

```bash
git clone https://github.com/sskelkar/code-reviewer.git
cd code-reviewer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
reviewer doctor  # offline release status; currently exits 1 while controls are missing
```

Keep the editable installation and its source checkout: prompts and criteria are loaded from it. Keep the virtual environment active when running `reviewer`. Installation pulls in `tomli` on Python 3.9/3.10; otherwise runtime code uses the standard library.

Check the configured request and current integration blockers without fetching code, reading credentials or calling a model:

```bash
reviewer review OWNER/REPO#123 --mode single-agent --preflight
reviewer review OWNER/REPO#123 --provider openai --model MODEL_ID --mode agent-personas --preflight
```

Preflight returns JSON and exits nonzero while required controls are missing. Both providers are currently blocked. CLI/REPL reviews, `--dry-run`, `doctor --live`, paid replay and the old connected real-key path refuse execution before target/model activity. The planned direct-host option is not implemented yet; neither provider nor repository labels enable execution. The offline flow algorithms and harmless no-key deployment checks remain testable.

On a Mac with a prepared dedicated Lima worker and the pinned runtime artifacts, run the supported deployment command:

```bash
reviewer sandbox check --artifacts /path/to/verified-artifacts \
  --lima-home /path/to/dedicated-lima-state --instance reviewer-sandbox
```

It builds the current code, runs both flows with simulated replies plus no-key boundary probes, saves a private JSON report and stops the worker. It accepts no API key; optional `--pr` tests acquisition without inference. See [prerequisites and results](docs/sandbox-check.md); this verifies deployment, not a live PR review or production qualification.

Clean images are reused when their source, builder and OS inputs match. Add `--lifecycle` to test cancellation and supervisor loss, or `--pr https://github.com/OWNER/REPOSITORY/pull/NUMBER` to check public acquisition and source-access revocation inside the review VM without inference. An abandoned dedicated worker can be reset explicitly with `reviewer sandbox recover --lima-home DIR --instance NAME`; see [recovery scope](docs/sandbox-check.md#recover-an-abandoned-worker).

Operator settings come only from an explicit `--config` outside Git working trees or `~/.code-reviewer/review.toml`. Current-directory `review.toml` is ignored. Files must be bounded regular files owned by the operator/root and not writable by other users. A VM failure never selects host execution automatically. Explicit host execution is an intended separate operator choice. Public PR acquisition now has a no-key VM check; authenticated review and supported sandbox dispatch remain required integration work; see the [security contract](docs/security-contract.md).

## Execution location and provider

The planned interface has independent choices:

- `--provider claude|openai` chooses the adapter used by the same review core.
- `--mode single-agent|agent-personas` chooses the review strategy.
- VM execution is the default; the proposed `--no-sandbox` flag explicitly selects direct-host acquisition/review without VM startup overhead. **That flag and live dispatch are not implemented yet.**

Any repository uses the chosen settings; there is no production/personal/public reviewer tier. Direct mode preserves the existing host acquisition feature and common review checks, while VM mode adds kernel/filesystem/network isolation. A failed VM request stops rather than switching modes. See the [execution contract](docs/security-contract.md).

## Review flows

The implemented flow algorithms below are exercised directly by offline tests and guest workloads; the public review entry point is currently gated. Both flows pin the PR's commits, load its diff and criteria, and give fresh model sessions read access to a separate temporary head checkout. Sessions never see earlier passes' findings. **Models propose; Python validates evidence and replies, merges findings, counts agreement and writes every artifact.**

Each new review fetches the current PR head and target-branch tip into private refs, without updating your branches, remote-tracking refs or tags. Those commits stay fixed only for that run. The diff is merge-base to PR head; the checkout is the PR head, not a simulated merge with the latest target.

Choose with `--mode`, or set `review_mode` in `review.toml`:

| Mode | Pass schedule | Consolidation and verification |
|---|---|---|
| `single-agent` (default) | One general Reviewer; normally 3–6 independent passes, stopping after two quiet passes | Incremental duplicate merging; no separate Verifier |
| `agent-personas` | Four focused Reviewers, three passes each by default, configurable with `--passes N` | Shared master list, per-persona agreement and advisory verification of flagged conflicts |

### Single-agent

```mermaid
flowchart TD
    PREP["Pin PR, load diff and criteria"] --> REV["Claude: fresh general Reviewer"]
    REV --> CHECK["Harness: validate and ground findings"]
    CHECK --> MERGE["Comparator when needed; harness merges sightings"]
    MERGE --> NEXT{"Continue review?"}
    NEXT -->|yes| REV
    NEXT -->|no| REPORT["Report and summary with agreement scores"]
```

The harness updates the master after every pass. By default it requires three successful passes before convergence and stops after two consecutive passes add no new scored finding, or at six scheduled attempts. Budget, cancellation and failure limits can stop it earlier. Use `--passes N` to change the maximum; `--passes 1` produces unrated findings.

### Agent-personas

```bash
reviewer review OWNER/REPO#123 --mode agent-personas --model haiku
# One pass per persona: four total review slots.
reviewer review OWNER/REPO#123 --mode agent-personas --passes 1
```

The personas focus on **correctness**, **code quality**, **test quality** and **production safety**. Their thirteen default rules are assigned ahead of time. Accepted project rules are classified to one or more personas; the harness checks complete coverage and falls back to all personas for unresolved assignments, with warnings.

```mermaid
flowchart TD
    PREP["Pin PR and discover criteria"] --> ASSIGN["Load static defaults; classify project rules if present"]
    ASSIGN --> FREEZE["Freeze and save four persona prompts"]
    FREEZE --> ROUND["Next round"]
    ROUND --> C["Correctness Reviewer"]
    ROUND --> Q["Code-quality Reviewer"]
    ROUND --> T["Test-quality Reviewer"]
    ROUND --> S["Production-safety Reviewer"]
    C --> CHECK
    Q --> CHECK
    T --> CHECK
    S --> CHECK
    CHECK["Wait for the round; validate findings"]
    CHECK --> MERGE["Serial Comparator in fixed persona order:<br/>duplicates, categories and conflict flags"]
    MERGE --> MASTER["Harness: update one master list"]
    MASTER --> MORE{"More slots and review allowed?"}
    MORE -->|yes| ROUND
    MORE -->|no| FLAGS{"Unexamined flagged groups<br/>and verification allowed?"}
    FLAGS -->|yes| VER["Claude: fresh targeted Verifier"]
    VER --> ANN["Harness: validate and attach annotations"]
    ANN --> FLAGS
    FLAGS -->|no| REPORT["Combined report: per-persona agreement,<br/>conflict annotations and unfinished work"]
```

Set passes per persona with `--passes N` or `persona_passes` in TOML (default 3). All 4 × N slots are scheduled even if earlier passes find nothing new. Normal retries, budgets, cancellation and failure limits can leave slots incomplete. A timeout leaves cost unknown; the pool ledger conservatively charges the reserved allowance ([accounting details](docs/usage.md#persona-budget-pools)). Each round runs up to four Reviewers concurrently on one pinned checkout, then merges results in fixed persona order before the next round. `--parallel N` (1–4, default 4; `persona_parallel` in TOML) controls concurrency.

All passes within a persona receive identical prompts. Any persona may report an evidenced correctness/safety issue outside its focus. Categories never transfer votes: agreement stays with the discovering persona.

The Comparator flags possible conflicts while merging. The Verifier investigates only those groups and explains evidence, uncertainty or tradeoffs. **Annotations never suppress findings or change severity/votes.** No flags means no Verifier calls; blocked or failed verification stays visible as pending/unresolved.

For module-level walkthroughs, see [architecture](docs/architecture.md). The [persona criteria guide](docs/persona-criteria.md) lists the rules, and [proposal §12](docs/proposal.md#12-agreed-persona-design-and-next-steps) records the full contract.

Persona budgets scale with the total cap: 10% criteria builder, 10% Comparator, 13⅓% Verifier, then an equal split of the remainder among personas (about 16⅔% each). At $3 this is $0.30/$0.30/$0.40 plus $0.50 per persona. Calls/retries reserve their allowance atomically; unused pools are not transferred. An exhausted persona or its consecutive-failure limit skips only that persona's remaining slots. Comparator exhaustion stops new review work; Verifier exhaustion leaves conflicts pending. Already-running token usage can exceed the global token threshold. [Budget details](docs/usage.md#persona-budget-pools).

## Reading the results

A review prints its findings, completion/cost line and full-report path. Runs are saved under `~/.code-reviewer/runs/`:

| Artifact | Purpose |
|---|---|
| `summary.md` | Shareable findings; also used for the optional PR comment |
| `report.md` | Detailed criteria, pass accounting, evidence and usage |
| `input/`, `pass-NN/`, `master.json`, `stats.json` | Saved inputs, raw replies, findings, consolidation and accounting |
| `input/personas/`, `verification/` | Persona-mode frozen prompts and conditional verification records |

**Agreement measures repetition, not correctness.** High confidence starts at 75%, Medium at 40%; fewer than two completed passes are unrated. Single-agent uses completed passes as the denominator. Persona mode scores each discovering persona separately—for example, `test-quality: 3/3`—with no combined tier. Failed slots do not contribute votes or completions. Unrated findings remain visible under tier filters.

To inspect and judge findings interactively, start `reviewer` inside the project clone, then run `review OWNER/REPO#123 --mode agent-personas` (or `single-agent`):

```text
show F-001                 inspect a finding
mark F-001 real            record your judgment (real, nit or wrong)
missed "describe an issue"  record a missed issue
drop F-001                 omit it from the shareable summary
summary                    show the summary with your omissions
open                       open the full report
post                       preview, then ask for confirmation
```

The CLI posts only with an explicit `--post` flag; it does not ask for an additional confirmation. Posting creates or updates one summary comment per PR. See the [usage reference](docs/usage.md) for all commands, options and report details.

## Configuration

Copy [review.example.toml](review.example.toml) outside Git working trees to `~/.code-reviewer/review.toml` (recommend mode 0600). Project-directory configuration is not loaded. An explicit file goes before the subcommand: `reviewer --config PATH review OWNER/REPO#123`. Command options override file settings for that run.

Common settings are `review_mode`, `persona_passes`, `persona_parallel`, the `model_*` roles, `criteria_mode`, `extra_exclude_patterns` and run/call budgets. `--model sonnet` changes **all roles**. Single-agent pass controls do not change persona scheduling. See the [complete settings reference](docs/usage.md#configuration) for defaults and limits.

Project criteria come from the **base commit**, so a PR cannot change the rules it is judged by. Point to review-rule files from root `AGENTS.md`; applicable nested `AGENTS.md` and `.claude/rules` files are also included by location. Project rules take precedence over conflicting defaults. See [preparing your project](docs/usage.md#preparing-your-project-optional-recommended).

## Scope and safety

Reviewers and the persona Verifier have Read/Grep/Glob in a temporary checkout; they do not execute tests, linters or builds. Other model roles have no code tools. The checkout is outside saved-run storage and is deleted afterward unless you pass `--keep-checkout`.

PR metadata, diff, criteria and files read during review are supplied to the selected model provider. Email redaction covers PR text only; diffs/files are not redacted. Saved runs contain code and should be stored privately.

VM execution adds the shared jailer/resource/deadline lifecycle, read-only admitted input, phase-scoped external networking and private bounded transfer. Its no-key deployment checks pass; authenticated dispatch and adapter integration remain unfinished. Explicit host execution is planned and retains acquisition on the machine; it does not claim VM isolation. See the [VM checklist](docs/sandbox-todo.md) and [privacy reference](docs/usage.md#privacy-and-safety).

## Development and roadmap

After the editable installation, run the offline suite:

```bash
python3 -m unittest discover -s tests -t .
```

Tests use fake models and temporary repositories, with no network or paid calls. Supported Python is 3.9+; contributors run the full suite on their oldest and newest available Python. Repository rules are in [AGENTS.md](AGENTS.md); code ownership, checks, debugging and replay are in the [development guide](docs/development.md) and [architecture guide](docs/architecture.md).

Current priority: finish one workflow with runtime-selected provider and explicit execution location. Keep host acquisition for the direct option and the shared jailed lifecycle for the VM default; common criteria, validators, budgets and reports stay shared. The standalone VM experiment now lives outside this repository. See the [roadmap](docs/security-roadmap.md) and [VM checklist](docs/sandbox-todo.md). The unused eval framework was removed; review quality is judged manually.

## Documentation

| Guide | Contents |
|---|---|
| [Usage reference](docs/usage.md) | CLI/REPL commands, reports, project criteria, all settings, storage and troubleshooting |
| [Security contract](docs/security-contract.md) | Shared requirements, VM-default and explicit direct-host execution |
| [Connected sandbox](docs/sandbox-connected.md) | Existing engineering components, boundary probes and current limitations |
| [Run the sandbox check](docs/sandbox-check.md) | Supported Mac command: build, both flows, boundary probes, private result and shutdown |
| [Finish the VM](docs/sandbox-todo.md) | Current bounded checklist, completed controls and next work |
| [Sandbox alternatives](docs/sandbox-alternatives.md) | Single-VM Mac designs, dedicated Linux and security tradeoffs |
| [Architecture](docs/architecture.md) | Code map, flow ownership and detailed single-agent diagrams |
| [Development](docs/development.md) | Setup, tests, common changes, saved-run debugging and Comparator replay |
| [Prompt contracts](prompts/README.md) | Model inputs, outputs, tools and deterministic validation |
| [Persona criteria](docs/persona-criteria.md) | Canonical defaults and assignments |
| [Proposal](docs/proposal.md) | Decisions, experiments and roadmap |
| [Python walkthrough](docs/project-explainer.md) | Code explained for Java developers |
