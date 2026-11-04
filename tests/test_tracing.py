"""Synthetic trace boundaries, VM transfer and optional SDK mapping; no external calls."""
from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_reviewer.tracing import recorder as tracing
from code_reviewer.config import Config
from code_reviewer.engine import Engine
from code_reviewer.review_flow import ReviewOutcome, run_review
from code_reviewer.tracing.review import finish, review_trace
from code_reviewer.sandbox.settings import job_settings, config_from_job
from code_reviewer.backends.openai import OpenAIRunner, TokenPrices
from .helpers import FakeRunner, make_repo
from .test_openai_backend import response, final


class TracingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def record(self):
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('review.run'):
            with tracing.observation('model.turn', 'generation', model='synthetic', input=20, output=3, cache_read=10):
                pass
        return recorder.snapshot()

    def test_closed_metadata_never_serializes_source_exceptions_or_arguments(self):
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('review.run', source='SOURCE_CANARY', key='SECRET_CANARY'):
            try:
                with tracing.observation('model.turn', 'generation', error_code='UNTRUSTED_CANARY'):
                    raise ValueError('EXCEPTION_CANARY')
            except ValueError:
                pass
        encoded = json.dumps(tracing.validate(recorder.snapshot()))
        self.assertNotIn('CANARY', encoded)
        self.assertIn('error', encoded)

    def test_parent_context_is_explicitly_copied_into_threads(self):
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('review.run'):
            with ThreadPoolExecutor(2) as executor:
                futures = [executor.submit(copy_context().run, self.child) for _ in range(2)]
                for future in futures:
                    future.result()
        value = tracing.validate(recorder.snapshot())
        self.assertEqual(len(value['records']), 3)
        self.assertTrue(all(row['parent'] == value['records'][0]['id'] for row in value['records'][1:]))
        self.assertIsNone(tracing.current())

    def child(self):
        with tracing.observation('model.call', 'agent'):
            pass

    def test_malformed_guest_records_fail_validation(self):
        value = self.record()
        mutations = [lambda v: v['records'][1].update(parent=v['records'][1]['id']),
                     lambda v: v['records'][1].update(id=v['records'][0]['id']),
                     lambda v: v['records'][1]['attributes'].update(source='CANARY'),
                     lambda v: v['records'][1]['attributes'].update(input=10**400),
                     lambda v: v['records'][1].update(end_ns=0),
                     lambda v: v['records'][1].update(name='CANARY'),
                     lambda v: v.update(dropped=-1)]
        for mutation in mutations:
            bad = copy.deepcopy(value)
            mutation(bad)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                tracing.validate(bad)
        with self.assertRaises(ValueError):
            tracing.validate(value, trace_id='0' * 32)
        with self.assertRaises(ValueError):
            tracing.validate(value, models={'other-model'})

    def test_saturation_drops_telemetry_and_preserves_valid_parent_tree(self):
        recorder = tracing.Recorder()
        with patch.object(tracing, 'MAX_RECORDS', 3), tracing.activate(recorder), tracing.observation('review.run'):
            for _ in range(10):
                self.child()
        value = tracing.validate(recorder.snapshot())
        self.assertEqual(value['dropped'], 8)
        stats = tracing.attach({'important': 'report accounting'}, value, max_bytes=130)
        self.assertEqual(stats['important'], 'report accounting')
        self.assertLessEqual(len(json.dumps(stats, indent=2).encode()) + 1, 130)

    def test_review_pipeline_preserve_results_and_persona_parentage(self):
        root = self.root / 'agent-personas'
        root.mkdir()
        ctx = make_repo(root)
        cfg = Config(persona_passes=1,
                     criteria_mode='defaults_only', runs_dir=str(root / 'runs'))
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('review.run'):
            result = Engine(cfg, FakeRunner([{'findings': []}])).review(ctx)
        value = tracing.validate(recorder.snapshot())
        generations = [r for r in value['records'] if r['kind'] == 'generation']
        self.assertEqual(len(generations), 4)
        self.assertTrue(all(r['parent'] is not None for r in generations))
        personas = {r['attributes'].get('persona') for r in value['records'] if r['kind'] == 'agent'}
        self.assertEqual(personas, set(tracing.ENUMS['persona']))
        self.assertTrue(result.report_path.is_file())

    def test_validation_retry_is_visible_without_changing_accounting(self):
        ctx = make_repo(self.root)
        cfg = Config(persona_passes=1, persona_parallel=1, criteria_mode='defaults_only', runs_dir=str(self.root / 'runs'))
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('review.run'):
            result = Engine(cfg, FakeRunner([{}, {'findings': []}])).review(ctx)
        attempts = [r for r in recorder.snapshot()['records'] if r['kind'] == 'generation']
        self.assertEqual(len(attempts), 5)
        self.assertEqual(attempts[0]['attributes']['outcome'], 'error')
        self.assertEqual(attempts[0]['attributes']['rejected'], 1)
        self.assertEqual(attempts[1]['attributes']['outcome'], 'ok')
        self.assertEqual(len(result.stats.calls), 5)
        self.assertEqual(result.passes_completed, 4)

    def test_host_frontdoor_saves_trace_even_when_export_fails(self):
        ctx = make_repo(self.root)
        cfg = Config(sandbox=False, criteria_mode='defaults_only', runs_dir=str(self.root / 'runs'))
        self.assertTrue(cfg.tracing)  # An ordinary review records traces by default.
        options = SimpleNamespace(preflight=False, dry_run=False)
        with patch.object(Engine, 'prepare', return_value=ctx), patch('code_reviewer.tracing.langfuse.export', side_effect=RuntimeError('SECRET_CANARY')), redirect_stderr(io.StringIO()) as err:
            from code_reviewer.shared.git_ops import PRRef
            outcome = run_review(cfg, FakeRunner([{'findings': []}]), PRRef('github.com', 'acme', 'project', 7), options, self.root)
        stats = json.loads((outcome.result.run_dir / 'stats.json').read_text())
        self.assertFalse(stats['tracing']['exported'])
        tracing.validate(stats['trace'])
        self.assertNotIn('SECRET_CANARY', err.getvalue())

    def test_disabled_preflight_dry_run_never_finish_or_read_tracing_keys(self):
        @review_trace
        def call(cfg, runner, ref, options, path):
            return ReviewOutcome()
        with patch('code_reviewer.tracing.review.finish', side_effect=AssertionError('no export')):
            for enabled, preflight, dry_run in ((False, False, False), (True, True, False), (True, False, True)):
                call(Config(tracing=enabled), None, None, SimpleNamespace(preflight=preflight, dry_run=dry_run), self.root)
            from code_reviewer.shared.git_ops import PRRef
            result = run_review(Config(tracing=True), None, PRRef('github.com', 'acme', 'project', 7),
                                SimpleNamespace(preflight=True), self.root)
            self.assertIsNotNone(result.preflight)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_failure_records_survive_review_exception(self):
        @review_trace
        def call(cfg, runner, ref, options, path):
            raise RuntimeError('REVIEW_CANARY')
        with patch('code_reviewer.tracing.langfuse.export', side_effect=RuntimeError), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'REVIEW_CANARY'):
                call(Config(tracing=True, runs_dir=str(self.root)), None, None, SimpleNamespace(), self.root)
        stats = json.loads(next(self.root.glob('trace-*/stats.json')).read_text())
        self.assertEqual(stats['trace']['records'][0]['attributes']['outcome'], 'error')
        self.assertNotIn('REVIEW_CANARY', json.dumps(stats))

    def test_vm_settings_carry_identity_and_flag_but_no_exporter_configuration(self):
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('review.run'):
            value = job_settings(Config(tracing=True))
        cfg = config_from_job(value, self.root)
        self.assertTrue(cfg.tracing)
        self.assertEqual(value['trace_context']['trace_id'], recorder.trace_id)
        self.assertNotIn('KEY', json.dumps(value))
        bad = copy.deepcopy(value)
        bad['trace_context']['endpoint'] = 'http://untrusted.invalid'
        with self.assertRaises(ValueError):
            config_from_job(bad, self.root)

    def test_validated_guest_trace_merges_under_host_span(self):
        host = tracing.Recorder()
        with tracing.activate(host), tracing.observation('review.run'):
            context = tracing.context()
        host.guest_context = context
        guest = tracing.Recorder(context['trace_id'], context['parent_id'])
        with tracing.activate(guest), tracing.observation('review.guest'):
            self.child()
        directory = self.root / 'run'
        directory.mkdir()
        (directory / 'stats.json').write_text(json.dumps({'trace': guest.snapshot()}))
        with patch('code_reviewer.tracing.langfuse.export'), redirect_stderr(io.StringIO()):
            finish(host, Config(), SimpleNamespace(run_dir=directory))
        value = json.loads((directory / 'stats.json').read_text())['trace']
        self.assertEqual(len(tracing.validate(value)['records']), 3)

    def test_guest_roots_and_host_id_collisions_cannot_discard_host_trace(self):
        for attack in ('second-root', 'host-id-collision'):
            with self.subTest(attack=attack):
                host = tracing.Recorder()
                with tracing.activate(host), tracing.observation('review.run'):
                    context = tracing.context()
                    with tracing.observation('vm.execute'):
                        pass
                host.guest_context = context
                recorder = tracing.Recorder(context['trace_id'], context['parent_id'])
                with tracing.activate(recorder), tracing.observation('review.guest'):
                    pass
                guest = recorder.snapshot()
                if attack == 'second-root':
                    guest['records'][0]['parent'] = None
                else:
                    guest['records'][0]['id'] = host.records[1]['id']
                directory = self.root / attack
                directory.mkdir()
                (directory / 'stats.json').write_text(json.dumps({'trace': guest, 'important': 123}))
                with patch('code_reviewer.tracing.langfuse.export') as export, redirect_stderr(io.StringIO()):
                    finish(host, Config(), SimpleNamespace(run_dir=directory))
                saved = json.loads((directory / 'stats.json').read_text())
                self.assertEqual(saved['important'], 123)
                self.assertEqual(len(saved['trace']['records']), 2)
                self.assertEqual(saved['trace']['dropped'], 1)
                export.assert_called_once()

    def test_trace_is_persisted_before_export_starts(self):
        host = tracing.Recorder()
        with tracing.activate(host), tracing.observation('review.run'):
            pass
        def export(value):
            saved = json.loads((self.root / 'stats.json').read_text())
            self.assertEqual(saved['trace'], value)
            self.assertFalse(saved['tracing']['exported'])
            raise TimeoutError
        with patch('code_reviewer.tracing.langfuse.export', side_effect=export), redirect_stderr(io.StringIO()):
            finish(host, Config(), SimpleNamespace(run_dir=self.root))
        self.assertTrue((self.root / 'stats.json').exists())

    def test_actual_guest_trace_fits_fixed_artifacts_and_contains_no_credentials(self):
        from code_reviewer.sandbox import capsule, guest, connected
        ctx = make_repo(self.root)
        archive = self.root / 'input.tar'
        capsule.prepare(ctx.git_dir, ctx.base_sha, ctx.head_sha, archive)
        directory = self.root / 'agent-personas'
        directory.mkdir()
        cfg = Config(tracing=True, provider='openai', model_reviewer='synthetic',
                     model_comparator='synthetic', model_resolver='synthetic', model_verifier='synthetic',
                     openai_input_price=1, openai_cached_input_price=.5, openai_output_price=2,
                     criteria_mode='defaults_only', persona_passes=1)
        host = tracing.Recorder()
        with tracing.activate(host), tracing.observation('review.run'):
            settings = job_settings(cfg)
        with patch.object(guest, 'receive_json', return_value=settings), patch.object(guest, 'receive',
                side_effect=[archive.read_bytes(), b'CREDENTIAL_CANARY']), patch.object(guest, 'send_json'), \
                patch('code_reviewer.backends.openai.request_response', return_value=response(final({'findings': []}))):
            result = guest.run_job(None, directory, probe_network=lambda: {k: True for k in connected.NETWORK_CHECKS})
        self.assertEqual(set(result['files']), {'report.md', 'summary.md', 'stats.json', 'findings.json'})
        data = base64.b64decode(result['files']['stats.json'])
        self.assertNotIn(b'CREDENTIAL_CANARY', data)
        trace = json.loads(data)['trace']
        tracing.validate(trace, trace_id=host.trace_id, parent_id=host.guest_context['parent_id'], models={'synthetic'})
        self.assertTrue(any(r['kind'] == 'generation' for r in trace['records']))
        self.assertLessEqual(sum(len(base64.b64decode(v)) for v in result['files'].values()), guest.MAX_EXPORT)

    def test_openai_turns_tools_and_cache_usage_have_no_content_or_duplicate_generations(self):
        (self.root / 'app.py').write_text('SOURCE_CANARY\n')
        replies = [response([dict(type='function_call', name='Read', call_id='1', arguments='{"path":"app.py","offset":1,"limit":1}')]),
                   response(final({'findings': []}))]
        runner = OpenAIRunner('KEY_CANARY', model='synthetic', prices=TokenPrices(1, .5, 2), transport=lambda *a: replies.pop(0))
        recorder = tracing.Recorder()
        with tracing.activate(recorder), tracing.observation('model.call', 'agent'):
            result = runner.run(system_prompt='PROMPT_CANARY', user_prompt='DIFF_CANARY', schema={'type': 'object'}, model='synthetic',
                                tools='Read', cwd=self.root, budget_usd=1, timeout_s=5)
        value = tracing.validate(recorder.snapshot())
        generations = [r for r in value['records'] if r['kind'] == 'generation']
        tools = [r for r in value['records'] if r['kind'] == 'tool']
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(generations), 2)
        self.assertEqual(sum(r['attributes']['cache_read'] for r in generations), result.tokens['cache_read'])
        self.assertEqual(tools[0]['parent'], generations[0]['parent'])
        self.assertFalse(generations[0]['attributes']['cache_write_known'])
        self.assertNotIn('CANARY', json.dumps(value))

    def test_exporter_subprocess_receives_only_local_trace_and_selected_keys(self):
        from code_reviewer.tracing.langfuse import export
        with patch.dict(os.environ,
                {'LANGFUSE_PUBLIC_KEY': 'pk-synthetic', 'LANGFUSE_SECRET_KEY': 'sk-synthetic',
                 'OTEL_EXPORTER_OTLP_ENDPOINT': 'http://CANARY', 'OPENAI_API_KEY': 'SECRET_CANARY'}), \
             patch('code_reviewer.tracing.langfuse.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            export(self.record())
        args = run.call_args.kwargs
        self.assertNotIn('OTEL_EXPORTER_OTLP_ENDPOINT', args['env'])
        self.assertNotIn('OPENAI_API_KEY', args['env'])
        self.assertNotIn('SECRET_CANARY', args['input'])
        self.assertEqual(args['timeout'], 20)

    def test_project_keys_are_not_forwarded_to_native_model_process(self):
        from code_reviewer.backends.factory import create_runner
        runner = create_runner(Config(provider='claude'), selected_credential='synthetic-model-key',
                               env={'PATH': '/usr/bin:/bin', 'LANGFUSE_PUBLIC_KEY': 'pk-CANARY', 'LANGFUSE_SECRET_KEY': 'sk-CANARY'})
        self.assertNotIn('LANGFUSE_PUBLIC_KEY', runner.env)
        self.assertNotIn('LANGFUSE_SECRET_KEY', runner.env)

    def test_private_setup_preserves_keys_and_refuses_insecure_existing_file(self):
        spec = importlib.util.spec_from_file_location('langfuse_setup', Path(__file__).resolve().parents[1] / 'deploy/langfuse/setup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.Path, 'home', return_value=self.root), patch('builtins.print'):
            module.main()
            path = self.root / '.code-reviewer/langfuse/local.env'
            before = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            module.main()
            self.assertEqual(path.read_bytes(), before)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                module.main()

    def test_multibyte_stats_and_trace_saturation_keep_existing_byte_limits(self):
        stats = {'important': '\u20ac' * 100}
        value = self.record()
        output = tracing.attach(stats, value, max_bytes=450)
        self.assertEqual(output['important'], stats['important'])
        self.assertLessEqual(len(json.dumps(output, indent=2, ensure_ascii=False).encode()) + 1, 450)

    @unittest.skipUnless(importlib.util.find_spec('langfuse'), 'optional tracing SDK not installed')
    def test_sdk_public_api_preserves_original_timestamps_identity_and_usage(self):
        from code_reviewer.tracing.langfuse import send
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter = InMemorySpanExporter()
        value = self.record()
        send(value, {'LANGFUSE_PUBLIC_KEY': 'pk-synthetic', 'LANGFUSE_SECRET_KEY': 'sk-synthetic'}, exporter=exporter)
        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        by_id = {span.context.span_id: span for span in spans}
        for row in value['records']:
            span = by_id[int(row['id'], 16)]
            self.assertEqual((span.start_time, span.end_time), (row['start_ns'], row['end_ns']))
            self.assertEqual(span.context.span_id, int(row['id'], 16))
        parent = by_id[int(value['records'][0]['id'], 16)]
        child = by_id[int(value['records'][1]['id'], 16)]
        self.assertEqual(child.parent.span_id, parent.context.span_id)
        self.assertTrue(parent.attributes.get('langfuse.internal.is_app_root'))
        self.assertFalse(child.attributes.get('langfuse.internal.is_app_root', False))
        usage = json.loads(child.attributes['langfuse.observation.usage_details'])
        self.assertEqual(usage, dict(input=20, output=3, cache_read=10))
        self.assertEqual(json.loads(child.attributes['langfuse.observation.cost_details']), {'total': 0.0})

    @unittest.skipUnless(importlib.util.find_spec('langfuse'), 'optional tracing SDK not installed')
    def test_sdk_exports_explicit_api_cost_without_model_price_inference(self):
        from code_reviewer.tracing.langfuse import send
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter = InMemorySpanExporter()
        value = self.record()
        value['records'][1]['attributes']['cost_usd'] = 0.00125
        send(value, {'LANGFUSE_PUBLIC_KEY': 'pk-synthetic-cost', 'LANGFUSE_SECRET_KEY': 'sk-synthetic-cost'}, exporter=exporter)
        generation = next(span for span in exporter.get_finished_spans() if span.name == 'model.turn')
        self.assertEqual(json.loads(generation.attributes['langfuse.observation.cost_details']), {'total': 0.00125})
