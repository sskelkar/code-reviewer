# Prompts and schemas

The active single-agent templates and schemas live in [`src/code_reviewer/flows/single_agent/prompts/`](../src/code_reviewer/flows/single_agent/prompts/). Filenames in the table below are relative to that folder. The prompt loader is `flows/single_agent/prompt_contract.py`; generic rendering lives in `shared/templates.py`. Legacy package-root modules only re-export these implementations.

Templates the harness renders for each LLM call. Each role has a **system** prompt (static instructions, passed with `--append-system-prompt`) and a **user** prompt (the request with data, sent on stdin in `-p` mode). Each role also has a JSON schema (passed with `--json-schema`).

| Role | System | User | Schema | Tools |
|---|---|---|---|---|
| Criteria Resolver (when project discovery needs it) | `resolver.system.md` | `resolver.user.md` | `schemas/resolver.schema.json` | none (`--tools ""`) |
| Reviewer (every pass) | `reviewer.system.md` | `reviewer.user.md` | `schemas/reviewer.schema.json` | `Read,Grep,Glob` |
| Comparator (nontrivial batches) | `comparator.system.md` | `comparator.user.md` | `schemas/comparator.schema.json` | none (`--tools ""`) |

## Persona prompts (implemented; offline-tested)

The optional mode is documented in [proposal section 12](../docs/proposal.md#12-agreed-persona-design-and-next-steps) and the [README flow](../README.md#agent-personas). Its own templates and schemas live in `flows/agent_personas/prompts/`, loaded by its `prompt_contract.py`. Selecting `--mode agent-personas` uses these contracts; single-agent templates/defaults remain unchanged.

| Role | Input and output | Model / tools |
|---|---|---|
| Resolver | Existing base-commit project-file discovery contract, copied into this flow | `model_resolver`; none |
| Classifier | Accepted project rule IDs/text + focus definitions → assignments/reasons; retry then visible all-persona fallback for unresolved rules | `model_resolver`; none |
| Reviewer | Common review contract + one persona focus + its criteria + pinned PR/diff → existing finding schema | `model_reviewer`; Read/Grep/Glob in checkout |
| Comparator | Complete master + incoming batch → duplicate mappings, categories, conflict references; no finding text is truncated to fit | `model_comparator`; none |
| Verifier | One distinct flagged group + complete accepted base criteria + diff + head access → per-claim grounded evidence/status and group explanation | `model_verifier`; Read/Grep/Glob in checkout |

The caller saves request attempts in `*.raw.requests.json`, including validation-retry feedback, alongside replies; `invoked` distinguishes a blocked oversized retry from an invocation attempt. It atomically reserves budget from the role/persona pool before every attempt, limits per-call dollars to remaining pool cents and rejects prompts exceeding `persona_prompt_max_chars` rather than silently truncating them. Non-unresolved Verifier claims need head-file quotes at the stated lines; removed-file evidence alone may remain unresolved. Categories can change through validated annotations, but source sightings, severity and original findings never do.

The [persona definitions](../src/code_reviewer/flows/agent_personas/personas.json) now record static default assignments and focus instructions for correctness, code quality, test quality and production safety. Thirteen canonical rules are defined in [the criteria assets](../src/code_reviewer/flows/agent_personas/criteria/), with [common review instructions](../src/code_reviewer/flows/agent_personas/review-standard.md) for prompt assembly. `COR-002` has one definition shared by correctness and safety. Safety also receives `SAFE-005` for repository-defined query/index compatibility; schema inspection must not be represented as runtime planner or performance verification. Persona prompts load these assets; the existing single-agent templates and `GEN-*` rules remain unchanged. An additional classification phase after existing project-rule discovery distributes every accepted project rule to one or more personas; code validates complete coverage and falls back to all personas for unresolved assignments. Original rule text/source and project precedence are preserved. Defaults need no model classification. The current single-agent mode remains available as the default.

Each persona receives `persona_passes` fresh-context passes (default 3; `--passes N` overrides), with the exact same system/user text within that persona. Reviewers run concurrently within each round with separate Caller/request-log state; their results are merged serially in fixed persona order before the next round. Inputs are frozen before any review starts and never include earlier findings or conflict annotations. A persona may read all relevant code/tests and report a supported correctness/safety issue it notices outside its focus. The Comparator proposes categories separately from the recorded source persona; categorization cannot change sighting counts. The harness stages consolidation and discards its changes if that slot is interrupted, so incomplete slots supply no votes or conflict flags.

The persona Comparator receives the incoming batch and the full master findings, proposing duplicate mappings, categories and possible conflict groups. The harness validates all references and records the flags. There is no separate Conflict Detector call. After the review phase, while budget/run status allow more calls, a fresh Verifier with Read/Grep/Glob in the same head checkout investigates only distinct flagged groups within budget. Its proposed evidence/context/tradeoff annotations do not hide or rewrite findings or change confidence; failures leave groups pending/unresolved. Cancellation or terminal failure stops further model calls, including verification. Existing restrictions, self-checks and grounding continue to apply.

Save fully rendered, filtered/redacted prompts once per persona in `input/personas/<persona>/reviewer.system.md` and `reviewer.user.md`, plus model/schema/invocation settings and per-pass references to the prompt hashes. List every assigned criterion name/source per persona in the report. These exact inputs replace the need for a separate verbose routing report. Preserve classification, Comparator and conditional Verifier inputs/outputs for inspection. Single-agent still saves its general prompts under `input/reviewer.*.md`; persona mode saves its four frozen inputs under `input/personas/`. The run report lists every assigned rule name/source and incomplete scheduling/verification state.

## Template syntax

- `{{name}}`: replaced with the variable's value.
- `{{#if flag}} … {{/if}}`: the block is kept when the flag is true and removed otherwise. Only `self_check` is used (Reviewer system prompt).
- Anything else in the files is literal text.

## Variables

**Resolver:** `repo`, `base_ref`, `base_sha`, `agents_md_content`, `candidate_paths_list` (one path per line).

**Reviewer (user):**

| Variable | Content |
|---|---|
| `pr_number`, `repo`, `base_ref`, `base_sha`, `head_sha` | Identifiers. There is deliberately **no pass number**: the Reviewer's system and user prompts are byte-for-byte identical in every single-agent pass, or within each persona's configured passes, so repeated passes are independent runs of the same task and share the prompt cache. |
| `pr_title`, `pr_body`, `commit_messages` | Untrusted PR metadata. |
| `criteria_block` | Merged criteria, one rule per block: `### [<rule_id>] (default\|project) <heading>` followed by the rule text. |
| `changed_files_list` | One per line: `<path> (<status>, +<added>/-<removed>)`. |
| `excluded_files_list` | Comma-separated paths, or `none`. |
| `diff_block` | Per file: `--- <path>` then one row per diff line: `L<new_line> <+ or space> <text>` for context and added lines, `R<old_line> - <text>` for removed lines. Hunks separated by `...`. |

**Reviewer (system):** flag `self_check` (from config). Persona mode additionally renders `persona_name`, `persona_focus` and `common_standard` from its static assets.

**Single-agent Comparator:** `pass_number`, `new_findings_json`, `candidate_findings_json`. Findings are passed as JSON arrays with the fields `local_id` (candidates: `id`), `title`, `description`, `file`, `line`, `kind`, `rule_id` and `severity`. Include `quote` for `present` findings and `anchor` for `absent` ones.

**Persona-only roles:** the Classifier receives `personas_json` and `rules_json`; the Comparator receives `categories_json`, `master_json` and `findings_json`; the Verifier receives `categories_json`, `claims_json`, `reason`, `criteria_block` and `diff_block`. These contracts and their complete payloads are separate from the single-agent Comparator's compact candidate format.

## Default criteria and the Reviewer contract

The ten rules (`GEN-001` through `GEN-010`, in checklist order) in `src/code_reviewer/flows/single_agent/criteria/general.md` are loaded and rendered inside `<review_criteria>` in the Reviewer **user** prompt. Each rule body uses **Inspect**, **Report** and **Do not report** paragraphs. They specify repository-visible checks, including finite input bounds, atomic database updates, appropriate test levels/mocking, and updates to API/event/other contract documentation.

The system prompt continues to define change scope, concrete consequences, evidence (`quote` or `anchor`/`expected`), rule citations, self-checks and JSON output. Criteria do not introduce a second output format or override those requirements. For test or maintainability gaps, describe the specific regression or maintenance consequence in the existing `description` field; unresolved assumptions belong in `notes`.

`load_default_rules` loads only non-empty sections with ID-bearing headings. Introductory text is not sent as a rule, and nested Markdown headings split sections, so model-facing instructions belong in the rule bodies with bold labels rather than subheadings. The exact criteria block and index are saved per run; template hashes alone do not identify a criteria revision. No live quality improvement is established for the revised defaults yet; comparator replay cannot evaluate Reviewer criteria changes.

## Evaluator-only rubric

`evals/RUBRIC.md` is a grading-policy draft for human evaluators and any future calibrated judge. It is not another Reviewer prompt. Its worked examples, expected issue IDs, later repair references and per-case answer keys must not be added to `<review_criteria>`, the Reviewer checkout, or Reviewer metadata. The production templates and JSON schema are unchanged by this draft. The local eval harness implements the three-check human judgment format and deterministic metrics. It snapshots the rubric and hashes the code/prompts/default criteria for reproducibility. No LLM judge is implemented; the rubric remains provisional until calibrated on actual findings. Evals explicitly use defaults-only criteria, leaving Reviewer templates, schemas and tool permissions unchanged. See `evals/README.md`.

## Harness duties around the prompts

- **Freeze fresh commits per run.** Fetch the current PR head and target tip into private review refs, ignoring configured remote-tracking mappings and automatic tag following. All passes use that run's pinned commits. Subsequent reviews fetch again. The diff is merge-base to head, and code tools see the head checkout rather than a simulated merge.

- **Escape untrusted text.** Before rendering, replace any literal closing tag (`</untrusted_pr_metadata>`, `</diff>`, `</review_criteria>`, `</agents_md>`, and similar) inside untrusted values with `[removed]`, so PR text cannot close a delimited block early.
- **Assign finding IDs in the harness.** The Reviewer does not number its findings; the harness gives each one `p<pass>-f<n>`, so IDs are unique across passes and never come from the model.
- **Excluded files.** Files matching `exclude_patterns` or `extra_exclude_patterns` (lockfiles, generated files, and any planning documents the project listed) are left out of the diff but named in `<excluded_files>`. The system prompt tells the Reviewer they may be read to understand why something was done, are the author's claims, and are never used to excuse a problem or report drift. The harness drops any finding whose file or anchor is in an excluded file and counts it as out of scope.
- **Validate beyond the schema.** The schemas only check shape. The harness enforces `quote` for `present` findings, `anchor` and `expected` for `absent` findings, `rule_id`, and self-check handling: when enabled, missing or invalid `self_check` is counted and treated as confirmed; valid rejected sightings are recorded without scoring. For rule citations, a citation that only needs reformatting (brackets, case, a missing `P:`) is repaired, and one that names no listed rule is set to `none` with the original text kept in `rule_citation`. A bad citation never drops a finding (§4 of the proposal).
- **Record prompt hashes** (SHA-256 of each template file) in the run's `config.json`, so runs stay comparable when prompts change.
- **Redact personal data in the PR text.** Email addresses in the PR title, description and commit messages are replaced with `[email]` (`redact_emails`) before the Reviewer prompt is rendered. The diff, criteria and repository files are not redacted.
- **Comparator validation:** one decision per new finding in the same order, `match` is a candidate `id` or `new`, `same_as` refers to an earlier new finding.
- Keep all templates language-agnostic. Language- or project-specific guidance belongs in the criteria, not in these prompts.

Persona pool ratios and exhaustion/accounting behavior are specified in the [usage reference](../docs/usage.md#persona-budget-pools). A persona can exhaust its pool without stopping the others. Criteria fallback, comparator exhaustion and verifier exhaustion have distinct handling; none creates votes from unconsolidated output. Cancellation waits for active subprocess groups before checkout cleanup. Model-facing templates are unchanged by parallel scheduling.
