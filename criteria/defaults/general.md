# Default review criteria

Ten generic, language-agnostic criteria, supplied inside `<review_criteria>`. The Reviewer system prompt defines scope, evidence, self-checks and JSON output; these rules define what to inspect and when a concern warrants a finding. Project rules take precedence.

## [GEN-001] Implement behavior correctly

**Inspect:** Changed calculations, conditions, branches and state transitions against code, tests and requirements documented in the repository.

**Report:** Incorrect results, unreachable required behavior, or contradictions with repository-defined behavior. Give the triggering input or execution sequence, the actual result and the expected result supported by the repository.

**Do not report:** Missing product requirements inferred from personal expectations or external systems, or hypothetical failures whose necessary conditions cannot be established from the repository.

## [GEN-002] Keep logic simple and maintainable

**Inspect:** Changed branching, operations, abstractions and duplicated logic.

**Report:** Unnecessary complexity or duplication with a specific, repository-supported maintenance consequence, such as one shared rule requiring coordinated edits in multiple implementations. Identify the affected code and explain how it creates inconsistent changes or makes a concrete modification error-prone.

**Do not report:** A preferred design pattern, extraction based only on superficial similarity, speculative future requirements, or a request to simplify without explaining the practical consequence. Do not repeat a correctness finding as a separate refactoring finding.

## [GEN-003] Keep code and contract documentation clear and accurate

**Inspect:** Changed names, comments, instructions and examples against the implementation. For changed API, event, schema or configuration contracts, inspect the corresponding contract documentation available in the repository.

**Report:** Misleading descriptions, a non-obvious constraint demonstrated in the repository whose missing explanation enables a specific misuse, or contract documentation that is missing required changed details or contradicts the implementation. Identify the changed contract, the affected document or repository-defined documentation location, and the incorrect use or understanding it causes.

**Do not report:** Comments that merely restate code, personal naming or formatting preferences, or assumptions that an external document exists or must be updated. Do not invent a documentation location when the repository provides no evidence of one.

## [GEN-004] Protect security and privacy

**Inspect:** Changed handling of untrusted data, authentication, resource authorization, credentials, personal or sensitive data, and dangerous operations such as query execution, command execution and file access.

**Report:** A supported injection or unauthorized-access path, usable credentials exposed in repository files or logs, or sensitive data disclosed contrary to repository-defined handling requirements or to an unintended recipient. Identify the data path, missing control and concrete exposure. Cite repository evidence for the access policy; do not assume an operation must be private.

**Do not report:** Clearly synthetic credentials or fixtures as real leaks, concatenation without an unsafe interpretation of untrusted data, or a missing local access check already enforced on the repository-visible path.

## [GEN-005] Validate and bound inputs

**Inspect:** Input boundaries implemented in the repository, including requests, files, messages, configuration and command-line arguments. Check required fields, types, formats, allowed ranges and size limits.

**Report:** A boundary accepts invalid input or unbounded text, collections, payloads, files, pagination or batches. Identify the missing check or finite bound and the resulting failure or uncontrolled work or allocation. Base the finding on the input path visible in the repository, not on assumptions about external callers.

**Do not report:** Every internal function as a new trust boundary, or a missing local check when the repository's input path already enforces it. Do not invent a particular numerical limit when none is specified.

## [GEN-006] Handle failures and release resources

**Inspect:** Changed calls that can fail, error and early-return paths, resource acquisition and cleanup, retries and cancellation.

**Report:** Swallowed errors, success returned after failure, missing cleanup, retries without termination bounds, or cancellation that leaves work or resources running. Describe the reachable failure path and its concrete consequence, using repository evidence.

**Do not report:** An error simply because it propagates to a caller, cleanup already provided by a context manager or equivalent mechanism, or an intentionally long-running worker merely because it has a loop.

## [GEN-007] Preserve data integrity and concurrency safety

**Inspect:** Related database writes, transactions, shared state, check-then-act sequences, retries and concurrent execution paths.

**Report:** Partial updates, lost updates, corruption or unintended duplication, explaining a concrete failure sequence or interleaving. When code or repository documentation establishes that related database writes must succeed or fail together, report a missing transaction or equivalent atomic mechanism that can leave inconsistent data.

**Do not report:** Multiple database writes alone as proof that a transaction is needed, or missing synchronization when an existing transaction, lock, constraint or atomic operation already protects the relevant invariant. Do not assume undocumented database guarantees.

## [GEN-008] Keep internal contracts consistent

**Inspect:** Changed signatures, return values, API and event schemas, stored data formats and configuration, together with callers, consumers and usages available in the repository.

**Report:** A specific producer-consumer mismatch or violation of a compatibility requirement recorded in the repository. Identify both sides of the contract and the execution or migration path that breaks.

**Do not report:** Speculative breakage of unknown external consumers or an intentional contract change whose repository-visible usages and documented migration remain consistent. Documentation drift belongs under GEN-003 rather than a duplicate finding here.

## [GEN-009] Cover changed behavior with meaningful tests

**Inspect:** Existing and changed tests for important changed branches, validation boundaries, edge cases and failure paths. Check that assertions verify outcomes and would detect the relevant defect.

**Report:** A specific important behavior without relevant coverage, a test that passes despite the defect it claims to detect, or a test level that cannot exercise the interaction being asserted. Name the missing case or demonstrate the ineffective assertion. Explain the concrete regression the gap could leave undetected.

**Do not report:** Missing test-file changes alone, behavior already covered by existing tests, or deviation from a numerical test-pyramid ratio. Prefer focused tests where sufficient; integration and end-to-end tests are appropriate for behavior that requires real component interactions.

## [GEN-010] Keep tests independent and mocks faithful

**Inspect:** Test setup and cleanup, shared state, execution-order dependencies, time or randomness, and mocked interactions.

**Report:** A concrete order dependency, leaked state, uncontrolled nondeterminism, or a mock that replaces the behavior under test, contradicts the repository-visible collaborator contract, or hides an interaction the test claims to verify. Explain how the test can pass despite a defect or fail without one.

**Do not report:** Mocking alone, legitimate isolation of external services or failure conditions, or a blanket preference for real dependencies. If the same defect is an ineffective assertion under GEN-009, report it once under the most specific rule.
