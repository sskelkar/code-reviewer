# Offline Firecracker prototype

This is the first implementation slice of the [one-VM-per-review architecture](../docs/sandbox-architecture.md). It supports a synthetic fake-Claude smoke and a separate **real-Claude startup probe without credentials or network**, and an **offline OpenAI adapter smoke**. Normal `reviewer` commands are unchanged; there is no production sandbox mode yet.

The whole Python review harness and its fresh headless subprocesses run inside one Firecracker guest. Both flows exercise grounded findings and consolidation, including a four-persona parallel round. The first smoke does not fetch GitHub, authenticate to Claude, contact a provider, publish anything or accept a target repository.

## Prerequisites

A disposable Ubuntu 24.04 ARM64 Linux build/test host with Python 3.12, Git, static BusyBox, cpio, gzip and working KVM. On a supported Mac, use a **dedicated mountless Lima VZ VM with nested virtualization**, not a default profile sharing the home directory. The earlier feasibility check established this on M4/macOS 15.8.1. Existing infrastructure is not modified automatically.

Use an approved Firecracker binary and compatible ARM64 kernel, and record their SHA-256 digests from trusted provenance. The successful feasibility artifacts were Firecracker 1.17.0 and Linux 6.1.188; the guest kernel came from Firecracker's official CI bucket and is not a production-qualified image. No downloads are built into these scripts.

## Bundle on the trusted workstation

From this repository, with an activated development environment:

```bash
python -m code_reviewer.sandbox.bundle --output /tmp/reviewer-source.tar
```

The output must not exist. The bundle contains only `src/code_reviewer` Python/Markdown/JSON files and a digest manifest; it excludes local configuration, runs, tests, target repositories and environment files. Transfer it and `sandbox/firecracker/build-offline-guest.sh` into the disposable build VM using an explicit file copy, with no host directory mounts.

## Build inside the disposable Linux VM

```bash
sh /tmp/build-offline-guest.sh /tmp/reviewer-source.tar /tmp/reviewer-offline-guest
```

The output directory must be new. This builds `initrd.cpio.gz`, records runtime versions and writes SHA-256 values. It uses the build VM's installed runtime libraries; this is not a bit-for-bit reproducible production image. No credentials or real repositories belong in the build VM or image.

## Launch on Linux/KVM

The trusted launcher modules must be installed or available through `PYTHONPATH` on the Linux test host. In the prototype, the builder's `root/opt/reviewer/src` also provides that trusted source copy. Supply the actual artifact paths and independently recorded expected digests:

```bash
PYTHONPATH=/tmp/reviewer-offline-guest/root/opt/reviewer/src \
python3 -m code_reviewer.sandbox.launch \
  --firecracker /path/to/firecracker --firecracker-sha256 EXPECTED_BINARY_SHA256 \
  --kernel /path/to/vmlinux --kernel-sha256 EXPECTED_KERNEL_SHA256 \
  --initrd /tmp/reviewer-offline-guest/initrd.cpio.gz --initrd-sha256 EXPECTED_INITRD_SHA256
```

Replace all three digest placeholders with lowercase 64-character SHA-256 values. Calculating a digest from an arbitrary downloaded file and passing it to the launcher does **not** establish the file's provenance; establish trust during artifact acquisition. The launcher detects artifact mismatches but is not a signing/attestation system.

Success returns a synthetic JSON summary with two completed single-agent passes, four completed persona passes, and one consolidated finding per mode. The fake executable is invoked three times in single-agent mode and eight times in persona mode. Reports are generated and checked inside the guest, then discarded; full report export is not implemented yet.

The VMM is terminated/reaped on cancellation, deadline or output overflow. The guest has no network interface or attached disk. Stop the outer build/test VM after manual checks. The scripts do not create, start, repair or delete Lima/Colima profiles themselves.

## Verification and limits

```bash
python -m unittest tests.test_sandbox_prototype
```

The full normal suite must also pass on the supported oldest/newest Python versions. Unit tests use local fake processes and never boot a VM or call a model. Real guest execution is a separate deployment check recorded in the handover.

This prototype has a writable root-owned guest runtime and no production jailer configuration; it is **not suitable for real or untrusted reviews**. Read the [architecture and next gates](../docs/sandbox-architecture.md#next-implementation-gates) before extending it. No image is automatically promoted into a production path, and no ordinary review falls back to this smoke test.

## Real Claude startup probe (offline)

Verify a pinned Linux ARM64 binary against its signed release manifest first; see [runtime/authentication](../docs/sandbox-runtime-auth.md) for provenance, auth choices and limits. No login or model request is performed.

The same builder accepts two additional operator-controlled arguments:

```bash
sh /tmp/build-offline-guest.sh /tmp/reviewer-source.tar /tmp/reviewer-runtime-guest \
  /path/to/verified-linux-arm64-claude EXPECTED_CLAUDE_SHA256
```

Use a new output directory. Supplying these arguments selects the runtime-probe guest instead of the fake-Claude flow smoke. On the Linux test host, launch it with the usual three verified artifact paths/digests and add `--workload runtime`. This selects a 4096 MiB guest (2 vCPUs); allow at least 6 GiB for the outer Lima VM. The default `--workload smoke` remains 1024 MiB.

A passing runtime result has `authenticated: false`, `auth_method: "none"` and `headless_auth_refused: true`. These are expected successful checks, not a working login. The launcher still accepts no credentials, network interface or PR input. Neither mode tests real model output. Stop the dedicated outer VM afterward.

## OpenAI adapter smoke (offline)

The operator selected OpenAI for controlled sandbox testing before Claude-only production use. This implemented smoke still uses synthetic fixtures; the planned live review uses the selected non-sensitive personal repository. See the [backend design and remaining connected-test gates](../docs/sandbox-openai.md).

```bash
python -m code_reviewer.sandbox.openai_smoke
sh /tmp/build-offline-guest.sh /tmp/reviewer-source.tar /tmp/reviewer-openai-guest openai-offline
```

Launch that image with the usual verified artifact paths/digests and `--workload openai-offline`. It uses 1024 MiB, no NIC/disks and simulated Responses API replies; no real key is read or transmitted. Both flows exercise the actual adapter and bounded local Read tool. The [connected-pilot plan](../docs/sandbox-architecture.md#agreed-connected-pilot-2026-10-26-not-implemented) adds bounded personal-repository inputs/reports, external destination restrictions and private delivery of a disposable key into the guest. It defers a custom relay and accepts key exposure if that test guest is compromised. None is implemented here yet; these commands still accept no key or target repo. Do not supply a key yet.

## Review before connected implementation

The [2026-10-26 review](../docs/sandbox-architecture.md#review-before-connected-implementation-2026-10-26) records two current issues: archive pipeline failures can be hidden by the final compression status, and skipped scan files consume IO without charging the decoded-text budget. Correct these with regressions before extending the prototype to the personal-repository pilot. This runbook describes existing offline commands; it provides no live workaround.
