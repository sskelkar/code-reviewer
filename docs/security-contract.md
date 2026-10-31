# Reviewer execution and security contract

One reviewer supports runtime-selected providers and an independent operator choice of execution location.

## Independent runtime choices

| Choice | Meaning |
|---|---|
| Provider | Claude or OpenAI adapter; same review algorithms and output contract |
| Authentication | Explicit API-key or subscription access; unsupported combinations refuse without billing fallback |
| Review strategy | Single-agent or agent-personas; independent of provider/execution location |
| Execution | VM by default; explicit direct-host selection for trusted use without VM startup overhead |

The direct flag is `--no-sandbox`; trusted operator config can also set `sandbox = false`. Explicit host dispatch is implemented. Host acquisition is supported. No repository classification chooses these settings. Only trusted operator configuration/arguments may choose execution location, provider credentials or executables. A VM failure refuses that request; it never silently switches to host execution.

## Shared requirements

Both execution paths pin the observed base/head/merge-base for one run, use a separate head snapshot and base-commit criteria, expose only Read/Grep/Glob to code-reading model roles, validate replies/evidence, bound inputs and calls, account usage, and write reports privately. No target hooks, dependencies, builds or scripts run. Publishing remains an explicit operator action.

Keep provider credentials out of target files, images, command arguments, console logs and reports. Adapter-specific authentication/protocol details belong behind the runner interface; they do not create separate reviewer products. Unsupported combinations fail clearly instead of switching provider/model or discovering alternate credentials. Cost estimates are not hard provider spending limits.

## VM execution

One disposable VM contains acquisition, Python orchestration and all model sessions/personas. On macOS, dedicated mountless Lima supplies Linux/KVM for Firecracker. The external launcher owns pinned artifacts, jailer/resource/deadline controls, phase-scoped networking, bounded private transfers and cleanup/recovery. Source access is removed before inference. Guest code runs unprivileged against read-only admitted input, with no shared home/keychain or host mounts.

A compromised guest can corrupt its findings/accounting and expose a credential available to it. Keep credential lifetime/exposure explicit; no custom credential broker or guest-wide syscall allow-list is mandated by this plan. Preserve tested isolation controls rather than describing a VM as absolute protection.

## Optional tracing

Optional Langfuse tracing returns only a closed, bounded metadata schema through the existing VM stats artifact. The host validates one connected guest subtree, rejects span IDs colliding with host observations, and checks timing, model names and fields before exporting to the fixed local endpoint. Local metadata is persisted atomically before export starts. Source, prompts, findings text, raw errors and authentication material are excluded. Langfuse keys remain on the host and are not passed to native model processes. No new guest egress is granted. SDK export runs in a deadline-bounded subprocess without ambient exporters/proxies or redirects; failures do not change review outcomes or cleanup. See [tracing](tracing.md) for storage and limitations.

## Direct-host execution

The operator deliberately selects the host boundary. Acquisition uses the existing host/local-clone path and the same review core. It avoids VM setup, VM transport and external guest networking. It retains read-only model tools, separate checkout, validation, budgets and cleanup, but does not claim the VM's kernel/filesystem/network isolation. VM readiness checks must not become requirements for this explicit direct path.

## Supported limits and later work

The normal VM CLI is currently Mac/Lima plus public github.com acquisition. `--local-path`, `--keep-checkout`, `--dry-run` and the operator-file `criteria_manifest` override are host-only; VM reviews discover project criteria from pinned base-commit files. VM deadlines are 60–1800 seconds. Unsupported choices fail before target/credential activity.

Supported interfaces and parked code are listed in [usage](usage.md); outstanding qualification and extensions belong in [the roadmap](proposal.md#checklist).

Binary contents are omitted from model checkouts and diff hunks in both execution locations. Prefix classification is a heuristic, not binary validation or malware scanning. Omission metadata and text/acquisition limits are documented in [usage](usage.md#binary-omissions); binary Git objects still cross the acquisition boundary and remain subject to its bounds.

Authentication and credential lifetime are specified in [the LLM guide](llm.md).
