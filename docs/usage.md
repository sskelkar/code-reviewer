# Usage reference

Start with the [README quick start](../README.md#quick-start). The REPL is parked and bare `reviewer` shows help. This guide covers one-shot commands, interpreting results, project criteria, configuration, storage and troubleshooting.

[Commands](#reviewing-a-pull-request) · [Results](#reading-the-report) · [Project criteria](#preparing-your-project-optional-recommended) · [Configuration](#configuration) · [Storage](#where-things-are-stored-and-cleaning-up) · [Troubleshooting](#troubleshooting)

## Reviewing a pull request

### Check configuration

```bash
reviewer review OWNER/REPO#123 --preflight
reviewer review OWNER/REPO#123 --provider openai --model MODEL_ID --passes 1 --preflight
```

The CLI prints JSON with the provider, effective settings, configuration errors and execution choice. It performs no target-directory discovery, Git operations, model/version calls, credential reads or posting. Exit 1 means configuration is missing/unsupported. `ready: true` means offline settings passed, it does not verify files, the worker or authentication. `runtime_checked: false` makes clear this is an offline configuration report, not a live check. OpenAI needs explicit model IDs rather than Claude aliases. Preflight does not establish account access, price correctness or runtime compatibility.

The CLI runs one review and exits. VM execution requires trusted worker/artifact settings; host execution requires explicit operator selection. Both execution locations use the selected runtime credential.

### Execution selection

VM is the default and never falls back to the host. Use `--no-sandbox` or trusted `sandbox = false` for direct-host execution. VM dry-run is unsupported. `--preflight` cannot be combined with `--post` or `--dry-run`.

The CLI selects execution before acquisition and uses the same pipeline and selected adapter in either location. See the [security contract](security-contract.md).

### Review options

Use these with `reviewer review <url> ...`; `reviewer review --help` describes them. The parked REPL retains shared option definitions for later use.

| Option | Meaning |
|---|---|
| `--no-sandbox` | Explicit direct-host acquisition/review; no VM startup. Also selectable with trusted `sandbox = false`; no automatic fallback |
| `--preflight` | Show release blockers and effective request without target/model/credential activity |
| `--auth NAME` | `api-key` (default) or `subscription`; independent of provider/location. ChatGPT uses reviewer-owned sign-in; Claude uses native subscription login on the host or an explicit OAuth token for host/VM |
| `--account LABEL` | Local saved ChatGPT account label for subscription access; defaults to `chatgpt_account` |
| `--provider NAME` | `claude` or `openai`; identical security requirements |
| `--parallel N` | Concurrent reviewers from 1 to 4, default `persona_parallel` (4). Reviewers run concurrently within each round, then consolidation is serial; available for every review |
| `--passes N` | Scheduled passes per persona, default `persona_passes` (3), for 4 × N total slots. A positive integer; budget, failure and cancellation limits can stop work earlier |
| `--model NAME` | Model for all roles (including persona classification and verification), an alias such as `haiku` or `sonnet`, or a full model name. Default: the `model_*` settings (`haiku`) |
| `--criteria MODE` | `merge`, `project_only` or `defaults_only`; overrides `criteria_mode` for this run. `defaults_only` skips the project's own rules (smaller prompt) |
| `--no-self-check` | Skip the phase where the reviewer tries to refute its own findings. Cheaper, but findings are not double-checked |
| `--keep-checkout` | Host-only: keep the separate temporary snapshot so you can inspect exactly what the reviewer saw. Model tools are read-only; host mode does not mount the snapshot read-only. Its location is printed. It is in a temporary directory outside the run directory; delete it when done. Otherwise it is deleted when the run ends, even if the run fails |
| `--dry-run` | Host-only target/prompt preview without credentials or model calls; VM requests refuse. Use `--preflight` for no target IO |
| `--verbose` | Print extra progress detail (which model is being called, how many findings are being compared) |
| `--post` | **One-shot only.** Post the summary comment to the PR when the review finishes. Nothing is posted without it. The retained interactive frontend is parked |
| `--local-path DIR` | **One-shot only.** Local clone to use as the git object store. Default: the current directory, if it is a clone of the same repository; otherwise a cached clone is used |
| `--config PATH` | **Goes before the subcommand:** `reviewer --config PATH review <url>` (bare `reviewer` only shows help). Explicit trusted operator file outside Git working trees, replacing `~/.code-reviewer/review.toml` |

Options override the settings file for that run only. Everything else (budgets, thresholds, criteria mode, ...) is set in [Configuration](#configuration).

### Posting a comment

`--post` writes a single comment containing exactly the review summary the tool printed (what you see is what gets posted), at the chosen confidence levels (default: High, Medium and Low; change it with the `post_tiers` setting), labeled as AI-generated, with the commit reviewed and the pass count. It only ever creates or updates one comment per PR from this tool (it recognizes its own hidden marker), so re-running replaces the earlier comment instead of adding more. Inline per-line comments are not supported yet.

## Live progress and provisional findings

VM reviews stream bounded status messages through the existing private SSH/vsock channel. The CLI shows input acquisition/admission, criteria discovery/classification, scheduled reviewer calls and their completion, consolidation, conflict verification, report transfer and cleanup. During a stage without messages, a host-side heartbeat prints every 15 seconds. “Still waiting” identifies the last reported stage; it is not proof that a guest is making progress, an estimated completion time or permission to skip a failed check. Persona calls can be queued when `--parallel` is below four. Normal reviews use timestamped progress instead of duplicate `sandbox:` lifecycle lines, including image build/reuse summaries. Interactive terminals show a rotating ASCII activity indicator on one temporary line. It clears before logs/previews and on normal exit, cancellation or failure; it does not change cursor visibility. Redirected output and `TERM=dumb` terminals receive plain logs and heartbeats without animation.

As soon as a reviewer returns, a glimpse shows proposed/self-rejected counts and up to three candidate titles, severities and locations, labelled **Verification pending**. Persona glimpses arrive before the round barrier, so other reviewers can still be running. They are raw model proposals: locations and claims have not been checked against the repository and duplicates have not been merged. Clearly self-rejected entries are withheld, malformed preview fields are omitted and further proposed entries are counted as not shown. The  flow exposes the same glimpse before grounding.

After a consolidated round, a provisional summary shows completed/incomplete slots, new findings and the cumulative distinct total. It lists up to ten newly actionable master findings with ID, severity, title and file/line. Further entries are counted as omitted. Duplicate sightings and self-rejected/ungrounded findings are not presented as new defects. Explicit host reviews use the same per-round/pass preview events.

```text
[01:10] correctness: review 1 returned — 1 proposed, 0 self-rejected
[01:10]   Verification pending
[01:10]   [medium] app.py:42 — Zero batch size prevents loop progress
[01:25] still waiting — waiting for reviewers: production-safety
[01:40] checking evidence and consolidating findings
[01:50] Round 1 provisional: 4 completed, 0 incomplete; candidates: 1 new, 1 total. UNCONFIRMED — not independently verified; see the final report.
[01:50]   [medium] F-001 app.py:42 — Zero batch size prevents loop progress
```

Previews are advisory, not final review results. They do not change findings, agreement, severity, stopping rules or posting. Independent verification runs only for flagged conflicts; not every finding receives it. A later failure can prevent a final report even after previews have appeared. Final artifacts still require validated results, VM/network cleanup and confirmed worker shutdown; `--post` uses only the final summary.

Both kinds of preview titles and locations can contain private repository information: treat terminal output as private review material. Raw prompts, replies, source quotes and suggestions are not streamed. Terminal controls are sanitized and exact selected-credential matches are redacted/refused. Progress is bounded to 512 frames / 512 KiB per channel, with 32 KiB per frame; normal guest emission reserves space for worker lifecycle messages and stops at its advisory cap. Invalid/excessive remote frames fail closed. The transport deadline and existing final-result limits remain unchanged. Langfuse receives metadata only and exports after cleanup; it does not receive these textual previews or a live VM trace stream.

## Reading the report

The review summary printed at the end is also saved as `summary.md` in the run directory. It is meant to be shared and can be lengthy when there are many findings. `report.md` is the long version for digging in: what the agent was given, what it ignored, cost, and the details of every pass. The report contains:

| Section | What it tells you |
|---|---|
| Header | PR, commits reviewed, models, passes completed, why it stopped, diff size, estimated cost |
| Criteria applied | Default and project rules used, project criteria files, references that were ignored (for example a wiki link), warnings (for example "this PR modifies the criteria files") |
| Overview | Finding counts by confidence tier and severity |
| Findings | Category-grouped findings with file/line, criterion, evidence, suggestion and per-persona agreement. One completed pass per persona is unrated |
| Rejected by self-check | Findings the model raised and then refuted itself. Not scored, shown for transparency |
| Appendix | New findings per pass (the discovery curve), pass-to-pass overlap, per-role and per-pass tokens and cost, and counters for findings dropped because their evidence could not be verified, and for rule citations that were repaired or unknown |

The report groups findings under **Correctness → Production safety → Test quality → Code quality**, omitting empty sections. Within each section, findings appear by severity, then file/line/title for stable ordering. A finding assigned to several categories appears once under the first applicable section, with every category still listed. Descriptive titles replace internal IDs in finding headings; conflict annotations refer to titles and locations. Internal IDs remain in structured state and diagnostic artifacts. This display order does not change reviewer scheduling, category assignments, votes or confidence.

The report shows each finding's agreement per discovering persona, all assigned criteria, all scheduled slots and their statuses (twelve at the default count), and any conflict annotations. Confidence is measured independently per discovering persona, never across different perspectives. Its Verifier annotates flagged conflicts without removing findings or changing votes/severity. See [how the modes differ](../README.md#review-flow).

How to read the report:

- A finding that every pass found has high agreement; repetition alone does not establish correctness. **Overlap near 1.00** means the passes behaved almost identically, so confidence tells you little.
- **Mostly Low findings does not mean they are wrong.** Passes explore different code paths, so independent passes complement each other. Read all findings (Low included) and judge them manually; the appendix shows how many findings each pass raised, and host runs also retain per-pass notes in `pass-NN/findings.json`. The persona Verifier annotates flagged conflicts without changing confidence (see the roadmap in [docs/proposal.md](proposal.md)).
- **Duplicates are merged by a model, and merging is not checking.** The Comparator compares each pass's findings with the findings of earlier passes (and with each other) and merges those that report the same issue, so one problem is not listed several times. Borderline cases can go either way: one issue may still appear as two or three findings (the "variants" in the report show the other wordings), and occasionally two related issues are merged. The Comparator only decides whether findings are the *same*, never whether they are *true*. A wrong finding that several passes repeat is merged into one finding with high confidence. The separate Verifier in persona mode annotates only flagged conflicts; checking every finding remains later work (see the [persona flow](../README.md#review-flow)).
- **One pass cannot rate confidence.** After a single pass (for example `--passes 1`, or a run that stopped early) every finding was found by "1 of 1" passes, which would score 100% and mean nothing. Those findings are shown as **unrated** and a posted comment labels them the same way. Run two or more passes to get High, Medium and Low.
- Findings are only kept if their evidence checks out: a whitespace-normalized quote that matches the code, or, for "something is missing", an anchor on a changed hunk. Unverifiable findings are counted in the appendix and dropped.
- Cost figures come from native CLI accounting or operator-supplied API token rates and may not match billing.

## Preparing your project (optional, recommended)

The agent applies two sets of criteria:

- **Defaults**: generic, language-agnostic rules shipped with the agent ([the persona criteria](persona-criteria.md)).
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


The default criteria use `COR-*`, `SAFE-*`, `TEST-*` and `CQ-*` by subject. Saved runs retain their original criteria; compare the saved criteria blocks/indexes as well as prompt hashes when evaluating runs. Tests verify criteria loading and delivery; they do not establish review quality. Reports and the PR comment name each criterion by its title and source; the IDs stay internal.

The pipeline uses thirteen canonical rules with static assignments; see the [persona criteria guide](persona-criteria.md). Accepted project rules are classified to at least one persona; unresolved assignments fall back to all personas with a warning.

### Customizing the default rules

Edit `src/code_reviewer/review/criteria/*.md` using canonical ID-bearing headings, and maintain static assignments in `review/personas.json`. Shared rules have one definition. Prompt/criteria changes require authorized comparisons under the [comparison policy](proposal.md#review-quality-experiments).

## Configuration

Operator settings come from `--config PATH` or `~/.code-reviewer/review.toml` only. Current-directory `review.toml` is ignored. Explicit files inside Git working trees are rejected, along with symlinks/hardlinks, non-regular/oversized files, foreign ownership and group/other-write permissions. The loader uses directory-relative no-follow opens and a 64 KiB limit. A security-ready flag is not a configuration option. Copy [review.example.toml](../review.example.toml) and change what you need; every key is optional. Unknown keys are rejected so typos do not pass silently.

| Setting | Default | Meaning |
|---|---|---|
| `provider` | `claude` | Select the runtime adapter: Claude or OpenAI |
| `auth_mode` | `api-key` | Select API-key or subscription access; unsupported combinations refuse without billing fallback. See [LLM support](llm.md) |
| `model_reviewer`, `model_comparator`, `model_resolver`, `model_verifier` | `haiku` | Model alias or full name per role. Persona classification uses `model_resolver`; `model_verifier` handles flagged conflicts. OpenAI requires a single explicit model ID across roles. |
| `persona_parallel` | `4` | Concurrent reviewers (1–4), overridden with `--parallel N`; consolidation follows the round barrier |
| `persona_passes` | `3` | Scheduled passes per persona. `--passes N` overrides it for one run; 4 × N review slots, with fixed rounds. One completed pass per persona is unrated |
| `self_check` | `true` | The Reviewer tries to refute its own findings before answering |
| `criteria_mode` | `merge` | `merge`, `project_only` or `defaults_only` |
| `criteria_manifest` | `""` | Host-only operator JSON file replacing the Resolver answer; VM mode refuses this override |
| `disabled_default_rules` | `[]` | Default rule IDs to switch off |
| `max_consecutive_failures` | `2` | Consecutive failures per persona; disable only that persona |
| `tier_high`, `tier_medium` | `0.75`, `0.40` | Confidence tier thresholds |
| `max_diff_lines` | `3000` | Refuse PRs with more changed lines (after exclusions) |
| `exclude_patterns` | lockfiles, minified, generated, `vendor/`, `node_modules/`, ... | Files left out of the diff (this list replaces the built-in one) |
| `extra_exclude_patterns` | none | Added to `exclude_patterns` without replacing it. Use it for planning documents such as PRDs and implementation plans (the author's claims, which may drift from the code): `["docs/prds/", "docs/plans/"]`. They are left out of the prompt but listed by path, the reviewer can still open them in the checkout, and findings located in excluded files are dropped and counted. Keep public-facing documentation (API and event docs) out of this list: it must stay reviewed |
| `persona_prompt_max_chars` | `500000` | Persona-only limit on complete system + user text per call, including retry feedback. Oversized inputs are reported and never silently truncated |
| `criteria_max_chars` | `200000` | Cap on project criteria text |
| `call_budget_usd`, `call_timeout_s` | `0.50`, `600` | API estimated spend allowance and per-call timeout; subscription mode ignores dollar allowances |
| `chatgpt_account` | `default` | Local saved ChatGPT account label |
| `call_budget_tokens` | `200000` | Subscription per-call token allowance, including cache reads |
| `run_budget_usd`, `run_budget_tokens` | `3.00`, `2000000` | Thresholds for a whole review; new calls stop at the relevant budget check. API token accounting excludes cache reads. Subscription token accounting includes cache reads and conservatively charges incomplete usage. The harness checks before each call/retry and reserves dollars (API) or tokens (subscription) from independent pools; reported usage can overshoot a threshold. API persona call budgets need at least $0.01 |
| `isolation_flags` | `--safe-mode`, `--restricted` | Claude Code flags that keep the Reviewer isolated from your local setup. Host-only customization; VM mode uses the fixed default flags |
| `claude_bin` | `claude` | Host native binary; VM mode uses the digest-pinned `vm_claude_binary` artifact |
| `post_tiers` | `["High","Medium","Low"]` | Confidence tiers shown in the review summary and included in a posted comment. Single-pass (unrated) findings are always included |
| `redact_emails` | `true` | Replace email addresses in the PR title, description and commit messages with `[email]` before they are sent to the model. The diff and repository files are not redacted. Set to `false` to send PR text as written |
| `runs_dir` | `""` | Where runs are stored (default `~/.code-reviewer/runs`) |

Environment variables: `CODE_REVIEWER_HOME` (repository checkout containing the review pipeline and their assets), `CODE_REVIEWER_DATA` (replaces `~/.code-reviewer` for runs, cache and config).

## Persona budget pools

The API dollar cap or subscription token cap is split at startup: weights 3/3/4 for criteria (Resolver + Classifier), Comparator and Verifier out of 30, with the remaining 20/30 divided equally among the four personas. API allocations round down to cents, subscription allocations to whole tokens; remainders go to personas in fixed order. Allocations sum to the configured cap. The per-call cap still applies.

Before each attempt/retry, atomically reserve its allowance from its own pool; reconcile reported cost (API) or tokens (subscription) afterward. No borrowing or automatic redistribution occurs. For API calls, if cost is unavailable (for example after timeout/cancellation), conservatively consume that attempt's allowance in the budget ledger and increment an unknown-cost counter; `spent_usd` includes these conservative charges, rather than only confirmed spend; `cost_known_for_all_calls` is false for failed calls without reported cost, even with zero reported tokens. Subscription ledgers use `allocated_tokens`, `spent_tokens`, `reserved_tokens` and `unknown_usage_calls`; incomplete/missing-usage calls consume at least their allowance; validated usage is reconciled even if output validation fails. API cost totals remain estimates. Startup output, dry runs, `report.md` and `stats.json` show allocations/accounting. CLI budget enforcement is not a billing guarantee.

A persona budget/failure limit disables only that persona. Criteria exhaustion follows existing discovery/classification fallback with warnings. Comparator exhaustion stops further rounds and preserves unconsolidated raw replies without counting their votes. Verifier exhaustion leaves groups pending/unresolved. Completed results survive; failed/unmerged/skipped slots are visible. `incomplete` means some persona slots could not complete and produces a nonzero CLI exit. Ctrl-C cancels the whole run, terminates owned Claude subprocess groups on POSIX, waits for workers and then cleans the checkout. The global token threshold stops new calls when reported usage reaches it; in-flight calls can overshoot it.

## Where things are stored, and cleaning up

| Path | Contents |
|---|---|
| `~/.code-reviewer/auth/chatgpt.json` | Private reviewer-owned ChatGPT registrations and credentials; outside Git, 0600 under 0700 directory |
| `~/.code-reviewer/runs/vm-<random ID>/` | VM run: private report, summary and stats only; guest raw replies/prompts are not retained |
| `~/.code-reviewer/runs/<time>_<repo>_<pr>/` | Host review: summary/report, master, usage, labels and settings; `input/` holds redacted PR metadata, diff and criteria; `pass-NN/` holds raw replies, findings and mappings. Frozen persona prompts live under `input/personas/`, with conditional annotations under `verification/` |
| `~/.code-reviewer/cache/` | Bare clones used when you review a repository other than the current one |
| `<system temp dir>/code-reviewer-checkout-*/` | The reviewer's separate PR snapshot, exposed through read-only model tools, **outside the run directory** so nothing the run produced (earlier passes' findings, reports) is next to it. Deleted when the run ends unless host-only `--keep-checkout` |
| `refs/code-reviewer/*` in your local clone | The PR head and base commits fetched for a review. Your branches and working tree are never changed |

In host mode, the temporary checkout is deleted when the run ends, even if the run fails (keep it with `--keep-checkout`). New host run directories and cache directories are created with mode 0700, independent of umask. This does not change permissions of old saved data. To clean up, delete run directories you no longer need. To remove the fetched refs from a clone:

```bash
git for-each-ref --format='%(refname)' refs/code-reviewer | xargs -n1 git update-ref -d
```

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
- **What the Reviewer cannot see:** pull request comments, review threads, issue comments, other reviewers' or bots' reviews, and other passes' findings. It is given only the PR title, description and commit messages, the diff, the criteria and the checkout. The tool fetches only an allow-list of PR fields from GitHub for the review (a test fails if comments are ever added to it). Posting a comment separately lists the PR's comments to find the tool's own earlier one; that list is never given to the model. Host `--no-sandbox --dry-run` describes the defaults-only inputs without credentials or model calls, and `input/pr.json` and `input/reviewer.*.md` in a run to see what was sent.
- **Personal data in PR text:** email addresses in the PR title, description and commit messages are replaced with `[email]` before anything is sent (setting `redact_emails`, on by default; `git@host:` clone addresses and version strings such as `lodash@4.17.21` are left alone). This covers **only** emails in the PR text. Names, chat links (for example Slack URLs) and anything in the diff or repository files are sent as they are. The internal dry-run output shows the text as it would be sent, and `input/pr.json` records what was sent and how many addresses were replaced.
- **Untrusted PR text:** the PR description and code are treated as data and the model is told never to follow instructions found in them. That reduces the risk of prompt injection but cannot eliminate it. Read findings critically.
- **Nothing outward-facing happens without you:** the supported publishing action is the explicit one-shot `--post` flag. Interactive post-preview/confirmation is part of the parked REPL.
- **Limits:** the reviewer does not scan the diff for secrets or apply content-based restrictions within the admitted checkout. Code-reading roles can inspect its text through bounded read tools. See the [execution contract](security-contract.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `reviewer: command not found` | The virtual environment is not active: `source .venv/bin/activate` in the code-reviewer directory |
| `pip install -e .` says editable mode requires a setuptools-based build | Old pip. Run `python -m pip install --upgrade pip` first |
| Prompt asset not found | Check the active executable/virtual environment matches this checkout. Prompts live under `src/code_reviewer/review/prompts/`; keep the checkout when using an editable install |
| `error: gh ... failed` | Run `gh auth status` and `gh auth login`. Check you can read the repository. For a company-hosted GitHub, log in to that host |
| `cannot lock ref ... is at ... but expected ...` during fetch | A ref changed during Git's update, often because another fetch ran concurrently. Current review fetches ignore configured remote-tracking mappings and disable automatic tag fetching, updating only their private review refs. Use a fresh one-shot invocation after updating. A new review fetches the latest branch tips; commits are pinned only within a run |
| `VM setup missing Firecracker archive`, `guest kernel` or `Lima worker definition` | Check `vm_artifacts`, `vm_lima_home` and `vm_instance`; restore the [persistent installation](sandbox-check.md#persistent-first-time-setup). Existing empty parent directories are insufficient. Credential access follows successful setup and admission |
| `GitHub public PR metadata returned HTTP ...` | The VM could not obtain public PR metadata, before credential handoff. Check that the PR exists and is publicly accessible and that unauthenticated API access is not rate-limited. Private source authentication is not implemented in VM mode; trusted host execution requires explicit `--no-sandbox` |
| `VM public acquisition failed: ... admission limit` | The VM rejected repository input before credential handoff. Current caps are 2 MiB per text file, 32 MiB of text per tree and 5000 entries (including binaries). Binary contents are omitted rather than triggering text-size limits; retained Git acquisition limits still apply. An oversized-file error includes only an escaped bounded filename and byte count, not file contents. Changing input scope/limits is a separate explicit decision; recovery or a larger model budget will not fix it |
| `VM review failed during ...` | The message identifies the failing preparation/startup/image/review/cleanup/export phase. Run the no-key deployment check for infrastructure failures; recovery is for abandoned worker state, not missing installations |
| `error: ... refusing to review` | The diff exceeds `max_diff_lines`. Review a smaller PR or raise the limit |
| `error: ... no reviewable changes` | Every changed file matches `exclude_patterns` or `extra_exclude_patterns` |
| `pass N FAILED: ... timed out` or `not a valid JSON object` | For host runs, inspect `pass-NN/reviewer.raw.json`. VM exports contain only report/summary/stats; use their failure accounting. Raise `call_timeout_s` or `call_budget_usd`, or try another model |
| `pass N FAILED` mentioning budget | The call hit `call_budget_usd`. Raise it, or review a smaller PR |
| Many findings counted as ungrounded | See the `reason` of each in `pass-NN/findings.json`. The model may be quoting from memory. Try `--model sonnet` |
| Warning: criteria resolver failed | Resolver-selected rules may be missing; enabled defaults and automatically included project rules can still apply. Check the criteria index, warnings and `input/resolver.raw.json` |
| Every pass reports only one finding, or findings are all Low | Passes may stop early or pick a different issue each time. Check `notes` in each `pass-NN/findings.json` for what the pass verified and what it left "unverified", and compare the number of findings per pass in the report's appendix. See [Reading the report](#reading-the-report) |
| No findings at all | May be correct. Check `notes` in `pass-NN/findings.json` and compare with the diff. Try `--model sonnet` |
| `post` fails | You need permission to comment on the PR, and `gh` must be logged in as that user |

## VM execution status

Normal VM reviews acquire public GitHub PRs inside the VM, select the configured adapter, privately hand off credentials after boundary checks and return bounded reports after worker shutdown. Explicit host dispatch remains available. Private URL authentication and normal-CLI capsule input remain unsupported; low-level no-key checks retain capsules. See the [execution contract](security-contract.md) and [VM runbook](sandbox-check.md).

## Host adapter configuration

Use the same provider/authentication settings and factory as the guest. `--no-sandbox` chooses host execution; it does not choose authentication. Choose the credential source described in [the LLM support matrix](llm.md#selecting-access). OpenAI API-key access requires explicit model/token rates. Claude host subscription mode uses an explicit `CLAUDE_CODE_OAUTH_TOKEN` override when set; otherwise it checks and reuses the native Claude subscription/SSO login. Complete your normal `claude` login first. Cached Console/API-key logins are refused. See [Claude sign-in](llm.md#claude-subscription-sign-in) for selection and isolation details. ChatGPT subscription access uses `reviewer auth login` for the selected host/VM identity; see [sign-in](llm.md#chatgpt-sign-in).

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

For API/native credentials, set only the intended credential in the invoking process environment: `OPENAI_API_KEY`, or for Claude, `ANTHROPIC_API_KEY` with `auth_mode = "api-key"`, or `CLAUDE_CODE_OAUTH_TOKEN` with `auth_mode = "subscription"`. No key/token value belongs in TOML, arguments, images or chat. The native adapter receives only its selected credential, fixed proxy configuration and a fresh guest home; host login files/keychains are not imported. OAuth and API billing never fall back to each other. OpenAI requires an explicit model; token prices apply only to API-key access. For subscription access, use reviewer-owned [ChatGPT sign-in](llm.md#chatgpt-sign-in) rather than an environment token.

The launcher reads that selected credential only after input admission, external source-access revocation and native/file/network probes pass. Private SSH process pipes and vsock carry bounded frames. The inference proxy permits only the selected provider's fixed endpoints: `api.openai.com`, or `api.anthropic.com` and `platform.claude.com`. Guest-side interactive login, SSO, extra providers/endpoints and custom gateways are unsupported. ChatGPT consent/refresh run on the host, with only an access token handed to the guest. A missing endpoint fails under policy; it does not widen access automatically. See [native authentication details](llm.md).

After VM/network cleanup and confirmed outer-worker shutdown, the host saves `report.md`, `summary.md` and `stats.json` in a fresh `vm-<random ID>` directory under `runs_dir`. The directory is 0700 and files 0600; internal prompts/raw replies remain ephemeral in the VM. The terminal/posting path uses the prefiltered summary generated inside the guest. `--post` remains explicit and uses the operator's host GitHub CLI after review. No untrusted Python result object is deserialized.

See the [support and qualification matrix](llm.md#support-and-qualification) for live coverage. A compromised guest can falsify findings/accounting or reuse the credential it receives. VM destruction does not revoke that credential or enforce a hard provider spending cap. Use revocable credentials and retain provider-side limits.

## Binary omissions

The reviewer omits binary contents from the model checkout and diff hunks, in VM and explicit host execution. Detection samples the first 8000 bytes for NUL or invalid UTF-8, independent of extensions; this is a heuristic, not format validation. UTF-16 files are therefore omitted too. Source, configuration, dependency declarations, checksums and documentation remain reviewable as text. Repository diff attributes cannot force detected binary contents into the model's diff.

The summary warns about omissions. The report lists escaped paths, change types and base/head Git blob sizes, including unchanged checkout binaries and binary deletions. Here “base” means the pinned merge base used for the diff. Report details are capped at 200 with an explicit remaining count; `stats.json` retains the full omission metadata. Binaries are not certified safe or analyzed for their compiled behavior. A binary-only PR produces metadata and an omission warning, not a content review.

VM tree admission still limits text to 2 MiB per file and 32 MiB per tree, and counts all entries against the 5000-entry cap. Binary blobs are sampled and excluded before snapshot export, without expanding a full binary archive. Fetch/object storage, deadlines, scratch and result bounds remain in force; a sufficiently large binary dependency can still exceed acquisition limits.

## API errors and retries

Subscription requests explicitly advertise `Accept: text/event-stream`. An HTTP-200 response with an explicitly incompatible media type is a protocol failure. If the header is missing, the same bounded SSE parser must still validate framing and a terminal `response.completed`; missing headers never permit accepting a JSON result as a completed stream. Diagnostics retain only a closed media-type label and any allowlisted error code from a bounded JSON error body. Raw headers, body text and successful non-streaming output are discarded; the call is not automatically replayed.

API failures report the HTTP status and a recognized provider error code/type. `stats.json` call entries include `http_errors` with allowlisted codes, validated request/token limit/reset headers, normalized `Retry-After` seconds and the retry decision. Unknown codes and raw error messages/bodies, arbitrary headers and request identifiers are discarded. A bare 429 can still be ambiguous when the server supplies no recognized code/type.

Temporary HTTP 429 and 500/502/503/504 responses allow at most three attempts for the same request, including the initial attempt. A valid `Retry-After` is a minimum wait; otherwise delays use exponential backoff starting at one second, with a small random jitter. Waiting is cancellable in persona workers and interruptible in sequential calls. Waits and requests share the original call timeout and budget allowance. If the required delay exceeds the remaining deadline or the 60-second per-wait limit, the call stops and reports that it must be retried later; it never retries earlier than requested.

Known quota, credit and spending/usage-limit errors stop immediately, as do authentication errors and other non-temporary HTTP statuses. The shared model-output retry does not restart exhausted/deferred HTTP retries. HTTP attempts are nested within a runner call; their diagnostic entries are separate from the outer call/retry counts. A provider limit is independent of the reviewer's estimated spending cap. Successful responses continue through the ordinary usage accounting; failed calls with unknown usage remain explicitly unknown.

This behavior follows the [provider's rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits). It requires no additional dependency, tracing service or outbound endpoint.

## Optional local tracing

Tracing is enabled by default; set `tracing = false` in trusted operator configuration to opt out. Metadata is exported to local Langfuse at **http://localhost:4000**. Install the optional `tracing` extra using Python 3.13 and supply local project keys through `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`. Preflight/dry-run skip tracing credential/network access. After a review, open the printed `Trace:` link or `tracing.url` in its `stats.json`. See [viewing a review's trace](tracing.md#view-a-reviews-trace), [missing traces](tracing.md#missing-traces) and [setup/cache details](tracing.md); no new review command or guest-network permission is required.


## Subscription commands and limits

`reviewer auth login/status/models/logout` operate on reviewer-owned ChatGPT registrations. Select the same `--account LABEL` and execution location for authentication and review. Use `--no-sandbox` on each command for host access. The default is the configured VM identity. `auth status` is offline; `auth models` contacts the catalog without inference. See [sign-in and storage](llm.md#chatgpt-sign-in).

Subscription mode uses `call_budget_tokens` and `run_budget_tokens`, including cache reads, rather than dollar pools. Incomplete or missing usage consumes the reserved allowance conservatively; validated usage is reconciled even when the answer fails output validation. API cost is N/A. Persona proportions and no-borrowing behavior apply to tokens. Limits stop further work after observed usage; they cannot enforce a hard provider-side cap on one response. Interrupted/failed streams never count as completed output and are not automatically replayed.
