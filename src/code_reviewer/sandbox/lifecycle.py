"""Opt-in no-key deployment evidence for cancellation and actual supervisor SIGKILL."""
from __future__ import annotations

import json
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .artifacts import Artifact
from .capsule import regular
from .runtime import MAX_INITRD_BYTES, MAX_KERNEL_BYTES
from .network import command
from .vm import CHECKS, VirtualMachine, has_mount, stop_unit


def machine(args, root):
    return VirtualMachine(root, Artifact(args.root/'firecracker', args.firecracker_sha256, MAX_KERNEL_BYTES),
        Artifact(args.root/'jailer', args.jailer_sha256, MAX_KERNEL_BYTES),
        Artifact(args.root/'kernel', args.kernel_sha256, MAX_KERNEL_BYTES),
        Artifact(args.image_dir/'initrd.cpio.gz', args.initrd_sha256, MAX_INITRD_BYTES),
        timeout=30, network=False, memory=1024)


def observe(vm):
    # The offline guest waits for a client; do not connect, so it remains alive
    # until the external deadline rather than exiting when a socket is closed.
    until = time.monotonic()+15
    while time.monotonic() < until:
        if vm.proc.poll() is not None:
            raise RuntimeError('VM exited during lifecycle check')
        try:
            return vm.confinement()
        except (OSError, ValueError, RuntimeError, IndexError):
            time.sleep(.1)
    raise TimeoutError('VMM confinement observation timed out')


def abandoned_child(args):
    root = args.root/'abandoned-vm'
    root.mkdir(mode=0o700)
    vm = machine(args, root)
    state = args.root/'abandoned.json'
    temporary = args.root/'abandoned.pending'
    temporary.write_text(json.dumps({'unit': vm.unit}))
    temporary.rename(state)
    vm.__enter__()  # Deliberately killed without __exit__; this is not a review path.
    checks = observe(vm)
    temporary = args.root/'abandoned.pending'
    temporary.write_text(json.dumps({'unit': vm.unit, 'checks': checks}))
    temporary.rename(state)
    while True:
        signal.pause()


def exercise(args):
    if args.image_dir is None or args.initrd_sha256 is None:
        raise ValueError('lifecycle checks require a pinned offline image')
    cancel_root = args.root/'cancelled-vm'
    cancel_root.mkdir(mode=0o700)
    result = {}
    try:
        vm = machine(args, cancel_root)
        try:
            with vm:
                observe(vm)
                raise KeyboardInterrupt
        except KeyboardInterrupt:
            result['cancel_stopped_vm'] = vm.proc.poll() is not None and not vm.mounted
        finally:
            if not vm.mounted:
                shutil.rmtree(cancel_root)
        child_args = ['python3', '-m', 'code_reviewer.sandbox.check_worker', '--abandon', '--root', str(args.root),
            '--firecracker-sha256', args.firecracker_sha256, '--kernel-sha256', args.kernel_sha256,
            '--jailer-sha256', args.jailer_sha256, '--image-dir', str(args.image_dir), '--initrd-sha256', args.initrd_sha256]
        child = subprocess.Popen(child_args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True,
                                 env={'PATH':'/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE':'1',
                                      'PYTHONPATH':str(Path(__file__).parents[2])})
        state, unit = args.root/'abandoned.json', None
        try:
            until = time.monotonic()+20
            while time.monotonic() < until:
                if child.poll() is not None:
                    raise RuntimeError('lifecycle child exited before observation')
                if state.exists():
                    value = json.loads(regular(state, 65536))
                    unit = value['unit']
                    checks = value.get('checks', {})
                    if set(checks) == CHECKS and all(flag is True for flag in checks.values()):
                        break
                time.sleep(.1)
            else:
                raise TimeoutError('lifecycle child observation timed out')
            child.kill()
            child.wait(timeout=5)
            active = lambda: command(['systemctl','show',unit,'--property=ActiveState','--value'],check=False).stdout.strip()
            result['survived_supervisor_sigkill'] = active() == 'active'
            until = time.monotonic()+45
            while active() in ('active','activating','deactivating') and time.monotonic() < until:
                time.sleep(.2)
            result['deadline_stopped_vm'] = active() in ('inactive','failed')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if unit is not None:
                stop_unit(unit)
            mounted = args.root/'abandoned-vm/jail/firecracker/review/root/runtime'
            if has_mount(mounted):
                command(['umount',str(mounted)])
            shutil.rmtree(args.root/'abandoned-vm')
        result['runtime_mount_removed'] = not has_mount(mounted)
    finally:
        (args.root/'abandoned.json').unlink(missing_ok=True)
    if set(result) != {'cancel_stopped_vm','survived_supervisor_sigkill','deadline_stopped_vm','runtime_mount_removed'} or not all(result.values()):
        raise RuntimeError('lifecycle deployment checks failed')
    return result
