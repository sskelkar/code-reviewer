# Review implementation

[Architecture](../../../docs/architecture.md#review-pipeline) describes scheduling, consolidation and verification; [usage](../../../docs/usage.md#persona-budget-pools) specifies budgets; [criteria](../../../docs/persona-criteria.md) indexes defaults; [prompts](../../../docs/prompts.md#persona-prompts) defines model contracts.

| Module | Responsibility |
|---|---|
| `flow.py` | Inputs, rounds and ordered consolidation |
| `roles.py`, `prompt_contract.py`, `prompts/` | Model contracts and prompt assembly |
| `caller.py`, `budget.py`, `runner.py` | Per-attempt checks, atomic pools and cancellation forwarding |
| `contracts.py`, `master.py`, `models.py` | Validated proposals, sightings, categories, conflicts and results |
| `report.py` | Flow presentation |

## Canonical assets

| Asset | Purpose |
|---|---|
| [personas.json](personas.json) | Four focus instructions and static rule-ID assignments; no runtime classification of defaults |
| [review-standard.md](review-standard.md) | Common scope, evidence and reporting requirements for the Reviewer contract |
| [criteria/correctness.md](criteria/correctness.md) | Behavior, contracts and entry-point/artifact wiring |
| [criteria/code-quality.md](criteria/code-quality.md) | Maintainability and accurate code/contract documentation |
| [criteria/test-quality.md](criteria/test-quality.md) | Coverage/assertions, faithful isolation, test discovery and failure propagation |
| [criteria/production-safety.md](criteria/production-safety.md) | Security/privacy, finite input bounds, failure/resource handling and integrity/concurrency and query/index compatibility |
