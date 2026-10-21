# agent-personas

This folder owns the optional four-persona workflow. `flow.py` gathers the pinned inputs and criteria, `roles.py` freezes Reviewer inputs and calls role contracts, and `caller.py` checks budgets before every actual attempt. `budget.py` reserves proportional role/persona allowances atomically; `runner.py` owns cancellable subprocess groups. Configured rounds run concurrently (up to persona_parallel=4 Reviewer calls), then consolidate serially in persona order: correctness, code quality, test quality and production safety; `persona_passes` defaults to three rounds. No quiet-pass early stopping is used. `contracts.py` validates proposals, `master.py` records source sightings/categories/conflicts, and `report.py` owns all presentation. `models.py` contains this flow's results. No imports of the single-agent implementation are needed.

After reviewing, fresh Verifier sessions investigate distinct flagged groups only. Evidence must ground at its stated line in the head checkout. Verifier results annotate and may correct categories, but never remove/rewrite findings, change severity or transfer votes. Budget, terminal failure or cancellation stops further calls and leaves pending/unresolved groups visible.

The workflow loads these default criteria assets:

| Asset | Purpose |
|---|---|
| [personas.json](personas.json) | Four focus instructions and static rule-ID assignments; no runtime classification of defaults |
| [review-standard.md](review-standard.md) | Common scope, evidence and reporting requirements for the Reviewer contract |
| [criteria/correctness.md](criteria/correctness.md) | Behavior, contracts and entry-point/artifact wiring |
| [criteria/code-quality.md](criteria/code-quality.md) | Maintainability and accurate code/contract documentation |
| [criteria/test-quality.md](criteria/test-quality.md) | Coverage/assertions, faithful isolation, test discovery and failure propagation |
| [criteria/production-safety.md](criteria/production-safety.md) | Security/privacy, finite input bounds, failure/resource handling and integrity/concurrency and query/index compatibility |

There are thirteen canonical rules. `COR-002` is authored once in `criteria/correctness.md` and assigned to both correctness and production safety. Prompt assembly resolves the IDs through one canonical rule index; copying or rewording the shared rule would let it drift. The Markdown files use the existing shared criteria parser's ID-bearing headings and Inspect / Report / Do not report format. Their preambles explain ownership; model-facing rule instructions are in the rule bodies.

These are source assets; rendered prompts/settings are saved once per persona under `input/personas/`. Actual model requests, including retry feedback, and all model replies are saved outside the checkout. A full report lists every assigned criterion name/source, successful/failed/skipped slots and verification state. Consolidation is staged so interruption cannot leave sightings or conflict flags from an incomplete slot. `--dry-run` saves defaults-only prompts for all four personas without model/version calls or a checkout.

Select with `--mode agent-personas`; `--passes N` sets N scheduled passes per persona (4 × N total), overriding `persona_passes = 3` in TOML. Reports, progress and dry runs use the configured counts; one completed pass per persona remains unrated. `model_resolver` is reused for classification; `model_verifier` defaults to Haiku. The mode is offline-tested but no live quality comparison has run. Keep shared mechanics in `code_reviewer.shared`, preserve single-agent as the baseline and do not add persona branches inside its loop.

The agreed design is in [proposal section 12](../../../../docs/proposal.md#12-agreed-persona-design-and-next-steps); [the criteria guide](../../../../docs/persona-criteria.md) explains allocation and reporting boundaries.

`--parallel N` (1–4) overrides `persona_parallel`. Four Reviewer jobs share one pinned checkout; each has independent caller/log state. The next round waits for all review results and ordered consolidation. No independent persona queues or warmup model calls are used. Budgets and persona failure limits isolate exhausted personas; Comparator exhaustion stops new rounds, while Verifier exhaustion leaves groups pending. See [budget accounting](../../../../docs/usage.md#persona-budget-pools).
