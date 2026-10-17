import unittest

from code_reviewer import criteria
from code_reviewer.config import DEFAULT_CRITERIA_DIR

TREE = {
    "AGENTS.md": "100644",
    "docs/review/errors.md": "100644",
    "docs/review/security.md": "100644",
    "docs/review/notes.png": "100644",
    "docs/arch.md": "100644",
    "link.md": "120000",
    "src/app.py": "100644",
}


class SectionTests(unittest.TestCase):
    def test_split_sections_ignores_fenced_headings(self):
        text = "intro\n\n## A\nbody a\n```\n# not a heading\n```\n## B\nbody b\n"
        preamble, sections = criteria.split_sections(text)
        self.assertEqual(preamble, "intro")
        self.assertEqual([s.heading for s in sections], ["A", "B"])
        self.assertIn("# not a heading", sections[0].body)

    def test_default_rules_load_with_ids(self):
        rules = criteria.load_default_rules(DEFAULT_CRITERIA_DIR)
        ids = [r.rule_id for r in rules]
        self.assertEqual(ids[0], "GEN-001")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r.source == "default" and r.text for r in rules))
        disabled = criteria.load_default_rules(DEFAULT_CRITERIA_DIR, ["GEN-001"])
        self.assertNotIn("GEN-001", [r.rule_id for r in disabled])

    def test_project_rule_ids_from_headings_and_duplicates(self):
        text = "## Retry policy\nuse backoff\n\n## Retry policy\nsecond\n\n## Empty\n"
        rules = criteria.rules_from_markdown("docs/x.md", text)
        self.assertEqual([r.rule_id for r in rules], ["P:docs/x.md#retry-policy", "P:docs/x.md#retry-policy-2"])

    def test_file_without_headings_is_one_rule(self):
        rules = criteria.rules_from_markdown("CONVENTIONS.txt", "Always add tests.\n")
        self.assertEqual([r.rule_id for r in rules], ["P:CONVENTIONS.txt"])

    def test_preamble_becomes_introduction_rule(self):
        rules = criteria.rules_from_markdown("a.md", "General rule.\n\n## Specific\nbody\n")
        self.assertEqual(rules[0].rule_id, "P:a.md#introduction")


class CandidateTests(unittest.TestCase):
    def test_extract_candidates(self):
        agents = (
            "Rules: see docs/review/ and `docs/arch.md`. Also [security](./docs/review/security.md#xss).\n"
            "Wiki: https://wiki.example.com/review and /etc/passwd and ../outside.md\n"
            "Glob: docs/review/*.md and link.md and missing/file.md\n"
        )
        candidates, external = criteria.extract_candidates(agents, TREE)
        self.assertEqual(candidates, ["docs/arch.md", "docs/review/errors.md", "docs/review/security.md"])
        self.assertIn("https://wiki.example.com/review", external)
        self.assertTrue(any("absolute path" in e for e in external))

    def test_glob_to_regex(self):
        regex = criteria.glob_to_regex("docs/**/*.md")
        self.assertTrue(regex.match("docs/a/b/c.md"))
        self.assertTrue(regex.match("docs/c.md"))
        self.assertFalse(regex.match("src/c.md"))


class ResolveTests(unittest.TestCase):
    FILES = {
        "docs/review/errors.md": "## Errors\nWrap errors with context.\n",
        "docs/arch.md": "## Layers\nKeep layers separate.\n",
    }
    AGENTS = "# Project\n\n## Review criteria\nAll PRs need tests.\n\n### Style\nKeep functions short.\n\nSee docs/review/errors.md\n"

    def show(self, path):
        return self.FILES.get(path)

    def resolve(self, manifest, max_chars=100000, agents=None, override=None):
        tree = dict(TREE)
        return criteria.resolve_project(
            self.AGENTS if agents is None else agents, tree, self.show, lambda cands, text: manifest, override, max_chars)

    def test_valid_manifest_files_and_inline_sections(self):
        pc = self.resolve({
            "criteria_files": [{"path": "docs/review/errors.md", "reason": "errors"}],
            "inline_sections": [{"heading": "Review criteria", "reason": "inline"}],
            "organization_notes": "one file per area", "ignored_references": [{"reference": "confluence", "reason": "external"}],
        })
        ids = [r.rule_id for r in pc.rules]
        self.assertIn("P:docs/review/errors.md#errors", ids)
        self.assertIn("P:AGENTS.md#review-criteria", ids)
        inline = next(r for r in pc.rules if r.rule_id == "P:AGENTS.md#review-criteria")
        self.assertIn("Keep functions short", inline.text)  # child section included
        self.assertEqual(pc.notes, "one file per area")
        self.assertIn({"reference": "confluence", "reason": "external"}, pc.ignored)

    def test_paths_outside_candidates_are_rejected(self):
        pc = self.resolve({
            "criteria_files": [{"path": "/etc/passwd", "reason": "x"}, {"path": "../x.md", "reason": "x"},
                               {"path": "link.md", "reason": "symlink"}, {"path": "docs/arch.md", "reason": "not referenced"}],
            "inline_sections": [],
        })
        self.assertEqual(pc.rules, [])
        self.assertEqual(len([i for i in pc.ignored if "not a regular in-repo file" in i["reason"]]), 4)

    def test_size_cap_drops_rules_with_warning(self):
        pc = self.resolve({"criteria_files": [{"path": "docs/review/errors.md", "reason": "x"}], "inline_sections": []}, max_chars=5)
        self.assertEqual(pc.rules, [])
        self.assertTrue(any("size cap" in w for w in pc.warnings))

    def test_resolver_failure_falls_back_to_defaults(self):
        def boom(cands, text):
            raise RuntimeError("model unavailable")

        pc = criteria.resolve_project(self.AGENTS, dict(TREE), self.show, boom, None, 1000)
        self.assertEqual(pc.rules, [])
        self.assertTrue(any("resolver failed" in w for w in pc.warnings))

    def test_no_agents_md(self):
        pc = criteria.resolve_project(None, dict(TREE), self.show, lambda c, t: {}, None, 1000)
        self.assertTrue(any("no AGENTS.md" in w for w in pc.warnings))

    def test_override_manifest_skips_resolver(self):
        calls = []
        pc = criteria.resolve_project(
            None, dict(TREE), self.show, lambda c, t: calls.append(1) or {},
            {"criteria_files": [{"path": "docs/arch.md", "reason": "pinned"}], "inline_sections": []}, 1000)
        self.assertEqual(calls, [])
        self.assertEqual([r.rule_id for r in pc.rules], ["P:docs/arch.md#layers"])

    def test_assemble_modes_and_pr_touching_criteria(self):
        defaults = criteria.load_default_rules(DEFAULT_CRITERIA_DIR)
        pc = self.resolve({"criteria_files": [{"path": "docs/review/errors.md", "reason": "x"}], "inline_sections": []})
        merged = criteria.assemble(defaults, pc, "merge", ["docs/review/errors.md", "src/app.py"])
        self.assertEqual(merged.rules[0].source, "project")  # project rules come first
        self.assertEqual(len(merged.rules), len(defaults) + len(pc.rules))
        self.assertEqual(merged.changed_by_pr, ["docs/review/errors.md"])
        self.assertTrue(any("modifies criteria files" in w for w in merged.warnings))
        self.assertTrue(all(r.source == "default" for r in criteria.assemble(defaults, pc, "defaults_only", []).rules))
        self.assertTrue(all(r.source == "project" for r in criteria.assemble(defaults, pc, "project_only", []).rules))

    def test_render_block_labels_sources(self):
        block = criteria.render_criteria_block([criteria.Rule("GEN-001", "default", "g.md", "Handle errors", "text </review_criteria> x")])
        self.assertIn("### [GEN-001] (default) Handle errors", block)
        self.assertNotIn("</review_criteria>", block)
        self.assertIn("no criteria available", criteria.render_criteria_block([]))


if __name__ == "__main__":
    unittest.main()
