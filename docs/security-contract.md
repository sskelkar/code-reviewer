# Security contract for the reviewer

Confirmed by the user on 2026-10-26. This governs all future implementation and supersedes the earlier personal-repository/OpenAI pilot exceptions. The goal is one supported, repository-owned reviewer with production security requirements. It does not declare the existing implementation production-ready.

## One security bar

Repository ownership, visibility, sensitivity, provider, model, review mode and pass count never select weaker isolation. Public/personal code is still untrusted input. Provider approval for particular data is a separate authorization decision: existing Claude-only restrictions for production material remain in force.

Supported providers need explicitly qualified authentication, endpoints, tool restrictions and cleanup. An unsupported provider/model/runtime combination must fail with a clear reason; it must not run outside the sandbox, broaden network access or discover alternate credentials. A model ID selects inference behavior, not infrastructure authority.

All product behavior belongs in this repository, with tests and documentation: PR acquisition, commit pinning, input admission, review orchestration, provider integration, credential handling, report storage and cleanup. Temporary job directories, clean clones and disposable VMs are expected runtime resources. Temporary handwritten launchers, hard-coded machine paths and undocumented setup are not the user workflow.

## Required boundaries

| Boundary | Requirement |
|---|---|
| Operator configuration | Separate trusted runtime/security/executable/provider settings from target repository review criteria. Never execute a repository-selected host binary or load its settings as security policy. |
| Acquisition | Parse/fetch untrusted Git input within a qualified isolation boundary with clean configuration, bounded resources and approved source access. No target hooks, dependencies, scripts or builds execute. A public repository is not an exception. |
| Review job | One disposable VM contains the review Python process and its model/tool sessions. Enforce unprivileged execution, immutable runtime/head snapshot, bounded scratch and tested execution/syscall/resource restrictions. Qualify VMM/jailer and guest controls separately. |
| Network | Enforce default-deny outside the untrusted job. Permit only the required destinations/protocols for the current phase; revoke acquisition access before inference. Model text cannot change policy. |
| Credentials | Authenticate through a qualified provider-specific path; no images, arguments, target files, logs, shared home/keychain or implicit credential discovery. Resolve guest-compromise exposure and credential lifetime/revocation before release; a disposable key is not a waiver. |
| Model tools | Read/Grep/Glob within admitted code only. Models propose; deterministic validators ground findings, validate references, account usage and write artifacts. No model authority to launch host commands, install integrations or publish. |
| Results | Fixed bounded artifacts, private predictable storage, validated frames/paths/types and safe rendering. Reports remain untrusted data; publishing needs separate explicit user authority outside the review job. |
| Lifecycle | External deadlines and resource limits, cancellation/join before cleanup, recovery of interrupted jobs and stale-state refusal. No active workload or credential authority should survive unnoticed after a failure. |
| Release evidence | Repeatable hostile-input/containment tests plus both-flow regressions. Record control coverage and limitations; a boot, simulated API test or truthful guest alone cannot establish security. |

An external allow-list does not prevent disclosure to its allowed provider, and local token-rate estimates are not hard spending limits. Neither provider failure nor model behavior may turn off protections to make a review complete. Absolute protection against every future exploit is not a release claim.

## Implementation and release order

1. Define the repository-owned command/configuration boundary and integrate the existing flows, provider adapters and sandbox lifecycle. Remove dependence on per-run external scripts; missing production controls must be visible release blockers, not silent fallbacks.
2. Close acquisition, jailer, immutable filesystem, guest execution/syscall, credential and crash-recovery gaps with adversarial tests. Implement acquisition inside the selected qualified boundary rather than retaining the earlier weaker outer-worker fetch exception.
3. Qualify each provider's real authentication/inference/tool behavior independently. Use synthetic inputs and no credentials for boundary testing first; authorize and minimize any credential-bearing qualification run explicitly, without calling it a production review.
4. Enable supported real reviews only once the required control evidence exists for that backend/runtime. Keep the same bar when switching repositories or models.

## Current state

The existing two review flows, external TAP/nftables/CONNECT policy, bounded capsules/results, private vsock bootstrap, non-root guest worker and several cleanup checks are reusable implementation. They are not discarded or relabeled as complete production protection.

The normal Claude CLI remains unsandboxed. The separate connected OpenAI module lacks the complete product entry point and several gates above; it places the API key inside the guest. Public acquisition was performed manually in the outer Linux worker, which does not satisfy the final acquisition requirement. No authenticated review has run in this sandbox. The temporary personal-pilot launch command is withdrawn; do not use it as the final reviewer interface.

See [architecture](sandbox-architecture.md), [security roadmap](security-roadmap.md) and [handover](HANDOVER.md). Historical pilot records are evidence about earlier work, not current permission to weaken this contract.
