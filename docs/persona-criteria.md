# Default criteria for the four review personas

**Initial assets, 2026-10-20.** Thirteen generic criteria are defined under [the persona flow](../src/code_reviewer/flows/agent_personas/README.md). They retain the ten existing concerns and add entry-point/artifact wiring, test discovery/failure propagation and query/index compatibility. The persona workflow now loads these assets; it is tested offline but has no live quality result. The single-agent `GEN-*` defaults, prompt contents and schemas are unchanged.

The canonical wording lives in the linked files below. This guide is an index, so future edits do not leave competing copies of the rules. Every rule specifies **Inspect**, **Report** and **Do not report**. These rules define inspection subjects; the persona Reviewer contract still owns evidence, self-checks, scope and JSON output.

## Static assignments

| Persona | Rule IDs and subjects | Canonical wording |
|---|---|---|
| Correctness | `COR-001` behavior/calculations; `COR-002` producer-consumer contracts; `COR-003` entry points, registrations and packaged resources | [Correctness criteria](../src/code_reviewer/flows/agent_personas/criteria/correctness.md) |
| Code quality | `CQ-001` understandable responsibilities/maintenance; `CQ-002` accurate explanations and contract docs | [Code quality criteria](../src/code_reviewer/flows/agent_personas/criteria/code-quality.md) |
| Test quality | `TEST-001` meaningful coverage/assertions and appropriate levels; `TEST-002` repeatability/isolation and faithful mocks; `TEST-003` intended test discovery and failure propagation | [Test quality criteria](../src/code_reviewer/flows/agent_personas/criteria/test-quality.md) |
| Production safety | `SAFE-001` security/privacy; `SAFE-002` input validation/finite bounds; `SAFE-003` errors/retries/resource lifecycles; `SAFE-004` integrity/atomicity/concurrency; `SAFE-005` query/index compatibility; shared `COR-002` contracts | [Safety criteria](../src/code_reviewer/flows/agent_personas/criteria/production-safety.md), [shared contract](../src/code_reviewer/flows/agent_personas/criteria/correctness.md#cor-002-keep-producer-and-consumer-contracts-consistent) |

[personas.json](../src/code_reviewer/flows/agent_personas/personas.json) records the exact focus instructions and static assignments. Each logical rule has one canonical ID and text. `COR-002` is defined once in correctness and referenced by safety; there are thirteen unique rules and fourteen assignments. Counts are not quotas, and no persona has greater authority than another. Applicable project rules are distributed at runtime, retaining text/source and precedence over defaults.

## What the three additions address

- **`COR-003`: integration wiring.** A locally correct function can still be unreachable because its command, route, export or registration is missing, or an installed artifact can omit a required module/resource. This criterion requires a concrete repository-defined launch/build/install path, accounts for visible generation steps and does not infer failures from the review machine's installed dependencies.
- **`TEST-003`: false green test execution.** Good assertions provide no protection when discovery/registration silently omits the tests or a script ignores the runner's failure. This criterion traces the repository-defined selection and exit-status path. It does not demand CI or replace the assertion/coverage rule.

- **`SAFE-005`: query/index compatibility.** Production safety compares changed queries with repository-defined schemas, migrations and indexes, including applicable implicit, composite, expression and partial indexes. Findings require a supported mismatch or indexing requirement and a concrete consequence. It cannot confirm actual planner choices, deployed indexes or runtime slowness from schema code alone; a missing index is not automatically a defect.

The existing criteria also make inspection concrete: missing versus explicit false values and boundary cases, hidden process-state dependencies, successful and failed test outcomes, validation before affected work/allocation, and database transactions where related writes must succeed or fail together. The rules still require a supported trigger and consequence; they do not mandate patterns, numerical test ratios, speculative performance targets or undocumented external guarantees.

## Common standard and boundaries

[review-standard.md](../src/code_reviewer/flows/agent_personas/review-standard.md) supplies the common review instructions: report introduced/worsened issues with repository evidence, respect project precedence, read relevant context beyond the focus, report a root cause once, and keep unverified assumptions in notes. A persona may report an evidenced correctness/safety defect it notices outside its focus. It cannot run tests or infer correctness from their mere existence.

Contract changes must be reflected in the appropriate repository-visible API, event, schema or configuration documentation. Input boundaries must reject invalid or unbounded inputs where the visible path establishes a concrete failure or uncontrolled work. Test-level recommendations should use focused tests where sufficient and real component interactions where necessary, without a fixed test-pyramid ratio or blanket ban on mocking.

No universal deployment model, database guarantee, external policy or documentation location is assumed. Seeing a query and an index definition alone does not establish a runtime query plan. Unknown external requirements and unverified assumptions do not become findings by assertion.

## Next step

Loading, project-rule distribution, frozen prompts, the configured round-robin scheduler, Comparator/conflict records, advisory verification and reports are implemented as specified in [proposal section 12](proposal.md#12-agreed-persona-design-and-next-steps). Review the implementation and use authorized manual comparisons before drawing quality conclusions. Offline tests check plumbing, not semantic routing or review accuracy. The eval harness stays parked.

In persona mode, `disabled_default_rules` accepts canonical persona IDs and the ten legacy `GEN-*` aliases mapped in proposal §12; unknown IDs fail clearly. Disabling a shared rule removes it from every persona. Single-agent IDs and behavior remain unchanged.
