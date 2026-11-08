"""Round-scaled allowances across CLI selection, role pools and the VM boundary."""
from __future__ import annotations

import argparse
import json
import unittest
from pathlib import Path

from code_reviewer.config import Config, ConfigError
from code_reviewer.review.budget import Budget, RunStopped
from code_reviewer.review_options import review_banner, add_run_options, apply_run_options
from code_reviewer.sandbox.settings import job_settings, config_from_job


class RoundBudgetTests(unittest.TestCase):
    def test_round_selection_scales_global_threshold_and_role_pools(self):
        parser = argparse.ArgumentParser()
        add_run_options(parser)
        original = Config(auth_mode='subscription')
        for rounds in (1, 2, 3, 5):
            cfg = apply_run_options(original, parser.parse_args(['--passes', str(rounds)]))
            self.assertEqual(original.persona_passes, 3)
            budget = Budget(cfg)
            expected = 10000000 * rounds
            self.assertEqual(cfg.effective_run_budget_tokens, expected)
            self.assertEqual(sum(p['allocated_tokens'] for p in budget.snapshot().values()), expected)
            self.assertEqual(cfg.call_budget_tokens, 2000000)
            self.assertEqual(budget.reserve('correctness', cfg.call_budget_tokens), min(2000000, budget.snapshot()['correctness']['allocated_tokens']))
            self.assertIn(format(expected, ','), review_banner('example/demo#1', cfg))
            budget.tokens = expected
            with self.assertRaises(RunStopped):
                budget.check('verifier')

    def test_explicit_run_override_and_api_dollars_do_not_scale(self):
        for rounds in (1, 4):
            cfg = Config(auth_mode='subscription', persona_passes=rounds, run_budget_tokens=3000)
            self.assertEqual(sum(p['allocated_tokens'] for p in Budget(cfg).snapshot().values()), 3000)
            api = Budget(Config(persona_passes=rounds))
            self.assertAlmostEqual(sum(p['allocated_usd'] for p in api.snapshot().values()), 3.0)

    def test_guest_round_trip_preserves_automatic_and_explicit_limits(self):
        for override in (None, 123456):
            cfg = Config(auth_mode='subscription', persona_passes=2, run_budget_tokens=override)
            clone = config_from_job(json.loads(json.dumps(job_settings(cfg))), Path('/synthetic'))
            self.assertEqual(clone.effective_run_budget_tokens, override or 20000000)
            self.assertEqual(clone.call_budget_tokens, 2000000)

    def test_invalid_limits_rejected(self):
        for key in ('round_budget_tokens', 'run_budget_tokens', 'call_budget_tokens'):
            for value in (True, 0, -1, 1.5, '100'):
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                    Config(**{key: value}).validate()
