# Handover: state of the project and what comes next

For an AI agent (or a person) taking over. Read [`AGENTS.md`](../AGENTS.md) first for the working rules. This file says where things stand and what to do next. The design reasoning lives in [`proposal.md`](proposal.md); usage and the code map are in the [README](../README.md).

**Current state: 2026-10-19.** The ten generic review criteria and local eval harness are implemented. Active public cases remain eight pinned changes (four regression/repair pairs across Express, HTTPX and Resilience4j). `evals/RUBRIC.md` draft-2 is implemented as a human-label policy with deterministic scoring, but is not calibrated. Public keys remain agent-curated/provisional; no model-quality result exists.

**Latest user direction:** finish the harness, then update this handover. The user cannot usefully judge unfamiliar public cases and expects to refine the rubric on their own project. **Only Claude is allowed to review production code.** Do not inspect their production clone, diffs, saved findings or private cases in this Codex chat. The user will run Claude reviews and judge findings locally. Do not ask them to paste production material here. Generic framework development and public/synthetic verification are allowed.

**Working tree:** all changes remain uncommitted, including prior criteria/docs changes. Preserve them. No commits, pushes or paid model calls were made. All **224 tests** pass on Python 3.9.6 and 3.13 (see verification below). Tests validate the harness, not Claude's review quality.

**Immediate next step:** the user can follow `evals/README.md` to create one private pinned case outside this repository, preview it, and when ready run a one-model/one-pass pilot locally through Claude. Judge actual findings, refine/confirm expectations privately, then compare models/pass counts on the same frozen cases. Do not expand the public dataset or ask for abstract rubric rubber-stamping. No production project/path was supplied or accessed, and no production storage directory was created.

## 1. What exists

A Python package (`code_reviewer`, command `reviewer`) that reviews a GitHub PR with several independent Claude Code headless passes:

1. Check out the PR head in a temporary directory (outside the run directory, always deleted afterward) and fetch only an allow-list of PR fields.
2. Build the inputs once: redacted PR text, the filtered diff with line markers, and the review criteria.
3. Run passes. Each pass is a fresh `claude -p` session with only Read, Grep and Glob, blind to other passes. The prompt is byte-identical every pass.
4. After each pass, the harness grounds every finding against the code (quote must match, or an anchor over a changed hunk for "absent" findings), repairs or neutralizes the rule ID, and applies the model's own self-check verdict.
5. The Comparator maps each pass's findings onto a master list (same finding, or new). The harness validates its answer.
6. A stop policy ends the run (min 3 passes, max 6, stop after 2 consecutive quiet passes, stop after 2 failures, token budget).
7. Output: a shareable summary (printed, and the same text posted as a PR comment after confirmation), a detailed `report.md`, token and cost accounting, and every input and raw model reply saved under `~/.code-reviewer/runs/<run>/`.

Confidence is the share of completed passes that found a finding: High at 75% or more, Medium at 40% or more, Low below that, Unrated if fewer than 2 passes completed.

**Criteria** come from three places, merged with project rules taking precedence:
- Defaults: `criteria/defaults/general.md` (ten criteria numbered `GEN-001` through `GEN-010` in checklist order).
- Files named by the Resolver model from links in the project's root `AGENTS.md` (read from the base branch, never the PR head).
- Files added by location without a model call: an `AGENTS.md` in or above any directory containing a changed file, and every `.claude/rules/*.md` file that has no `paths:` front matter or whose `paths:` globs match a changed file.

**Interfaces:** `reviewer` (interactive REPL with `review`, `show`, `drop`, `undrop`, `mark`, `missed`, `summary`, `post`, `open`, `help`), `reviewer review <pr>` (one-shot), `reviewer doctor [--live]`, `python -m code_reviewer.replay <run dir>` (re-runs only the Comparator on saved findings), and `python -m code_reviewer.evals` (local eval workflow; section 4).

## 2. Decisions to keep (and why)

| Decision | Why |
|---|---|
| Models propose, code decides | Every model answer is validated; nothing the model says is written or posted unchecked |
| Passes are independent and identical | Agreement between passes only means something if passes cannot influence each other. Each pass is a new session, the prompt does not contain the pass number |
| Claude Code headless with the user's login, not an API key | The user has only an enterprise Claude Code login. `--bare` is unusable with it |
| Haiku for all roles by default | Cheap. Sonnet is consistent but more aggressive on merges and about 15 times the comparator cost. `model_comparator` is configurable |
| Show Low-confidence findings for now | Better to see them while we learn what is useful. `post_tiers` defaults to all three tiers |
| Criteria read from the base branch | A PR cannot edit the rules it is judged by |
| Findings are never dropped for a bad rule ID | An early version dropped them and silently lost about a third of the findings on one PR |
| Cache reads are excluded from the token budget | Multi-turn reviews re-read cached context cheaply. Cache creation is counted |
| Security loosened for v0 | Controlled learning environment. The backlog is in `proposal.md` section 5 |
| No commits unless asked | The user reviews the diff first |

## 3. What the runs have taught us

These are summaries of real runs on private repositories (identifiers deliberately left out; see `AGENTS.md` rule 3).

1. **A first prompt made each pass report one finding.** Six passes each raised one different issue, so everything scored Low and a real issue seen earlier was missed. The Reviewer prompt now asks for a thorough review and says to report every issue; findings per pass rose from about 1 to 2–7.
2. **Two defects hid agreement on a larger PR:** 11 of 30 raised findings were dropped because the model wrote rule IDs in brackets, and one issue appeared as three findings. Both fixed (rule IDs are repaired or set to `none`; the Comparator treats a broader and a narrower statement of the same gap as one finding). Replaying the saved findings twice with Haiku gave identical groupings, with the formerly scattered issue at 6 of 6 passes.
3. **Agreement is not correctness.** One finding was raised by 4 of 6 passes and was wrong: the reviewer did not know a project-specific data-access rule, so a refactor it flagged as risky was actually the required form. The rule existed in the repository but only in a subagent-memory file and an eval rubric, and no criteria file or `AGENTS.md` mentioned it. All six passes looked at the right code; none searched for the documentation. Correlated errors across passes are the main weakness of agreement-based confidence, and the reason for the Verifier (P2) and the context pack below.
4. **Observability gap:** `reviewer.raw.json` does not record the tool calls a pass made, so we cannot tell what a pass searched. Only the pass's own `notes` ("checked X, not verified Y") say what it opened.

## 4. Current work: criteria and eval design

### 4.1 Agreed review criteria (implemented)

`criteria/defaults/general.md` now contains only the new ten criteria, numbered in checklist order. The user explicitly requested dropping the old list and legacy ID mappings:

| ID | Topic |
|---|---|
| GEN-001 | Correctness |
| GEN-002 | Simplicity and maintainability |
| GEN-003 | Clarity and documentation, including API/event/other contract-document updates |
| GEN-004 | Security and privacy |
| GEN-005 | Input validation and finite bounds (no unbounded inputs) |
| GEN-006 | Failure handling and resource management |
| GEN-007 | Data integrity and concurrency, including missing atomicity for related database writes |
| GEN-008 | Internal contract consistency |
| GEN-009 | Test coverage and appropriate test levels |
| GEN-010 | Test independence and responsible mocking |

Each has **Inspect / Report / Do not report** paragraphs. Criteria must be assessable from the repository rather than undocumented external behavior. Architecture and performance were deliberately excluded from generic defaults; projects can add them. Multiple writes alone do not prove a transaction is needed, and mocking alone is not a defect. Test levels follow the behavior being tested, with no numerical test-pyramid quota. Existing test coverage counts.

The rules are loaded into `<review_criteria>` in the Reviewer user prompt. `prompts/reviewer.system.md` and the output schema are unchanged: they still define scope, consequences, evidence, self-checks and JSON. Tests check that all ten complete rule bodies reach the actual Reviewer invocation. There is no live evidence yet that this revision improves quality. Historical rule IDs can mean different topics: use each saved run's criteria block/index, not its ID alone or template hashes alone, when comparing runs.

### 4.2 Active open-source cases (eight total)

See `evals/CASES.md` for all eight pinned GitHub diff links, full source revisions, line counts, evidence and limits. Active manifests are `evals/suites/express.json` (two cases), `httpx.json` (four) and `resilience4j.json` (two). The eight cases are four regression/repair pairs:

| Pair | Expected regression | Validation |
|---|---|---|
| Express ETag | Transfer-Encoding condition also suppresses ETag generation | Actual send function exercised before/regression/repair; full HTTP suite not run |
| HTTPX client certificate | verify=False returns before applying a supplied certificate | Actual helper exercised with standard-library SSLContext; no real TLS handshake |
| HTTPX empty Zstandard body | Decoder flush rejects an empty encoded response | Source and upstream regression tests; zstandard not installed locally |
| Resilience4j configuration copy | Derived builder aliases/mutates original config | Source and upstream regression tests; no local JDK |

This changes the earlier suggested six-defect/two-control mix to four of each, using well-supported pairs rather than extending the search. The user set the total cap at eight; the ratio was an agent proposal. Repairs are provisional controls, not certified free of other defects. Additional findings need adjudication. Keep related pairs together when splitting calibration/held-out data: there are four underlying scenarios, not eight independent ones.

Default-branch tips were observed on cloning on 2026-10-19 and are pinned in each suite. Every case has its own full base/head SHAs; review the historical head, not the current default branch. Future pushes must not silently update the dataset. The Resilience4j introduction has 1746 changed lines, above the normal 1500 cap. Including it needs an explicit max_diff_lines=2000 eval setting; no global limit has been changed and the manifest recommendation is not an override.

The user's earlier-project feedback is preserved in `evals/reserve/`: trip creation uncertain; resident filter no issue seen/requested replacement; amount-entry issue agreed; async action tests no issue seen. The user then said their projects were too shallow and asked to check open source. Those earlier six candidates are not in the active set. Repository-reference permission does not extend to copying unrelated private production runs, secrets or profile data.

No real model evaluations, full external-project suites, or human-adjudicated findings exist. The harness is implemented and tested offline; case curation and passing harness tests do not establish review quality.

### 4.3 Implemented local evaluation workflow

See **`evals/README.md`** for copyable commands and private use. Entry point: `python -m code_reviewer.evals`.

| Command | Behavior |
|---|---|
| `init` | Resolve local base/head refs, pin merge-base/head SHA, write a new neutral case outside both repositories; no model calls |
| `plan` | Preflight all selected commits/diffs, save Reviewer inputs for the matrix; no model/version calls or checkout |
| `run` | Paid Claude reviews via existing Engine; only Reviewer model varies |
| `label` | Record human verdict/checks, issue ID, evidence reason and duplicate reference locally; no model calls |
| `grade` | Validate frozen artifacts/labels and calculate JSON/Markdown reports, optionally combining suites; no model calls |
| `rekey` | Copy saved reviews under revised expected/status/reason fields and reset all labels; no model calls |

Each suite targets one existing clone. Nothing fetches, modifies its working tree or posts comments. Expected answers/notes/case IDs/later repair references/commit messages do not enter Reviewer prompts. Titles/descriptions must be neutral; historical head files may still reveal clues. Production manifests, code references, outputs, labels and detailed reports must stay physically outside code-reviewer, not merely gitignored. Output location checks resolve symlinks and reject either repository.

Evals use **defaults_only** criteria, ignoring the normal criteria mode/manifest; configured disabled defaults still apply. This keeps generic criteria fixed and avoids Resolver variance. It does not evaluate normal project-rule discovery. Comparator/settings stay fixed. Requested passes mean fixed attempts (`min_passes = max_passes`); budgets/failures can make trials incomplete. This does not evaluate the production early-stop policy.

One trial uses one temporary head checkout shared read-only across fresh Claude sessions. Separate trials get fresh checkouts, removed after completion/errors. Engine Reviewer tools remain Read/Grep/Glob; no tool permissions/prompts/schemas were changed. Only `run` calls Claude; local storage does not imply local inference.

Existing run/call budgets remain active. Optional `--max-total-cost` stops scheduling between trials, reduces remaining trial/call budgets and halts if cost is unknown. Limits are soft and may overshoot; incomplete/skipped/cancelled trials and incurred/unknown costs stay visible. Ctrl-C stops subsequent trials. There is no resume command and output directories must be new.

### 4.4 Human judgments and scores

The rubric is **provisional**, not rubber-stamp-validated. The three implemented checks are correctness/support, introduced-or-worsened-by-the-change, and specificity/usefulness. `judgments.json` binds check-level labels to rubric/master hashes. Labels: match, additional, wrong, duplicate, uncertain, pending. Valid findings pass all checks; wrong findings need demonstrated failure; uncertainty needs an unresolved check. Additional valid discoveries are not automatically false positives; duplicate valid issues explicitly reference an original with the same stable issue ID.

Precision = distinct valid issues / reported scored findings (duplicates penalized). Known-issue recall uses only cases explicitly human-attested `expectation_status: confirmed` with an `expectation_reason`. All eight public keys remain provisional. Python validates the attestation field, not whether the human actually verified it. Empty reviews have undefined precision; zero recall only when confirmed expected issues exist. Repairs are not certified clean.

Pending/uncertain labels or incomplete scheduled trials suppress the group's final quality scores; individual trial status/scores and all spending remain visible. Combined reports require identical frozen code/prompt/default-criteria/rubric/settings hashes, reject overlapping trials, and flag unequal case/repetition coverage. Scenario counts group pairs. Bookkeeping readiness does not imply statistical significance, judge calibration or broad model superiority.

Suite/rubric/environment/master hashes reject accidental edits. The hashes are not tamper-proof signatures. Actual prompts/criteria/config/model usage are retained per run; requested aliases can change across time, so compare close in time or use explicit model IDs. Revising only answer keys is supported through `rekey`, which copies the old reviews and resets all labels for consistent re-adjudication. Code/prompt/criteria/rubric changes require a new experiment. Bundled-claim splitting, per-tier/pass scores, `mark`/`missed` imports, independent Comparator scoring and LLM judging are not implemented.

Research links and authored examples remain in the rubric/proposal history. These are not actual Reviewer outputs or calibration data. Production reviews and judgments belong to Claude and the user locally; Codex should work only on generic/public/synthetic material here.

### 4.5 Files, verification and next actions

Implemented modules:
- `eval_cases.py`: version-1 suite validation, local SHA pinning/PRContext, path/hash helpers.
- `eval_runner.py`: preflight, fixed-attempt matrix, environment/rubric snapshots, costs/failures/cancellation.
- `eval_judgments.py`: human-label schema and rubric consistency/duplicate checks.
- `eval_grading.py`: deterministic trial/group metrics and combined reports.
- `evals.py`: CLI (`init`, `plan`, `run`, `label`, `grade`, `rekey`).

No eval dependency/framework was added: this is a small standard-library adapter over Engine, not an implementation of Inspect AI/promptfoo/DeepEval. The JSON format is project-specific. `evals/public.example.toml` is an explicit 2000-line opt-in for all public suites in a combined experiment; normal 1500-line defaults are unchanged.

Dedicated tests use synthetic throwaway git repositories and FakeRunner. They cover pinning, no-call plans, fixed passes/repetitions, answer isolation, fresh checkouts/cleanup, working-tree preservation, Comparator accounting, failures/cancellation, known/unknown budgets, CLI labels/grade/rekey, expectation eligibility, duplicates/additional findings, undefined metrics, stale artifacts, path escapes, combined settings/coverage and paired scenario counting. No test calls a real model.

Verification completed for this implementation:
- `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -t .`: **224 passed** (Python 3.9.6).
- `PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -t .`: **224 passed**.
- **34 dedicated eval tests** are included in that total; the prior suite had 190.
- A model-free preflight of all eight active cases created **32 planned trials** (Haiku/Sonnet x 1/3 passes, one repetition), using the explicit public configuration and identical environment hashes across all three suites. A runner that raises on model/version calls confirmed neither was invoked. These are plans, not paid reviews or quality results.
- `git diff --check` passed. No production code, production findings or private run artifacts were read/copied.

Next actions:
1. User runs one private pilot locally following `evals/README.md`; no production content should be supplied here.
2. User labels actual findings and independently establishes any recall key, revising the provisional rubric only when concrete examples justify it. Any generic changes discussed here must omit production details.
3. Run a paid comparison only after the user chooses/authorizes its concrete matrix and budget. Haiku/Sonnet with 1/3 passes and repetitions is an example, not an approved experiment.
4. Report honest quality/cost/variance and incomplete trials. Do not infer quality from offline harness tests or agent-curated keys.

## 5. Practical notes for the next agent

- **Do not run real reviews or `doctor --live` unprompted.** They use the user's Claude Code login and cost money. The user runs production reviews and judges them locally; do not request or inspect their production reports in this Codex chat. For public/synthetic runs, saved artifacts are read-only evidence: `~/.code-reviewer/runs/<run>/` holds `input/` (what the reviewer was given: prompts, criteria block and index, diff, PR text), `pass-NN/` (`reviewer.raw.json`, `findings.json`, `comparator.raw.json`, `mapping.json`), `master.json`, `stats.json`, `labels.json`, `report.md` and `summary.md`.
- **Replay a saved run's Comparator** (cheap, a few cents with Haiku): `python -m code_reviewer.replay <run dir> --model haiku --repeat 2`. The tool is for comparator experiments only; it cannot test Reviewer prompt changes, which need a real run.
- **Claude CLI facts** (Claude Code 2.1.x): the reviewer flags are `--safe-mode --restricted --tools "Read,Grep,Glob" --strict-mcp-config --disable-slash-commands --permission-prompts none --no-session-persistence --output-format json --json-schema <schema> --max-budget-usd <n> --append-system-prompt <text>`, with the prompt on stdin. There is no `--max-turns`. `--json-schema` adds an internal `StructuredOutput` tool and the answer is in `structured_output` (the doctor allows that tool name).
- **Prompt caching:** identical prompts across passes mean cache hits. Do not add anything pass-specific to the prompt.
- **Test helpers:** `tests/helpers.py` has `FakeRunner`, throwaway git repos (with `gc.auto=0` to avoid background cleanup races) and `finding(...)`. A real reply is kept as `tests/fixtures/live_envelope_haiku.json`.
- **Docs that go stale most easily:** README (options, code map, limits), `docs/proposal.md` (sections 4, 5, 9 and 10), `prompts/README.md` (what the harness enforces) and the explainer (call-stack tree, module list).
- **User preferences:** short, direct answers with scannable structure; explain eval design and agree on it before implementing further; no commits without being asked; the user likes the `reviewer review <pr>` one-shot command and the welcome banner; keep documentation honest about what did not work.

## 6. Deferred backlog (not the current priority)

These ideas predate the current eval discussion. They are not the execution order. The local eval harness is implemented; representative private judgments and model comparisons come first as described in section 4. Preserve the ideas for later, with tests, synchronized docs and honest evidence in proposal section 9.

### A. Project context pack

Problem: the reviewer lacks project knowledge that exists in the repo but is not in the criteria (see 3.3).

Idea: after the diff is parsed, the harness extracts identifiers the diff touches (called or changed method and class names, table and column names), greps the base-commit tree for them in documentation and agent-instruction files (`*.md`, `docs/`, `.claude/`, `.ai/`, ADRs and similar), and adds the matching passages (capped, with file path and heading) to the prompt as a separate `<project_context>` block, clearly marked as reference material and not as rules. Deterministic, no model call, byte-identical across passes. Open design points: how to rank hits, the size cap, which directories to search (including agent-memory directories is what would have fixed the case above), and how to cite (a new ID prefix such as `C:` for context, or keep `none`).

Also record the context-pack contents in `input/` like the criteria, and add it to the report.

### B. Reviewer prompt: check assumptions against the repo

Add to `prompts/reviewer.system.md`: before reporting a finding that rests on an assumption ("if X is out of sync", "might behave differently"), read the comment above and the tests of any function the change starts or stops calling, and search the repository's documentation for the term. A finding that is conditional on something the reviewer did not verify goes in `notes`, not `findings` (the prompt already says unverified items go in `notes`; make the conditional case explicit). Check the effect with a replay-style experiment before keeping it, and keep the prompt short.

### C. Label command

The REPL has `mark` and `missed`, which write `labels.json` in the run directory, but there is no way to label a run from the shell and no aggregate view. Add `reviewer label <run dir> <finding id> <right|wrong|duplicate> [--why "…"]` (and `reviewer labels` to list/aggregate) so past runs can be labeled quickly. Labeled runs are the seed data for evals (E) and the check for the Verifier (D).

### D. Verifier pass prototype (roadmap P2)

A new role: for each Low or Medium finding (not only the unrated ones), a fresh session receives only that one finding, the relevant code reference and the criteria, and is asked to refute it with evidence from the repository. The harness validates the verdict like any other model output and uses it in confidence instead of, or alongside, pass frequency. Prototype it as a replay-like tool on a saved run first (as `replay.py` does for the Comparator) and compare against labels from C.

### E. Broader eval coverage (roadmap P1)

Expand the pinned external-project cases from section 4 with adjudicated saved runs and, if useful, planted defects. The original suggestion to write a sample repository from scratch is not the current plan.

### F. Smaller items

- Show the reviewer an index of the repository's documentation (paths only) so it knows what to search for, once A exists.
- Unscoped `.claude/rules` files apply to every PR and can be noisy; consider an option to limit them.
- Run a Sonnet replay on a saved run to compare with Haiku (about $0.06) and record it in `proposal.md`.
- Record tool calls per pass (`stream-json` output) to close the observability gap (3.4). `--verbose` live tool activity is not implemented either.
- Open verification items from `proposal.md` section 9: `--max-budget-usd` behavior when exceeded, clean timeout kill against the real CLI, and running one tiny prompt 3 times to eyeball pass-to-pass variation.
- Later roadmap: P3 multi-perspective passes, P4 inline PR comments and an MCP server, P5 provider-agnostic backend. Security hardening before any use beyond a controlled environment.
