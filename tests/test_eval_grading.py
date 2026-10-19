"""Judging policy boundaries and metric denominators, without model opinions."""
from __future__ import annotations

import copy
import unittest

from code_reviewer.eval_cases import EvalError, file_hash, object_hash
from code_reviewer.eval_grading import load_experiment, render_evaluation, summarize, summarize_many
from code_reviewer.eval_judgments import judgment_template, make_judgment, validate_judgments
from code_reviewer.run_store import dump_json

from .helpers import TempDirTestMixin


class GradingTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.root = self.make_tmp()
        self.case = {"id": "sample", "pair": "scenario", "base": "a" * 40, "head": "b" * 40,
                     "title": "Change behavior", "description": "Update behavior.",
                     "expectation_status": "confirmed", "expectation_reason": "Synthetic evidence independently verified.",
                     "expected": [{"id": key, "criterion": "GEN-001", "file": "app.py",
                                   "description": "Expected defect " + key, "evidence": "Synthetic evidence."}
                                  for key in ("one", "two")]}
        self.suite = {"version": 1, "source": "synthetic", "cases": [self.case]}
        self.master = {"findings": [{"id": "F-%03d" % n, "seen_in_passes": [1]} for n in range(1, 6)]}
        self.master["findings"].append({"id": "F-006", "seen_in_passes": []})
        (self.root / "rubric.md").write_text("Synthetic rubric")
        self.rubric_hash = file_hash(self.root / "rubric.md")
        self.env = {"config": {"model_comparator": "fixed"}, "files": {}}
        self.trial = {"id": "trial-0001", "case": "sample", "model": "model-a", "passes": 1,
                      "repeat": 1, "status": "complete", "base": self.case["base"], "head": self.case["head"],
                      "run": "run", "judgments": "judgments.json", "master_hash": object_hash(self.master),
                      "cost_usd_estimated": .15, "cost_known": True, "duration_s": 2}
        self.exp = {"version": 1, "mode": "run", "repo": "/synthetic/local-clone", "suite_hash": object_hash(self.suite),
                    "environment_hash": object_hash(self.env), "rubric_hash": self.rubric_hash,
                    "trials": [self.trial]}
        (self.root / "run").mkdir()
        self.doc = judgment_template(self.master, self.rubric_hash)
        self.save()

    def save(self):
        self.exp["suite_hash"] = object_hash(self.suite)
        self.trial["master_hash"] = object_hash(self.master)
        dump_json(self.root / "experiment.json", self.exp)
        dump_json(self.root / "environment.json", self.env)
        dump_json(self.root / "suite.json", self.suite)
        dump_json(self.root / "run/master.json", self.master)
        dump_json(self.root / "judgments.json", self.doc)

    def label(self, number, verdict, issue="", **kwargs):
        self.doc["findings"]["F-%03d" % number] = make_judgment(verdict, issue, "Synthetic repository evidence.", **kwargs)

    def all_final(self):
        self.label(1, "match", "one")
        self.label(2, "duplicate", "one", duplicate_of="F-001")
        self.label(3, "wrong", fail="introduced")
        self.label(4, "additional", "new-defect")
        self.label(5, "wrong", fail="correct")
        self.save()

    def score(self):
        self.save()
        exp, suite = load_experiment(self.root)
        return summarize(exp, suite, self.root)

    def test_precision_penalizes_duplicates_recall_only_matches_known_defects(self):
        self.all_final()
        summary = self.score()
        group = summary["groups"][0]
        self.assertEqual(group["precision"], 2 / 5)
        self.assertEqual(group["known_issue_recall"], 1 / 2)
        self.assertEqual(group["duplicates"], 1)
        self.assertEqual(group["wrong"], 2)
        self.assertEqual(group["cost_per_valid_issue"], .075)
        self.assertEqual(summary["trials"][0]["missed_expected"], ["two"])
        self.assertEqual(summary["trials"][0]["rejected_only"], 1)
        self.assertTrue(summary["comparison_ready"])

    def test_pending_and_uncertain_block_quality_scores(self):
        self.label(1, "match", "one")
        self.label(2, "uncertain", uncertain="correct")
        summary = self.score()
        self.assertFalse(summary["comparison_ready"])
        self.assertIsNone(summary["groups"][0]["precision"])
        self.assertIsNone(summary["groups"][0]["known_issue_recall"])
        self.assertEqual(summary["groups"][0]["pending"], 3)
        self.assertEqual(summary["groups"][0]["uncertain"], 1)
        self.assertIsNone(summary["trials"][0]["missed_expected"])

    def test_provisional_expectations_never_produce_recall_even_with_valid_labels(self):
        self.all_final()
        self.case["expectation_status"] = "provisional"
        summary = self.score()
        self.assertEqual(summary["groups"][0]["precision"], .4)
        self.assertIsNone(summary["groups"][0]["known_issue_recall"])
        self.assertIsNone(summary["trials"][0]["missed_expected"])
        self.assertEqual(summary["groups"][0]["confirmed_expected"], 0)

    def test_empty_review_has_undefined_precision_and_zero_confirmed_recall(self):
        self.master = {"findings": []}
        self.doc = judgment_template(self.master, self.rubric_hash)
        summary = self.score()
        self.assertIsNone(summary["groups"][0]["precision"])
        self.assertEqual(summary["groups"][0]["known_issue_recall"], 0)
        self.case["expected"] = []
        self.assertIsNone(self.score()["groups"][0]["known_issue_recall"])

    def test_an_expected_empty_case_can_contain_additional_valid_findings(self):
        self.case["expected"] = []
        for n in range(1, 6):
            self.label(n, "additional", "new-%d" % n)
        group = self.score()["groups"][0]
        self.assertEqual(group["precision"], 1)
        self.assertIsNone(group["known_issue_recall"])
        self.assertEqual(group["wrong"], 0)

    def test_valid_labels_require_all_checks_pass_and_wrong_requires_failure(self):
        for verdict in ("match", "additional", "duplicate", "wrong", "uncertain"):
            with self.subTest(verdict=verdict):
                bad = copy.deepcopy(self.doc)
                entry = make_judgment(verdict, "one", "Synthetic reason")
                if verdict in ("match", "additional", "duplicate"):
                    entry["checks"]["correct"] = "uncertain"
                bad["findings"]["F-001"] = entry
                with self.assertRaises(EvalError):
                    validate_judgments(bad, self.master, self.case, self.rubric_hash)

    def test_duplicates_need_valid_original_with_same_issue_and_no_implicit_dedup(self):
        self.label(1, "match", "one")
        self.label(2, "match", "one")
        with self.assertRaises(EvalError):
            self.score()
        self.label(2, "duplicate", "two", duplicate_of="F-001")
        with self.assertRaises(EvalError):
            self.score()
        self.label(2, "duplicate", "one", duplicate_of="F-001")
        self.score()

    def test_match_and_additional_need_correct_issue_namespace(self):
        for verdict, issue in (("match", "unknown"), ("additional", "one")):
            with self.subTest(verdict=verdict):
                self.label(1, verdict, issue)
                with self.assertRaises(EvalError):
                    self.score()

    def test_missing_or_extra_labels_and_stale_master_are_rejected(self):
        for mutation in ("missing", "extra", "stale"):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(self.doc)
                if mutation == "missing":
                    del bad["findings"]["F-001"]
                elif mutation == "extra":
                    bad["findings"]["F-006"] = bad["findings"]["F-001"]
                else:
                    bad["master_hash"] = "stale"
                with self.assertRaises(EvalError):
                    validate_judgments(bad, self.master, self.case, self.rubric_hash)

    def test_artifact_paths_cannot_escape_experiment(self):
        self.trial["run"] = "../other"
        with self.assertRaises(EvalError):
            self.score()
        self.trial["run"] = str(self.root / "run")
        with self.assertRaises(EvalError):
            self.score()

    def test_incomplete_trials_suppress_group_scores_and_keep_all_spending(self):
        self.all_final()
        failed = dict(self.trial, id="trial-0002", repeat=2, status="failed", cost_usd_estimated=.2, cost_known=False)
        self.exp["trials"].append(failed)
        summary = self.score()
        group = summary["groups"][0]
        self.assertEqual(group["graded_complete_trials"], 1)
        self.assertIsNone(group["precision"])
        self.assertFalse(group["cost_known"])
        self.assertAlmostEqual(group["cost_usd_estimated"], .35)
        self.assertFalse(summary["comparison_ready"])

    def test_cross_suite_aggregation_counts_pairs_and_detects_unbalanced_matrix(self):
        self.all_final()
        second = copy.deepcopy(self.suite)
        second["source"] = "other-synthetic"
        other_exp = copy.deepcopy(self.exp)
        other_exp["trials"][0]["model"] = "model-b"
        summary = summarize_many([(self.exp, self.suite, self.root), (other_exp, second, self.root)])
        self.assertEqual(summary["underlying_scenarios"], 2)
        self.assertFalse(summary["balanced_cases_and_repetitions"])
        self.assertFalse(summary["comparison_ready"])
        with self.assertRaises(EvalError):
            summarize_many([(self.exp, self.suite, self.root), (self.exp, self.suite, self.root)])
        other_exp["environment_hash"] = "changed-settings"
        with self.assertRaises(EvalError):
            summarize_many([(self.exp, self.suite, self.root), (other_exp, second, self.root)])

    def test_paired_cases_share_one_underlying_scenario(self):
        self.all_final()
        second = copy.deepcopy(self.suite)
        second["cases"][0]["description"] = "Repair behavior."
        summary = summarize_many([(self.exp, self.suite, self.root), (self.exp, second, self.root)])
        self.assertEqual(summary["underlying_scenarios"], 1)

    def test_markdown_keeps_table_rows_together_and_explains_limits(self):
        self.all_final()
        report = render_evaluation(self.score())
        self.assertIn("provisional keys never enter", report)
        self.assertIn("$0.1500", report)
        self.assertIn("Underlying scenarios: 1", report)
