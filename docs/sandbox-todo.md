# Finish the review VM

Working checklist for the VM execution path. The current product direction also retains explicit direct-host execution; repository labels never choose behavior. Keep one disposable Firecracker VM for the whole review; Lima only supplies Linux/KVM on macOS. Keep the existing review loops unchanged. No per-persona VMs, distributed scheduler, enterprise audit service or new VM backend.

## Already implemented

- [x] Trusted operator settings and pinned artifact staging; no target-selected security settings or executables.
- [x] Bounded Read/Grep/Glob and input/result frames; no model command-execution tool.
- [x] External deny-by-default guest networking with an explicitly permitted inference tunnel.
- [x] Non-root connected reviewer, bounded scratch and initial resource/deadline controls.
- [x] A repository-owned Mac deployment check with private results and handled-failure worker shutdown.

## Finish next

- [x] Start Firecracker through the matching pinned upstream jailer; verify reduced privileges, process/filesystem confinement and resource limits in a real VM check.
- [x] Make admitted code read-only to the reviewer independently of its file tools; keep runtime root-owned and prevent privilege escalation. Test write/chmod attempts using native processes.
- [x] Share VM start/supervision/stop code between the supported checks and connected job supervisor instead of maintaining separate lifecycle implementations. The normal review command now reuses that lifecycle.
- [x] Make abrupt supervisor loss bounded and stale-job recovery explicit; exercise cancellation/deadline/crash cleanup. A separate no-key lifecycle test kills the supervisor and observes the VM deadline; explicit recovery restarts only the selected dedicated worker under its launcher lock. Host failure still interrupts the job; automatic resume is not promised.
- [x] Separate reusable image preparation from job execution; avoid rebuilding unchanged images without weakening source/artifact pinning. Clean root-owned images are keyed by source, builder, copied OS bytes/permissions/links and workload; image hashes are checked again on reuse and staging.
- [x] Fetch and pin a public PR directly inside its review VM, with bounded clean unprivileged Git and source access revoked before inference. Capsule import remains optional. The supported no-key check exercises acquisition; normal-command dispatch now uses the same acquisition.
- [x] Implement selected-adapter settings, endpoint policies and private runtime credentials behind the shared core. Live authentication/inference/native-tool qualification remains below. Keep credentials private and document guest-compromise exposure; no automatic custom broker.
- [x] Connect the normal command to the VM-default lifecycle, private result transfer and display; never silently fall back. The explicit direct-host selector/provider factory now use preserved host acquisition and the same core; both dispatch paths are wired.
- [ ] Run hostile-input/containment and both-flow regressions, then an authorized PR through the supported command.
- [ ] Consolidate current instructions/runbooks and remove superseded prototype instructions; replace static readiness claims with concrete supported-runtime checks as implementation becomes available.

## Scope decisions

Use the existing VM boundary, upstream confinement, non-root execution, read-only code, bounded tools/resources and external networking. A custom guest-wide executable/syscall sandbox is deferred, not silently declared implemented. Read-only model tools do not contain native implementation bugs. Guest self-reported probes are diagnostics, not attestation; host controls enforce isolation.

The current [execution contract](security-contract.md) and [roadmap](security-roadmap.md) define shared behavior and the independent VM/host choice. This checklist records the agreed simplification direction; unfinished qualification must remain visible. VM dispatch now checks concrete configuration and runtime controls; explicit host reviews require operator selection. No test count or status-row change enables live reviews by itself. Record completed work and exact evidence here. A handover document is created only on explicit user request.

## Evidence: 2026-10-28

The actual Mac command passed with both workloads using the shared jailed `VirtualMachine`. Host observations confirmed non-root UID, no capabilities, no-new-privileges, default seccomp, separate PID/mount namespaces, chroot and cgroup limits. The connected guest confirmed a read-only input mount and failed native write/chmod/runtime-write attempts. Both flow algorithms completed with simulated OpenAI replies; no authentication or paid inference was performed. Host network cleanup and outer-worker shutdown passed. Remaining rows are not implied by these checks.

The opt-in `--lifecycle` check also passed actual cancellation and supervisor SIGKILL. After SIGKILL the VM remained under its independent systemd deadline, then stopped without Python cleanup running; the test reaped remaining state and removed the runtime bind mount. This exposed and fixed graceful-stop delays and unreliable bind-mount detection. `sandbox recover` passed its fresh-worker/idle-state check and shutdown. Image preparation/reuse is covered by invalidation, corrupt-cache and partial-build regressions; no target data or credentials are cached.

Public acquisition also passed the real no-key Mac check on the explicitly selected public PR. API and Git parsing/fetching occurred inside the jailed review VM, pins were returned privately, existing source tunnels closed on the external phase switch, fresh GitHub tunnels were denied, and file/native/network probes plus host network cleanup and worker shutdown passed. No key, inference or production material was used. The earlier cleanup passed its regression suites on Python 3.9 and 3.13; finding quality has not been evaluated by this check.


Normal-command VM dispatch now reuses the worker/image/jailer components. All 370 tests pass on Python 3.9 and 3.13. Regression tests cover private two-way worker framing, delayed selected-credential release, both providers/strategies, complete review-setting transfer, native parallel-process environments, native-image digest invalidation, private report export and refusal when shutdown fails. Configuration readiness is separate from live qualification. A new real no-key deployment check passed both flow simulations, boundary probes, network cleanup and worker shutdown. The initial rerun caught a TLS-probe destination-variable regression, which was fixed and covered for both provider destinations. No credential or inference was used; authenticated verification remains outstanding.
