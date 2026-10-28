# Execution integration roadmap

One reviewer supports runtime-selected providers and independent single-agent/persona strategies. The default request is the review VM (dispatch wired; authenticated inference untested), with implemented explicit `--no-sandbox` direct-host execution. Repository visibility or purpose does not create another product tier. See the [current contract](security-contract.md).

## Work in order

1. **Done:** select provider/execution from trusted settings, create the runner at dispatch time, and connect explicit host acquisition/core. Preflight/dry-run read no model key.
2. Verify the implemented default VM dispatch/authentication against the shared jailed lifecycle. Direct dispatch already uses existing acquisition/core. VM failures refuse; never fall back silently. Common validators, criteria, budgets and reports stay shared.
3. Qualify the implemented credential, inference, endpoint and native-tool paths using explicitly authorized live calls for both adapters inside the VM. Differences stay in adapter/runtime plumbing, not separate repository categories or review loops.
4. Verify implemented private report transfer/retention and CLI presentation during those authorized end-to-end checks. The REPL is parked with its code/tests retained. Test both providers, both strategies and explicit execution selection.
5. Return to manual review-quality comparison. The unused eval framework has been removed.

## Keep

- The shared VM/jailer lifecycle, host-observed confinement, finite resources/deadlines, read-only input and non-root/no-new-privileges.
- External acquisition/inference network phases, bounded private input/results and safe artifact staging.
- Explicit cancellation/recovery and clean image reuse; no target data or credentials cached.
- Read/Grep/Glob, deterministic evidence/JSON validation and visible accounting in both execution paths.
- A clear distinction between tests with simulated replies, real no-key deployment checks and authenticated review results.

## Avoid

No per-persona VMs, distributed scheduler, custom orchestration service, automatic credential broker, enterprise audit platform, additional VM backend or repository-specific security hierarchy. Direct mode intentionally skips VM isolation; it must be explicitly selected by the operator. No automatic handover document.

Existing checks and limitations are recorded in the [VM checklist](sandbox-todo.md) and [deployment runbook](sandbox-check.md). No-key checks and simulated inference do not establish live authentication or review quality.
