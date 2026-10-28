# VM execution architecture

The reviewer chooses provider, review strategy and execution location independently. VM execution is the default; `--no-sandbox` explicitly selects the implemented direct-host path. Neither path is selected from repository ownership or purpose. See the [shared execution contract](security-contract.md).

## Boundaries and process placement

One disposable Firecracker VM contains acquisition, Python orchestration, every persona and model/tool session. On macOS, dedicated mountless Lima supplies the Linux/KVM environment; on native Linux that outer compatibility layer is unnecessary. Normal Mac dispatch uses the components below; authenticated inference is implemented but remains untested. The normal launcher currently supports the Mac/Lima deployment, not a standalone native-Linux CLI path.

```mermaid
flowchart TB
    subgraph MAC["Mac workstation"]
        INPUT["Public PR URL + trusted operator configuration"]
        LAUNCH["reviewer CLI: stage trusted runtime, manage Lima,<br/>read selected process credential after probes"]
        subgraph LINUX["Dedicated mountless Lima Linux VM: KVM worker"]
            HOST["Trusted Python supervisor: settings, admission gate, cleanup"]
            VMM["Pinned matching jailer + Firecracker<br/>systemd cgroup limits / independent deadline"]
            PROXY["Standard Squid CONNECT proxy<br/>External nftables TAP and proxy-UID policy"]
            subgraph JOB["Disposable review VM: separate Linux kernel"]
                ACQUIRE["Unprivileged API/Git acquisition<br/>Pin and admit base/head/merge-base"]
                PY["Unprivileged Python: selected flow + selected adapter"]
                TREE["Read-only admitted Git input and head snapshot"]
                TOOLS["Read / Grep / Glob only"]
                SCRATCH["512 MiB scratch: runs and temporary files"]
                ACQUIRE -->|"Export, revoke source access, seal mounts"| TREE
                PY --> TOOLS --> TREE
                PY --> SCRATCH
            end
            HOST --> VMM --> JOB
            HOST -->|"Private vsock: input and phase acknowledgements"| ACQUIRE
            PY -->|"Bounded fixed report artifacts over vsock"| HOST
            ACQUIRE -->|"GitHub TLS during acquisition only"| PROXY
            PY -->|"Provider TLS during inference only"| PROXY
        end
        INPUT --> LAUNCH
        LAUNCH <-->|"Private framed SSH pipes<br/>Settings, credential request/handoff, bounded results"| HOST
        LAUNCH -->|"Publish private files after worker shutdown"| OUTPUT["Local inspection; no automatic publication"]
    end
    PROXY -->|"Acquisition: github.com + api.github.com:443"| SOURCE["Public GitHub"]
    PROXY -->|"After source revocation: inference endpoint only"| PROVIDER["Selected model provider"]
```

The trusted launcher is ordinary operator-controlled Python in the dedicated Linux worker, outside the inner review VM. It manages the jailer/VM, external network policy, private bootstrap/results and cleanup; it does not review code. The shared review core runs inside the selected execution boundary with either provider adapter. No per-persona VM, container controller or always-running orchestration service is involved.

## Implemented VM controls

- Pinned Firecracker, matching upstream jailer and kernel; hashes cover the actual staged bytes. Runtime/configuration are root-owned outside the VMM-writable socket directory.
- The shared `vm.py` starts a transient systemd service with CPU/RAM/swap/task/deadline limits, chroot, private PID/mount namespaces, reduced UID/GID, no-new-privileges and default VMM seccomp. Host observations check these controls independently of guest reports.
- Guest setup seals admitted Git/head input read-only and drops privileges. Both flows reuse the prepared head snapshot. Native write/chmod/runtime-write denials are tested.
- External TAP/nftables and standard Squid CONNECT policy deny direct guest networking. Public acquisition allows GitHub endpoints only; the supervisor kills existing source tunnels before starting inference-only policy. Inference policy selects only `api.openai.com`, or `api.anthropic.com` and `platform.claude.com`. The no-key deployment check tests the first route; native live authentication/tool behavior remains unqualified.
- Private bounded SSH pipes and vsock frames carry settings, runtime credentials, input and results. Only the selected credential is read after admission/probes; the host home/keychain is never copied. Reports use fixed names/byte limits and private local files. No target-controlled archive is blindly extracted on the workstation.
- Clean root-owned images are keyed by source/builder/copied OS identity and the optional native binary digest, hash-checked on reuse and contain no target inputs or keys. Shutdown joins/kills owned work before scratch removal. Independent deadlines bound supervisor loss; explicit dedicated-worker recovery clears abandoned state without resuming the review.

A guest compromise can falsify results/accounting and expose a credential available inside it. VM destruction does not revoke that credential. Custom guest-wide syscall filtering and a custom credential broker are deferred. Read-only model tools and guest probe reports are not substitutes for host-enforced containment.

## Acquisition and input

`sandbox/acquire.py` runs API/Git parsing, pinned fetch, tree admission and export unprivileged inside the review VM. Public GitHub URLs are supported by normal review dispatch and no-key checks; private source authentication is not implemented. Limits include bounded metadata, shallow history, retained Git bytes/file count, admitted trees, scratch and deadlines. Missing commits or merge-base fail instead of silently widening history or changing pins. See [exact limits](sandbox-check.md#public-pr-acquisition-check).

The optional capsule path transfers bounded file-only Git objects from a chosen existing clone; it invokes no target Git on the workstation and copies no target configuration/hooks. It can include other history from that object store. Guest admission reconstructs clean Git state, admits the pinned trees and exports the head. This remains separate from the implemented direct-host acquisition feature, which reuses the existing host acquisition implementation.

## Lifecycle

1. Read trusted operator settings and choose provider, strategy and execution location.
2. For VM execution, prepare clean artifacts/images and start the jailed disposable guest with external policy already installed.
3. Acquire/admit/pin input inside the guest, export the snapshot, revoke source access and seal read-only mounts before model work.
4. Supply selected-adapter authentication privately at runtime; never bake credentials into images, target files, arguments or logs.
5. Run the shared review algorithm with bounded Read/Grep/Glob and deterministic validators/accounting.
6. Transfer bounded report/summary/stats frames privately and validate them, without publishing a local success yet.
7. Stop/join the VM and proxy, remove network/scratch state and confirm worker shutdown. Failures do not select host execution automatically.
8. Save the private local files and print the summary. GitHub posting requires explicit `--post`.

Normal CLI dispatch, selected-adapter private credentials and report transfer are wired. Authenticated provider calls remain untested. Explicit direct-host execution is implemented with the provider factory and same Engine. No-key checks exercise the listed components without claiming review-quality evidence.

## Development and current work

Use [the supported deployment command](sandbox-check.md), [VM checklist](sandbox-todo.md) and [development guide](development.md). Checks run an offline simulated-provider workload and a sequential connected probe workload; these are tests, not additional VM layers or per-persona isolation.

The standalone raw-Firecracker experiment was moved outside the project. Its old console-based launcher is not an alternative reviewer entry point. `sandbox/runtime.py` now contains only the bounded subprocess, boot configuration and result-validation utilities used by the supported components. See [the code map](architecture.md#code-map).
