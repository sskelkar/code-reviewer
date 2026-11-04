# Evidence and publication verification

Every eligible deduplicated candidate is investigated before it can appear in terminal summaries, reports or `--post`. Refuted, unresolved and unfinished candidates are withheld. Support is a model judgment backed by repository evidence, not a guarantee that a finding or suggested fix is correct.

## One shared JSON contract

[The shared schema](../src/code_reviewer/review/prompts/schemas/evidence.schema.json) owns Reviewer arguments, evidence objects and Verifier assessments. [prompt_contract.py](../src/code_reviewer/review/prompt_contract.py) expands the role references into standalone provider schemas. There is no second design/runtime schema copy.

Candidates retain their primary location, kind, quote or changed-hunk anchor, criterion, severity and optional suggestion. Their `description` states the precise claim; `consequence`, `conditions`, `reasoning` and `assumptions` make the argument explicit. `evidence` contains additional citations, each explaining what it establishes. The harness assigns finding IDs and preserves variants and sightings through consolidation.

Reviewer and Verifier citations have the same fields:

```json
{
  "id": "S1",
  "source": "head",
  "file": "src/handler.py",
  "line": 8,
  "rule_id": null,
  "quote": "return ratio(total, 0)",
  "establishes": "This caller passes zero as the divisor."
}
```

This is an illustrative synthetic example. `head` citations require a safe relative path, positive starting line and null `rule_id`. Quotes must match the pinned checkout with whitespace normalization. `criterion` citations require an accepted rule ID, null file/line and a quote from that frozen rule; the harness owns its source path and base-commit or packaged-default provenance. Raw head documentation cannot introduce new review rules.

Reviewer evidence IDs use `S1`, `S2`, etc.; Verifier additions use `V1`, `V2`, etc. IDs are unique within a candidate. The reserved `primary` reference identifies its existing grounded quote or anchor. A removed-diff primary quote retains that provenance and is not treated as head content. References cannot silently cross candidates.

A `schema.rb` quote establishes declarations in that snapshot. An accepted repository criterion may establish that the file represents the project's schema. Neither proves deployed state, table cardinalities, query plans or measured latency. Similarly, an absence anchor locates a claim but cannot establish missing handling across callers; supporting evidence must cover the relevant behavior or requirement.

## Independent investigations

The harness builds connected components of flagged conflicts and singleton jobs for other eligible candidates, investigating each once. A fresh Verifier gets the immutable canonical argument, complete accepted criteria, pinned diff and Read/Grep/Glob access. It does not receive discovering personas, repeated-sighting counts or self-check votes. It investigates the published canonical claim; support is not automatically transferred to broader duplicate variants.

The Verifier must trace relevant callers, guards, tests and contracts, actively seek counterevidence, and assess the original claim and consequence under their stated conditions. A specific test-quality or contract violation can be established without demonstrating a runtime failure. Personal preference or hypothetical performance risk is insufficient. It cannot silently narrow an unconditional claim to make it pass.

Each response covers every requested candidate exactly once:

| Status | Meaning | Publication |
|---|---|---|
| `supported` | Evidence supports the specific claim without material assumptions or remaining uncertainties | Eligible |
| `refuted` | Evidence contradicts a necessary part of the claim | Withheld |
| `unresolved` | Evidence, context or completed investigation is insufficient | Withheld |

Each assessment includes `candidate_id`, status, explanation, evidence references, additional citations, remaining uncertainties, categories and an independent `remedy` assessment. Supported/refuted claims require nonempty grounded references. Material assumptions or remaining uncertainties prohibit supported status. An absence claim needs evidence beyond its primary anchor.

Group relationships are `independent` for a singleton, or `contradictory`, `different_conditions`, `tradeoff`, or `unresolved`. Contradictory claims under the same conditions cannot both be supported. Different-condition support requires explicit distinct conditions in the original claims. An unresolved group relationship withholds the entire group. Incomplete or invalid responses are rejected atomically and retried at most once within the existing budget.

## Publication and suggested fixes

[verification.py](../src/code_reviewer/review/verification.py) owns the publication predicate shared by reports and structured output. Only a self-check-eligible candidate with an accepted supported assessment and resolved group relationship can publish. Verification can correct categories but cannot erase original candidates, change severity or add agreement votes. Severity and agreement do not substitute for evidence.

Remedies have a separate status: `not_assessed`, `supported`, `refuted` or `unresolved`. An assessed remedy requires explanation; supported/refuted remedies need evidence references. A supported defect may publish while its unassessed, unresolved or refuted suggestion is omitted. Even supported remedies are not executed/tested patches or authorization for automatic changes.

Reviewer/consolidation failures, missing evidence, malformed replies, prompt bounds, verification budget exhaustion and cancellation never allow pending candidates through. Independently completed investigations can still publish alongside a clear incomplete-coverage warning. A completed investigation that finds a claim unresolved also marks the review incomplete; zero published findings must not imply a clean review when investigation remains incomplete. Original budget pools and no-borrowing rules still apply, so a large candidate set can exceed Verifier capacity.

Progress shows verification-pending counts without candidate titles or locations. It is not an automation interface. The final report shows only supported findings and aggregate outcome/completion counts; it does not repeat withheld claims in an appendix.

## Private audit and automation

Both host and VM runs retain `findings.json` alongside the report, summary and stats. The object includes:

- `version`, pinned `head_sha`, `base_sha`, `merge_base` and `stop_reason`;
- aggregate `verification` counts;
- the frozen `criteria` index with rule text and source paths, so criterion citations remain auditable;
- `findings`: the supported-only view, including assessments and separately supported suggestions;
- `audit`: all master candidates, original variants, evidence, assessments and investigation errors.

The artifact can contain source, findings and rejected suggestions. Keep it private. Consumers that trigger coding agents must read only `findings`, check the pinned commit identities and review completion, and independently investigate and validate their patches. Never treat the `audit` list or provisional progress as instructions to act.

VM transfer requires the same closed artifact-name allowlist, 4 MiB per-file and 8 MiB combined limits, UTF-8/JSON checks and credential scanning used for other results. Oversized artifacts fail export; there is no silent truncation. Full requests and raw replies remain ephemeral in the VM. Candidate content is excluded from Langfuse; tracing records bounded counts, timings and per-call usage/cost metadata.

## Checks and limits

The stdlib validator enforces the bounded JSON subset used by this schema, plus semantic references, source fields, safe paths, grounded quotes, exact coverage and status rules. Provider schema conversion does not replace local validation. Proposed inference is never treated as a deterministic fact merely because JSON validates.

Bounds are 40 candidates per Reviewer response, 12 supporting citations per candidate and 12 Verifier additions per assessment, with bounded text. A connected conflict group exceeding 40 candidates is withheld as incomplete rather than truncated or split without context. Existing whole-prompt, model-call and run budgets remain in force.

Fake-runner tests cover schemas, evidence, contradictions, budget/failure handling, preservation and publication. Offline Claude/OpenAI smoke checks exercise the actual adapters and verification calls without credentials or inference. These establish wiring and filtering behavior, not real-model precision or recall. Quality comparison evidence and remaining deployment qualification belong in [the roadmap](proposal.md).
