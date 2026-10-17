import unittest
from pathlib import Path

from code_reviewer import diffview
from code_reviewer.findings import FileCache, FindingsPreparer, find_quote

from .helpers import TempDirTestMixin, finding

DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def get(user_id):
-    return db.run("SELECT 1")
+    query = "SELECT * FROM t WHERE id=" + user_id
+    return db.run(query)
"""
SOURCE = 'def get(user_id):\n\n    query = "SELECT * FROM t WHERE id=" + user_id\n    return db.run(query)\n'


class GroundingTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.root = self.make_tmp()
        (self.root / "app.py").write_text(SOURCE)
        self.diff = diffview.DiffIndex(diffview.parse_unified_diff(DIFF))
        self.cache = FileCache(self.root)
        self.rules = {"GEN-004": {}, "P:docs/review.md#logging": {}}

    def prepare(self, findings, self_check=True):
        return FindingsPreparer(self.rules, self_check, self.cache, self.diff, 30).prepare(findings, 1)

    def test_quote_matches_and_line_is_corrected(self):
        prepared = self.prepare([finding(line=99)])
        self.assertEqual(len(prepared.ok), 1)
        self.assertEqual(prepared.ok[0]["line"], 3)  # blank lines are skipped when matching, real line number is kept
        self.assertEqual(prepared.ok[0]["local_id"], "p1-f1")
        self.assertEqual(prepared.line_corrected, 1)

    def test_fabricated_quote_is_ungrounded(self):
        prepared = self.prepare([finding(quote="exec(user_input)")])
        self.assertEqual(len(prepared.ok), 0)
        self.assertEqual(prepared.ungrounded[0]["status"], "ungrounded")

    def test_whitespace_differences_are_tolerated_and_partial_lines_match(self):
        prepared = self.prepare([finding(quote='query   =  "SELECT * FROM t WHERE id="')])
        self.assertEqual(len(prepared.ok), 1)

    def test_multiline_quote(self):
        prepared = self.prepare([finding(quote='query = "SELECT * FROM t WHERE id=" + user_id\nreturn db.run(query)')])
        self.assertEqual(len(prepared.ok), 1)

    def test_removed_code_quote_is_grounded_via_diff(self):
        prepared = self.prepare([finding(quote='return db.run("SELECT 1")', line=2)])
        self.assertEqual(len(prepared.ok), 1)

    def test_missing_file_is_ungrounded(self):
        prepared = self.prepare([finding(file="nope.py")])
        self.assertEqual(len(prepared.ungrounded), 1)

    def test_path_traversal_is_ungrounded(self):
        prepared = self.prepare([finding(file="../secrets.txt", quote="x")])
        self.assertEqual(len(prepared.ungrounded), 1)

    def test_absent_finding_needs_anchor_over_changed_hunk(self):
        good = finding(kind="absent", quote="", anchor={"file": "app.py", "line_start": 2, "line_end": 3, "symbol": "get"},
                       expected="validate user_id", line=0)
        bad = finding(kind="absent", quote="", anchor={"file": "app.py", "line_start": 50, "line_end": 60},
                      expected="validate user_id")
        no_expected = finding(kind="absent", quote="", anchor={"file": "app.py", "line_start": 2, "line_end": 3}, expected="")
        prepared = self.prepare([good, bad, no_expected])
        self.assertEqual([f["local_id"] for f in prepared.ok], ["p1-f1"])
        self.assertEqual(prepared.ok[0]["line"], 2)  # taken from the anchor
        self.assertEqual(len(prepared.ungrounded), 2)

    def test_present_without_quote_is_ungrounded(self):
        prepared = self.prepare([finding(quote="")])
        self.assertEqual(prepared.ungrounded[0]["reason"], "kind present requires a quote")

    def test_unknown_rule_id_is_invalid_but_none_is_fine(self):
        prepared = self.prepare([finding(rule_id="GEN-999"), finding(rule_id="none"), finding(rule_id="P:docs/review.md#logging")])
        self.assertEqual(len(prepared.invalid_rule), 1)
        self.assertEqual(len(prepared.ok), 2)

    def test_self_check_verdicts(self):
        prepared = self.prepare([
            finding(self_check={"verdict": "rejected", "reason": "guarded upstream"}),
            finding(self_check=None),
        ])
        self.assertEqual([f["verdict"] for f in prepared.ok], ["rejected", "confirmed"])
        self.assertEqual(prepared.self_check_missing, 1)
        off = self.prepare([finding(self_check={"verdict": "rejected", "reason": "x"})], self_check=False)
        self.assertEqual(off.ok[0]["verdict"], "confirmed")

    def test_malformed_findings(self):
        prepared = self.prepare(["junk", {"title": "", "kind": "present"}, finding(kind="weird")])
        self.assertEqual(len(prepared.malformed), 3)
        self.assertEqual(self.prepare(None).all_records(), [])

    def test_invalid_severity_defaults_to_low(self):
        self.assertEqual(self.prepare([finding(severity="critical")]).ok[0]["severity"], "low")

    def test_find_quote_returns_all_starts(self):
        lines = [(1, "a b"), (2, "x"), (3, "a b")]
        self.assertEqual(find_quote(lines, "a b"), [1, 3])


if __name__ == "__main__":
    unittest.main()
