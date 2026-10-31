import unittest

from code_reviewer.config import Config
from code_reviewer.flows.single_agent.stop_policy import BUDGET_EXCEEDED, CONVERGED, FAILURES, MAX_PASSES, StopPolicy


def policy(**overrides):
    return StopPolicy(Config(**overrides))


class StopPolicyTests(unittest.TestCase):
    def test_converges_after_min_passes_and_quiet_streak(self):
        p = policy(min_passes=3, quiet_k=2)
        self.assertIsNone(p.after_success(2))   # pass 1 finds things
        self.assertIsNone(p.after_success(0))   # quiet 1
        self.assertEqual(p.after_success(0), CONVERGED)  # quiet 2 and 3 passes completed

    def test_quiet_streak_is_not_enough_before_min_passes(self):
        p = policy(min_passes=4, quiet_k=2)
        for _ in range(3):
            self.assertIsNone(p.after_success(0))
        self.assertEqual(p.after_success(0), CONVERGED)

    def test_new_finding_resets_the_streak(self):
        p = policy(min_passes=3, quiet_k=2)
        p.after_success(1)
        p.after_success(0)
        self.assertIsNone(p.after_success(1))
        self.assertEqual(p.quiet_streak, 0)
        self.assertIsNone(p.after_success(0))
        self.assertEqual(p.after_success(0), CONVERGED)

    def test_failures_do_not_count_as_completed_or_touch_streak(self):
        p = policy(min_passes=3, quiet_k=2, max_consecutive_failures=2)
        p.after_success(0)
        self.assertIsNone(p.after_failure())
        self.assertEqual((p.completed, p.quiet_streak), (1, 1))
        self.assertIsNone(p.after_success(0))  # a success resets the failure counter
        self.assertIsNone(p.after_failure())
        self.assertEqual(p.after_failure(), FAILURES)

    def test_budget_and_final_reason(self):
        p = policy()
        self.assertEqual(p.before_pass(True), BUDGET_EXCEEDED)
        self.assertIsNone(p.before_pass(False))
        self.assertEqual(p.final_reason(None), FAILURES)  # nothing completed
        p.after_success(1)
        self.assertEqual(p.final_reason(None), MAX_PASSES)
        self.assertEqual(p.final_reason("cancelled"), "cancelled")


if __name__ == "__main__":
    unittest.main()
