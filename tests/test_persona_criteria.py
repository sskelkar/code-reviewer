"""Keep canonical persona assets and static assignments coherent."""
from __future__ import annotations

import json
import unittest

from code_reviewer.config import REPO_ROOT
from code_reviewer.shared.criteria import load_default_rules, render_criteria_block


ASSETS = REPO_ROOT / 'src/code_reviewer/flows/agent_personas'


class PersonaCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ASSETS / 'personas.json').read_text(encoding='utf-8'))
        self.rules = load_default_rules(ASSETS / 'criteria')
        self.index = {rule.rule_id: rule for rule in self.rules}

    def test_static_allocations_cover_every_rule_without_unknown_ids(self):
        self.assertEqual(self.manifest['version'], 1)
        standard = ASSETS / self.manifest['common_instructions']
        self.assertTrue(standard.read_text(encoding='utf-8').strip())
        personas = self.manifest['personas']
        self.assertEqual([p['id'] for p in personas], [
            'correctness', 'code-quality', 'test-quality', 'production-safety',
        ])
        assigned = set()
        for persona in personas:
            self.assertTrue(persona['name'] and persona['focus'])
            ids = persona['default_rule_ids']
            self.assertTrue(ids, persona['id'])
            self.assertEqual(len(ids), len(set(ids)), persona['id'])
            self.assertFalse(set(ids) - set(self.index), persona['id'])
            assigned.update(ids)
        self.assertEqual(assigned, set(self.index))

    def test_original_concerns_and_shared_contract_have_one_canonical_definition(self):
        original = {
            'COR-001', 'COR-002', 'CQ-001', 'CQ-002', 'SAFE-001',
            'SAFE-002', 'SAFE-003', 'SAFE-004', 'TEST-001', 'TEST-002',
        }
        self.assertEqual(set(self.index), original | {'COR-003', 'TEST-003', 'SAFE-005'})
        self.assertEqual(len(self.rules), len(self.index))
        assigned_contract = [p['id'] for p in self.manifest['personas']
                             if 'COR-002' in p['default_rule_ids']]
        self.assertEqual(assigned_contract, ['correctness', 'production-safety'])
        self.assertEqual(self.index['COR-002'].path, 'correctness.md')
        self.assertEqual([p['id'] for p in self.manifest['personas']
                          if 'SAFE-005' in p['default_rule_ids']], ['production-safety'])

    def test_rules_keep_reporting_contract_and_render_with_shared_parser(self):
        for rule in self.rules:
            with self.subTest(rule=rule.rule_id):
                self.assertEqual(rule.source, 'default')
                self.assertTrue(rule.heading)
                for label in ('**Inspect:**', '**Report:**', '**Do not report:**'):
                    self.assertIn(label, rule.text)
                disabled = load_default_rules(ASSETS / 'criteria', [rule.rule_id])
                self.assertEqual({r.rule_id for r in disabled}, set(self.index) - {rule.rule_id})
        for persona in self.manifest['personas']:
            selected = [self.index[rule_id] for rule_id in persona['default_rule_ids']]
            rendered = render_criteria_block(selected)
            self.assertEqual(rendered.count('### ['), len(selected))
            for rule in selected:
                self.assertIn('### [%s] (default) %s\n%s' %
                              (rule.rule_id, rule.heading, rule.text), rendered)
