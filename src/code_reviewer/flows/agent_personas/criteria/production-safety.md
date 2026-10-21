# Production safety default criteria

Canonical rules for this persona. Static assignments and focus instructions are in [personas.json](../personas.json); common review requirements are in [review-standard.md](../review-standard.md). Project rules take precedence. Also assigned: the canonical [COR-002 producer-consumer contract rule](correctness.md#cor-002-keep-producer-and-consumer-contracts-consistent), authored once in correctness.

## [SAFE-001] Preserve security and privacy boundaries

**Inspect:** Changed handling of untrusted data, authentication, resource authorization, secrets and sensitive data. Trace inputs through dangerous interpretation points such as SQL, shell commands, file paths or rendered content. Examine repository-visible access and data-handling policies.

**Report:** A supported injection/unauthorized-access path, credential material embedded in code or exposed through files/logs on a repository-visible path, or sensitive data disclosed contrary to repository-defined requirements or to an unintended recipient. Identify the input/data path, missing or ineffective control, and concrete exposure. Establish access-policy expectations from repository evidence.

**Do not report:** Synthetic credentials as real secrets, string concatenation without an unsafe interpretation of untrusted data, an operation merely because it lacks a local check already enforced on the visible path, or an assumed requirement that every operation be private. Do not invent external infrastructure guarantees or failures, or assert that a credential is externally valid from its appearance.

## [SAFE-002] Validate inputs and enforce finite bounds

**Inspect:** Repository-implemented input boundaries for requests, messages, files, configuration and command-line arguments. Check required fields, types, formats, allowed ranges and finite size/work limits for text, collections, payloads, files, pagination and batches. Trace whether checks happen before the affected allocation, parsing or work, using the actual repository-visible execution order.

**Report:** Invalid input or an unbounded input is accepted on a repository-visible path and can cause a concrete failure or uncontrolled work/allocation. Identify the boundary, the missing validation or finite bound, and how the input reaches the affected operation.

**Do not report:** Every internal function as another trust boundary, a local check already enforced by the repository-visible path, or a particular numeric threshold without repository support. Do not rely on undocumented external callers to establish or dismiss the problem.

## [SAFE-003] Handle failures, retries and resource lifecycles safely

**Inspect:** Changed operations that can fail, error/early-return paths, resource acquisition and cleanup, retries and cancellation. Trace what happens when an operation fails partway through, and whether work and owned resources actually stop or are released.

**Report:** A reachable failure is swallowed or presented as success, cleanup is missing, retry work has no termination bound, or cancellation leaves work/resources running. Explain the triggering failure, the relevant control flow and its concrete consequence. Retry-induced duplicate state changes belong under `SAFE-004` when that is the root cause.

**Do not report:** An error merely because it propagates, cleanup already provided by a context manager or equivalent mechanism, blanket requests for try/catch, or an intentionally persistent worker merely because it loops. Do not assume a dependency fails or blocks in a particular way without supporting evidence.

## [SAFE-004] Preserve data integrity, atomicity and concurrency invariants

**Inspect:** Related database writes, transaction boundaries, shared mutable state, check-then-act sequences and retry/replay behavior. Identify the repository-supported invariant and trace a specific partial failure, repeated operation or concurrent interleaving against it.

**Report:** Partial updates, lost updates, corruption, unintended duplication or shared-state races violate that invariant. Where repository code/contracts establish that related writes must succeed or fail together, report a missing transaction or equivalent atomic mechanism and the inconsistent state it can leave behind.

**Do not report:** Multiple writes alone as proof of a missing transaction, synchronization already provided by a visible lock/constraint/atomic operation, or an assumed database isolation or delivery guarantee. Do not require idempotency for an operation without establishing a repetition path and the harmful duplicate effect.

## [SAFE-005] Check query compatibility with repository-defined indexes

**Inspect:** New or changed queries, including ORM-generated query shapes that can be established from the code, against repository-visible schemas, mappings and migrations. Compare filters, joins and ordering with declared indexes, including indexes implied by primary-key or unique constraints where the database defines them. Consider composite column order, indexed expressions, partial-index predicates and index methods using the database engine/version and behavior supported by repository evidence. Account for applicable migrations and generated schema definitions before concluding that an index is missing.

**Report:** A demonstrated query/index mismatch, loss of a previously supported indexed access path, or violation of an explicit repository indexing requirement, with a concrete repository-supported consequence. Identify the query, relevant schema/index definitions, the incompatible or missing index support and why it matters on the reachable code path. Describe static eligibility or a missing repository definition precisely; keep uncertain planner behavior or performance implications in notes rather than presenting them as established defects.

**Do not report:** Every unindexed query as a defect, a matching column name alone as proof of index eligibility, or the presence of an eligible index as proof it will be used. Do not assert an actual full-table scan, measured slowness, production table size, or the deployed index state from schema code alone. Do not invent database engine/version assumptions, unseen external indexes or universal composite-index rules. An execution plan can support claims only for its recorded query, parameters and environment; schema inspection alone cannot establish a runtime plan.
