import unittest

from code_reviewer.claude_cli import ClaudeResult
from code_reviewer.stats import Stats


class StatsBudgetTokensTests(unittest.TestCase):
    def test_budget_tokens_exclude_cache_reads(self):
        stats = Stats()
        tokens = {"input": 10, "output": 400, "cache_creation": 100, "cache_read": 700000}
        stats.record("reviewer", 1, ClaudeResult(ok=True, tokens=tokens), 1, "haiku")
        self.assertEqual(stats.total_tokens(), 700510)
        self.assertEqual(stats.budget_tokens(), 510)
        self.assertEqual(stats.to_dict()["budget_tokens"], 510)


if __name__ == "__main__":
    unittest.main()
