"""Mode isolation, compatibility and unchanged single-agent assets, without real calls."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from code_reviewer import cli
from code_reviewer.config import Config, ConfigError, DEFAULT_CRITERIA_DIR, PROMPTS_DIR, REPO_ROOT
from code_reviewer.engine import Engine
from code_reviewer.flows.single_agent.flow import SingleAgentFlow
from code_reviewer.review_options import apply_run_options
from code_reviewer.repl import Session
from code_reviewer.shared import git_ops

from .helpers import FakeRunner, TempDirTestMixin, make_repo


class FlowSelectionTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.runner = FakeRunner(reviewer=[])

    def test_default_uses_only_single_agent(self):
        with patch('code_reviewer.flows.agent_personas.flow.AgentPersonasFlow') as personas:
            engine = Engine(Config(), self.runner)
        self.assertIsInstance(engine._flow, SingleAgentFlow)
        self.assertEqual(engine.cfg.review_mode, 'single-agent')
        self.assertIs(engine.inputs, engine._flow.inputs)
        self.assertEqual(self.runner.calls, [])
        personas.assert_not_called()

    def test_config_preserves_existing_positional_model_argument(self):
        cfg = Config('sonnet')
        cfg.validate()
        self.assertEqual(cfg.model_reviewer, 'sonnet')
        self.assertEqual(cfg.review_mode, 'single-agent')

    def test_personas_load_only_their_assets_without_preparing_git_or_creating_runs(self):
        self.runner.version = lambda: self.fail('must not call Claude version')
        cfg = Config(review_mode='agent-personas', runs_dir=str(self.tmp / 'runs'))
        with patch('code_reviewer.flows.single_agent.flow.SingleAgentFlow') as single, \
                patch('code_reviewer.flows.single_agent.prompt_contract.load_prompt', side_effect=AssertionError('single assets')), \
                patch.object(git_ops, '_run', side_effect=AssertionError('must not call git')):
            engine = Engine(cfg, self.runner)
        self.assertEqual(engine._flow.__class__.__name__, 'AgentPersonasFlow')
        self.assertEqual(engine.prompts_dir.parent.name, 'agent_personas')
        single.assert_not_called()
        self.assertFalse((self.tmp / 'runs').exists())
        self.assertEqual(self.runner.calls, [])

    def test_cli_persona_dry_run_dispatches_without_model_calls(self):
        out, err = io.StringIO(), io.StringIO()
        cfg = Config(runs_dir=str(self.tmp / 'runs'))
        ctx = make_repo(self.tmp)
        with patch.object(cli, 'load_config', return_value=cfg), \
                patch.object(cli, 'ClaudeRunner', return_value=self.runner), \
                patch.object(Engine, 'prepare', return_value=ctx), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['review', ctx.label, '--mode', 'agent-personas', '--dry-run'])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('agent-personas', out.getvalue())
        self.assertEqual(self.runner.calls, [])

    def test_repl_persona_dry_run_dispatches_without_model_calls(self):
        lines = []
        ctx = make_repo(self.tmp)
        session = Session(Config(runs_dir=str(self.tmp / 'runs')), self.runner, local_path=self.tmp, out=lines.append)
        with patch.object(Engine, 'prepare', return_value=ctx):
            self.assertTrue(session.dispatch('review ' + ctx.label + ' --mode agent-personas --dry-run'))
        self.assertIn('agent-personas', '\n'.join(lines))
        self.assertEqual(self.runner.calls, [])

    def test_persona_pass_override_reaches_both_frontends_without_mutating_config(self):
        ctx = make_repo(self.tmp)
        for mode in ('single-agent', 'agent-personas'):
            for flags, count in ((['--passes', '1', '--mode', 'agent-personas'], 1), (['--mode', 'agent-personas', '--passes', '4'], 4)):
                with self.subTest(config_mode=mode, flags=flags):
                    cfg = Config(review_mode=mode, persona_passes=2, runs_dir=str(self.tmp / 'runs'))
                    out, err, lines = io.StringIO(), io.StringIO(), []
                    with patch.object(cli, 'load_config', return_value=cfg), \
                            patch.object(cli, 'ClaudeRunner', return_value=self.runner), \
                            patch.object(Engine, 'prepare', return_value=ctx), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = cli.main(['review', ctx.label, '--dry-run'] + flags)
                        session = Session(cfg, self.runner, local_path=self.tmp, out=lines.append)
                        self.assertTrue(session.dispatch('review ' + ctx.label + ' --dry-run ' + ' '.join(flags)))
                        self.assertTrue(session.dispatch('help'))
                    self.assertEqual(code, 0, err.getvalue())
                    expected = '%d passes per persona (%d scheduled)' % (count, count * 4)
                    self.assertIn(expected, out.getvalue())
                    self.assertIn(expected, '\n'.join(lines))
                    self.assertEqual(cfg.persona_passes, 2)
                    self.assertIsNone(session.result)
        self.assertEqual(self.runner.calls, [])

    def test_persona_pass_config_is_loaded_and_rejects_invalid_types_and_values(self):
        from code_reviewer import config
        path = self.tmp / 'review.toml'
        path.write_text('persona_passes = 2\n')
        with patch.object(config, '_read_toml', return_value={'persona_passes': 2}):
            self.assertEqual(config.load_config(str(path)).persona_passes, 2)
        for value in (0, -1, True, '2', 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    Config(persona_passes=value).validate()
                with patch.object(config, '_read_toml', return_value={'persona_passes': value}):
                    with self.assertRaises(ConfigError):
                        config.load_config(str(path))

    def test_cli_mode_overrides_config_without_mutating_it(self):
        original = Config(review_mode='agent-personas')
        args = Namespace(mode='single-agent', passes=None, model=None, no_self_check=False)
        cfg = apply_run_options(original, args)
        self.assertEqual(original.review_mode, 'agent-personas')
        self.assertEqual(cfg.review_mode, 'single-agent')
        self.assertIsInstance(Engine(cfg, self.runner)._flow, SingleAgentFlow)

    def test_unknown_mode_is_never_a_fallback(self):
        with self.assertRaises(ConfigError):
            Config(review_mode='typo').validate()
        with self.assertRaises(ConfigError):
            Engine(Config(review_mode='typo'), self.runner)
        self.assertEqual(self.runner.calls, [])

    def test_templates_schemas_and_criteria_are_byte_identical_to_pre_refactor(self):
        hashes = json.loads((Path(__file__).parent / 'fixtures/single_agent_asset_hashes.json').read_text())
        for name, expected in hashes.items():
            kind, relative = name.split('/', 1)
            base = PROMPTS_DIR if kind == 'prompts' else DEFAULT_CRITERIA_DIR
            self.assertEqual(hashlib.sha256((base / relative).read_bytes()).hexdigest(), expected, name)
        self.assertEqual(PROMPTS_DIR.parent, DEFAULT_CRITERIA_DIR.parent)
        self.assertEqual(PROMPTS_DIR.parent.name, 'single_agent')

    def test_custom_asset_directories_still_reach_selected_flow(self):
        engine = Engine(Config(), self.runner, prompts_dir=PROMPTS_DIR, defaults_dir=DEFAULT_CRITERIA_DIR)
        self.assertEqual(engine.prompts_dir, PROMPTS_DIR)
        self.assertEqual(engine.defaults_dir, DEFAULT_CRITERIA_DIR)

    def test_legacy_imports_share_canonical_implementations_and_patch_targets(self):
        from code_reviewer import engine as legacy_engine
        from code_reviewer import git_ops as legacy_git
        from code_reviewer.master import MasterList as LegacyMaster
        from code_reviewer.models import RunResult as LegacyResult
        from code_reviewer.flows.single_agent.master import MasterList
        from code_reviewer.flows.single_agent.models import RunResult
        self.assertIs(legacy_git, git_ops)
        self.assertIs(LegacyMaster, MasterList)
        self.assertIs(LegacyResult, RunResult)
        self.assertEqual(legacy_engine.__all__, ['Engine'])

    def test_shared_mechanics_have_no_workflow_dependencies(self):
        shared = REPO_ROOT / 'src/code_reviewer/shared'
        for path in shared.glob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn('flows', (node.module or '').split('.'), str(path))
                    self.assertNotEqual(node.module, 'engine', str(path))
