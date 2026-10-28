"""Confinement contracts without host mounts, root, systemd jobs or VM boots."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from code_reviewer.config import Config
from code_reviewer.engine import Engine
from code_reviewer.sandbox import snapshot, vm
from code_reviewer.sandbox.artifacts import Artifact
from code_reviewer.sandbox.git_input import bounded_export
from tests.helpers import FakeRunner, make_repo


class VMConfinementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = self.root / 'job'
        self.job.mkdir()
        inputs = []
        for name in ('firecracker', 'jailer', 'kernel', 'initrd'):
            path = self.root / name
            path.write_bytes(name.encode())
            inputs.append(Artifact(path, hashlib.sha256(name.encode()).hexdigest(), 1024))
        self.machine = vm.VirtualMachine(self.job, *inputs, timeout=180)
        self.proc = Mock()
        self.proc.wait.return_value = 0

    def test_uses_jailer_namespaces_cgroup_deadline_and_readonly_runtime_then_stops(self):
        with patch.object(vm, 'command', return_value=SimpleNamespace(stdout='inactive', returncode=0)) as command, \
             patch.object(vm.subprocess, 'Popen', return_value=self.proc) as popen:
            with self.machine:
                argv = popen.call_args.args[0]
                for expected in ('--new-pid-ns', '--property=NoNewPrivileges=yes', '--property=RuntimeMaxSec=180',
                                 '--property=MemoryMax=4608M', '--property=TasksMax=64', '--property=CPUQuota=200%',
                                 '--property=MemorySwapMax=0', '--property=ExitType=cgroup', '/runtime/config.json'):
                    self.assertIn(expected, argv)
                self.assertIn('--property=KillSignal=SIGKILL', argv)
                self.assertIn('--property=TimeoutStopSec=5', argv)
                self.assertEqual(argv[argv.index('--uid') + 1], '65534')
                self.assertNotIn('--no-seccomp', argv)
                self.assertNotIn('--daemonize', argv)
                self.assertTrue(any('remount,bind,ro,nosuid,nodev,noexec' in call.args[0] for call in command.call_args_list))
                self.assertEqual((self.job/'runtime/config.json').stat().st_mode & 0o777, 0o444)
            self.assertTrue(any(call.args[0][:2] == ['systemctl', 'stop'] for call in command.call_args_list))
            self.assertEqual(command.call_args.args[0][0], 'umount')

    def test_partial_launch_failure_unmounts_runtime(self):
        with patch.object(vm, 'command') as command, patch.object(vm.subprocess, 'Popen', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError), self.machine:
                pass
        self.assertEqual(command.call_args.args[0][0], 'umount')
        self.assertFalse(self.machine.mounted)

    def test_interruption_stops_service_and_unconfirmed_shutdown_refuses_cleanup(self):
        with patch.object(vm, 'command', return_value=SimpleNamespace(stdout='inactive', returncode=0)) as command, \
             patch.object(vm.subprocess, 'Popen', return_value=self.proc):
            with self.assertRaises(KeyboardInterrupt), self.machine:
                raise KeyboardInterrupt
            self.assertEqual(command.call_args.args[0][0], 'umount')
        self.machine.proc, self.machine.mounted = self.proc, True
        with patch.object(vm, 'command', return_value=SimpleNamespace(stdout='active', returncode=0)) as command:
            with self.assertRaisesRegex(RuntimeError, 'did not stop'):
                self.machine.__exit__(None, None, None)
            self.assertFalse(any(call.args[0][0] == 'umount' for call in command.call_args_list))

    def test_observes_confinement_from_host_and_rejects_missing_controls(self):
        jail = self.job/'jail/firecracker/review/root'
        jail.mkdir(parents=True)
        status = 'Uid:\t65534\t65534\t65534\t65534\nNoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\nNSpid:\t123\t1\n'
        properties = 'MemoryMax=4831838208\nMemorySwapMax=0\nTasksMax=64\nCPUQuotaPerSecUSec=2s\nRuntimeMaxUSec=3min\n'
        original_stat = os.stat
        def fake_read(path, *args, **kwargs):
            if str(path).endswith('/status'):
                return status
            if str(path).endswith('/cgroup'):
                return '0::/system.slice/' + self.machine.unit + '.service\n'
            raise AssertionError('unexpected host path')
        def fake_stat(path, *args, **kwargs):
            return original_stat(jail if str(path) == '/proc/123/root' else path, *args, **kwargs)
        with patch('code_reviewer.sandbox.capsule.regular', return_value=b'123'), \
             patch.object(Path, 'read_text', fake_read), patch.object(vm.os, 'stat', side_effect=fake_stat), \
             patch.object(vm.os, 'readlink', side_effect=lambda path: 'different' if '/123/' in path else 'self'), \
             patch.object(vm, 'command', return_value=SimpleNamespace(stdout=properties)):
            self.assertEqual(self.machine.confinement(), dict.fromkeys(vm.CHECKS, True))
            status = status.replace('Seccomp:\t2', 'Seccomp:\t0')
            with self.assertRaisesRegex(RuntimeError, 'seccomp'):
                self.machine.confinement()

    def test_snapshot_mount_failure_prevents_privilege_drop_and_uses_exact_git_trust(self):
        ctx = make_repo(self.root)
        with patch.object(snapshot.os, 'geteuid', return_value=0), patch.object(snapshot, 'readonly') as readonly, \
             patch.object(snapshot, 'drop_privileges') as drop, patch.dict(os.environ):
            path = snapshot.prepare(ctx, self.root)
            self.assertEqual([call.args[0] for call in readonly.call_args_list], [path, ctx.git_dir])
            self.assertEqual(os.environ['GIT_CONFIG_VALUE_0'], str(ctx.git_dir))
            self.assertEqual(os.environ['GIT_CONFIG_KEY_0'], 'safe.directory')
            self.assertEqual((path/'app.py').stat().st_mode & 0o777, 0o444)
            drop.assert_called_once()
        with patch.object(snapshot.subprocess, 'run', side_effect=OSError('synthetic mount failure')):
            with self.assertRaises(OSError):
                snapshot.readonly(self.root)

    def test_no_new_privileges_is_required_before_dropping_uid(self):
        libc = Mock()
        libc.prctl.return_value = -1
        with patch.object(snapshot.ctypes, 'CDLL', return_value=libc), patch.object(snapshot.os, 'setuid') as uid:
            with self.assertRaises(OSError):
                snapshot.drop_privileges()
            uid.assert_not_called()

    def test_bind_mount_detection_and_service_cleanup_do_not_use_unsafe_shortcuts(self):
        target = self.job/'runtime'
        row = '1 2 3:4 / '+str(target)+' ro - ext4 /dev/synthetic ro\n'
        with patch.object(Path, 'read_text', return_value=row), patch.object(vm.os.path, 'ismount', return_value=False):
            self.assertTrue(vm.has_mount(target))
            self.assertFalse(vm.has_mount(self.root/'unrelated'))
        with patch.object(vm, 'command') as command:
            for name in ('unrelated.service', 'reviewer-vm-../bad', 'reviewer-vm-'):
                with self.assertRaises(ValueError):
                    vm.stop_unit(name)
            command.assert_not_called()

    def test_both_flows_reuse_external_snapshot_without_exporting_or_deleting_it(self):
        ctx = make_repo(self.root)
        checkout = self.root/'snapshot'
        bounded_export(ctx.git_dir, ctx.head_sha, checkout)
        for mode in ('single-agent', 'agent-personas'):
            cfg = Config(review_mode=mode, criteria_mode='defaults_only', min_passes=1, max_passes=1,
                         persona_passes=1, persona_parallel=1, runs_dir=str(self.root/mode))
            runner = FakeRunner([{'findings': []}])
            with patch('code_reviewer.shared.git_ops.export_tree', side_effect=AssertionError('already exported')):
                result = Engine(cfg, runner).review(ctx, prepared_checkout=checkout)
            self.assertEqual(result.passes_completed, 1 if mode == 'single-agent' else 4)
            self.assertTrue((checkout/'app.py').exists())
            self.assertTrue(all(call['cwd'] == checkout for call in runner.calls if call['tools']))
