"""Evidence and publication behavior on synthetic code; no model-quality claims."""
from __future__ import annotations

import copy
import json
import unittest

from code_reviewer.config import Config, PROMPTS_DIR
from code_reviewer.engine import Engine
from code_reviewer.review.evidence import reviewer_errors, evidence_errors, shape_errors
from code_reviewer.review.master import MasterList
from code_reviewer.review.prompt_contract import load_prompts
from code_reviewer.review.verification import artifact, candidate_inputs, counts, groups, publishable, validate_assessments
from code_reviewer.shared.findings import FileCache
from code_reviewer.backends.openai import strict_schema
from .helpers import TempDirTestMixin, finding, make_repo, verification_response, block
from .test_persona_flow import PersonaRunner


class VerificationTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        self.root = self.make_tmp()
        self.ctx = make_repo(self.root)
        self.cfg = Config(persona_passes=1, persona_parallel=1, criteria_mode='defaults_only',
                          runs_dir=str(self.root / 'runs'))
        self.prompts = load_prompts(PROMPTS_DIR)

    def run_flow(self, transform=None, candidate=None):
        def verify(kw):
            response = verification_response(kw)
            if transform:
                transform(response)
            return response
        self.runner = PersonaRunner(reviewer=[{'findings': [candidate or finding(rule_id='none')]}, {'findings': []}],
                                    verifier=[verify])
        return Engine(self.cfg, self.runner).review(self.ctx)

    def test_every_unique_candidate_is_investigated_without_a_conflict(self):
        result = self.run_flow()
        self.assertEqual(self.runner.counts['verifier'], 1)
        self.assertTrue(publishable(result.master.items['F-001']))
        self.assertEqual(counts(result.master)['published'], 1)
        call = next(c for c in self.runner.calls if c['role'] == 'verifier')
        self.assertEqual(call['tools'], 'Read,Grep,Glob')
        self.assertNotIn('sightings', call['user_prompt'])
        self.assertNotIn('self_check', call['user_prompt'])
        self.assertNotIn('seen_in_passes', call['user_prompt'])
        self.assertNotIn('Use a parameterized query.', result.report_path.read_text())
        data = json.loads((result.run_dir / 'findings.json').read_text())
        self.assertEqual(data['head_sha'], self.ctx.head_sha)
        self.assertEqual(data['criteria'], result.criteria.index())
        self.assertEqual(len(data['findings']), 1)
        self.assertIsNone(data['findings'][0]['candidate']['suggestion'])
        self.assertIn('Use a parameterized query.', json.dumps(data['audit']))

    def test_refuted_and_unresolved_candidates_are_private_and_never_posted(self):
        from code_reviewer.post import format_comment
        for status in ('refuted', 'unresolved'):
            with self.subTest(status=status):
                def change(response):
                    response['assessments'][0]['status'] = status
                result = self.run_flow(change)
                self.assertFalse(publishable(result.master.items['F-001']))
                self.assertNotIn('SQL built by concatenation', result.report_path.read_text())
                self.assertNotIn('SQL built by concatenation', format_comment(result, self.cfg.post_tiers, []))
                data = artifact(result)
                self.assertEqual(data['findings'], [])
                self.assertIn('SQL built by concatenation', json.dumps(data['audit']))
                if status == 'unresolved':
                    self.assertEqual(result.stop_reason, 'incomplete')
                    self.assertIn('Verification incomplete', result.report_path.read_text())

    def test_invalid_or_incomplete_verification_retries_and_withholds_candidate(self):
        transforms = [lambda r: r['assessments'].clear(),
                      lambda r: r['assessments'].append(copy.deepcopy(r['assessments'][0])),
                      lambda r: r['assessments'][0].update(evidence_refs=['V99']),
                      lambda r: r['assessments'][0].update(status='certain'),
                      lambda r: r['assessments'][0].update(remaining_uncertainties=['Caller guarantee is unknown.'])]
        for transform in transforms:
            with self.subTest(transform=transform):
                result = self.run_flow(transform)
                self.assertEqual(self.runner.counts['verifier'], 2)
                self.assertFalse(publishable(result.master.items['F-001']))
                self.assertEqual(counts(result.master)['pending'], 1)
                self.assertEqual(result.stop_reason, 'incomplete')

    def test_supported_remedy_is_separate_from_supported_defect(self):
        def remedy(response):
            response['assessments'][0]['remedy'] = dict(status='supported', explanation='Synthetic remedy evidence.',
                                                       evidence_refs=['primary'])
        result = self.run_flow(remedy)
        self.assertIn('Suggestion (supported', result.report_path.read_text())
        self.assertEqual(artifact(result)['findings'][0]['candidate']['suggestion'], finding()['suggestion'])

    def test_invalid_supplementary_citation_is_counted_and_not_merged(self):
        candidate = finding(evidence=[dict(id='S1', source='head', file='../outside', line=1,
            rule_id=None, quote='fabricated', establishes='An alleged guarantee.')])
        result = self.run_flow(candidate=candidate)
        self.assertEqual(result.master.items, {})
        self.assertEqual(result.passes[0].ungrounded, 1)
        self.assertNotIn('verifier', self.runner.counts)

    def test_criterion_provenance_uses_frozen_rule_not_head_or_model_authority(self):
        cache = FileCache(self.ctx.git_dir)
        criteria = {'P:schema': {'text': 'Use schema.rb as the declared schema snapshot.'}}
        entry = dict(id='S1', source='criterion', file=None, line=None, rule_id='P:schema',
                     quote='Use schema.rb as the declared schema snapshot.', establishes='Repository convention.')
        self.assertEqual(evidence_errors([entry], 'S', cache, criteria), [])
        for changes in ({'rule_id': 'P:invented'}, {'file': 'schema.rb'}, {'quote': 'Production matches this file.'},
                        {'source': 'head', 'file': '../schema.rb', 'line': 1}):
            with self.subTest(changes=changes):
                self.assertTrue(evidence_errors([dict(entry, **changes)], 'S', cache, criteria))

    def test_head_citations_require_exact_line_and_valid_namespace(self):
        entry = dict(id='S1', source='head', file='app.py', line=2, rule_id=None,
                     quote=finding()['quote'], establishes='Declared query construction.')
        cache = FileCache(self.ctx.git_dir)
        self.assertEqual(evidence_errors([entry], 'S', cache, {}), [])
        for changes in ({'line': 1}, {'file': 'bad\x00path'}, {'id': 'V1'}, {'rule_id': 'P:schema'}):
            self.assertTrue(evidence_errors([dict(entry, **changes)], 'S', cache, {}))
        self.assertTrue(evidence_errors([entry, entry], 'S', cache, {}))

    def test_shared_schema_checks_bounds_required_fields_and_bool_lines(self):
        schema = self.prompts['reviewer'].schema
        payload = {'findings': [finding()], 'notes': ''}
        self.assertEqual(reviewer_errors(payload, schema, True), [])
        self.assertEqual(shape_errors(payload, strict_schema(schema)), [])
        mutations = [lambda f: f.pop('reasoning'), lambda f: f.update(line=True),
                     lambda f: f.update(description=' '), lambda f: f.update(unknown='instruction'),
                     lambda f: f.update(evidence=[{}] * 13), lambda f: f.update(self_check=None),
                     lambda f: f.update(file='/etc/passwd')]
        for mutate in mutations:
            candidate = finding()
            mutate(candidate)
            self.assertTrue(reviewer_errors({'findings': [candidate]}, schema, True))
        self.assertTrue(reviewer_errors({'findings': [finding()] * 41}, schema, True))
        self.assertEqual(reviewer_errors({'findings': [finding(self_check=None)]}, schema, False), [])

    def test_overlapping_conflicts_form_one_complete_investigation(self):
        result = self.run_flow()
        master = result.master
        base = master.items['F-001']
        for ident in ('F-002', 'F-003', 'F-004'):
            master.items[ident] = copy.deepcopy(base)
            master.items[ident]['id'] = ident
        master.conflicts = {'C-001': {'findings': ['F-001', 'F-002']},
                            'C-002': {'findings': ['F-002', 'F-003']}}
        self.assertEqual(groups(master), [['F-001', 'F-002', 'F-003'], ['F-004']])
        inputs = candidate_inputs(master, ['F-001'])
        self.assertEqual(set(inputs[0]), {'id', 'candidate'})
        self.assertNotIn('sightings', inputs[0]['candidate'])

    def test_assumptions_and_anchor_only_absence_cannot_be_supported(self):
        candidate = finding(assumptions=['The production table is large.'])
        result = self.run_flow(candidate=candidate)
        self.assertEqual(result.stop_reason, 'incomplete')
        self.assertFalse(publishable(result.master.items['F-001']))
        # Validate an absence candidate directly: its anchor alone is not evidence of absence.
        master = MasterList()
        master.items['F-001'] = {'canonical': finding(kind='absent', anchor=dict(file='app.py', line_start=2, line_end=2),
                                                   expected='Validate input.')}
        response = verification_response({'user_prompt': '<conflict_claims>\n[{"id":"F-001"}]\n</conflict_claims>'})
        self.assertTrue(validate_assessments(response, self.prompts['verifier'].schema, ['F-001'], master,
                                           FileCache(self.ctx.git_dir), {}))

    def test_failed_verifier_does_not_drop_other_successfully_verified_candidates(self):
        from code_reviewer.backends.contract import LLMResult
        runner = PersonaRunner(reviewer=[{'findings': [finding(title='First'), finding(title='Second')]}, {'findings': []}],
            comparator=[lambda kw: {'decisions': [dict(local_id=f['local_id'], match='new', same_as=None,
                categories=['correctness']) for f in block(kw['user_prompt'], 'new_findings')], 'conflicts': []}],
            verifier=[LLMResult(ok=False, error='synthetic verifier failure', retryable=False), verification_response])
        result = Engine(self.cfg, runner).review(self.ctx)
        self.assertEqual(counts(result.master)['published'], 1)
        self.assertEqual(counts(result.master)['pending'], 1)
        self.assertNotIn('#### [high] First', result.report_path.read_text())
        self.assertIn('#### [high] Second', result.report_path.read_text())

    def test_conflicting_support_is_atomic_and_unresolved_relationship_blocks_publication(self):
        from .test_persona_flow import PersonaFlowTests
        # Reuse the synthetic two-claim conflict fixture, not a presentation seam.
        fixture = PersonaFlowTests()
        for relationship in ('contradictory', 'unresolved'):
            with self.subTest(relationship=relationship):
                response = fixture.annotation()
                response['relationship'] = relationship
                for claim in response['assessments']:
                    claim['status'] = 'supported'
                runner = fixture.conflict_runner(response)
                result = Engine(self.cfg, runner).review(self.ctx)
                self.assertEqual(counts(result.master)['published'], 0)
                self.assertNotIn('SQL built by concatenation', result.report_path.read_text())
                self.assertNotIn('Opposing claim', result.report_path.read_text())
                self.assertEqual(result.stop_reason, 'incomplete')

    def test_self_rejected_candidates_never_enter_reports_or_verification(self):
        candidate = finding(self_check=dict(verdict='rejected', reason='Synthetic caller guarantee defeats the claim.'))
        result = self.run_flow(candidate=candidate)
        self.assertEqual(len(result.master.rejected_only()), 1)
        self.assertNotIn('verifier', self.runner.counts)
        self.assertNotIn(candidate['title'], result.report_path.read_text())
        self.assertIn(candidate['title'], json.dumps(artifact(result)['audit']))

    def test_verifier_additions_are_grounded_not_merely_well_formed(self):
        def fabricate(response):
            claim = response['assessments'][0]
            claim['additional_evidence'] = [dict(id='V1', source='head', file='app.py', line=2, rule_id=None,
                quote='imaginary validation', establishes='Alleged caller handling.')]
            claim['evidence_refs'] = ['V1']
        result = self.run_flow(fabricate)
        self.assertEqual(self.runner.counts['verifier'], 2)
        self.assertEqual(counts(result.master)['published'], 0)
        self.assertIn('not grounded', result.master.items['F-001']['verification_error'])

    def test_verification_records_numeric_metadata_without_candidate_content(self):
        from code_reviewer.tracing import Recorder, activate, observation
        recorder = Recorder()
        with activate(recorder), observation('review.run'):
            result = self.run_flow()
        rows = recorder.snapshot()['records']
        verify = next(row for row in rows if row['name'] == 'review.verify')
        self.assertEqual(verify['attributes']['proposed'], 1)
        self.assertEqual(verify['attributes']['accepted'], 1)
        self.assertNotIn(finding()['title'], json.dumps(rows))
        self.assertTrue(publishable(result.master.items['F-001']))
