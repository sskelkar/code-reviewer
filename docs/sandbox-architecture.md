# Sandbox architecture: one VM per review

Current decision: **one disposable VM contains the entire review job**, including Python orchestration and every model/tool call. The implemented connected pilot uses OpenAI; Claude production integration remains pending. Agreed 2026-10-25; the simpler connected-pilot scope below was confirmed 2026-10-26. This replaces the earlier proposal for a controller container, a supervisor service and a microVM per model invocation. [Security roadmap](security-roadmap.md) tracks the remaining controls; [architecture](architecture.md) describes the existing reviewer internals.

## Implementation status

| Part | Status |
|---|---|
| Local M4/macOS -> Lima Linux -> nested Firecracker boot | Verified with a tiny synthetic Linux guest |
| Both existing Python flows with a fake headless Claude executable | Implemented as an offline smoke workload |
| Bounded offline Firecracker launcher and trusted-source bundle | Implemented prototype; Linux/KVM only |
| Whole-harness smoke inside a Firecracker guest | Passed: 2 single-agent passes / 4 persona slots; 3 / 8 fake calls |
| Real Claude binary: offline startup / unauthenticated headless refusal | Passed for pinned native ARM64 2.1.301; [runtime/auth record](sandbox-runtime-auth.md) |
| OpenAI backend for controlled sandbox testing | Implemented and offline guest-tested; [provider/tool boundaries](sandbox-openai.md) |
| Connected OpenAI pilot launcher/input/network/key/export path | Implemented; no-key real boundary/cleanup probes pass; [runbook](sandbox-connected.md) |
| Actual authenticated OpenAI inference and personal-repo review | Not run: no key/model/repo was supplied |
| Bounded local pinned input, non-root worker and resource limits | Implemented for the controlled pilot; no GitHub fetch |
| Read-only snapshot against native compromise, syscall policy and production jailer | Still pending production qualification |
| Bounded report export | Implemented with fixed artifact names, byte limits and private files; publishing stays disabled |

The normal `reviewer` CLI/REPL still runs as before. It does not silently use this prototype and must not be described as sandboxed. The implemented prototype has three offline workloads: fake-Claude review, real-Claude unauthenticated startup, and simulated-OpenAI review. Those offline workloads use synthetic inputs. The separate connected pilot accepts a bounded capsule from a user-selected non-sensitive personal repository; only harmless fixtures have been used in deployment checks so far. Implementation checks used no paid calls, personal target repositories or host credential mounts.

## Boundaries and process placement

This diagram shows the **implemented connected OpenAI pilot**. Production controls that remain pending are listed separately below.

```mermaid
flowchart TB
    subgraph MAC["Mac workstation"]
        INPUT["File-only capsule: pinned base/head and Git objects"]
        subgraph LINUX["Dedicated mountless Lima Linux VM: KVM worker"]
            HOST["Trusted Python supervisor: settings, admission gate, cleanup"]
            VMM["Unprivileged Firecracker + independent deadline"]
            PROXY["Standard Squid CONNECT proxy<br/>External nftables TAP and proxy-UID policy"]
            subgraph JOB["Disposable review VM: separate Linux kernel"]
                PY["Unprivileged Python: selected review flow + OpenAI runner"]
                TREE["Clean Git input and bounded head checkout"]
                TOOLS["Read / literal Grep / Glob"]
                SCRATCH["512 MiB scratch: runs and temporary files"]
                PY --> TOOLS --> TREE
                PY --> SCRATCH
            end
            HOST --> VMM --> JOB
            HOST -->|"Private vsock: inputs, then key after probes"| PY
            PY -->|"Bounded fixed report artifacts over vsock"| HOST
            PY -->|"Provider TLS through CONNECT"| PROXY
            KEY["Operator enters disposable key locally"] --> HOST
        end
        INPUT -->|"Explicit file transfer; no shared mounts"| HOST
        HOST -->|"Private result files"| OUTPUT["Local inspection; no automatic publication"]
    end
    PROXY -->|"Only api.openai.com:443 CONNECT authority"| PROVIDER["OpenAI"]
```

On native Linux/KVM the outer Lima layer disappears. The host supervisor is separate from the Python reviewer inside the job: it only enforces lifecycle and transfers bounded inputs/results. No host directories or credential sockets are shared into the guest. The provider connection keeps end-to-end TLS; the standard proxy does not see the key or inspect encrypted HTTP content. See the [runbook](sandbox-connected.md) for network-policy limitations, including provider/CDN behavior and the disposable-key tradeoff.

The connected guest worker is non-root; exported files have read-only permissions but the owning guest user could change those permissions after native compromise. Immutable runtime/snapshot qualification, a production jailer and guest execution/syscall allow-listing remain future gates. Claude authentication/native tools are a separate qualification. None is represented as implemented by this diagram.

The three offline workloads have **no network interface or attached disks** and run their synthetic/startup checks as guest root. Their writable in-memory runtime and console summary interface are distinct from the connected pilot's unprivileged worker and private framed channel.

## Why Python is inside the job

Dockerizing an application and separating privileges are compatible choices. A container for Python is optional packaging, not another required security service. For this version, put the trusted reviewer code and its runtime into the guest image and run it there directly.

The host launcher has a small responsibility: select operator-approved artifacts/policy, start the VM, enforce an external deadline/output budget, collect a bounded result and stop/reap the VM. It does not run the target's tests, builds, hooks or scripts, nor receive arbitrary host commands from the guest. It must not become a general remote-execution API. No privileged controller container or host Docker socket is required.

The guest's Python harness retains normal deterministic validation and role separation during honest execution. A compromised guest could still corrupt its own report, other persona state or accounting; sharing a VM deliberately gives up strong isolation between invocations. Treat the whole guest output as untrusted. This design protects the host boundary, not the semantic truth or tamper resistance of a report produced by a compromised guest.

## Job lifecycle for real reviews (planned)

1. **Select trusted policy.** Read operator configuration outside the reviewed repository. Target `review.toml`, rules and PR text cannot choose executables, loosen isolation or authorize publication. Reject unsupported runtime/policy combinations; no host fallback.
2. **Start an empty disposable guest.** Use pinned images, explicit vCPU/RAM/disk/output/time limits, a private job directory and a production jailer. Keep long-lived host credentials, previous runs and other jobs outside it.
3. **Admit/fetch the requested PR.** Use approved hosts/protocols and bounded Git operations inside the isolated job. Pin base/head; export the review tree with path/type/size checks. Public fetches need no credential; private fetching requires a separately scoped supported auth design. If a fetch credential is provided, externally revoke/expire its authority before model execution rather than assuming deleting an environment variable erases it.
4. **Freeze inputs and make the snapshot read-only.** Preserve current criteria discovery from base and the same head snapshot for all passes. The guest runtime/policy stays separate from repository-controlled files. Allow only the required process/helper execution; Read/Grep/Glob remain the only code tools.
5. **Run the existing review flow.** Python starts independent headless sessions; persona calls overlap within rounds and consolidate serially as today. Model calls use only explicitly approved inference/auth routes. Network restrictions are enforced outside the guest, so a guest cannot broaden them.
6. **Export a bounded result.** Initially return a local report for inspection; publishing stays disabled inside the VM. Export only allow-listed regular result files or a framed result stream, with path/type/count/size validation. Never blindly extract a guest-provided archive onto the host. A result digest identifies bytes, not the truth of their contents.
7. **Destroy the guest on every outcome.** Reap owned processes and clear scratch on success, error, timeout or Ctrl-C. Mark interrupted/unknown usage honestly. A later external lease/watchdog must cover launcher crashes; a Python `finally` block alone cannot guarantee cleanup after its own SIGKILL or host failure.

No persistent warm snapshot may contain private code, credentials, prior prompts or writable state from a completed job. Caching clean runtime artifacts is separate from reusing a live review guest.

## Offline prototype contract

The new modules live under `src/code_reviewer/sandbox/`, separate from both review flows:

| File | Responsibility |
|---|---|
| `bundle.py` | Archive only trusted reviewer Python/Markdown/JSON assets; reject links/oversize inputs; stable tar metadata and a per-file SHA-256 manifest |
| `smoke.py` | Manufacture a tiny Git change inside the guest; run both real flows; check pass counts, consolidated finding count, fake calls and report creation |
| `openai_smoke.py` | Run both flows through the actual OpenAI adapter with simulated Responses replies and local Read dispatch, without a real key |
| `runtime_probe.py` | Start pinned real Claude in a fresh home with no credentials; check required flags, auth status and unauthenticated headless refusal |
| `fake_claude.py` | Synthetic executable honoring the existing headless JSON interface for Reviewer/Comparator; never contacts a provider |
| `launch.py` | Linux/KVM preflight, operator-supplied artifact digest checks, fixed no-network boot configuration, deadline, bounded output and process-group cleanup |
| `sandbox/firecracker/build-offline-guest.sh` | Build the test-only ARM64 initramfs inside the disposable Ubuntu build VM, using its installed Python/Git/runtime libraries and a validated source bundle |

The launcher has no PR, credential, publishing or generic command option. Its boot config is fixed at 2 vCPUs, with no drives or network interfaces. The default smoke workload uses 1024 MiB RAM; `--workload runtime` selects a 4096 MiB guest and the distinct runtime-result validator. `--workload openai-offline` selects the 1024 MiB simulated-OpenAI flow test and its own offline result validator. The builder optionally accepts a verified operator-supplied Claude binary/digest to produce that runtime-probe image; this is never selected by target repository configuration. It requires explicit SHA-256 values for the Firecracker binary, kernel and initrd. Output is capped at 1 MiB across stdout/stderr, and the default deadline is 180 seconds (configurable only within 1–600 seconds). There is no API socket and no unsandboxed fallback.

`REVIEWER_SMOKE_RESULT=` frames a single JSON result. Host validation requires both flow results and expected completion, finding and fake-call counts. This proves the test workload completed, not that malicious guest output is trustworthy. The workload exercises the actual subprocess transport, Git export, finding grounding, duplicate consolidation, per-mode reports and parallel persona calls. It does not use the parked eval harness.

The first image is **repeatable scaffolding, not a reproducible production image**: runtime libraries come from the installed Ubuntu build VM, versions are recorded, and artifacts are content-hashed. The builder takes only the trusted source tar made by `bundle.py`; extraction rejects traversal, links, duplicate names and oversized entries and checks manifest coverage/digests. It must never be used as a target-repository importer.

The prototype guest runs its synthetic workload as guest root, with a writable in-memory runtime. Firecracker currently runs unprivileged with a VM-local KVM ACL and default seccomp; a production jailer is still pending. These limitations are acceptable only for the explicit offline smoke test, not untrusted real reviews.

## Running the offline check

See [the runbook](../sandbox/README.md) for bundle/build/launch commands. The host offline unit suite does not need Lima, KVM, Firecracker or a model login. Running the real boot check is an explicit infrastructure operation outside the normal test suite.

The ordinary `reviewer` installation is unchanged; do not add `--sandbox` examples until that user interface and real-review path exist. Keep generated images, archives, logs and VM disks outside the repository. Never mount the user's home directory just to supply packages or authentication.

## Agreed connected pilot (2026-10-26)

**Implementation update:** the controlled pilot path below is now implemented. Actual key/inference/personal-repo validation remains pending. Use the [connected runbook](sandbox-connected.md).

The next live review uses the operator's **non-sensitive personal repository and an OpenAI test key**. There is no requirement to write a new example project. The existing synthetic fixtures remain offline regressions and containment probes; they are not the required live review input. The operator will select the repository, a pinned base/head change, model, token rates and run budget before a live run. No production repository, saved production run or identifiers belong in this project. Production reviews remain Claude-only.

Keep this pilot small:

- One disposable Firecracker review VM contains the whole Python flow and model/tool loop. The dedicated mountless outer Lima VM remains the Mac compatibility layer.
- A network interface is enabled only after **external default-deny rules** are installed and checked. During review, allow only the required OpenAI HTTPS destination; block other destinations and any direct/proxy, IPv6, DNS, private-network or metadata bypass. The guest cannot edit the enforcing policy. An API hostname hard-coded in Python is not this boundary.
- Supply a dedicated disposable test key privately **at runtime into the guest**. The key is not baked into an image/archive, put in command arguments or logs, or obtained through a shared home/keychain mount. Use a bounded per-job bootstrap channel to deliver it to worker memory/environment outside the reviewed tree; the implementation uses a one-shot host-initiated vsock channel, not an inference service. Reuse that single private connection for the bounded result, then close it at job completion; it exposes no general command interface.
- On completion, error or cancellation, terminate the guest and remove per-job network rules, channels and scratch. Revoke the test key after the test; VM destruction and deleting an environment variable do not revoke it.
- **Defer the custom API relay, host-held credential broker, per-persona VMs and extra orchestration services.** They are not prerequisites for this controlled pilot. If a standard network proxy is needed for destination enforcement, it is network plumbing, not a custom inference/credential relay.

Accepted tradeoff: **a compromised guest could steal and reuse the test key**, consume its authority outside the Python dollar ledger, and alter its report. This is a controlled feasibility test with a disposable credential, not the eventual production security target. Do not describe provider spending alerts or our estimated budget as a hard externally enforced dollar cap.

### Connected implementation sequence

1. **Correct the recorded prototype defects — complete.** Resolve the builder failure-status and scan-accounting findings below with regression tests. Keep the ordinary Claude command and both review flows stable.
2. **Wire a bounded personal-repository input and report path — implemented.** Admit pinned base/head metadata and bounded Git objects (the selected clone's store can include other history) without executing target code or inheriting target Git/config authority. Use bounded, validated input transfer; no host checkout mount or target scripts/builds/hooks. For the first pilot, admit prepared inputs before review so inference-stage egress need not include GitHub or a GitHub credential. Export only bounded expected regular report files. Never use the trusted-runtime bundle builder to import the target repository.
3. **Add external network policy and private runtime key delivery — implemented.** Install and verify policy before releasing the key or starting model calls; refuse missing/unsupported enforcement. A hostname policy must account for DNS changes/shared addresses rather than relying on a one-time IP lookup. Reuse existing network tooling; if a mandatory proxy is chosen, add explicit trusted proxy support because the current OpenAI transport ignores proxy environment variables. Keep verified HTTPS and reject redirects; never solve connectivity by opening general egress.
4. **Verify the boundary with harmless probes — listed probes pass.** Test denied model-tool reads outside the checkout, absence of write/execute tools, attempts to access the host outside admitted inputs, network bypasses, unsafe input/output paths, key absence from images/logs/reports, timeouts and cleanup. Native guest process/privilege/syscall probes belong to the stronger enforcement gate below. Keep generated canaries/fixtures for these probes; no new review sample project or review-quality eval harness is needed. Identify which controls the pilot actually enforces; do not claim deferred jailer/syscall restrictions have passed.
5. **Run one small controlled personal-repository review.** After inputs, model/rates and limits are supplied, start with limited passes/concurrency, inspect the report and accounting, and clean up/revoke the test key. A valid review can contain zero findings. The offline fixture's fixed finding/call counts are not live result requirements.
6. **Return to production hardening and Claude qualification.** Complete jailer, non-root/read-only, execution/syscall and resource enforcement plus adversarial tests. Qualify Claude authentication/native tool restrictions separately before any authorized Claude-only production pilot. An OpenAI run does not qualify Claude's integrations or credentials.

The user subsequently authorized implementation of this pilot. `python -m code_reviewer.sandbox.connected` now exposes it on the dedicated Linux worker; the regular CLI still has no `--sandbox` flag. No target repository, real key or paid inference has been used during implementation checks.

## Review before connected implementation (2026-10-26)

The review identified two prototype defects; both are **now fixed with regression tests** in the subsequent implementation. The historical evidence below explains the changes. The controlled connected path is implemented and boundary-tested, but actual model/account/personal-repository compatibility is not yet measured.

| Finding | Evidence and consequence | Required follow-up |
|---|---|---|
| Fixed P2: image creation could report success after archive failure | [Original builder pipeline](../sandbox/firecracker/build-offline-guest.sh) used `find → cpio → gzip` under POSIX `sh`; `set -e` sees the final command status. A fake `cpio` exiting 23 produced an empty compressed archive with overall exit 0. Stderr is discarded and the builder can hash/announce a partial image. | **Fixed:** `sandbox/image.py` checks each archive stage before publishing, with a failure regression. The guest success marker still prevents this from becoming a successful review. |
| Fixed P2: Grep scan accounting excluded bytes read from skipped files | [Original scan loop](../src/code_reviewer/backends/read_tools.py) counted bytes only after `_read` succeeds; binary/non-UTF-8 files are already read before being skipped. With a one-byte allowance, a 32-byte binary was read and returned empty results without a limit failure. The 16 MiB decoded-text cap does not bound total read IO across such files. | **Fixed:** raw read bytes, including skipped inputs, consume the scan allowance; binary/non-UTF-8 regressions verify refusal. Per-file and entry limits still bound the current workload more loosely. |

The previously absent connected input/network/bootstrap/export pieces now exist. `capsule.py` reads bounded objects without host Git execution; `git_input.py` reconstructs and bounds trees inside the guest. The client has an explicit trusted CONNECT route, with no ambient proxy inheritance. Guest UID/resource/scratch limits and signal/deadline cleanup are implemented. Remaining production gaps include a jailer, an immutable checkout against native compromise, a guest syscall allow-list, crash-state recovery and separate Claude qualification. The [connected runbook](sandbox-connected.md) describes exact controls and limits, including uninspected encrypted traffic and manual key revocation.

## Next implementation gates

| Gate | Required evidence |
|---|---|
| 1. Offline whole-job smoke — passed | Both modes complete inside the guest with fake Claude, no network/credentials and bounded host collection |
| 2. Controlled personal-repo OpenAI pilot — implemented, live test pending | Bounded input/export, external CONNECT policy, private key path and no-key denial/cleanup probes pass. Actual model/key/repository selection and review remain to be exercised |
| 3. Enforcement | Production jailer, read-only runtime/snapshot, unprivileged processes, externally enforced egress and resource/deadline controls pass synthetic attack probes |
| 4. Real PR pilot | Trusted configuration, bounded isolated fetching and safe artifact export work; host publishing remains manual and separate; staged private-data use is authorized |

Do not add per-persona VMs, an orchestration service, a credential gateway we do not need, a controller container, signed publication tokens or multi-tenant audit infrastructure as prerequisites. A custom gateway remains deferred unless a later production requirement justifies it; the pilot accepts a disposable key inside its guest. Multi-tenant hosting, cross-invocation integrity and automatic publication require new decisions and tests.

## Connected implementation map

- `sandbox/capsule.py`: bounded private pinned input; no host-side target Git/config/hook execution.
- `sandbox/git_input.py`: clean guest Git object reconstruction, tree admission and bounded checkout export.
- `sandbox/network.py`: scoped TAP/nftables and foreground unprivileged standard Squid, with no broad NAT.
- `sandbox/wire.py`: bounded one-shot private frames; `sandbox/probes.py`: harmless file/network checks.
- `sandbox/guest.py`: unprivileged resource-limited flow execution and bounded report collection.
- `sandbox/connected.py`: trusted root Linux supervisor, private runtime key prompt/FD, deadline/process/network cleanup and validated host export.
- `sandbox/image.py`: checked archive stages; the image builder's `connected` option includes public CA material and the guest entry point, never a key or target.
