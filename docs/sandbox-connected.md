# Connected personal-repository sandbox

Implemented 2026-10-26 as the controlled OpenAI pilot in the [architecture](sandbox-architecture.md). The ordinary `reviewer` command stays Claude-backed and unsandboxed. This separate Linux-only launcher runs the whole selected review flow inside one Firecracker VM. Production repositories remain Claude-only and outside this pilot.

## What is implemented

- Bounded local input capsule containing pinned commits and Git objects, with no target Git config, hooks, remotes, worktree scripts or host-side Git invocation.
- One connected VM: 2 vCPUs, 4096 MiB RAM, 512 MiB private scratch, no attached disks or host filesystem mounts. Guest Python runs as UID/GID 65534 with core dumps disabled and finite CPU/file/process/descriptor limits. Scratch is `nosuid,nodev,noexec`.
- A scoped TAP and nftables rules outside the guest. Direct forwarding, guest DNS, IPv6 and host-service access are blocked; only the standard proxy port is reachable from the guest.
- A per-job **standard Squid CONNECT proxy**, not a custom inference/credential relay. It permits exactly the `api.openai.com:443` CONNECT authority, denies literal IPs/other domains/ports and private destinations, and disables access/cache logs and caching. A separate nftables output guard for the dedicated proxy UID permits only configured IPv4 DNS resolvers and public HTTPS connections; private/reserved destinations and IPv6 are refused at packet level as well. This worker must not run unrelated jobs under the same `proxy` account. DNS resolution happens outside the guest and is checked on use. The client explicitly uses this route and verifies provider TLS; no CA substitution or TLS decryption is performed.
- A private one-shot Firecracker vsock bootstrap. Inputs/settings enter first; the guest admits them and probes the boundary; the host checks policy again before requesting/releasing the disposable key. The key travels only in memory over this channel, never in an image or command argument. It is not put in the reviewed tree.
- Bounded allow-listed report export and cleanup on success, error, timeout, Ctrl-C, SIGTERM and SIGHUP. Independent GNU `timeout` wrappers bound the VMM/proxy if the Python launcher disappears. Residual TAP/firewall state after a hard kill refuses a new job rather than being silently overwritten.

The network policy controls CONNECT destinations and resolved network addresses; it does **not** inspect encrypted HTTP authority/content, enforce API URL paths/model spending, or certify provider/CDN behavior against domain fronting. The permitted provider can receive admitted code. A compromised guest can obtain/reuse its disposable key, corrupt results and bypass the Python accounting. Those controlled-pilot risks do not become a production security guarantee.

## Prerequisites

Use the dedicated mountless Ubuntu 24.04 ARM64 Linux VM from the [offline runbook](../sandbox/README.md), with at least 6 GiB RAM, nested KVM and no personal credentials. Native Linux/KVM removes the outer Lima layer. This image builder currently supports ARM64 only.

Linux host tools: `ip`/iproute2, `nft`/nftables, **Squid 6** (`proxy` account and `/usr/sbin/squid`), GNU coreutils `timeout`, Python, Git, static BusyBox and GNU cpio. These are explicit OS/infrastructure prerequisites, not new Python dependencies. The launcher does not install them, modify the system Squid service, enable general IP forwarding or clear unrelated firewall rules. Tests used Squid 6.14. Other Squid major versions need separate qualification.

Run the connected launcher with root only on this dedicated Linux worker, for scoped TAP/firewall setup and process privilege dropping. It drops the VMM to the invoking sudo user (or nobody), with KVM group access, and Squid to its own `proxy` account. Missing KVM permissions, occupied policy names or an overlapping `192.0.2.0/30` route refuse startup. Only one connected job per worker runs at a time.

Keep Firecracker/kernel/initrd artifacts operator-selected and content-pinned. The current CI kernel and copied Ubuntu runtime remain prototype artifacts, not independently qualified production images.

## Prepare the selected personal repository

On the trusted workstation, using a **normal complete local clone** with a `.git` directory, provide full pinned base/head commit hashes:

```bash
python -m code_reviewer.sandbox.capsule \
  --repo /path/to/personal-clone \
  --base FULL_BASE_COMMIT_HASH --head FULL_HEAD_COMMIT_HASH \
  --title 'Describe this change' --description 'Optional intent and expected behavior' \
  --output /tmp/personal-review-input.tar
```

This command reads files only and never runs Git or target code. The capsule is private (mode 0600); transfer it explicitly to the Linux worker without mounting the clone or home. **It contains the chosen clone's Git object store, potentially including other branches/history**, not just changed files. Select only a non-sensitive personal repository and keep the capsule/results outside this project and out of public storage. Model file tools see only the exported head checkout; the admitted base is also used for diff/criteria discovery.

Limits: 128 MiB capsule, 32 MiB per object file and 10,000 object entries. Symlink/hardlink objects, alternate stores, worktree `.git` files and unsupported object layouts are refused. Git parsing happens only in the guest using a new bare repository and clean config/templates. Both selected commits and their merge-base must exist locally. Each admitted tree is limited to 5,000 entries, 2 MiB per blob and 32 MiB aggregate blobs; checkout export checks paths/types and the same byte/count limits. There is no GitHub fetch, submodule/LFS download or PR URL interface in this pilot. The first live review can contain zero findings.

## Build the connected runtime

Bundle the trusted harness as in the offline runbook, explicitly copy that source tar and the builder into the disposable Linux worker, then:

```bash
sh /tmp/build-offline-guest.sh /tmp/reviewer-source.tar /tmp/reviewer-connected-guest connected
```

This includes the public CA bundle and connected guest entry point, **no target repository or key**. Record the artifact digests. Trusted image generation now checks every `find`/`cpio`/compression stage and publishes only after success; the old pipeline failure defect is fixed.

The builder's `root/opt/reviewer/src` supplies the trusted launcher source copy on Linux. Do not run that copy from a target-controlled checkout or load its `review.toml`. All connected settings come from explicit operator arguments; target rules remain review data.

## Check admission and network without a key

On the Linux worker:

```bash
sudo env PYTHONPATH=/tmp/reviewer-connected-guest/root/opt/reviewer/src \
python3 -m code_reviewer.sandbox.connected \
  --firecracker /path/to/firecracker --firecracker-sha256 EXPECTED_BINARY_SHA256 \
  --kernel /path/to/vmlinux --kernel-sha256 EXPECTED_KERNEL_SHA256 \
  --initrd /tmp/reviewer-connected-guest/initrd.cpio.gz --initrd-sha256 EXPECTED_INITRD_SHA256 \
  --input /tmp/personal-review-input.tar --output /tmp/personal-boundary-result \
  --model synthetic-probe --prices 1 0.5 2 --probe-only
```

The probe's dummy model/rates are not used for inference. It accepts no key, makes no HTTP model request, and exports only `boundary-report.json`. It tests local file-tool refusal and direct public/private/metadata/DNS/IPv6/host-port/other-proxy-destination denial. A successful approved-provider check opens verified TLS to OpenAI through the proxy, then closes it; it does not authenticate, retrieve model data or send a prompt.

The output directory must be new. Missing/failed probes or host policy prevent key release and prevent a review from starting. This is repeatable evidence for the listed probes, not an attestation that a malicious guest report is truthful or that every possible escape is prevented.

## Run the controlled review

Use the same command with a **new result directory**, remove `--probe-only`, and choose an accessible actual OpenAI model plus verified USD-per-million input/cached-input/output token rates. Start with `--passes 1 --parallel 1`; use `--mode agent-personas` to select the persona flow. Passes are per persona there. No implicit Haiku-to-OpenAI mapping exists.

The launcher prompts invisibly for the disposable API key only after admission, policy and guest probes pass. Run it interactively in the dedicated Linux terminal; noninteractive use without `--key-fd` is refused so `getpass` cannot fall back to an echoing prompt. For automation, `--key-fd N` reads a bounded key from an already-open private descriptor; never put the key itself in an argument, source file, shell history or chat. No environment/keychain discovery or host-home mount is used. Probe-only mode rejects `--key-fd`.

Defaults: dollar estimate cap $3, per-call estimate allowance $0.50, one pass, one persona worker, whole-job deadline 900 seconds. Flags: `--budget`, `--call-budget`, `--passes` (1–10), `--parallel` (1–4), `--mode` and `--timeout` (60–1800 seconds). These local token-rate estimates are not provider-enforced spending limits. HTTP inactivity limits and unknown-cost handling remain in the [OpenAI adapter](sandbox-openai.md); a killed request may still be billed.

On success, the host validates/exports only `report.md`, `summary.md` and `stats.json` (each at most 4 MiB; aggregate 8 MiB). Files are UTF-8, JSON artifacts parse, names are fixed, writes never overwrite old results, sudo-launched results are privately owned by the invoking operator, and literal copies of the known test key are refused. This is not comprehensive secret detection or report-truth verification. Reports contain target code/metadata and untrusted Markdown: keep them private and do not auto-execute, auto-publish or render active remote content. Full prompts/raw run directories stay in the disposable guest and are discarded.

Partial review, budget exhaustion and the flows' `failures` state export their diagnostic reports but return nonzero. Infrastructure/probe/deadline failure returns no successful report and no host fallback. It may leave usage unknown if a key/request had already been released.

**Revoke the disposable key after the test.** Cleanup removes VM/proxy, TAP, the job's nft table, sockets and scratch on handled outcomes; it cannot revoke the provider credential. Hard kills/host failure can leave restrictive stale infrastructure until operator inspection; automatic production recovery/jailer/audit enforcement is a separate gate.

## Validation and remaining scope

Offline unit tests cover malformed capsules/frames, target config/hook exclusion, clean Git reconstruction, export paths, key-release ordering, literal key rejection, standard proxy policy, archive failures, binary scan IO, and both flows with simulated API replies. They never alter host networking or call a model.

Real deployment checks use the dedicated disposable Linux worker and harmless fixtures. No personal repository, real key or paid call is used by those checks. See the [handover](HANDOVER.md) for actual outcomes. Guest native execution/syscall allow-listing, a production jailer, an immutable/read-only checkout against native compromise, crash recovery and Claude-specific authentication/native tool qualification remain pending. No production review or OpenAI finding-quality claim is implied.

Infrastructure sources: [Firecracker TAP networking](https://github.com/firecracker-microvm/firecracker/blob/main/docs/network-setup.md), [vsock](https://github.com/firecracker-microvm/firecracker/blob/main/docs/vsock.md), [Squid access control](https://www.squid-cache.org/Doc/config/http_access/) and [ACL matching](https://www.squid-cache.org/Doc/config/acl/). We intentionally do not use the upstream quick-start's broad NAT/forwarding rules.
