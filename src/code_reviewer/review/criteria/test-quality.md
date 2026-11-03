# Test quality default criteria

Canonical rules for this persona. Static assignments and focus instructions are in [personas.json](../personas.json); common review requirements are in [review-standard.md](../review-standard.md). Project rules take precedence.

## [TEST-001] Cover important changed behavior with effective tests at the right level

**Inspect:** Existing and changed coverage for meaningful changed branches, boundaries, failure paths and component interactions. Inspect both successful and rejected/error outcomes where behavior changes. Trace setup, action and assertions: what behavior can fail while the test still passes? Check whether the selected test level can exercise the interaction the test claims to verify.

**Report:** A specific important changed behavior lacks relevant coverage, an assertion does not detect the claimed defect, or the test isolates away an interaction it claims to verify. Name the missing scenario or ineffective assertion and the concrete regression it could leave undetected. Recommend a focused test where sufficient, or integration/end-to-end coverage where real interactions are necessary.

**Do not report:** Missing test-file changes alone, behavior already covered by existing tests, a numerical test-pyramid ratio, or a universal requirement for integration/end-to-end tests. Do not require tests that mirror private implementation details without protecting meaningful behavior.

## [TEST-002] Keep tests independent, repeatable and mocks faithful

**Inspect:** Setup/cleanup, shared state, execution-order dependencies, time/randomness, and mocked interactions. Compare mocks and stubs with repository-visible collaborator contracts, and check whether the mock replaces the very behavior the test claims to exercise.

**Report:** A demonstrated state leak, order dependency, uncontrolled source of nondeterminism, or misleading mock can make a test fail without a relevant defect or pass despite one. Explain the failure sequence or the real interaction hidden by the test setup, with repository evidence.

**Do not report:** Mocking alone, legitimate isolation of an external boundary, deliberate fault injection, or a blanket preference for real dependencies. Do not invent external service behavior. If this is the same defect as an ineffective assertion under `TEST-001`, report it once under the most specific rule.

## [TEST-003] Discover the intended tests and propagate their failures

**Inspect:** Repository-defined test commands, test discovery patterns, suite registration, skip conditions and failure handling in scripts or automation. Trace whether the relevant tests are selected and whether assertion failures, test-runner errors or failed setup make the enclosing test command fail as intended.

**Report:** A concrete configuration/registration change leaves intended tests undiscovered or unintentionally skipped, or a visible script/error-handling path turns a failed test run into success. Identify the affected tests, the repository-defined command/configuration and the selection or exit-status path that creates the false green result. An individual ineffective assertion belongs under `TEST-001`.

**Do not report:** Missing CI, a preferred runner, intentional test subsets or documented skip conditions, or an unavailable dependency in the review environment alone. Do not infer that a suite runs or passes merely because tests exist; do not claim execution results without evidence.
