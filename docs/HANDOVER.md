# Handover: state of the project and what comes next

For an AI agent (or a person) taking over. Read [`AGENTS.md`](../AGENTS.md) first for the working rules. This file says where things stand and what to do next. The design reasoning lives in [`proposal.md`](proposal.md); usage and the code map are in the [README](../README.md).

**Current state: 2026-10-20.** Everything described here is committed except the changes listed at the end of section 7. 231 tests pass on Python 3.13 and 3.9; they validate the harness with a fake Claude, not review quality. The agent has been run on real pull requests of the user's own project (Haiku, 3 to 6 passes) and the user judges the results by hand.

**Direction (supersedes the eval-first plan of 2026-10-19):** the user decided that building an eval harness now is a distraction. The eval code stays in the repository but is **parked**: do not extend it (section 4). The goal is to make the agent useful on real pull requests and to try strategies side by side on the same PRs: the current loop with Haiku, the same loop with Sonnet, then **multi-perspective review** (for example code quality, test quality, failure modes, each with its own passes). The task checklist is in [`proposal.md`](proposal.md) (section "Checklist"); the design notes for the next steps are in proposal section 12.

**Constraints from the user:** commit or push only when asked. Only Claude may review production code: another agent (for example Codex) must not read the user's production clone, diffs, saved findings or private cases, and must not ask the user to paste them. Generic framework work and public or synthetic verification are fine. Never put company or project identifiers (repository names, PR numbers, ticket keys, run directory names, code from saved runs) into this repository.

**Immediate next step:** the user is running reviews on their own PRs and will report what they see. Then, in order: multi-perspective review, project context pack, Verifier pass (proposal section 12). Parallel passes (harness threads, not Claude sub-agents) and an effort/thinking setting to cut cost are also on the list (section 6).

## 1. What exists

A Python package (`code_reviewer`, command `reviewer`) that reviews a GitHub PR with several independent Claude Code headless passes:

1. Check out the PR head in a temporary directory (outside the run directory, always deleted afterward) and fetch only an allow-list of PR fields.
2. Build the inputs once: redacted PR text, the filtered diff with line markers (files matching `exclude_patterns` or `extra_exclude_patterns`, such as lockfiles and planning documents, are left out of the diff but named in `<excluded_files>`), and the review criteria.
3. Run passes. Each pass is a fresh `claude -p` session with only Read, Grep and Glob, blind to other passes. The prompt is byte-identical every pass.
4. After each pass, the harness drops findings located in excluded files (counted as out of scope), grounds every finding against the code (quote must match, or an anchor over a changed hunk for "absent" findings), repairs or neutralizes the rule ID, and applies the model's own self-check verdict.
5. The Comparator maps each pass's findings onto a master list (same finding, or new). The harness validates its answer.
6. A stop policy ends the run (min 3 passes, max 6, stop after 2 consecutive quiet passes, stop after 2 failures, token budget).
7. Output: a shareable summary (printed, and the same text posted as a PR comment after confirmation) and a detailed `report.md`, both naming each finding's criterion by name and source, never by its internal ID, plus token and cost accounting, and every input and raw model reply saved under `~/.code-reviewer/runs/<run>/`.

Confidence is the share of completed passes that found a finding: High at 75% or more, Medium at 40% or more, Low below that, Unrated if fewer than 2 passes completed.

**Criteria** come from three places, merged with project rules taking precedence:
- Defaults: `criteria/defaults/general.md` (ten criteria numbered `GEN-001` through `GEN-010` in checklist order).
- Files named by the Resolver model from links in the project's root `AGENTS.md` (read from the base branch, never the PR head).
- Files added by location without a model call: an `AGENTS.md` in or above any directory containing a file under review (excluded files do not count), and every `.claude/rules/*.md` file that has no `paths:` front matter or whose `paths:` globs match a changed file.

**Interfaces:** `reviewer` (interactive REPL with `review`, `show`, `drop`, `undrop`, `mark`, `missed`, `summary`, `post`, `open`, `help`), `reviewer review <pr>` (one-shot), `reviewer doctor [--live]`, `python -m code_reviewer.replay <run dir>` (re-runs only the Comparator on saved findings), and the parked `python -m code_reviewer.evals` (section 4.2). The README has flow diagrams of a review (a high-level one and a detailed one).

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

## 4. Default criteria, and the parked eval harness

### 4.1 Default criteria

`criteria/defaults/general.md` has ten criteria numbered in checklist order. Each has **Inspect / Report / Do not report** paragraphs and is assessable from the repository. Architecture and performance are deliberately not generic defaults (projects add them). The IDs changed meaning in the 2026-10-18 revision (`GEN-004` used to be injection), so older saved runs cannot be compared by ID: compare their saved criteria blocks.

| ID | Topic |
|---|---|
| GEN-001 | Correctness |
| GEN-002 | Simplicity and maintainability |
| GEN-003 | Clarity and documentation, including API/event/other contract documents (not planning documents) |
| GEN-004 | Security and privacy |
| GEN-005 | Input validation and finite bounds |
| GEN-006 | Failure handling and resource management |
| GEN-007 | Data integrity and concurrency, including missing atomicity for related database writes |
| GEN-008 | Internal contract consistency |
| GEN-009 | Test coverage and appropriate test levels |
| GEN-010 | Test independence and responsible mocking |

The rules are loaded into `<review_criteria>` in the Reviewer user prompt; the system prompt (`prompts/reviewer.system.md`) owns scope, evidence, self-check and JSON. The criteria have been used on real PRs but never compared before/after, so no improvement is claimed.

### 4.2 Parked: local eval harness

Built 2026-10-19 and then set aside (details and reasons: proposal section 9, "Parked"). It is `python -m code_reviewer.evals` (`init`, `plan`, `run`, `label`, `grade`, `rekey`) with modules `evals.py`, `eval_cases.py`, `eval_runner.py`, `eval_judgments.py`, `eval_grading.py`, the human-label rubric `evals/RUBRIC.md`, eight pinned public cases (four regression/repair pairs from open-source projects, `evals/CASES.md`, `evals/suites/`) with agent-curated, provisional answer keys, and the guide `evals/README.md`. The user's earlier personal-project candidates are in `evals/reserve/` and cite the old rule numbering. No model was ever run through the harness and no quality result exists. It uses default criteria only, so it cannot test project-rule discovery. If revived: record a reason for every wrong finding (missing project knowledge, unverified assumption, misread code, pre-existing issue, style preference), and add a mode that freezes the project's resolved criteria.

## 5. Practical notes for the next agent

- **Do not run real reviews or `doctor --live` unprompted.** They use the user's Claude Code login and cost money. The user runs reviews on their own PRs and shares report paths. Saved runs are read-only evidence: `~/.code-reviewer/runs/<run>/` holds `input/` (what the reviewer was given: prompts, criteria block and index, diff, PR text), `pass-NN/` (`reviewer.raw.json` with each attempt's usage envelope, `findings.json`, `comparator.raw.json`, `mapping.json`), `master.json`, `stats.json`, `labels.json`, `report.md` and `summary.md`. Only Claude may read production material (see the constraints at the top).
- **Replay a saved run's Comparator** (cheap, a few cents with Haiku): `python -m code_reviewer.replay <run dir> --model haiku --repeat 2`. The tool is for comparator experiments only; it cannot test Reviewer prompt changes, which need a real run.
- **Claude CLI facts** (Claude Code 2.1.x): the reviewer flags are `--safe-mode --restricted --tools "Read,Grep,Glob" --strict-mcp-config --disable-slash-commands --permission-prompts none --no-session-persistence --output-format json --json-schema <schema> --max-budget-usd <n> --append-system-prompt <text>`, with the prompt on stdin. There is no `--max-turns`. `--json-schema` adds an internal `StructuredOutput` tool and the answer is in `structured_output` (the doctor allows that tool name).
- **Prompt caching:** identical prompts across passes mean cache hits. Do not add anything pass-specific to the prompt.
- **Test helpers:** `tests/helpers.py` has `FakeRunner`, throwaway git repos (with `gc.auto=0` to avoid background cleanup races) and `finding(...)`. A real reply is kept as `tests/fixtures/live_envelope_haiku.json`.
- **Docs that go stale most easily:** README (options, code map, limits), `docs/proposal.md` (sections 4, 5, 9 and 10), `prompts/README.md` (what the harness enforces) and the explainer (call-stack tree, module list).
- **User preferences:** short, direct answers with scannable structure; explain a design and agree on it before building it; no commits without being asked; the user likes the `reviewer review <pr>` one-shot command and the welcome banner; keep documentation honest about what did not work.

## 6. Next steps and backlog

The checklist with the agreed order is in [`proposal.md`](proposal.md). Order: multi-perspective review (proposal section 12), project context pack (A), Verifier (D), then the rest. Each item ends with tests, synchronized docs and an honest note in proposal section 9. The items below are design notes.

Additional items raised after the first handover (all unbuilt):
- **Parallel passes.** Reviewer calls are independent of each other, so they can run concurrently in harness threads (not Claude sub-agents, which would break independence, validation and accounting). Split `PassRunner.run` into a parallel review phase and a serial compare-and-merge phase processed in pass order, run pass 1 alone first so the prompt cache is warm, then batches, behind a `--parallel N` option with default 1 so comparisons stay unchanged. `Stats` and progress output need locks.
- **Cost.** One pass costs roughly $0.19 (first, cache write) then $0.05-0.07 on a 2,100-line diff, driven by the 85k-token prompt (criteria about half), cache reads on every turn, and about 12k thinking tokens of output. Try the `claude --effort` option (not used yet) and a smaller criteria cap; compare findings by hand.
- **Audit trail for governance and sandboxing of checkouts and prompt-injection safeguards** are on the proposal checklist.

### A. Project context pack

Problem: the reviewer lacks project knowledge that exists in the repo but is not in the criteria (see 3.3).

Idea: after the diff is parsed, the harness extracts identifiers the diff touches (called or changed method and class names, table and column names), greps the base-commit tree for them in documentation and agent-instruction files (`*.md`, `docs/`, `.claude/`, `.ai/`, ADRs and similar), and adds the matching passages (capped, with file path and heading) to the prompt as a separate `<project_context>` block, clearly marked as reference material and not as rules. Deterministic, no model call, byte-identical across passes. Open design points: how to rank hits, the size cap, which directories to search (including agent-memory directories is what would have fixed the case above), and how to cite (a new ID prefix such as `C:` for context, or keep `none`).

Also record the context-pack contents in `input/` like the criteria, and add it to the report.

### B. Reviewer prompt: check assumptions against the repo

Add to `prompts/reviewer.system.md`: before reporting a finding that rests on an assumption ("if X is out of sync", "might behave differently"), read the comment above and the tests of any function the change starts or stops calling, and search the repository's documentation for the term. A finding that is conditional on something the reviewer did not verify goes in `notes`, not `findings` (the prompt already says unverified items go in `notes`; make the conditional case explicit). Check the effect with a replay-style experiment before keeping it, and keep the prompt short.

### C. Label command

The REPL has `mark` and `missed`, which write `labels.json` in the run directory, but there is no way to label a run from the shell and no aggregate view. Useful once the user has judged several real runs; add a short reason for each wrong finding. Add `reviewer label <run dir> <finding id> <right|wrong|duplicate> [--why "…"]` (and `reviewer labels` to list/aggregate) so past runs can be labeled quickly. Labeled runs are the seed data for evals (E) and the check for the Verifier (D).

### D. Verifier pass prototype (roadmap P2)

Not a second Comparator: the Comparator decides whether two findings are the *same issue* (text only, removes duplicates); the Verifier decides whether one finding is *true* (reads the code, removes false positives) and runs after it, once per distinct finding.

A new role: for each Low or Medium finding (not only the unrated ones), a fresh session receives only that one finding, the relevant code reference and the criteria, and is asked to refute it with evidence from the repository. The harness validates the verdict like any other model output and uses it in confidence instead of, or alongside, pass frequency. Prototype it as a replay-like tool on a saved run first (as `replay.py` does for the Comparator) and compare against labels from C.

### E. Evals (roadmap P1, parked)

See section 4.2. Not the current plan.

### F. Smaller items

- Show the reviewer an index of the repository's documentation (paths only) so it knows what to search for, once A exists.
- Unscoped `.claude/rules` files apply to every PR and can be noisy; consider an option to limit them.
- Run a Sonnet replay on a saved run to compare with Haiku (about $0.06) and record it in `proposal.md`.
- Record tool calls per pass (`stream-json` output) to close the observability gap (3.4). `--verbose` live tool activity is not implemented either.
- Open verification items from `proposal.md` section 9: `--max-budget-usd` behavior when exceeded, clean timeout kill against the real CLI, and running one tiny prompt 3 times to eyeball pass-to-pass variation.
- Later roadmap: P3 multi-perspective passes, P4 inline PR comments and an MCP server, P5 provider-agnostic backend. Security hardening before any use beyond a controlled environment.
