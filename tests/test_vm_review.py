"""Normal VM dispatch, private credentials and adapter selection without paid calls."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_reviewer import cli
from code_reviewer.flows.agent_personas.runner import ParallelRunner
from code_reviewer.config import Config
from code_reviewer.sandbox import review, settings, guest, connected, wire, network
from code_reviewer.sandbox.channel import Channel
from code_reviewer.sandbox.snapshot import CHECKS as ISOLATION
from code_reviewer.sandbox.vm import CHECKS as VMM
from code_reviewer.shared.git_ops import PRRef
from code_reviewer.shared.models import ReviewRefused
from tests.helpers import make_repo, FakeRunner


class VMReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ref = PRRef('github.com', 'example', 'project', 1)
        self.options = SimpleNamespace(dry_run=False, local_path=None, keep_checkout=False)

    def cfg(self, provider='openai', mode='single-agent'):
        return Config(provider=provider, review_mode=mode, min_passes=1, max_passes=1, persona_passes=1,
                      model_reviewer='synthetic-model', model_comparator='synthetic-model',
                      model_resolver='synthetic-model', model_verifier='synthetic-model',
                      openai_input_price=1, openai_cached_input_price=.5, openai_output_price=2,
                      vm_artifacts=str(self.root), vm_lima_home=str(self.root),
                      vm_claude_binary=str(self.root/'claude'), vm_claude_sha256='a'*64,
                      runs_dir=str(self.root/'runs'))

    def checks(self):
        return {'network': {name: True for name in connected.NETWORK_CHECKS},
                'file_tools': {name: True for name in connected.FILE_CHECKS},
                'isolation': {name: True for name in ISOLATION}, 'source_access_revoked': True}

    def envelope(self):
        files = {name: base64.b64encode(data).decode() for name,data in
                 {'report.md':b'full report', 'summary.md':b'summary', 'stats.json':b'{"calls":[]}'}.items()}
        return {'kind':'finished', 'cleanup':True, 'files':files,
                'result': {'stop_reason':'completed', 'checks':self.checks(), 'vm_checks':{name:True for name in VMM},
                           'acquisition':{'repo':'example/project','number':1,'base':'a'*40,'head':'b'*40,
                                          'merge_base':'a'*40,'source_access_revoked':True}}}

    def test_full_settings_roundtrip_preserves_algorithm_options_without_host_paths(self):
        cfg = self.cfg()
        cfg.criteria_mode = 'defaults_only'
        cfg.extra_exclude_patterns = ['fixtures/']
        cfg.post_tiers = ['High']
        raw = settings.job_settings(cfg)
        clone = settings.config_from_job(json.loads(json.dumps(raw)), self.root)
        for name in settings.CORE:
            self.assertEqual(getattr(clone,name),getattr(cfg,name),name)
        self.assertNotIn('vm_artifacts',raw['config'])
        self.assertNotIn('runs_dir',raw['config'])
        raw['config']['claude_bin'] = '/untrusted'
        with self.assertRaises(ValueError):
            settings.config_from_job(raw, self.root)

    def test_preflight_has_no_activity_and_distinguishes_configuration_from_qualification(self):
        out = io.StringIO()
        with patch.object(cli,'load_config',return_value=self.cfg()), patch.object(settings.sys,'platform','darwin'), \
             patch.object(review,'run',side_effect=AssertionError('no VM')), \
             patch.object(settings,'credential',side_effect=AssertionError('no credential')), contextlib.redirect_stdout(out):
            code = cli.main(['review','example/project#1','--preflight'])
        self.assertEqual(code,0)
        value = json.loads(out.getvalue())
        self.assertTrue(value['ready'])
        self.assertFalse(value['runtime_checked'])

    def test_unsupported_manifest_and_deadline_refuse_during_offline_configuration(self):
        with patch.object(settings.sys,'platform','darwin'):
            for name,value in (('criteria_manifest','manifest.json'),('vm_timeout_s',1801)):
                cfg=self.cfg()
                setattr(cfg,name,value)
                with patch.object(Path,'read_text',side_effect=AssertionError('no manifest/target IO')):
                    errors=review.configuration_errors(cfg,self.ref,self.options)
                self.assertTrue(errors)
                self.assertIn('host-only' if name=='criteria_manifest' else '1800','; '.join(errors))

    def test_normal_cli_selects_vm_instead_of_host_for_each_provider_and_strategy(self):
        for provider in ('claude','openai'):
            for mode in ('single-agent','agent-personas'):
                cfg = self.cfg(provider, mode)
                result = review.ExportedReview(cfg,self.root,'completed','review summary')
                out = io.StringIO()
                with patch.object(cli,'load_config',return_value=cfg), patch.object(settings.sys,'platform','darwin'), \
                     patch.object(review,'run',return_value=result) as run, \
                     patch('code_reviewer.engine.Engine.prepare',side_effect=AssertionError('no host acquisition')), \
                     contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(['review','example/project#1']),0)
                run.assert_called_once()
                self.assertIn('review summary',out.getvalue())
                self.assertIn('VM review:',out.getvalue())

    def test_private_channel_releases_only_selected_credential_after_exact_request(self):
        for provider in ('claude','openai'):
            cfg = self.cfg(provider)
            a,b = socket.socketpair()
            errors = []
            observed = []
            def remote():
                try:
                    request = wire.receive_json(b)
                    observed.append(request)
                    wire.send_json(b,{'kind':'credential','provider':provider,'auth':'api-key'})
                    observed.append(wire.receive(b,4096))
                    wire.send_json(b,self.envelope(),wire.RESULT_LIMIT)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    b.close()
            thread = threading.Thread(target=remote)
            thread.start()
            try:
                value,key = review.converse(Channel(a.fileno(),a.fileno(),5),cfg,review.source_url(self.ref),
                                            lambda p,auth:'fake-selected-key')
            finally:
                a.close()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors,errors)
            self.assertEqual(observed[1],b'fake-selected-key')
            self.assertNotIn('fake-selected-key',str(observed[0]))
            self.assertEqual(value['stop_reason'],'completed')

    def test_worker_bridge_uses_private_frames_and_validated_files(self):
        from code_reviewer.sandbox import review_worker
        a,b=socket.socketpair()
        observed=[]
        result=self.envelope()['result']
        def run(*args,**kwargs):
            key=kwargs['key_reader']('credential')
            observed.append(key)
            raw=dict(kind='result',stop_reason='completed',checks=self.checks(),files=self.envelope()['files'])
            exported=connected.export_result(raw,args[4],key,False)
            return dict(result, **exported)
        failures=[]
        def remote():
            try:
                value=review_worker.dispatch(Channel(b.fileno(),b.fileno(),5),self.root,Path('/image'),'a'*64,900)
                wire.send_json(Channel(b.fileno(),b.fileno(),5),value,wire.RESULT_LIMIT)
            except BaseException as exc:
                failures.append(exc)
            finally:
                b.close()
        with patch.object(review_worker,'run',side_effect=run),patch.object(review_worker,'verify_idle'):
            thread=threading.Thread(target=remote)
            thread.start()
            try:
                value,key=review.converse(Channel(a.fileno(),a.fileno(),5),self.cfg(),review.source_url(self.ref),
                                          lambda *args:'fake-selected-key')
            finally:
                a.close();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures,failures)
        self.assertEqual(observed,['fake-selected-key'])
        self.assertEqual(value['stop_reason'],'completed')
        self.assertEqual((self.root/'result').stat().st_mode & 0o777,0o700)

    def test_public_metadata_failure_reaches_operator_before_key_access(self):
        from code_reviewer.sandbox.acquire import MetadataUnavailable
        for status in (403,404,429):
            with patch.object(review,'send_json'),patch.object(review,'receive_json',return_value=MetadataUnavailable(status).frame()):
                with self.assertRaisesRegex(MetadataUnavailable,'HTTP %d' % status):
                    review.converse(None,self.cfg(),review.source_url(self.ref),lambda *args:self.fail('no credential'))

    def test_wrong_request_or_failed_admission_does_not_read_key(self):
        for value in ({'kind':'failed'}, {'kind':'credential','provider':'claude','auth':'oauth'}):
            with patch.object(review,'send_json'),patch.object(review,'receive_json',return_value=value):
                with self.assertRaises(ValueError):
                    review.converse(None,self.cfg(),review.source_url(self.ref),lambda *args:self.fail('no key'))

    def test_shutdown_failure_publishes_no_reports(self):
        events = []
        class Worker:
            def __init__(self,*args): pass
            @contextlib.contextmanager
            def session(self):
                events.append('start')
                yield
                events.append('stop')
                raise RuntimeError('shutdown failed')
            def shell(self,*args,**kwargs): pass
        cfg = self.cfg()
        (self.root / 'firecracker.tgz').write_bytes(b'synthetic staged archive')
        (self.root / 'vmlinux-6.1.188').write_bytes(b'synthetic staged kernel')
        worker_root = self.root / cfg.vm_instance
        worker_root.mkdir()
        (worker_root / 'lima.yaml').write_text('synthetic worker')
        value = dict(kind='result',stop_reason='completed',checks=self.checks(),files=self.envelope()['files'])
        with patch.object(settings.sys,'platform','darwin'), patch.object(review,'Worker',Worker), \
             patch.object(review,'stage_inputs'),patch.object(review,'build_bundle',return_value={'sha256':'a'*64}), \
             patch.object(review,'prepare_worker',return_value=((),{'images':{'connected':{'path':'/image','sha256':'b'*64}}})), \
             patch.object(review,'execute',return_value=(value,'fake-key')):
            with self.assertRaises(ReviewRefused):
                review.run(cfg,self.ref,self.options)
        self.assertEqual(events,['start','stop'])
        self.assertFalse(cfg.runs_path().exists())

    def test_missing_runtime_inputs_refuse_clearly_before_worker_or_credential_activity(self):
        from code_reviewer.config import ConfigError
        cfg = self.cfg()
        for expected, create in (
                ('Firecracker archive', lambda: (self.root / 'firecracker.tgz').write_bytes(b'fixture')),
                ('guest kernel', lambda: (self.root / 'vmlinux-6.1.188').write_bytes(b'fixture')),
                ('Lima worker definition', lambda: None)):
            with patch.object(settings.sys, 'platform', 'darwin'), \
                 patch.object(review, 'Worker', side_effect=AssertionError('must not start worker')):
                with self.assertRaisesRegex(ConfigError, expected):
                    review.run(cfg, self.ref, self.options, key_reader=lambda *args: self.fail('no credentials'))
            create()

    def test_private_pipe_deadline_applies_to_partial_reads_and_blocked_writes(self):
        import os
        reader, writer = os.pipe()
        try:
            channel = Channel(reader, writer, .02)
            with self.assertRaises(TimeoutError):
                channel.sendall(b'x' * 1024 * 1024)
        finally:
            os.close(reader)
            os.close(writer)
        a,b = socket.socketpair()
        try:
            channel = Channel(a.fileno(), a.fileno(), .02)
            b.sendall(b'\x00')
            with self.assertRaises(TimeoutError):
                wire.receive(channel, 10)
        finally:
            a.close(); b.close()

    def test_native_parallel_subprocess_preserves_private_runner_environment(self):
        from code_reviewer.backends.claude import ClaudeRunner
        from threading import Event
        runner = ClaudeRunner('/fake/claude', env={'ANTHROPIC_API_KEY':'fake-key', 'HTTPS_PROXY':'http://proxy'})
        budget = SimpleNamespace(cancelled=Event())
        with patch('code_reviewer.backends.claude.subprocess.Popen') as popen,              patch('code_reviewer.backends.claude.os.killpg'):
            proc = popen.return_value
            proc.communicate.return_value=(json.dumps({'type':'result','result':'{"findings":[]}'}),'')
            proc.returncode=0
            result=ParallelRunner(runner,budget).run(system_prompt='system',user_prompt='user',schema={},
                    model='model',tools='Read,Grep,Glob',cwd=self.root,budget_usd=.1,timeout_s=5)
        self.assertTrue(result.ok)
        self.assertEqual(popen.call_args.kwargs['env'],runner.env)
        self.assertNotIn('fake-key',str(popen.call_args.args))

    def test_tls_probe_uses_selected_provider_after_direct_connection_denials(self):
        from code_reviewer.sandbox import probes
        class Sock:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def settimeout(self,*args): pass
            def connect(self,*args): raise OSError('blocked')
            def sendto(self,*args): pass
            def recv(self,*args): raise OSError('blocked')
        class Proxy(Sock):
            def sendall(self,*args): pass
            def recv(self,*args): return b'HTTP/1.1 403 Forbidden\r\n'
        def connect(address,**kwargs):
            if address==network.PROXY: return Proxy()
            raise OSError('blocked')
        for provider,host in (('openai','api.openai.com'),('claude','api.anthropic.com')):
            with patch.object(probes.socket,'create_connection',side_effect=connect),                  patch.object(probes.socket,'socket',return_value=Sock()),                  patch.object(probes.http.client,'HTTPSConnection') as https:
                self.assertTrue(all(probes.network_probes(provider).values()))
            https.return_value.set_tunnel.assert_called_once_with(host,443)

    def test_provider_policies_are_exact_and_do_not_grant_other_provider_or_source(self):
        for provider, endpoint in (('openai','api.openai.com'),('claude','api.anthropic.com')):
            policy = network.squid_config('/tmp/proxy',provider=provider)
            self.assertIn(endpoint, policy)
            self.assertNotIn('github.com',policy)
            other = 'api.openai.com' if provider=='claude' else 'api.anthropic.com'
            self.assertNotIn(other,policy)
            self.assertNotIn(endpoint, network.squid_config('/tmp/proxy','acquisition',provider))

    def test_guest_uses_selected_adapter_and_configuration_for_both_algorithms(self):
        from code_reviewer.sandbox import capsule
        ctx = make_repo(self.root)
        target = self.root/'input.tar'
        capsule.prepare(ctx.git_dir,ctx.base_sha,ctx.head_sha,target)
        def api(payload,key,timeout,**kwargs):
            properties = payload['text']['format']['schema'].get('properties',{})
            value = {'criteria_files':[], 'inline_sections':[]} if 'criteria_files' in properties else {'findings':[]}
            return {'status':'completed','usage':{'input_tokens':10,'output_tokens':2},
                    'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}]}
        for provider,auth in (('claude','api-key'),('claude','subscription'),('openai','api-key')):
            for mode in ('single-agent','agent-personas'):
                cfg = self.cfg(provider,mode)
                cfg.auth_mode=auth
                cfg.criteria_mode='defaults_only'
                root = self.root/(provider+auth+mode)
                root.mkdir()
                with patch.object(guest,'receive_json',return_value=settings.job_settings(cfg)), \
                     patch.object(guest,'receive',side_effect=[target.read_bytes(),b'fake-selected-key']), \
                     patch.object(guest,'send_json'),patch.object(guest,'request_response',api), \
                     patch('code_reviewer.backends.factory.ClaudeRunner') as native:
                    native.return_value=FakeRunner([{'findings':[]}] * 8)
                    result=guest.run_job(None,root,probe_network=lambda:self.checks()['network'])
                self.assertIn(result['stop_reason'],('completed','max_passes'))
                metadata=json.loads(next((root/'runs').glob('*/config.json')).read_text())['config']
                self.assertEqual(metadata['provider'],provider)
                self.assertEqual(metadata['criteria_mode'],'defaults_only')
                if provider=='claude':
                    env=native.call_args.kwargs['env']
                    selected_name=settings.credential_name(provider,auth)
                    self.assertEqual(env[selected_name],'fake-selected-key')
                    self.assertNotIn('ANTHROPIC_API_KEY' if auth=='subscription' else 'CLAUDE_CODE_OAUTH_TOKEN',env)
                    self.assertNotIn('OPENAI_API_KEY',env)
                self.assertNotIn('fake-selected-key',json.dumps(result))
                self.assertIn('ephemeral guest files',base64.b64decode(result['files']['report.md']).decode())
