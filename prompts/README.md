# Prompts and schemas

The active single-agent templates and schemas live in [`src/code_reviewer/flows/single_agent/prompts/`](../src/code_reviewer/flows/single_agent/prompts/). Filenames in the table below are relative to that folder. The prompt loader is `flows/single_agent/prompt_contract.py`; generic rendering lives in `shared/templates.py`. Legacy package-root modules only re-export these implementations.

Templates the harness renders for each LLM call. Each role has a **system** prompt (static instructions, passed with `--append-system-prompt`) and a **user** prompt (the request with data, sent on stdin in `-p` mode). Each role also has a JSON schema (passed with `--json-schema`).

| Role | System | User | Schema | Tools |
|---|---|---|---|---|
| Criteria Resolver (when project discovery needs it) | `resolver.system.md` | `resolver.user.md` | `schemas/resolver.schema.json` | none (`--tools ""`) |
| Reviewer (every pass) | `reviewer.system.md` | `reviewer.user.md` | `schemas/reviewer.schema.json` | `Read,Grep,Glob` |
| Comparator (nontrivial batches) | `comparator.system.md` | `comparator.user.md` | `schemas/comparator.schema.json` | none (`--tools ""`) |

## Planned persona prompts (not implemented)

The agreed optional mode is documented in [proposal section 12](../docs/proposal.md#12-agreed-persona-design-and-next-steps) and the [proposed README flow](../README.md#proposed-persona-flow-not-implemented). The structural refactor moved active assets into the single-agent flow without changing their bytes, schemas, tool settings or rule IDs. Future persona assets will live under `flows/agent_personas/`. The CLI now accepts `--mode`, but selecting `agent-personas` raises a not-implemented error before review preparation or model calls.

Four persona definitions will select statically assigned default rules: correctness, code quality, test quality and production safety. An additional classification phase after existing project-rule discovery will distribute every accepted project rule to one or more personas; code validates complete coverage and falls back to all personas for unresolved assignments. Original rule text/source and project precedence are preserved. Defaults need no model classification. The current single-agent mode remains available as the default.

Each persona will receive three fixed fresh-context passes, with the exact same system/user text within that persona. Inputs are frozen before any review starts and never include earlier findings or conflict annotations. A persona may read all relevant code/tests and report a supported correctness/safety issue it notices outside its focus. The Comparator will propose categories separately from the recorded source persona; categorization cannot change sighting counts.

The persona Comparator will receive the incoming batch and the full master findings, proposing duplicate mappings, categories and possible conflict groups. The harness validates all references and records the flags. There is no separate Conflict Detector call. After the review phase, while budget/run status allow more calls, a fresh Verifier with Read/Grep/Glob in the same head checkout will investigate only distinct flagged groups within budget. Its proposed evidence/context/tradeoff annotations do not hide or rewrite findings or change confidence; failures leave groups pending/unresolved. Cancellation or terminal failure stops further model calls, including verification. Existing restrictions, self-checks and grounding continue to apply.

Save fully rendered, filtered/redacted prompts once per persona in `input/personas/<persona>/reviewer.system.md` and `reviewer.user.md`, plus model/schema/invocation settings and per-pass references to the prompt hashes. List every assigned criterion name/source per persona in the report. These exact inputs replace the need for a separate verbose routing report. Preserve classification, Comparator and conditional Verifier inputs/outputs for inspection. Current implementations still save the general prompts under `input/reviewer.*.md` and implement none of these extensions yet.

## Template syntax

- `{{name}}`: replaced with the variable's value.
- `{{#if flag}} … {{/if}}`: the block is kept when the flag is true and removed otherwise. Only `self_check` is used (Reviewer system prompt).
- Anything else in the files is literal text.

## Variables

**Resolver:** `repo`, `base_ref`, `base_sha`, `agents_md_content`, `candidate_paths_list` (one path per line).

**Reviewer (user):**

| Variable | Content |
|---|---|
| `pr_number`, `repo`, `base_ref`, `base_sha`, `head_sha` | Identifiers. There is deliberately **no pass number**: the Reviewer's system and user prompts are byte-for-byte identical in every pass, so passes are independent runs of the same task and share the prompt cache. |
| `pr_title`, `pr_body`, `commit_messages` | Untrusted PR metadata. |
| `criteria_block` | Merged criteria, one rule per block: `### [<rule_id>] (default\|project) <heading>` followed by the rule text. |
| `changed_files_list` | One per line: `<path> (<status>, +<added>/-<removed>)`. |
| `excluded_files_list` | Comma-separated paths, or `none`. |
| `diff_block` | Per file: `--- <path>` then one row per diff line: `L<new_line> <+ or space> <text>` for context and added lines, `R<old_line> - <text>` for removed lines. Hunks separated by `...`. |

**Reviewer (system):** flag `self_check` (from config).

**Comparator:** `pass_number`, `new_findings_json`, `candidate_findings_json`. Findings are passed as JSON arrays with the fields `local_id` (candidates: `id`), `title`, `description`, `file`, `line`, `kind`, `rule_id` and `severity`. Include `quote` for `present` findings and `anchor` for `absent` ones.

## Default criteria and the Reviewer contract

The ten rules (`GEN-001` through `GEN-010`, in checklist order) in `src/code_reviewer/flows/single_agent/criteria/general.md` are loaded and rendered inside `<review_criteria>` in the Reviewer **user** prompt. Each rule body uses **Inspect**, **Report** and **Do not report** paragraphs. They specify repository-visible checks, including finite input bounds, atomic database updates, appropriate test levels/mocking, and updates to API/event/other contract documentation.

The system prompt continues to define change scope, concrete consequences, evidence (`quote` or `anchor`/`expected`), rule citations, self-checks and JSON output. Criteria do not introduce a second output format or override those requirements. For test or maintainability gaps, describe the specific regression or maintenance consequence in the existing `description` field; unresolved assumptions belong in `notes`.

`load_default_rules` loads only non-empty sections with ID-bearing headings. Introductory text is not sent as a rule, and nested Markdown headings split sections, so model-facing instructions belong in the rule bodies with bold labels rather than subheadings. The exact criteria block and index are saved per run; template hashes alone do not identify a criteria revision. No live quality improvement is established for the revised defaults yet; comparator replay cannot evaluate Reviewer criteria changes.

## Evaluator-only rubric

`evals/RUBRIC.md` is a grading-policy draft for human evaluators and any future calibrated judge. It is not another Reviewer prompt. Its worked examples, expected issue IDs, later repair references and per-case answer keys must not be added to `<review_criteria>`, the Reviewer checkout, or Reviewer metadata. The production templates and JSON schema are unchanged by this draft. The local eval harness implements the three-check human judgment format and deterministic metrics. It snapshots the rubric and hashes the code/prompts/default criteria for reproducibility. No LLM judge is implemented; the rubric remains provisional until calibrated on actual findings. Evals explicitly use defaults-only criteria, leaving Reviewer templates, schemas and tool permissions unchanged. See `evals/README.md`.

## Harness duties around the prompts

- **Escape untrusted text.** Before rendering, replace any literal closing tag (`</untrusted_pr_metadata>`, `</diff>`, `</review_criteria>`, `</agents_md>`, and similar) inside untrusted values with `[removed]`, so PR text cannot close a delimited block early.
- **Assign finding IDs in the harness.** The Reviewer does not number its findings; the harness gives each one `p<pass>-f<n>`, so IDs are unique across passes and never come from the model.
- **Excluded files.** Files matching `exclude_patterns` or `extra_exclude_patterns` (lockfiles, generated files, and any planning documents the project listed) are left out of the diff but named in `<excluded_files>`. The system prompt tells the Reviewer they may be read to understand why something was done, are the author's claims, and are never used to excuse a problem or report drift. The harness drops any finding whose file or anchor is in an excluded file and counts it as out of scope.
- **Validate beyond the schema.** The schemas only check shape. The harness enforces `quote` for `present` findings, `anchor` and `expected` for `absent` findings, `rule_id`, and self-check handling: when enabled, missing or invalid `self_check` is counted and treated as confirmed; valid rejected sightings are recorded without scoring. For rule citations, a citation that only needs reformatting (brackets, case, a missing `P:`) is repaired, and one that names no listed rule is set to `none` with the original text kept in `rule_citation`. A bad citation never drops a finding (§4 of the proposal).
- **Record prompt hashes** (SHA-256 of each template file) in the run's `config.json`, so runs stay comparable when prompts change.
- **Redact personal data in the PR text.** Email addresses in the PR title, description and commit messages are replaced with `[email]` (`redact_emails`) before the Reviewer prompt is rendered. The diff, criteria and repository files are not redacted.
- **Comparator validation:** one decision per new finding in the same order, `match` is a candidate `id` or `new`, `same_as` refers to an earlier new finding.
- Keep all templates language-agnostic. Language- or project-specific guidance belongs in the criteria, not in these prompts.
