"""One jailed Firecracker process supervised by the dedicated worker's systemd."""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from .artifacts import Artifact
from .runtime import boot_config
from .network import command

CHECKS = {'non_root', 'no_new_privileges', 'seccomp', 'no_capabilities', 'pid_namespace',
          'mount_namespace', 'chroot', 'cgroup', 'resource_limits'}


def has_mount(path):
    # os.path.ismount misses bind mounts on the same filesystem.
    return any(row.split()[4] == str(path) for row in Path('/proc/self/mountinfo').read_text().splitlines())


def stop_unit(unit):
    if not re.fullmatch(r'reviewer-vm-[a-f0-9]{32}', unit):
        raise ValueError('invalid owned VM service name')
    command(['systemctl', 'stop', unit], check=False)
    state = command(['systemctl', 'show', unit, '--property=ActiveState', '--value'], check=False)
    if state.stdout.strip() not in ('inactive', 'failed'):
        raise RuntimeError('review VM service did not stop')


class VirtualMachine:
    """Never daemonize a VMM behind the launcher's process cleanup."""

    def __init__(self, root, binary, jailer, kernel, initrd, *, timeout, network=True, memory=4096):
        self.root = Path(root)
        self.inputs = dict(firecracker=binary, jailer=jailer, kernel=kernel, initrd=initrd)
        if any(not isinstance(value, Artifact) for value in self.inputs.values()):
            raise ValueError('pinned VMM, matching jailer, kernel and initrd required')
        if type(timeout) is not int or not 30 <= timeout <= 1800 or memory not in (1024, 4096):
            raise ValueError('invalid VM resource limits')
        self.timeout, self.network, self.memory = timeout, network, memory
        self.unit = 'reviewer-vm-' + uuid.uuid4().hex
        self.proc = None
        self.mounted = False

    def __enter__(self):
        try:
            runtime = self.root / 'runtime'
            runtime.mkdir(mode=0o700)
            for name, artifact in self.inputs.items():
                path = artifact.stage(runtime / name)
                path.chmod(0o555 if name in ('firecracker', 'jailer') else 0o444)
            # The jailer owns its root directory as the VMM UID. Mount runtime data
            # separately read-only so that UID cannot replace the kernel/config.
            jail = self.root / 'jail/firecracker/review/root'
            jail.mkdir(parents=True, mode=0o755)
            target = jail / 'runtime'
            target.mkdir(mode=0o755)
            config = boot_config(Path('/runtime/kernel'), Path('/runtime/initrd'), runtime=self.memory == 4096)
            config['machine-config']['mem_size_mib'] = self.memory
            if self.network:
                from .network import TAP
                config['network-interfaces'] = [{'iface_id': 'review', 'host_dev_name': TAP,
                                                 'guest_mac': '06:00:00:00:00:02'}]
            config['vsock'] = {'guest_cid': 3, 'uds_path': '/bootstrap.sock'}
            (runtime / 'config.json').write_text(json.dumps(config))
            (runtime / 'config.json').chmod(0o444)
            runtime.chmod(0o755)
            command(['mount', '--bind', str(runtime), str(target)])
            self.mounted = True
            command(['mount', '-o', 'remount,bind,ro,nosuid,nodev,noexec', str(target)])
            self.socket = jail / 'bootstrap.sock'
            argv = ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--service-type=exec',
                    '--unit=' + self.unit, '--property=ExitType=cgroup', '--property=KillMode=control-group',
                    '--property=KillSignal=SIGKILL', '--property=TimeoutStopSec=5',
                    '--property=RuntimeMaxSec=' + str(self.timeout), '--property=MemoryMax=' + str(self.memory + 512) + 'M',
                    '--property=MemorySwapMax=0', '--property=TasksMax=64', '--property=CPUQuota=200%',
                    '--property=NoNewPrivileges=yes', '--property=LimitCORE=0',
                    str(runtime / 'jailer'), '--id', 'review', '--exec-file', str(runtime / 'firecracker'),
                    '--uid', '65534', '--gid', '65534', '--chroot-base-dir', str(self.root / 'jail'),
                    '--new-pid-ns', '--cgroup-version', '2', '--parent-cgroup', 'system.slice/' + self.unit + '.service',
                    '--resource-limit', 'no-file=256', '--resource-limit', 'fsize=67108864',
                    '--', '--no-api', '--config-file', '/runtime/config.json']
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True,
                                         env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def confinement(self):
        """Observe the VMM from outside its jail; guest messages are not authority."""
        pid_path = self.root / 'jail/firecracker/review/root/firecracker.pid'
        from .capsule import regular
        pid = int(regular(pid_path, 32).decode())
        status = {}
        for row in Path('/proc/%d/status' % pid).read_text().splitlines():
            key, _, value = row.partition(':')
            status[key] = value.strip()
        cgroup = Path('/proc/%d/cgroup' % pid).read_text().strip().split('::', 1)[1]
        properties = dict(line.split('=', 1) for line in command([
            'systemctl', 'show', self.unit, '--property=MemoryMax,MemorySwapMax,TasksMax,CPUQuotaPerSecUSec,RuntimeMaxUSec'
        ]).stdout.splitlines())
        duration = properties.get('RuntimeMaxUSec', '')
        parts = re.findall(r'(\d+)(min|s)', duration)
        duration_ok = bool(parts) and re.sub(r'\d+(?:min|s)|\s+', '', duration) == '' and sum(
            int(amount) * (60 if unit == 'min' else 1) for amount, unit in parts) == self.timeout
        checks = {
            'non_root': status.get('Uid', '').split() == ['65534'] * 4,
            'no_new_privileges': status.get('NoNewPrivs') == '1',
            'seccomp': status.get('Seccomp') == '2',
            'no_capabilities': status.get('CapEff') == '0000000000000000',
            'pid_namespace': status.get('NSpid', '').split()[-1:] == ['1'],
            'mount_namespace': os.readlink('/proc/%d/ns/mnt' % pid) != os.readlink('/proc/self/ns/mnt'),
            'chroot': os.path.samestat(os.stat('/proc/%d/root' % pid),
                                      os.stat(self.root / 'jail/firecracker/review/root')),
            'cgroup': cgroup == '/system.slice/' + self.unit + '.service',
            'resource_limits': properties.get('MemoryMax') == str((self.memory + 512) * 1024 ** 2)
                and properties.get('MemorySwapMax') == '0' and properties.get('TasksMax') == '64'
                and properties.get('CPUQuotaPerSecUSec') == '2s' and duration_ok,
        }
        if not all(checks.values()):
            raise RuntimeError('host-observed VMM confinement check failed: ' + ', '.join(k for k,v in checks.items() if not v))
        return checks

    def __exit__(self, *args):
        if self.proc is not None:
            stop_unit(self.unit)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.mounted:
            command(['umount', str(self.root / 'jail/firecracker/review/root/runtime')])
            self.mounted = False
