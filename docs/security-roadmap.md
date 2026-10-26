# Security roadmap: one isolated review job

Current plan, simplified and confirmed 2026-10-26: **one disposable VM runs Python and all Claude calls for a review**. The [sandbox architecture](sandbox-architecture.md) is the canonical design and lifecycle reference; [the runbook](../sandbox/README.md) documents the offline prototype. This deliberately replaces the earlier enterprise-scale plan with a small single-operator design.

The current ordinary reviewer is still a controlled-environment tool. Neither a successful boot nor the word “zero trust” proves production readiness. Treat source/model output as hostile, enforce boundaries independently of model behavior and record which tests pass. We cannot guarantee safety against every future exploit, a compromised host/hypervisor or a compromised model provider.

## Keep the design small

- One job VM contains Git preparation, Python orchestration and fresh Claude sessions. Persona passes remain independent model contexts; they share the job's process/filesystem boundary.
- The trusted host launcher starts/stops the guest and transfers bounded inputs/results. It is not a general command-execution service. On a compatible Mac, a mountless Lima Linux VM supplies nested KVM; on Linux/KVM no outer VM is needed.
- A container inside the job is optional packaging. No controller container, microVM per persona, custom orchestration API or separate publication service is required for the first version.
- Publishing is disabled inside the guest. Inspect and publish outside it manually initially. Do not supply GitHub write credentials or the host home/keychain to the guest.
- Own/private and unknown/public repositories use the same execution boundaries. A trusted repository can receive hostile changes; an unknown repository's base rules do not become operator security policy.
- Keep Read/Grep/Glob only; no target tests/builds, installs, hooks, skills, MCP or LSP. Any extension of execution capability needs a new decision.
- The whole guest can corrupt its own report or other persona state if compromised. Host isolation is the first boundary; per-persona integrity and semantic correctness are not guaranteed by this simpler design.

## Controlled pilot scope

The [connected-pilot plan](sandbox-architecture.md#agreed-connected-pilot-2026-10-26-not-implemented) is authoritative: one VM, external default-deny access to the required OpenAI HTTPS endpoint, and a disposable test key privately supplied to guest memory/environment at runtime. Use the operator's non-sensitive personal repository; no authored review sample project is required. Keep synthetic fixtures for automated regressions and harmless denial probes. Defer the custom API relay/credential broker and extra orchestration. A compromised guest can steal/reuse its test key; accept that only for this controlled feasibility test, revoke afterward, and keep production Claude-only.

This is not a relaxation of the eventual production gates below. Personal-repository admission/report export and externally enforced egress are needed for this pilot even though the existing prototype only accepts synthetic offline workloads. The two [confirmed review findings](sandbox-architecture.md#review-before-connected-implementation-2026-10-26) must be fixed first. No new implementation, target access or paid run is part of the current documentation update.

## Build next, in order

Effort bands are relative engineering estimates, not delivery commitments. Authentication and the chosen runtime's compatibility may change them. No new Python dependencies are authorized or added by this plan.

| Step | Work | Effort | Exit evidence |
|---|---|---|---|
| 1 | Offline whole-job and provider startup checks | Complete as scaffolding | Both flows pass with fake Claude/simulated OpenAI; real Claude refuses without auth |
| 2 | Correct the two recorded prototype defects | Small | Archive-stage failure and skipped-file IO budget regressions pass |
| 3 | Controlled personal-repo OpenAI pilot | Medium | Bounded base/head input and report export, external destination enforcement, private disposable runtime key, denial/cleanup probes, then one small user-selected review |
| 4 | Production boundary and stronger enforcement | Larger | Jailer, immutable runtime/snapshot, non-root execution, guest execution/syscall/resource controls and independent cleanup pass hostile probes |
| 5 | Claude-specific authentication/tool qualification | Medium/uncertain | Supported login without host home/keychain exposure; Claude-specific file/integration policy and egress validated |
| 6 | Authorized Claude-only production pilot | Ongoing validation | Production gates below pass; useful reviews complete with manual publishing and documented residual risks |

Implement the personal-pilot input, egress and credential pieces together before a real key/repository run; do not turn the offline smoke into a live review by adding a key alone. Trusted configuration cannot come from the target. Missing enforcement must refuse startup; never widen egress, mount host credentials or silently execute on the host to repair a failed setup.

## Minimum controls for the first real-review version

| Boundary | Required control |
|---|---|
| Configuration | Security policy and executables come from trusted operator input outside target directories; repository preferences cannot override them |
| Input | Approved source hosts, clean object stores, safe export paths/types, explicit file/count/diff/clone/disk limits; no repository code execution |
| Filesystem | Immutable runtime and admitted head snapshot; bounded private scratch; no host home, other runs, agent sockets or daemon sockets |
| Execution/syscalls | Unprivileged guest worker, required helpers only, no privilege escalation; VMM default seccomp and production jailer; guest execution restrictions tested separately |
| Network/auth | External default-deny enforcement; approved inference route only during review; no broad GitHub/cloud credentials; no unsandboxed egress exception |
| Data | Provider use approved for the project; consistent sensitive-file admission across diff/tool/report paths; private output storage and basic retention |
| Availability | Explicit numeric CPU/RAM/process/disk/output/time limits and bounded concurrency, with external job termination; reported cost is still an estimate |
| Output | Validate bounded regular artifacts or result frames, render safely, never execute/extract arbitrary guest output; manual posting outside VM |
| Evidence | Runtime/artifact versions and digests, completion/policy failures, no secrets in logs, and repeatable synthetic containment tests |

A secret detector reduces disclosure risk but cannot certify a repository secret-free. An inference allow-list still permits sending admitted code to the provider. Deleting a credential variable does not revoke a credential, and a hash records bytes without proving their provenance. Use a supported credential/proxy arrangement rather than inventing unsupported token handling.

## What already exists

Fresh contexts, pinned head/base, limited tools, saved inputs/results, deterministic validators/grounding, budgets/time limits and persona process-group cleanup are implemented. Export already skips links/special files and checks containment; Git diff disables external diff/text conversion. These are useful building blocks, not whole-job isolation.

The ordinary CLI still implicitly loads current-directory `review.toml`, which can select the Claude executable and isolation flags. A harmless synthetic load confirmed acceptance. Fix that authority boundary before any real sandbox CLI integration; a VM cannot rescue a host launcher that executes repository-selected code first.

## Local Firecracker feasibility (2026-10-25)

**Boot passed; not a security qualification.** Apple M4/macOS 15.8.1 reported nested virtualization support. A dedicated Lima 1.0.6 VZ/Ubuntu 24.04 VM exposed KVM on Linux 6.8.0-147. Runtime checks found no shared host filesystem mounts or SSH-agent forwarding; the existing Colima profile was untouched.

Official ARM64 Firecracker 1.17.0, checked against its release metadata digest, booted the official CI Linux 6.1.188 kernel with a locally constructed BusyBox initramfs, 1 vCPU and 256 MiB RAM. It emitted completion markers, had only loopback networking, powered off and exited 0. No repository, credentials, Claude, guest disk or NIC was supplied. Ubuntu image checksums were checked against the publisher's HTTPS listing. This is not independently verified production image provenance.

The [OpenAI backend](sandbox-openai.md) is implemented for synthetic testing, with bounded local read tools; both flows also passed in a no-network guest with simulated API responses and no real key. Restricted connectivity/key handoff and actual inference remain pending. Production reviews remain Claude-only.

The [real native runtime probe](sandbox-runtime-auth.md) also passed offline: pinned Claude 2.1.301 starts and refuses the unauthenticated harness invocation in a 4 GiB guest. The outer VM was resized to 6 GiB for that probe. No credential or connected inference was tested.

The whole-harness prototype also passed inside the guest: two single-agent passes (three fake calls) and one four-persona round (eight fake calls), with one consolidated synthetic finding per mode. Both flows generated reports inside the guest; only a bounded JSON summary was exported. Both boot tests use unprivileged Firecracker with a VM-local KVM ACL and default seccomp; the production jailer, read-only/non-root guest, auth/egress enforcement and hostile-input qualification remain gates. No-network boot configuration alone is not proof of an egress allow-list implementation.

## Acceptance tests before untrusted real reviews

Use synthetic files, fake credentials and disposable canaries. Test enforcement directly with hostile probe processes, not by asking a model whether it refused. These tests are separate from the parked review-quality eval harness. Paid model tests require explicit authorization.

- Repository config cannot start a marker executable, change security policy or enable hooks/integrations.
- Guest cannot read host files, other jobs or non-admitted secrets; snapshot/runtime writes fail.
- Path traversal, symlinks, hardlinks, special files, oversized archives and result artifacts fail safely.
- Direct/IPv6/DNS/redirect/private/localhost/metadata network attempts are blocked while authorized inference succeeds.
- Timeout, Ctrl-C, excessive output, disk/RAM/process abuse and crashes do not leave an active job or exhaust the host. Distinguish guest failure, policy refusal and unknown usage.
- Malicious report strings cannot become commands or active rendering behavior. Guest results cannot trigger GitHub posting.
- Missing KVM, image mismatch, missing firewall/jailer or unsupported runtime refuses the job; no weaker fallback.
- The normal single-agent and persona regression suites still pass. A shared guest intentionally does not pass a per-persona kernel-compromise isolation claim.

## Defer until a real requirement exists

MicroVMs per invocation, distributed orchestration, shared multi-tenant hosting, a controller container, signed publication approvals, central tamper-evident audit storage and an independently operated publishing service are not first-version prerequisites. For a hosted multi-user service, reassess tenant authorization/isolation, audit integrity, rate limits and incident operations before enabling that scope.

The custom credential-injection/API relay is explicitly deferred for the controlled pilot. Reconsider it only for a concrete later production requirement; ordinary network policy may use existing tooling. Keep patching, private storage, basic provenance, repeatable boundary tests and credential revocation from the beginning; deeper enterprise process does not replace those controls.

## Reference guidance checked 2026-10-25

These are design references, not certifications of this project. Recheck applicable upstream/runtime guidance before release and after security-relevant changes.

- [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final): explicit authorization and least privilege.
- [OWASP Agentic Top 10 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) and [prompt injection guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/): agent threat coverage and mitigations.
- [OWASP Agent Control Standard](https://genai.owasp.org/resource/agent-control-standard-acs/) and [GenAI Red Teaming Manual](https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/): runtime controls and measurable adversarial evidence.
- [Firecracker production guidance](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md): jailer, resource constraints and host-enforced networking; VMM seccomp does not filter guest application syscalls.
- [Claude sandbox scope](https://code.claude.com/docs/en/sandboxing): the built-in shell sandbox does not contain native file tools.
- [Git security guidance](https://git-scm.com/docs/git#_security): distinguish untrusted remote contents from untrusted local Git configuration.
