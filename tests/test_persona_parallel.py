"""Real concurrency and subprocess cancellation, using only synthetic repositories and binaries."""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path

from code_reviewer.config import Config, ConfigError
from code_reviewer.engine import Engine
from code_reviewer.flows.agent_personas.budget import Budget, RunStopped
from code_reviewer.flows.agent_personas.criteria import PERSONAS
from code_reviewer.flows.agent_personas.runner import ParallelRunner
from code_reviewer.shared.claude_cli import ClaudeResult, ClaudeRunner
from .helpers import TempDirTestMixin, finding, make_repo
from .test_persona_flow import PersonaRunner, block


class BudgetTests(unittest.TestCase):
    def test_ratios_scale_and_round_without_creating_money(self):
        for total in (3, 6, .01, 3.07):
            budget = Budget(Config(run_budget_usd=total))
            pools = budget.snapshot()
            self.assertAlmostEqual(sum(p['allocated_usd'] for p in pools.values()), total)
            values = [pools[p]['allocated_usd'] for p in PERSONAS]
            self.assertLessEqual(max(values) - min(values), .0100001)
        pools = Budget(Config()).snapshot()
        self.assertEqual([pools[p]['allocated_usd'] for p in ('criteria', 'comparator', 'verifier')], [.3, .3, .4])
        self.assertTrue(all(pools[p]['allocated_usd'] == .5 for p in PERSONAS))

    def test_reservations_are_exclusive_reconciled_and_unknown_costs_stay_reserved_as_spent(self):
        budget = Budget(Config())
        amount = budget.reserve('correctness', .5)
        with self.assertRaises(RunStopped):
            budget.reserve('correctness', .5)
        budget.settle('correctness', amount, ClaudeResult(ok=True, cost_usd=.1))
        self.assertEqual(budget.reserve('correctness', .5), .4)
        budget.settle('correctness', .4, None)
        with self.assertRaises(RunStopped):
            budget.check('correctness')
        self.assertEqual(budget.snapshot()['correctness']['unknown_cost_calls'], 1)
        self.assertEqual(budget.reserve('test-quality', .5), .5)


class ParallelTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)
        self.cfg = Config(review_mode='agent-personas', criteria_mode='defaults_only',
                          persona_passes=2, runs_dir=str(self.tmp / 'runs'))

    def test_round_barriers_serial_merge_and_isolated_request_logs(self):
        owner = self
        class Runner(PersonaRunner):
            def __init__(self):
                super().__init__(reviewer=[{'findings': [finding(rule_id='none')]}])
                self.barrier = threading.Barrier(4)
                self.lock = threading.Lock()
                self.finished = 0
                self.merged = []
            def run(self, **kw):
                if 'meticulous senior engineer' in kw['system_prompt']:
                    self.barrier.wait(timeout=5)  # fails if calls accidentally become serial
                    with self.lock:
                        if self.finished >= 4:
                            owner.assertEqual(len(self.merged), 4)
                        value = super().run(**kw)
                        self.finished += 1
                        return value
                with self.lock:
                    owner.assertEqual(self.finished, 4 if len(self.merged) < 4 else 8)
                    self.merged.append(block(kw['user_prompt'], 'new_findings')[0]['local_id'])
                    return super().run(**kw)
        runner = Runner()
        result = Engine(self.cfg, runner).review(self.ctx)
        self.assertEqual(result.stop_reason, 'completed')
        self.assertEqual(runner.merged, ['p%d-f1' % n for n in range(1, 9)])
        self.assertEqual(result.completed_by_persona, dict.fromkeys(PERSONAS, 2))
        calls = [c for c in runner.calls if c['role'] == 'reviewer']
        self.assertEqual(len({c['cwd'] for c in calls}), 1)
        for slot in result.passes:
            log = json.loads((result.run_dir / ('pass-%02d/reviewer.raw.requests.json' % slot.pass_no)).read_text())
            frozen = result.run_dir / 'input/personas' / slot.persona / 'reviewer.system.md'
            self.assertEqual(log[0]['system_prompt'], frozen.read_text())
        self.assertTrue(all(p['reserved_usd'] == 0 for p in result.budget_pools.values()))

    def test_concurrency_limit_two_is_enforced(self):
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active, peak = [0], [0]
        class Runner:
            def run(self, **kwargs):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                barrier.wait(timeout=5)
                with lock:
                    active[0] -= 1
                return ClaudeResult(ok=True, parsed={'findings': []}, cost_usd=.001, raw={})
        self.cfg.persona_parallel = 2
        self.assertEqual(Engine(self.cfg, Runner()).review(self.ctx).passes_completed, 8)
        self.assertEqual(peak[0], 2)

    def test_exhausted_persona_does_not_stop_other_personas(self):
        self.cfg.persona_parallel = 1
        first = ClaudeResult(ok=True, parsed={'findings': [finding(rule_id='none')]}, cost_usd=.5, raw={})
        result = Engine(self.cfg, PersonaRunner(reviewer=[first, {'findings': []}])).review(self.ctx)
        self.assertEqual(result.stop_reason, 'incomplete')
        self.assertEqual(result.completed_by_persona, dict(zip(PERSONAS, (1, 2, 2, 2))))
        self.assertEqual(result.passes[4].status, 'failed')
        self.assertIn('correctness budget exhausted', result.warnings + [result.passes[4].error])
        self.assertIn('correctness: 1/1 (Unrated)', result.report_path.read_text())

    def test_bad_parallel_values_and_single_agent_override_rejected(self):
        from code_reviewer.cli import build_parser
        from code_reviewer.review_options import apply_run_options
        for value in (0, 5, True, 1.5):
            with self.assertRaises(ConfigError):
                Config(persona_parallel=value).validate()
        args = build_parser().parse_args(['review', 'owner/repo#1', '--parallel', '2'])
        with self.assertRaises(ConfigError):
            apply_run_options(Config(), args)
        args.mode = 'agent-personas'
        self.assertEqual(apply_run_options(Config(), args).persona_parallel, 2)

    @unittest.skipUnless(os.name == 'posix', 'process group test')
    def test_large_prompt_reaches_slow_reader_in_full(self):
        binary = self.tmp / 'fake-slow-claude'
        binary.write_text('#!' + sys.executable + '\nimport sys,time,json\n'
            'time.sleep(.3)\ntext=sys.stdin.read()\n'
            'print(json.dumps({"structured_output":{"length":len(text)},"total_cost_usd":.001}))\n')
        binary.chmod(0o700)
        prompt = 'synthetic prompt \u03bb\n' * 20000
        result = ParallelRunner(ClaudeRunner(str(binary)), Budget(self.cfg)).run(
            system_prompt='system', user_prompt=prompt, schema={}, model='fake', tools='',
            cwd=self.tmp, budget_usd=.01, timeout_s=2)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.parsed, {'length': len(prompt)})

    @unittest.skipUnless(os.name == 'posix', 'process group test')
    def test_cancel_kills_owned_process_group_before_returning(self):
        ready = self.tmp / 'ready'
        binary = self.tmp / 'fake-claude'
        binary.write_text('#!' + sys.executable + '\nimport os,sys,time,subprocess\nfrom pathlib import Path\n'
            'sys.stdin.read()\nsubprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])\n'
            'Path(%r).write_text(str(os.getpid()))\ntime.sleep(30)\n' % str(ready))
        binary.chmod(0o700)
        budget = Budget(self.cfg)
        runner = ParallelRunner(ClaudeRunner(str(binary)), budget)
        errors = []
        def run():
            try:
                runner.run(system_prompt='system', user_prompt='user', schema={}, model='fake', tools='',
                           cwd=self.tmp, budget_usd=.01, timeout_s=20)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            budget.cancelled.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0], KeyboardInterrupt)
        finally:
            budget.cancelled.set()
            if thread.is_alive() and ready.exists():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            thread.join(5)
