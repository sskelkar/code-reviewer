# Reviewer VM development

Use [`reviewer sandbox check`](../docs/sandbox-check.md) for the supported Mac/Lima/Firecracker check. It owns artifact validation, clean image preparation/reuse, both offline review algorithms, no-key connected probes, private results and dedicated-worker shutdown. Add `--pr` for in-VM public acquisition and source-access revocation, or `--lifecycle` for cancellation/supervisor-loss checks. It never requests a model key or performs inference.

The standalone raw-Firecracker launcher was extracted into an independent external playground at the operator's request. It is no longer packaged or maintained as part of this project. This repository retains the shared jailed VM lifecycle and the helpers it needs in `sandbox/runtime.py`.

## Build inputs

The dedicated worker needs ARM64 Ubuntu, nested KVM on compatible Macs, Python/Git/BusyBox/cpio and the standard Linux network/service tools listed in the [deployment runbook](../docs/sandbox-check.md#prerequisites). No host directories or credential sockets are mounted. Artifacts are operator-selected and digest-pinned.

Bundle trusted reviewer sources on the workstation:

```bash
python -m code_reviewer.sandbox.bundle --output /tmp/reviewer-source.tar
```

The bundle includes trusted Python/Markdown/JSON assets and a manifest, not target inputs, runs or environment files. Explicitly transfer it and `firecracker/build-offline-guest.sh` to the dedicated worker. The supported preparer selects clean cached images and checks identity/digests. Manual builder invocations are development interfaces, not alternate review launchers.

## Development checks

```bash
python -m unittest tests.test_sandbox_prototype tests.test_vm_confinement
python -m code_reviewer.sandbox.openai_smoke
```

These simulate models and need no key/network/VM boot. Both algorithms exercise real validation, grounding, consolidation, tool dispatch and reports. The separate native CLI startup probe records argument/authentication compatibility without inference. The whole suite must pass on Python 3.9 and the newest available Python.

Actual no-key deployment evidence belongs in the [VM checklist](../docs/sandbox-todo.md#evidence-2026-10-28). Real reviews require completed dispatch/authentication integration; the normal frontend is currently preflight/refusal only. The [execution contract](../docs/security-contract.md) plans VM-default and explicit direct-host operation with either provider. The [VM architecture](../docs/sandbox-architecture.md) explains the isolated path; [alternatives](../docs/sandbox-alternatives.md) records other designs considered.
