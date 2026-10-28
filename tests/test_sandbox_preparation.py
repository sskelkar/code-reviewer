"""Clean image reuse and scoped recovery; no root, actual images, VM or network required."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_reviewer.sandbox import check, prepare, recover


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root/'cache'
        self.source, self.builder = self.root/'source', self.root/'builder'
        self.source.write_bytes(b'clean synthetic source')
        self.builder.write_bytes(b'synthetic builder')
        self.calls = []

    def fake_build(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        image = Path(cmd[3])
        image.mkdir(mode=0o700)
        (image/'initrd.cpio.gz').write_bytes(b'synthetic image '+cmd[4].encode())

    def context(self, runtime=None):
        stack = ExitStack()
        real_fstat = os.fstat
        def fake_fstat(fd):
            values = list(real_fstat(fd))
            values[4] = 0
            return os.stat_result(values)
        stack.enter_context(patch.object(prepare.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(prepare.os, 'fstat', side_effect=fake_fstat))
        stack.enter_context(patch.object(prepare, 'private_cache', side_effect=lambda path: path.mkdir(mode=0o700, parents=True, exist_ok=True)))
        stack.enter_context(patch.object(prepare, 'runtime_digest', side_effect=runtime or (lambda: 'a'*64)))
        stack.enter_context(patch.object(prepare, 'collect', side_effect=self.fake_build))
        return stack

    def run_prepare(self, **kwargs):
        return prepare.prepare_images(self.source, hashlib.sha256(self.source.read_bytes()).hexdigest(),
                                      self.builder, hashlib.sha256(self.builder.read_bytes()).hexdigest(), cache=self.cache, **kwargs)

    def test_unchanged_inputs_reuse_images_and_changes_invalidate_identity(self):
        with self.context():
            first, second = self.run_prepare(), self.run_prepare()
            self.assertEqual(len(self.calls), 2)
            self.assertTrue(all(not row['reused'] for row in first['images'].values()))
            self.assertTrue(all(row['reused'] for row in second['images'].values()))
            self.source.write_bytes(b'changed synthetic source')
            third = self.run_prepare()
            self.assertEqual(len(self.calls), 4)
            self.assertNotEqual(first['images']['offline']['path'], third['images']['offline']['path'])
            self.builder.write_bytes(b'changed synthetic builder')
            self.run_prepare()
            self.assertEqual(len(self.calls), 6)
        with self.context(runtime=lambda: 'b'*64):
            self.run_prepare()
            self.assertEqual(len(self.calls), 8)

    def test_native_runtime_digest_invalidates_connected_image_and_is_checked_before_build(self):
        binary=self.root/'claude'
        binary.write_bytes(b'synthetic native binary')
        digest=hashlib.sha256(binary.read_bytes()).hexdigest()
        with self.context():
            base=self.run_prepare()
            native=self.run_prepare(claude=binary,claude_sha=digest)
            self.assertEqual(base['images']['offline']['path'],native['images']['offline']['path'])
            self.assertNotEqual(base['images']['connected']['path'],native['images']['connected']['path'])
            self.assertEqual(len(self.calls),3)
            self.assertEqual(self.calls[-1][-2].split('/')[-1],'claude')
            self.assertEqual(self.calls[-1][-1],digest)
            binary.write_bytes(b'replaced bytes')
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                self.run_prepare(claude=binary,claude_sha=digest)
            self.assertEqual(len(self.calls),3)

    def test_corrupt_image_or_identity_refuses_reuse_instead_of_silently_rebuilding(self):
        with self.context():
            images = self.run_prepare()['images']
            entry = Path(images['offline']['path'])
            original = (entry/'initrd.cpio.gz').read_bytes()
            (entry/'initrd.cpio.gz').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                self.run_prepare()
            self.assertEqual(len(self.calls), 2)
            (entry/'initrd.cpio.gz').write_bytes(original)
            metadata = json.loads((entry/'image.json').read_text())
            metadata['source_sha256'] = '0'*64
            (entry/'image.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.run_prepare()

    def test_failed_build_or_concurrent_os_change_never_publishes_partial_images(self):
        with self.context(), patch.object(prepare, 'collect', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.run_prepare()
        self.assertEqual([p.name for p in self.cache.iterdir()], ['.lock'])
        values = iter(['a'*64, 'b'*64])
        with self.context(runtime=lambda: next(values)):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                self.run_prepare()
        self.assertEqual([p.name for p in self.cache.iterdir()], ['.lock'])

    def test_runtime_fingerprint_tracks_bytes_permissions_and_link_targets(self):
        runtime = self.root/'runtime'
        runtime.mkdir()
        target = runtime/'library'
        target.write_bytes(b'first')
        a = prepare.runtime_digest([runtime])
        target.write_bytes(b'second')
        b = prepare.runtime_digest([runtime])
        self.assertNotEqual(a, b)
        target.chmod(0o600)
        self.assertNotEqual(b, prepare.runtime_digest([runtime]))
        executable = self.root/'exe'
        executable.symlink_to(target)
        before = prepare.runtime_digest([executable])
        target.write_bytes(b'updated executable')
        self.assertNotEqual(before, prepare.runtime_digest([executable]))

    def test_cached_images_are_not_accepted_from_an_arbitrary_path(self):
        value = dict(source_sha256='a'*64, builder_sha256='b'*64, runtime_sha256='c'*64,
                     stale_directories_removed=0, images={label:dict(path='/var/lib/code-reviewer/images/'+'d'*64,
                        sha256='e'*64, reused=True) for label in ('offline','connected')})
        check.prepared_images(json.dumps(value), 'a'*64, 'b'*64)
        value['images']['offline']['path'] = '/tmp/untrusted'
        with self.assertRaises(ValueError):
            check.prepared_images(json.dumps(value), 'a'*64, 'b'*64)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_cleanup_only_matching_owned_directories_and_never_follows_links(self):
        stale = self.root/('reviewer-check-'+'a'*32)
        stale.mkdir()
        current = self.root/('reviewer-check-'+'b'*32)
        current.mkdir()
        unrelated = self.root/'personal-files'
        unrelated.mkdir()
        (unrelated/'canary').write_text('preserve')
        (stale/'outside-link').symlink_to(unrelated, target_is_directory=True)
        self.assertEqual(recover.cleanup_stale(self.root, os.geteuid(), keep=current), 1)
        self.assertTrue((unrelated/'canary').exists())
        self.assertTrue(current.exists())
        (self.root/('reviewer-job-'+'a'*8)).symlink_to(unrelated, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unsafe stale'):
            recover.cleanup_stale(self.root, os.geteuid(), keep=current)
        self.assertTrue((unrelated/'canary').exists())

    def test_recovery_refuses_live_services_mounts_or_network_state(self):
        with patch.object(recover.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='reviewer-vm-synthetic.service')):
            with self.assertRaisesRegex(ValueError, 'services'):
                recover.verify_idle()
        with patch.object(recover.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')), \
             patch.object(Path, 'read_text', return_value='1 2 3:4 / /tmp/reviewer-job-synthetic rw - tmpfs tmpfs rw'):
            with self.assertRaisesRegex(ValueError, 'mount'):
                recover.verify_idle()
        with patch.object(recover.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=0)]), \
             patch.object(Path, 'read_text', return_value=''):
            with self.assertRaisesRegex(ValueError, 'network'):
                recover.verify_idle()

    def test_recovery_restarts_only_the_selected_dedicated_worker_under_its_lock(self):
        (self.root/'synthetic-worker').mkdir()
        worker = check.Worker(self.root, 'synthetic-worker')
        from tests.test_sandbox_check import profile
        calls = []
        with patch.object(worker, 'state', side_effect=[dict(profile(), status='Running'), profile(), profile()]), \
             patch.object(worker, 'call', side_effect=lambda *args, **kwargs: calls.append(args)):
            with worker.session(recover=True):
                pass
        self.assertEqual([args[0] for args in calls], ['stop','start','stop'])
