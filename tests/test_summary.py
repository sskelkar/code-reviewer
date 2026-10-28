"""The shareable summary: shown in the terminal, saved as summary.md and posted as the PR comment."""
import contextlib
import io
import unittest
from unittest import mock

from code_reviewer import cli
from code_reviewer.config import Config
from code_reviewer.engine import Engine
from code_reviewer.post import MARKER, format_comment
from code_reviewer.report import humanize, render_finding, rule_label
from code_reviewer.summary import render_summary, run_line, select_findings

from .helpers import FakeRunner, TempDirTestMixin, finding, make_repo

OTHER = dict(title="Result of the query is never checked", description="db.run may return None here.", severity="medium",
             quote="return db.run(query)", rule_id="none", file="app.py", line=3)
RESOLVER = {"criteria_files": [], "inline_sections": [], "organization_notes": "", "ignored_references": []}


def decisions(*pairs):
    return {"decisions": [{"local_id": local, "match": match, "same_as": None} for local, match in pairs]}


class SummaryTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        from .helpers import synthetic_frontend
        synthetic_frontend(self)
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)

    def run_review(self, reviewer, comparator=None, **cfg):
        config = Config(runs_dir=str(self.tmp / "runs"), **cfg)
        return Engine(config, FakeRunner(reviewer=reviewer, comparator=comparator or [], resolver=RESOLVER)).review(self.ctx)

    def three_pass_result(self):
        """F-001 found in all three passes (High). F-002 found only in pass 1 (Low)."""
        both = {"findings": [finding(rule_id="none"), finding(**OTHER)]}
        only_first = {"findings": [finding(rule_id="none")]}
        return self.run_review(
            [both, only_first, only_first],
            [decisions(("p1-f1", "new"), ("p1-f2", "new")), decisions(("p2-f1", "F-001")), decisions(("p3-f1", "F-001"))])

    def test_low_confidence_findings_are_shown_by_default(self):
        result = self.three_pass_result()
        text = render_summary(result, result.cfg.post_tiers, [])
        self.assertIn("**2 findings: 1 high confidence, 1 low confidence**", text)
        self.assertIn("### F-001 · high severity · High confidence", text)
        self.assertIn("### F-002 · medium severity · Low confidence", text)
        self.assertLess(text.index("F-001"), text.index("F-002"))  # higher confidence first
        self.assertEqual(Config().post_tiers, ["High", "Medium", "Low"])

    def test_tier_filter_hides_findings_and_says_so(self):
        result = self.three_pass_result()
        text = render_summary(result, ["High", "Medium"], [])
        self.assertIn("**1 finding: 1 high confidence** · 1 finding at other confidence levels not shown", text)
        self.assertNotIn("F-002", text)

    def test_dropped_findings_are_left_out(self):
        result = self.three_pass_result()
        self.assertEqual([i["id"] for i in select_findings(result, ["High", "Medium", "Low"], ["F-001"])], ["F-002"])
        self.assertNotIn("F-001", render_summary(result, ["High", "Medium", "Low"], ["F-001"]))

    def test_no_findings(self):
        result = self.run_review([{"findings": []}], max_passes=3, min_passes=3)
        self.assertIn("**No findings.**", render_summary(result, ["High"], []))

    def test_no_completed_pass_has_no_review_to_share(self):
        from code_reviewer.claude_cli import ClaudeResult
        runner = FakeRunner(reviewer=[ClaudeResult(ok=False, error="timed out")], resolver=RESOLVER)
        result = Engine(Config(runs_dir=str(self.tmp / "runs")), runner).review(self.ctx)
        self.assertIn("No pass completed, so there is no review to share", render_summary(result, ["High"], []))

    def test_single_pass_is_unrated_and_always_shown(self):
        result = self.run_review([{"findings": [finding(rule_id="none")]}], max_passes=1, min_passes=1)
        text = render_summary(result, ["High"], [])  # even a tier filter that excludes everything
        self.assertIn("unrated: only one pass completed", text)
        self.assertIn("F-001", text)
        self.assertIn("seen in 1/1 pass\n", text)

    def test_absent_findings_show_what_was_expected(self):
        absent = finding(kind="absent", quote="", anchor={"file": "app.py", "line_start": 2, "line_end": 3},
                         expected="validate user_id before building the query", rule_id="none", line=0)
        result = self.run_review([{"findings": [absent]}], max_passes=1, min_passes=1)
        self.assertIn("**Expected:** validate user_id before building the query", render_summary(result, ["High"], []))

    def test_summary_file_matches_the_rendered_summary(self):
        result = self.three_pass_result()
        saved = (result.run_dir / "summary.md").read_text()
        self.assertEqual(saved, render_summary(result, result.cfg.post_tiers, []))

    def test_comment_is_the_summary_plus_a_hidden_marker(self):
        result = self.three_pass_result()
        comment = format_comment(result, ["High", "Medium", "Low"], [])
        self.assertTrue(comment.startswith(MARKER + "\n"))
        self.assertEqual(comment[len(MARKER) + 1:], render_summary(result, ["High", "Medium", "Low"], []))
        self.assertNotIn(MARKER, render_summary(result, ["High"], []))

    def test_run_line_is_short_and_has_no_nested_parentheses(self):
        line = run_line(self.three_pass_result())
        self.assertRegex(line, r"^Run: 3 passes, converged · est\. cost \$[\d.]+ · \d+s of model time$")

    def test_one_shot_cli_prints_the_summary_then_the_report_path(self):
        fake = FakeRunner(reviewer=[{"findings": [finding(rule_id="none")]}], resolver=RESOLVER)
        out = io.StringIO()
        config = Config(runs_dir=str(self.tmp / "runs"))
        with mock.patch.object(cli, "load_config", return_value=config), \
                mock.patch("code_reviewer.backends.factory.create_runner", return_value=fake), \
                mock.patch.object(Engine, "prepare", return_value=self.ctx), \
                contextlib.redirect_stdout(out):
            code = cli.main(["review", "acme/project#7", "--passes", "1"])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("## AI-generated code review (experimental)", text)
        self.assertIn("SQL built by concatenation", text)
        self.assertIn("Run: 1 pass, pass limit reached", text)
        self.assertLess(text.index("AI-generated code review"), text.index("full report:"))
        self.assertTrue(text.strip().splitlines()[-1].startswith("full report: "))


class CriterionWordingTests(TempDirTestMixin, unittest.TestCase):
    """People read criterion names, not internal IDs."""

    RULES = {
        "GEN-004": {"source": "default", "path": "general.md", "heading": "Protect security and privacy", "text": "x"},
        "P:docs/ddd.md#controllers": {"source": "project", "path": "docs/ddd.md", "heading": "Controllers", "text": "x"},
    }

    def test_labels_name_the_criterion_and_its_source(self):
        self.assertEqual(rule_label("GEN-004", self.RULES), "Protect security and privacy (default criteria)")
        self.assertEqual(rule_label("P:docs/ddd.md#controllers", self.RULES), "Controllers (`docs/ddd.md`)")
        self.assertEqual(rule_label("none", self.RULES), "")
        self.assertEqual(rule_label("GEN-999", self.RULES), "")

    def test_rule_ids_in_the_models_own_words_are_replaced(self):
        text = "Violates GEN-004 and [GEN-004]; see `P:docs/ddd.md#controllers`. Not GEN-0040 or GEN-999."
        self.assertEqual(
            humanize(text, self.RULES),
            'Violates "Protect security and privacy" and "Protect security and privacy"; see "Controllers". '
            "Not GEN-0040 or GEN-999.")
        self.assertEqual(humanize("no ids here", self.RULES), "no ids here")

    def test_summary_and_report_show_the_name_and_never_the_id(self):
        both = {"findings": [finding(rule_id="GEN-001", title="Breaks GEN-001 on empty input")]}
        config = Config(runs_dir=str(self.make_tmp() / "runs"))
        ctx = make_repo(self.make_tmp())
        result = Engine(config, FakeRunner(
            reviewer=[both, both, both],
            comparator=[decisions(("p2-f1", "F-001")), decisions(("p3-f1", "F-001"))], resolver=RESOLVER)).review(ctx)
        summary = render_summary(result, config.post_tiers, [])
        report = render_finding(result.master.scored()[0], result.passes_completed, result.criteria.index())
        for text in (summary, report):
            self.assertIn("Criterion: Implement behavior correctly (default criteria)", text)
            self.assertNotIn("GEN-001", text)


if __name__ == "__main__":
    unittest.main()
