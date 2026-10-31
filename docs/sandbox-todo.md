# Finish the review VM

Working checklist for the VM execution path. The current product direction also retains explicit direct-host execution; repository labels never choose behavior. Keep one disposable Firecracker VM for the whole review; Lima only supplies Linux/KVM on macOS. Keep the existing review loops unchanged. No per-persona VMs, distributed scheduler, enterprise audit service or new VM backend.

## Implemented controls

- [x] Trusted operator settings and pinned artifact staging; no target-selected security settings or executables.
- [x] Bounded Read/Grep/Glob and input/result frames; no model command-execution tool.
- [x] External deny-by-default guest networking with an explicitly permitted inference tunnel.
- [x] Non-root connected reviewer, bounded scratch and initial resource/deadline controls.
- [x] A repository-owned Mac deployment check with private results and handled-failure worker shutdown.

## Verification checklist

- [x] Start Firecracker through the matching pinned upstream jailer; verify reduced privileges, process/filesystem confinement and resource limits in a real VM check.
- [x] Make admitted code read-only to the reviewer independently of its file tools; keep runtime root-owned and prevent privilege escalation. Test write/chmod attempts using native processes.
- [x] Share VM start/supervision/stop code between the supported checks and connected job supervisor instead of maintaining separate lifecycle implementations. The normal review command uses that lifecycle.
- [x] Make abrupt supervisor loss bounded and stale-job recovery explicit; exercise cancellation/deadline/crash cleanup. A separate no-key lifecycle test kills the supervisor and observes the VM deadline; explicit recovery restarts only the selected dedicated worker under its launcher lock. Host failure still interrupts the job; automatic resume is not promised.
- [x] Separate reusable image preparation from job execution; avoid rebuilding unchanged images without weakening source/artifact pinning. Clean root-owned images are keyed by source, builder, copied OS bytes/permissions/links and workload; image hashes are checked again on reuse and staging.
- [x] Fetch and pin a public PR directly inside its review VM, with bounded clean unprivileged Git and source access revoked before inference. Capsule import remains optional. The supported no-key check exercises acquisition; normal-command dispatch uses the same acquisition.
- [x] Implement selected-adapter settings, endpoint policies and private runtime credentials behind the shared core. Live authentication/inference/native-tool qualification remains below. Keep credentials private and document guest-compromise exposure; no automatic custom broker.
- [x] Connect the normal command to the VM-default lifecycle, private result transfer and display; never silently fall back. The explicit direct-host selector/provider factory use host acquisition and the same core; both dispatch paths are wired.
- [x] Run offline hostile-input/containment, adapter, flow and command-wiring regressions.
- [x] Complete a real ChatGPT-backed review through the normal VM command (user-confirmed).
- [ ] Qualify native Claude authentication/read tools, live persona coverage and long-running subscription refresh through the VM.
- [x] Consolidate current docs/runbooks, remove duplicate security plans and the unusable alternate CLI, and share honest offline configuration status instead of static certification tables.

## Scope decisions

Use the existing VM boundary, upstream confinement, non-root execution, read-only code, bounded tools/resources and external networking. A custom guest-wide executable/syscall sandbox is deferred, not silently declared implemented. Read-only model tools do not contain native implementation bugs. Guest self-reported probes are diagnostics, not attestation; host controls enforce isolation.

The current [execution contract](security-contract.md) and [roadmap](proposal.md#checklist) define shared behavior and the independent VM/host choice. VM dispatch checks concrete configuration and runtime controls; explicit host reviews require operator selection. Record qualification results against the named controls. Create a handover only when explicitly requested.

## Qualification status

No-key deployment checks cover both simulated review flows, public source acquisition/revocation, jailer/native confinement, resource limits, cancellation/supervisor loss, cleanup and worker recovery. These checks establish the named infrastructure behavior only.

Subscription protocol tests cover browser callback state/PKCE, signed identities, protected account storage, rotating refresh credentials, streaming completion, bounded read-tool continuations, token accounting and private access-token handoff. They use simulated endpoints and synthetic credentials.

ChatGPT-backed VM review is user-confirmed. This is execution evidence, not a quality assessment or independent audit of every boundary. Outstanding: native Claude authentication/tools, live persona coverage, long-running refresh and broader failure-path qualification. Account entitlement, model quality and hard provider billing caps are not established by unit tests.
