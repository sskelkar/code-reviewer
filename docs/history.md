# Review-quality observations

Small-sample experiments inform the [review-quality comparison policy](proposal.md#review-quality-experiments). Interpret each result using the prompts and criteria saved with that experiment; the results do not establish current accuracy.

| Experiment | Observation | Limitation |
|---|---|---|
| Initial Haiku Reviewer | Separate one-pass runs found 1 and 2 issues; six passes found 4 distinct issues, each with low agreement. No findings were dropped by grounding or self-check. | Relevant code paths were missed; agreement was sensitive to inspection coverage. No controlled before/after result establishes the benefit of the subsequent completeness instruction. |
| Rule citations and duplicate grouping | A six-pass run produced 30 findings; 11 were dropped for bracketed rule IDs. After citation repair and Comparator changes, two Haiku replays produced identical groupings, including two issues seen in all six passes. | Replaying fixed findings tests consolidation, not fresh review quality. |
| Borderline Comparator merges | A rewritten Haiku instruction produced 2 unchanged and 2 partially merged results across 4 replays. Sonnet merged the target cluster in all 4 replays, but also merged a debatable separate claim in another saved run. | Small sample; more aggressive merging is not necessarily more accurate. |
| Ten-default criteria revision | Offline tests verify loading and prompt delivery. | No live before/after comparison; no quality improvement claimed. Older rule IDs must be interpreted using their saved criteria. |
| Repeated false positive | One incorrect finding appeared in 4 of 6 passes; relevant repository guidance was absent from the review context. | Correlated mistakes invalidate interpreting agreement as correctness; the recorded native output did not expose the searched tool paths. |

## Review scope

The project uses four review perspectives as its sole pipeline. The single-agent flow, mode selector, convergence settings, generic GEN-* defaults and replay helper were removed. Retained persona prompts, schemas and rule text were moved to `src/code_reviewer/review/` without content changes.
