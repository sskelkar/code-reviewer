# VM supervisor and private transport

Use [`reviewer sandbox check`](sandbox-check.md) for the supported Mac build/check/shutdown lifecycle. It needs no model key or local clone. Optional `--pr` checks public acquisition inside the review VM without inference. This document explains the components, not a separate reviewer product or a repository-specific tier.

## What is implemented

The shared VM lifecycle uses a matching jailer, reduced privileges, finite resources/deadlines and a read-only runtime bind mount. Guest code runs non-root with no-new-privileges, read-only admitted code and 512 MiB scratch. External TAP/nftables/standard CONNECT policy restricts traffic by acquisition/inference phase. Existing source tunnels close when the phase changes. Fixed bounded report artifacts return privately, and the supervisor cleans up processes/network/scratch.

Public URL acquisition parses/fetches only inside the review VM. Optional capsule transfer admits pinned Git objects without host-side Git/config/hooks. These feed the same review core; see [acquisition limits](sandbox-check.md#public-pr-acquisition-check).

The normal CLI now dispatches both providers through this workflow. Trusted review settings, selected credentials and results travel through private SSH pipes/vsock; credentials are released only after admission and boundary checks. Native CLI credentials use an explicit API-key or OAuth choice, without host login import or billing fallback. Bounded reports are published locally only after worker shutdown. Authenticated inference/native-tool qualification remains untested. See [VM configuration](usage.md#vm-review-configuration). The one-shot CLI also supports explicit direct-host review; the REPL remains parked; see the [execution contract](security-contract.md).

## What “trusted launcher” means

`sandbox/connected.py` is ordinary deterministic Python running in the dedicated Linux worker outside the review VM. On compatible Macs that worker is mountless Lima; on native Linux it is the KVM host. It selects trusted artifacts/settings, installs external network policy, launches the jailed VM, exchanges bounded private frames, validates exported files and cleans up. Scoped setup needs root; VMM/proxy processes have reduced privileges.

`guest.py` runs the actual review Python and selected adapter inside the VM. Repository rules and model output cannot ask the launcher to execute arbitrary host commands or change execution location. No custom inference relay, credential-injection service or publication daemon is involved.

## Prerequisites

The dedicated ARM64 Ubuntu worker needs nested KVM on compatible Macs, Python, Git, static BusyBox, cpio, systemd 255/cgroup v2, matching pinned jailer, iproute2/nftables and Squid 6 with its dedicated proxy user. The system Squid service must not occupy the job port. Full prerequisites/artifacts are in the [deployment runbook](sandbox-check.md#prerequisites). The command does not install tools, mount the workstation home or alter unrelated workers.

Only one connected job runs per worker. Existing TAP/firewall names, unsupported runtime, occupied resources or unconfirmed cleanup cause refusal. VM failure never selects direct-host mode automatically.

## Optional existing-clone capsule input

```bash
python -m code_reviewer.sandbox.capsule \
  --repo /path/to/clone --base FULL_BASE_HASH --head FULL_HEAD_HASH \
  --title 'Describe the change' --output /tmp/review-input.tar
```

The capsule contains the selected clone's bounded Git object store, potentially including other branches/history. Keep it private and outside this project. No host Git or target code executes. Symlink/hardlink objects, alternates, unsupported layouts and oversized inputs are refused. Each admitted tree has existing path/blob/count/byte limits; no submodule/LFS content is downloaded. Public URL acquisition avoids this manual input step; private URL authentication remains unsupported.

This importer is retained alongside the intended direct-host acquisition feature. A capsule enters the VM; direct-host acquisition/review runs on the machine by explicit operator choice.

## Network and credential limits

CONNECT authority and resolved-address checks do not inspect encrypted HTTP paths/content, enforce model spending or certify provider/CDN behavior. TLS remains end-to-end. The selected provider receives admitted prompt/code content. Host execution does not claim the VM's network isolation.

Credentials belong outside images, CLI arguments, target files and logs. A credential available in a compromised guest may be copied/reused; Python budgets are estimates and VM teardown does not revoke it. Authentication/lifetime/revocation handling must be explicit without preemptively building another credential service.

## Checks

The no-key deployment check validates host VMM confinement, native/file-tool denials, direct/forbidden network access, approved TLS, bounded transfer and cleanup. `--pr` additionally checks source acquisition and revocation, including an established tunnel. `--lifecycle` tests cancellation and supervisor loss. Unit tests simulate external operations and never boot a VM or call a model. See [evidence](sandbox-todo.md#evidence-2026-10-28).
