# Review VM deployment assets

[VM setup/checks/recovery](../docs/sandbox-check.md) is the operator runbook. [VM architecture](../docs/sandbox-architecture.md) explains the boundary; [alternatives](../docs/sandbox-alternatives.md) compares other designs.

This directory contains Firecracker image builders and the Lima worker profile. Runtime Python belongs in `src/code_reviewer/sandbox/`. Builders are development interfaces; the normal CLI uses the shared jailed lifecycle.

To build a trusted source bundle for development:

```bash
python -m code_reviewer.sandbox.bundle --output /tmp/reviewer-source.tar
```

It contains Python/Markdown/JSON assets and a manifest, excluding target inputs, runs and environment files. Artifacts remain operator-selected and digest-pinned.

Offline developer checks (no key/network/VM boot):

```bash
python -m unittest tests.test_sandbox_prototype tests.test_vm_confinement
python -m code_reviewer.sandbox.openai_smoke
```
