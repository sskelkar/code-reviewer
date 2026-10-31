# Tracing delivery checklist

The current architecture, configuration, privacy policy and cache accounting are documented in [local tracing](tracing.md). Module ownership is in [architecture](architecture.md#ownership); the method-level path is in [the project explainer](project-explainer.md#6-optional-tracing-across-the-vm-boundary). Keep those guides authoritative rather than duplicating the implementation design here.

## Delivered

- [x] Digest-pinned local Langfuse 4.72.0 deployment, loopback UI on port 4000, private generated secrets and persistent volumes.
- [x] Optional host SDK 5.1.0 on Python 3.11+, with Python 3.9-compatible core/guest recording and no SDK in the VM.
- [x] Metadata-only typed stages, model calls, tool operations, retries, validation counts, cache/token usage and outcomes for both flows.
- [x] Explicit persona thread context and host/guest trace identity; one logical root with original timestamps.
- [x] Bounded guest stats transport and host validation, including connected-subtree and host-ID collision checks; unchanged guest egress.
- [x] Atomic local persistence before deadline-bounded host export; nonfatal SDK/service failures and credential-free preflight/dry-run.
- [x] Local synthetic reviews through both adapter paths and flows, plus retry/quota failure cases; stored observations fetched through the Langfuse CLI and audited against current best practices.
- [x] Root-detection regression, source/secret canaries, malformed/saturated trace handling, interrupted export persistence and both supported Python suites.
- [x] Tracing package, separate deployment assets, current setup guide and architecture/call-chain diagrams.

## Qualification status

Local synthetic ingestion establishes harness/export behavior: stored spans have one root, complete parent links/types, original timing, matching token/cache totals and no source/secret canaries. It does not establish real provider usage behavior or review quality. Unit tests do not boot VMs or call model endpoints.

## Remaining qualification

- [ ] Exercise tracing through an actual Firecracker workload and verify returned metadata plus unchanged confinement/networking.
- [ ] Run a separately authorized authenticated review and compare provider usage/cache fields with the report and local trace.
- [ ] Qualify native Claude intermediate visibility if aggregate invocation traces prove insufficient; current native tool internals are explicitly unavailable.

No prompt changes, additional paid comparisons, full-content capture, tracing collector, live VM stream or expanded guest egress are prerequisites. Keep ordinary review budgets and reports authoritative; traces are diagnostic data.

## References

- [Langfuse tracing best practices](https://langfuse.com/docs/observability/best-practices).
- [Docker Compose deployment](https://langfuse.com/self-hosting/deployment/docker-compose).
- [SDK compatibility](https://langfuse.com/docs/observability/sdk/overview) and [token/cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking).
