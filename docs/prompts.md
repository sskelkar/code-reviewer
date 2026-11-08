# Prompts and schemas

All model contracts live in [`src/code_reviewer/review/prompts/`](../src/code_reviewer/review/prompts/), loaded by `review/prompt_contract.py`. Generic rendering and delimiter escaping live in `shared/templates.py`. Every call has system text, user data and a JSON schema; adapters transport the same contracts.

## Persona prompts

| Role | Input and output | Model / tools |
|---|---|---|
| Resolver (legacy asset; inactive) | Project review guidance is now discovered deterministically from explicit base-commit instructions | No model call |
| Classifier | Accepted rules and persona focus → assignments and reasons | `model_resolver`; none |
| Reviewer | Common standard, one focus, assigned criteria, pinned PR and diff → findings and self-checks | `model_reviewer`; Read/Grep/Glob |
| Comparator | Complete master and incoming findings → duplicate mappings, categories and conflicts | `model_comparator`; none |
| Verifier | Each candidate or connected conflict group, complete accepted criteria, diff and head access → supported/refuted/unresolved assessments | `model_verifier`; Read/Grep/Glob |

Each role has `<role>.system.md`, `<role>.user.md` and `schemas/<role>.schema.json`. Reviewer/Verifier schema references are expanded from the single shared `schemas/evidence.schema.json` owner into standalone provider contracts. The [persona definitions](../src/code_reviewer/review/personas.json) provide four focuses and static default assignments. The [common standard](../src/code_reviewer/review/review-standard.md) and [criteria](persona-criteria.md) define inspection scope and evidence requirements.

## Criteria and frozen input

Thirteen unique defaults have fourteen assignments: `COR-002` is shared by correctness and production safety. Project rules take precedence over defaults and retain original text/source. Their Classifier assignments must cover every accepted rule; unresolved rules visibly fall back to all personas. Defaults need no model classification.

Every reviewer's complete prompt is frozen before rounds begin and reused unchanged across that persona's passes. Each model call starts a fresh session and never receives earlier findings. The harness saves frozen prompts/settings in `input/personas/<persona>/`. A persona may report an evidenced defect outside its focus; the Comparator assigns categories separately from the discovering persona.

## Validation and accounting

`PersonaCaller` checks full prompt bounds and selects the relevant pool. `BudgetRunner` reserves allowance before every invocation, including retries. `*.raw.requests.json` records the actual request, retry feedback and whether it was invoked. Oversized prompts fail rather than being silently truncated. Role output validation permits at most one shared retry; all attempts are accounted.

The harness normalizes finding shape and rule citations, enforces evidence for present/absent claims, handles self-checks, drops excluded-file findings and grounds quotes or changed-line anchors. The Reviewer contract requires a valid self-check when enabled and null when disabled; valid rejected sightings are retained without scoring. Unknown rule citations become `none`, preserving the original citation for audit. Bad citations alone do not discard grounded findings.

The Comparator must return one decision per incoming finding in input order, with valid master/earlier-local references and allowed categories. Distinct actionable canonical claims may be flagged as conflicts. Python validates references before applying a staged master copy; incomplete consolidation creates no votes or conflict flags.

A Verifier assesses every requested candidate exactly once. Shared evidence references resolve to grounded head citations, frozen accepted criteria or original primary evidence. Python rejects incomplete/inconsistent groups and invalid references. Only supported assessments can publish; unresolved, refuted and unfinished candidates remain in private artifacts and completion counts. A suggested fix needs separate remedy support. See the complete [verification contract](verification.md). Cancellation stops additional calls.

OpenAI receives a converted copy of the canonical schemas: required/closed object properties and no unsupported array `uniqueItems`. Python validates uniqueness and semantic references. Canonical schemas are not mutated by transport conversion.

## Harness boundaries

- Pin current base/head/merge-base once per review. Later reviews fetch again. Code tools see the head snapshot; criteria come from the base tip.
- Escape untrusted closing delimiter tags before rendering. Models cannot select tools, infrastructure or execution location.
- Assign internal finding IDs in Python, never from model output. Display titles and category groups are presentation, not master identity.
- Redact email addresses in PR title/body/commit messages before prompts. Code, diffs and criteria are not redacted.
- Keep model tools read-only. No target hooks, tests, dependencies or arbitrary commands execute.
- VM exports retain report, summary, stats and private candidate/assessment audit (`findings.json`); host runs also retain requests, replies and inputs. See [usage](usage.md#where-things-are-stored-and-cleaning-up).

Canonical prompt/schema/criteria changes need separately authorized before/after comparisons under [the comparison policy](proposal.md#review-quality-experiments). Unit tests check contracts and wiring, not review quality. See [LLM transport](llm.md), [execution boundaries](security-contract.md) and [pool behavior](usage.md#persona-budget-pools).
