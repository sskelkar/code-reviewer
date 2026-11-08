"""Untrusted acquisition never selects host Git, policy, credentials or moving refs."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from code_reviewer.sandbox import acquire, connected, guest, network, snapshot
from code_reviewer.sandbox.connected import FILE_CHECKS, NETWORK_CHECKS

URL = 'https://github.com/example/demo/pull/7'


def pins():
    return dict(repo='example/demo', number=7, base='a'*40, head='b'*40, merge_base='c'*40)


def metadata():
    return dict(number=7, title='Example change', body='Example description',
                base=dict(sha='a'*40, repo=dict(full_name='example/demo', private=False)), head=dict(sha='b'*40))


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_only_exact_public_github_pr_urls_and_bounded_hashes_are_admitted(self):
        self.assertEqual(acquire.parse_pr(URL+'/changes'), ('example/demo', 7))
        for url in (URL+'?token=test', URL+'#x', URL.replace('https:', 'http:'),
                    URL.replace('github.com', 'github.com.evil.invalid'), URL.replace('github.com', 'user@github.com'),
                    URL.replace('/demo/', '/../'), URL.replace('/7', '/0'), URL.replace('/7', '/1234567890')):
            with self.subTest(url=url), self.assertRaises(ValueError):
                acquire.parse_pr(url)
        for row in (dict(pins(), base='main'), dict(pins(), number=True), dict(pins(), repo='example/other'),
                    dict(pins(), head='-option'), dict(pins(), extra='ignored')):
            with self.assertRaises(ValueError):
                acquire.pin(row, URL)

    def test_metadata_redirect_auth_rate_limit_and_oversize_fail_without_credentials(self):
        connection = Mock()
        response = connection.getresponse.return_value
        with patch.object(acquire.http.client, 'HTTPSConnection', return_value=connection):
            for status in (301, 302, 401, 403, 429):
                response.status = status
                with self.assertRaises(ValueError):
                    acquire.metadata(URL)
            response.status = 200
            response.read.return_value = b'x'*(acquire.META_BYTES+1)
            with self.assertRaises(ValueError):
                acquire.metadata(URL)
            response.read.return_value = json.dumps(metadata()).encode()
            self.assertEqual(acquire.metadata(URL), metadata())
        connection.set_tunnel.assert_called_with('api.github.com', 443)
        headers = connection.request.call_args.kwargs['headers']
        self.assertNotIn('Authorization', headers)
        self.assertTrue(connection.close.called)

    def test_comparison_request_is_pinned_bounded_and_never_accepts_an_external_url(self):
        connection=Mock()
        response=connection.getresponse.return_value
        response.status=200
        response.read.return_value=json.dumps({'base_commit':{'sha':'a'*40},
                                              'merge_base_commit':{'sha':'c'*40}}).encode()
        with patch.object(acquire.http.client,'HTTPSConnection',return_value=connection):
            value=acquire.metadata(URL,compare=('a'*40,'b'*40))
            self.assertEqual(acquire.comparison_base(value,'a'*40,'b'*40),'c'*40)
            path=connection.request.call_args.args[1]
            self.assertEqual(path,'/repos/example/demo/compare/'+'a'*40+'...'+'b'*40+'?per_page=1&page=2')
            self.assertNotIn('Authorization',connection.request.call_args.kwargs['headers'])
            calls=connection.request.call_count
            for hashes in (('main','b'*40),('https://evil.invalid','b'*40)):
                with self.assertRaisesRegex(ValueError,'comparison'):
                    acquire.metadata(URL,compare=hashes)
            self.assertEqual(connection.request.call_count,calls)

    def test_source_failure_frames_accept_only_status_not_server_text(self):
        for status in (301,401,403,404,429,503):
            failure=acquire.source_failure(acquire.MetadataUnavailable(status).frame())
            self.assertEqual(failure.status,status)
            self.assertIn('HTTP %d' % status,str(failure))
        for frame in ({'kind':'failed','code':'github_metadata_http','http_status':True},
                      {'kind':'failed','code':'github_metadata_http','http_status':404,'body':'must not propagate'}):
            with self.assertRaises(ValueError):
                acquire.source_failure(frame)
        self.assertIsNone(acquire.source_failure({'kind':'failed','error':'untrusted text'}))

    def test_tree_limit_errors_include_bounded_escaped_metadata_not_exception_text(self):
        from code_reviewer.sandbox import git_input
        entries = {b'fixture.txt':dict(mode=b'100644',kind=b'blob',oid='a'*40)}
        with patch.object(git_input,'tree',return_value=entries), \
             patch.object(git_input,'sizes',return_value={'a'*40:git_input.FILE_BYTES+1}), \
             patch.object(git_input,'blob_is_binary',return_value=False):
            try:
                git_input.admit_tree(self.root,'b'*40)
            except ValueError as exc:
                failure=acquire.acquisition_error(exc)
        self.assertEqual(failure.reason,'file-bytes')
        self.assertIn('2 MiB',str(failure))
        self.assertEqual(failure.file, '"fixture.txt"')
        self.assertEqual(acquire.source_failure(failure.frame()).size,git_input.FILE_BYTES+1)
        escaped=acquire.acquisition_error(git_input.TreeFileTooLarge(b'\x1bunsafe\n.bin',git_input.FILE_BYTES+1))
        self.assertNotIn('\x1b',escaped.file)
        self.assertNotIn('\n',escaped.file)
        self.assertEqual(acquire.acquisition_error(ValueError('arbitrary secret-like text')).reason,'unexpected')
        self.assertNotIn('secret-like',str(acquire.acquisition_error(ValueError('arbitrary secret-like text'))))
        with self.assertRaises(ValueError):
            acquire.source_failure({'kind':'failed','code':'public_acquisition','reason':['not a code']})

    def test_metadata_cannot_select_another_repo_private_input_or_git_ref(self):
        acquire.describe(metadata(), URL)
        changes = [('private', True), ('full_name', 'example/other')]
        for key, value in changes:
            row = metadata()
            row['base']['repo'][key] = value
            with self.assertRaises(ValueError):
                acquire.describe(row, URL)
        for key, value in [('base', {'sha': '--option'}), ('number', True), ('title', 'x'*1025), ('body', 'x'*32769)]:
            with self.assertRaises(ValueError):
                acquire.describe(dict(metadata(), **{key:value}), URL)

    def test_fetch_uses_only_pinned_hashes_clean_https_policy_and_reaps_helpers(self):
        from code_reviewer.shared import snapshots
        process = Mock(pid=1234, returncode=0)
        process.poll.return_value = 0
        captured=[]
        def launch(argv, **kwargs):
            captured.append((argv,kwargs['stdin'].read()))
            return process
        with patch.object(snapshots.subprocess,'Popen',side_effect=launch), \
             patch.object(snapshots.subprocess,'run'), patch.object(snapshots.os,'killpg') as kill:
            acquire.fetch(self.root,'example/demo',['a'*40,'b'*40],shallow=True)
        argv,refs=captured[0]
        self.assertEqual(argv[-1],'https://github.com/example/demo.git')
        self.assertEqual(refs,('a'*40+'\n'+'b'*40+'\n').encode())
        for option in ('protocol.allow=never','protocol.https.allow=always','credential.helper=',
                       'http.followRedirects=false','http.sslVerify=true','fetch.fsckObjects=true',
                       '--no-tags','--no-recurse-submodules','--depth=1','--filter=blob:none'):
            self.assertIn(option,argv)
        kill.assert_called_once()
        process.wait.assert_called_once()
        process.poll.return_value = None
        with patch.object(snapshots.subprocess,'Popen',return_value=process), \
             patch.object(snapshots.subprocess,'run'), patch.object(snapshots.os,'killpg'), \
             patch.object(acquire,'inventory',side_effect=ValueError('limit')):
            with self.assertRaisesRegex(ValueError,'limit'):
                acquire.fetch(self.root,'example/demo',['a'*40])
        self.assertEqual(process.wait.call_count,2)

    def test_inventory_caps_storage_and_files(self):
        (self.root/'pack').write_bytes(b'1234')
        with patch.object(acquire, 'GIT_BYTES', 3), self.assertRaisesRegex(ValueError, 'bytes'):
            acquire.inventory(self.root)
        with patch.object(acquire, 'GIT_FILES', 0), self.assertRaisesRegex(ValueError, 'count'):
            acquire.inventory(self.root)

    def test_setup_uses_a_clean_unprivileged_subprocess_and_bounded_reply(self):
        value = dict(pins(), title='Example', body='')
        def run(*args, **kwargs):
            kwargs['stdout'].write(json.dumps(value).encode())
            return Mock(returncode=0)
        with patch.object(acquire.subprocess, 'run', side_effect=run) as call, \
             patch.dict(os.environ, {'GITHUB_TOKEN':'synthetic-not-a-secret', 'GIT_CONFIG_COUNT':'10'}):
            ctx = acquire.in_guest(URL, self.root/'repo')
        env = call.call_args.kwargs['env']
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('GIT_CONFIG_COUNT', env)
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(call.call_args.kwargs['preexec_fn'], snapshot.drop_privileges)
        self.assertEqual(ctx.repo, 'example/demo')

    def test_phase_switch_kills_existing_proxy_before_new_policy_and_never_goes_back(self):
        policy = network.Network(self.root, 65534, acquisition=True)
        policy.tap = policy.table = True
        events = []
        with patch.object(policy, 'stop_proxy', side_effect=lambda: events.append('stop')), \
             patch.object(policy, 'start_proxy', side_effect=lambda: events.append(policy.phase)), \
             patch.object(network, 'command'):
            policy.inference()
            with self.assertRaises(ValueError):
                policy.inference()
        self.assertEqual(events, ['stop', 'inference'])
        source = network.squid_config(self.root, 'acquisition')
        inference = network.squid_config(self.root)
        self.assertIn('dstdomain -n github.com api.github.com', source)
        self.assertNotIn('api.openai.com', source)
        self.assertNotIn('github.com', inference)

    def test_failed_phase_switch_cannot_acknowledge_revoke_or_read_credentials(self):
        policy = Mock()
        policy.inference.side_effect = RuntimeError('policy failed')
        settings = dict(probe_only=False)
        with patch.object(connected, 'send_json') as send, patch.object(connected, 'receive_json',
                return_value=dict(kind='acquired', source=pins())):
            with self.assertRaisesRegex(RuntimeError, 'policy failed'):
                connected.exchange(None, settings, None, self.root/'out',
                                   lambda _: self.fail('no credentials'), network=policy, source=URL)
        self.assertEqual(send.call_args_list[0].args[1], settings)
        self.assertEqual(send.call_args_list[-1].args[1], dict(kind='github-pr', url=URL))
        self.assertFalse((self.root/'out').exists())

    def test_acquired_hashes_and_revocation_are_returned_without_key_or_target_files(self):
        checks = dict(network=dict.fromkeys(NETWORK_CHECKS, True), file_tools=dict.fromkeys(FILE_CHECKS, True),
                      source_access_revoked=True)
        value = dict(kind='result', stop_reason='probes_passed', checks=checks,
                     files={'boundary-report.json':base64.b64encode(b'{}').decode()})
        with patch.object(connected, 'send_json') as send, patch.object(connected, 'send'), \
             patch.object(connected, 'command'), patch.object(connected, 'receive_json', side_effect=[
                 dict(kind='acquired', source=pins()), dict(kind='ready', checks=checks), value]):
            policy = Mock()
            result = connected.exchange(None, dict(probe_only=True), None, self.root/'out',
                         lambda _: self.fail('no key'), network=policy, source=URL)
        policy.inference.assert_called_once()
        self.assertEqual(send.call_args_list[-1].args[1], dict(kind='source-access-revoked'))
        self.assertEqual(result['acquisition'], dict(pins(), source_access_revoked=True))
        self.assertEqual(result['files'], ['boundary-report.json'])

    def test_guest_waits_for_external_revocation_before_sealing_and_readiness(self):
        ctx = acquire.context(dict(pins(), title='Example', body=''), URL, self.root/'repo')
        settings = dict(model='synthetic', prices=[1, .5, 2], passes=1, parallel=1,
                        budget=3, call_budget=.5, probe_only=True)
        tunnel = Mock()
        tunnel.__enter__ = Mock(return_value=tunnel)
        tunnel.__exit__ = Mock(return_value=False)
        tunnel.recv.return_value = b''
        events = []
        def message(sock, value):
            events.append(value['kind'])
        def seal(*args, **kwargs):
            self.assertEqual(kwargs, {'exported':True})
            events.append('seal')
            return self.root/'snapshot'
        with patch.object(guest, 'receive_json', side_effect=[settings, {'kind':'source-access-revoked'}]), \
             patch.object(guest, 'receive', side_effect=[json.dumps(dict(kind='github-pr', url=URL)).encode(), b'']), \
             patch.object(acquire, 'in_guest', return_value=ctx), patch.object(guest, 'send_json', side_effect=message), \
             patch('code_reviewer.sandbox.probes.source_tunnel', return_value=tunnel), \
             patch('code_reviewer.sandbox.probes.source_denied', return_value=True), \
             patch('code_reviewer.sandbox.snapshot.probes', return_value={}), patch.object(guest, 'file_probes', return_value={}):
            result = guest.run_job(None, self.root, probe_network=lambda: {}, seal=seal)
        self.assertEqual(events, ['acquired', 'seal', 'ready'])
        self.assertTrue(result['checks']['source_access_revoked'])

    def test_export_refuses_a_result_that_loses_its_source_revocation_check(self):
        checks = dict(network=dict.fromkeys(NETWORK_CHECKS, True), file_tools=dict.fromkeys(FILE_CHECKS, True),
                      source_access_revoked=True)
        with patch.object(connected, 'send_json'), patch.object(connected, 'send'), \
             patch.object(connected, 'command'), patch.object(connected, 'receive_json', side_effect=[
                 dict(kind='acquired', source=pins()), dict(kind='ready', checks=checks),
                 dict(kind='result', checks=dict(checks, source_access_revoked=False))]):
            with self.assertRaisesRegex(ValueError, 'source revocation'):
                connected.exchange(None, dict(probe_only=True), None, self.root/'out',
                                   lambda _: self.fail('no key'), network=Mock(), source=URL)
        self.assertFalse((self.root/'out').exists())
