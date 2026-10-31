"""Safe provider diagnostics and bounded request retries; no network, keys or sleeping."""
from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from code_reviewer.backends import http_errors, openai
from code_reviewer.config import Config
from code_reviewer.shared.llm import CallSpec, ModelCaller
from code_reviewer.shared.models import PassFailed
from code_reviewer.shared.stats import Stats
from .test_openai_backend import response, final


def details(status=429, code='rate_limit_exceeded', headers=None, type_=None):
    return dict(status=status, code=code, type=type_, headers=headers or {})


class HTTPErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.kwargs=dict(system_prompt='Synthetic rules',user_prompt='Synthetic input',schema={'type':'object'},
                         model='synthetic-model',tools='',cwd=self.root,budget_usd=1,timeout_s=30)

    def runner(self,transport):
        return openai.OpenAIRunner('synthetic-private-key',model='synthetic-model',
                                  prices=openai.TokenPrices(1,.5,2),transport=transport)

    def test_transport_exports_only_closed_codes_and_validated_headers(self):
        reply=Mock(status=429)
        reply.read.return_value=json.dumps({'error':{'code':'insufficient_quota','type':'insufficient_quota',
                                                      'message':'synthetic-private-key source text','param':'private'}}).encode()
        headers={'Retry-After':'12','x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'6m0s',
                 'x-ratelimit-limit-requests':'private-id','x-ratelimit-reset-requests':'12s\nprivate',
                 'Authorization':'synthetic-private-key'}
        reply.getheader.side_effect=lambda name:headers.get(name)
        connection=Mock()
        connection.getresponse.return_value=reply
        with patch.object(openai.http.client,'HTTPSConnection',return_value=connection):
            with self.assertRaises(openai.ProviderHTTPError) as caught:
                openai.request_response({},'synthetic-private-key',5)
        error=caught.exception
        self.assertIn('insufficient_quota',str(error))
        self.assertFalse(http_errors.temporary(error.details))
        self.assertEqual(error.details['headers'],{'retry_after_s':12.0,'x-ratelimit-remaining-tokens':'0',
                                                  'x-ratelimit-reset-tokens':'6m0s'})
        self.assertNotIn('private',json.dumps(error.details)+str(error))
        reply.read.assert_called_once_with(http_errors.MAX_ERROR_BYTES+1)
        connection.close.assert_called_once()

    def test_unknown_malformed_and_oversized_error_bodies_are_not_retained(self):
        for data in (b'not json synthetic-private-key',b'x'*(http_errors.MAX_ERROR_BYTES+1),
                     b'[]',b'{"error":{"code":["private"],"type":{}}}',
                     b'{"error":{"code":"private-secret","message":"private"}}'):
            reply=Mock(status=429)
            reply.read.return_value=data
            reply.getheader.return_value=None
            parsed=http_errors.diagnostics(reply)
            self.assertIsNone(parsed['code'])
            self.assertIsNone(parsed['type'])
            self.assertNotIn('private',json.dumps(parsed))

    def test_retry_after_parses_seconds_and_http_dates_and_rejects_invalid_values(self):
        self.assertEqual(http_errors.retry_after('1.250'),1.25)
        future=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=30),usegmt=True)
        self.assertTrue(28 <= http_errors.retry_after(future) <= 30)
        for value in ('-1','nan','inf','1e300','2\nsecret','x'*129,None):
            self.assertIsNone(http_errors.retry_after(value))

    def test_temporary_failure_retries_same_request_and_keeps_diagnostics_in_stats(self):
        transport=Mock(side_effect=[openai.ProviderHTTPError(details(headers={'retry_after_s':3})),
                                    response(final({'findings':[]}))])
        stats=Stats()
        caller=ModelCaller(self.runner(transport),Config(call_timeout_s=30,call_budget_usd=1),stats)
        spec=CallSpec('reviewer',1,'Synthetic rules','Synthetic input',{'type':'object'},'synthetic-model','',self.root)
        with patch.object(openai.time,'sleep') as sleep,patch.object(openai.random,'uniform',return_value=.1):
            result=caller.call(spec,lambda value:[],self.root/'raw.json')
        self.assertTrue(result.ok,result.error)
        sleep.assert_called_once_with(3.1)
        self.assertEqual(transport.call_count,2)
        self.assertEqual(transport.call_args_list[0].args[0],transport.call_args_list[1].args[0])
        self.assertEqual(stats.calls[0]['http_errors'][0]['code'],'rate_limit_exceeded')
        self.assertNotIn('synthetic-private-key',(self.root/'raw.json').read_text())

    def test_retry_cap_does_not_multiply_with_shared_call_retry(self):
        transport=Mock(side_effect=openai.ProviderHTTPError(details()))
        caller=ModelCaller(self.runner(transport),Config(call_timeout_s=30,call_budget_usd=1),Stats())
        spec=CallSpec('reviewer',1,'rules','input',{'type':'object'},'synthetic-model','',self.root)
        with patch.object(openai.time,'sleep') as sleep,patch.object(openai.random,'uniform',return_value=0):
            with self.assertRaisesRegex(PassFailed,'rate_limit_exceeded'):
                caller.call(spec,lambda value:[],self.root/'raw.json')
        self.assertEqual(transport.call_count,3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2])

    def test_quota_and_auth_failures_stop_without_sleep_or_outer_retry(self):
        for detail in (details(code='insufficient_quota'), details(code='credit_balance_exhausted'),
                       details(code='project_spend_limit_exceeded'),details(code=None,type_='insufficient_quota'),
                       details(status=401,code='invalid_api_key')):
            transport=Mock(side_effect=openai.ProviderHTTPError(detail))
            caller=ModelCaller(self.runner(transport),Config(call_timeout_s=30,call_budget_usd=1),Stats())
            spec=CallSpec('reviewer',1,'rules','input',{'type':'object'},'synthetic-model','',self.root)
            with patch.object(openai.time,'sleep') as sleep:
                with self.assertRaises(PassFailed):
                    caller.call(spec,lambda value:[],self.root/'raw.json')
            transport.assert_called_once()
            sleep.assert_not_called()

    def test_long_server_delay_or_insufficient_deadline_defers_instead_of_retrying_early(self):
        for delay in (31,120):
            transport=Mock(side_effect=openai.ProviderHTTPError(details(headers={'retry_after_s':delay})))
            with patch.object(openai.time,'sleep') as sleep:
                result=self.runner(transport).run(**self.kwargs)
            self.assertFalse(result.retryable)
            self.assertIn('retry later',result.error)
            self.assertIn('deferred',result.http_errors[-1]['retry'])
            transport.assert_called_once()
            sleep.assert_not_called()

    def test_wait_is_cancellable_and_overload_uses_same_bounded_retry(self):
        stop=threading.Event()
        transport=Mock(side_effect=openai.ProviderHTTPError(details(status=503,code='server_is_overloaded')))
        def cancel(delay):
            stop.set()
            return True
        with patch.object(stop,'wait',side_effect=cancel):
            with self.assertRaises(KeyboardInterrupt):
                self.runner(transport).run_cancellable(stop,**self.kwargs)
        transport.assert_called_once()

    def test_recovered_http_failure_does_not_disable_output_validation_retry(self):
        invalid = response([{'type': 'message', 'content': [{'type': 'output_text', 'text': 'not JSON'}]}])
        transport = Mock(side_effect=[openai.ProviderHTTPError(details()), invalid, response(final({'findings': []}))])
        stats = Stats()
        caller = ModelCaller(self.runner(transport), Config(call_timeout_s=30, call_budget_usd=1), stats)
        spec = CallSpec('reviewer', 1, 'rules', 'input', {'type': 'object'}, 'synthetic-model', '', self.root)
        with patch.object(openai.time, 'sleep'):
            result = caller.call(spec, lambda value: [], self.root / 'raw.json')
        self.assertTrue(result.ok, result.error)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(len(stats.calls), 2)

    def test_both_flows_recover_with_http_retries_between_tool_turns(self):
        from code_reviewer.sandbox import openai_smoke
        original = openai_smoke.offline_response
        count = [0]
        def transport(*args):
            count[0] += 1
            if count[0] % 2:
                raise openai.ProviderHTTPError(details(headers={'retry_after_s': 0}))
            return original(*args)
        with patch.object(openai_smoke, 'offline_response', side_effect=transport), \
             patch.object(openai.time, 'sleep'), patch.object(openai.random, 'uniform', return_value=0):
            result = openai_smoke.run_offline()
        self.assertEqual(result['flows']['single-agent']['completed'], 2)
        self.assertEqual(result['flows']['agent-personas']['completed'], 4)

    def test_expired_deadline_after_wait_does_not_restart_outer_call(self):
        transport=Mock(side_effect=openai.ProviderHTTPError(details()))
        # clock changes only once the retry wait finishes
        clock=[0.0]
        def sleep(delay):
            clock[0]=31.0
        with patch.object(openai.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(openai.time,'sleep',side_effect=sleep):
            result=self.runner(transport).run(**self.kwargs)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.retryable)
        transport.assert_called_once()
