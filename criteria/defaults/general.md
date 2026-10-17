# Default review criteria

Generic, language-agnostic review rules. Each rule heading carries a stable ID in square brackets. Replace or extend these with your own rules; the IDs are what findings cite.

## [GEN-001] Handle errors explicitly

Errors and failure results from calls that can fail (I/O, network, parsing, external services, fallible operations) must be handled, propagated or deliberately documented as ignored. Do not swallow exceptions silently, return success after a failure, or leave partially applied changes without cleanup or rollback.

## [GEN-002] Validate input at trust boundaries

Data from users, requests, files, message queues, other services and environment variables must be validated before use: type, range, length, format and required fields. Reject or sanitize invalid input rather than assuming it is well formed.

## [GEN-003] Never commit secrets

Credentials, tokens, API keys, private keys and passwords must not appear in code, configuration checked into the repository, tests or logs. They belong in a secret store or environment configuration.

## [GEN-004] Prevent injection

Never build SQL, shell commands, queries, templates or file paths by concatenating untrusted input. Use parameterized queries, argument arrays, safe APIs and path normalization with an allowed base directory.

## [GEN-005] Check authorization and ownership

Operations on a resource must verify that the caller is authenticated and allowed to act on that specific resource, not only that the resource exists. New endpoints and handlers must apply the same access checks as comparable existing ones.

## [GEN-006] Release resources and bound loops

Opened files, connections, locks, transactions, timers and background tasks must be released on every path, including error paths. Loops, retries, queues and caches need explicit limits so they cannot grow or spin without bound.

## [GEN-007] Guard shared state and concurrent access

Code that can run concurrently must not read-modify-write shared state without synchronization, atomic operations or transactions. Check for race conditions, check-then-act sequences, and operations that must be idempotent.

## [GEN-008] Keep contracts backward compatible

Changes to public APIs, message formats, database schemas, configuration keys and event payloads must remain compatible with existing callers and stored data, or include a migration or versioning plan. Check callers of changed signatures.

## [GEN-009] Protect personal and sensitive data

Personal, payment and other sensitive data must not be written to logs, error messages, analytics or test fixtures in raw form. Collect and retain only what the feature needs.

## [GEN-010] Test changed behavior

New logic and bug fixes need automated tests that cover the changed behavior, including failure and edge cases. Tests must assert outcomes, not only that code runs without error. Changed behavior with no corresponding test change should be flagged.

## [GEN-011] Avoid duplicated logic

Do not copy substantial logic that already exists elsewhere in the codebase. Reuse or extract shared code, and keep related changes consistent across all places that implement the same rule.

## [GEN-012] Make non-obvious code understandable

Code whose purpose or constraints are not obvious from its names and structure needs a short comment explaining why. Remove dead code, stale comments and debugging leftovers introduced by the change.
