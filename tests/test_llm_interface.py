"""Provider/authentication contract and host/VM selection, without real credentials."""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from code_reviewer import cli
from code_reviewer.backends import factory
from code_reviewer.backends.claude import ClaudeRunner
from code_reviewer.backends.contract import LLMResult, RunnerError
from code_reviewer.backends.openai import OpenAIRunner
from code_reviewer.config import Config, ConfigError, REPO_ROOT, load_config
from code_reviewer.review_options import apply_run_options
from code_reviewer.sandbox import settings
from code_reviewer.security import readiness
from code_reviewer.shared.stats import Stats


class LLMInterfaceTests(unittest.TestCase):
    def test_provider_defaults_follow_cli_selection_without_mutating_original(self):
        original = Config()
        roles = ('reviewer', 'comparator', 'resolver', 'verifier')
        selected = apply_run_options(original, cli.build_parser().parse_args([
            'review', 'example/project#7', '--provider', 'openai', '--auth', 'subscription']))
        self.assertEqual([getattr(selected, 'model_' + r) for r in roles], ['gpt-6-luna'] * 4)
        self.assertEqual([getattr(original, 'model_' + r) for r in roles], ['haiku'] * 4)
        self.assertEqual(factory.configuration_errors(selected), [])
        self.assertEqual(Config(provider='openai').model_reviewer, 'gpt-6-luna')
        back = apply_run_options(selected, cli.build_parser().parse_args([
            'review', 'example/project#7', '--provider', 'claude']))
        self.assertEqual([getattr(back, 'model_' + r) for r in roles], ['haiku'] * 4)

    def test_explicit_model_overrides_are_not_translated_between_providers(self):
        cfg = Config(model_reviewer='haiku', model_comparator='haiku', model_resolver='haiku', model_verifier='haiku')
        selected = apply_run_options(cfg, cli.build_parser().parse_args([
            'review', 'example/project#7', '--provider', 'openai', '--auth', 'subscription']))
        self.assertEqual(selected.model_reviewer, 'haiku')
        self.assertTrue(factory.configuration_errors(selected))
        override = apply_run_options(selected, cli.build_parser().parse_args([
            'review', 'example/project#7', '--model', 'synthetic-model']))
        self.assertEqual([getattr(override, 'model_' + r) for r in ('reviewer', 'comparator', 'resolver', 'verifier')],
                         ['synthetic-model'] * 4)

    def test_loaded_provider_defaults_and_explicit_models_survive_vm_serialization(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'review.toml'
            for explicit in (False, True):
                text = 'provider = "openai"\nauth_mode = "subscription"\n'
                if explicit:
                    text += ''.join('model_%s = "synthetic-model"\n' % role
                                    for role in ('reviewer', 'comparator', 'resolver', 'verifier'))
                path.write_text(text)
                path.chmod(0o600)
                cfg = load_config(str(path))
                expected = 'synthetic-model' if explicit else 'gpt-6-luna'
                self.assertEqual(cfg.model_reviewer, expected)
                # Cross the real JSON boundary: the guest receives resolved strings.
                import json
                job = json.loads(json.dumps(settings.job_settings(cfg)))
                clone = settings.config_from_job(job, Path('/synthetic'))
                self.assertEqual(clone.model_reviewer, expected)
                self.assertEqual(factory.configuration_errors(clone), [])
                self.assertEqual(cfg.to_dict()['model_verifier'], expected)

    def api_config(self):
        return Config(provider='openai',auth_mode='api-key',model_reviewer='synthetic-model',
                      model_comparator='synthetic-model',model_resolver='synthetic-model',
                      model_verifier='synthetic-model',openai_input_price=1,
                      openai_cached_input_price=.5,openai_output_price=2)

    def test_flow_and_shared_code_import_neutral_contract_not_provider_implementation(self):
        root=REPO_ROOT/'src/code_reviewer'
        for folder in ('review','shared'):
            self.assertTrue(list((root/folder).rglob('*.py')))
            for path in (root/folder).rglob('*.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node,ast.ImportFrom):
                        self.assertNotIn(node.module,('backends.claude','backends.openai','claude_cli'),str(path))
        self.assertFalse((root/'shared/claude_cli.py').exists())
        for name in ('models','templates','roles','git_ops','claude_cli'):
            self.assertFalse((root/(name+'.py')).exists())

    def test_both_factories_return_adapters_with_shared_result_contract(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'synthetic-api','ANTHROPIC_API_KEY':'synthetic-native'},clear=True):
            self.assertIsInstance(factory.create_runner(self.api_config()),OpenAIRunner)
            self.assertIsInstance(factory.create_runner(Config()),ClaudeRunner)
        result=LLMResult(ok=False,error='synthetic',billing_mode='subscription')
        stats=Stats()
        stats.record('reviewer',1,result,1,'synthetic-model')
        self.assertEqual(stats.calls[0]['billing_mode'],'subscription')
        self.assertFalse(issubclass(RunnerError,ConfigError))

    def test_subscription_strips_api_and_gateway_credentials_from_native_environment(self):
        cfg=Config(auth_mode='subscription')
        with patch.dict(os.environ,{'PATH':'/usr/bin','HOME':'/synthetic-home',
              'CLAUDE_CODE_OAUTH_TOKEN':'synthetic-subscription','ANTHROPIC_API_KEY':'unselected-api',
              'ANTHROPIC_AUTH_TOKEN':'unselected-gateway','OPENAI_API_KEY':'unselected-other',
              'NODE_OPTIONS':'unselected-loader','CLAUDE_CODE_USE_BEDROCK':'1'},clear=True):
            runner=factory.create_runner(cfg)
        self.assertEqual(runner.env,{'PATH':'/usr/bin','HOME':'/synthetic-home',
                                    'CLAUDE_CODE_OAUTH_TOKEN':'synthetic-subscription'})
        self.assertEqual(runner.billing_mode,'subscription')

    def test_missing_selected_credential_never_uses_another_billing_mode(self):
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'synthetic-unselected'},clear=True):
            with self.assertRaisesRegex(ConfigError,'CLAUDE_CODE_OAUTH_TOKEN'):
                factory.create_runner(Config(auth_mode='subscription'))
        with patch.dict(os.environ,{'CLAUDE_CODE_OAUTH_TOKEN':'synthetic-unselected'},clear=True):
            with self.assertRaisesRegex(ConfigError,'ANTHROPIC_API_KEY'):
                factory.create_runner(Config(auth_mode='api-key'))

    def test_guest_credentials_use_same_factory_without_host_environment_reads(self):
        cfg=Config(auth_mode='subscription')
        with patch.object(factory,'credential',side_effect=AssertionError('host credentials must not be read')):
            runner=factory.create_runner(cfg,selected_credential='synthetic-pipe-token',
                     env={'HOME':'/work/home','HTTPS_PROXY':'http://192.0.2.1:3128','ANTHROPIC_API_KEY':'unselected'})
        self.assertEqual(runner.env['CLAUDE_CODE_OAUTH_TOKEN'],'synthetic-pipe-token')
        self.assertNotIn('ANTHROPIC_API_KEY',runner.env)
        self.assertEqual(runner.env['HTTPS_PROXY'],'http://192.0.2.1:3128')

    def test_chatgpt_subscription_preflight_stays_offline_without_requiring_api_prices(self):
        cfg=replace(self.api_config(),auth_mode='subscription',sandbox=False,
                    openai_input_price=0,openai_cached_input_price=0,openai_output_price=0)
        with patch.object(factory,'credential',side_effect=AssertionError('no credentials')):
            report=readiness(cfg)
            self.assertTrue(report['ready'])
        self.assertFalse(report['runtime_checked'])
        self.assertEqual(report['auth_mode'],'subscription')

    def test_cli_selection_is_independent_and_does_not_mutate_operator_config(self):
        cfg=Config()
        args=cli.build_parser().parse_args(['review','example/project#7','--no-sandbox','--auth','subscription'])
        selected=apply_run_options(cfg,args)
        self.assertEqual(selected.auth_mode,'subscription')
        self.assertEqual(selected.provider,'claude')
        self.assertFalse(selected.sandbox)
        self.assertEqual(cfg.auth_mode,'api-key')
        for bad in ('oauth','auto',True):
            with self.assertRaises(ConfigError):
                replace(cfg,auth_mode=bad).validate()

    def test_vm_roundtrip_refuses_mismatched_authentication_envelope(self):
        cfg=Config(auth_mode='subscription')
        raw=settings.job_settings(cfg)
        clone=settings.config_from_job(raw,Path('/synthetic'))
        self.assertEqual(clone.auth_mode,'subscription')
        raw['auth']='api-key'
        with self.assertRaisesRegex(ValueError,'does not match'):
            settings.config_from_job(raw,Path('/synthetic'))

    def test_removed_vm_auth_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'review.toml'
            for text in ('vm_claude_auth = "oauth"\n',
                         'vm_claude_auth = "oauth"\nauth_mode = "api-key"\n'):
                path.write_text(text)
                path.chmod(0o600)
                with self.assertRaisesRegex(ConfigError, 'unknown config key: vm_claude_auth'):
                    load_config(str(path))
