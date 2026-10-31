"""Subscription protocol and private credentials; synthetic data, no live services."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from code_reviewer import cli
from code_reviewer.backends import factory
from code_reviewer.backends.chatgpt import oidc, login, models
from code_reviewer.backends.chatgpt.store import Store, token_record, runtime_key
from code_reviewer.backends.openai_stream import read_stream, StreamFailure
from code_reviewer.backends.contract import LLMResult
from code_reviewer.config import Config, ConfigError
from code_reviewer.engine import Engine
from code_reviewer.flows.agent_personas.budget import Budget, RunStopped
from code_reviewer.sandbox import settings
from tests.helpers import make_repo


def config(**kwargs):
    values = dict(provider='openai', auth_mode='subscription', sandbox=False,
                  model_reviewer='test-model', model_comparator='test-model',
                  model_resolver='test-model', model_verifier='test-model',
                  min_passes=1, max_passes=1, persona_passes=1, criteria_mode='defaults_only')
    values.update(kwargs)
    return Config(**values)


def reply(payload=None):
    return {'status': 'completed', 'usage': {'input_tokens': 10, 'output_tokens': 5,
            'input_tokens_details': {'cached_tokens': 3}}, 'output': [{'type': 'message',
            'content': [{'type': 'output_text', 'text': json.dumps(payload or {'findings': [], 'notes': ''})}]}]}


def record(host, **values):
    result = dict(client_id='oaiapp_test', host_id=host, issuer=oidc.ISSUER,
                  subject='synthetic-user', email='', nonce='nonce', access_token='access',
                  refresh_token='refresh', id_token='signed', scopes=sorted(oidc_scopes()), expires_at=time.time()+3600)
    result.update(values)
    return result


def oidc_scopes():
    return set(oidc.SCOPES.split())


def oauth_reply(**values):
    result = dict(token_type='Bearer', access_token='new-access', refresh_token='new-refresh',
                  expires_in=3600, scope=oidc.SCOPES)
    result.update(values)
    return result


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key = Path(cls.tmp.name)/'key.pem'
        subprocess.run(['/usr/bin/openssl', 'genrsa', '-out', str(cls.key), '2048'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(cls.key, 0o600)
        modulus = subprocess.check_output(['/usr/bin/openssl', 'rsa', '-in', str(cls.key), '-modulus', '-noout'], stderr=subprocess.DEVNULL)
        raw = bytes.fromhex(modulus.decode().strip().split('=')[1])
        cls.jwks = {'keys': [dict(kty='RSA', alg='RS256', use='sig', kid='test', n=oidc.b64(raw), e=oidc.b64(b'\x01\x00\x01'))]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def signed(self, **overrides):
        claims = dict(iss=oidc.ISSUER, aud='oaiapp_test', sub='synthetic-user',
                      exp=int(time.time())+120, iat=int(time.time()), nonce='nonce')
        claims.update(overrides)
        head = oidc.b64(json.dumps(dict(alg='RS256', kid='test')).encode())
        body = oidc.b64(json.dumps(claims).encode())
        message = (head+'.'+body).encode()
        sig = subprocess.check_output(['/usr/bin/openssl', 'dgst', '-sha256', '-sign', str(self.key)], input=message)
        return message.decode()+'.'+oidc.b64(sig)

    def test_signed_identity_and_tampering(self):
        token = self.signed()
        self.assertEqual(oidc.verify_id(token, 'oaiapp_test', nonce='nonce', jwks=self.jwks)['sub'], 'synthetic-user')
        parts = token.split('.')
        parts[1] = oidc.b64(json.dumps({'sub': 'forged'}).encode())
        for bad in ('.'.join(parts), token[:-10], 'not.a.jwt'):
            with self.assertRaises(ConfigError):
                oidc.verify_id(bad, 'oaiapp_test', jwks=self.jwks)

    def test_signed_wrong_claims_rejected(self):
        for claims in (dict(iss='https://invalid.example'), dict(aud='another-client'), dict(sub='other'),
                       dict(exp=int(time.time())-60), dict(iat=int(time.time())+60), dict(nonce='wrong'),
                       dict(exp=True), dict(at_hash='wrong')):
            with self.subTest(claims=claims), self.assertRaises(ConfigError):
                oidc.verify_id(self.signed(**claims), 'oaiapp_test', nonce='nonce',
                               subject='synthetic-user', access_token='access', jwks=self.jwks)
        with self.assertRaises(ConfigError):
            oidc.verify_id(self.signed(), 'oaiapp_test', jwks={'keys': []})

    def test_token_record_requires_permission_and_identity(self):
        value = oauth_reply(id_token=self.signed())
        with patch.object(oidc, 'request', return_value=self.jwks):
            result = token_record(value, 'oaiapp_test', 'host', nonce='nonce')
        self.assertEqual(result['subject'], 'synthetic-user')
        for replacement in ({'scope': 'openid'}, {'expires_in': True}, {'token_type': 'Basic'}, {'access_token': 'bad\n'}):
            with self.assertRaises(ConfigError):
                token_record(dict(value, **replacement), 'oaiapp_test', 'host', nonce='nonce')

    def test_reauthorization_accepts_new_nonce_but_refresh_cannot_change_it(self):
        previous = record('host')
        with patch.object(oidc, 'request', return_value=self.jwks):
            renewed = token_record(oauth_reply(id_token=self.signed(nonce='fresh-nonce')),
                                   'oaiapp_test', 'host', nonce='fresh-nonce', previous=previous)
            self.assertEqual(renewed['nonce'], 'fresh-nonce')
            with self.assertRaisesRegex(ConfigError, 'nonce mismatch'):
                token_record(oauth_reply(id_token=self.signed(nonce='wrong-refresh-nonce')),
                             'oaiapp_test', 'host', previous=previous)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'auth'
        self.store = Store(self.root)
        self.host, _ = self.store.prepare('local', 'default')
        self.store.install('local', 'default', record(self.host))

    def test_private_permissions_and_separate_account_runtime(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root/'chatgpt.json').stat().st_mode & 0o777, 0o600)
        other, _ = self.store.prepare('vm-test', 'default')
        self.assertNotEqual(other, self.host)
        with self.assertRaises(ConfigError):
            self.store.access('vm-test', 'default')
        self.assertNotEqual(runtime_key(config()), runtime_key(config(sandbox=True)))
        self.assertEqual(self.store.access('local', 'default'), 'access')
        with self.assertRaises(ConfigError):
            self.store.install('local', 'default', record(self.host, subject='another-user'))

    def expire(self):
        self.store.install('local', 'default', record(self.host, expires_at=0))

    def test_refresh_rotates_and_uses_issued_registration(self):
        self.expire()
        with patch.object(oidc, 'request', return_value=oauth_reply()) as call:
            self.assertEqual(self.store.access('local', 'default', 120), 'new-access')
            self.assertEqual(self.store.access('local', 'default'), 'new-access')
        call.assert_called_once()
        form = call.call_args.args[1]
        self.assertEqual(form['client_id'], 'oaiapp_test')
        self.assertEqual(form['refresh_token'], 'refresh')
        self.assertNotIn('scope', form)
        self.assertEqual(self.store.records('local')['default']['refresh_token'], 'new-refresh')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_terminal_refresh_clears_tokens_but_temporary_failure_preserves(self):
        self.expire()
        with patch.object(oidc, 'request', side_effect=oidc.AuthError(503, 'temporarily_unavailable')):
            with self.assertRaises(ConfigError):
                self.store.access('local', 'default')
        self.assertEqual(self.store.records('local')['default']['refresh_token'], 'refresh')
        with patch.object(oidc, 'request', side_effect=oidc.AuthError(400, 'invalid_grant')):
            with self.assertRaises(ConfigError):
                self.store.access('local', 'default')
        saved = self.store.records('local')['default']
        self.assertEqual(saved['client_id'], 'oaiapp_test')
        self.assertFalse(saved['refresh_token'])
        self.assertFalse(saved['access_token'])

    def test_store_refuses_symlinks_hardlinks_permissions_and_invalid_records(self):
        path = self.root/'chatgpt.json'
        original = path.read_bytes()
        path.chmod(0o644)
        with self.assertRaises(ConfigError):
            self.store.records('local')
        path.chmod(0o600)
        link = self.root/'copy'
        os.link(path, link)
        with self.assertRaises(ConfigError):
            self.store.records('local')
        link.unlink()
        path.unlink()
        link.write_bytes(original)
        path.symlink_to(link)
        with self.assertRaises(ConfigError):
            self.store.records('local')
        path.unlink()
        malformed = json.loads(original)
        malformed['accounts']['local'] = []
        path.write_text(json.dumps(malformed))
        path.chmod(0o600)
        with self.assertRaises(ConfigError):
            self.store.records('local')

    def test_logout_local_clear_even_when_remote_unavailable(self):
        with patch.object(oidc, 'discovery', side_effect=ConfigError('offline')):
            self.assertFalse(self.store.logout('local', 'default'))
        self.assertFalse(self.store.records('local')['default']['refresh_token'])


class LoginTests(unittest.TestCase):
    def test_callback_state_duplicates_and_issued_client(self):
        target = '/auth/callback?'+urlencode(dict(state='state', code='code', client_id='oaiapp_test'))
        self.assertEqual(login.callback(target, 'state'), ('code', 'oaiapp_test'))
        for bad in (target+'&state=state', target.replace('state=state', 'state=wrong'),
                    target.replace('state=state', 'state=%C3%A9'), target.replace('/auth/callback', '/other')):
            with self.assertRaises(ConfigError):
                login.callback(bad, 'state')
        with self.assertRaises(ConfigError):
            login.callback(target, 'state', 'different-client')
        with self.assertRaises(login.ConsentDeclined):
            login.callback('/auth/callback?state=state&error=access_denied', 'state')

    def test_login_validates_before_install_and_reuses_returning_client(self):
        with tempfile.TemporaryDirectory() as name:
            store = Store(Path(name)/'auth')
            pending = dict(code='code', client_id='oaiapp_test', nonce='nonce', code_verifier='verifier', redirect_uri='http://127.0.0.1:1234/auth/callback')
            def verified(value, client, host, **kwargs):
                return record(host)
            with patch.object(oidc, 'discovery'), patch.object(login, 'authorize', return_value=pending) as authorize, \
                 patch.object(oidc, 'request', return_value=oauth_reply()), patch.object(login, 'token_record', side_effect=verified):
                login.login(config(), 'default', store=store)
                login.login(config(), 'default', store=store)
            self.assertIsNone(authorize.call_args_list[0].args[1])
            self.assertEqual(authorize.call_args_list[1].args[1], 'oaiapp_test')
            with patch.object(oidc, 'discovery'), patch.object(login, 'authorize', side_effect=login.ConsentDeclined('declined')), \
                 patch.object(oidc, 'request') as exchange:
                with self.assertRaises(ConfigError):
                    login.login(config(), 'default', store=store)
            exchange.assert_not_called()

    def test_preflight_and_auth_status_do_not_refresh(self):
        out = io.StringIO()
        with patch.object(cli, 'load_config', return_value=config()), patch.object(Store, 'access', side_effect=AssertionError('no auth')), \
             contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['review', 'example/repo#1', '--preflight']), 0)
        self.assertTrue(json.loads(out.getvalue())['ready'])


class StreamTests(unittest.TestCase):
    def parse(self, chunks, limit=65536):
        parts = iter(chunks)
        return read_stream(lambda: next(parts, b''), limit)

    def test_chunk_boundaries_and_required_terminal_completion(self):
        value = b'event: response.completed\ndata: '+json.dumps(dict(type='response.completed', response=reply())).encode()+b'\n\n'
        self.assertEqual(self.parse([value[i:i+3] for i in range(0, len(value), 3)]), reply())
        for chunks in ([value[:-1]], [b'data: [DONE]\n\n'], [b'data: {}\n\n'], [b'bad\n']):
            with self.assertRaises(StreamFailure):
                self.parse(chunks)
        with self.assertRaises(StreamFailure):
            self.parse([value], limit=10)

    def test_failure_does_not_disclose_raw_message(self):
        raw = b'data: {"type":"error","error":{"code":"subscription_sharing_usage_limit_exceeded","message":"secret"}}\n\n'
        with self.assertRaises(StreamFailure) as caught:
            self.parse([raw])
        self.assertNotIn('secret', str(caught.exception))
        self.assertIn('subscription_sharing_usage_limit_exceeded', str(caught.exception))


class AdapterTests(unittest.TestCase):
    def test_factory_is_subscription_only_and_stream_payload_is_compatible(self):
        requests = []
        def transport(payload, token, timeout):
            requests.append(copy.deepcopy(payload))
            self.assertTrue(payload['stream'])
            self.assertFalse(payload['store'])
            self.assertNotIn('max_output_tokens', payload)
            self.assertEqual(token, 'selected-access')
            return reply()
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'unselected'}), patch.object(Store, 'access', return_value='selected-access') as access:
            runner = factory.create_runner(config(), transport=transport)
            result = runner.run(system_prompt='system', user_prompt='user', schema={'type':'object'},
                                model='test-model', tools='', cwd=None, budget_usd=.01, timeout_s=10)
        self.assertTrue(result.ok, result.error)
        self.assertIsNone(result.cost_usd)
        self.assertEqual(result.billing_mode, 'subscription')
        self.assertGreaterEqual(access.call_count, 2)
        self.assertEqual(len(requests), 1)

    def test_namespaced_read_tool_continuation(self):
        with tempfile.TemporaryDirectory() as name:
            Path(name, 'a.txt').write_text('hello')
            responses = [dict(reply(), output=[dict(type='function_call', namespace='reviewer', name='Read', call_id='call', arguments='{"path":"a.txt","offset":1,"limit":10}')]), reply()]
            seen = []
            def transport(payload, *_):
                seen.append(copy.deepcopy(payload))
                return responses.pop(0)
            runner = factory.create_runner(config(), selected_credential='access', transport=transport)
            result = runner.run(system_prompt='s', user_prompt='u', schema={'type':'object'}, model='test-model',
                                tools='Read', cwd=Path(name), budget_usd=1, timeout_s=10)
            self.assertTrue(result.ok, result.error)
            self.assertEqual(seen[0]['tools'][0]['type'], 'namespace')
            self.assertIn('hello', seen[1]['input'][-1]['output'])

    def test_partial_stream_is_not_retried(self):
        with patch.object(Store, 'access', return_value='access'):
            transport = unittest.mock.Mock(side_effect=StreamFailure('stream interrupted'))
            runner = factory.create_runner(config(), transport=transport)
            result = runner.run(system_prompt='s', user_prompt='u', schema={}, model='test-model', tools='', cwd=None, budget_usd=1, timeout_s=10)
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(transport.call_count, 1)

    def test_token_pools_preserve_ratios_and_no_cash(self):
        budget = Budget(config(run_budget_tokens=3000))
        pools = budget.snapshot()
        self.assertEqual(pools['criteria']['allocated_tokens'], 300)
        self.assertEqual(pools['comparator']['allocated_tokens'], 300)
        self.assertEqual(pools['verifier']['allocated_tokens'], 400)
        self.assertEqual(sum(p['allocated_tokens'] for p in pools.values()), 3000)
        allowance = budget.reserve('criteria', 300)
        budget.settle('criteria', allowance, LLMResult(ok=False, billing_mode='subscription', usage_known=False))
        with self.assertRaises(RunStopped):
            budget.reserve('criteria', 1)
        self.assertGreater(budget.reserve('comparator', 10), 0)
        self.assertNotIn('allocated_usd', pools['criteria'])

    def test_guest_factory_uses_only_handed_off_access_token(self):
        cfg = config(sandbox=True)
        raw = settings.job_settings(cfg)
        guest_cfg = settings.config_from_job(raw, Path('/work'))
        with patch.object(Store, 'access', side_effect=AssertionError('no guest store')):
            runner = factory.create_runner(guest_cfg, selected_credential='x'*8000, transport=lambda *_: reply())
            self.assertIsNone(runner.token_source)
        with self.assertRaises(ConfigError):
            factory.create_runner(config(auth_mode='api-key', openai_input_price=1, openai_cached_input_price=1, openai_output_price=1), selected_credential='x'*8000)

class WiringTests(unittest.TestCase):
    def test_both_one_shot_flows_use_subscription_factory_and_write_reports(self):
        for mode in ('single-agent', 'agent-personas'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                ctx = make_repo(root)
                cfg = config(review_mode=mode, runs_dir=str(root/'runs'))
                output = io.StringIO()
                with patch.object(cli, 'load_config', return_value=cfg), \
                     patch.object(Engine, 'prepare', return_value=ctx), \
                     patch.object(Store, 'access', return_value='synthetic-access') as access, \
                     patch('code_reviewer.backends.openai.request_response', return_value=reply()) as infer, \
                     contextlib.redirect_stdout(output):
                    code = cli.main(['review', 'example/project#1', '--account', 'selected'])
                self.assertEqual(code, 0, output.getvalue())
                self.assertEqual(infer.call_count, 4 if mode == 'agent-personas' else 1)
                self.assertEqual(access.call_args.args[1], 'selected')
                for call in infer.call_args_list:
                    self.assertTrue(call.args[0]['stream'])
                    self.assertNotIn('max_output_tokens', call.args[0])
                stats_file = next((root/'runs').rglob('stats.json'))
                stats = json.loads(stats_file.read_text())
                self.assertIsNone(stats['total_cost_usd_estimated'])
                self.assertGreater(stats['budget_tokens'], 0)
                report = stats_file.with_name('report.md').read_text()
                self.assertNotIn('$0.00', report)
                self.assertIn('subscription', output.getvalue())

    def test_browser_authorization_uses_loopback_pkce_nonce_and_no_secret_hint(self):
        from unittest.mock import MagicMock
        from urllib.parse import urlsplit, parse_qs
        server, client = MagicMock(), MagicMock()
        server.__enter__.return_value = server
        client.__enter__.return_value = client
        server.getsockname.return_value = ('127.0.0.1', 3210)
        server.accept.return_value = (client, ('127.0.0.1', 5678))
        requests = []
        def browser(url):
            query = parse_qs(urlsplit(url).query)
            requests.append(query)
            target = '/auth/callback?'+urlencode(dict(state=query['state'][0], code='code', client_id='oaiapp_test'))
            client.recv.return_value = ('GET '+target+' HTTP/1.1\r\nHost: 127.0.0.1:3210\r\n\r\n').encode()
            return True
        with patch.object(login.socket, 'socket', return_value=server), contextlib.redirect_stdout(io.StringIO()):
            result = login.authorize('urn:uuid:synthetic', open_browser=browser)
        server.bind.assert_called_once_with(('127.0.0.1', 0))
        self.assertEqual(requests[0]['client_id'], ['dynamic_agent_client'])
        self.assertEqual(requests[0]['code_challenge'], [oidc.b64(hashlib.sha256(result['code_verifier'].encode()).digest())])
        self.assertEqual(requests[0]['nonce'], [result['nonce']])
        self.assertNotIn('id_token_hint', requests[0])
        self.assertEqual(result['redirect_uri'], 'http://127.0.0.1:3210/auth/callback')

    def test_concurrent_refresh_serializes_rotating_token_use(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as name:
            store = Store(Path(name)/'auth')
            host, _ = store.prepare('local', 'default')
            store.install('local', 'default', record(host, expires_at=0))
            def refresh(*_):
                time.sleep(.02)
                return oauth_reply()
            with patch.object(oidc, 'request', side_effect=refresh) as request, ThreadPoolExecutor(max_workers=2) as workers:
                values = list(workers.map(lambda _: store.access('local', 'default'), range(2)))
            self.assertEqual(values, ['new-access', 'new-access'])
            self.assertEqual(request.call_count, 1)

class TransportTests(unittest.TestCase):
    def test_http_stream_transport_accepts_completion_and_passes_cancellation(self):
        import threading
        from unittest.mock import MagicMock
        from code_reviewer.backends.openai import request_response
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.getheader.return_value = 'text/event-stream; charset=utf-8'
        completed = b'data: '+json.dumps({'type':'response.completed', 'response':reply()}).encode()+b'\n\n'
        response.read1.side_effect = [completed, b'']
        cancelled = threading.Event()
        with patch('code_reviewer.backends.openai.http.client.HTTPSConnection', return_value=conn):
            value = request_response({'stream':True}, 'access', 5, cancelled=cancelled)
        self.assertEqual(value, reply())
        self.assertEqual(conn.request.call_args.args[1], '/v1/responses')
        self.assertEqual(conn.request.call_args.args[3]['Accept'], 'text/event-stream')
        conn.close.assert_called_once()
        cancelled.set()
        with patch('code_reviewer.backends.openai.http.client.HTTPSConnection', return_value=conn):
            with self.assertRaises(KeyboardInterrupt):
                request_response({'stream':True}, 'access', 5, cancelled=cancelled)

    def test_subscription_usage_limit_stops_without_retry_or_api_fallback(self):
        from code_reviewer.backends.openai import ProviderHTTPError
        transport = unittest.mock.Mock(side_effect=ProviderHTTPError(dict(status=429,
                    code='subscription_sharing_usage_limit_exceeded', type=None, headers={}, retry_after_s=None)))
        runner = factory.create_runner(config(), selected_credential='access', transport=transport)
        result = runner.run(system_prompt='s', user_prompt='u', schema={}, model='test-model', tools='',
                            cwd=None, budget_usd=1, timeout_s=10)
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(transport.call_count, 1)
        self.assertIn('subscription_sharing_usage_limit_exceeded', result.error)

    def test_small_token_allowance_refuses_before_model_request(self):
        transport = unittest.mock.Mock()
        runner = factory.create_runner(config(), selected_credential='access', transport=transport)
        result = runner.run(system_prompt='s', user_prompt='u', schema={}, model='test-model', tools='',
                            cwd=None, budget_usd=1, timeout_s=10, token_limit=1)
        self.assertTrue(result.budget_exceeded)
        transport.assert_not_called()

    def test_catalog_uses_selected_access_token_and_filters_visible_models(self):
        from unittest.mock import MagicMock
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [json.dumps({'models':[
            dict(slug='supported-model',visibility='list'),dict(slug='hidden',visibility='hide')]}).encode(),b'']
        with patch.object(models.http.client, 'HTTPSConnection', return_value=conn):
            self.assertEqual(models.catalog('selected-access'), ['supported-model'])
        self.assertEqual(conn.request.call_args.kwargs['headers']['Authorization'], 'Bearer selected-access')
        self.assertEqual(conn.request.call_args.args[:2], ('GET','/v1/models'))

    def test_slow_refresh_does_not_extend_inference_deadline(self):
        transport = unittest.mock.Mock()
        runner = factory.create_runner(config(), selected_credential='access', transport=transport)
        runner.token_source = lambda: 'refreshed-access'
        with patch('code_reviewer.backends.openai.time.monotonic', side_effect=[0, 6]):
            with self.assertRaises(TimeoutError):
                runner._request({}, 5, None, [])
        transport.assert_not_called()


class UnexpectedStreamTests(unittest.TestCase):
    def run_response(self, media, body):
        from unittest.mock import MagicMock
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.getheader.return_value = media
        pending = bytearray(body)
        def read(count):
            chunk = bytes(pending[:count])
            del pending[:count]
            return chunk
        response.read1.side_effect = read
        runner = factory.create_runner(config(), selected_credential='synthetic-access')
        with patch('code_reviewer.backends.openai.http.client.HTTPSConnection', return_value=conn):
            result = runner.run(system_prompt='s', user_prompt='u', schema={}, model='test-model', tools='',
                                cwd=None, budget_usd=1, timeout_s=5)
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(conn.request.call_count, 1)
        conn.close.assert_called_once()
        return result, response

    def test_json_error_preserves_only_allowlisted_code(self):
        result, response = self.run_response('application/json; charset=utf-8', json.dumps({'error':{
            'code':'subscription_sharing_unsupported_capability', 'message':'private-source synthetic-access'}}).encode())
        self.assertIn('media type application/json', result.error)
        self.assertIn('subscription_sharing_unsupported_capability', result.error)
        self.assertNotIn('private-source', str(result.raw))
        self.assertNotIn('synthetic-access', str(result.raw))
        self.assertNotIn('private-source', result.error)
        self.assertLessEqual(max(c.args[0] for c in response.read1.call_args_list), 8193)

    def test_unexpected_types_never_log_arbitrary_header_or_body(self):
        for media in ('text/html', 'private-header synthetic-access'):
            with self.subTest(media=media):
                result, response = self.run_response(media, b'private-source synthetic-access')
                response.read1.assert_not_called()
                self.assertNotIn('private', result.error)
                self.assertNotIn('synthetic-access', result.error)

    def test_json_success_and_oversized_or_malformed_json_are_not_accepted(self):
        for body in (json.dumps(reply()).encode(), b'{bad', b'x'*9000, b'['*2000+b']'*2000):
            result, response = self.run_response('application/json', body)
            self.assertIn('response rejected', result.error)
            self.assertNotIn('output_text', result.error)

    def test_top_level_stream_error_code_is_safe_and_visible(self):
        body = b'data: {"type":"error","code":"subscription_sharing_usage_limit_exceeded","message":"private-source"}\n\n'
        parts = iter([body])
        with self.assertRaises(StreamFailure) as error:
            read_stream(lambda: next(parts,b''),65536)
        self.assertIn('subscription_sharing_usage_limit_exceeded', str(error.exception))
        self.assertNotIn('private-source', str(error.exception))


class HeaderlessStreamTests(unittest.TestCase):
    def transport_response(self, body):
        import http.client
        from unittest.mock import MagicMock
        class Socket:
            def makefile(self, *_):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n'+body)
        response = http.client.HTTPResponse(Socket())
        response.begin()
        conn = MagicMock()
        conn.getresponse.return_value = response
        runner = factory.create_runner(config(), selected_credential='synthetic-access')
        with patch('code_reviewer.backends.openai.http.client.HTTPSConnection', return_value=conn):
            result = runner.run(system_prompt='s', user_prompt='u', schema={}, model='test-model', tools='',
                                cwd=None, budget_usd=1, timeout_s=5)
        self.assertEqual(conn.request.call_count, 1)
        response.close()
        return result

    def test_valid_headerless_sse_completes_through_real_http_parser(self):
        body = b'event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':reply()}).encode()+b'\n\n'
        result = self.transport_response(body)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.parsed, {'findings': [], 'notes': ''})

    def test_headerless_json_error_retains_only_known_code(self):
        body = json.dumps({'error': {'code': 'subscription_sharing_unsupported_capability',
                                     'message': 'private-source synthetic-access'}}).encode()
        result = self.transport_response(body)
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertIn('body format JSON', result.error)
        self.assertIn('subscription_sharing_unsupported_capability', result.error)
        self.assertNotIn('private-source', result.error)
        self.assertNotIn('synthetic-access', result.error)

    def test_missing_header_does_not_accept_json_success_empty_html_or_truncated_stream(self):
        for body in (json.dumps(reply()).encode(), b'', b'<html>private-source</html>',
                     b'data: {"type":"response.created"}\n\n',
                     b'data: {"type":"response.completed"}'):
            with self.subTest(body_length=len(body)):
                result = self.transport_response(body)
                self.assertFalse(result.ok)
                self.assertFalse(result.retryable)
                self.assertIn('Content-Type missing', result.error)
                self.assertNotIn('private-source', result.error)
                if not body:
                    self.assertIn('received 0 bytes', result.error)

    def test_headerless_prefetch_preserves_stream_limits(self):
        from code_reviewer.backends.openai_stream import read_untyped_stream
        response = SimpleNamespace(getheader=lambda _: None)
        parts = iter([b'data: {}\n\n'])
        with self.assertRaisesRegex(StreamFailure, 'byte limit'):
            read_untyped_stream(response, lambda *_: next(parts,b''), 2)


    def test_failed_pass_keeps_full_safe_diagnostic_in_report_without_replay(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            ctx = make_repo(root)
            cfg = config(runs_dir=str(root/'runs'))
            message = ('OpenAI subscription expected text/event-stream; received HTTP 200, media type missing; '
                       'body format JSON (subscription_sharing_unsupported_capability); response rejected; no automatic replay')
            transport = unittest.mock.Mock(side_effect=StreamFailure(message))
            runner = factory.create_runner(cfg, selected_credential='synthetic-access', transport=transport)
            result = Engine(cfg, runner).review(ctx)
            self.assertEqual(result.passes_completed, 0)
            self.assertEqual(transport.call_count, 1)
            self.assertIn('subscription_sharing_unsupported_capability', result.report_path.read_text())

class DocumentedProtocolTests(unittest.TestCase):
    """Synthetic examples follow the official Responses/Sign-in wire contracts."""
    def events(self, items, *, terminal=None, failure=False):
        values = [dict(type='response.created', response={'status':'in_progress'})]
        for index, item in enumerate(items):
            values += [dict(type='response.output_item.added', output_index=index,
                            item=dict(item, status='in_progress')),
                       dict(type='response.output_item.done', output_index=index, item=item)]
        if failure:
            values.append(dict(type='response.failed', response={'status':'failed', 'error':{
                'code':'subscription_sharing_usage_limit_exceeded', 'message':'private-source'}}))
        else:
            values.append(dict(type='response.completed', response=terminal or dict(reply(), output=[])))
        return b''.join(('event: '+v['type']+'\ndata: '+json.dumps(v)+'\n\n').encode() for v in values)

    def parse(self, data):
        parts = iter([data[n:n+13] for n in range(0,len(data),13)])
        return read_stream(lambda:next(parts,b''), 65536)

    def test_finalized_items_survive_terminal_metadata_and_do_not_duplicate_full_output(self):
        message = reply()['output'][0]
        self.assertEqual(self.parse(self.events([message]))['output'], [message])
        self.assertEqual(self.parse(self.events([message],terminal=reply()))['output'], [message])
        reasoning = dict(type='reasoning', id='r1', summary=[], encrypted_content='opaque-synthetic')
        parsed = self.parse(self.events([reasoning,message]))
        self.assertEqual(parsed['output'], [reasoning,message])

    def test_streamed_read_then_json_keeps_reasoning_and_call_id_in_continuation(self):
        from unittest.mock import MagicMock
        import http.client
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/'app.py').write_text('synthetic source')
            reasoning = dict(type='reasoning', id='r1', summary=[], encrypted_content='opaque-synthetic')
            call = dict(type='function_call', id='f1', call_id='call_1', name='Read', namespace='reviewer',
                        arguments=json.dumps(dict(path='app.py', offset=1, limit=10)))
            streams = iter([self.events([reasoning,call]), self.events(reply()['output'])])
            class Socket:
                def __init__(self, data): self.data=data
                def makefile(self, *_): return io.BytesIO(self.data)
            seen = []
            def connect(*_,**__):
                # Real stdlib HTTP framing, fragmented reads, no live network.
                data = next(streams)
                response = http.client.HTTPResponse(Socket(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n'+data))
                response.begin()
                connection = MagicMock()
                connection.getresponse.return_value = response
                connection.request.side_effect = lambda method,path,body,headers: seen.append(json.loads(body))
                connection.close.side_effect = response.close
                return connection
            runner = factory.create_runner(config(), selected_credential='synthetic-access', proxy=('192.0.2.1',3128))
            with patch('code_reviewer.backends.openai.http.client.HTTPSConnection', side_effect=connect):
                result = runner.run(system_prompt='s', user_prompt='u', schema={'type':'object','properties':{
                    'findings':{'type':'array','items':{'type':'string'}}},'required':['findings']},
                    model='test-model', tools='Read', cwd=root, budget_usd=1, timeout_s=5)
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.num_turns,2)
            second = seen[1]['input']
            self.assertIn(reasoning, second)
            self.assertIn(call, second)
            self.assertEqual(second[-1]['type'],'function_call_output')
            self.assertEqual(second[-1]['call_id'],'call_1')
            self.assertIn('synthetic source',second[-1]['output'])
            for request in seen:
                self.assertFalse(request['store'])
                self.assertTrue(request['stream'])
                self.assertEqual(request['tools'][0]['type'],'namespace')
                self.assertEqual(request['text']['format']['type'],'json_schema')
                self.assertNotIn('max_output_tokens',request)
                self.assertNotIn('previous_response_id',request)

    def test_item_done_without_completed_or_failed_stream_never_accepts_tools(self):
        call = dict(type='function_call',call_id='c1',name='Read',namespace='reviewer',arguments='{}')
        with self.assertRaises(StreamFailure):
            self.parse(self.events([call],failure=True))
        raw = b'data: '+json.dumps(dict(type='response.output_item.done',output_index=0,item=call)).encode()+b'\n\n'
        with self.assertRaises(StreamFailure):
            self.parse(raw)
        with self.assertRaisesRegex(StreamFailure,'duplicate'):
            self.parse(raw+raw+self.events([]))

    def test_unusable_outputs_have_specific_safe_terminal_errors(self):
        outputs = [([], 'no output items'),
                   ([dict(type='reasoning',encrypted_content='private-source')], 'no function calls or output_text'),
                   ([dict(type='message',content=[dict(type='output_text',text='private-source')])], 'not valid JSON'),
                   ([dict(type='message',content=None)], 'content must be a list'),
                   ([dict(type='message',content=[dict(type='output_text',text=None)])], 'missing string text'),
                   ([dict(type='function_call',namespace='reviewer',name='Read',call_id='c1',arguments={})], 'JSON string')]
        with tempfile.TemporaryDirectory() as name:
            for items, expected in outputs:
                with self.subTest(expected=expected):
                    transport = unittest.mock.Mock(return_value=dict(reply(),output=items))
                    runner = factory.create_runner(config(),selected_credential='synthetic-access',transport=transport)
                    result = runner.run(system_prompt='s',user_prompt='u',schema={},model='test-model',tools='Read',
                                        cwd=Path(name),budget_usd=1,timeout_s=5)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.retryable, expected == 'not valid JSON')
                    self.assertIn(expected,result.error)
                    self.assertNotIn('private-source',result.error)
                    self.assertEqual(transport.call_count,1)


class AccountingAuditTests(unittest.TestCase):
    def test_known_usage_is_reconciled_even_when_final_output_is_invalid(self):
        from code_reviewer.shared.stats import Stats
        from code_reviewer.shared.llm import ModelCaller, CallSpec
        from code_reviewer.shared.models import PassFailed
        cfg = config(run_budget_tokens=3000, call_budget_tokens=200)
        result = LLMResult(ok=False, error='invalid output', retryable=False, billing_mode='subscription',
                           usage_known=True, tokens={'input':10,'output':5,'cache_creation':0,'cache_read':3})
        budget = Budget(cfg)
        allowance = budget.reserve('criteria', 200)
        budget.settle('criteria',allowance,result)
        self.assertEqual(budget.snapshot()['criteria']['spent_tokens'], 18)
        self.assertEqual(budget.snapshot()['criteria']['unknown_usage_calls'], 0)
        with tempfile.TemporaryDirectory() as name:
            # The same known-usage rule applies without persona reservations.
            result.budget_tokens_used = None
            stats = Stats()
            runner = unittest.mock.Mock()
            runner.run.return_value = result
            caller = ModelCaller(runner,cfg,stats)
            spec = CallSpec('reviewer',1,'s','u',{},'test-model','',Path(name))
            with self.assertRaises(PassFailed):
                caller.call(spec,lambda _:[],Path(name)/'call.json')
            self.assertEqual(stats.budget_tokens(),18)
