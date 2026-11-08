from __future__ import annotations

import unittest

from code_reviewer.shared import criteria
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
        self.assertEqual(sorted(ids), sorted(['COR-001','COR-002','COR-003','CQ-001','CQ-002','TEST-001','TEST-002','TEST-003','SAFE-001','SAFE-002','SAFE-003','SAFE-004','SAFE-005']))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r.source == "default" and r.text for r in rules))
        for rule in rules:
            for label in ("**Inspect:**", "**Report:**", "**Do not report:**"):
                self.assertIn(label, rule.text, rule.rule_id)
        disabled = criteria.load_default_rules(DEFAULT_CRITERIA_DIR, ["COR-001"])
        self.assertNotIn("COR-001", [r.rule_id for r in disabled])

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


class ExplicitGuidanceTests(unittest.TestCase):
    def resolve(self, files, changed=('src/app.py',), max_chars=100000):
        return criteria.resolve_project({p: '100644' for p in files}, files.get, max_chars, changed)

    def test_general_instructions_and_architecture_do_not_become_criteria(self):
        pc = self.resolve({'AGENTS.md': '## Coding conventions\nAlways use factories.\nSee docs/architecture.md',
                           'README.md': '## Setup\nRun tests.',
                           'docs/architecture.md': '## Layers\nUse queues.',
                           '.claude/rules/general.md': '## Rules\nAlways use mocks.'})
        self.assertEqual(pc.rules, [])
        defaults = criteria.load_default_rules(DEFAULT_CRITERIA_DIR)
        self.assertEqual(criteria.assemble(defaults, pc, []).rules, defaults)

    def test_implementation_review_package_is_not_project_guidance(self):
        pc = self.resolve({'src/tool/review/prompts/reviewer.system.md': '# Role\nFollow docs/architecture.md',
                           'docs/architecture.md': '# Layers\nUse queues.'})
        self.assertEqual(pc.rules, [])
        self.assertEqual(criteria.assemble(criteria.load_default_rules(DEFAULT_CRITERIA_DIR), pc, []).rules,
                         criteria.load_default_rules(DEFAULT_CRITERIA_DIR))

    def test_documentation_and_skill_review_directories_are_explicit(self):
        pc = self.resolve({'docs/review/errors.md': '# Errors\nCheck cleanup.',
                           '.agents/skills/code-review/SKILL.md': '# Checks\nCheck callers.'})
        self.assertEqual(set(pc.files_used), {'docs/review/errors.md', '.agents/skills/code-review/SKILL.md'})

    def test_readme_review_section_and_explicit_reference_only(self):
        pc = self.resolve({'README.md': '## Setup\nInstall things.\n## Code review\nUse [checks](docs/checks.md).\n### Exceptions\nIgnore generated code.\n## Architecture\nUse factories.',
                           'docs/checks.md': '## Retries\nCheck replay safety.'})
        self.assertEqual(len(pc.rules), 2)
        self.assertIn('Ignore generated code', pc.rules[0].text)
        self.assertNotIn('Use factories', pc.rules[0].text)
        self.assertEqual(set(pc.files_used), {'README.md', 'docs/checks.md'})

    def test_review_skill_and_relative_references_without_execution(self):
        pc = self.resolve({'.agents/skills/a/SKILL.md': '---\nname: code-review\ndescription: Review changes.\n---\n# Checks\nRead [details](references/checks.md). Never run hooks.',
                           '.agents/skills/a/references/checks.md': 'Check caller contracts.',
                           '.agents/skills/build/SKILL.md': '---\nname: build\n---\n# Build\nExecute build.sh'})
        self.assertEqual(set(pc.files_used), {'.agents/skills/a/SKILL.md', '.agents/skills/a/references/checks.md'})

    def test_nested_instructions_scoping_and_rule_frontmatter(self):
        pc = self.resolve({'src/AGENTS.md': '## Code review\nCheck cancellation.',
                           'other/AGENTS.md': '## Code review\nUnrelated.',
                           '.claude/rules/review.md': '---\npaths: other/**\n---\n# Code review\nUnrelated.',
                           'src/README.md': '## Setup\nDo not import as rules.'})
        self.assertEqual(pc.files_used, ['src/AGENTS.md'])

    def test_dedicated_review_document_and_cycles_are_bounded(self):
        pc = self.resolve({'docs/code-review.md': '# Checks\nSee code-review.md and https://example.com/policy'})
        self.assertEqual(len(pc.rules), 1)
        self.assertEqual(len(pc.files_used), 1)
        self.assertEqual(pc.ignored[0]['reference'], 'https://example.com/policy')

    def test_parent_relative_skill_reference_and_missing_reference(self):
        pc = self.resolve({'skills/code-review/SKILL.md': '# Rules\nUse [shared](../shared.md) and [missing](missing.md).',
                           'skills/shared.md': 'Check error propagation.'})
        self.assertIn('skills/shared.md', pc.files_used)
        self.assertTrue(any('reference unavailable' in w for w in pc.warnings))

    def test_limits_and_symlinks_do_not_silently_load_partial_guidance(self):
        pc = self.resolve({'code-review.md': 'x' * 100}, max_chars=10)
        self.assertEqual(pc.rules, [])
        self.assertTrue(any('cap' in w for w in pc.warnings))
        pc = criteria.resolve_project({'code-review.md': '120000'}, lambda p: self.fail('symlink read'), 1000)
        self.assertEqual(pc.rules, [])

    def test_base_sources_and_modified_guidance_are_recorded(self):
        pc = self.resolve({'AGENTS.md': '## Code review\nCheck state.'})
        merged = criteria.assemble(criteria.load_default_rules(DEFAULT_CRITERIA_DIR), pc, ['AGENTS.md'])
        self.assertEqual(merged.changed_by_pr, ['AGENTS.md'])
        self.assertEqual(merged.rules, pc.rules)
    def test_explicit_reference_upgrades_previously_inspected_documents_in_either_order(self):
        for source, target in [('AGENTS.md', 'README.md'), ('README.md', 'AGENTS.md')]:
            with self.subTest(source=source):
                pc = self.resolve({source: '## Code review\nFollow [standards](' + target + ').',
                                   target: '## Coding standards\nNever swallow errors.'})
                self.assertIn('Never swallow errors.', '\n'.join(r.text for r in pc.rules))
                self.assertEqual(pc.files_used.count(target), 1)
                self.assertEqual(len({r.rule_id for r in pc.rules}), len(pc.rules))

    def test_reference_upgrade_replaces_partial_selection_without_duplicate_rules(self):
        pc = self.resolve({'AGENTS.md': '## Code review\nFollow [standards](README.md).',
                           'README.md': '## Code review\nCheck errors.\n## Standards\nCheck cleanup.'})
        rules = [r for r in pc.rules if r.path == 'README.md']
        self.assertEqual(len(rules), 2)
        self.assertEqual(len({r.rule_id for r in pc.rules}), len(pc.rules))

    def test_entry_documents_are_prioritized_over_unrelated_skills(self):
        files = {'.agents/skills/build/SKILL.md': '---\nname: build\n---\n' + 'x' * 100,
                 'AGENTS.md': '## Code review\nCheck cancellation.'}
        pc = self.resolve(files, max_chars=140)
        self.assertEqual(pc.files_used, ['AGENTS.md'])

    def test_explicit_references_precede_unrelated_skill_candidate_limit(self):
        files = {f'.agents/skills/build-{i:03}/SKILL.md': '# Build\nCompile things.' for i in range(201)}
        files.update({'AGENTS.md': '## Code review\nFollow [checks](docs/checks.md).',
                      'docs/checks.md': 'Check error propagation.'})
        reads = []
        pc = criteria.resolve_project({p: '100644' for p in files},
                                      lambda p: reads.append(p) or files[p], 100000, ['src/app.py'])
        self.assertIn('docs/checks.md', pc.files_used)
        self.assertLessEqual(len(reads), criteria.MAX_CANDIDATES)
        self.assertTrue(any('candidate cap' in w for w in pc.warnings))

    def test_oversized_cycle_is_read_once_and_warns(self):
        files = {'code-review.md': '# Checks\nFollow [large](large.md).', 'large.md': 'x' * 1000}
        reads = []
        pc = criteria.resolve_project({p: '100644' for p in files},
                                      lambda p: reads.append(p) or files[p], 100)
        self.assertEqual(reads.count('large.md'), 1)
        self.assertTrue(any('cap' in w for w in pc.warnings))

    def test_nested_document_can_reference_a_root_path_without_false_warning(self):
        pc = self.resolve({'skills/code-review/SKILL.md': '# Checks\nFollow [standards](docs/checks.md).',
                           'docs/checks.md': 'Check caller contracts.'})
        self.assertIn('docs/checks.md', pc.files_used)
        self.assertFalse(any('unavailable' in w for w in pc.warnings))

    def test_repeated_review_headings_keep_distinct_rule_ids(self):
        pc = self.resolve({'AGENTS.md': '## Code review\nCheck errors.\n## Code review\nCheck cleanup.'})
        self.assertEqual(len(pc.rules), 2)
        self.assertEqual(len({r.rule_id for r in pc.rules}), 2)

    def test_real_input_builder_uses_project_or_enabled_baseline_without_resolver_calls(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from code_reviewer.config import Config
        from code_reviewer.engine import Engine
        from .helpers import FakeRunner, make_repo
        for project in (False, True):
            with self.subTest(project=project), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                ctx = make_repo(root, project_criteria=project)
                engine = Engine(Config(disabled_default_rules=['COR-001']), FakeRunner([]))
                files, _ = engine.inputs.load_diff(ctx)
                actual = engine.inputs.resolve_criteria(ctx, files)
                if project:
                    self.assertTrue(actual.rules)
                    self.assertTrue(all(r.source == 'project' for r in actual.rules))
                else:
                    expected = criteria.load_default_rules(DEFAULT_CRITERIA_DIR, ['COR-001'])
                    self.assertEqual(actual.rules, expected)


class ScopedFileTests(unittest.TestCase):
    TREE = {
        "AGENTS.md": "100644",
        "spec/AGENTS.md": "100644",
        "spec/controllers/AGENTS.md": "100644",
        "spec/workers/AGENTS.md": "100644",
        "app/AGENTS.md": "120000",
        ".claude/rules/always.md": "100644",
        ".claude/rules/controllers.md": "100644",
        ".claude/rules/specs.md": "100644",
        ".claude/rules/listed.md": "100644",
        ".claude/rules/notes.txt": "100644",
    }
    FILES = {
        "spec/AGENTS.md": "## Specs\nMirror app/.\n",
        "spec/controllers/AGENTS.md": "## Controller specs\nShare examples.\n",
        "spec/workers/AGENTS.md": "## Worker specs\nTest retries.\n",
        ".claude/rules/always.md": "## Always\nApplies everywhere.\n",
        ".claude/rules/controllers.md": "---\npaths: app/controllers/**/*.rb\n---\n\n## Controllers\nValidate input.\n",
        ".claude/rules/specs.md": "---\npaths:\n  - \"spec/controllers/**/*\"\n  - 'spec/models/**/*'\n---\n## Specs\nUse shared examples.\n",
        ".claude/rules/listed.md": "## Listed\nAlso chosen by the resolver.\n",
    }

    def show(self, path):
        return self.FILES.get(path)

    def test_front_matter_globs_are_read_and_removed(self):
        self.assertEqual(criteria.split_front_matter(self.FILES[".claude/rules/controllers.md"])[0], ["app/controllers/**/*.rb"])
        globs, body = criteria.split_front_matter(self.FILES[".claude/rules/specs.md"])
        self.assertEqual(globs, ["spec/controllers/**/*", "spec/models/**/*"])
        self.assertTrue(body.startswith("## Specs"))
        self.assertEqual(criteria.split_front_matter("## No front matter\n"), ([], "## No front matter\n"))
        self.assertEqual(criteria.split_front_matter("---\ntitle: x\n---\nbody\n"), ([], "body\n"))
        self.assertEqual(criteria.split_front_matter("---\npaths: [\"a/**\", b/*.rb]\n---\nx")[0], ["a/**", "b/*.rb"])



if __name__ == '__main__':
    unittest.main()
