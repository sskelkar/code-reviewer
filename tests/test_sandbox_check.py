"""The supported deployment command owns its lifecycle without accepting real review inputs."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from code_reviewer.cli import main
from code_reviewer.sandbox import check
from code_reviewer.sandbox.check_worker import RESULT_PREFIX
from code_reviewer.sandbox.connected import FILE_CHECKS, NETWORK_CHECKS


def profile():
    return {'name': 'synthetic-worker', 'status': 'Stopped', 'vmType': 'vz', 'arch': 'aarch64',
            'memory': 6 * 1024 ** 3, 'config': {'plain': True, 'nestedVirtualization': True,
            'propagateProxyEnv': False, 'ssh': dict(forwardAgent=False, forwardX11=False, loadDotSSHPubKeys=False),
            'containerd': dict(system=False, user=False)}}


def boundary():
    return {'network_cleanup': True, 'initrd_sha256': 'a' * 64,
            'checks': {'network': dict.fromkeys(NETWORK_CHECKS, True), 'file_tools': dict.fromkeys(FILE_CHECKS, True)}}


def offline():
    return {'format': 1, 'synthetic': True, 'provider': 'openai', 'live': False, 'flows': {
        'single-agent': dict(calls=3, completed=2, findings=1, stop_reason='max_passes'),
        'agent-personas': dict(calls=8, completed=4, findings=1, stop_reason='completed')}}


class SandboxCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_cli_uses_supported_command_without_loading_review_config(self):
        with patch('code_reviewer.cli.load_config', side_effect=AssertionError('no target/config')), \
             patch.object(check, 'cli', return_value=0) as run:
            self.assertEqual(main(['sandbox', 'check', '--artifacts', '/synthetic', '--lima-home', '/synthetic']), 0)
        self.assertEqual(run.call_args[0][0].instance, 'reviewer-sandbox')

    def test_refuses_worker_profiles_with_integrations_or_running_workloads(self):
        row = profile()
        check.validate_worker(row, row['name'])
        mutations = [dict(status='Running'), dict(memory=1024), dict(vmType='qemu'), dict(arch='x86_64')]
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                check.validate_worker(dict(row, **change), row['name'])
        for key, value in [('plain', False), ('nestedVirtualization', False), ('mounts', [{'location': '~'}]),
                           ('propagateProxyEnv', True), ('ssh', {'forwardAgent': True}), ('containerd', {'system': True})]:
            bad = copy.deepcopy(row)
            bad['config'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                check.validate_worker(bad, row['name'])

    def test_session_stops_after_success_start_failure_or_body_interruption(self):
        (self.root / 'synthetic-worker').mkdir()
        worker = check.Worker(self.root, 'synthetic-worker')
        for failure in (None, 'start', 'body'):
            calls = []
            def invoke(*args, **kwargs):
                calls.append(args)
                if failure == 'start' and args[0] == 'start':
                    raise RuntimeError('synthetic start failure')
            with patch.object(worker, 'state', return_value=profile()), patch.object(worker, 'call', side_effect=invoke):
                if failure:
                    with self.assertRaises((RuntimeError, KeyboardInterrupt)):
                        with worker.session():
                            raise KeyboardInterrupt
                else:
                    with worker.session():
                        pass
            self.assertEqual([row[0] for row in calls], ['start', 'stop'])

    def test_refused_running_worker_is_never_stopped_and_lock_is_exclusive(self):
        (self.root / 'synthetic-worker').mkdir()
        worker = check.Worker(self.root, 'synthetic-worker')
        with patch.object(worker, 'state', return_value=dict(profile(), status='Running')), patch.object(worker, 'call') as call:
            with self.assertRaises(ValueError), worker.session():
                pass
            call.assert_not_called()
        with patch.object(worker, 'state', return_value=profile()), patch.object(worker, 'call'):
            with worker.session():
                with self.assertRaises(BlockingIOError), worker.session():
                    pass

    def test_stop_failure_cannot_report_success_and_credentials_are_not_forwarded(self):
        (self.root / 'synthetic-worker').mkdir()
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-not-a-secret', 'SSH_AUTH_SOCK': '/synthetic'}):
            worker = check.Worker(self.root, 'synthetic-worker')
        self.assertNotIn('OPENAI_API_KEY', worker.env)
        self.assertNotIn('SSH_AUTH_SOCK', worker.env)
        with patch.object(worker, 'state', side_effect=[profile(), dict(profile(), status='Running')]), \
             patch.object(worker, 'call'):
            with self.assertRaisesRegex(RuntimeError, 'shutdown'), worker.session():
                pass

    def test_boundary_result_requires_complete_probes_and_host_cleanup(self):
        value = boundary()
        self.assertEqual(check.boundary_result('status\n' + RESULT_PREFIX + json.dumps(value)), value)
        for changed in (dict(value, network_cleanup=False), dict(value, initrd_sha256='invalid'),
                        dict(value, checks={'network': {}, 'file_tools': {}})):
            with self.assertRaises(ValueError):
                check.boundary_result(RESULT_PREFIX + json.dumps(changed))
        with self.assertRaises(ValueError):
            check.boundary_result((RESULT_PREFIX + json.dumps(value) + '\n') * 2)

    def test_archive_member_is_bounded_hashed_and_never_extracted_by_path(self):
        archive = self.root / 'firecracker.tgz'
        def write(data, kind=tarfile.REGTYPE):
            with tarfile.open(archive, 'w:gz') as tar:
                member = tarfile.TarInfo(check.FIRECRACKER_MEMBER)
                member.size, member.type = len(data), kind
                tar.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        stage = self.root / 'stage'
        stage.mkdir()
        write(b'synthetic')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            check.stage_inputs(self.root, stage)
        self.assertEqual(list(stage.iterdir()), [])
        (self.root / 'vmlinux-6.1.188').write_bytes(b'kernel')
        with patch.object(check, 'FIRECRACKER_SHA', hashlib.sha256(b'synthetic').hexdigest()), \
             patch.object(check, 'ARCHIVE_SHA', hashlib.sha256(archive.read_bytes()).hexdigest()), \
             patch.object(check, 'KERNEL_SHA', hashlib.sha256(b'kernel').hexdigest()):
            check.stage_inputs(self.root, stage)
        self.assertEqual((stage / 'firecracker').read_bytes(), b'synthetic')
        self.assertEqual((stage / 'kernel').read_bytes(), b'kernel')
        write(b'', tarfile.SYMTYPE)
        with patch.object(check, 'ARCHIVE_SHA', hashlib.sha256(archive.read_bytes()).hexdigest()), \
             self.assertRaisesRegex(ValueError, 'invalid Firecracker'):
            check.stage_inputs(self.root, stage)

    def test_complete_lifecycle_reports_only_after_shutdown_and_deletes_remote_state_on_failure(self):
        events = []
        class FakeWorker:
            instance = 'synthetic-worker'
            def __init__(self, *args):
                pass
            @contextmanager
            def session(self):
                events.append('start')
                try:
                    yield self
                finally:
                    events.append('stop')
            def call(self, *args, **kwargs):
                events.append(args)
            def shell(self, *args, **kwargs):
                events.append(args)
                if args[0] == 'id':
                    return '502'
                if args[0] == 'sha256sum':
                    return 'a' * 64 + ' image'
                if 'code_reviewer.sandbox.launch' in args:
                    return json.dumps(offline())
                if 'code_reviewer.sandbox.check_worker' in args:
                    return RESULT_PREFIX + json.dumps(boundary())
                return ''
        target = self.root / 'result'
        with patch.object(check.sys, 'platform', 'darwin'), patch.object(check, 'Worker', FakeWorker), \
             patch.object(check, 'stage_inputs'), patch.object(check, 'boundary_result', wraps=check.boundary_result) as validate:
            report = check.run_check(self.root, self.root, 'synthetic-worker', target)
            result = json.loads(report.read_text())
            self.assertTrue(result['worker_stopped'])
            self.assertFalse(result['production_qualified'])
            self.assertFalse(result['live'])
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual(events[-1], 'stop')
            self.assertEqual(events[-2][:5], ('sudo', '-n', 'rm', '-rf', '--'))
            with self.assertRaisesRegex(ValueError, 'new'):
                check.run_check(self.root, self.root, 'synthetic-worker', target)
            validate.side_effect = ValueError('synthetic invalid result')
            with self.assertRaises(ValueError):
                check.run_check(self.root, self.root, 'synthetic-worker', self.root / 'failed')
            self.assertEqual(events[-1], 'stop')
            self.assertEqual(events[-2][:5], ('sudo', '-n', 'rm', '-rf', '--'))
            self.assertFalse((self.root / 'failed').exists())
