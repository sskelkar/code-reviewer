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

## One production security contract

The [security contract](security-contract.md) is authoritative. The user requires one supported reviewer for every repository and provider. Earlier weaker personal/public/OpenAI pilot exceptions and per-run launch scripts are superseded. Preserve reusable sandbox code, but do not describe its missing production controls as optional merely because a test uses public code or a cheap model. Existing Claude-only data authorization remains separate from the common isolation requirements.

## Build next, in order

No new Python dependencies are authorized. Keep the one-job VM design; add services or isolation layers only when a required boundary calls for them.

| Step | Work | Exit evidence |
|---|---|---|
| 1 | Repository-owned review command, trusted operator configuration and provider selection | No external driver scripts or target-selected executables/security policy; predictable private reports; unsupported or unenforced configurations refuse execution |
| 2 | Qualified isolated PR acquisition and admission | Clean bounded fetching/parsing, pinned base/head, no target execution; source access revoked before inference |
| 3 | Complete production VM/filesystem/execution boundaries | Jailer, immutable runtime/snapshot, non-root worker and tested guest execution/syscall/resource controls |
| 4 | Provider-specific credential and inference qualification | Supported secure authentication, approved egress/tool behavior, credential lifetime/compromise/revocation addressed for each backend |
| 5 | Lifecycle and release tests | Hostile path/network/process/input/output tests, cancellation, hard-failure recovery, both-flow regressions and explicit release evidence |
| 6 | Supported real reviews | All required controls pass for the selected backend/runtime; no weaker public/personal/model exception |

The existing offline workloads and connected module provide useful evidence and reusable mechanics. They are development components until these gates pass. Do not prompt for a real key or resume the earlier personal review through the temporary launcher to bypass the missing final workflow. Credential-bearing qualification must be an explicit bounded test of a specific control, not a default escape hatch.

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

The [OpenAI backend](sandbox-openai.md) is implemented for synthetic testing, with bounded local read tools; both flows also passed in a no-network guest with simulated API responses and no real key. Restricted connectivity/private key bootstrap and bounded input/report paths are now implemented with no-key real boundary checks; actual inference remains pending. Production reviews remain Claude-only.

The [real native runtime probe](sandbox-runtime-auth.md) also passed offline: pinned Claude 2.1.301 starts and refuses the unauthenticated harness invocation in a 4 GiB guest. The outer VM was resized to 6 GiB for that probe. No credential or connected inference was tested.

The whole-harness prototype also passed inside the guest: two single-agent passes (three fake calls) and one four-persona round (eight fake calls), with one consolidated synthetic finding per mode. Both flows generated reports inside the guest; only a bounded JSON summary was exported. Both boot tests use unprivileged Firecracker with a VM-local KVM ACL and default seccomp; the production jailer, immutable guest runtime/snapshot, stronger execution/syscall policy and Claude authentication/native-tool qualification remain gates. The separate connected pilot adds a non-root worker and external OpenAI egress policy, with no-key checks passing. No-network boot configuration alone is not proof of an egress allow-list implementation.

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

Credential handling must meet the common contract for each provider. Decide whether a broker, provider-scoped credential or another supported mechanism is needed from the concrete compromise/lifetime requirements; do not mandate a custom relay solely for architectural symmetry. Ordinary network policy may use existing tooling. Keep patching, private storage, basic provenance, repeatable boundary tests and credential revocation from the beginning; deeper enterprise process does not replace those controls.

## Reference guidance checked 2026-10-25

These are design references, not certifications of this project. Recheck applicable upstream/runtime guidance before release and after security-relevant changes.

- [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final): explicit authorization and least privilege.
- [OWASP Agentic Top 10 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) and [prompt injection guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/): agent threat coverage and mitigations.
- [OWASP Agent Control Standard](https://genai.owasp.org/resource/agent-control-standard-acs/) and [GenAI Red Teaming Manual](https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/): runtime controls and measurable adversarial evidence.
- [Firecracker production guidance](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md): jailer, resource constraints and host-enforced networking; VMM seccomp does not filter guest application syscalls.
- [Claude sandbox scope](https://code.claude.com/docs/en/sandboxing): the built-in shell sandbox does not contain native file tools.
- [Git security guidance](https://git-scm.com/docs/git#_security): distinguish untrusted remote contents from untrusted local Git configuration.
