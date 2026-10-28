# Usage reference

For an already prepared Mac/Lima worker, `reviewer sandbox check --artifacts DIR --lima-home DIR --instance NAME` builds the current code, runs both flows with simulated replies and no-key boundary probes, saves a private report and stops the worker. See the [deployment command](sandbox-check.md) for prerequisites. It accepts no key; optional `--pr` tests in-VM public acquisition and does not enable live reviews.

Start with the [README quick start](../README.md#quick-start). The REPL is parked and bare `reviewer` shows help. This guide covers one-shot commands, interpreting results, project criteria, configuration, storage and troubleshooting.

[Commands](#reviewing-a-pull-request) · [Results](#reading-the-report) · [Project criteria](#preparing-your-project-optional-recommended) · [Configuration](#configuration) · [Storage](#where-things-are-stored-and-cleaning-up) · [Troubleshooting](#troubleshooting)

## Reviewing a pull request

### Current supported preflight

```bash
reviewer review OWNER/REPO#123 --preflight
reviewer review OWNER/REPO#123 --provider openai --model MODEL_ID --mode agent-personas --passes 1 --preflight
```

The CLI prints JSON with the provider, effective settings, configuration blockers and qualification evidence. It performs no target-directory discovery, Git operations, model/version calls, credential reads or posting. Exit 1 means configuration is missing/unsupported. `ready: true` means offline settings passed, not that files, worker or authentication have been checked. `qualified: false` records that live VM qualification is outstanding. OpenAI needs explicit model IDs rather than Claude aliases. Preflight does not establish account access, price correctness or runtime compatibility.

The retained REPL modules are not launched by the CLI. Explicit host review/dry-run is now wired. Default VM dispatch is wired; the deployment still needs trusted worker/artifact settings and the selected runtime credential.

### Execution selection

Default VM review uses the dedicated Mac/Lima worker and one jailed Firecracker VM. VM dry-run, `doctor --live` and retained paid replay are unsupported and refuse. Explicit host review/dry-run is available. Repository labels do not choose execution behavior. Use `--no-sandbox` or trusted `sandbox = false` to select host execution. `--preflight` cannot be combined with `--post` or `--dry-run`. There is no automatic host fallback.

Both internal review algorithms remain implemented and covered by offline tests; the descriptions of their reports, agreement and saved artifacts below explain that behavior. The normal CLI selects the execution backend before acquisition. Prior host-CLI runs are historical evidence. VM remains the default request; explicit host dispatch via `--no-sandbox` is implemented. Keep host acquisition for that direct path; use the same core and runtime-selected adapter in either boundary. See the [security contract](security-contract.md).

### Review options

Use these with `reviewer review <url> ...`; `reviewer review --help` describes them. The parked REPL retains shared option definitions for later use.

| Option | Meaning |
|---|---|
| `--no-sandbox` | Explicit direct-host acquisition/review; no VM startup. Also selectable with trusted `sandbox = false`; no automatic fallback |
| `--preflight` | Show release blockers and effective request without target/model/credential activity |
| `--provider NAME` | `claude` or `openai`; identical security requirements |
| `--parallel N` | Persona-only concurrency from 1 to 4, default `persona_parallel` (4). Reviewers run concurrently within each round, then consolidation is serial; rejected for single-agent |
| `--mode NAME` | Select `single-agent` (default) or `agent-personas`. Each runs its own workflow. The REPL is parked |
| `--passes N` | **Single-agent:** maximum review attempts, default `max_passes` (6). **Agent-personas:** scheduled passes per persona, default `persona_passes` (3), for 4 × N total slots and no quiet-pass stopping. N must be a positive whole number. In single-agent mode: The review can still stop earlier, once at least `min_passes` have run and two in a row find nothing new. `--passes 1` runs exactly one pass, which is the cheap way to try the tool. A value below `min_passes` (3) lowers it to match, so `--passes 2` runs at most 2 |
| `--model NAME` | Model for all roles (including persona classification and verification), an alias such as `haiku` or `sonnet`, or a full model name. Default: the `model_*` settings (`haiku`) |
| `--no-self-check` | Skip the phase where the reviewer tries to refute its own findings. Cheaper, but findings are not double-checked |
| `--keep-checkout` | Keep the temporary read-only checkout so you can inspect exactly what the reviewer saw. Its location is printed. It is in a temporary directory outside the run directory; delete it when done. Otherwise it is deleted when the run ends, even if the run fails |
| `--dry-run` | Host-only target/prompt preview without credentials or model calls; VM requests refuse. Use `--preflight` for no target IO |
| `--verbose` | Print extra progress detail (which model is being called, how many findings are being compared) |
| `--post` | **One-shot only.** Post the summary comment to the PR when the review finishes. Nothing is posted without it. In the interactive prompt use the `post` command instead |
| `--local-path DIR` | **One-shot only.** Local clone to use as the git object store. Default: the current directory, if it is a clone of the same repository; otherwise a cached clone is used |
| `--config PATH` | **Goes before the subcommand:** `reviewer --config PATH review <url>` (bare `reviewer` only shows help). Explicit trusted operator file outside Git working trees, replacing `~/.code-reviewer/review.toml` |

Options override the settings file for that run only. Everything else (budgets, thresholds, criteria mode, ...) is set in [Configuration](#configuration).

### Posting a comment

`post` (REPL) or `--post` (one-shot) writes a single comment containing exactly the review summary the tool printed (what you see is what gets posted), at the chosen confidence levels (default: High, Medium and Low; change it with the `post_tiers` setting or `post --tiers`), labeled as AI-generated, with the commit reviewed and the pass count. It only ever creates or updates one comment per PR from this tool (it recognizes its own hidden marker), so re-running replaces the earlier comment instead of adding more. Inline per-line comments are not supported yet.

## Reading the report

The review summary printed at the end is also saved as `summary.md` in the run directory. It is meant to be shared and can be lengthy when there are many findings. `report.md` is the long version for digging in: what the agent was given, what it ignored, cost, and the details of every pass. The single-agent report contains:

| Section | What it tells you |
|---|---|
| Header | PR, commits reviewed, models, passes completed, why it stopped, diff size, estimated cost |
| Criteria applied | Default and project rules used, project criteria files, references that were ignored (for example a wiki link), warnings (for example "this PR modifies the criteria files") |
| Overview | Finding counts by confidence tier and severity |
| High / Medium / Low confidence | Findings. Confidence is the share of completed passes that found it (needs at least two completed passes; with one pass the section is **"Findings, unrated"** instead): **High** at 75% or more, **Medium** at 40% or more, **Low** below. Each finding shows `file:line`, the criterion it falls under (by name and source), the evidence, a suggestion and "seen in x/N passes" |
| Rejected by self-check | Findings the model raised and then refuted itself. Not scored, shown for transparency |
| Appendix | New findings per pass (the discovery curve), pass-to-pass overlap, per-role and per-pass tokens and cost, and counters for findings dropped because their evidence could not be verified, and for rule citations that were repaired or unknown |

The persona report instead shows each finding's agreement per discovering persona, all assigned criteria, all scheduled slots and their statuses (twelve at the default count), and any conflict annotations. It has no combined confidence tier. Its Verifier annotates flagged conflicts without removing findings or changing votes/severity. See [how the modes differ](../README.md#review-flows).

How to read either report:

- A finding that every pass found has high agreement; repetition alone does not establish correctness. **Overlap near 1.00** means the passes behaved almost identically, so confidence tells you little.
- **Mostly Low findings does not mean they are wrong.** In an early run on a small PR, six passes each reported a different, valid issue, so every finding was Low. Passes explore different code paths, so independent passes complement each other. Read all findings (Low included) and judge them yourself with `mark`; the appendix shows how many findings each pass raised, and the notes in `pass-NN/findings.json` show what each pass did and did not verify. The persona Verifier annotates flagged conflicts without changing confidence (see the roadmap in [docs/proposal.md](proposal.md)).
- **Duplicates are merged by a model, and merging is not checking.** The Comparator compares each pass's findings with the findings of earlier passes (and with each other) and merges those that report the same issue, so one problem is not listed several times. Borderline cases can go either way: one issue may still appear as two or three findings (the "variants" in the report show the other wordings), and occasionally two related issues are merged. The Comparator only decides whether findings are the *same*, never whether they are *true*. A wrong finding that several passes repeat is merged into one finding with high confidence. The separate Verifier in persona mode annotates only flagged conflicts; checking every finding remains later work (see the [persona flow](../README.md#agent-personas)).
- **One pass cannot rate confidence.** After a single pass (for example `--passes 1`, or a run that stopped early) every finding was found by "1 of 1" passes, which would score 100% and mean nothing. Those findings are shown as **unrated** and a posted comment labels them the same way. Run two or more passes to get High, Medium and Low.
- Findings are only kept if their evidence checks out: an exact quote that exists in the code, or, for "something is missing", an anchor on a changed hunk. Unverifiable findings are counted in the appendix and dropped.
- Cost figures are Claude Code's estimates and may not match your billing.

## Preparing your project (optional, recommended)

The agent applies two sets of criteria:

- **Defaults**: generic, language-agnostic rules shipped with the agent ([src/code_reviewer/flows/single_agent/criteria/general.md](../src/code_reviewer/flows/single_agent/criteria/general.md)).
- **Project criteria**: your project's own rules. **Project rules take precedence** when they cover the same concern (the model is instructed to follow them).

To make your project's rules visible, describe where they are in the **`AGENTS.md` at the repository root**. There is no required format; the agent reads it and works out which files hold criteria. For example:

```markdown
# AGENTS.md

## Build
Run `make test`.

## Code review criteria
Reviewers and AI agents must apply the rules in:
- [Error handling](docs/review/errors.md)
- [Security](docs/review/security.md)
- Every file under `docs/review/api/`
```

Rules of the road:

- **Only files inside the repository count.** Links to a wiki, Confluence or any URL, absolute paths and paths outside the repo are ignored, and the report lists what was ignored.
- **Criteria are read from the PR's target (base) branch,** not the PR itself. A PR that edits the rules is still judged by the old ones, and the report flags that the PR touches them.
- **Write rules as Markdown headings.** Each heading becomes a citable rule ID such as `P:docs/review/errors.md#retry-policy`. A file with no headings becomes one rule.
- **Keep it small.** About 200,000 characters (roughly 50,000 tokens) of project criteria are used; the rest is dropped with a warning.
- **Some files are added automatically,** because where they apply is clear from their location. You do not need to link them from the root `AGENTS.md`:
  - an `AGENTS.md` in a directory that contains a changed file, or in one of its parent directories (for a change under `spec/controllers/`, both `spec/AGENTS.md` and `spec/controllers/AGENTS.md`);
  - every `.claude/rules/*.md` file, except those with a `paths:` list in their front matter that matches none of the changed files (such a file is scoped to those paths on purpose).
  The report lists them with the reason each was added.
- Without a root `AGENTS.md`, or if nothing in it resolves, the files above and the defaults are still used, and the report says so.

The ten single-agent defaults cover correctness, simplicity, clarity and contract documentation, security/privacy, bounded input validation, failure/resource handling, data integrity/concurrency, internal contracts, test coverage/test levels, and test independence/mocking. Each rule states what to inspect, when to report and what not to report, using repository-visible evidence. They do not include generic architecture or performance review requirements. The system prompt still owns finding scope, evidence fields, self-checks and JSON output; project rules take precedence.

The current criteria are numbered `GEN-001` through `GEN-010` in checklist order. Saved runs retain their original criteria; compare the saved criteria blocks/indexes as well as prompt hashes when evaluating runs. The revised defaults are tested for loading and delivery to the Reviewer, and have been used on real pull requests, but no before/after comparison of review quality exists. Reports and the PR comment name each criterion by its title and source; the IDs stay internal.

Persona mode uses thirteen canonical rules with static assignments; see the [persona criteria guide](persona-criteria.md). Accepted project rules are classified to at least one persona; unresolved assignments fall back to all personas with a warning.

### Customizing the default rules

For single-agent, edit [src/code_reviewer/flows/single_agent/criteria/general.md](../src/code_reviewer/flows/single_agent/criteria/general.md). Each rule is a heading of the form `## [GEN-011] Title` followed by the rule text. The ID is what findings cite. To turn rules off for your runs, list them in `disabled_default_rules`, or set `criteria_mode` to `project_only` or `defaults_only`.

## Configuration

Operator settings come from `--config PATH` or `~/.code-reviewer/review.toml` only. Current-directory `review.toml` is ignored. Explicit files inside Git working trees are rejected, along with symlinks/hardlinks, non-regular/oversized files, foreign ownership and group/other-write permissions. The loader uses directory-relative no-follow opens and a 64 KiB limit. A security-ready flag is not a configuration option. Copy [review.example.toml](../review.example.toml) and change what you need; every key is optional. Unknown keys are rejected so typos do not pass silently.

| Setting | Default | Meaning |
|---|---|---|
| `review_mode` | `single-agent` | Flow selected by default; override for one run with `--mode`. `agent-personas` uses `persona_passes` slots for each of four personas, then verifies flagged conflicts within budget |
| `model_reviewer`, `model_comparator`, `model_resolver`, `model_verifier` | `haiku` | Model alias or full name per role. Persona classification uses `model_resolver`; `model_verifier` is persona-only. Historical Comparator comparisons are recorded in the proposal; replay instructions are in the development guide. |
| `persona_parallel` | `4` | Persona-only concurrent model calls (1–4), overridden with `--parallel N`. Single-agent stays sequential |
| `persona_passes` | `3` | Persona-only scheduled passes per persona. `--passes N` overrides it for one run; 4 × N review slots, with no convergence stopping. One completed pass per persona is unrated |
| `self_check` | `true` | The Reviewer tries to refute its own findings before answering |
| `criteria_mode` | `merge` | `merge`, `project_only` or `defaults_only` |
| `criteria_manifest` | `""` | Path to a JSON file that replaces the criteria resolver's answer (reproducible experiments) |
| `disabled_default_rules` | `[]` | Default rule IDs to switch off |
| `min_passes`, `max_passes` | `3`, `6` | Single-agent only: Successful passes required before convergence / maximum scheduled attempts |
| `quiet_k` | `2` | Single-agent only: Stop after this many consecutive passes with no new finding (after `min_passes`) |
| `max_consecutive_failures` | `2` | Single-agent: consecutive failures across passes. Personas: consecutive failures per persona; disable only that persona |
| `tier_high`, `tier_medium` | `0.75`, `0.40` | Confidence tier thresholds |
| `max_diff_lines` | `3000` | Refuse PRs with more changed lines (after exclusions) |
| `exclude_patterns` | lockfiles, minified, generated, `vendor/`, `node_modules/`, ... | Files left out of the diff (this list replaces the built-in one) |
| `extra_exclude_patterns` | none | Added to `exclude_patterns` without replacing it. Use it for planning documents such as PRDs and implementation plans (the author's claims, which may drift from the code): `["docs/prds/", "docs/plans/"]`. They are left out of the prompt but listed by path, the reviewer can still open them in the checkout, and findings located in excluded files are dropped and counted. Keep public-facing documentation (API and event docs) out of this list: it must stay reviewed |
| `persona_prompt_max_chars` | `500000` | Persona-only limit on complete system + user text per call, including retry feedback. Oversized inputs are reported and never silently truncated |
| `criteria_max_chars` | `200000` | Cap on project criteria text |
| `call_budget_usd`, `call_timeout_s` | `0.50`, `600` | Spend and time cap for every single Claude call |
| `run_budget_usd`, `run_budget_tokens` | `3.00`, `2000000` | Thresholds for a whole review; new calls stop at the relevant budget check. The token cap counts new input, output and cache-write tokens only. **Cache reads are not counted**: a many-turn review re-reads the same cached context every turn, which adds up to hundreds of thousands of tokens but costs very little. Single-agent checks run thresholds between passes. Persona mode checks before every call/retry and reserves dollars from independent proportional pools (see below), rounded down to cents; reported usage can still overshoot a threshold. Persona call budgets need at least $0.01 |
| `isolation_flags` | `--safe-mode`, `--restricted` | Claude Code flags that keep the Reviewer isolated from your local setup. Do not remove unless you know why |
| `claude_bin` | `claude` | Path or name of the Claude Code binary |
| `post_tiers` | `["High","Medium","Low"]` | Confidence tiers shown in the review summary and included in a posted comment. Single-pass (unrated) findings are always included |
| `redact_emails` | `true` | Replace email addresses in the PR title, description and commit messages with `[email]` before they are sent to the model. The diff and repository files are not redacted. Set to `false` to send PR text as written |
| `runs_dir` | `""` | Where runs are stored (default `~/.code-reviewer/runs`) |
| `candidate_window`, `candidate_all_threshold`, `candidate_max`, `grounding_line_window` | `20`, `30`, `40`, `30` | First three tune single-agent duplicate candidates; persona mode supplies the full master; `grounding_line_window` counts corrections larger than this distance. Quotes are searched across the file and line numbers are corrected even within this window |

Environment variables: `CODE_REVIEWER_HOME` (repository checkout containing both flows and their assets), `CODE_REVIEWER_DATA` (replaces `~/.code-reviewer` for runs, cache and config).

## Persona budget pools

The total dollar cap is split at startup: weights 3/3/4 for criteria (Resolver + Classifier), Comparator and Verifier out of 30, with the remaining 20/30 divided equally among the four personas. Shared allocations round down to cents; leftover cents are assigned to personas in fixed order, so persona allocations differ by at most one cent and the sum never exceeds the cap. The per-call cap still applies.

Before each attempt/retry, atomically reserve its allowance from its own pool; reconcile the reported cost afterward. No borrowing or automatic redistribution occurs. If cost is unavailable (for example after timeout/cancellation), conservatively consume that attempt's allowance in the budget ledger and increment an unknown-cost counter; `spent_usd` includes these conservative charges, rather than only confirmed spend; `cost_known_for_all_calls` is false for failed calls without reported cost, even with zero reported tokens. Reported cost totals remain estimates. Startup output, dry runs, `report.md` and `stats.json` show allocations/accounting. CLI budget enforcement is not a billing guarantee.

A persona budget/failure limit disables only that persona. Criteria exhaustion follows existing discovery/classification fallback with warnings. Comparator exhaustion stops further rounds and preserves unconsolidated raw replies without counting their votes. Verifier exhaustion leaves groups pending/unresolved. Completed results survive; failed/unmerged/skipped slots are visible. `incomplete` means some persona slots could not complete and produces a nonzero CLI exit. Ctrl-C cancels the whole run, terminates owned Claude subprocess groups on POSIX, waits for workers and then cleans the checkout. The global token threshold stops new calls when reported usage reaches it; in-flight calls can overshoot it.

## Where things are stored, and cleaning up

| Path | Contents |
|---|---|
| `~/.code-reviewer/runs/<time>_<repo>_<pr>/` | One review: summary/report, master, usage, labels and settings; `input/` holds redacted PR metadata, diff and criteria; `pass-NN/` holds raw replies, findings and mappings. Single-agent prompts live under `input/reviewer.*.md`; persona prompts under `input/personas/`, with conditional annotations under `verification/` |
| `~/.code-reviewer/cache/` | Bare clones used when you review a repository other than the current one |
| `<system temp dir>/code-reviewer-checkout-*/` | The reviewer's read-only checkout of the PR, **outside the run directory** so nothing the run produced (earlier passes' findings, reports) is next to it. Deleted when the run ends unless `--keep-checkout` |
| `refs/code-reviewer/*` in your local clone | The PR head and base commits fetched for a review. Your branches and working tree are never changed |

The temporary checkout is deleted when the run ends, even if the run fails (keep it with `--keep-checkout`). To clean up, delete run directories you no longer need. To remove the fetched refs from a clone:

```bash
git for-each-ref --format='%(refname)' refs/code-reviewer | xargs -n1 git update-ref -d
```

## Cost, time and limits

- Single-agent passes run sequentially. Persona Reviewers run concurrently within a round, followed by serial consolidation; rounds wait for the slowest active Reviewer. A review can take several minutes. Cost depends on diff/criteria size, model, tool turns and thinking output. Every run reports tokens and estimated cost; budget settings stop further calls at the applicable checks. Historical measurements are in [proposal §9](proposal.md#9-experiment-plan-and-v0-checks), not estimates for a new persona run.
- Only GitHub pull requests are supported (no local-branch mode yet).
- Diffs above `max_diff_lines` are refused rather than reviewed badly. Lockfiles, vendored and generated files do not count.
- The tool does not run tests, linters or builds. Your pipeline still owns those.
- `claude -p` in the installed CLI has no turn limit flag, so calls are bounded by the per-call spend cap and timeout instead.

## Privacy and safety

- **What reaches the selected provider:** PR text, diff, criteria and repository files read by code-reading model roles. Authentication uses the selected adapter; repository labels do not select a provider. Both dispatch paths are wired; authenticated VM inference remains untested. Saved prompts/reports also contain code and should stay private.
- **What the Reviewer and persona Verifier can do:** read files in a separate checkout of the PR. They cannot run commands, edit files, use the network or load your local Claude configuration (skills, hooks, MCP servers, `CLAUDE.md`). The checkout lives in its own temporary directory, away from the run's files, so earlier passes' findings are not next to it. The former host-based `doctor --live` implementation has been removed. Runtime containment checks use the [sandbox deployment command](sandbox-check.md); the tool allow-list alone is not an OS boundary.
- **What the Reviewer cannot see:** pull request comments, review threads, issue comments, other reviewers' or bots' reviews, and other passes' findings. It is given only the PR title, description and commit messages, the diff, the criteria and the checkout. The tool fetches only an allow-list of PR fields from GitHub for the review (a test fails if comments are ever added to it). Posting a comment separately lists the PR's comments to find the tool's own earlier one; that list is never given to the model. Host `--no-sandbox --dry-run` describes the defaults-only inputs without credentials or model calls, and `input/pr.json` and `input/reviewer.*.md` in a run to see what was sent.
- **Personal data in PR text:** email addresses in the PR title, description and commit messages are replaced with `[email]` before anything is sent (setting `redact_emails`, on by default; `git@host:` clone addresses and version strings such as `lodash@4.17.21` are left alone). This covers **only** emails in the PR text. Names, chat links (for example Slack URLs) and anything in the diff or repository files are sent as they are. The internal dry-run output shows the text as it would be sent, and `input/pr.json` records what was sent and how many addresses were replaced.
- **Untrusted PR text:** the PR description and code are treated as data and the model is told never to follow instructions found in them. That reduces the risk of prompt injection but cannot eliminate it. Read findings critically.
- **Nothing outward-facing happens without you:** the only action on GitHub is `post`, after a preview and your confirmation (or the explicit `--post` flag).
- **Not done in v0:** secret scanning of the diff, and filtering of what the Reviewer may read inside the checkout. See [proposal.md](proposal.md) section 5.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `reviewer: command not found` | The virtual environment is not active: `source .venv/bin/activate` in the code-reviewer directory |
| `pip install -e .` says editable mode requires a setuptools-based build | Old pip. Run `python -m pip install --upgrade pip` first |
| `config error: ... needs Python 3.11+ or the 'tomli' package` | Run `pip install tomli`, or reinstall with `pip install -e .` |
| `FileNotFoundError` points to `prompts/resolver.system.md` after updating the reviewer | Exit any already-running `reviewer` REPL and launch it again. An editable install does not reload modules in a running Python process. Current prompts live under `src/code_reviewer/flows/<flow>/prompts/`; do not copy assets back to the old location. If a fresh launch still uses old paths, check which `reviewer` executable/virtual environment is active |
| `--passes applies to single-agent` after updating | An older REPL is still running: exit and restart it. Current persona mode accepts `--passes N` as passes per persona (default 3), so `--mode agent-personas --passes 1` schedules four total review slots |
| `error: gh ... failed` | Run `gh auth status` and `gh auth login`. Check you can read the repository. For a company-hosted GitHub, log in to that host |
| `cannot lock ref ... is at ... but expected ...` during fetch | A ref changed during Git's update, often because another fetch ran concurrently. Current review fetches ignore configured remote-tracking mappings and disable automatic tag fetching, updating only their private review refs. Restart an old REPL after updating the reviewer. A new review fetches the latest branch tips; commits are pinned only within a run |
| `error: ... refusing to review` | The diff exceeds `max_diff_lines`. Review a smaller PR or raise the limit |
| `error: ... no reviewable changes` | Every changed file matches `exclude_patterns` or `extra_exclude_patterns` |
| `pass N FAILED: ... timed out` or `not a valid JSON object` | Look at `pass-NN/reviewer.raw.json` in the run directory. Raise `call_timeout_s` or `call_budget_usd`, or try another model |
| `pass N FAILED` mentioning budget | The call hit `call_budget_usd`. Raise it, or review a smaller PR |
| Many findings counted as ungrounded | See the `reason` of each in `pass-NN/findings.json`. The model may be quoting from memory. Try `--model sonnet` |
| Warning: criteria resolver failed | Resolver-selected rules may be missing; enabled defaults and automatically included project rules can still apply. Check the criteria index, warnings and `input/resolver.raw.json` |
| Every pass reports only one finding, or findings are all Low | Passes may stop early or pick a different issue each time. Check `notes` in each `pass-NN/findings.json` for what the pass verified and what it left "unverified", and compare the number of findings per pass in the report's appendix. See [Reading the report](#reading-the-report) |
| No findings at all | May be correct. Check `notes` in `pass-NN/findings.json` and compare with the diff. Try `--model sonnet` |
| `post` fails | You need permission to comment on the PR, and `gh` must be logged in as that user |

## Sandbox prototype status

Normal VM reviews acquire public GitHub PRs inside the VM, select the configured adapter, privately hand off credentials after boundary checks and return bounded reports after worker shutdown. Explicit host dispatch remains available. Private URL authentication and normal-CLI capsule input remain unsupported; low-level no-key checks retain capsules. See the [execution contract](security-contract.md) and [VM runbook](sandbox-check.md).

## Host adapter configuration

Host reviews use `--no-sandbox` or trusted `sandbox = false`; preflight does not check/read model credentials. The CLI adapter uses its configured local login/token. The API adapter reads `OPENAI_API_KEY` only when a real host review starts; never put the key in arguments or TOML. Configure `openai_input_price`, `openai_cached_input_price` and `openai_output_price` as positive USD per million tokens. The current API adapter requires one explicit model ID across all `model_*` roles (`--model MODEL_ID` sets all of them). A host dry-run needs a valid model ID, but no key or rates. Missing/invalid configuration refuses before target acquisition. The [example TOML](../review.example.toml) lists the fields.


### VM review configuration

Store these **outside Git working trees**, in `~/.code-reviewer/review.toml` or an explicit `reviewer --config /absolute/path/review.toml review ...` file:

```toml
sandbox = true
vm_artifacts = "/absolute/path/to/verified-artifacts"
vm_lima_home = "/absolute/path/to/dedicated-lima-state"
vm_instance = "reviewer-sandbox"
vm_timeout_s = 1800
# For the native CLI adapter, also provide a verified Linux ARM64 artifact:
vm_claude_binary = "/absolute/path/to/linux-arm64-claude"
vm_claude_sha256 = "REPLACE_WITH_VERIFIED_64_CHARACTER_LOWERCASE_SHA256"
vm_claude_auth = "api-key" # or "oauth"; never chooses billing automatically
```

Use the worker/artifact prerequisites in [sandbox check](sandbox-check.md). Preflight checks configuration shape without opening artifacts, inspecting the worker or reading a key. Real execution hashes staged artifacts, validates/locks the dedicated stopped worker, prepares clean images and starts one jailed VM. The selected native binary digest joins the connected image's cache identity. The normal command currently supports **public github.com PR acquisition only**; it does not inspect/fetch the target on the workstation. `--local-path` and `--keep-checkout` refuse in VM mode.

Set only the intended credential in the invoking process environment: `OPENAI_API_KEY`, or for Claude, `ANTHROPIC_API_KEY` with `vm_claude_auth = "api-key"`, or `CLAUDE_CODE_OAUTH_TOKEN` with `"oauth"`. No key/token value belongs in TOML, arguments, images or chat. The native adapter receives only its selected credential, fixed proxy configuration and a fresh guest home; host login files/keychains are not imported. OAuth and API billing never fall back to each other. For OpenAI, retain explicit model and token rates as described above.

The launcher reads that selected credential only after input admission, external source-access revocation and native/file/network probes pass. Private SSH process pipes and vsock carry bounded frames. The inference proxy permits only the selected provider's fixed endpoints: `api.openai.com`, or `api.anthropic.com` and `platform.claude.com`. Interactive login, SSO, extra providers/endpoints and custom gateways are unsupported. A missing endpoint fails under policy; it does not widen access automatically. See [native authentication details](sandbox-runtime-auth.md).

After VM/network cleanup and confirmed outer-worker shutdown, the host saves `report.md`, `summary.md` and `stats.json` in a fresh `vm-<random ID>` directory under `runs_dir`. The directory is 0700 and files 0600; internal prompts/raw replies remain ephemeral in the VM. The terminal/posting path uses the prefiltered summary generated inside the guest. `--post` remains explicit and uses the operator's host GitHub CLI after review. No untrusted Python result object is deserialized.

This wiring is covered by simulated provider/transport tests; **authenticated VM inference has not yet been exercised**. A compromised guest can falsify findings/accounting or reuse the credential it receives. VM destruction does not revoke that credential or enforce a hard provider spending cap. Use revocable credentials and retain provider-side limits.
