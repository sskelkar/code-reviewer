"""Offline matrix integration: real Engine/git, fake Claude, no network or paid calls."""
from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from code_reviewer.claude_cli import ClaudeResult
from code_reviewer.config import Config, REPO_ROOT
from code_reviewer.eval_cases import EvalError, case_context, load_suite, read_object, validate_suite
from code_reviewer.eval_grading import load_experiment, summarize
from code_reviewer.eval_runner import environment, run_matrix
from code_reviewer.evals import main
from code_reviewer.models import ReviewRefused
from code_reviewer.run_store import dump_json

from .helpers import FakeRunner, TempDirTestMixin, finding, git, make_repo


class EvalTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)
        self.suite = {"version": 1, "source": "synthetic", "cases": [{
            "id": "sample", "base": self.ctx.base_sha, "head": self.ctx.head_sha,
            "title": "Change lookup", "description": "Update lookup implementation.",
            "notes": "EVALUATOR_ONLY_SECRET", "expected": [{
                "id": "injection", "criterion": "GEN-004", "file": "app.py",
                "description": "EVALUATOR_ONLY_SECRET", "evidence": "EVALUATOR_ONLY_SECRET",
            }], "expectation_status": "confirmed", "expectation_reason": "Synthetic fixture verified by test.",
        }]}
        self.cfg = Config(claude_bin="definitely-not-claude")
        self.suite_path = self.tmp / "suite.json"
        dump_json(self.suite_path, self.suite)

    def run_eval(self, runner=None, name="experiment", **kwargs):
        runner = runner or FakeRunner(reviewer=[{"findings": [finding()]}])
        root = self.tmp / name
        experiment = run_matrix(self.suite, self.ctx.git_dir, root, self.cfg, runner,
                                kwargs.pop("models", ["haiku"]), kwargs.pop("passes", [1]),
                                execute=kwargs.pop("execute", True), **kwargs)
        return root, experiment, runner

    def cli(self, argv):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(argv)

    def test_plan_is_model_free_and_keeps_answers_out_of_prompts(self):
        runner = FakeRunner(reviewer=[])
        runner.version = lambda: self.fail("plan must not invoke Claude version")
        root, exp, runner = self.run_eval(runner, models=["haiku", "sonnet"], passes=[1, 3], repeat=2, execute=False)
        self.assertEqual(len(exp["trials"]), 8)
        self.assertEqual(runner.calls, [])
        for trial in exp["trials"]:
            self.assertEqual(trial["status"], "planned")
            prompt = (root / trial["run"] / "input/reviewer.user.md").read_text()
            self.assertNotIn("EVALUATOR_ONLY_SECRET", prompt)
            self.assertNotIn("Build query by concatenation", prompt)  # historical commit message
            self.assertIn("GEN-010", prompt)
        self.assertEqual(git(self.ctx.git_dir, "status", "--porcelain"), "")
        with self.assertRaises(EvalError):
            load_experiment(root)

    def test_matrix_requests_all_passes_and_uses_fresh_cleaned_checkouts(self):
        runner = FakeRunner(reviewer=[{"findings": []}])
        root, exp, runner = self.run_eval(runner, models=["haiku", "sonnet"], passes=[1, 4], repeat=2)
        self.assertEqual(len(exp["trials"]), 8)
        self.assertEqual(len(runner.calls_for("reviewer")), 20)
        self.assertEqual(runner.calls_for("resolver"), [])
        checkouts = {}
        for call in runner.calls_for("reviewer"):
            checkouts.setdefault(str(call["cwd"]), []).append(call)
            self.assertEqual(call["tools"], "Read,Grep,Glob")
            self.assertNotIn("EVALUATOR_ONLY_SECRET", call["user_prompt"])
        self.assertEqual(len(checkouts), 8)
        for path, calls in checkouts.items():
            self.assertFalse(Path(path).exists())
            self.assertEqual(len({c["user_prompt"] for c in calls}), 1)
        for trial in exp["trials"]:
            self.assertEqual(trial["status"], "complete")
            self.assertEqual(trial["completed"], trial["passes"])
            self.assertTrue((root / trial["id"] / "findings.md").is_file())
        self.assertEqual(git(self.ctx.git_dir, "rev-parse", "HEAD"), self.ctx.head_sha)
        self.assertEqual(git(self.ctx.git_dir, "status", "--porcelain"), "")

    def test_trial_records_comparator_cost_and_failure_counters(self):
        runner = FakeRunner(reviewer=[{"findings": [finding()]}], comparator=[
            {"decisions": [{"local_id": "p2-f1", "match": "F-001", "same_as": None}]}])
        root, exp, runner = self.run_eval(runner, passes=[2])
        trial = exp["trials"][0]
        self.assertEqual(trial["status"], "complete")
        self.assertAlmostEqual(trial["cost_usd_estimated"], .03)
        self.assertEqual(trial["usage"]["by_role"]["comparator"]["calls"], 1)
        self.assertEqual(len(trial["pass_records"]), 2)
        self.assertEqual(trial["passes"], 2)
        self.assertNotIn("haiku", (root / trial["id"] / "findings.md").read_text())

    def test_failed_pass_is_not_a_completed_fixed_pass_trial(self):
        runner = FakeRunner(reviewer=[ClaudeResult(ok=False, error="synthetic failure", cost_usd=.01)])
        root, exp, _ = self.run_eval(runner, passes=[3])
        self.assertEqual(exp["trials"][0]["status"], "incomplete")
        self.assertFalse(summarize(exp, self.suite, root)["comparison_ready"])
        self.assertGreater(exp["trials"][0]["usage"]["failed_calls"], 0)

    def test_experiment_budget_skips_later_trials(self):
        runner = FakeRunner(reviewer=[{"findings": []}], cost=.02)
        root, exp, runner = self.run_eval(runner, repeat=3, max_total_cost=.02)
        self.assertEqual([t["status"] for t in exp["trials"]], ["complete", "skipped", "skipped"])
        self.assertEqual(len(runner.calls), 1)
        summary = summarize(exp, self.suite, root)
        self.assertAlmostEqual(summary["groups"][0]["cost_usd_estimated"], .02)
        self.assertIsNone(summary["groups"][0]["precision"])

    def test_unknown_cost_stops_budgeted_experiment(self):
        runner = FakeRunner(reviewer=[{"findings": []}], cost=None)
        _, exp, runner = self.run_eval(runner, repeat=2, max_total_cost=1)
        self.assertFalse(exp["trials"][0]["cost_known"])
        self.assertEqual(exp["trials"][1]["status"], "skipped")
        self.assertEqual(len(runner.calls), 1)

    def test_run_budget_can_interrupt_trial_without_discarding_cost(self):
        self.cfg.run_budget_usd = .01
        runner = FakeRunner(reviewer=[{"findings": []}], cost=.02)
        root, exp, _ = self.run_eval(runner, passes=[3])
        trial = exp["trials"][0]
        self.assertEqual(trial["status"], "incomplete")
        self.assertEqual(trial["stop_reason"], "budget_exceeded")
        self.assertAlmostEqual(summarize(exp, self.suite, root)["groups"][0]["cost_usd_estimated"], .02)

    def test_keyboard_interrupt_stops_scheduling(self):
        class InterruptedRunner(FakeRunner):
            def run(self, **kwargs):
                raise KeyboardInterrupt()
        runner = InterruptedRunner(reviewer=[])
        _, exp, _ = self.run_eval(runner, repeat=2)
        self.assertEqual(exp["trials"][0]["status"], "incomplete")
        self.assertEqual(exp["trials"][0]["stop_reason"], "cancelled")
        self.assertEqual(exp["trials"][1]["status"], "skipped")

    def test_limits_and_missing_commits_fail_before_calls_or_output(self):
        runner = FakeRunner(reviewer=[])
        self.cfg.max_diff_lines = 1
        with self.assertRaises(ReviewRefused):
            self.run_eval(runner)
        self.assertFalse((self.tmp / "experiment").exists())
        self.cfg.max_diff_lines = 1500
        self.suite["cases"][0]["head"] = "f" * 40
        with self.assertRaises(EvalError):
            self.run_eval(runner)
        self.assertEqual(runner.calls, [])

    def test_exception_during_review_cleans_up_and_records_unknown_cost(self):
        class BrokenRunner(FakeRunner):
            def run(self, **kwargs):
                self.cwd = kwargs["cwd"]
                raise OSError("synthetic runner failure")
        runner = BrokenRunner(reviewer=[])
        _, exp, _ = self.run_eval(runner, repeat=2, max_total_cost=1)
        self.assertEqual(exp["trials"][0]["status"], "failed")
        self.assertFalse(exp["trials"][0]["cost_known"])
        self.assertEqual(exp["trials"][1]["status"], "skipped")
        self.assertFalse(Path(runner.cwd).exists())

    def test_rejects_outputs_in_either_repository_and_symlink_alias(self):
        for root in (self.ctx.git_dir / "results", REPO_ROOT / "results"):
            with self.assertRaises(EvalError):
                run_matrix(self.suite, self.ctx.git_dir, root, self.cfg, FakeRunner([]), ["haiku"], [1])
        alias = self.tmp / "alias"
        alias.symlink_to(self.ctx.git_dir, target_is_directory=True)
        with self.assertRaises(EvalError):
            run_matrix(self.suite, self.ctx.git_dir, alias / "results", self.cfg, FakeRunner([]), ["haiku"], [1])

    def test_cli_init_pins_refs_and_refuses_overwrite(self):
        dest = self.tmp / "private" / "suite.json"
        args = ["init", "--repo", str(self.ctx.git_dir), "--base", "main", "--head", "feature",
                "--output", str(dest), "--title", "Update lookup", "--description", "Change lookup."]
        self.assertEqual(self.cli(args), 0)
        case = load_suite(dest)["cases"][0]
        self.assertEqual(case["base"], self.ctx.base_sha)
        self.assertEqual(case["head"], self.ctx.head_sha)
        self.assertEqual(case["expectation_status"], "provisional")
        self.assertEqual(self.cli(args), 2)

    def test_cli_run_label_grade_round_trip_and_invalid_label_does_not_write(self):
        root = self.tmp / "cli"
        with patch("code_reviewer.evals.ClaudeRunner", return_value=FakeRunner([{"findings": [finding()]}])), \
                patch("code_reviewer.evals.load_config", return_value=self.cfg):
            self.assertEqual(self.cli(["run", str(self.suite_path), "--repo", str(self.ctx.git_dir),
                                       "--output", str(root), "--passes", "1"]), 0)
        label = ["label", str(root), "trial-0001", "F-001"]
        labels = root / "trial-0001/judgments.json"
        original = labels.read_bytes()
        self.assertEqual(self.cli(label + ["wrong", "--reason", "No failing check supplied"]), 2)
        self.assertEqual(labels.read_bytes(), original)
        self.assertEqual(self.cli(label + ["match", "--issue", "injection", "--reason", "app.py:2 builds SQL from input."]), 0)
        self.assertEqual(self.cli(["grade", str(root)]), 0)
        scores = read_object(root / "evaluation.json")["groups"][0]
        self.assertEqual(scores["precision"], 1)
        self.assertEqual(scores["known_issue_recall"], 1)
        self.assertTrue((root / "evaluation.md").is_file())

    def test_cli_combines_disjoint_suites_and_rejects_answers_inside_clone(self):
        runner = FakeRunner(reviewer=[{"findings": []}])
        first, _, _ = self.run_eval(runner, name="first")
        self.suite["source"] = "other-synthetic-source"
        second, _, _ = self.run_eval(runner, name="second")
        output = self.tmp / "combined"
        self.assertEqual(self.cli(["grade", str(first), str(second), "--output", str(output)]), 0)
        summary = read_object(output / "evaluation.json")
        self.assertEqual(summary["groups"][0]["trials"], 2)
        self.assertEqual(summary["underlying_scenarios"], 2)
        inside = self.ctx.git_dir / "answers.json"
        dump_json(inside, self.suite)
        with patch("code_reviewer.evals.ClaudeRunner", side_effect=AssertionError("must not create runner")):
            self.assertEqual(self.cli(["plan", str(inside), "--repo", str(self.ctx.git_dir),
                                       "--output", str(self.tmp / "bad")]), 2)

    def test_rekey_reuses_reviews_without_models_and_resets_labels(self):
        root, exp, runner = self.run_eval()
        self.assertEqual(self.cli(["label", str(root), "trial-0001", "F-001", "match", "--issue", "injection",
                                   "--reason", "Synthetic source evidence."]), 0)
        self.cli(["grade", str(root)])
        revised = copy.deepcopy(self.suite)
        revised["cases"][0]["expected"] = []
        path = self.tmp / "revised.json"
        dump_json(path, revised)
        output = self.tmp / "rekeyed"
        calls = len(runner.calls)
        self.assertEqual(self.cli(["rekey", str(root), str(path), "--output", str(output)]), 0)
        new_exp, new_suite = load_experiment(output)
        self.assertEqual(new_suite["cases"][0]["expected"], [])
        self.assertEqual(len(runner.calls), calls)
        self.assertEqual(read_object(output / "trial-0001/judgments.json")["findings"]["F-001"]["verdict"], "pending")
        self.assertEqual(read_object(root / "trial-0001/judgments.json")["findings"]["F-001"]["verdict"], "match")
        self.assertFalse((output / "evaluation.json").exists())
        self.assertFalse(summarize(new_exp, new_suite, output)["comparison_ready"])
        revised["cases"][0]["description"] = "Changed Reviewer input"
        dump_json(path, revised)
        self.assertEqual(self.cli(["rekey", str(root), str(path), "--output", str(self.tmp / "bad-rekey")]), 2)

    def test_saved_inputs_and_findings_are_bound_to_judgments(self):
        for target in ("suite.json", "rubric.md", "environment.json", "master"):
            with self.subTest(target=target):
                root, exp, _ = self.run_eval(name=target.replace(".", "-"))
                path = root / exp["trials"][0]["run"] / "master.json" if target == "master" else root / target
                if path.suffix == ".md":
                    path.write_text("changed rubric")
                else:
                    value = read_object(path)
                    value["tampered"] = True
                    dump_json(path, value)
                with self.assertRaises(EvalError):
                    loaded, suite = load_experiment(root)
                    summarize(loaded, suite, root)

    def test_schema_rejects_ambiguous_keys_and_nonancestor(self):
        bad = copy.deepcopy(self.suite)
        bad["cases"][0]["base"] = "main"
        with self.assertRaises(EvalError):
            validate_suite(bad)
        bad = copy.deepcopy(self.suite)
        bad["cases"][0]["expectation_reason"] = ""
        with self.assertRaises(EvalError):
            validate_suite(bad)
        bad = copy.deepcopy(self.suite)
        bad["cases"].append(copy.deepcopy(bad["cases"][0]))
        with self.assertRaises(EvalError):
            validate_suite(bad)
        case = dict(self.suite["cases"][0], base=self.ctx.head_sha, head=self.ctx.base_sha)
        with self.assertRaises(EvalError):
            case_context(self.ctx.git_dir, case, 1)

    def test_committed_public_suites_remain_eight_provisional_cases(self):
        suites = [load_suite(path) for path in (REPO_ROOT / "evals/suites").glob("*.json")]
        self.assertEqual(sum(len(s["cases"]) for s in suites), 8)
        for suite in suites:
            self.assertTrue(all(c.get("expectation_status", "provisional") == "provisional" for c in suite["cases"]))

    def test_schema_hashes_are_frozen_and_environment_changes_stop_scheduling(self):
        frozen = environment(self.cfg)
        self.assertTrue(any("/prompts/schemas/" in path for path in frozen["files"]))
        self.assertIn("src/code_reviewer/flows/single_agent/flow.py", frozen["files"])
        self.assertIn("src/code_reviewer/shared/git_ops.py", frozen["files"])
        changed = copy.deepcopy(frozen)
        changed["files"]["changed-during-run"] = "different"
        runner = FakeRunner(reviewer=[{"findings": []}])
        with patch("code_reviewer.eval_runner.environment", side_effect=[frozen, frozen, changed]):
            _, exp, _ = self.run_eval(runner, repeat=2)
        self.assertEqual([t["status"] for t in exp["trials"]], ["complete", "skipped"])
        self.assertEqual(len(runner.calls), 1)

    def test_invalid_matrix_does_not_start(self):
        for kwargs in ({"passes": [0]}, {"passes": [True]}, {"models": ["haiku", "haiku"]},
                       {"repeat": 0}, {"max_total_cost": float("nan")}, {"selected": ["missing"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(EvalError):
                self.run_eval(**kwargs)
