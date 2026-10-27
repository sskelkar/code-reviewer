# Run the sandbox deployment check

`reviewer sandbox check` is the repository-owned command for checking the current Mac/Lima/Firecracker deployment. It replaces handwritten external check drivers. It accepts no PR, repository checkout, model selection or API key and makes no inference request. It is a deployment check, not a review of your project or proof of production readiness.

## Prerequisites

Use the editable installation from the [README](../README.md#quick-start). This command currently supports an Apple Silicon Mac with an **already prepared, stopped, dedicated** Lima VZ worker: ARM64 Ubuntu 24.04, nested virtualization, at least 6 GiB RAM, plain mode, no host mounts, forwarded SSH agent/X11/host keys, containerd or proxy-environment propagation. It refuses to take over a running worker. Other Lima instances and Colima are not selected or modified.

The worker needs Python 3.12, Git, static BusyBox, GNU cpio/coreutils, util-linux (`setpriv`), iproute2, nftables, Squid 6 with its dedicated `proxy` user and noninteractive root sudo for setup. The system Squid service must not occupy the check proxy port. The command verifies the worker profile and lets missing tools/KVM/policy refuse execution; it does not install packages or relax the host firewall. Initial worker provisioning remains separate; see the [offline runbook](../sandbox/README.md) and [connected prerequisites](sandbox-connected.md#prerequisites).

Supply an operator-controlled artifact directory containing:

- `firecracker.tgz`: the upstream ARM64 v1.17.0 release archive, SHA-256 `e351ebe4f7a16b5873bbd51005d2e6767103cff4d5ebc829df2d3f95a93e2256` from the recorded upstream release metadata. The command checks the compressed archive before parsing, extracts only the exact bounded binary member and checks it against the previously verified executable SHA-256 `fe726e0b43c04363ac07e358be4dee982c3947c65ed3ae10c770fef5e1cd756c`.
- `vmlinux-6.1.188`: the previously tested ARM64 kernel, SHA-256 `52748cec9d75865246d95906021fea6754bc0abc2cba0cfc792dac5ec7f46f83`.

It deliberately does not download arbitrary URLs or calculate trust from whatever file it finds. Changing these pinned versions requires updating deployment evidence. The current runtime derives from the worker's installed Ubuntu libraries; it is not a reproducible, production-qualified image.

## Command

```bash
reviewer sandbox check \
  --artifacts /path/to/verified-artifacts \
  --lima-home /path/to/dedicated-lima-state \
  --instance reviewer-sandbox
```

Use your actual operator paths and prepared instance name. Add `--output /path/to/new-result-directory` to choose where the report goes. Otherwise the command creates a random temporary result directory and prints its path. Output directories are private (0700), with `report.json` mode 0600; existing output directories are refused. `--config`/current-directory `review.toml` are not read by this command.

The command:

1. Checks/stages the pinned artifacts and bundles the current trusted reviewer source, without target code or credentials.
2. Locks the selected worker, validates its profile and starts it.
3. Transfers trusted inputs into a private per-check directory and builds two images using the repository's builder.
4. Runs both real flow algorithms with simulated OpenAI Responses replies and actual bounded Read calls in an **offline Firecracker VM**, with no network device. The VMM runs under the worker operator UID with the KVM group, not root.
5. Generates a harmless Git fixture inside the outer worker and runs admission/file/network probes in a **second, sequential Firecracker VM**. This connected check opens verified provider TLS only; no HTTP model request or key is sent. It exercises direct/forbidden-destination denial and file-tool limits, then verifies host TAP/firewall cleanup.
6. Removes the remote job directory and stops the outer worker before saving a successful report. A worker already running at startup is refused and never stopped by this command.

These are two sequential test workloads, not a new architecture for actual reviews. A real review will still use one job VM. Check output records both flow results, boundary checks, source/runtime hashes and confirmed worker shutdown, with `live: false` and `production_qualified: false`.

## Failures and limits

The exit status is zero only after validated results and cleanup. Ctrl-C, SIGTERM, SIGHUP, failed build, bad result or process deadline trigger shutdown through the same lifecycle. Process output is bounded and inherited API-key/SSH-agent environment variables are not forwarded by the Mac launcher. No API key prompt exists.

If a shutdown/cleanup operation itself fails, the command fails and reports that failure; do not assume success. SIGKILL, workstation crashes and host compromise cannot be handled by Python `finally`; independent crash recovery remains unfinished. A stale network policy refuses reuse rather than being overwritten. The report confirms only the named tests, not every possible escape or correct model behavior.

The public live-review path remains blocked. Next connect isolated PR acquisition, complete the VM/provider security controls and wire authenticated review/report dispatch into the normal review command. This check does not bypass those controls. See the [security roadmap](security-roadmap.md), [architecture](sandbox-architecture.md) and [alternative designs](sandbox-alternatives.md).
