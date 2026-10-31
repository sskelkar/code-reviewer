"""Binary omission policy: real throwaway Git, no network or model calls."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_reviewer.config import Config
from code_reviewer.engine import Engine
from code_reviewer.presentation import render_summary
from code_reviewer.sandbox import capsule, git_input
from code_reviewer.shared import git_ops
from code_reviewer.shared.git_content import binary_sample, blob_is_binary, omission_section
from .helpers import FakeRunner, git, make_repo
from .test_persona_flow import PersonaRunner


class BinaryOmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = make_repo(self.root)
        self.repo = self.ctx.git_dir

    def commit(self):
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'Synthetic content')
        self.ctx.head_sha = git(self.repo, 'rev-parse', 'HEAD')

    def test_sample_is_content_based_and_handles_utf8_boundary(self):
        self.assertTrue(binary_sample(b'archive\0data'))
        self.assertTrue(binary_sample(b'\xff\xfe'))
        self.assertFalse(binary_sample('source = "caf\u00e9"'.encode()))
        self.assertFalse(binary_sample(b'a'*7999 + b'\xc3'))

    def test_all_binaries_are_omitted_from_vm_and_host_exports(self):
        (self.repo/'small.py').write_bytes(b'\0synthetic binary')
        (self.repo/'large.dat').write_bytes(b'\0'*(git_input.FILE_BYTES+1))
        (self.repo/'looks-binary.jar').write_text('ordinary text\n')
        self.commit()
        for name, exporter in [('vm',git_input.bounded_export), ('host',git_ops.export_tree)]:
            dest = self.root/name
            stats = exporter(self.repo, self.ctx.head_sha, dest)
            self.assertEqual(stats['skipped_binaries'],2)
            self.assertFalse((dest/'small.py').exists())
            self.assertFalse((dest/'large.dat').exists())
            self.assertEqual((dest/'looks-binary.jar').read_text(),'ordinary text\n')
            self.assertTrue((dest/'app.py').exists())

    def test_large_text_and_total_text_limits_still_refuse(self):
        (self.repo/'large.txt').write_bytes(b'x'*(git_input.FILE_BYTES+1))
        self.commit()
        with self.assertRaises(git_input.TreeFileTooLarge):
            git_input.admit_tree(self.repo,self.ctx.head_sha)
        (self.repo/'large.txt').unlink()
        (self.repo/'large.dat').write_bytes(b'\0'*(git_input.FILE_BYTES+1))
        self.commit()
        with patch.object(git_input,'TREE_BYTES',1), self.assertRaisesRegex(ValueError,'total byte'):
            git_input.admit_tree(self.repo,self.ctx.head_sha)
        with patch.object(git_input,'TREE_FILES',1), self.assertRaisesRegex(ValueError,'file count'):
            git_input.admit_tree(self.repo,self.ctx.head_sha)

    def test_capsule_admission_skips_binary_without_expanding_an_archive(self):
        (self.repo/'asset.bin').write_bytes(b'\0'*(git_input.FILE_BYTES+1))
        self.commit()
        target = self.root/'input.tar'
        capsule.prepare(self.repo,self.ctx.base_sha,self.ctx.head_sha,target)
        ctx = git_input.prepare_repository(target.read_bytes(),self.root/'admitted')
        with patch.object(git_input.subprocess,'run', wraps=subprocess.run) as calls:
            git_input.bounded_export(ctx.git_dir,ctx.head_sha,self.root/'snapshot')
        self.assertFalse((self.root/'snapshot/asset.bin').exists())
        self.assertFalse(any('archive' in c.args[0] for c in calls.call_args_list))

    def test_metadata_includes_binary_deletions_and_unchanged_checkout_omissions(self):
        git(self.repo,'checkout','-q',self.ctx.base_sha)
        (self.repo/'deleted.bin').write_bytes(b'\0old')
        (self.repo/'kept.bin').write_bytes(b'\0same')
        git(self.repo,'add','.')
        git(self.repo,'commit','-qm','Synthetic binary base')
        self.ctx.base_sha = self.ctx.merge_base = git(self.repo,'rev-parse','HEAD')
        (self.repo/'deleted.bin').unlink()
        (self.repo/'added.txt').write_bytes(b'\xff\xfe')
        self.commit()
        items = {r['path']:r for r in git_ops.binary_omissions(self.ctx)}
        self.assertEqual(items['deleted.bin']['status'],'deleted')
        self.assertEqual(items['deleted.bin']['base_bytes'],4)
        self.assertIsNone(items['deleted.bin']['head_bytes'])
        self.assertEqual(items['kept.bin']['status'],'unchanged')
        self.assertEqual(items['added.txt']['status'],'added')
        self.assertIsNone(git_ops.git_show(self.repo,self.ctx.head_sha,'added.txt'))

    def test_both_flows_report_omissions_and_do_not_send_forced_text_binary_hunks(self):
        (self.repo/'asset.bin').write_bytes(b'\0must-never-reach-the-model')
        (self.repo/'.gitattributes').write_text('asset.bin diff\n')
        self.commit()
        for mode,runner in [('single-agent',FakeRunner([{'findings':[]}])), ('agent-personas',PersonaRunner())]:
            cfg = Config(review_mode=mode,criteria_mode='defaults_only',runs_dir=str(self.root/mode),
                         min_passes=1,max_passes=1,persona_passes=1,persona_parallel=1)
            result = Engine(cfg,runner).review(self.ctx)
            self.assertIn('Binary contents omitted',result.report_path.read_text())
            self.assertIn('asset.bin',result.report_path.read_text())
            self.assertIn('Binary contents were not reviewed',render_summary(result,cfg.post_tiers,[]))
            self.assertEqual((result.run_dir/'summary.md').read_text(),render_summary(result,cfg.post_tiers,[]))
            stats=json.loads((result.run_dir/'stats.json').read_text())
            self.assertEqual(stats['binary_omissions'][0]['head_bytes'],27)
            for call in runner.calls:
                self.assertNotIn('must-never-reach-the-model',call['user_prompt'])

    def test_report_paths_are_escaped_and_details_capped(self):
        self.ctx.binary_omissions = [dict(path='</code>\n<script>.bin',status='added',base_bytes=None,head_bytes=3)]*201
        text=omission_section(self.ctx)
        self.assertNotIn('<script>',text)
        self.assertIn('200',text)
        self.assertIn('1 additional omissions',text)

    def test_binary_sampling_reaps_child_on_timeout(self):
        oid='a'*40
        from unittest.mock import Mock
        proc=Mock(stdout=Mock(),pid=123)
        selector=Mock()
        selector.__enter__=Mock(return_value=selector)
        selector.__exit__=Mock(return_value=False)
        selector.select.return_value=[]
        proc.poll.return_value=None
        blob_is_binary.cache_clear()
        with patch('code_reviewer.shared.git_content.subprocess.Popen',return_value=proc), \
             patch('code_reviewer.shared.git_content.selectors.DefaultSelector',return_value=selector):
            with self.assertRaises(TimeoutError):
                blob_is_binary('synthetic-repo',oid,42)
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()
        proc.stdout.close.assert_called_once()
