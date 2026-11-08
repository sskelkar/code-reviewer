# Default criteria for the four review personas

Thirteen generic criteria are defined under [the persona flow](../src/code_reviewer/review/README.md). The persona flow loads these assets. Offline tests check wiring; model quality needs manual evaluation.

The canonical wording lives in the linked files below. This guide is an index, so future edits do not leave competing copies of the rules. Every rule specifies **Inspect**, **Report** and **Do not report**. These rules define inspection subjects; the persona Reviewer contract still owns evidence, self-checks, scope and JSON output.

## Static assignments

| Persona | Rule IDs and subjects | Canonical wording |
|---|---|---|
| Correctness | `COR-001` behavior/calculations; `COR-002` producer-consumer contracts; `COR-003` entry points, registrations and packaged resources | [Correctness criteria](../src/code_reviewer/review/criteria/correctness.md) |
| Code quality | `CQ-001` understandable responsibilities/maintenance; `CQ-002` accurate explanations and contract docs | [Code quality criteria](../src/code_reviewer/review/criteria/code-quality.md) |
| Test quality | `TEST-001` meaningful coverage/assertions and appropriate levels; `TEST-002` repeatability/isolation and faithful mocks; `TEST-003` intended test discovery and failure propagation | [Test quality criteria](../src/code_reviewer/review/criteria/test-quality.md) |
| Production safety | `SAFE-001` security/privacy; `SAFE-002` input validation/finite bounds; `SAFE-003` errors/retries/resource lifecycles; `SAFE-004` integrity/atomicity/concurrency; `SAFE-005` query/index compatibility; shared `COR-002` contracts | [Safety criteria](../src/code_reviewer/review/criteria/production-safety.md), [shared contract](../src/code_reviewer/review/criteria/correctness.md#cor-002-keep-producer-and-consumer-contracts-consistent) |

[personas.json](../src/code_reviewer/review/personas.json) records the exact focus instructions and static assignments. Each logical rule has one canonical ID and text. `COR-002` is defined once in correctness and referenced by safety; there are thirteen unique rules and fourteen assignments. Counts are not quotas, and no persona has greater authority than another. When explicit project criteria load, they replace the baseline and are distributed at runtime with their original text/source. Otherwise enabled baseline rules use the static assignments.

## Common standard and boundaries

[review-standard.md](../src/code_reviewer/review/review-standard.md) supplies the common review instructions: report introduced/worsened issues with repository evidence, respect project precedence, read relevant context beyond the focus, report a root cause once, and keep unverified assumptions in notes. A persona may report an evidenced correctness/safety defect it notices outside its focus. It cannot run tests or infer correctness from their mere existence.

Contract changes must be reflected in the appropriate repository-visible API, event, schema or configuration documentation. Input boundaries must reject invalid or unbounded inputs where the visible path establishes a concrete failure or uncontrolled work. Test-level recommendations should use focused tests where sufficient and real component interactions where necessary, without a fixed test-pyramid ratio or blanket ban on mocking.

No universal deployment model, database guarantee, external policy or documentation location is assumed. Seeing a query and an index definition alone does not establish a runtime query plan. Unknown external requirements and unverified assumptions do not become findings by assertion.

## Disabled defaults

`disabled_default_rules` accepts canonical IDs such as `SAFE-001` or `COR-002`. Disabling a shared rule removes it from every persona. Unknown IDs fail validation.

Scheduling is documented in [architecture](architecture.md#review-pipeline); role contracts are in [prompts](prompts.md#persona-prompts).
