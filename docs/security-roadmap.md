# Execution integration roadmap

One reviewer supports runtime-selected providers and independent single-agent/persona strategies. The intended execution default is the review VM, with an explicit `--no-sandbox` direct-host option. Repository visibility or purpose does not create another product tier. See the [current contract](security-contract.md).

## Work in order

1. Keep one review orchestration path and select its runner and execution location from trusted operator settings. Preserve host acquisition for the direct path. The proposed direct flag is not implemented yet.
2. Wire direct dispatch to the existing acquisition/core, and VM dispatch to the shared jailed lifecycle. VM failures refuse; never fall back silently. Common validators, criteria, budgets and reports stay shared.
3. Finish authentication, inference, endpoint policy and tool integration for both adapters inside the VM. Differences stay in adapter/runtime plumbing, not separate repository categories or review loops.
4. Integrate private report transfer/retention and local presentation, then run authorized end-to-end checks. Test both providers, both strategies and explicit execution selection.
5. Return to manual review-quality comparison. The unused eval framework has been removed.

## Keep

- The shared VM/jailer lifecycle, host-observed confinement, finite resources/deadlines, read-only input and non-root/no-new-privileges.
- External acquisition/inference network phases, bounded private input/results and safe artifact staging.
- Explicit cancellation/recovery and clean image reuse; no target data or credentials cached.
- Read/Grep/Glob, deterministic evidence/JSON validation and visible accounting in both execution paths.
- A clear distinction between tests with simulated replies, real no-key deployment checks and authenticated review results.

## Avoid

No per-persona VMs, distributed scheduler, custom orchestration service, automatic credential broker, enterprise audit platform, additional VM backend or repository-specific security hierarchy. Direct mode intentionally skips VM isolation; it must be explicitly selected by the operator. No automatic handover document.

Existing checks and limitations are recorded in the [VM checklist](sandbox-todo.md) and [deployment runbook](sandbox-check.md). Passing them does not by itself implement live dispatch.
