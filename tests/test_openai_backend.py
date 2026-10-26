"""Synthetic Responses protocol, local read boundaries and both review flows; no API calls."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from code_reviewer.backends import openai
from code_reviewer.backends.read_tools import ReadOnlyTools
from code_reviewer.sandbox import openai_smoke


def response(output, **changes):
    reply = {'status': 'completed', 'output': output,
             'usage': {'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 20}, 'output_tokens': 10}}
    reply.update(changes)
    return reply


def final(value):
    return [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]


class OpenAIBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'app.py').write_text('first\nsecond\n')
        self.prices = openai.TokenPrices(1, .5, 2)
        self.kwargs = dict(system_prompt='Synthetic rules', user_prompt='Synthetic diff',
                           schema={'type': 'object', 'properties': {'findings': {'type': 'array', 'items': {'type': 'string'}}}},
                           model='synthetic-model', tools='', cwd=self.root, budget_usd=1, timeout_s=5)

    def runner(self, transport):
        return openai.OpenAIRunner('synthetic-secret', model='synthetic-model', prices=self.prices, transport=transport)

    def test_strict_schema_is_independent_and_nested_objects_are_closed(self):
        schema = {'type': 'object', 'properties': {'nested': {'type': 'object', 'properties': {'x': {'type': 'string'}}}}}
        before = copy.deepcopy(schema)
        converted = openai.strict_schema(schema)
        self.assertEqual(schema, before)
        self.assertFalse(converted['properties']['nested']['additionalProperties'])
        self.assertEqual(converted['properties']['nested']['required'], ['x'])

    def test_response_preserves_reasoning_and_executes_only_allowed_read(self):
        calls = []
        def transport(payload, key, timeout):
            calls.append(copy.deepcopy(payload))
            self.assertFalse(payload['store'])
            if len(calls) == 1:
                return response([{'type': 'reasoning', 'encrypted_content': 'synthetic'},
                                 {'type': 'function_call', 'name': 'Read', 'call_id': 'one',
                                  'arguments': '{"path":"app.py","offset":2,"limit":1}'}])
            self.assertIn('2: second', payload['input'][-1]['output'])
            self.assertEqual(payload['input'][1]['type'], 'reasoning')
            return response(final({'findings': []}))
        result = self.runner(transport).run(**dict(self.kwargs, tools='Read,Grep,Glob'))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.tokens, {'input': 160, 'output': 20, 'cache_read': 40, 'cache_creation': 0})
        self.assertAlmostEqual(result.cost_usd, .00022)
        self.assertNotIn('synthetic-secret', json.dumps(result.raw))
        self.assertEqual(result.num_turns, 2)

    def test_unavailable_tool_is_refused_even_if_model_requests_it(self):
        for name in ('Bash', 'Read'):
            result = self.runner(lambda *a: response([{'type': 'function_call', 'name': name,
                                                      'call_id': 'one', 'arguments': '{}'}])).run(**self.kwargs)
            self.assertFalse(result.ok)
            self.assertIn('unavailable tool', result.error)
        result = self.runner(lambda *a: self.fail('must not request')).run(**dict(self.kwargs, tools='Bash'))
        self.assertFalse(result.ok)

    def test_missing_usage_and_transport_failure_keep_cost_unknown(self):
        def failed(*args):
            raise OSError('synthetic-secret')
        for transport in (failed, lambda *a: response(final({}), usage=None)):
            result = self.runner(transport).run(**self.kwargs)
            self.assertFalse(result.ok)
            self.assertIsNone(result.cost_usd)
            self.assertNotIn('synthetic-secret', result.error)
            self.assertNotIn('synthetic-secret', json.dumps(result.raw))

    def test_refusals_and_incomplete_or_non_object_output_are_failures(self):
        replies = [response([{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}]),
                   response(final({}), status='incomplete'), response(final([])),
                   response(final({}), usage={'input_tokens': True, 'output_tokens': 2})]
        for reply in replies:
            self.assertFalse(self.runner(lambda *a: reply).run(**self.kwargs).ok)

    def test_budget_preflight_sends_no_request_and_has_zero_known_cost(self):
        result = self.runner(lambda *a: self.fail('must not request')).run(**dict(self.kwargs, budget_usd=.000001))
        self.assertFalse(result.ok)
        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.cost_usd, 0)

    def test_turn_limit_is_bounded(self):
        def transport(*a):
            return response([{'type': 'function_call', 'name': 'Glob', 'call_id': 'synthetic', 'arguments': '{"pattern":"*"}'}])
        with patch.object(openai, 'MAX_TURNS', 2):
            result = self.runner(transport).run(**dict(self.kwargs, tools='Glob'))
        self.assertFalse(result.ok)
        self.assertEqual(result.num_turns, 2)
        self.assertIn('turn limit', result.error)

    def test_cancel_before_dispatch_and_after_response(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(KeyboardInterrupt):
            self.runner(lambda *a: self.fail('must not request')).run_cancellable(stop, **self.kwargs)
        stop.clear()
        def transport(*a):
            stop.set()
            return response(final({}))
        with self.assertRaises(KeyboardInterrupt):
            self.runner(transport).run_cancellable(stop, **self.kwargs)

    def test_invalid_model_and_prices_cannot_silently_select_claude(self):
        with self.assertRaises(ValueError):
            openai.OpenAIRunner('synthetic', model='haiku', prices=self.prices)
        for rate in (0, -1, True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                openai.TokenPrices(rate, 1, 1)
        result = self.runner(lambda *a: self.fail('must not request')).run(**dict(self.kwargs, model='other-model'))
        self.assertFalse(result.ok)

    def test_https_transport_has_fixed_endpoint_and_no_redirect_or_error_body_logging(self):
        class Reply:
            status = 302
            def read1(self, *a):
                raise AssertionError('must not read error body')
        class Connection:
            def __init__(self, host, timeout):
                self.host = host
            def request(self, method, path, body, headers):
                self.path = path
            def getresponse(self):
                return Reply()
            def close(self):
                pass
        with patch.object(openai.http.client, 'HTTPSConnection', Connection):
            with self.assertRaisesRegex(openai.ProviderError, 'HTTP status 302'):
                openai.request_response({}, 'synthetic', 5)
        with patch.object(openai, 'MAX_REQUEST', 1):
            with self.assertRaises(openai.ProviderError):
                openai.request_response({'synthetic': 'payload'}, 'synthetic', 5)

    def test_both_flows_complete_via_offline_responses_and_real_read_dispatch(self):
        result = openai_smoke.run_offline()
        self.assertEqual(result['flows']['single-agent']['completed'], 2)
        self.assertEqual(result['flows']['agent-personas']['completed'], 4)
        framed = openai_smoke.RESULT_PREFIX + json.dumps(result)
        self.assertEqual(openai_smoke.parse_result(framed), result)
        with self.assertRaises(ValueError):
            openai_smoke.parse_result(framed + '\n' + framed)


@unittest.skipUnless(os.name == 'posix', 'descriptor-relative POSIX file tools')
class ReadToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        (self.checkout / 'app.py').write_text('alpha\nbeta\n')
        self.tools = ReadOnlyTools(self.checkout)

    def read(self, path):
        return json.loads(self.tools.execute('Read', {'path': path, 'offset': 1, 'limit': 10}))

    def test_read_literal_search_and_path_glob(self):
        self.assertIn('2: beta', self.read('app.py')['text'])
        partial = json.loads(self.tools.execute('Read', {'path': 'app.py', 'offset': 1, 'limit': 1}))
        self.assertTrue(partial['truncated'])
        self.assertIn('app.py:1: alpha', json.loads(self.tools.execute('Grep', {'pattern': 'alpha'}))['text'])
        self.assertEqual(json.loads(self.tools.execute('Glob', {'pattern': '*.py'}))['text'], 'app.py')
        self.assertEqual(json.loads(self.tools.execute('Grep', {'pattern': '.*'}))['text'], '')

    def test_traversal_absolute_symlink_hardlink_special_and_git_files_are_denied(self):
        outside = self.root / 'outside'
        outside.write_text('synthetic private canary')
        (self.checkout / 'link').symlink_to(outside)
        (self.checkout / 'linked-dir').symlink_to(self.root)
        os.link(outside, self.checkout / 'hardlink')
        os.mkfifo(self.checkout / 'fifo')
        (self.checkout / '.git').mkdir()
        (self.checkout / '.git/config').write_text('synthetic')
        for path in ('../outside', str(outside), 'link', 'linked-dir/outside', 'hardlink', 'fifo', '.git/config'):
            self.assertIn('error', self.read(path), path)
        text = self.tools.execute('Grep', {'pattern': 'canary'})
        self.assertNotIn('synthetic private canary', text)

    def test_input_file_and_enumeration_limits(self):
        for args in ({'path': 'app.py', 'offset': True, 'limit': 1},
                     {'path': 'app.py', 'offset': 1, 'limit': 10000},
                     {'path': 'app.py', 'offset': 1, 'limit': 1, 'command': 'synthetic'}):
            self.assertIn('error', json.loads(self.tools.execute('Read', args)))
        with patch('code_reviewer.backends.read_tools.MAX_FILE', 2):
            self.assertIn('error', self.read('app.py'))
        with patch('code_reviewer.backends.read_tools.MAX_ENTRIES', 0):
            self.assertIn('error', json.loads(self.tools.execute('Glob', {'pattern': '*'})))
        self.assertIn('error', json.loads(self.tools.execute('Bash', {'command': 'synthetic'})))
