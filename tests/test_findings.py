import unittest
from pathlib import Path

from code_reviewer import diffview
from code_reviewer.findings import FileCache, FindingsPreparer, find_quote, resolve_rule_id

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

    def test_a_rule_citation_never_causes_a_grounded_finding_to_be_dropped(self):
        prepared = self.prepare([finding(rule_id="GEN-999"), finding(rule_id="none"), finding(rule_id="P:docs/review.md#logging")])
        self.assertEqual(len(prepared.ok), 3)
        self.assertEqual([f["rule_id"] for f in prepared.ok], ["none", "none", "P:docs/review.md#logging"])
        self.assertEqual(prepared.ok[0]["rule_citation"], "GEN-999")  # what the model wrote is kept for auditing
        self.assertEqual((prepared.rule_unknown, prepared.rule_repaired), (1, 0))

    def test_badly_formatted_citations_of_real_rules_are_repaired(self):
        rules = {"GEN-004": {}, "P:docs/review.md#logging": {}}
        preparer = FindingsPreparer(rules, True, self.cache, self.diff, 30)
        prepared = preparer.prepare([finding(rule_id=c) for c in ("[GEN-004]", "`gen-004`", "docs/review.md#logging", "[P:docs/review.md#logging]")], 1)
        self.assertEqual([f["rule_id"] for f in prepared.ok], ["GEN-004", "GEN-004", "P:docs/review.md#logging", "P:docs/review.md#logging"])
        self.assertEqual((prepared.rule_repaired, prepared.rule_unknown), (4, 0))
        self.assertTrue(all("rule_citation" not in f for f in prepared.ok))

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



class ResolveRuleIdTests(unittest.TestCase):
    RULES = {"GEN-010": {}, "P:docs/style.md#43-pull-request-checks": {}, "P:docs/ddd.md#aggregate": {}}

    def resolve(self, raw):
        return resolve_rule_id(raw, self.RULES)

    def test_exact_and_none(self):
        self.assertEqual(self.resolve("GEN-010"), ("GEN-010", "exact"))
        for nothing in ("none", "None", "", "  ", None):
            self.assertEqual(self.resolve(nothing), ("none", "none"))

    def test_the_formats_seen_in_a_real_run_are_repaired(self):
        self.assertEqual(self.resolve("[GEN-010]"), ("GEN-010", "repaired"))
        self.assertEqual(self.resolve("[P:docs/style.md#43-pull-request-checks]"),
                         ("P:docs/style.md#43-pull-request-checks", "repaired"))
        self.assertEqual(self.resolve("docs/style.md#43-pull-request-checks"),
                         ("P:docs/style.md#43-pull-request-checks", "repaired"))

    def test_other_cosmetic_variants(self):
        for raw in ("`GEN-010`", "gen-010", "(GEN-010)", "GEN-010 (default)", "GEN-010.", "'GEN-010'"):
            self.assertEqual(self.resolve(raw), ("GEN-010", "repaired"), raw)
        self.assertEqual(self.resolve("p:docs/ddd.md#Aggregate"), ("P:docs/ddd.md#aggregate", "repaired"))

    def test_text_that_names_no_rule_is_unknown(self):
        for raw in ("Style Guide 4.3 (Requirements Match)", "GEN-999", "P:docs/ddd.md#missing", "see the style guide"):
            self.assertEqual(self.resolve(raw), ("none", "unknown"), raw)


if __name__ == "__main__":
    unittest.main()
