# Prompts and schemas

Templates the harness renders for each LLM call. Each role has a **system** prompt (static instructions, passed with `--append-system-prompt`) and a **user** prompt (the request with data, passed as the `-p` argument). Each role also has a JSON schema (passed with `--json-schema`).

| Role | System | User | Schema | Tools |
|---|---|---|---|---|
| Criteria Resolver (once per review) | `resolver.system.md` | `resolver.user.md` | `schemas/resolver.schema.json` | none (`--tools ""`) |
| Reviewer (every pass) | `reviewer.system.md` | `reviewer.user.md` | `schemas/reviewer.schema.json` | `Read,Grep,Glob` |
| Comparator (every pass) | `comparator.system.md` | `comparator.user.md` | `schemas/comparator.schema.json` | none (`--tools ""`) |

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

The ten rules (`GEN-001` through `GEN-010`, in checklist order) in `criteria/defaults/general.md` are loaded and rendered inside `<review_criteria>` in the Reviewer **user** prompt. Each rule body uses **Inspect**, **Report** and **Do not report** paragraphs. They specify repository-visible checks, including finite input bounds, atomic database updates, appropriate test levels/mocking, and updates to API/event/other contract documentation.

The system prompt continues to define change scope, concrete consequences, evidence (`quote` or `anchor`/`expected`), rule citations, self-checks and JSON output. Criteria do not introduce a second output format or override those requirements. For test or maintainability gaps, describe the specific regression or maintenance consequence in the existing `description` field; unresolved assumptions belong in `notes`.

`load_default_rules` loads only non-empty sections with ID-bearing headings. Introductory text is not sent as a rule, and nested Markdown headings split sections, so model-facing instructions belong in the rule bodies with bold labels rather than subheadings. The exact criteria block and index are saved per run; template hashes alone do not identify a criteria revision. No live quality improvement is established for the revised defaults yet; comparator replay cannot evaluate Reviewer criteria changes.

## Evaluator-only rubric

`evals/RUBRIC.md` is a grading-policy draft for human evaluators and any future calibrated judge. It is not another Reviewer prompt. Its worked examples, expected issue IDs, later repair references and per-case answer keys must not be added to `<review_criteria>`, the Reviewer checkout, or Reviewer metadata. The production templates and JSON schema are unchanged by this draft. The local eval harness implements the three-check human judgment format and deterministic metrics. It snapshots the rubric and hashes the code/prompts/default criteria for reproducibility. No LLM judge is implemented; the rubric remains provisional until calibrated on actual findings. Evals explicitly use defaults-only criteria, leaving Reviewer templates, schemas and tool permissions unchanged. See `evals/README.md`.

## Harness duties around the prompts

- **Escape untrusted text.** Before rendering, replace any literal closing tag (`</untrusted_pr_metadata>`, `</diff>`, `</review_criteria>`, `</agents_md>`, and similar) inside untrusted values with `[removed]`, so PR text cannot close a delimited block early.
- **Assign finding IDs in the harness.** The Reviewer does not number its findings; the harness gives each one `p<pass>-f<n>`, so IDs are unique across passes and never come from the model.
- **Validate beyond the schema.** The schemas only check shape. The harness enforces `quote` for `present` findings, `anchor` and `expected` for `absent` findings, `self_check` when the flag is on, and the `rule_id`: a citation that only needs reformatting (brackets, case, a missing `P:`) is repaired, and one that names no listed rule is set to `none` with the original text kept in `rule_citation`. A bad citation never drops a finding (§4 of the proposal).
- **Record prompt hashes** (SHA-256 of each template file) in the run's `config.json`, so runs stay comparable when prompts change.
- **Redact personal data in the PR text.** Email addresses in the PR title, description and commit messages are replaced with `[email]` (`redact_emails`) before the Reviewer prompt is rendered. The diff, criteria and repository files are not redacted.
- **Comparator validation:** one decision per new finding in the same order, `match` is a candidate `id` or `new`, `same_as` refers to an earlier new finding.
- Keep all templates language-agnostic. Language- or project-specific guidance belongs in the criteria, not in these prompts.
