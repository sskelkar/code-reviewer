"""Explicit host dispatch uses the actual CLI/core without the presentation test seam."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from code_reviewer import cli
from code_reviewer.backends import factory
from code_reviewer.backends.openai import OpenAIRunner
from code_reviewer.config import Config, ConfigError
from code_reviewer.engine import Engine
from code_reviewer.review_options import apply_run_options
from .helpers import FakeRunner, make_repo


class HostExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(runs_dir=str(self.root/'runs'), criteria_mode='defaults_only',
                          openai_input_price=1, openai_cached_input_price=.5, openai_output_price=2)

    def invoke(self, cfg, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'load_config', return_value=cfg), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(['review', 'example/project#1', *args])
        return status, out.getvalue(), err.getvalue()

    def test_run_directories_are_private_under_a_permissive_umask(self):
        import os
        from code_reviewer.shared.run_store import RunStore
        from tests.helpers import make_repo
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            ctx=make_repo(root)
            mask=os.umask(0)
            try:
                store=RunStore.create(root/'runs',ctx)
                from code_reviewer.shared import git_ops
                with patch.object(git_ops, '_run'):
                    git_ops.ensure_cache_clone(git_ops.PRRef('github.com','example','project',1), root/'cache')
                store.dump('config.json',{'synthetic':True})
                passes=store.pass_dir(1)
            finally:
                os.umask(mask)
            for directory in (store.root,store.input_dir,passes,root/'cache'):
                self.assertEqual(directory.stat().st_mode & 0o777,0o700)

    def test_real_native_adapter_reaches_both_host_flows_through_cli(self):
        import sys
        ctx=make_repo(self.root)
        binary=self.root/'fake-native'
        binary.write_text('#!'+sys.executable+'\nimport json\n'
                          'print(json.dumps({"type":"result","structured_output":{"findings":[]},"total_cost_usd":0.001}))\n')
        binary.chmod(0o700)
        for mode in ('single-agent','agent-personas'):
            cfg=replace(self.cfg,claude_bin=str(binary),review_mode=mode)
            with patch.object(Engine,'prepare',return_value=ctx):
                status,out,err=self.invoke(cfg,'--no-sandbox','--passes','1')
            self.assertEqual(status,0,(mode,err))
            self.assertIn('full report:',out)

    def test_invalid_host_manifest_is_a_configuration_error_instead_of_traceback(self):
        ctx=make_repo(self.root)
        manifest=self.root/'manifest.json'
        for content in ('not-json','[]','{"criteria_files":7}'):
            manifest.write_text(content)
            cfg=replace(self.cfg,criteria_mode='merge',criteria_manifest=str(manifest))
            with patch.object(Engine,'prepare',return_value=ctx),patch.object(factory,'create_runner',return_value=FakeRunner([])):
                status,out,err=self.invoke(cfg,'--no-sandbox','--passes','1')
            self.assertEqual(status,1)
            self.assertIn('criteria_manifest',err)

    def test_default_vm_request_never_creates_host_runner_or_prepares_target(self):
        with patch.object(factory, 'create_runner', side_effect=AssertionError('no credentials/runner')), \
             patch.object(Engine, 'prepare', side_effect=AssertionError('no host acquisition')):
            status, _, err = self.invoke(self.cfg)
        self.assertEqual(status, 1)
        self.assertIn('VM review unavailable', err)

    def test_host_preflight_has_no_credentials_git_or_vm_activity(self):
        for provider in ('claude', 'openai'):
            args = ['--provider', provider, '--no-sandbox', '--preflight']
            if provider == 'openai':
                args += ['--model', 'synthetic-model']
            with patch.object(factory, 'create_runner', side_effect=AssertionError('no credentials')), \
                 patch.object(Engine, 'prepare', side_effect=AssertionError('no target')), \
                 patch('subprocess.run', side_effect=AssertionError('no process')):
                status, text, _ = self.invoke(self.cfg, *args)
            value = json.loads(text)
            self.assertEqual(status, 0)
            self.assertTrue(value['ready'])
            self.assertEqual(value['execution'], 'host')
            self.assertNotIn('isolated-acquisition', value['blockers'])
            self.assertTrue(self.cfg.sandbox)

    def test_host_cli_runs_both_providers_and_both_algorithms_with_fake_model_transport(self):
        ctx = make_repo(self.root)
        for provider in ('claude', 'openai'):
            for mode in ('single-agent', 'agent-personas'):
                with self.subTest(provider=provider, mode=mode):
                    calls = []
                    def transport(payload, token, timeout):
                        calls.append(payload)
                        history = payload['input']
                        if payload['tools'] and not any(i.get('type') == 'function_call_output' for i in history):
                            items = [dict(type='function_call', name='Read', call_id='read-fixture',
                                          arguments=json.dumps(dict(path='app.py', offset=1, limit=20)))]
                        else:
                            if payload['tools']:
                                self.assertIn('SELECT', json.loads(history[-1]['output'])['text'])
                            items = [dict(type='message', content=[dict(type='output_text', text='{"findings":[]}')])]
                        return dict(status='completed', usage=dict(input_tokens=100, output_tokens=20), output=items)
                    cfg = replace(self.cfg, runs_dir=str(self.root/(provider+'-'+mode)))
                    def api_runner(*args, **kwargs):
                        return OpenAIRunner(*args, **kwargs, transport=transport)
                    with patch.object(Engine, 'prepare', return_value=ctx), \
                         patch.object(factory, 'ClaudeRunner', return_value=FakeRunner(reviewer=[{'findings':[]}])) as native, \
                         patch.object(factory, 'OpenAIRunner', side_effect=api_runner) as api, \
                         patch.dict(os.environ, {'OPENAI_API_KEY':'synthetic-runtime-test-key'}):
                        args = ['--no-sandbox', '--provider', provider, '--mode', mode, '--passes', '1']
                        if provider == 'openai':
                            args += ['--model', 'synthetic-model']
                        status, text, err = self.invoke(cfg, *args)
                    self.assertEqual(status, 0, err)
                    self.assertIn('full report:', text)
                    self.assertEqual(native.call_count, int(provider == 'claude'))
                    self.assertEqual(api.call_count, int(provider == 'openai'))
                    stats_path = next(Path(cfg.runs_dir).glob('*/stats.json'))
                    stats = json.loads(stats_path.read_text())
                    self.assertEqual(len(stats['calls']), 4 if mode == 'agent-personas' else 1)
                    if provider == 'openai':
                        self.assertEqual(len(calls), 8 if mode == 'agent-personas' else 2)
                    for path in stats_path.parent.rglob('*'):
                        if path.is_file():
                            self.assertNotIn(b'synthetic-runtime-test-key', path.read_bytes())

    def test_host_dry_run_reads_target_without_runner_credentials_or_model_calls(self):
        ctx = make_repo(self.root)
        cfg = replace(self.cfg, openai_input_price=0, openai_cached_input_price=0, openai_output_price=0)
        with patch.object(Engine, 'prepare', return_value=ctx), \
             patch.object(factory, 'create_runner', side_effect=AssertionError('no credentials/model')):
            status, text, err = self.invoke(cfg, '--no-sandbox', '--dry-run', '--provider', 'openai', '--model', 'synthetic-model')
        self.assertEqual(status, 0, err)
        self.assertIn('nothing was sent to OpenAI', text)
        self.assertIn('THE HTTPS REQUEST', text)
        self.assertNotIn('--append-system-prompt', text)

    def test_invalid_api_configuration_or_missing_key_refuses_before_host_acquisition(self):
        with patch.object(Engine, 'prepare', side_effect=AssertionError('no target')):
            status, _, err = self.invoke(self.cfg, '--no-sandbox', '--provider', 'openai')
            self.assertEqual(status, 1)
            self.assertIn('explicit OpenAI model', err)
            with patch.dict(os.environ, {}, clear=True):
                status, _, err = self.invoke(self.cfg, '--no-sandbox', '--provider', 'openai', '--model', 'synthetic-model')
            self.assertEqual(status, 1)
            self.assertIn('OPENAI_API_KEY', err)

    def test_operator_can_select_host_in_config_and_cli_flag_does_not_mutate_it(self):
        cfg = replace(self.cfg, sandbox=False)
        status, text, _ = self.invoke(cfg, '--preflight')
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(text)['execution'], 'host')
        options = cli.build_parser().parse_args(['review', 'example/project#1', '--no-sandbox'])
        self.assertFalse(apply_run_options(self.cfg, options).sandbox)
        self.assertTrue(self.cfg.sandbox)
        for bad in (1, 'false', None):
            with self.assertRaises(ConfigError):
                replace(self.cfg, sandbox=bad).validate()

    def test_api_prices_and_role_model_settings_are_explicit_and_never_load_credentials(self):
        for value in (True, -1, float('inf'), float('nan')):
            with self.assertRaises(ConfigError):
                replace(self.cfg, openai_input_price=value).validate()
        cfg = replace(self.cfg, provider='openai', model_reviewer='synthetic-model')
        self.assertTrue(factory.configuration_errors(cfg))
        cfg = replace(cfg, model_resolver='synthetic-model', model_comparator='synthetic-model', model_verifier='synthetic-model')
        self.assertEqual(factory.configuration_errors(cfg), [])
        self.assertTrue(factory.configuration_errors(replace(cfg, openai_output_price=0)))
        self.assertEqual(factory.configuration_errors(replace(cfg, openai_output_price=0), dry_run=True), [])
