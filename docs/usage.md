# Usage reference

Start with the [README quick start](../README.md#quick-start). The REPL is parked and bare `reviewer` shows help. This guide covers one-shot commands, interpreting results, project criteria, configuration, storage and troubleshooting.

[Commands](#reviewing-a-pull-request) · [Results](#reading-the-report) · [Project criteria](#preparing-your-project-optional-recommended) · [Configuration](#configuration) · [Storage](#where-things-are-stored-and-cleaning-up) · [Troubleshooting](#troubleshooting)

## Running from a new terminal

After the [source installation](../README.md#install), run this from the code-reviewer checkout, replacing the PR reference and selecting your usual provider/authentication options:

```bash
source .venv/bin/activate && reviewer review OWNER/REPO#123 --passes 1
```

Activation makes the installed `reviewer` command available in that terminal. `&&` runs it only if activation succeeds. It does not install the package or choose provider, credentials or execution location; the existing configuration and flags still apply.

From another directory, use the absolute activation path, replacing `/absolute/path/to/code-reviewer` with your installation:

```bash
source /absolute/path/to/code-reviewer/.venv/bin/activate && reviewer review OWNER/REPO#123 --passes 1
```

You can also invoke the Python entry point directly from the reviewer checkout without activation or a PATH change:

```bash
.venv/bin/python -m code_reviewer.cli review OWNER/REPO#123 --passes 1
```

The review commands below include activation so they can be copied into a fresh terminal from the reviewer checkout. Further commands in an already activated terminal can use `reviewer` directly.

## Reviewing a pull request

### Check configuration

```bash
source .venv/bin/activate && reviewer review OWNER/REPO#123 --preflight
source .venv/bin/activate && reviewer review OWNER/REPO#123 --provider openai --model MODEL_ID --passes 1 --preflight
```

The CLI prints JSON with the provider, effective settings, configuration errors and execution choice. It performs no target-directory discovery, Git operations, model/version calls, credential reads or posting. Exit 1 means configuration is missing/unsupported. `ready: true` means offline settings passed, it does not verify files, the worker or authentication. `runtime_checked: false` makes clear this is an offline configuration report, not a live check. OpenAI defaults to `gpt-6-luna`; explicit overrides must use OpenAI model IDs rather than Claude aliases. Preflight does not establish account access, price correctness or runtime compatibility.

The CLI runs one review and exits. VM execution requires trusted worker/artifact settings; host execution requires explicit operator selection. Both execution locations use the selected runtime credential.

### Execution selection

VM is the default and never falls back to the host. Use `--no-sandbox` or trusted `sandbox = false` for direct-host execution. VM dry-run is unsupported. `--preflight` cannot be combined with `--post` or `--dry-run`.

The CLI selects execution before acquisition and uses the same pipeline and selected adapter in either location. See the [security contract](security-contract.md).

### Review options

Use these with `reviewer review <url> ...`; `reviewer review --help` lists the current options.

| Option | Meaning |
|---|---|
| `--no-sandbox` | Explicit direct-host acquisition/review; no VM startup. Also selectable with trusted `sandbox = false`; no automatic fallback |
| `--preflight` | Check effective configuration and unsupported choices without target/model/credential activity |
| `--auth NAME` | `api-key` (default) or `subscription`; independent of provider/location. ChatGPT uses reviewer-owned sign-in; Claude uses native subscription login on the host or an explicit OAuth token for host/VM |
| `--account LABEL` | Local saved ChatGPT account label for subscription access; defaults to `chatgpt_account` |
| `--provider NAME` | `claude` or `openai`; identical security requirements |
| `--parallel N` | Concurrent reviewers from 1 to 4, default `persona_parallel` (4). Reviewers run concurrently within each round, then consolidation is serial; available for every review |
| `--passes N` | Scheduled passes per persona, default `persona_passes` (3), for 4 × N total slots. A positive integer; budget, failure and cancellation limits can stop work earlier |
| `--model NAME` | Model for all roles (including persona classification and verification), an alias such as `haiku` or `sonnet`, or a full model name. Default: explicit `model_*` settings, otherwise `haiku` for Claude or `gpt-6-luna` for OpenAI |
| `--no-self-check` | Skip the Reviewer’s own self-refutation phase. Independent verification and supported-only publication still apply |
| `--keep-checkout` | Host-only: keep the separate temporary snapshot so you can inspect exactly what the reviewer saw. Model tools are read-only; host mode does not mount the snapshot read-only. Its location is printed. It is in a temporary directory outside the run directory; delete it when done. Otherwise it is deleted when the run ends, even if the run fails |
| `--dry-run` | Host-only target/prompt preview without credentials or model calls; VM requests refuse. Use `--preflight` for no target IO |
| `--verbose` | Print extra progress detail (which model is being called, how many findings are being compared) |
| `--post` | Post the supported-only summary comment to the PR when the review finishes. Nothing is posted without it |
| `--local-path DIR` | Host-only: reuse a matching local clone when all required snapshot objects are present, without changing it. Otherwise use the private snapshot object cache |
| `--config PATH` | **Goes before the subcommand:** `reviewer --config PATH review <url>` (bare `reviewer` only shows help). Explicit trusted operator file outside Git working trees, replacing `~/.code-reviewer/review.toml` |

Options override the settings file for that run only. Other settings, including budgets and agreement thresholds, are set in [Configuration](#configuration).

### Posting a comment

`--post` writes a single comment containing exactly the review summary the tool printed (what you see is what gets posted), at the chosen confidence levels (default: High, Medium and Low; change it with the `post_tiers` setting), labeled as AI-generated, with the pinned commits reviewed and the pass count. Comments exceeding the posting limit are explicitly marked as truncated; the full local summary remains available. It only ever creates or updates one comment per PR from this tool (it recognizes its own hidden marker), so re-running replaces the earlier comment instead of adding more. Inline per-line comments are not supported yet.

## Live progress and candidate counts

VM reviews stream bounded status messages through the existing private SSH/vsock channel. The CLI shows input acquisition/admission, criteria discovery/classification, scheduled reviewer calls and their completion, consolidation, candidate verification, report transfer and cleanup. During a stage without messages, a host-side heartbeat prints every 15 seconds. “Still waiting” identifies the last reported stage; it is not proof that a guest is making progress, an estimated completion time or permission to skip a failed check. Persona calls can be queued when `--parallel` is below four. Normal reviews use timestamped progress instead of duplicate `sandbox:` lifecycle lines, including image build/reuse summaries. Interactive terminals show a rotating ASCII activity indicator on one temporary line. It clears before logs/previews and on normal exit, cancellation or failure; it does not change cursor visibility. Redirected output and `TERM=dumb` terminals receive plain logs and heartbeats without animation.

When the checkout is ready, host reviews print its temporary absolute path and whether it will be removed or kept. VM reviews print `/work/snapshot`, explicitly labelled as inside the disposable VM; there is no corresponding host checkout path.

As soon as a reviewer returns, progress shows proposed/self-rejected counts and **Verification pending**. Round summaries show completed/incomplete slots, new candidates and the cumulative distinct total. Candidate titles, locations, severities and suggestions are withheld until final verification supports publication. Host and VM reviews use the same count-only presentation, so a later-refuted claim is not shown as a provisional defect.

```text
[01:10] correctness: review 1 returned — 1 proposed, 0 self-rejected
[01:10]   Verification pending; candidate details are withheld until supported.
[01:25] still waiting — waiting for reviewers: production-safety
[01:40] checking evidence and consolidating findings
[01:50] Round 1 provisional: 4 completed, 0 incomplete; candidates: 1 new, 1 total. UNCONFIRMED — not independently verified; see the final report.
```

Progress is advisory. It does not change admission, findings, agreement, severity, stopping rules or posting. Every eligible deduplicated candidate is independently investigated; failed, unresolved, refuted and pending candidates do not enter the final report. Final artifacts require validated results, VM/network cleanup and confirmed worker shutdown. `--post` uses only the supported-only final summary.

The private channel validates closed schemas and bounds: 512 frames / 512 KiB per channel, with 32 KiB per frame. It reserves room for lifecycle messages and fails closed on invalid/excessive remote frames. Terminal controls are sanitized and selected-credential matches are redacted/refused. Langfuse receives bounded metadata after cleanup, not candidate text or a live VM trace stream.

## Reading the report

The review summary printed at the end is also saved as `summary.md` in the run directory. It is meant to be shared and can be lengthy when there are many findings. `report.md` is the long version for digging in: what the agent was given, what it ignored, cost, and the details of every pass. The report contains:

| Section | What it tells you |
|---|---|
| Header | PR, pinned head/base/merge-base commits, reviewer model, completed passes, stop reason and estimated cost |
| Verification | Candidate counts by supported/refuted/unresolved/pending status and a warning when investigation is incomplete |
| Findings | Supported findings grouped by category, with primary location/evidence, applicable criterion, conditions, impact, assessment and per-persona agreement; suggestions appear only with separate support |
| Criteria assigned to each persona | Accepted rule headings, sources and paths; discovery details and ignored references are retained in host input artifacts |
| Scheduled passes | Every slot's persona, round, completion/error status and raised/usable/ungrounded/malformed/out-of-scope counts |
| Budget pools; usage and validation | Role/pool accounting, call usage, validation diagnostics and normalization counters |

Self-rejected, refuted and unresolved candidate text is retained only in the private audit. `findings.json` contains both that audit and a distinct supported-only `findings` array; automation consumers must select the latter. Frozen inputs, raw requests/replies and pass findings are retained by host runs; VM exports retain the bounded audit but not these internal files.

The report groups findings under **Correctness → Production safety → Test quality → Code quality**, omitting empty sections. Within each section, findings appear by severity, then file/line/title for stable ordering. A finding assigned to several categories appears once under the first applicable section, with every category still listed. Descriptive titles replace internal IDs in finding headings; supported assessments and their evidence appear beside findings. Internal IDs remain in structured state and diagnostic artifacts. This display order does not change reviewer scheduling, category assignments, votes or confidence.

The report shows each finding's agreement per discovering persona, all assigned criteria, all scheduled slots and their statuses (twelve at the default count), and verification outcome/completion counts. Confidence is measured independently per discovering persona, never across different perspectives. Only supported assessments admit findings to the report. Original candidates, rejected claims, evidence and assessments remain in the private audit; severity and votes are unchanged. See [verification](verification.md).

A run with no completed review says **No review completed**, rather than presenting zero findings as a successful review. Inspect its errors and skipped work in the full report. A completed review with no accepted findings can report zero findings; neither outcome proves the change is issue-free. Completed allowance overruns remain eligible for validation; later budget exhaustion can still prevent consolidation or verification.

How to read the report:

- A finding that every pass found has high agreement; repetition alone does not establish correctness. Identical repeated results do not provide independent proof; inspect the supporting evidence.
- **Mostly Low findings does not mean they are wrong.** Passes explore different code paths, so independent passes complement each other. Low agreement does not bypass verification. Review the supported findings and evidence; the scheduled-pass table shows how many candidates each pass raised, and host runs also retain per-pass notes in `pass-NN/findings.json`. Verifier decisions control publication independently from agreement (see [verification](verification.md)).
- **Duplicates are merged by a model, and merging is not checking.** The Comparator compares each pass's findings with the findings of earlier passes (and with each other) and merges those that report the same issue, so one problem is not listed several times. Borderline cases can go either way: one issue may still appear as two or three findings (the private audit's "variants" retain the other wordings), and occasionally two related issues are merged. The Comparator only decides whether findings are the *same*, never whether they are *true*. A wrong finding that several passes repeat is merged into one finding with high confidence. Every eligible unique candidate is independently investigated before publication. Repetition is not a substitute for evidence (see [verification](verification.md)).
- **One pass cannot rate confidence.** After a single pass (for example `--passes 1`, or a run that stopped early) every finding was found by "1 of 1" passes, which would score 100% and mean nothing. Those findings are shown as **unrated** and a posted comment labels them the same way. Run two or more passes to get High, Medium and Low.
- Candidates first pass deterministic grounding: a whitespace-normalized quote that matches the code, or, for "something is missing", an anchor on a changed hunk. Ungrounded candidates are counted and excluded before merging. A grounded quote or anchor alone does not prove the claim; independent verification must also support it before publication.
- Cost figures come from native CLI accounting or operator-supplied API token rates and may not match billing.

## Preparing your project (optional, recommended)

The reviewer uses explicit project review instructions when available. Otherwise it uses its thirteen built-in [baseline criteria](persona-criteria.md). Project criteria replace the baseline; all four perspectives retain the shared evidence and verification requirements.

### Point to your review instructions

Add a `Code review` section to your root `AGENTS.md` or `README.md`. Write instructions there or link to a repository Markdown file **with any name**:

```markdown
## Code review
Follow [our review instructions](engineering/change-checks.md).
```

Linked files can reference further Markdown guidance, including skill references. Paths resolve relative to the containing document, with repository-root paths also supported. Only links inside accepted review guidance are followed. General architecture and setup documents do not become criteria merely because they exist.

The reviewer also discovers conventionally named review documents, such as `code-review.md` or `review-checklist.md`, and review directories in documentation, skill and rule locations. These are discovery shortcuts, not required project layouts. An unlinked document with an arbitrary name is not automatically discovered; link it from your review section.

Review skills use `SKILL.md` with front-matter name or description explicitly identifying code review, or live in a dedicated review skill directory. Skill scripts and hooks do not execute; model tools remain Read/Grep/Glob.

### Scope and limits

- Instructions come from the pinned base commit. A PR changing instructions is reviewed against the base version.
- Nested README/AGENTS review sections apply to reviewed files beneath their directory. Front-matter `paths:` globs restrict a source to matching reviewed paths.
- Only regular repository Markdown files are loaded. Symlinks, external URLs and absolute paths are excluded; external references are recorded as ignored.
- `criteria_max_chars` limits accepted rule text plus headings (default 200,000 characters), not entire READMEs, unrelated skills or out-of-scope instructions. Referenced documents are counted once; a whole-document inclusion replaces any earlier section-only inclusion.
- Discovery separately bounds its work to 200 source files, 2 MiB per source and 16 MiB of cached source text, measured as UTF-8 bytes. Git blob sizes are checked before content is captured. These resource limits do not consume the accepted-criteria allowance.
- Exceeding either the discovery bounds or accepted-criteria limit stops the review before classification or reviewer calls. The failed report gives the limit and corrective action; instructions are not trimmed and the run does not continue with partial criteria or the baseline. For accepted-criteria overflow, shorten guidance or raise `criteria_max_chars` in operator settings while respecting the complete model-prompt limit. Unavailable references still produce warnings. Enabled baseline rules apply when discovery completes without any project rules.

Accepted sources, inclusion reasons and rule IDs are saved in private input artifacts. Project rules are assigned to relevant personas; unresolved assignments go to all personas with a warning. Reports display criterion titles and sources.

### Customizing the default rules

For a target project, add project criteria and optionally disable built-in rule IDs through `disabled_default_rules`; avoid modifying the reviewer installation for project-specific conventions. To contribute a built-in rule, edit `src/code_reviewer/review/criteria/*.md` using canonical ID-bearing headings and maintain static assignments in `review/personas.json`. Shared rules have one definition. Prompt/criteria changes require authorized comparisons under the [comparison policy](proposal.md#review-quality-experiments).

## Configuration

Operator settings come from `--config PATH` or `~/.code-reviewer/review.toml` only. Current-directory `review.toml` is ignored. Explicit files inside Git working trees are rejected, along with symlinks/hardlinks, non-regular/oversized files, foreign ownership and group/other-write permissions. The loader uses directory-relative no-follow opens and a 64 KiB limit. A security-ready flag is not a configuration option. Copy [review.example.toml](../review.example.toml) and change what you need; every key is optional. Unknown keys are rejected so typos do not pass silently.

| Setting | Default | Meaning |
|---|---|---|
| `provider` | `claude` | Select the runtime adapter: Claude or OpenAI |
| `auth_mode` | `api-key` | Select API-key or subscription access; unsupported combinations refuse without billing fallback. See [LLM support](llm.md) |
| `model_reviewer`, `model_comparator`, `model_resolver`, `model_verifier` | Claude: `haiku`; OpenAI: `gpt-6-luna` | Model alias or full name per role. Persona classification uses `model_resolver`; `model_verifier` investigates every unique candidate and connected conflicts. OpenAI requires the same model ID across roles; `--model` overrides all four. |
| `persona_parallel` | `4` | Concurrent reviewers (1–4), overridden with `--parallel N`; consolidation follows the round barrier |
| `persona_passes` | `3` | Scheduled passes per persona. `--passes N` overrides it for one run; 4 × N review slots, with fixed rounds. One completed pass per persona is unrated |
| `self_check` | `true` | The Reviewer tries to refute its own findings before answering |
| `disabled_default_rules` | `[]` | Default rule IDs to switch off |
| `max_consecutive_failures` | `2` | Consecutive failures per persona; disable only that persona |
| `tier_high`, `tier_medium` | `0.75`, `0.40` | Confidence tier thresholds |
| `max_diff_lines` | `3000` | Refuse PRs with more changed lines (after exclusions) |
| `exclude_patterns` | lockfiles, minified, generated, `vendor/`, `node_modules/`, ... | Files left out of the diff (this list replaces the built-in one) |
| `extra_exclude_patterns` | none | Added to `exclude_patterns` without replacing it. Use it for planning documents such as PRDs and implementation plans (the author's claims, which may drift from the code): `["docs/prds/", "docs/plans/"]`. They are left out of the prompt but listed by path, the reviewer can still open them in the checkout, and findings located in excluded files are dropped and counted. Keep public-facing documentation (API and event docs) out of this list: it must stay reviewed |
| `persona_prompt_max_chars` | `500000` | Persona-only limit on complete system + user text per call, including retry feedback. Oversized inputs are reported and never silently truncated |
| `criteria_max_chars` | `200000` | Accepted project rule text plus headings; overflow stops the review without dropping rules. Independent of discovery resource bounds and the full prompt limit |
| `call_budget_usd`, `call_timeout_s` | `0.50`, `600` | API estimated spend allowance and per-call timeout; subscription mode ignores dollar allowances |
| `chatgpt_account` | `default` | Local saved ChatGPT account label |
| `call_budget_tokens` | `2000000` | Subscription allowance per model conversation, including all tool turns and cache reads; completed overruns remain eligible for validation |
| `round_budget_tokens` | `10000000` | Token allowance per requested round; the effective run threshold is this value multiplied by `persona_passes` |
| `run_budget_tokens` | unset | Optional positive whole-run token threshold overriding automatic round scaling |
| `run_budget_usd` | `3.00` | API dollar threshold for a whole review; new calls stop at the relevant budget check. API token accounting excludes cache reads. Subscription token accounting includes cache reads and conservatively charges incomplete usage. The harness checks before each call/retry and reserves dollars (API) or tokens (subscription) from independent pools; reported usage can overshoot a threshold. API persona call budgets need at least $0.01 |
| `isolation_flags` | `--safe-mode`, `--restricted` | Claude Code flags that keep the Reviewer isolated from your local setup. Host-only customization; VM mode uses the fixed default flags |
| `claude_bin` | `claude` | Host native binary; VM mode uses the digest-pinned `vm_claude_binary` artifact |
| `post_tiers` | `["High","Medium","Low"]` | Confidence tiers shown in the review summary and included in a posted comment. Single-pass (unrated) findings are always included |
| `redact_emails` | `true` | Replace email addresses in the PR title, description and commit messages with `[email]` before they are sent to the model. The diff and repository files are not redacted. Set to `false` to send PR text as written |
| `runs_dir` | `""` | Where runs are stored (default `~/.code-reviewer/runs`) |

Environment variables: `CODE_REVIEWER_HOME` (repository checkout containing the review pipeline and their assets), `CODE_REVIEWER_DATA` (replaces `~/.code-reviewer` for runs, cache and config).

## Persona budget pools

The token threshold defaults to `round_budget_tokens × persona_passes`: 10 million for one round, 20 million for two and 30 million for the default three. One round includes one review from each of the four perspectives. CLI `--passes` overrides are applied before the threshold is calculated, on both host and VM. The startup banner shows the resolved token allowance. An explicit positive `run_budget_tokens` overrides this calculation with a whole-run cap; omit that setting for automatic scaling. The API dollar cap does not scale with rounds.

The API dollar cap or subscription token cap is split at startup: weights 3/3/4 for criteria (Classifier), Comparator and Verifier out of 30, with the remaining 20/30 divided equally among the four personas. API allocations round down to cents, subscription allocations to whole tokens; remainders go to personas in fixed order. Allocations sum to the configured cap. The per-call allowance is reserved at admission; it is not a hard provider-side ceiling.

Before each attempt/retry, atomically reserve its allowance from its own pool; reconcile reported cost (API) or tokens (subscription) afterward. No borrowing or automatic redistribution occurs. For API calls, if cost is unavailable (for example after timeout/cancellation), conservatively consume that attempt's allowance in the budget ledger and increment an unknown-cost counter; `spent_usd` includes these conservative charges, rather than only confirmed spend; `cost_known_for_all_calls` is false for failed calls without reported cost, even with zero reported tokens. Subscription ledgers use `allocated_tokens`, `spent_tokens`, `reserved_tokens` and `unknown_usage_calls`; incomplete/missing-usage calls consume at least their allowance; validated usage is reconciled even if output validation fails. API cost totals remain estimates. Startup output, dry runs, `report.md` and `stats.json` show allocations/accounting. CLI budget enforcement is not a billing guarantee.

A persona budget/failure limit disables only that persona. Criteria model-budget exhaustion uses classification fallback with warnings; discovery resource/criteria-size exhaustion stops before inference. Comparator exhaustion stops further rounds and preserves unconsolidated raw replies without counting their votes. Verifier exhaustion leaves groups pending/unresolved. Completed results survive; failed/unmerged/skipped slots are visible. `incomplete` means some persona slots could not complete and produces a nonzero CLI exit. Ctrl-C cancels the whole run, terminates owned Claude subprocess groups on POSIX, waits for workers and then cleans the checkout. The global token threshold stops new calls when reported usage reaches it; in-flight calls can overshoot it. Complete returned output is retained for validation after an overrun; call stats/traces mark `budget_exceeded` and the report warns. Invalid or incomplete output still fails. OpenAI cannot continue a tool conversation after exceeding its allowance, and exhausted budgets block subsequent role calls and retries. Verification remains mandatory, so retaining output does not guarantee published findings if remaining budgets cannot cover consolidation and verification.

## Where things are stored, and cleaning up

| Path | Contents |
|---|---|
| `~/.code-reviewer/auth/chatgpt.json` | Private reviewer-owned ChatGPT registrations and credentials; outside Git, 0600 under 0700 directory |
| `~/.code-reviewer/runs/vm-<random ID>/` | VM run: private report, summary, stats and findings.json (supported view plus candidate audit); guest raw replies/prompts are not retained |
| `~/.code-reviewer/runs/<time>_<repo>_<pr>/` | Host review: summary/report, master, usage, labels and settings; `input/` holds redacted PR metadata, diff and criteria; `pass-NN/` holds raw replies, findings and mappings. Frozen persona prompts live under `input/personas/`, with candidate investigations under `verification/` and a private `findings.json` audit/automation view |
| `~/.code-reviewer/cache/` | Private bare snapshot object caches; new stores fetch pinned snapshots and selected blobs, not branch history |
| `<system temp dir>/code-reviewer-checkout-*/` | The reviewer's separate PR snapshot, exposed through read-only model tools, **outside the run directory** so nothing the run produced (earlier passes' findings, reports) is next to it. Deleted when the run ends unless host-only `--keep-checkout` |

In host mode, the temporary checkout is deleted when the run ends, even if the run fails (keep it with `--keep-checkout`). Run directories and snapshot caches are private (0700). The snapshot object cache persists and can be deleted when no review is using it; GitHub acquisition repopulates it. Reusing a complete local clone changes none of its refs, branches, configuration or shallow boundaries. Missing snapshots go to the private cache using the matching clone's Git transport or the operator's GitHub CLI protocol preference (SSH/HTTPS); HTTPS credentials stay managed by GitHub CLI, not copied into cache URLs.

## Cost, time and limits

- Reviewers run concurrently within a round, followed by serial consolidation; rounds wait for the slowest active Reviewer. A review can take several minutes. Cost depends on diff/criteria size, model, tool turns and thinking output. Every run reports tokens and estimated cost; budget settings stop further calls at the applicable checks. Use observed usage to tune the next run.
- Only GitHub pull requests are supported (no local-branch mode yet).
- Diffs above `max_diff_lines` are refused rather than reviewed badly. Lockfiles, vendored and generated files do not count.
- The tool does not run tests, linters or builds. Your pipeline still owns those.
- `claude -p` in the installed CLI has no turn limit flag, so calls are bounded by the per-call spend cap and timeout instead.
- OpenAI calls permit at most 32 read-tool calls and 33 response turns, including the final answer. Reaching a tool, token/cost or time limit fails that call; a failed persona slot is reported rather than counted as a completed review.

## Privacy and safety

- **What reaches the selected provider:** PR text, diff, criteria and repository files read by code-reading model roles. Authentication uses the selected adapter; repository labels do not select a provider. Saved prompts/reports also contain code and should stay private.
- **What the Reviewer and persona Verifier can do:** read files in a separate checkout of the PR. The model tool allow-list provides no command, write or network tools. Native isolation flags are intended to exclude local hooks/skills/MCP/config; actual native behavior still needs live verification. Explicit host mode is not an OS sandbox. The checkout lives in its own temporary directory, away from the run's files, so earlier passes' findings are not next to it. Runtime containment checks use the [sandbox deployment command](sandbox-check.md); the tool allow-list alone is not an OS boundary.
- **What the Reviewer cannot see:** pull request comments, review threads, issue comments, other reviewers' or bots' reviews, and other passes' findings. Acquisition supplies the PR title/description, pinned diff, criteria and checkout, without commit-message history. The tool fetches only an allow-list of PR fields from GitHub for the review (a test fails if comments are ever added to it). Posting a comment separately lists the PR's comments to find the tool's own earlier one; that list is never given to the model. Host `--no-sandbox --dry-run` describes the defaults-only inputs without credentials or model calls. Inspect `input/pr.json` and `input/personas/<persona>/` in a run to see what was sent.
- **Personal data in PR text:** email addresses in the PR title, description and commit messages are replaced with `[email]` before anything is sent (setting `redact_emails`, on by default; `git@host:` clone addresses and version strings such as `lodash@4.17.21` are left alone). This covers **only** emails in the PR text. Names, chat links (for example Slack URLs) and anything in the diff or repository files are sent as they are. The internal dry-run output shows the text as it would be sent, and `input/pr.json` records what was sent and how many addresses were replaced.
- **Untrusted PR text:** the PR description and code are treated as data and the model is told never to follow instructions found in them. That reduces the risk of prompt injection but cannot eliminate it. Read findings critically.
- **Nothing outward-facing happens without you:** the supported publishing action is the explicit one-shot `--post` flag. Interactive post-preview/confirmation is part of the parked REPL.
- **Limits:** the reviewer does not scan the diff for secrets or apply content-based restrictions within the admitted checkout. Code-reading roles can inspect its text through bounded read tools. See the [execution contract](security-contract.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `reviewer: command not found` | From the code-reviewer checkout, use `source .venv/bin/activate && reviewer review ...`, or the direct Python invocation in [new-terminal usage](#running-from-a-new-terminal) |
| `pip install -e .` says editable mode requires a setuptools-based build | Old pip. Run `python -m pip install --upgrade pip` first |
| `set vm_claude_binary ... vm_claude_sha256 ... no host fallback` | A Claude review selected VM execution without its verified Linux ARM64 CLI artifact. To reuse the Mac's native Claude login, explicitly add `--no-sandbox`; for VM execution, configure the [native runtime artifact](llm.md#native-runtime-artifact). OpenAI VM reviews use the Python adapter and do not require this artifact |
| Prompt asset not found | Check the active executable/virtual environment matches this checkout. Prompts live under `src/code_reviewer/review/prompts/`; keep the checkout when using an editable install |
| `error: gh ... failed` | Run `gh auth status` and `gh auth login`. Check you can read the repository. For a company-hosted GitHub, log in to that host |
| `cannot lock ref ... is at ... but expected ...` during fetch | A ref changed during Git's update, often because another fetch ran concurrently. Current review fetches ignore configured remote-tracking mappings and disable automatic tag fetching, updating only their private review refs. Retry with a fresh invocation. A new review fetches the latest branch tips; commits are pinned only within a run |
| `VM setup missing Firecracker archive`, `guest kernel` or `Lima worker definition` | Check `vm_artifacts`, `vm_lima_home` and `vm_instance`; restore the [persistent installation](sandbox-check.md#persistent-first-time-setup). Existing empty parent directories are insufficient. Credential access follows successful setup and admission |
| `GitHub public PR metadata returned HTTP ...` | The VM could not obtain public PR metadata, before credential handoff. Check that the PR exists and is publicly accessible and that unauthenticated API access is not rate-limited. Private source authentication is not implemented in VM mode; trusted host execution requires explicit `--no-sandbox` |
| `VM public acquisition failed: ... admission limit` | The VM rejected repository input before credential handoff. Current caps are 2 MiB per text file, 64 MiB of text per tree and 10,000 entries (including binaries). Known binary suffixes are filtered before transfer; other binary contents are omitted after sampling. Neither triggers text-size limits, but any fetched objects still count toward retained Git acquisition limits. An oversized-file error includes only an escaped bounded filename and byte count, not file contents. Changing input scope/limits is a separate explicit decision; recovery or a larger model budget will not fix it |
| `VM review failed during ...` | The message identifies the failing preparation/startup/image/review/cleanup/export phase. Run the no-key deployment check for infrastructure failures; recovery is for abandoned worker state, not missing installations |
| `error: ... refusing to review` | The diff exceeds `max_diff_lines`. Review a smaller PR or raise the limit |
| `error: ... no reviewable changes` | Every changed file matches `exclude_patterns` or `extra_exclude_patterns` |
| `pass N FAILED: ... timed out` or `not a valid JSON object` | Inspect the failure stage and role in the report. Host runs retain `pass-NN/reviewer.raw.json`; VM runs retain bounded reports and the private candidate audit. Check configured timeouts and the selected API/token allowance before retrying |
| `pass N FAILED` mentioning budget | Check which API-dollar or subscription-token pool exhausted and whether a final answer was returned. Complete overrun output is retained for validation; unfinished conversations and subsequent calls can still be blocked. Pools do not borrow. Inspect accounting before adjusting scope or allowances |
| Many findings counted as ungrounded | Inspect grounding counters. Host `pass-NN/findings.json` records rejection reasons; fabricated or mismatched quotes are withheld rather than published |
| Warning: no explicit project code-review instructions loaded | Enabled baseline rules are used. Check discovery warnings for unreadable or oversized sources and the saved criteria index for accepted guidance. |
| Every pass reports only one finding, or findings are all Low | Review the completed-slot counts, per-persona agreement and verification outcomes. Host pass notes and the private audit can explain differences; low agreement alone does not invalidate a supported finding. See [Reading the report](#reading-the-report) |
| No findings at all | Check whether reviews completed and whether candidates were refuted, unresolved or never verified. Inspect verification counts and private findings.json; zero supported findings does not prove the PR is correct |
| `--post` fails | You need permission to comment on the PR, and `gh` must be logged in as that user |

## Host adapter configuration

`--no-sandbox` selects execution location independently from provider and authentication. Follow [LLM access](llm.md#selecting-access) for credentials, model selection, token rates and host/VM sign-in identities; these rules use the same adapter factory in both locations.

## VM review configuration

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
auth_mode = "api-key" # or "subscription"; never chooses billing automatically
```

Use the [persistent worker/artifact setup](sandbox-check.md#persistent-first-time-setup). Keep both installed artifacts and Lima state outside system temporary directories; only per-review staging is temporary. Preflight checks configuration shape without opening artifacts, inspecting the worker or reading a key. Real execution hashes staged artifacts, validates/locks the dedicated stopped worker, prepares clean images and starts one jailed VM. The selected native binary digest joins the connected image's cache identity. The normal command currently supports **public github.com PR acquisition only**; it does not inspect/fetch the target on the workstation. `--local-path` and `--keep-checkout` refuse in VM mode.

For API/native credentials, set only the intended credential in the invoking process environment: `OPENAI_API_KEY`, or for Claude, `ANTHROPIC_API_KEY` with `auth_mode = "api-key"`, or `CLAUDE_CODE_OAUTH_TOKEN` with `auth_mode = "subscription"`. No key/token value belongs in TOML, arguments, images or chat. The native adapter receives only its selected credential, fixed proxy configuration and a fresh guest home; host login files/keychains are not imported. OAuth and API billing never fall back to each other. OpenAI defaults to `gpt-6-luna`; token prices apply only to API-key access. For subscription access, use reviewer-owned [ChatGPT sign-in](llm.md#chatgpt-sign-in) rather than an environment token.

The launcher reads that selected credential only after input admission, external source-access revocation and native/file/network probes pass. Private SSH process pipes and vsock carry bounded frames. The inference proxy permits only the selected provider's fixed endpoints: `api.openai.com`, or `api.anthropic.com` and `platform.claude.com`. Guest-side interactive login, SSO, extra providers/endpoints and custom gateways are unsupported. ChatGPT consent/refresh run on the host, with only an access token handed to the guest. A missing endpoint fails under policy; it does not widen access automatically. See [native authentication details](llm.md).

After VM/network cleanup and confirmed outer-worker shutdown, the host saves `report.md`, `summary.md`, `stats.json` and private `findings.json` in a fresh `vm-<random ID>` directory under `runs_dir`. The directory is 0700 and files 0600; internal prompts/raw replies remain ephemeral in the VM. The terminal/posting path uses the prefiltered summary generated inside the guest. `--post` remains explicit and uses the operator's host GitHub CLI after review. No untrusted Python result object is deserialized.

See the [support and qualification matrix](llm.md#support-and-qualification) for live coverage. A compromised guest can falsify findings/accounting or reuse the credential it receives. VM destruction does not revoke that credential or enforce a hard provider spending cap. Use revocable credentials and retain provider-side limits.

## Binary omissions

The reviewer omits binary contents from the model checkout and diff hunks, in VM and explicit host execution. Known binary filename suffixes (images, archives, compiled artifacts, fonts and media) are excluded before blob download. Other files are downloaded as text candidates, then sampled for NUL or invalid UTF-8 in their first 8000 bytes. Both checks are heuristics, not format validation: text with a binary suffix is omitted, and an unknown binary format can be downloaded before detection. UTF-16 files are therefore omitted too. Source, configuration, dependency declarations, checksums and documentation remain reviewable as text. Repository diff attributes cannot force detected binary contents into the model's diff.

The summary warns about omissions. The report lists escaped paths, change types and base/head Git blob sizes, including unchanged checkout binaries and binary deletions. Size is unknown when an omitted object was not downloaded; that is distinct from an absent file. Here “base” means the pinned merge base used for the diff. Report details are capped at 200 with an explicit remaining count; `stats.json` retains the full omission metadata. Binaries are not certified safe or analyzed for their compiled behavior. A binary-only PR produces metadata and an omission warning, not a content review.

VM tree admission still limits text to 2 MiB per file and 64 MiB per tree, and counts all entries against the 10,000-entry cap. Binary blobs are sampled and excluded before snapshot export, without expanding a full binary archive. Fetch/object storage, deadlines, scratch and result bounds remain in force; a sufficiently large binary dependency can still exceed acquisition limits.

## API errors and retries

Subscription requests explicitly advertise `Accept: text/event-stream`. An HTTP-200 response with an explicitly incompatible media type is a protocol failure. If the header is missing, the same bounded SSE parser must still validate framing and a terminal `response.completed`; missing headers never permit accepting a JSON result as a completed stream. Diagnostics retain only a closed media-type label and any allowlisted error code from a bounded JSON error body. Raw headers, body text and successful non-streaming output are discarded; the call is not automatically replayed.

API failures report the HTTP status and a recognized provider error code/type. `stats.json` call entries include `http_errors` with allowlisted codes, validated request/token limit/reset headers, normalized `Retry-After` seconds and the retry decision. Unknown codes and raw error messages/bodies, arbitrary headers and request identifiers are discarded. A bare 429 can still be ambiguous when the server supplies no recognized code/type.

Temporary HTTP 429 and 500/502/503/504 responses allow at most three attempts for the same request, including the initial attempt. A valid `Retry-After` is a minimum wait; otherwise delays use exponential backoff starting at one second, with a small random jitter. Waiting is cancellable in persona workers and interruptible in sequential calls. Waits and requests share the original call timeout and budget allowance. If the required delay exceeds the remaining deadline or the 60-second per-wait limit, the call stops and reports that it must be retried later; it never retries earlier than requested.

Known quota, credit and spending/usage-limit errors stop immediately, as do authentication errors and other non-temporary HTTP statuses. The shared model-output retry does not restart exhausted/deferred HTTP retries. HTTP attempts are nested within a runner call; their diagnostic entries are separate from the outer call/retry counts. A provider limit is independent of the reviewer's estimated spending cap. Successful responses continue through the ordinary usage accounting; failed calls with unknown usage remain explicitly unknown.

This behavior follows the [provider's rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits). It requires no additional dependency, tracing service or outbound endpoint.

## Optional local tracing

Tracing defaults on. Follow [local Langfuse setup and trace viewing](tracing.md) for installation, project keys, token/cost interpretation and troubleshooting. `tracing = false` opts out; preflight/dry-run do not read tracing credentials or contact Langfuse.

## Subscription commands and limits

`reviewer auth login/status/models/logout` operate on reviewer-owned ChatGPT registrations. Select the same `--account LABEL` and execution location for authentication and review. Use `--no-sandbox` on each command for host access. The default is the configured VM identity. `auth status` is offline; `auth models` contacts the catalog without inference. See [sign-in and storage](llm.md#chatgpt-sign-in).

Subscription mode uses `call_budget_tokens` and the resolved round-scaled run allowance (or explicit `run_budget_tokens` override), including cache reads, rather than dollar pools. Incomplete or missing usage consumes the reserved allowance conservatively; validated usage is reconciled even when the answer fails output validation. API cost is N/A. Persona proportions and no-borrowing behavior apply to tokens. Budgets admit further calls based on observed usage; they cannot enforce a hard provider-side cap on one response. Completed overrun output still goes through normal validation instead of being discarded. Interrupted/failed streams never count as completed output and are not automatically replayed.
