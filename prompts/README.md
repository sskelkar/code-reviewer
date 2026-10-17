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
| `pr_number`, `repo`, `base_ref`, `base_sha`, `head_sha`, `pass_number` | Identifiers. |
| `pr_title`, `pr_body`, `commit_messages` | Untrusted PR metadata. |
| `criteria_block` | Merged criteria, one rule per block: `### [<rule_id>] (default\|project) <heading>` followed by the rule text. |
| `changed_files_list` | One per line: `<path> (<status>, +<added>/-<removed>)`. |
| `excluded_files_list` | Comma-separated paths, or `none`. |
| `diff_block` | Per file: `--- <path>` then one row per diff line: `L<new_line> <+ or space> <text>` for context and added lines, `R<old_line> - <text>` for removed lines. Hunks separated by `...`. |

**Reviewer (system):** flag `self_check` (from config).

**Comparator:** `pass_number`, `new_findings_json`, `candidate_findings_json`. Findings are passed as JSON arrays with the fields `local_id` (candidates: `id`), `title`, `description`, `file`, `line`, `kind`, `rule_id` and `severity`. Include `quote` for `present` findings and `anchor` for `absent` ones.

## Harness duties around the prompts

- **Escape untrusted text.** Before rendering, replace any literal closing tag (`</untrusted_pr_metadata>`, `</diff>`, `</review_criteria>`, `</agents_md>`, and similar) inside untrusted values with `[removed]`, so PR text cannot close a delimited block early.
- **Pass a different `local_id` prefix per pass** (`p<pass_number>-f<n>`); the Reviewer uses the `pass_number` it is given.
- **Validate beyond the schema.** The schemas only check shape. The harness enforces `quote` for `present` findings, `anchor` and `expected` for `absent` findings, `self_check` when the flag is on, and that `rule_id` exists in the criteria index or equals `none` (§4 of the proposal).
- **Record prompt hashes** (SHA-256 of each template file) in the run's `config.json`, so runs stay comparable when prompts change.
- **Comparator validation:** one decision per new finding in the same order, `match` is a candidate `id` or `new`, `same_as` refers to an earlier new finding.
- Keep all templates language-agnostic. Language- or project-specific guidance belongs in the criteria, not in these prompts.
