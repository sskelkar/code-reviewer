# Run the sandbox deployment check

`reviewer sandbox check` is the repository-owned command for checking the current Mac/Lima/Firecracker deployment. It replaces handwritten external check drivers. By default it generates a harmless fixture. An optional `--pr` URL checks public acquisition inside the review VM; it accepts no local checkout, model selection or API key and makes no inference request. It is a deployment check, not a review of your project or proof of production readiness.

## Prerequisites

Use the editable installation from the [README](../README.md#quick-start). This command currently supports an Apple Silicon Mac with an **already prepared, stopped, dedicated** Lima VZ worker: ARM64 Ubuntu 24.04, nested virtualization, at least 6 GiB RAM, plain mode, no host mounts, forwarded SSH agent/X11/host keys, containerd or proxy-environment propagation. It refuses to take over a running worker. Other Lima instances and Colima are not selected or modified.

The worker needs Python 3.12, Git, static BusyBox, GNU cpio/coreutils, systemd 255 with cgroup v2, iproute2, nftables, Squid 6 with its dedicated `proxy` user and noninteractive root sudo for setup. The system Squid service must not occupy the check proxy port. The command verifies the worker profile and lets missing tools/KVM/policy refuse execution; it does not install packages or relax the host firewall. Initial worker provisioning remains separate; see the [offline runbook](../sandbox/README.md) and [connected prerequisites](sandbox-connected.md#prerequisites).

Supply an operator-controlled artifact directory containing:

- `firecracker.tgz`: the upstream ARM64 v1.17.0 release archive, SHA-256 `e351ebe4f7a16b5873bbd51005d2e6767103cff4d5ebc829df2d3f95a93e2256` from the recorded upstream release metadata. The command checks the compressed archive before parsing, extracts only the exact bounded Firecracker and matching jailer members. The jailer SHA-256 is `4d8d2dd4dfc1d47932b2bd261479181dd0c54f0a2f32714421a7e7f9f07ddec8`; the Firecracker executable SHA-256 is `fe726e0b43c04363ac07e358be4dee982c3947c65ed3ae10c770fef5e1cd756c`.
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
3. Transfers trusted inputs into a private per-check directory, extracts a bounded source-only bootstrap and prepares two clean images. Unchanged images are reused from a private root-owned worker cache; changed source/builder/copied OS inputs select new images. Corrupt cache entries are refused.
4. Runs both real flow algorithms with simulated OpenAI Responses replies and actual bounded Read calls in an **offline Firecracker VM**, with no network device. Both workloads use the shared matching upstream jailer and transient systemd service. The host confirms UID 65534, no effective capabilities, no-new-privileges, default seccomp, PID/mount namespaces, chroot and cgroup/deadline controls. Results return over private vsock, not console markers.
5. Generates a harmless Git fixture inside the outer worker (or fetches the explicit public `--pr` inside the review VM) and runs admission/file/network probes in a **second, sequential Firecracker VM**. This connected check opens verified provider TLS only; no HTTP model request or key is sent. Before dropping privileges, the guest seals the admitted bare Git input and exported head snapshot as read-only mounts. Native write/chmod/runtime-write probes, file-tool limits and direct/forbidden-destination denials are checked, followed by host TAP/firewall cleanup.
6. Removes the remote job directory and stops the outer worker before saving a successful report. A worker already running at startup is refused and never stopped by this command.

These are two sequential test workloads, not a new architecture for actual reviews. A real review will still use one job VM. Check output records both flow results, boundary checks, source/runtime hashes and confirmed worker shutdown, with `live: false` and `production_qualified: false`.

Add `--lifecycle` to exercise cancellation and an actual supervisor SIGKILL with harmless offline VMs (about 30 seconds extra). The test observes the independent service deadline after supervisor death and confirms mount cleanup. No key, repository input or model request is involved.

## Public PR acquisition check

Add `--pr https://github.com/OWNER/REPOSITORY/pull/NUMBER` to the command above. The workstation and outer worker transfer only that validated URL; neither fetches or parses target Git. No existing clone or GitHub credentials are needed. This remains a no-key boundary check, not a code review.

Inside the same connected review VM, `acquire.py` runs as UID/GID 65534 with no-new-privileges and a clean environment. It reads bounded public PR metadata over verified HTTPS, pins the returned full base/head hashes once, then fetches those exact hashes from the requested GitHub repository. API-supplied clone URLs/branch names never become commands. Git has empty templates, no credentials/hooks/submodule recursion/tags, HTTPS only, no redirects, and explicit CA/proxy settings. Trees and exported files pass the existing admission limits.

Acquisition permits only `github.com:443` and `api.github.com:443` CONNECT authorities. After the guest reports admitted commits, the trusted supervisor kills the entire old proxy process group, closing existing tunnels, and starts the inference-only policy. The guest checks an already-open GitHub tunnel closes, fresh source tunnels are denied, and provider TLS works before readiness. The phase can only advance once; failures stop the job. Restarting the proxy does not extend its original lifetime. No provider credential is requested by this check.

Limits: 512 KiB API metadata; 256-commit shallow fetch; 120-second fetch deadline; monitored 128 MiB retained Git files / 10,000 entries, plus the guest's 512 MiB scratch, file/resource and external VM limits. Storage monitoring is periodic, not an exact network-byte quota. Missing commits, redirects, rate limits, unrelated histories or a merge-base beyond the shallow history fail closed; there is no unbounded deepening or alternate clone fallback. GitHub Enterprise/private repositories, submodule content and LFS downloads are unsupported. The public API cannot provide an atomic snapshot across later upstream updates: a run reviews its initially observed hashes or fails, while a later run reads current metadata again. The private report records pins and source revocation; target contents remain inside the disposable VM.

The metadata endpoint and shallow-fetch behavior follow [GitHub's pull-request API](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request) and [Git's fetch documentation](https://git-scm.com/docs/git-fetch); trusted transport restrictions are set using [Git configuration](https://git-scm.com/docs/git-config).

## Image reuse

`sandbox/prepare.py` owns preparation separately from job execution. Its cache lives only inside the dedicated Linux worker at `/var/lib/code-reviewer/images`. It stores trusted reviewer/runtime files, never repository inputs, API keys or saved findings. Cache identity covers the source-bundle hash, builder hash, workload, and the bytes/permissions/link targets of the installed OS files copied by the builder. The runtime fingerprint is checked before and after a build. Every reuse checks the recorded identity and image digest; the VM launcher independently hashes the bytes it stages.

Preparation is serialized and publication is atomic. Failed builds or changing OS inputs do not publish partial images. The result records `image_preparation.images.*.reused` and the selected hashes. A cache mismatch refuses execution rather than silently trusting/rebuilding a damaged entry; inspect/remove the affected clean cache entry on the worker before retrying. Older clean cache entries are retained within the worker's bounded disk and can be removed when not needed. These are locally prepared images, not signed/reproducible production attestations.

## Recover an abandoned worker

```bash
reviewer sandbox recover --lima-home /path/to/dedicated-lima-state --instance reviewer-sandbox
```

This is an explicit destructive recovery of **reviewer job state in the selected dedicated worker**, not a host cleanup command. It refuses if another supported launcher holds the worker lock. It validates the same mountless/agent-free worker profile, stops a running worker if necessary, starts it with fresh kernel state, confirms no review VM services/job mounts/network policy remain, removes only recognized root/operator-owned stale reviewer temporary directories, then stops the worker. Symlinks, unexpected mounted/live state or unverifiable cleanup are refused; unrelated directory names and other Lima instances are untouched. The clean image cache is retained.

Normal checks still refuse to take over a running worker. Their fresh-worker preparation also removes recognized stale scratch while preserving the current job directory. Neither path resumes an interrupted review or makes unknown provider usage known. Recovery has no API-key interface.

## Failures and limits

The exit status is zero only after validated results and cleanup. Ctrl-C, SIGTERM, SIGHUP, failed build, bad result or process deadline trigger shutdown through the same lifecycle. Process output is bounded and inherited API-key/SSH-agent environment variables are not forwarded by the Mac launcher. No API key prompt exists.

If a shutdown/cleanup operation itself fails, the command fails; do not assume success. Secondary scratch-cleanup failures do not hide the original error. The transient systemd unit bounds the VM independently of Python (180 seconds normally, 360 seconds for the public acquisition workload, 30 seconds in the lifecycle test), kills the whole process group immediately on stop, and bounds stop time; the proxy retains its separate deadline. Python `finally` cannot run after SIGKILL, so use explicit worker recovery for abandoned state. Host failure can still lose the run/result; automatic resume is not implemented. A stale network policy refuses reuse rather than being overwritten. The report confirms only the named tests, not every possible escape or correct model behavior.

The public live-review path remains blocked. Public acquisition is available through the no-key check. Next qualify provider authentication and wire authenticated review/report dispatch into the normal review command. This check does not bypass those controls. See the [security roadmap](security-roadmap.md), [architecture](sandbox-architecture.md) and [alternative designs](sandbox-alternatives.md).
