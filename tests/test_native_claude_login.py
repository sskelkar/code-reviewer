"""Native host login reuse through fake Claude executables; no credentials, network or models."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from code_reviewer import cli
from code_reviewer.backends import factory
from code_reviewer.config import Config, ConfigError
from code_reviewer.engine import Engine
from tests.helpers import make_repo


FAKE = '''#!{python}
import json,os,sys
from pathlib import Path
home=Path(os.environ['HOME'])
args=sys.argv[1:]
if '--version' in args:
 print('synthetic-native-login')
 sys.exit(0)
kind='status' if 'auth' in args else 'model'
with (home/'native-calls.jsonl').open('a') as out:
 out.write(json.dumps(dict(kind=kind,argv=args,cwd=os.getcwd(),
  config_dir=os.environ.get('CLAUDE_CONFIG_DIR'),
  credential_names=[key for key in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY',
   'CLAUDE_CODE_OAUTH_TOKEN','CLAUDE_CODE_USE_BEDROCK','NODE_OPTIONS') if key in os.environ]))+'\\n')
if kind=='status':
 # Model the native keychain lookup: a missing USER hides the saved login.
 print((home/'native-status.json').read_text() if os.environ.get('USER') else
  json.dumps(dict(loggedIn=False,authMethod='none')))
else:
 sys.stdin.read()
 print(json.dumps(dict(type='result',subtype='success',is_error=False,structured_output=dict(findings=[]),
  usage=dict(input_tokens=20,output_tokens=5),num_turns=1)))
'''


class NativeLoginTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.home=self.root/'home'
        self.home.mkdir()
        self.binary=self.root/'claude'
        self.binary.write_text(FAKE.format(python=sys.executable))
        self.binary.chmod(0o700)
        self.cfg=Config(tracing=False, sandbox=False, auth_mode='subscription', claude_bin=str(self.binary),
            criteria_mode='defaults_only', min_passes=1, max_passes=1, persona_passes=1,
            runs_dir=str(self.root/'runs'))
        self.set_status(dict(loggedIn=True,authMethod='claude.ai',email='PRIVATE_IDENTITY_CANARY'))
        environment=patch.dict(os.environ,dict(HOME=str(self.home),USER='synthetic-native-user',PATH='/usr/bin:/bin',
            CLAUDE_CONFIG_DIR=str(self.home/'work-claude'),ANTHROPIC_API_KEY='unselected-synthetic-api',
            ANTHROPIC_AUTH_TOKEN='unselected-synthetic-gateway',OPENAI_API_KEY='unselected-synthetic-openai',
            CLAUDE_CODE_USE_BEDROCK='1',NODE_OPTIONS='unselected-synthetic-loader'),clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def set_status(self,value):
        (self.home/'native-status.json').write_text(json.dumps(value))

    def calls(self):
        path=self.home/'native-calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_host_reuses_native_login_without_reading_or_exporting_tokens(self):
        with patch.object(factory,'credential',side_effect=AssertionError('no token read')):
            runner=factory.create_runner(self.cfg)
        result=runner.run(system_prompt='Synthetic rules', user_prompt='Synthetic input',
            schema={'type':'object'},model='haiku',tools='Read,Grep,Glob',cwd=self.root,
            budget_usd=.1,timeout_s=5)
        self.assertTrue(result.ok,result.error)
        self.assertEqual(result.billing_mode,'subscription')
        self.assertIsNone(result.cost_usd)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN',runner.env)
        rows=self.calls()
        self.assertEqual([r['kind'] for r in rows],['status','model'])
        self.assertNotEqual(rows[0]['cwd'],str(self.root))
        for row in rows:
            self.assertEqual(row['credential_names'],[])
            self.assertEqual(row['config_dir'],str(self.home/'work-claude'))
            self.assertIn('--safe-mode',row['argv'])
            self.assertIn('--restricted',row['argv'])
        self.assertNotIn('--max-budget-usd',rows[-1]['argv'])
        self.assertNotIn('PRIVATE_IDENTITY_CANARY',json.dumps(result.raw))

    def test_native_login_preserves_os_user_for_keychain_lookup(self):
        runner = factory.create_runner(self.cfg)
        self.assertEqual(runner.env['USER'], 'synthetic-native-user')
        self.assertNotIn('ANTHROPIC_API_KEY', runner.env)
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', runner.env)
        self.assertNotIn('OPENAI_API_KEY', runner.env)
        with patch.dict(os.environ, {}, clear=True):
            # A native status command without USER cannot locate this saved login.
            from code_reviewer.backends.claude import require_subscription_login
            with self.assertRaises(ConfigError):
                require_subscription_login(str(self.binary), {'HOME': str(self.home)}, [])
        self.assertTrue(all(row['kind'] == 'status' for row in self.calls()))

    def test_missing_non_subscription_or_malformed_login_fails_without_model_calls(self):
        cases=[dict(loggedIn=False,authMethod='none'),dict(loggedIn=1,authMethod='claude.ai'),[],
               *[dict(loggedIn=True,authMethod=method,email='PRIVATE_IDENTITY_CANARY')
                 for method in ('api_key','api_key_helper','third_party','none','unknown')]]
        for value in cases:
            with self.subTest(value=value):
                self.set_status(value)
                with self.assertRaises(ConfigError) as error:
                    factory.create_runner(self.cfg)
                self.assertIn('run claude',str(error.exception))
                self.assertNotIn('PRIVATE_IDENTITY_CANARY',str(error.exception))
        self.assertTrue(all(r['kind']=='status' for r in self.calls()))

    def test_explicit_token_wins_but_empty_token_does_not_fall_back(self):
        with patch.dict(os.environ,{'CLAUDE_CODE_OAUTH_TOKEN':'synthetic-explicit-token'}), \
                patch.object(factory,'require_subscription_login',side_effect=AssertionError('no native lookup')):
            runner=factory.create_runner(self.cfg)
        self.assertEqual(runner.env['CLAUDE_CODE_OAUTH_TOKEN'],'synthetic-explicit-token')
        with patch.dict(os.environ,{'CLAUDE_CODE_OAUTH_TOKEN':''}), \
                patch.object(factory,'require_subscription_login',side_effect=AssertionError('no fallback')):
            with self.assertRaises(ConfigError):
                factory.create_runner(self.cfg)
        self.assertEqual(self.calls(),[])

    def test_vm_and_api_key_modes_do_not_reuse_host_login(self):
        os.environ.pop('ANTHROPIC_API_KEY')
        for cfg in (replace(self.cfg,sandbox=True),replace(self.cfg,auth_mode='api-key')):
            with self.subTest(sandbox=cfg.sandbox,auth=cfg.auth_mode), \
                    patch.object(factory,'require_subscription_login',side_effect=AssertionError('no native fallback')):
                with self.assertRaises(ConfigError):
                    factory.create_runner(cfg)
        with patch.object(factory,'require_subscription_login',side_effect=AssertionError('no guest lookup')):
            guest=factory.create_runner(replace(self.cfg,sandbox=True),selected_credential='synthetic-guest-token')
        self.assertEqual(guest.env['CLAUDE_CODE_OAUTH_TOKEN'],'synthetic-guest-token')
        self.assertEqual(self.calls(),[])

    def test_auth_status_failures_have_fixed_errors_without_native_output(self):
        for error in (OSError('PRIVATE_IDENTITY_CANARY'),subprocess.TimeoutExpired('claude',15)):
            with patch('code_reviewer.backends.claude.subprocess.run',side_effect=error):
                with self.assertRaises(ConfigError) as caught:
                    factory.create_runner(self.cfg)
                self.assertNotIn('PRIVATE_IDENTITY_CANARY',str(caught.exception))

    def test_both_one_shot_flows_use_native_login_without_persisting_status_metadata(self):
        source=self.root/'source'
        source.mkdir()
        ctx=make_repo(source)
        for mode in ('single-agent','agent-personas'):
            cfg=replace(self.cfg,review_mode=mode,runs_dir=str(self.root/mode))
            out,err=io.StringIO(),io.StringIO()
            with patch.object(cli,'load_config',return_value=cfg),patch.object(Engine,'prepare',return_value=ctx), \
                    contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                code=cli.main(['review','example/project#1','--no-sandbox','--provider','claude',
                               '--auth','subscription','--mode',mode,'--passes','1'])
            self.assertEqual(code,0,err.getvalue())
            stats=json.loads(next(Path(cfg.runs_dir).glob('*/stats.json')).read_text())
            self.assertEqual(len(stats['calls']),4 if mode=='agent-personas' else 1)
            self.assertTrue(all(call['billing_mode']=='subscription' for call in stats['calls']))
            for path in Path(cfg.runs_dir).rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'PRIVATE_IDENTITY_CANARY',path.read_bytes())
        self.assertEqual(sum(row['kind']=='status' for row in self.calls()),2)

    def test_host_preflight_and_dry_run_do_not_check_native_login(self):
        source=self.root/'source'
        source.mkdir()
        ctx=make_repo(source)
        for option in ('--preflight','--dry-run'):
            with patch.object(cli,'load_config',return_value=self.cfg), \
                    patch.object(Engine,'prepare',return_value=ctx), \
                    patch.object(factory,'require_subscription_login',side_effect=AssertionError('no auth activity')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['review','example/project#1','--no-sandbox','--provider','claude',
                                           '--auth','subscription',option]),0)
        self.assertEqual(self.calls(),[])


if __name__=='__main__':
    unittest.main()
