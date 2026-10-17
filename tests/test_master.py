import unittest

from code_reviewer.master import MasterList, confidence, pass_overlap, tier, validate_decisions

from .helpers import finding


def f(local_id, pass_no=1, verdict="confirmed", **kw):
    data = finding(local_id=local_id, verdict=verdict, self_check_reason="why", **kw)
    data["pass"] = pass_no
    return data


class MasterTests(unittest.TestCase):
    def test_new_findings_create_entries_and_scoring(self):
        master = MasterList()
        outcome = master.apply(1, [f("p1-f1"), f("p1-f2", file="b.py")], {})
        self.assertEqual(outcome.new_ids, ["F-001", "F-002"])
        self.assertEqual(outcome.new_scored, ["F-001", "F-002"])
        self.assertEqual(master.items["F-001"]["seen_in_passes"], [1])

    def test_match_adds_sighting_and_is_not_new(self):
        master = MasterList()
        master.apply(1, [f("p1-f1")], {})
        outcome = master.apply(2, [f("p2-f1", 2, title="Different words", severity="medium")],
                               {"p2-f1": {"match": "F-001", "same_as": None}})
        self.assertEqual(outcome.new_ids, [])
        self.assertEqual(outcome.matched_ids, ["F-001"])
        self.assertEqual(outcome.new_scored, [])
        item = master.items["F-001"]
        self.assertEqual(item["seen_in_passes"], [1, 2])
        self.assertEqual(item["canonical"]["severity"], "high")  # highest severity wins
        self.assertEqual(item["variants"][0]["title"], "Different words")

    def test_same_as_merges_duplicates_within_a_pass(self):
        master = MasterList()
        outcome = master.apply(1, [f("p1-f1"), f("p1-f2")], {"p1-f1": {"match": "new", "same_as": None},
                                                             "p1-f2": {"match": "new", "same_as": "p1-f1"}})
        self.assertEqual(len(master.items), 1)
        self.assertEqual(outcome.new_ids, ["F-001"])

    def test_rejected_only_entries_are_not_scored_until_confirmed(self):
        master = MasterList()
        outcome = master.apply(1, [f("p1-f1", verdict="rejected")], {})
        self.assertEqual(outcome.new_scored, [])
        self.assertEqual(len(master.rejected_only()), 1)
        self.assertEqual(master.items["F-001"]["rejected_reasons"], {"1": "why"})
        outcome = master.apply(2, [f("p2-f1", 2, title="Confirmed later")], {"p2-f1": {"match": "F-001", "same_as": None}})
        self.assertEqual(outcome.new_scored, ["F-001"])  # becomes scored: counts as new for convergence
        self.assertEqual(master.items["F-001"]["canonical"]["title"], "Confirmed later")
        self.assertEqual(master.items["F-001"]["rejected_in_passes"], [1])

    def test_candidates_all_when_small_and_filtered_when_large(self):
        master = MasterList()
        for i in range(5):
            master.apply(1, [f("p1-f%d" % i, file="f%d.py" % i, line=10, rule_id="none")], {})
        small = master.candidates([f("n", file="f1.py")], 20, 30, 40)
        self.assertEqual(len(small), 5)
        filtered = master.candidates([f("n", file="f1.py", line=15)], 20, 3, 40)
        self.assertEqual([c["id"] for c in filtered], ["F-002"])

    def test_confidence_and_tiers(self):
        self.assertEqual(confidence({"seen_in_passes": [1, 2, 3]}, 4), 0.75)
        self.assertEqual(confidence({"seen_in_passes": [1]}, 0), 0.0)
        self.assertEqual(tier(0.75, 0.75, 0.4), "High")
        self.assertEqual(tier(0.5, 0.75, 0.4), "Medium")
        self.assertEqual(tier(0.2, 0.75, 0.4), "Low")

    def test_pass_overlap(self):
        master = MasterList()
        master.apply(1, [f("p1-f1"), f("p1-f2", file="b.py")], {})
        master.apply(2, [f("p2-f1", 2)], {"p2-f1": {"match": "F-001", "same_as": None}})
        overlap = pass_overlap(master, [1, 2])
        self.assertAlmostEqual(overlap["mean"], 0.5)


class ValidateDecisionsTests(unittest.TestCase):
    FINDINGS = [{"local_id": "a"}, {"local_id": "b"}]

    def test_valid(self):
        decisions, errors = validate_decisions(self.FINDINGS, {"F-001"}, {"decisions": [
            {"local_id": "a", "match": "F-001", "same_as": None}, {"local_id": "b", "match": "new", "same_as": "a"}]})
        self.assertEqual(errors, [])
        self.assertEqual(decisions["b"]["same_as"], "a")

    def test_errors(self):
        _, errors = validate_decisions(self.FINDINGS, {"F-001"}, {"decisions": [
            {"local_id": "a", "match": "F-999"}, {"local_id": "a", "match": "new"}, {"local_id": "zzz", "match": "new"}]})
        self.assertTrue(any("F-999" in e for e in errors))
        self.assertTrue(any("unknown local_id" in e for e in errors))
        self.assertTrue(any("missing decision for b" in e for e in errors))
        self.assertEqual(validate_decisions(self.FINDINGS, set(), {"nope": 1})[1], ["output has no 'decisions' list"])

    def test_same_as_must_point_to_earlier_finding_and_new(self):
        decisions, _ = validate_decisions(self.FINDINGS, {"F-001"}, {"decisions": [
            {"local_id": "a", "match": "new", "same_as": "b"}, {"local_id": "b", "match": "F-001", "same_as": "a"}]})
        self.assertIsNone(decisions["a"]["same_as"])
        self.assertIsNone(decisions["b"]["same_as"])



class RateTests(unittest.TestCase):
    ITEM = {"seen_in_passes": [1]}

    def test_fewer_than_two_passes_is_unrated(self):
        from code_reviewer.master import UNRATED, rate
        self.assertEqual(rate(self.ITEM, 1, 0.75, 0.4), UNRATED)
        self.assertEqual(rate(self.ITEM, 0, 0.75, 0.4), UNRATED)

    def test_two_or_more_passes_use_the_tiers(self):
        from code_reviewer.master import rate
        self.assertEqual(rate({"seen_in_passes": [1, 2]}, 2, 0.75, 0.4), "High")
        self.assertEqual(rate(self.ITEM, 2, 0.75, 0.4), "Medium")
        self.assertEqual(rate(self.ITEM, 4, 0.75, 0.4), "Low")


if __name__ == "__main__":
    unittest.main()
