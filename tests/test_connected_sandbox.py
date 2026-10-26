"""No-root/no-network regressions for pilot admission, credentials, policy and artifacts."""
from __future__ import annotations

import base64
import io
import json
import os
import socket
import struct
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_reviewer.backends.read_tools import ReadOnlyTools
from code_reviewer.backends.openai import request_response
from code_reviewer.sandbox import capsule, connected, guest, network, wire
from code_reviewer.sandbox.git_input import prepare_repository, bounded_export
from code_reviewer.sandbox.image import build_initrd
from tests.helpers import make_repo


class ConnectedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.settings = dict(model='synthetic-model', prices=[1,.5,2], mode='single-agent', passes=1,
                             parallel=1, budget=3, call_budget=.5, probe_only=True)

    def checks(self):
        return {'network': {key: True for key in connected.NETWORK_CHECKS},
                'file_tools': {key: True for key in connected.FILE_CHECKS}}

    def capsule(self):
        ctx = make_repo(self.root)
        target = self.root / 'input.tar'
        capsule.prepare(ctx.git_dir, ctx.base_sha, ctx.head_sha, target)
        return target, ctx

    def test_archive_failure_never_publishes_an_image(self):
        root = self.root / 'image'
        root.mkdir()
        (root / 'fixture').write_text('harmless')
        fake = self.root / 'fake-cpio'
        fake.write_text('#!/bin/sh\nexit 23\n')
        fake.chmod(0o700)
        output = self.root / 'initrd'
        with self.assertRaises(subprocess.CalledProcessError):
            build_initrd(root, output, cpio=str(fake))
        self.assertFalse(output.exists())

    def test_binary_and_invalid_utf8_reads_consume_scan_allowance(self):
        for data in (b'\x00' * 32, b'\xff' * 32):
            (self.root / 'binary.dat').write_bytes(data)
            with patch('code_reviewer.backends.read_tools.MAX_SCAN', 1):
                result = json.loads(ReadOnlyTools(self.root).execute('Grep', {'pattern': 'missing'}))
            self.assertIn('error', result)

    def test_capsule_preparation_runs_no_git_and_excludes_config_hooks(self):
        ctx = make_repo(self.root)
        (ctx.git_dir / '.git/config').write_text('synthetic operator-untrusted config')
        (ctx.git_dir / '.git/hooks/canary').write_text('not executable')
        target = self.root / 'input.tar'
        with patch('subprocess.run', side_effect=AssertionError('must not execute during prepare')):
            capsule.prepare(ctx.git_dir, ctx.base_sha, ctx.head_sha, target)
        with tarfile.open(target) as archive:
            names = archive.getnames()
        self.assertIn('job.json', names)
        self.assertFalse(any('config' in name or 'hooks' in name for name in names))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_pinned_objects_reconstruct_in_a_clean_repository(self):
        source, ctx = self.capsule()
        admitted = prepare_repository(source.read_bytes(), self.root / 'clean.git')
        self.assertEqual(admitted.head_sha, ctx.head_sha)
        dest = self.root / 'checkout'
        counts = bounded_export(admitted.git_dir, admitted.head_sha, dest)
        self.assertGreater(counts['files'], 0)
        self.assertIn('query', (dest / 'app.py').read_text())
        self.assertFalse((dest / '.git').exists())

    def test_object_links_alternates_and_size_limits_fail_closed(self):
        ctx = make_repo(self.root)
        objects = ctx.git_dir / '.git/objects'
        (objects / 'info/alternates').write_text('/not/admitted')
        with self.assertRaises(ValueError):
            capsule.prepare(ctx.git_dir, ctx.base_sha, ctx.head_sha, self.root / 'a')
        (objects / 'info/alternates').unlink()
        (objects / 'link').symlink_to(self.root)
        with self.assertRaises(ValueError):
            capsule.prepare(ctx.git_dir, ctx.base_sha, ctx.head_sha, self.root / 'a')
        (objects / 'link').unlink()
        with patch.object(capsule, 'MAX_OBJECT', 1):
            with self.assertRaises(ValueError):
                capsule.prepare(ctx.git_dir, ctx.base_sha, ctx.head_sha, self.root / 'a')

    def test_capsule_rejects_links_traversal_duplicates_and_missing_metadata(self):
        for name, link, duplicate in (('../escape', False, False), ('config', False, False),
                                       ('job.json', True, False), ('job.json', False, True)):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as archive:
                member = tarfile.TarInfo(name)
                data = b'{}'
                member.size = len(data)
                if link:
                    member.type, member.linkname = tarfile.SYMTYPE, '/outside'
                archive.addfile(member, io.BytesIO(data))
                if duplicate:
                    archive.addfile(member, io.BytesIO(data))
            with self.assertRaises(ValueError):
                capsule.unpack(output.getvalue(), self.root / 'no-export')
        self.assertFalse((self.root / 'escape').exists())

    def test_frame_length_and_eof_are_validated_before_allocation(self):
        a,b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        a.sendall(struct.pack('!Q', 1000000000))
        with self.assertRaises(ValueError):
            wire.receive(b, 10)
        a.close()
        with self.assertRaises(ValueError):
            wire.receive(b, 10)

    def test_model_settings_are_operator_only_bounded_and_complete(self):
        self.assertEqual(guest.validate_settings(dict(self.settings)), self.settings)
        for key, value in (('passes', True), ('parallel', 5), ('budget', float('nan')), ('model', ''), ('probe_only', 1)):
            with self.assertRaises(ValueError):
                guest.validate_settings(dict(self.settings, **{key: value}))
        with self.assertRaises(ValueError):
            guest.validate_settings(dict(self.settings, claude_bin='untrusted'))

    def test_hidden_key_prompt_refuses_noninteractive_echo_fallback(self):
        with patch.object(connected.sys,'platform','linux'), patch.object(connected.os,'geteuid',return_value=0), \
                patch.object(connected.os,'access',return_value=True), patch.object(connected.sys.stdin,'isatty',return_value=False):
            with self.assertRaisesRegex(ValueError,'terminal'):
                connected.run(self.root/'vmm',self.root/'kernel',self.root/'initrd',self.root/'input',self.root/'out',
                              dict(self.settings,probe_only=False))

    def test_key_reader_is_not_called_if_probes_or_host_policy_fail(self):
        reader = lambda prompt: self.fail('key must not be read')
        with patch.object(connected, 'send_json'), patch.object(connected, 'send'), \
                patch.object(connected, 'receive_json', return_value={'kind': 'failed'}):
            with self.assertRaises(ValueError):
                connected.exchange(None, dict(self.settings, probe_only=False), b'input', self.root/'out', reader)
        with patch.object(connected, 'send_json'), patch.object(connected, 'send'), \
                patch.object(connected, 'receive_json', return_value={'kind': 'ready','checks':self.checks()}), \
                patch.object(connected, 'command', side_effect=RuntimeError('policy missing')):
            with self.assertRaises(RuntimeError):
                connected.exchange(None, dict(self.settings, probe_only=False), b'input', self.root/'out', reader)

    def test_probe_mode_accepts_no_key_and_exports_only_expected_files(self):
        value = {'kind':'result','stop_reason':'probes_passed','checks':self.checks(),
                 'files': {'boundary-report.json':base64.b64encode(b'{}').decode()}}
        with patch.object(connected, 'send_json'), patch.object(connected, 'send') as send, \
                patch.object(connected, 'command'), patch.object(connected, 'receive_json', side_effect=[
                    {'kind':'ready','checks':self.checks()},value]):
            result = connected.exchange(None,self.settings,b'input',self.root/'out',lambda p:self.fail('no key'))
        self.assertEqual(send.call_args_list[-1].args[1], b'')
        self.assertEqual(result['stop_reason'],'probes_passed')
        self.assertEqual((self.root/'out').stat().st_mode & 0o777,0o700)

    def test_full_guest_review_exports_reports_in_both_modes_without_leaking_key(self):
        source, _ = self.capsule()
        def provider(payload, key, timeout, **kwargs):
            schema = payload['text']['format']['schema']
            if 'criteria_files' in schema.get('properties', {}):
                value = {'criteria_files': [], 'inline_sections': []}
            else:
                value = {'findings': []}
            return {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 20},
                    'output': [{'type': 'message', 'content': [{'type':'output_text','text':json.dumps(value)}]}]}
        for mode in ('single-agent', 'agent-personas'):
            root = self.root / mode
            root.mkdir()
            settings = dict(self.settings, mode=mode, probe_only=False)
            with patch.object(guest, 'receive_json', return_value=settings), \
                    patch.object(guest, 'receive', side_effect=[source.read_bytes(), b'disposable-unit-test-key']), \
                    patch.object(guest, 'send_json') as ready_message, patch.object(guest, 'request_response', provider):
                value = guest.run_job(None, root, probe_network=lambda: self.checks()['network'])
            self.assertEqual(ready_message.call_args.args[1]['kind'], 'ready')
            self.assertEqual(set(value['files']), {'report.md', 'summary.md', 'stats.json'})
            for encoded in value['files'].values():
                self.assertNotIn(b'disposable-unit-test-key',base64.b64decode(encoded))
            output = self.root / (mode + '-export')
            result = connected.export_result(value,output,'disposable-unit-test-key',False)
            self.assertIn(result['stop_reason'],('max_passes','completed'))

    def test_failed_reviews_preserve_reports_and_failure_accounting(self):
        source, _ = self.capsule()
        for mode in ('single-agent', 'agent-personas'):
            root = self.root / mode
            root.mkdir()
            settings = dict(self.settings, mode=mode, probe_only=False)
            with patch.object(guest, 'receive_json', return_value=settings), \
                    patch.object(guest, 'receive', side_effect=[source.read_bytes(), b'disposable-unit-test-key']), \
                    patch.object(guest, 'send_json'), \
                    patch.object(guest, 'request_response', return_value={
                        'status': 'completed', 'usage': {'input_tokens': 10, 'output_tokens': 5},
                        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{}'}]}]}):
                value = guest.run_job(None, root, probe_network=lambda: self.checks()['network'])
            self.assertEqual(value['stop_reason'], 'failures')
            output = self.root / (mode + '-failed-export')
            result = connected.export_result(value, output, 'disposable-unit-test-key', False)
            self.assertEqual(result['stop_reason'], 'failures')
            self.assertGreater(len(json.loads((output / 'stats.json').read_text())['calls']), 0)

    def test_output_ownership_is_assigned_after_files_without_widening_permissions(self):
        with patch.object(connected.os, 'chown') as chown, patch.object(connected.pwd, 'getpwuid') as user:
            user.return_value.pw_gid = 123
            connected.assign_output({'files':['report.md']},self.root/'out',456)
        self.assertEqual(chown.call_args_list[0].args,(self.root/'out/report.md',456,123))
        self.assertEqual(chown.call_args_list[-1].args,(self.root/'out',456,123))
        self.assertFalse(chown.call_args_list[-1].kwargs['follow_symlinks'])

    def test_unsafe_export_names_or_secret_bytes_are_rejected(self):
        checks=self.checks()
        for files in ({'../escape':'eA=='}, {'report.md':base64.b64encode(b'private-test-key').decode(),
                                             'summary.md':'eA==','stats.json':'e30='}):
            with self.assertRaises(ValueError):
                connected.export_result({'kind':'result','stop_reason':'completed','checks':checks,'files':files},
                                        self.root/'out','private-test-key',False)
        self.assertFalse((self.root/'out').exists())

    def test_proxy_policy_is_exact_and_direct_forwarding_is_blocked(self):
        config=network.squid_config('/tmp/private-proxy')
        self.assertIn('dstdomain -n api.openai.com',config)
        self.assertIn('http_access deny all',config)
        self.assertIn('http_access deny forbidden',config)
        self.assertNotIn('Authorization',config)
        self.assertIn('iifname "cr-review0" drop',network.firewall())
        self.assertIn('oifname "cr-review0" drop',network.firewall())
        self.assertNotIn('masquerade',network.firewall())
        self.assertIn('meta skuid 13 ip daddr',network.firewall())
        self.assertIn('meta skuid 13 meta nfproto ipv6 reject',network.firewall())
        with self.assertRaises(ValueError):
            network.firewall(13,[])
        with self.assertRaises(ValueError):
            network.firewall(13,['invalid resolver'])

    def test_network_cleanup_removes_tap_before_policy(self):
        commands=[]
        class Result: returncode=0
        policy=network.Network(self.root,123)
        policy.tap=policy.table=True
        with patch.object(network,'command',side_effect=lambda cmd,**kw:commands.append(cmd) or Result()):
            policy.__exit__(None,None,None)
        self.assertEqual(commands[0],['ip','link','del',network.TAP])
        self.assertEqual(commands[1][:3],['nft','delete','table'])

    def test_explicit_proxy_keeps_tls_target_and_key_out_of_connect_headers(self):
        recorded=[]
        class Reply:
            status=200
            def read1(self,count): return b''
        class Conn:
            sock=None
            def __init__(self,*args,**kwargs): recorded.append(('connect',args))
            def set_tunnel(self,*args): recorded.append(('tunnel',args))
            def request(self,*args): recorded.append(('request',args))
            def getresponse(self): return Reply()
            def close(self): pass
        with patch('http.client.HTTPSConnection',Conn):
            with self.assertRaises(ValueError):
                request_response({},'private-test-key',5,proxy=network.PROXY)
        self.assertEqual(recorded[1],('tunnel',('api.openai.com',443)))
        self.assertNotIn('private-test-key',str(recorded[:2]))
