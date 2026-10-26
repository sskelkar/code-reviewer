"""Offline packaging, result validation, and bounded process lifecycle for the VM prototype."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_reviewer.sandbox.bundle import build_bundle
from code_reviewer.sandbox.launch import boot_config, collect, parse_result, verify_file
from code_reviewer.sandbox.smoke import RESULT_PREFIX, run_smoke
from code_reviewer.sandbox import runtime_probe


class SandboxPrototypeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_bundle_is_repeatable_and_excludes_non_source_material(self):
        source = self.root / 'src/code_reviewer'
        source.mkdir(parents=True)
        (source / 'a.py').write_text('pass\n')
        (source / 'rule.md').write_text('Synthetic rule\n')
        (source / '.env').write_text('synthetic denied input')
        (self.root / 'review.toml').write_text('claude_bin="not included"')
        a, b = self.root / 'a.tar', self.root / 'b.tar'
        self.assertEqual(build_bundle(self.root, a), build_bundle(self.root, b))
        with tarfile.open(a) as archive:
            names = archive.getnames()
            self.assertEqual(names, ['src/code_reviewer/a.py', 'src/code_reviewer/rule.md', 'bundle-manifest.json'])
            manifest = json.load(archive.extractfile('bundle-manifest.json'))
            self.assertEqual(manifest['files']['src/code_reviewer/a.py'], hashlib.sha256(b'pass\n').hexdigest())
        with self.assertRaises(FileExistsError):
            build_bundle(self.root, a)

    @unittest.skipUnless(os.name == 'posix', 'symlink support')
    def test_bundle_refuses_links_and_oversize_sources(self):
        source = self.root / 'src/code_reviewer'
        source.mkdir(parents=True)
        (source / 'x.py').symlink_to('/not/a/source/file')
        with self.assertRaises(ValueError):
            build_bundle(self.root, self.root / 'out.tar')
        (source / 'x.py').unlink()
        (source / 'x.py').write_bytes(b'12345')
        with patch('code_reviewer.sandbox.bundle.MAX_FILE_BYTES', 4):
            with self.assertRaises(ValueError):
                build_bundle(self.root, self.root / 'out.tar')
        self.assertFalse((self.root / 'out.tar').exists())

    def test_artifacts_are_digest_checked_and_boot_has_no_disks_or_network(self):
        path = self.root / 'artifact'
        path.write_bytes(b'synthetic')
        digest = hashlib.sha256(b'synthetic').hexdigest()
        self.assertEqual(verify_file(path, digest, 32), path.resolve())
        for expected, size in (('0' * 64, 32), (digest, 2), ('BAD', 32)):
            with self.assertRaises(ValueError):
                verify_file(path, expected, size)
        config = boot_config(path, path)
        self.assertEqual(config['drives'], [])
        self.assertEqual(config['network-interfaces'], [])
        self.assertEqual(config['machine-config']['mem_size_mib'], 1024)
        self.assertEqual(boot_config(path, path, runtime=True)['machine-config']['mem_size_mib'], 4096)

    def test_both_real_flows_work_with_the_fake_headless_executable(self):
        result = run_smoke()
        self.assertEqual(result['flows']['agent-personas']['calls'], 8)
        self.assertEqual(result['flows']['single-agent']['calls'], 3)
        text = 'synthetic boot noise\n' + RESULT_PREFIX + json.dumps(result) + '\n'
        self.assertEqual(parse_result(text), result)
        with self.assertRaises(ValueError):
            parse_result(text + text)
        result['flows']['agent-personas']['calls'] = True
        with self.assertRaises(ValueError):
            parse_result(RESULT_PREFIX + json.dumps(result))
        result['flows']['agent-personas']['calls'] = 8
        result['flows']['agent-personas']['completed'] = 0
        with self.assertRaises(ValueError):
            parse_result(RESULT_PREFIX + json.dumps(result))
        with self.assertRaises(ValueError):
            parse_result(RESULT_PREFIX + '[]')

    @unittest.skipUnless(os.name == 'posix', 'process group support')
    def test_timeout_kills_child_processes_holding_pipes(self):
        pidfile = self.root / 'pid'
        program = ('import os,subprocess,sys,time;from pathlib import Path;'
                   'Path(sys.argv[1]).write_text(str(os.getpid()));'
                   'subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);time.sleep(30)')
        with self.assertRaises(TimeoutError):
            collect([sys.executable, '-c', program, str(pidfile)], self.root, .5)
        pid = int(pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    @unittest.skipUnless(os.name == 'posix', 'process group support')
    def test_output_limit_is_enforced_before_unbounded_buffering(self):
        with patch('code_reviewer.sandbox.launch.MAX_OUTPUT_BYTES', 1024):
            with self.assertRaisesRegex(ValueError, 'output limit'):
                collect([sys.executable, '-c', 'import sys;sys.stdout.write("x"*100000)'], self.root, 5)

    def test_runtime_probe_uses_fresh_environment_and_no_credentials(self):
        calls = []
        def fake_collect(cmd, cwd, timeout, **kwargs):
            calls.append((cmd, kwargs))
            env = kwargs['env']
            self.assertNotIn('ANTHROPIC_API_KEY', env)
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env)
            self.assertEqual(env['DISABLE_AUTOUPDATER'], '1')
            self.assertTrue(Path(env['HOME']).is_dir())
            if cmd[1] == '--version':
                return runtime_probe.VERSION
            if cmd[1] == '--help':
                return '--strict-mcp-config --disable-slash-commands --no-session-persistence'
            if cmd[1] == 'auth':
                return '{"loggedIn":false,"authMethod":"none"}'
            return '{"error":"Not logged in"}'
        with patch('code_reviewer.sandbox.launch.collect', side_effect=fake_collect):
            result = runtime_probe.run_probe(self.root / 'fake-cli')
        self.assertEqual(len(calls), 4)
        self.assertIn('--setting-sources', calls[-1][0])
        self.assertIn('--safe-mode', calls[-1][0])
        self.assertIn('--restricted', calls[-1][0])
        self.assertFalse(Path(calls[0][1]['env']['HOME']).exists())
        self.assertEqual(runtime_probe.parse_result(runtime_probe.RESULT_PREFIX + json.dumps(result)), result)
        result['authenticated'] = True
        with self.assertRaises(ValueError):
            runtime_probe.parse_result(runtime_probe.RESULT_PREFIX + json.dumps(result))

    def test_runtime_probe_refuses_preexisting_authentication(self):
        def fake_collect(cmd, *args, **kwargs):
            if cmd[1] == '--version':
                return runtime_probe.VERSION
            if cmd[1] == '--help':
                return '--strict-mcp-config --disable-slash-commands --no-session-persistence'
            return '{"loggedIn":true,"authMethod":"oauth_token"}'
        with patch('code_reviewer.sandbox.launch.collect', side_effect=fake_collect):
            with self.assertRaisesRegex(ValueError, 'must not have credentials'):
                runtime_probe.run_probe(self.root / 'fake-cli')
