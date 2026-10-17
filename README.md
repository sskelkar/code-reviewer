# code-reviewer

A multi-pass pull request review agent. It runs several **independent** review passes over a GitHub pull request with Claude Code in headless mode, merges the results, and scores each finding by how many passes found it. The output is a Markdown report with findings, token usage and cost. If you choose, it posts a summary comment on the PR after you confirm.

**Where to start**

| I want to... | Go to |
|---|---|
| Review a pull request with this tool | [Part 1: Using the agent](#part-1-using-the-agent) |
| Change or extend this tool | [Part 2: Contributing](#part-2-contributing) |
| Understand why it is designed this way | [proposal.md](docs/proposal.md) (short) or [proposal-full.md](docs/proposal-full.md) (detailed) |

Status: v0. The code is covered by automated tests that use a fake Claude, but it has not yet been exercised against the real Claude CLI or GitHub. Section [Your first run](#your-first-run) is the way to find out whether it works in your environment.

---

# Part 1: Using the agent

## What it does

For a pull request you point it at:

1. It pins the PR to exact commits and reads the diff, the PR title, description and commit messages.
2. It loads review criteria: generic defaults plus the reviewed project's own criteria, if the project describes them in its `AGENTS.md`.
3. It runs several review passes. Each pass is a fresh Claude session that has never seen the other passes. It can read the code in a read-only checkout, but it cannot run commands or change anything.
4. It verifies each finding's evidence mechanically (the quoted code must exist), merges duplicates across passes, and scores confidence as the share of passes that found a finding.
5. It stops when extra passes stop finding anything new, or at a pass or budget limit, and writes the report.

Findings are advisory. Treat the confidence score as a hint, not a verdict, and use `mark` (below) to record which findings were right so you can judge how much to trust the tool.

## Prerequisites

| Need | Details |
|---|---|
| Python | 3.9 or newer. On 3.9 and 3.10 the package `tomli` is also needed; `pip install` pulls it in. |
| git | Any recent version. |
| GitHub CLI `gh` | Logged in (`gh auth login`) with read access to the repositories you review. To post comments, your account must be allowed to comment on the PR. For a company-hosted GitHub, log in to that host with `gh` too. |
| Claude Code `claude` | Installed and logged in (run `claude` once interactively). The agent calls `claude -p` with your login. |
| Permission | Your company must allow sending the reviewed code to Claude through that login (see [Privacy and safety](#privacy-and-safety)). |

## Install

```bash
git clone https://github.com/sskelkar/code-reviewer.git
cd code-reviewer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip      # needed on Python 3.9, whose bundled pip cannot do an editable install from pyproject.toml
pip install -e .
reviewer --help
```

Install it **editable** (`-e`), and keep the cloned directory. v0 reads its prompts and default criteria from the checkout, not from the installed package. If you must move or install it elsewhere, set `CODE_REVIEWER_HOME` to the checkout path.

You need the virtual environment active (`source .venv/bin/activate`) whenever you run `reviewer`.

### Check the environment

```bash
reviewer doctor          # fast, free: checks claude, gh, git, flags, prompts, default criteria
reviewer doctor --live   # also makes ONE small real Haiku call (a few cents)
```

`doctor --live` verifies the things the agent depends on and cannot be tested offline: that the isolation flags work with your login, that only the Read, Grep and Glob tools are available, that a `CLAUDE.md` and a hook planted in the checked-out project are ignored, and that Claude reports cost and tokens. It saves the raw JSON reply to `~/.code-reviewer/doctor-last.json`. If any line says `FAIL`, fix it before reviewing real code ([Troubleshooting](#troubleshooting)).

## Your first run

Do these in order. Together they cost a few dollars at most.

1. `reviewer doctor --live` (above).
2. Pick a **small** PR, ideally one you already know well, in a repository you can read.
3. Run a single pass to see the whole flow cheaply:
   ```bash
   reviewer review https://github.com/OWNER/REPO/pull/123 --passes 1
   ```
4. Open the report it prints (`report: ...`) and the files next to it, especially `pass-01/reviewer.raw.json` and `pass-01/findings.json`. Check that findings have real quotes, that the cost line is filled in, and that the criteria section lists what you expect.
5. Only then run a full review (default up to 6 passes).

## Reviewing a pull request

### Interactive (recommended)

Start the REPL inside the project you want to review. It then uses your local clone, so nothing large is downloaded.

```bash
cd ~/code/my-service
source ~/code/code-reviewer/.venv/bin/activate
reviewer
reviewer> review https://github.com/my-org/my-service/pull/123
```

You can also write `owner/repo#123`. While it works it prints one line per stage and pass:

```
[00:04] diff: 12 files, 340 changed lines (2 excluded)
[00:09] criteria: 12 default + 6 project rules from 3 files, 1 ignored references
[00:41] pass 1: 5 new, 0 matched, 0 ungrounded · $0.17 · quiet streak 0/2
...
[07:12] converged after 4 passes
```

Run options: `review <url> --passes N --no-self-check --model sonnet --keep-checkout --verbose`.

If the PR belongs to a different repository than your current directory, it asks first and then uses a cached clone under `~/.code-reviewer/cache/`. Outside any git repository it does the same.

When the review ends you get a summary and these commands:

| Command | What it does |
|---|---|
| `show F-007` | Full detail of one finding |
| `mark F-007 real\|nit\|wrong` | Record your judgment. Saved in the run directory; later used to build evals |
| `missed "no length check in create()"` | Record a real issue the agent did not find |
| `drop F-007` / `undrop F-007` | Exclude a finding from the comment you may post |
| `post [--tiers High,Medium]` | Preview the PR comment, then post it **only if you answer `y`** |
| `summary` | Show the summary again |
| `open` | Open the report file |
| `help`, `quit` | |

Ctrl-C during a review stops the current call and gives you a report from the passes that finished.

### One-shot (scripts)

```bash
reviewer review https://github.com/my-org/my-service/pull/123 [--passes 4] [--model sonnet] [--no-self-check] [--keep-checkout] [--verbose] [--local-path DIR] [--post]
```

It prints the report path and exits (non-zero if the review failed). It **never posts** unless you pass `--post`, which is your explicit authorization.

### Posting a comment

`post` (REPL) or `--post` (one-shot) writes a single summary comment containing the findings at the chosen confidence levels (default High and Medium), labeled as AI-generated, with the commit reviewed and the pass count. It only ever creates or updates one comment per PR from this tool (it recognizes its own hidden marker), so re-running replaces the earlier comment instead of adding more. Inline per-line comments are not supported yet.

## Reading the report

`report.md` in the run directory (the path is printed at the end) contains:

| Section | What it tells you |
|---|---|
| Header | PR, commits reviewed, models, passes completed, why it stopped, diff size, estimated cost |
| Criteria applied | Default and project rules used, project criteria files, references that were ignored (for example a wiki link), warnings (for example "this PR modifies the criteria files") |
| Summary | Finding counts by confidence tier and severity |
| High / Medium / Low confidence | Findings. Confidence is the share of completed passes that found it: **High** at 75% or more, **Medium** at 40% or more, **Low** below. Each finding shows `file:line`, the rule, the evidence, a suggestion and "seen in x/N passes" |
| Rejected by self-check | Findings the model raised and then refuted itself. Not scored, shown for transparency |
| Appendix | New findings per pass (the discovery curve), pass-to-pass overlap, per-role and per-pass tokens and cost, and counters for findings dropped because their evidence could not be verified |

How to read it:

- A finding that every pass found is much more likely to be real than one found once, but it is still not guaranteed. **Overlap near 1.00** means the passes behaved almost identically, so confidence tells you little.
- Findings are only kept if their evidence checks out: an exact quote that exists in the code, or, for "something is missing", an anchor on a changed hunk. Unverifiable findings are counted in the appendix and dropped.
- Cost figures are Claude Code's estimates and may not match your billing.

## Preparing your project (optional, recommended)

The agent applies two sets of criteria:

- **Defaults**: generic, language-agnostic rules shipped with the agent ([criteria/defaults/general.md](criteria/defaults/general.md)).
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
- **Keep it small.** About 120,000 characters (roughly 30,000 tokens) of project criteria are used; the rest is dropped with a warning.
- Without an `AGENTS.md`, or if nothing in it resolves, only the defaults are used and the report says so.
- Only the root `AGENTS.md` is read, not ones in subdirectories.

### Customizing the default rules

Edit [criteria/defaults/general.md](criteria/defaults/general.md). Each rule is a heading of the form `## [GEN-013] Title` followed by the rule text. The ID is what findings cite. To turn rules off for your runs, list them in `disabled_default_rules`, or set `criteria_mode` to `project_only` or `defaults_only`.

## Configuration

Settings come from a TOML file, first found of: `--config PATH`, `./review.toml` (current directory), `~/.code-reviewer/review.toml`. Copy [review.example.toml](review.example.toml) and change what you need; every key is optional. Unknown keys are rejected so typos do not pass silently.

| Setting | Default | Meaning |
|---|---|---|
| `model_reviewer`, `model_comparator`, `model_resolver` | `haiku` | Model alias or full name for each role (for example `sonnet`) |
| `self_check` | `true` | The Reviewer tries to refute its own findings before answering |
| `criteria_mode` | `merge` | `merge`, `project_only` or `defaults_only` |
| `criteria_manifest` | `""` | Path to a JSON file that replaces the criteria resolver's answer (reproducible experiments) |
| `disabled_default_rules` | `[]` | Default rule IDs to switch off |
| `min_passes`, `max_passes` | `3`, `6` | Passes always run / at most run |
| `quiet_k` | `2` | Stop after this many consecutive passes with no new finding (after `min_passes`) |
| `max_consecutive_failures` | `2` | Abort after this many failed passes in a row |
| `tier_high`, `tier_medium` | `0.75`, `0.40` | Confidence tier thresholds |
| `max_diff_lines` | `1500` | Refuse PRs with more changed lines (after exclusions) |
| `exclude_patterns` | lockfiles, minified, generated, `vendor/`, `node_modules/`, ... | Files left out of the diff |
| `criteria_max_chars` | `120000` | Cap on project criteria text |
| `call_budget_usd`, `call_timeout_s` | `0.50`, `600` | Spend and time cap for every single Claude call |
| `run_budget_usd`, `run_budget_tokens` | `3.00`, `600000` | Caps for a whole review; it stops between calls once exceeded |
| `isolation_flags` | `--safe-mode`, `--restricted` | Claude Code flags that keep the Reviewer isolated from your local setup. Do not remove unless you know why |
| `claude_bin` | `claude` | Path or name of the Claude Code binary |
| `post_tiers` | `["High","Medium"]` | Confidence tiers included in a posted comment |
| `runs_dir` | `""` | Where runs are stored (default `~/.code-reviewer/runs`) |
| `candidate_window`, `candidate_all_threshold`, `candidate_max`, `grounding_line_window` | `20`, `30`, `40`, `30` | Tuning for duplicate matching and line-number tolerance. Rarely needed |

Environment variables: `CODE_REVIEWER_HOME` (location of the checkout containing `prompts/` and `criteria/`), `CODE_REVIEWER_DATA` (replaces `~/.code-reviewer` for runs, cache and config).

## Where things are stored, and cleaning up

| Path | Contents |
|---|---|
| `~/.code-reviewer/runs/<time>_<repo>_<pr>/` | One review: `report.md`, `master.json`, `stats.json`, `labels.json` (your marks), `config.json` (settings, prompt hashes, versions), `input/` (diff, criteria used), `pass-NN/` (raw Claude output, findings, merge decisions) |
| `~/.code-reviewer/cache/` | Bare clones used when you review a repository other than the current one |
| `refs/code-reviewer/*` in your local clone | The PR head and base commits fetched for a review. Your branches and working tree are never changed |
| `~/.code-reviewer/doctor-last.json` | Raw reply from `reviewer doctor --live` |

The temporary checkout inside a run is deleted when the run ends (keep it with `--keep-checkout`). To clean up, delete run directories you no longer need. To remove the fetched refs from a clone:

```bash
git for-each-ref --format='%(refname)' refs/code-reviewer | xargs -n1 git update-ref -d
```

## Cost, time and limits

- Each pass is a full agent run, so a review takes minutes per pass and several passes. These numbers have not been measured yet; your first runs will tell you. Every run reports its own tokens and estimated cost, and the budget settings above cap the spend.
- Only GitHub pull requests are supported (no local-branch mode yet).
- Diffs above `max_diff_lines` are refused rather than reviewed badly. Lockfiles, vendored and generated files do not count.
- The tool does not run tests, linters or builds. Your pipeline still owns those.
- `claude -p` in the installed CLI has no turn limit flag, so calls are bounded by the per-call spend cap and timeout instead.

## Privacy and safety

- **What is sent to Claude:** the PR title, description and commit messages, the diff, the criteria text, and any repository files the Reviewer reads. It goes through your Claude Code login. Make sure your company policy allows that for the repository, and do not point the agent at repositories or PRs containing customer, rider or other personal data.
- **What the Reviewer can do:** read files in a separate checkout of the PR. It cannot run commands, edit files, use the network or load your local Claude configuration (skills, hooks, MCP servers, `CLAUDE.md`). `reviewer doctor --live` checks that these restrictions hold.
- **Untrusted PR text:** the PR description and code are treated as data and the model is told never to follow instructions found in them. That reduces the risk of prompt injection but cannot eliminate it. Read findings critically.
- **Nothing outward-facing happens without you:** the only action on GitHub is `post`, after a preview and your confirmation (or the explicit `--post` flag).
- **Not done in v0:** secret scanning of the diff, and filtering of what the Reviewer may read inside the checkout. See [proposal.md](docs/proposal.md) section 5.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `reviewer: command not found` | The virtual environment is not active: `source .venv/bin/activate` in the code-reviewer directory |
| `pip install -e .` says editable mode requires a setuptools-based build | Old pip. Run `python -m pip install --upgrade pip` first |
| `config error: ... needs Python 3.11+ or the 'tomli' package` | Run `pip install tomli`, or reinstall with `pip install -e .` |
| `doctor`: `claude is installed` fails | Install Claude Code, or set `claude_bin` in `review.toml` |
| `doctor`: required flags missing | Your Claude Code is too old or different. Compare `claude --help` with `isolation_flags` and the flags in `src/code_reviewer/claude_cli.py` |
| `doctor --live`: more than Read, Grep, Glob available, or canary or hook not ignored | Isolation is not working. Do not review untrusted PRs until this is fixed |
| `error: gh ... failed` | Run `gh auth status` and `gh auth login`. Check you can read the repository. For a company-hosted GitHub, log in to that host |
| `error: ... refusing to review` | The diff exceeds `max_diff_lines`. Review a smaller PR or raise the limit |
| `error: ... no reviewable changes` | Every changed file matches `exclude_patterns` |
| `pass N FAILED: ... timed out` or `not a valid JSON object` | Look at `pass-NN/reviewer.raw.json` in the run directory. Raise `call_timeout_s` or `call_budget_usd`, or try another model |
| `pass N FAILED` mentioning budget | The call hit `call_budget_usd`. Raise it, or review a smaller PR |
| Many findings counted as ungrounded | See the `reason` of each in `pass-NN/findings.json`. The model may be quoting from memory. Try `--model sonnet` |
| Warning: criteria resolver failed | The review continued with default criteria only. See `input/resolver.raw.json` |
| No findings at all | May be correct. Check `notes` in `pass-NN/findings.json` and compare with the diff. Try `--model sonnet` |
| `post` fails | You need permission to comment on the PR, and `gh` must be logged in as that user |

---

# Part 2: Contributing

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

## Repository layout

```
code-reviewer/
  README.md  pyproject.toml  review.example.toml
  docs/                             proposal.md (short design doc) and proposal-full.md (detailed)
  criteria/defaults/general.md      default review rules (IDs GEN-001...)
  prompts/                          prompt templates and JSON schemas, see prompts/README.md
  src/code_reviewer/                the package (see the code map below)
  tests/                            unit and integration tests with fakes
```

## Design in one paragraph

A deterministic Python harness runs everything. Large language models only **propose**: a Criteria Resolver names criteria files, a Reviewer reports findings, a Comparator says which findings are duplicates. The harness **validates every model output, decides, and writes all state and files**. Each model call is a separate headless `claude -p` process with read-only tools and a fresh context, so passes are independent and the Reviewer cannot change anything. Confidence is arithmetic over independent passes. The reasoning, the alternatives we rejected and the roadmap are in [proposal.md](docs/proposal.md).

## How a review runs

1. `pr_source` fetches the PR head and base into `refs/code-reviewer/` and pins SHAs.
2. `engine._load_diff` parses the merge-base diff, drops excluded files, refuses empty or oversized PRs.
3. `git_ops.export_tree` exports the PR head to a checkout (symlinks skipped).
4. `engine._resolve_criteria` loads default rules, then resolves project criteria from the **base** commit: the harness extracts candidate paths from `AGENTS.md`, the `CriteriaResolver` picks files, the harness validates (in-repo regular files only), and rule IDs are assigned.
5. `engine._run_passes` loops. Each pass is `PassRunner.run`: `Reviewer` → `FindingsPreparer` (validate and ground) → `Comparator` → `MasterList.apply`.
6. `StopPolicy` decides after every pass whether to stop.
7. `report.render_report` writes the report; `RunStore` writes all artifacts.

## Code map

Start with [engine.py](src/code_reviewer/engine.py): `Engine.review` is the whole story in about 40 lines, and each step it calls lives in its own module.

| Module | Responsibility |
|---|---|
| `engine.py` | Wires the steps together and emits progress events |
| `pr_source.py` | Turns a PR reference into a `PRContext` pinned to exact commits (`gh` + `git`) |
| `diffview.py` | Parses the diff, excludes lockfiles and generated files, renders the numbered diff the Reviewer sees |
| `criteria.py` | Default rules, project rules found via `AGENTS.md` (in-repo only, from the base branch), rule IDs |
| `roles.py` | The three LLM roles: `CriteriaResolver`, `Reviewer`, `Comparator`. Each owns its prompt variables and output validation |
| `llm.py` | Every `claude` call goes through `ClaudeCaller`: one retry, usage accounting, raw-output log |
| `claude_cli.py` | The subprocess wrapper around `claude -p` and the JSON envelope parser |
| `passes.py` | One pass: review, check evidence, compare with the master list, merge |
| `findings.py` | Validates findings and checks their evidence exists (quote in the code, or anchor over a changed hunk) |
| `master.py` | The merged finding list, comparator decision validation, confidence tiers, pass overlap |
| `stop_policy.py` | When the loop stops: convergence, failures, budget. Pure logic |
| `stats.py`, `report.py` | Usage and cost accounting; the Markdown report |
| `run_store.py` | The on-disk layout of a run. The only module that knows file names |
| `repl.py`, `cli.py`, `progress.py`, `post.py`, `doctor.py` | Frontends, progress lines, PR comment posting, environment checks |
| `models.py`, `config.py`, `templates.py`, `events.py`, `git_ops.py` | Shared types, settings, prompt rendering, events, git and gh helpers |

Module dependencies point one way: `models`, `config` and `events` at the bottom; `engine` and the frontends at the top. Keep it that way (the engine imports the report, not the reverse).

## Tests

```bash
python3 -m unittest discover -s tests -t .                 # everything
python3 -m unittest tests.test_stop_policy -v              # one module
```

| Test module | Covers |
|---|---|
| `test_diffview`, `test_criteria`, `test_findings`, `test_master`, `test_stop_policy` | Pure logic: diff parsing, criteria discovery and validation, grounding, merging, stop rules |
| `test_claude_cli_and_templates` | Envelope parsing, command building, prompt rendering and escaping, config validation |
| `test_runner_plumbing` | `ClaudeRunner` against a fake `claude` executable: stdin, flags, working directory, timeout, exit codes |
| `test_engine` | End to end with a throwaway git repository and a scripted `FakeRunner`: convergence, retries, failures, budget, criteria from the base branch, prompt-injection escaping, REPL commands, git helpers |

Things to know when writing tests:

- `tests/helpers.py` has `make_repo` (a base commit and a feature commit in a temp repo) and `FakeRunner`. `FakeRunner` tells the roles apart by phrases in their **system prompts** ("identify which files", "deduplicate code-review findings"). If you reword those phrases in `prompts/`, update `FakeRunner`.
- Tests render the real prompt templates, so adding a `{{variable}}` to a prompt without supplying it in `roles.py` fails the tests.
- Nothing in the suite touches the network. Keep it that way; live behavior is checked with `reviewer doctor --live`.

## Common changes

| To... | Do this |
|---|---|
| Change what a role is asked | Edit the files in `prompts/` ([prompts/README.md](prompts/README.md) lists every variable). Prompt hashes are stored in each run's `config.json`, so runs stay comparable |
| Change output structure | Edit the schema in `prompts/schemas/`, the validation in `findings.py` or `master.py`, and the report |
| Add or change default rules | Edit `criteria/defaults/*.md` using `## [GEN-xxx] Title` headings. IDs must be unique (a test checks) |
| Add a setting | Add a field with a default to `Config` in `config.py` (the type is inferred from the default), validate it if needed, document it in `review.example.toml` and the settings table in this README |
| Add a progress message | Emit an event with `engine.emit(...)`, handle it in `progress.py` |
| Add an LLM role | Add `prompts/<role>.system.md`, `.user.md` and a schema, register the name in `templates.ROLES`, add a class in `roles.py` that builds a `CallSpec` and calls `ClaudeCaller.call`, and wire it in `engine.py` |
| Add a REPL command | Add a `cmd_*` method and an entry in `Session.dispatch` in `repl.py`, and the help text |
| Change the stop rules | `stop_policy.py` and `tests/test_stop_policy.py` |

## Code conventions

- Standard library only at runtime. Compatible with Python 3.9: start modules with `from __future__ import annotations`, no `match` statements, no `X | Y` unions outside annotations.
- LLMs propose, code decides: validate every model output in code and never execute it. All files are written by the harness.
- Modules do one job; keep functions short and named for what they do. Put new logic in the module that owns the concept rather than in `engine.py`, which should stay a readable list of steps.
- Keep prompts language-agnostic. Project- or language-specific guidance belongs in criteria, not in prompts.
- Add a test for every behavior change. Keep the README and [prompts/README.md](prompts/README.md) in sync with changes to usage or prompts, and update [proposal.md](docs/proposal.md) if a design decision changes.

## Debugging a review

Every run directory is a full record. Useful files:

| File | Question it answers |
|---|---|
| `pass-NN/reviewer.raw.json` | What did Claude actually return (every attempt, including errors)? |
| `pass-NN/findings.json` | Which findings were kept or dropped, and why (`status` and `reason`)? |
| `pass-NN/comparator.raw.json`, `pass-NN/mapping.json` | How were findings matched to earlier ones? |
| `input/diff.txt`, `input/criteria_block.md`, `input/criteria_index.json` | Exactly what the Reviewer was given |
| `input/criteria_manifest.json`, `input/resolver.raw.json` | Which project criteria files were chosen, and what was ignored |
| `config.json` | Settings, Claude Code version, prompt hashes |
| `stats.json` | Every call with tokens, cost, duration |

Re-run with `--keep-checkout` to keep the exact checkout the Reviewer saw.

## Verifying against the real Claude CLI

The offline tests cannot prove how Claude Code behaves. After upgrading Claude Code, or when something looks off, run `reviewer doctor --live` and a one-pass review, and check the points in [proposal.md](docs/proposal.md) section 9: flags accepted together, isolation canaries, JSON envelope fields, budget and timeout behavior. If the envelope shape differs from what `claude_cli.parse_envelope` expects, fix the parser and add a test using the real shape.

## Known limitations and roadmap

- Not yet exercised against real Claude and GitHub (see the status at the top).
- GitHub PRs only; root `AGENTS.md` only; `--verbose` shows stages, not live tool activity.
- Deferred security hardening: secret scanning, symlink and sensitive-file scrubbing inside the checkout, sanitizing posted text, instruction-pattern warnings (the hardening backlog in proposal section 5).
- Planned: evals from your `mark` and `missed` labels, a Verifier pass, multi-perspective review, inline PR comments, MCP server, raw-API backends (proposal section 10).
