"""The comparator replay tool: regroup the findings of a saved run with a (fake) comparator and report what changed."""
import unittest

from code_reviewer import replay
from code_reviewer.config import Config
from code_reviewer.engine import Engine

from .helpers import FakeRunner, TempDirTestMixin, finding, make_repo

RESOLVER = {"criteria_files": [], "inline_sections": [], "organization_notes": "", "ignored_references": []}
OTHER = dict(title="Result of the query is never checked", description="db.run may return None here.", severity="medium",
             quote="return db.run(query)", rule_id="none", file="app.py", line=3)
NEAR_DUPLICATE = dict(title="Concatenated SQL is injectable", description="user_id is concatenated into the SQL string.")


def decisions(*pairs):
    return {"decisions": [{"local_id": local, "match": match, "same_as": None} for local, match in pairs]}


class ReplayTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        tmp = self.make_tmp()
        ctx = make_repo(tmp)
        # pass 1 finds A; pass 2 finds the same problem worded differently (A') and another problem B.
        # The recorded comparator treated A' as new (a false split) and matched nothing else.
        reviewer = [{"findings": [finding(rule_id="none")]},
                    {"findings": [finding(rule_id="none", **NEAR_DUPLICATE), finding(**OTHER)]}]
        comparator = [decisions(("p2-f1", "new"), ("p2-f2", "new"))]
        runner = FakeRunner(reviewer=reviewer, comparator=comparator, resolver=RESOLVER)
        cfg = Config(runs_dir=str(tmp / "runs"), min_passes=2, max_passes=2)
        self.run_dir = Engine(cfg, runner).review(ctx).run_dir
        self.cfg = cfg
        self.passes = replay.load_pass_findings(self.run_dir)

    def test_loads_the_usable_findings_of_each_pass(self):
        self.assertEqual([(n, len(fs)) for n, fs in self.passes], [(1, 1), (2, 2)])

    def test_recorded_grouping_reproduces_the_original_run(self):
        groups, titles = replay.recorded_grouping(self.run_dir, self.passes)
        self.assertEqual(groups, {"F-001": ["p1-f1"], "F-002": ["p2-f1"], "F-003": ["p2-f2"]})
        self.assertEqual(titles["p2-f2"], OTHER["title"])

    def test_replay_with_a_better_comparator_merges_the_duplicate_and_reports_it(self):
        better = FakeRunner(reviewer=[], comparator=[decisions(("p2-f1", "F-001"), ("p2-f2", "new"))], resolver=RESOLVER)
        before, titles = replay.recorded_grouping(self.run_dir, self.passes)
        after, _ = replay.replay_grouping(self.passes, self.cfg, better)
        self.assertEqual(after, {"F-001": ["p1-f1", "p2-f1"], "F-002": ["p2-f2"]})
        merged, split = replay.changed_pairs(before, after)
        self.assertEqual((merged, split), ([("p1-f1", "p2-f1")], []))
        text = replay.describe_changes(before, after, titles)
        self.assertIn("now merged: p1-f1", text)
        self.assertIn("found in 2 passes", replay.describe("REPLAY", after, titles))
        self.assertEqual(len(better.calls_for("comparator")), 1)  # pass 1 needs no model call

    def test_identical_grouping_is_reported_as_such(self):
        same = FakeRunner(reviewer=[], comparator=[decisions(("p2-f1", "new"), ("p2-f2", "new"))], resolver=RESOLVER)
        before, titles = replay.recorded_grouping(self.run_dir, self.passes)
        after, _ = replay.replay_grouping(self.passes, self.cfg, same)
        self.assertEqual(replay.describe_changes(before, after, titles), "  grouping is identical to the original run")

    def test_a_worse_comparator_that_merges_different_issues_shows_up_as_merged(self):
        bad = FakeRunner(reviewer=[], comparator=[decisions(("p2-f1", "F-001"), ("p2-f2", "F-001"))], resolver=RESOLVER)
        before, _ = replay.recorded_grouping(self.run_dir, self.passes)
        after, _ = replay.replay_grouping(self.passes, self.cfg, bad)
        merged, _ = replay.changed_pairs(before, after)
        self.assertIn(("p2-f1", "p2-f2"), merged)  # a false merge is visible, not silent


if __name__ == "__main__":
    unittest.main()
