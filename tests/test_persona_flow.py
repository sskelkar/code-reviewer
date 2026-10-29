"""Synthetic end-to-end persona reviews; no real model or network calls."""
from __future__ import annotations

import json
import re
import unittest

from code_reviewer.config import Config
from code_reviewer.engine import Engine
from code_reviewer.flows.agent_personas.contracts import validate_annotation, validate_comparison
from code_reviewer.flows.agent_personas.criteria import PERSONAS
from code_reviewer.flows.agent_personas.master import MasterList, agreement
from code_reviewer.flows.agent_personas.report import render_summary
from code_reviewer.shared.claude_cli import ClaudeResult
from code_reviewer.shared.findings import FileCache
from .helpers import TempDirTestMixin, finding, make_repo


def block(text, name):
    return json.loads(re.search('<' + name + r'>\n(.*?)\n</' + name + '>', text, re.S).group(1))


class PersonaRunner:
    def __init__(self, reviewer=None, comparator=None, classifier=None, verifier=None, cost=0.001):
        self.scripts = dict(reviewer=reviewer, comparator=comparator, classifier=classifier, verifier=verifier)
        self.calls, self.counts, self.cost = [], {}, cost

    def version(self):
        return 'fake-personas'

    def run(self, **kw):
        system = kw['system_prompt']
        role = 'classifier' if 'distribute project review' in system else 'comparator' if 'consolidate persona' in system else 'verifier' if 'investigate one flagged group' in system else 'resolver' if 'identify which files' in system else 'reviewer'
        self.calls.append(dict(kw, role=role))
        index = self.counts.get(role, 0)
        self.counts[role] = index + 1
        script = self.scripts.get(role)
        value = script[min(index, len(script) - 1)] if script else None
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(kw)
        if value is None:
            if role == 'reviewer':
                value = {'findings': []}
            elif role == 'resolver':
                value = {'criteria_files': [{'path': 'docs/review.md'}], 'inline_sections': [], 'ignored_references': []}
            elif role == 'classifier':
                value = {'assignments': [{'rule_id': r['rule_id'], 'personas': ['correctness'], 'reason': 'Relevant behavior rule'}
                                        for r in block(kw['user_prompt'], 'project_rules')]}
            elif role == 'comparator':
                incoming = block(kw['user_prompt'], 'new_findings')
                master = block(kw['user_prompt'], 'candidate_findings')
                value = {'decisions': [{'local_id': f['local_id'], 'match': master[0]['id'] if master else 'new',
                                       'same_as': None, 'categories': ['production-safety']} for f in incoming], 'conflicts': []}
            else:
                raise AssertionError('unexpected verifier call')
        if isinstance(value, ClaudeResult):
            if value.cost_usd is None:
                value.cost_usd = self.cost
            return value
        return ClaudeResult(ok=True, parsed=value, raw={'structured_output': value}, cost_usd=self.cost,
                            tokens={'input': 10, 'output': 5, 'cache_creation': 0, 'cache_read': 0})


class PersonaFlowTests(TempDirTestMixin, unittest.TestCase):
    def setUp(self):
        from .helpers import synthetic_frontend
        synthetic_frontend(self)
        self.tmp = self.make_tmp()
        self.ctx = make_repo(self.tmp)
        self.cfg = Config(review_mode='agent-personas', persona_parallel=1, criteria_mode='defaults_only', runs_dir=str(self.tmp / 'runs'))

    def run_flow(self, runner, **changes):
        for key, value in changes.items():
            setattr(self.cfg, key, value)
        return Engine(self.cfg, runner).review(self.ctx)

    def test_quiet_reviews_still_run_all_twelve_fresh_slots(self):
        runner = PersonaRunner()
        result = self.run_flow(runner)
        self.assertEqual(result.stop_reason, 'completed')
        self.assertEqual(result.completed_by_persona, dict.fromkeys(PERSONAS, 3))
        self.assertEqual([s.persona for s in result.passes], list(PERSONAS) * 3)
        self.assertEqual(runner.counts, {'reviewer': 12})
        self.assertEqual(len({str(c['cwd']) for c in runner.calls}), 1)
        self.assertFalse(runner.calls[0]['cwd'].exists())
        for n, persona in enumerate(PERSONAS):
            calls = runner.calls[n::4]
            self.assertEqual(len({(c['system_prompt'], c['user_prompt']) for c in calls}), 1)
            directory = result.run_dir / 'input/personas' / persona
            self.assertEqual((directory / 'reviewer.system.md').read_text(), calls[0]['system_prompt'])
            self.assertEqual((directory / 'reviewer.user.md').read_text(), calls[0]['user_prompt'])
            self.assertEqual(calls[0]['tools'], 'Read,Grep,Glob')
            self.assertNotIn('p1-f1', calls[0]['user_prompt'])
        self.assertTrue((result.run_dir / 'pass-12/slot.json').is_file())

    def test_configured_pass_counts_schedule_all_rounds_with_correct_agreement_and_progress(self):
        import io
        from code_reviewer.progress import ProgressPrinter
        previous_prompts = None
        for count in (1, 2, 5):
            with self.subTest(count=count):
                self.cfg.persona_passes = count
                runner = PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]}])
                output = io.StringIO()
                result = Engine(self.cfg, runner, emit=ProgressPrinter(stream=output)).review(self.ctx)
                self.assertEqual(result.stop_reason, 'completed')
                self.assertEqual(len(result.passes), 4 * count)
                self.assertEqual([s.persona for s in result.passes], list(PERSONAS) * count)
                self.assertEqual([s.pass_no for s in result.passes], list(range(1, 4 * count + 1)))
                self.assertEqual(result.completed_by_persona, dict.fromkeys(PERSONAS, count))
                report = result.report_path.read_text()
                self.assertIn('agent-personas: %d/%d completed' % (4 * count, 4 * count), report)
                self.assertIn('round %d/%d (slot %d/%d)' % (count, count, 4 * count, 4 * count), output.getvalue())
                rates = agreement(result.master.items['F-001'], result.completed_by_persona, self.cfg)
                for persona in PERSONAS:
                    self.assertEqual(rates[persona], {'seen': count, 'completed': count, 'tier': 'Unrated' if count == 1 else 'High'})
                    self.assertIn('%s %d/%d' % (persona, count, count), report)
                saved = json.loads((result.run_dir / 'config.json').read_text())
                self.assertEqual(saved['config']['persona_passes'], count)
                stats = json.loads((result.run_dir / 'stats.json').read_text())
                self.assertEqual(len(stats['passes']), 4 * count)
                calls = [c for c in runner.calls if c['role'] == 'reviewer']
                prompts = {}
                for offset, persona in enumerate(PERSONAS):
                    repeated = {(c['system_prompt'], c['user_prompt']) for c in calls[offset::4]}
                    self.assertEqual(len(repeated), 1)
                    prompts[persona] = repeated.pop()
                if previous_prompts is not None:
                    self.assertEqual(prompts, previous_prompts)
                previous_prompts = prompts

    def test_configured_count_still_reports_budget_interruption_and_actual_vote_denominators(self):
        result = self.run_flow(PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]}], cost=0.01),
                               persona_passes=5, run_budget_tokens=75)
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertEqual(len(result.passes), 20)
        self.assertEqual(result.passes_completed, 1)
        summary = render_summary(result, ['High'], [])
        self.assertIn('agent-personas: 1/20 completed', summary)
        self.assertIn('correctness 1/5', summary)
        self.assertIn('correctness: 1/1 (Unrated)', summary)

    def test_categories_never_transfer_votes_and_filtering_is_per_persona(self):
        runner = PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]}, {'findings': []}, {'findings': []}, {'findings': []}])
        result = self.run_flow(runner)
        item = result.master.items['F-001']
        self.assertEqual(item['categories'], ['production-safety'])
        self.assertEqual(item['sightings'], {'correctness': [1]})
        rates = agreement(item, result.completed_by_persona, result.cfg)
        self.assertEqual(rates, {'correctness': {'seen': 1, 'completed': 3, 'tier': 'Low'}})
        self.assertNotIn('SQL built by concatenation', render_summary(result, ['High'], []))
        self.assertIn('SQL built by concatenation', render_summary(result, ['Low'], []))
        self.assertNotIn('SQL built by concatenation', render_summary(result, ['Low'], ['F-001']))

    def test_all_personas_can_independently_discover_the_same_issue(self):
        result = self.run_flow(PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]}]))
        item = result.master.items['F-001']
        self.assertEqual(len(result.master.items), 1)
        self.assertEqual({p: a['seen'] for p, a in agreement(item, result.completed_by_persona, self.cfg).items()}, dict.fromkeys(PERSONAS, 3))
        self.assertIn('production-safety: 3/3 (High)', result.report_path.read_text())
        self.assertIn('Validate inputs and enforce finite bounds', result.report_path.read_text())

    def test_project_rules_are_routed_without_rewriting_and_full_prompts_saved(self):
        result = self.run_flow(PersonaRunner(), criteria_mode='merge')
        project = [r for r in result.criteria.rules if r.source == 'project']
        self.assertTrue(project)
        self.assertTrue(all(r in result.assignments['correctness'] for r in project))
        self.assertFalse(any(r.source == 'project' for r in result.assignments['code-quality']))
        self.assertTrue((result.run_dir / 'input/classifier.raw.requests.json').is_file())
        for rule in project:
            self.assertIn(rule.text, (result.run_dir / 'input/personas/correctness/reviewer.user.md').read_text())

    def test_invalid_classification_retries_then_falls_back_without_losing_rules(self):
        runner = PersonaRunner(classifier=[{'assignments': []}])
        result = self.run_flow(runner, criteria_mode='merge')
        self.assertEqual(runner.counts['classifier'], 2)
        project = [r for r in result.criteria.rules if r.source == 'project']
        for rules in result.assignments.values():
            self.assertTrue(all(r in rules for r in project))
        self.assertIn('assigned to all personas', ' '.join(result.warnings))
        self.assertTrue(result.diagnostics)
        requests = json.loads((result.run_dir / 'input/classifier.raw.requests.json').read_text())
        self.assertIn('previous answer was rejected', requests[1]['user_prompt'])

    def test_budget_stops_between_review_and_comparison_without_counting_a_vote(self):
        runner = PersonaRunner(reviewer=[{'findings': [finding()]}], cost=0.01)
        result = self.run_flow(runner, run_budget_usd=0.015)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertEqual(result.passes_completed, 0)
        self.assertEqual(result.master.items, {})
        self.assertEqual(result.passes[0].status, 'failed')
        self.assertTrue(all(s.status == 'skipped' for s in result.passes[1:]))

    def test_failures_have_fixed_slots_and_do_not_supply_confidence_votes(self):
        failed = ClaudeResult(ok=False, error='scripted failure', raw={})
        runner = PersonaRunner(reviewer=[failed, failed, {'findings': [finding(rule_id='none')]}])
        result = self.run_flow(runner)
        self.assertEqual(result.passes_completed, 11)
        self.assertEqual(result.completed_by_persona['correctness'], 2)
        self.assertEqual(runner.counts['reviewer'], 13)
        self.assertEqual(agreement(result.master.items['F-001'], result.completed_by_persona, self.cfg)['correctness']['completed'], 2)

    def test_cancellation_stops_all_calls_and_cleans_checkout(self):
        runner = PersonaRunner(reviewer=[KeyboardInterrupt()])
        result = self.run_flow(runner)
        self.assertEqual(result.stop_reason, 'cancelled')
        self.assertGreaterEqual(len(runner.calls), 1)
        self.assertLessEqual(len(runner.calls), 4)
        self.assertFalse(runner.calls[0]['cwd'].exists())
        self.assertTrue(result.report_path.exists())

    def test_interrupted_consolidation_rolls_back_only_the_incomplete_slot(self):
        from unittest.mock import patch
        from code_reviewer.flows.agent_personas import flow
        original_dump = flow.dump_json
        for stage in ('mapping', 'completion_event'):
            with self.subTest(stage=stage):
                runner = self.conflict_runner(self.annotation())

                def dump(path, payload):
                    if stage == 'mapping' and path.parent.name == 'pass-02' and path.name == 'mapping.json':
                        raise KeyboardInterrupt
                    return original_dump(path, payload)

                def emit(event):
                    if stage == 'completion_event' and event.type == 'persona_pass_finished' and event.data['persona'] == 'code-quality':
                        raise KeyboardInterrupt

                with patch.object(flow, 'dump_json', side_effect=dump):
                    result = Engine(self.cfg, runner, emit=emit).review(self.ctx)
                self.assertEqual(result.stop_reason, 'cancelled')
                self.assertEqual(result.passes_completed, 1)
                self.assertEqual(len(result.master.items), 1)
                self.assertEqual(result.master.conflicts, {})
                self.assertEqual(result.master.items['F-001']['sightings'], {'correctness': [1]})
                self.assertEqual(result.passes[1].status, 'failed')
                self.assertFalse(result.passes[1].ok)
                saved = json.loads((result.run_dir / 'master.json').read_text())
                self.assertEqual(saved['findings'][0]['seen_in_passes'], [1])

    def test_scheduled_pass_table_is_one_markdown_block_even_with_multiline_errors(self):
        from code_reviewer.flows.agent_personas.report import render_report
        result = self.run_flow(PersonaRunner())
        result.passes[0].error = 'example | error\nsecond line'
        block = render_report(result).split('## Scheduled passes\n\n')[1].split('\n\n')[0]
        rows = block.splitlines()
        self.assertEqual(len(rows), 14)  # header, separator and all twelve slots
        self.assertTrue(all(row.startswith('|') and row.endswith('|') for row in rows))
        self.assertIn('example / error second line', rows[2])

    def test_terminal_failure_does_not_start_more_reviewers(self):
        runner = PersonaRunner(reviewer=[ClaudeResult(ok=False, error='failure', raw={})])
        result = self.run_flow(runner)
        self.assertEqual(result.stop_reason, 'failures')
        self.assertEqual(runner.counts, {'reviewer': 16})
        self.assertEqual(result.passes_completed, 0)

    def test_dry_run_calls_no_model_or_version_and_saves_all_four_defaults(self):
        runner = PersonaRunner()
        runner.version = lambda: self.fail('dry run must not call version')
        self.cfg.persona_passes = 2
        dry = Engine(self.cfg, runner).dry_run(self.ctx)
        from code_reviewer.flows.agent_personas.report import render_dry_run
        self.assertIn('2 passes per persona (8 scheduled)', render_dry_run(dry))
        self.assertEqual(runner.calls, [])
        self.assertEqual(set(dry.assignments), set(PERSONAS))
        self.assertFalse((dry.run_dir / 'pass-01').exists())

    def test_legacy_disabled_ids_map_only_within_persona_flow(self):
        result = self.run_flow(PersonaRunner(), disabled_default_rules=['GEN-004'])
        self.assertNotIn('SAFE-001', [r.rule_id for r in result.criteria.rules])

    def test_prompt_limit_fails_visibly_without_sending_partial_master(self):
        runner = PersonaRunner()
        result = self.run_flow(runner, persona_prompt_max_chars=10)
        self.assertEqual(runner.calls, [])
        self.assertEqual(result.stop_reason, 'failures')
        self.assertIn('not sent or truncated', result.report_path.read_text())

    def conflict_runner(self, verifier):
        def comparison(kw):
            findings = block(kw['user_prompt'], 'new_findings')
            local = findings[0]['local_id']
            master = block(kw['user_prompt'], 'candidate_findings')
            second = len(master) == 1
            return {'decisions': [{'local_id': local, 'match': 'new' if second or not master else 'F-001',
                                  'same_as': None, 'categories': ['correctness']}],
                    'conflicts': [{'findings': ['F-001', local if second else 'F-002'], 'reason': 'Competing recommendations'}] if master else []}
        return PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]},
            {'findings': [finding(title='Opposing claim', rule_id='none')]}, {'findings': []}],
            comparator=[comparison], verifier=[verifier])

    def annotation(self, **changes):
        evidence = {'file': 'app.py', 'line': 2, 'quote': finding()['quote']}
        result = {'outcome': 'resolved', 'explanation': 'The visible behavior supports one claim and refutes the other.',
                  'claims': [{'finding_id': ident, 'status': status, 'explanation': 'Repository evidence.',
                              'categories': ['production-safety'], 'evidence': [dict(evidence)]}
                             for ident, status in [('F-001', 'supported'), ('F-002', 'refuted')]]}
        result.update(changes)
        return result

    def test_conflict_verification_annotates_without_hiding_or_changing_votes(self):
        runner = self.conflict_runner(self.annotation())
        result = self.run_flow(runner, model_verifier='sonnet')
        self.assertEqual(len(result.master.conflicts), 1)
        self.assertEqual(runner.counts['verifier'], 1)
        self.assertEqual(len(result.master.items), 2)
        self.assertEqual(result.master.items['F-002']['sightings'], {'code-quality': [2]})
        self.assertEqual(result.master.conflicts['C-001']['status'], 'annotated')
        text = result.report_path.read_text()
        self.assertIn('Opposing claim', text)
        self.assertIn('refuted', text)
        verifier = next(c for c in runner.calls if c['role'] == 'verifier')
        self.assertEqual(verifier['model'], 'sonnet')
        self.assertEqual(verifier['tools'], 'Read,Grep,Glob')
        self.assertEqual(verifier['cwd'], runner.calls[0]['cwd'])

    def test_ungrounded_verifier_replies_retry_and_stay_unresolved(self):
        annotation = self.annotation()
        annotation['claims'][0]['evidence'][0]['file'] = '../outside.txt'
        runner = self.conflict_runner(annotation)
        result = self.run_flow(runner)
        self.assertEqual(runner.counts['verifier'], 2)
        group = result.master.conflicts['C-001']
        self.assertIsNone(group['annotation'])
        self.assertEqual(group['status'], 'unresolved')
        self.assertEqual(len(result.master.scored()), 2)

    def test_conflicts_are_remapped_deduplicated_and_seen_beyond_candidate_windows(self):
        runner = self.conflict_runner(self.annotation())
        runner.scripts['reviewer'] = [{'findings': [finding(rule_id='none')]},
            {'findings': [finding(rule_id='none', title='Opposing claim')]}, {'findings': [finding(rule_id='none')]}, {'findings': []}]
        result = self.run_flow(runner)
        self.assertEqual(len(result.master.conflicts), 1)
        self.assertEqual(runner.counts['verifier'], 1)
        group = result.master.conflicts['C-001']
        self.assertEqual(group['findings'], ['F-001', 'F-002'])
        self.assertEqual(group['original_references'], ['F-001', 'p2-f1'])
        calls = [c for c in runner.calls if c['role'] == 'comparator']
        self.assertEqual(len(block(calls[2]['user_prompt'], 'candidate_findings')), 2)

    def test_cancellation_before_verifier_leaves_pending_group_visible(self):
        runner = self.conflict_runner(self.annotation())
        original = runner.run
        def run(**kw):
            if 'investigate one flagged group' in kw['system_prompt']:
                raise KeyboardInterrupt
            return original(**kw)
        runner.run = run
        result = self.run_flow(runner)
        self.assertEqual(result.stop_reason, 'cancelled')
        self.assertEqual(result.master.conflicts['C-001']['status'], 'pending')
        self.assertIn('cancelled', result.master.conflicts['C-001']['error'])

    def test_bad_comparator_references_and_collapsed_conflicts_are_rejected(self):
        findings = [dict(finding(), local_id='p1-f1', verdict='confirmed'), dict(finding(), local_id='p1-f2', verdict='confirmed')]
        payload = {'decisions': [{'local_id': f['local_id'], 'match': 'new', 'same_as': None, 'categories': ['correctness']} for f in findings],
                   'conflicts': [{'findings': ['p1-f1', 'p1-f2'], 'reason': 'Opposing claims'}]}
        self.assertEqual(validate_comparison(payload, findings, MasterList()), [])
        payload['decisions'][1]['same_as'] = 'p1-f1'
        self.assertTrue(validate_comparison(payload, findings, MasterList()))
        payload['decisions'][1]['same_as'] = None
        payload['conflicts'][0]['findings'] = ['p1-f1', 'bogus']
        self.assertTrue(validate_comparison(payload, findings, MasterList()))

    def test_one_successful_completion_remains_unrated_under_filters(self):
        result = self.run_flow(PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]}], cost=0.01), run_budget_tokens=75)
        self.assertEqual(result.passes_completed, 1)
        self.assertIn('correctness: 1/1 (Unrated)', render_summary(result, ['High'], []))

    def test_budget_exhaustion_after_reviews_leaves_conflict_pending(self):
        runner = self.conflict_runner(self.annotation())
        runner.cost = 0.01
        from unittest.mock import patch
        from code_reviewer.flows.agent_personas.budget import Budget
        budget = Budget(self.cfg)
        allowance = budget.reserve('verifier', .4)
        budget.settle('verifier', allowance, ClaudeResult(ok=True, cost_usd=.4))
        with patch('code_reviewer.flows.agent_personas.flow.Budget', return_value=budget):
            result = self.run_flow(runner)
        self.assertEqual(result.passes_completed, 12)
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertNotIn('verifier', runner.counts)
        self.assertEqual(result.master.conflicts['C-001']['status'], 'pending')
        self.assertTrue(all(c['budget_usd'] >= 0.01 for c in runner.calls))

    def test_invalid_comparison_retries_without_committing_sightings(self):
        runner = PersonaRunner(reviewer=[{'findings': [finding()]}], comparator=[{'decisions': [], 'conflicts': []}])
        result = self.run_flow(runner)
        self.assertEqual(result.master.items, {})
        self.assertEqual(result.passes_completed, 0)
        self.assertEqual(runner.counts['comparator'], 16)
        self.assertTrue(result.diagnostics)

    def test_comparator_compares_conflicts_within_the_first_batch(self):
        def compare(kw):
            incoming = block(kw['user_prompt'], 'new_findings')
            self.assertEqual(block(kw['user_prompt'], 'candidate_findings'), [])
            return {'decisions': [{'local_id': f['local_id'], 'match': 'new', 'same_as': None, 'categories': ['correctness']} for f in incoming],
                    'conflicts': [{'findings': [f['local_id'] for f in incoming], 'reason': 'Opposing claims in one batch'}]}
        runner = PersonaRunner(reviewer=[{'findings': [finding(rule_id='none'), finding(rule_id='none', title='Opposing claim')]}, {'findings': []}],
                               comparator=[compare], verifier=[self.annotation()])
        result = self.run_flow(runner)
        self.assertEqual(result.master.conflicts['C-001']['findings'], ['F-001', 'F-002'])
        self.assertEqual(result.master.items['F-002']['sightings'], {'correctness': [1]})

    def test_cli_and_repl_present_persona_results_and_support_manual_actions(self):
        import contextlib
        import io
        from unittest.mock import patch
        from code_reviewer import cli
        from code_reviewer.post import format_comment
        from code_reviewer.repl import Session
        output = io.StringIO()
        runner = PersonaRunner()
        with patch.object(cli, 'load_config', return_value=self.cfg), patch('code_reviewer.backends.factory.create_runner', return_value=runner), \
                patch.object(Engine, 'prepare', return_value=self.ctx), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['review', self.ctx.label, '--mode', 'agent-personas']), 0)
        self.assertIn('agent-personas: 12/12 completed', output.getvalue())
        lines = []
        runner = PersonaRunner(reviewer=[{'findings': [finding(rule_id='none')]}])
        session = Session(self.cfg, runner, local_path=self.tmp, out=lines.append)
        with patch.object(Engine, 'prepare', return_value=self.ctx), contextlib.redirect_stdout(io.StringIO()):
            session.dispatch('review ' + self.ctx.label + ' --mode agent-personas')
        session.dispatch('show F-001')
        self.assertIn('correctness: 3/3', '\n'.join(lines))
        self.assertIn('SQL built by concatenation', format_comment(session.result, ['High'], []))
        session.dispatch('mark F-001 real')
        session.dispatch('drop F-001')
        self.assertNotIn('SQL built by concatenation', format_comment(session.result, ['High'], session.labels['dropped']))
        self.assertEqual(json.loads((session.result.run_dir / 'labels.json').read_text())['marks']['F-001'], 'real')

    def test_persona_pass_override_and_all_role_models_override_together(self):
        from argparse import Namespace
        from code_reviewer.review_options import apply_run_options
        args = Namespace(mode='agent-personas', passes=1, model='sonnet', no_self_check=False)
        cfg = apply_run_options(self.cfg, args)
        self.assertEqual(cfg.persona_passes, 1)
        self.assertEqual((cfg.min_passes, cfg.max_passes), (3, 6))
        self.assertEqual(self.cfg.persona_passes, 3)
        self.assertEqual((cfg.model_reviewer, cfg.model_comparator, cfg.model_resolver, cfg.model_verifier), ('sonnet',) * 4)
        self.assertEqual(self.cfg.model_verifier, 'haiku')

    def test_criteria_budget_failure_falls_back_without_stopping_other_pools(self):
        result = self.run_flow(PersonaRunner(cost=0.01), criteria_mode='merge', run_budget_usd=0.015)
        self.assertEqual(result.stop_reason, 'incomplete')
        self.assertEqual(result.passes_completed, 1)
        self.assertTrue(result.assignments)
        self.assertIn('criteria budget exhausted', ' '.join(result.warnings))

    def test_token_budget_stops_before_comparison_and_no_path_is_posted(self):
        runner = PersonaRunner(reviewer=[{'findings': [finding()]}])
        result = self.run_flow(runner, run_budget_tokens=1)
        self.assertEqual(runner.counts, {'reviewer': 1})
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertNotIn(str(result.run_dir), render_summary(result, ['High'], []))

    def test_self_rejected_claims_cannot_create_conflict_groups(self):
        findings = [dict(finding(), local_id='p1-f1', verdict='confirmed'), dict(finding(), local_id='p1-f2', verdict='rejected')]
        payload = {'decisions': [{'local_id': f['local_id'], 'match': 'new', 'same_as': None, 'categories': ['correctness']} for f in findings],
                   'conflicts': [{'findings': ['p1-f1', 'p1-f2'], 'reason': 'Opposing claims'}]}
        self.assertTrue(validate_comparison(payload, findings, MasterList()))

    def test_repeated_routing_ids_fall_back_even_if_one_assignment_is_invalid(self):
        from code_reviewer.flows.agent_personas.criteria import routes, validate_routes
        payload = {'assignments': [
            {'rule_id': 'P:a', 'personas': ['bogus'], 'reason': 'Invalid'},
            {'rule_id': 'P:a', 'personas': ['correctness'], 'reason': 'Duplicate'},
        ]}
        self.assertEqual(routes(payload, ['P:a']), {})
        self.assertTrue(validate_routes(payload, ['P:a']))

    def test_malformed_model_file_paths_fail_grounding_without_crashing_the_run(self):
        runner = PersonaRunner(reviewer=[{'findings': [finding(file='bad\x00path', rule_id='none')]}])
        result = self.run_flow(runner)
        self.assertEqual(result.passes_completed, 12)
        self.assertEqual(sum(s.ungrounded for s in result.passes), 12)
        self.assertEqual(result.master.items, {})
        annotation = self.annotation()
        annotation['claims'][0]['evidence'][0]['file'] = 'bad\x00path'
        self.assertTrue(validate_annotation(annotation, {'findings': ['F-001', 'F-002']}, FileCache(self.ctx.git_dir)))

    def test_single_agent_replay_rejects_persona_runs_before_model_calls(self):
        import contextlib
        import io
        from unittest.mock import patch
        from code_reviewer import replay
        result = self.run_flow(PersonaRunner())
        error = io.StringIO()
        with patch('subprocess.Popen', side_effect=AssertionError('must not call models')), contextlib.redirect_stderr(error):
            self.assertEqual(replay.main([str(result.run_dir)]), 1)
        self.assertIn('replay is parked', error.getvalue())

    def test_verifier_gets_authoritative_project_rules_even_when_claims_cite_none(self):
        runner = self.conflict_runner(self.annotation())
        result = self.run_flow(runner, criteria_mode='merge')
        verifier = next(c for c in runner.calls if c['role'] == 'verifier')
        project = [r for r in result.criteria.rules if r.source == 'project']
        self.assertTrue(project)
        for rule in project:
            self.assertIn(rule.text, verifier['user_prompt'])
