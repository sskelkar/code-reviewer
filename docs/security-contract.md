# Reviewer execution and security contract

Current direction, confirmed 2026-10-28: one reviewer, runtime-selected provider, and an independent choice of execution location. This replaces earlier requirements for mandatory VM execution and different repository/provider “pilot” tiers.

## Independent runtime choices

| Choice | Meaning |
|---|---|
| Provider | Claude or OpenAI adapter; same review algorithms and output contract |
| Review strategy | Single-agent or agent-personas; independent of provider/execution location |
| Execution | VM by default; explicit direct-host selection for trusted use without VM startup overhead |

The proposed direct flag is `--no-sandbox`. It is **not implemented yet**. Host acquisition is an intended feature, not an obsolete fallback. No repository classification chooses these settings. Only trusted operator configuration/arguments may choose execution location, provider credentials or executables. A VM failure refuses that request; it never silently switches to host execution.

## Shared requirements

Both execution paths pin the observed base/head/merge-base for one run, use a separate head snapshot and base-commit criteria, expose only Read/Grep/Glob to code-reading model roles, validate replies/evidence, bound inputs and calls, account usage, and write reports privately. No target hooks, dependencies, builds or scripts run. Publishing remains an explicit operator action.

Keep provider credentials out of target files, images, command arguments, console logs and reports. Adapter-specific authentication/protocol details belong behind the runner interface; they do not create separate reviewer products. Unsupported combinations fail clearly instead of switching provider/model or discovering alternate credentials. Cost estimates are not hard provider spending limits.

## VM execution

One disposable VM contains acquisition, Python orchestration and all model sessions/personas. On macOS, dedicated mountless Lima supplies Linux/KVM for Firecracker. The external launcher owns pinned artifacts, jailer/resource/deadline controls, phase-scoped networking, bounded private transfers and cleanup/recovery. Source access is removed before inference. Guest code runs unprivileged against read-only admitted input, with no shared home/keychain or host mounts.

A compromised guest can corrupt its findings/accounting and expose a credential available to it. Keep credential lifetime/exposure explicit; no custom credential broker or guest-wide syscall allow-list is mandated by this plan. Preserve tested isolation controls rather than describing a VM as absolute protection.

## Direct-host execution

The operator deliberately selects the host boundary. Acquisition uses the existing host/local-clone path and the same review core. It avoids VM setup, VM transport and external guest networking. It retains read-only model tools, separate checkout, validation, budgets and cleanup, but does not claim the VM's kernel/filesystem/network isolation. VM readiness checks must not become requirements for this explicit direct path.

## Current implementation and next work

Public CLI/REPL currently offer preflight/refusal; authenticated VM and explicit direct-host dispatch remain unwired. Both adapters and internal review algorithms exist. Keep those facts visible; do not make status-row edits stand in for implementation.

The supported VM checks cover jailed lifecycle, host/native containment, clean image reuse, bounded public PR acquisition, source-access revocation, private results and recovery without credentials/inference. Next wire an execution selector and provider factory into the common workflow, retain host acquisition for the direct path, and complete VM endpoint/authentication/tool integration for both adapters. Test explicit selection and absence of silent fallbacks.

See [architecture](architecture.md), [VM design](sandbox-architecture.md) and [checklist](sandbox-todo.md). The standalone Firecracker playground is external to this project.
