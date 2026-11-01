# Alternatives to Lima plus Firecracker

The supported backend uses Lima plus Firecracker. This comparison explains its tradeoffs; alternative backends are outside the implementation scope.

## Why the current design has two VMs

Firecracker requires Linux/KVM. On macOS, a dedicated Lima VM supplies them. The disposable Firecracker VM contains the entire review job, including Python orchestration and model file tools; personas do not have separate VMs.

```text
Mac
└── Dedicated Lima Linux worker — supplies Linux/KVM and external policy
    └── Disposable Firecracker review VM
        └── Repository preparation, Python reviewer, model/tool loop
```

Firecracker deliberately exposes a small device model, applies VMM syscall filters and provides a Linux jailer for process confinement. Network filtering is explicitly the operator's responsibility. These mechanisms depend on correct host integration; choosing Firecracker alone does not establish production security. The shared jailed lifecycle supports normal review dispatch and no-key deployment checks; native Claude inference still requires qualification. See the [upstream design](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md), [jailer documentation](https://github.com/firecracker-microvm/firecracker/blob/main/docs/jailer.md) and the [security roadmap](proposal.md#checklist).

## Alternative boundaries

| Option | Arrangement | What it retains | What changes |
|---|---|---|---|
| One disposable Lima VM per review | Mac → Lima review VM | Hardware virtualization and a separate guest kernel; one job per VM | Removes Firecracker and its specific device model/jailer. Host-side network enforcement and lifecycle need a Mac implementation. Simplest local alternative to investigate. |
| A small native launcher using Apple's Virtualization framework | Mac → Linux review VM | Hardware virtualization and direct control over exposed devices | Similar underlying VM approach to Lima's VZ backend, but the project would maintain more VM/image/boot/lifecycle code. Removes Lima, adding maintenance rather than automatically improving isolation. |
| Firecracker on a dedicated physical Linux host | Mac client → Linux host → Firecracker review VM | The actual Firecracker/KVM/jailer architecture, without nested virtualization | Requires another machine, secure remote control and private input/results transfer. Strongest reuse of the existing Linux enforcement code. |

Apple's [Virtualization framework](https://developer.apple.com/documentation/virtualization) runs Linux VMs on macOS. Lima already uses that framework through its VZ backend and offers [plain mode](https://lima-vm.io/docs/faq/#is-it-possible-to-disable-mounts-port-forwarding-containerd-etc-) to disable convenience integrations such as shared folders and automatic port forwarding. The suitability judgments in the table are project-level architectural assessments, not a security equivalence claim from either vendor.

The Linux-host alternative assumes a physical host with KVM support. Moving Firecracker into an ordinary cloud VM can still require nested virtualization; moving the machine elsewhere does not itself remove a virtualization layer. Remote hosting also introduces another operator/infrastructure trust boundary and must meet the project's data/provider authorization requirements.

## Could one VM retain all Firecracker security guarantees?

Another hardware VM can target the same application requirements: isolate the guest kernel, expose no host directories or credentials, restrict network access outside guest control, bound resources and destroy job state. An Apple VM is not identical to Firecracker: their device implementations, process confinement and attack surfaces differ. Neither design guarantees protection against every hypervisor bug, hardware side channel or compromised host.

With one direct Lima review VM, policy that currently lives in the outer Linux worker must move outside that review VM, to the Mac or separately controlled infrastructure. Putting the only firewall/proxy inside a potentially compromised review guest would lose independent enforcement. Replacing the VM backend therefore requires implementation and containment tests, not just removing a box from the diagram.

Credential handling, approved model destinations, safe input admission, report validation and crash cleanup remain application integration responsibilities for every option. A sandbox also cannot establish that a review finding is correct or prevent code from reaching an explicitly permitted provider. See the [common security requirements](security-contract.md).

## Why other container tools are not direct replacements

- Ordinary Linux containers share their host kernel. On macOS they generally run inside a Linux VM, but replacing each review VM with a container in a reused worker changes the job isolation boundary. It does not preserve Firecracker's per-job hardware VM architecture.
- [gVisor](https://gvisor.dev/docs/architecture_guide/intro/) uses an application kernel and restricted host interfaces. It is a different containment design; its Linux host requirement also remains relevant on a Mac. It could run inside one Linux VM, but this is not the same per-job hardware boundary.
- [Kata Containers](https://github.com/kata-containers/kata-containers/blob/main/docs/design/virtualization.md) places container workloads in VMs. A Linux deployment that needs a nested VM on macOS would not automatically eliminate the two virtualization layers, and its container-runtime integration would add machinery to this small reviewer.

## When to reconsider

Revisit a disposable direct Lima VM if local setup, boot/build time or nested-KVM compatibility makes the current design impractical. A dedicated Linux host is an option for reusing Firecracker without a Mac compatibility layer. An alternative backend needs measured operational benefits and equivalent application-level containment tests before adoption.
