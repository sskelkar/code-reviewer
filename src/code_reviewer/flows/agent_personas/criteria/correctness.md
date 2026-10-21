# Correctness default criteria

Canonical rules for this persona. Static assignments and focus instructions are in [personas.json](../personas.json); common review requirements are in [review-standard.md](../review-standard.md). Project rules take precedence.

## [COR-001] Implement the intended behavior correctly

**Inspect:** Changed calculations, boolean conditions, branches, ordering and state transitions. Follow representative normal and edge inputs through the changed path, including empty/zero values, missing versus explicit false values, duplicate values and boundary values where relevant. Check type conversions, precision, rounding, units or date/time semantics when the repository establishes their required meaning.

**Report:** A supported input or execution sequence produces an incorrect result, takes the wrong branch, loses required behavior, or reaches an invalid state. Explain the trigger, actual result and expected result, citing the code or authoritative repository contract that establishes the expectation.

**Do not report:** Invented business requirements, assumptions about unknown external systems, or a preference for another algorithm that preserves the required behavior. Do not treat a PR description, planning document or existing test as sufficient proof of the correct result without checking the relevant code/contracts.

## [COR-002] Keep producer and consumer contracts consistent

**Inspect:** Changed signatures, return values, exceptions, API/event schemas, stored formats and configuration, together with repository-visible callers, consumers and migration paths. Check compatible representations, required fields, optional values and error behavior where relevant.

**Report:** A specific producer-consumer mismatch or violation of a repository-defined compatibility requirement. Identify both sides, the incompatible behavior or representation, and the execution or migration sequence that breaks.

**Do not report:** Speculative breakage of unknown consumers, undocumented compatibility promises, or an intentional change whose visible usages and documented migration remain consistent. A documentation-only mismatch belongs under `CQ-002`; do not duplicate the same underlying defect.

## [COR-003] Keep entry points, registrations and packaged resources usable

**Inspect:** Changed entry points, imports, module exports, command/route/plugin registrations, build or package manifests, and runtime-loaded files. Follow the repository's declared launch/build/install paths and check that referenced symbols, files and registrations are included and reachable. Apply this only to artifacts and execution paths the repository actually supports.

**Report:** A repository-defined entry point cannot load, a changed feature is not connected to its declared caller or registry, or a required file/module is omitted from the delivered artifact. Identify the launch/build path, the missing or incompatible connection, and the concrete failure or unavailable behavior. A direct producer-consumer mismatch already covered by `COR-002` should be reported once under the most specific rule.

**Do not report:** An unsupported installation/deployment method, the absence of a registry the project does not use, or an import/dependency failure inferred solely from what is installed in the review environment. Account for visible generation/build steps. Do not require vendoring an externally provided dependency or claim a build was run.
