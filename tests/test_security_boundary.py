"""Production frontdoors must refuse before any target parsing or credential use."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_reviewer import cli, config, security
from code_reviewer.config import Config, ConfigError
from code_reviewer.repl import Session
from code_reviewer.shared.models import ReviewRefused
from code_reviewer.sandbox import connected
from tests.helpers import FakeRunner


class BoundaryTests(unittest.TestCase):
    def test_every_provider_and_mode_refuses_before_git_models_credentials_or_posting(self):
        for provider in ('claude', 'openai'):
            for mode in ('single-agent', 'agent-personas'):
                for flags in ([], ['--dry-run'], ['--post']):
                    with self.subTest(provider=provider, mode=mode, flags=flags):
                        err = io.StringIO()
                        with patch.object(cli, 'load_config', return_value=Config()), \
                                patch('subprocess.run', side_effect=AssertionError('must not spawn a command')), \
                                patch('getpass.getpass', side_effect=AssertionError('must not read a key')), \
                                patch.object(cli, 'post_comment', side_effect=AssertionError('must not post')), \
                                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                            code = cli.main(['review', 'example/project#1', '--provider', provider, '--mode', mode] + flags)
                        self.assertEqual(code, 1)
                        self.assertIn('VM review unavailable', err.getvalue())

    def test_preflight_is_real_shared_frontend_output_with_no_target_or_model_io(self):
        for provider in ('claude', 'openai'):
            class TerminalOutput(io.StringIO):
                def isatty(self):
                    return True
            out = TerminalOutput()
            cfg = Config()
            with patch.object(cli, 'load_config', return_value=cfg), \
                    patch('subprocess.run', side_effect=AssertionError('no Git/model/version')), \
                    contextlib.redirect_stdout(out):
                code = cli.main(['review', 'example/project#1', '--provider', provider, '--mode', 'agent-personas',
                                 '--passes', '2', '--parallel', '1', '--preflight'])
            report = json.loads(out.getvalue())
            self.assertEqual(code, 1)
            self.assertFalse(report['ready'])
            self.assertEqual(report['provider'], provider)
            self.assertEqual(report['request']['passes'], 2)
            self.assertEqual(report['request']['parallel'], 1)
            self.assertIn('vm-configuration', report['blockers'])
            self.assertFalse(report['qualified'])
            self.assertEqual(cfg.provider, 'claude')

    def test_repl_start_and_review_do_not_inspect_local_git(self):
        for provider in ('claude', 'openai'):
            commands = iter(['review example/project#1 --preflight', 'review example/project#1', 'quit'])
            rows = []
            session = Session(Config(provider=provider), FakeRunner([]), out=rows.append, ask=lambda p: next(commands))
            with patch('subprocess.run', side_effect=AssertionError('no Git/model')):
                session.loop()
            self.assertIn('VM review unavailable', '\n'.join(rows))
            self.assertIsNone(session.result)
            self.assertEqual(session.runner.calls, [])

    def test_preflight_post_and_dry_run_combinations_cannot_escape_the_gate(self):
        for flags in (['--preflight', '--post'], ['--preflight', '--dry-run']):
            with patch.object(cli, 'load_config', return_value=Config()), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    result = cli.main(['review', 'example/project#1'] + flags)
                except SystemExit as exc:
                    result = exc.code
            self.assertNotEqual(result, 0)

    def test_qualifying_status_rows_alone_cannot_reactivate_legacy_host_dispatch(self):
        with patch('code_reviewer.security.require_ready'), patch('subprocess.run', side_effect=AssertionError('no host command')), \
                patch.object(cli, 'load_config', return_value=Config()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['review', 'example/project#1']), 1)

    def test_operator_cannot_certify_missing_controls_or_enable_an_unknown_provider(self):
        with self.assertRaises(ReviewRefused):
            security.require_ready('openai')
        with self.assertRaises(ReviewRefused):
            security.readiness('arbitrary-provider')
        with self.assertRaises(ConfigError):
            Config(provider='arbitrary-provider').validate()

    def test_low_level_connected_entry_cannot_bypass_live_release_gate(self):
        settings = dict(model='synthetic-model', prices=[1, .5, 2], mode='single-agent', passes=1,
                        parallel=1, budget=3, call_budget=.5, probe_only=False)
        with patch.object(connected, 'regular', side_effect=AssertionError('no input access')), \
                patch('getpass.getpass', side_effect=AssertionError('no key')):
            with self.assertRaisesRegex(ReviewRefused, 'VM review unavailable'):
                connected.run(None, None, None, None, None, settings)

    def test_doctor_live_cannot_call_the_legacy_host_model(self):
        with patch.object(cli, 'load_config', return_value=Config()), \
                patch('subprocess.run', side_effect=AssertionError('no diagnostic host command')), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['doctor', '--live']), 1)
            self.assertEqual(cli.main(['doctor']), 1)

    def test_retained_replay_cannot_launch_models_outside_the_boundary(self):
        from code_reviewer import replay
        with patch('subprocess.run', side_effect=AssertionError('no external process')), \
                patch('pathlib.Path.read_text', side_effect=AssertionError('no target/saved-run reads')), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(replay.main(['/missing/saved-run']), 1)

    def test_nonfinite_operator_budgets_are_rejected(self):
        for value in (float('nan'), float('inf'), True, 0, 10 ** 10000):
            with self.assertRaises(ConfigError):
                Config(run_budget_usd=value).validate()


class OperatorConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / 'operator'
        self.home.mkdir()
        self.target = self.root / 'target'
        self.target.mkdir()
        (self.target / '.git').mkdir()

    def test_current_directory_configuration_is_not_discovered(self):
        (self.target / 'review.toml').write_text('claude_bin = "/must/not/run"\n')
        with patch.object(config, 'DATA_DIR', self.home), patch.object(Path, 'cwd', return_value=self.target):
            self.assertEqual(config.load_config(), Config())

    def test_configuration_cannot_assert_security_readiness(self):
        path = self.home / 'review.toml'
        path.write_text('security_ready = true\n')
        with patch.object(config, '_read_toml', return_value={'security_ready': True}):
            with self.assertRaisesRegex(ConfigError, 'unknown config key'):
                config.load_config(str(path))

    def test_trusted_config_bytes_have_bounded_pinned_regular_file_opens(self):
        path = self.home / 'review.toml'
        path.write_text('model_reviewer = "synthetic-model"\n')
        path.chmod(0o600)
        self.assertEqual(config._operator_bytes(path), path.read_bytes())
        path.chmod(0o666)
        with self.assertRaises(ConfigError):
            config._operator_bytes(path)

    def test_target_config_and_linked_files_are_rejected_even_when_explicit(self):
        path = self.target / 'review.toml'
        path.write_text('claude_bin = "/must/not/run"\n')
        with self.assertRaisesRegex(ConfigError, 'outside Git'):
            config._operator_bytes(path)
        link = self.home / 'review.toml'
        link.symlink_to(path)
        with self.assertRaisesRegex(ConfigError, 'symlink'):
            config._operator_bytes(link)
        link.unlink()
        os.link(path, link)
        with self.assertRaises(ConfigError):
            config._operator_bytes(link)

    def test_final_component_is_not_resolved_after_a_symlink_swap(self):
        real = self.home / 'other.toml'
        real.write_text('model_reviewer = "unexpected"\n')
        link = self.home / 'review.toml'
        link.symlink_to(real)
        # Simulate a swap after the preliminary diagnostic check. The final
        # descriptor open must still refuse rather than resolve the link.
        with patch.object(Path, 'is_symlink', return_value=False):
            with self.assertRaises(OSError):
                config._operator_bytes(link)

    def test_fifo_and_oversized_config_fail_without_blocking(self):
        path = self.home / 'review.toml'
        os.mkfifo(path)
        with self.assertRaises(ConfigError):
            config._operator_bytes(path)
        path.unlink()
        path.write_bytes(b'x' * 65537)
        with self.assertRaises(ConfigError):
            config._operator_bytes(path)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / 'runtime'
        self.source.write_bytes(b'approved runtime')

    def artifact(self, limit=100):
        from code_reviewer.sandbox.artifacts import Artifact
        return Artifact(self.source, hashlib.sha256(b'approved runtime').hexdigest(), limit)

    def test_the_staged_bytes_are_hashed_and_previous_destinations_are_preserved(self):
        target = self.root / 'staged'
        self.artifact().stage(target)
        self.assertEqual(target.read_bytes(), b'approved runtime')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            self.artifact().stage(target)
        self.assertEqual(target.read_bytes(), b'approved runtime')

    def test_source_change_after_selection_is_rejected_without_partial_output(self):
        artifact = self.artifact()
        self.source.write_bytes(b'replaced runtime')
        target = self.root / 'staged'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            artifact.stage(target)
        self.assertFalse(target.exists())

    def test_links_special_files_and_oversized_artifacts_refuse(self):
        self.source.unlink()
        os.mkfifo(self.source)
        with self.assertRaises(ValueError):
            self.artifact().stage(self.root / 'staged')
        self.source.unlink()
        self.source.symlink_to(self.root / 'missing')
        with self.assertRaises(OSError):
            self.artifact().stage(self.root / 'staged')
        self.source.unlink()
        self.source.write_bytes(b'approved runtime')
        os.link(self.source, self.root / 'hardlink')
        with self.assertRaises(ValueError):
            self.artifact().stage(self.root / 'staged')
        (self.root / 'hardlink').unlink()
        with self.assertRaises(ValueError):
            self.artifact(limit=1).stage(self.root / 'staged')
        self.assertFalse((self.root / 'staged').exists())
