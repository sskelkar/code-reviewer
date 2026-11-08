"""Real filtered Git transfers against throwaway local remotes; no network or models."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_reviewer.shared import git_ops, pr_source, snapshots
from code_reviewer.sandbox import acquire, git_input
from code_reviewer.engine import Engine
from code_reviewer.config import Config
from .helpers import FakeRunner, git

URL = 'https://github.com/example/demo/pull/7'


class SnapshotAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'
        self.source.mkdir()
        git(self.source,'init','-qb','main')
        git(self.source,'config','uploadpack.allowFilter','true')
        git(self.source,'config','uploadpack.allowAnySHA1InWant','true')
        (self.source/'old.txt').write_text('historical content never needed by this review\n')
        git(self.source,'add','.')
        git(self.source,'commit','-qm','History')
        self.history = git(self.source,'rev-parse','HEAD')
        self.old_blob = git(self.source,'rev-parse','HEAD:old.txt')
        (self.source/'old.txt').unlink()
        (self.source/'app.py').write_text('value = 1\n')
        git(self.source,'add','-A')
        git(self.source,'commit','-qm','Common ancestor')
        self.merge = git(self.source,'rev-parse','HEAD')
        git(self.source,'checkout','-qb','feature')
        (self.source/'app.py').write_text('value = 2\n')
        (self.source/'asset.png').write_bytes(b'\0' * (2*1024*1024+1))
        (self.source/'unknown.dat').write_bytes(b'\0binary')
        (self.source/'code.svg').write_text('<svg>text remains reviewable</svg>\n')
        (self.source/'hook.sh').write_text('exit 99\n')
        (self.source/'link').symlink_to('/outside')
        git(self.source,'add','.')
        git(self.source,'commit','-qm','Head')
        self.head = git(self.source,'rev-parse','HEAD')
        self.binary = git(self.source,'rev-parse','HEAD:asset.png')
        git(self.source,'checkout','-q','main')
        (self.source/'AGENTS.md').write_text('## Code review\nCheck cleanup.\n')
        (self.source/'target-only.txt').write_text('Unrelated target change\n')
        git(self.source,'add','.')
        git(self.source,'commit','-qm','Advance target branch')
        self.base = git(self.source,'rev-parse','HEAD')
        self.unused_base_blob = git(self.source,'rev-parse','HEAD:target-only.txt')
        self.real_fetch = snapshots.fetch_objects

    def fetch(self, repo, url, oids, **kwargs):
        # Replace only the transport destination; exercise real filtering and Git objects.
        kwargs['options'] = ('protocol.file.allow=always',)
        self.real_fetch(repo,self.source.as_uri(),oids,**kwargs)

    def missing(self, repo, oid):
        return snapshots.read_git(repo,'cat-file','--batch-check',input=(oid+'\n').encode()).strip() == (oid+' missing').encode()

    def assert_snapshot_only(self, repo):
        self.assertTrue(self.missing(repo,self.history))
        self.assertTrue(self.missing(repo,self.old_blob))
        self.assertTrue(self.missing(repo,self.unused_base_blob))
        self.assertTrue(self.missing(repo,self.binary))
        for sha in (self.base,self.head,self.merge):
            self.assertEqual(snapshots.read_git(repo,'rev-parse',sha+'^{commit}').decode().strip(),sha)
        config=Path(repo)/'config'
        self.assertNotIn('promisor = true',config.read_text())
        self.assertNotIn('remote "',config.read_text())

    def test_public_guest_snapshot_filtering_preserves_pins_diff_criteria_and_review_wiring(self):
        row=dict(number=7,title='Example',body='',base=dict(sha=self.base,repo=dict(full_name='example/demo',private=False)),
                 head=dict(sha=self.head))
        comparison=dict(base_commit=dict(sha=self.base),merge_base_commit=dict(sha=self.merge))
        def metadata(url,**kwargs):
            if kwargs:
                self.assertEqual(kwargs,{'compare':(self.base,self.head)})
                return comparison
            return row
        root=self.root/'guest'
        root.mkdir()
        with patch.object(acquire,'metadata',side_effect=metadata), patch.object(acquire,'fetch_objects',side_effect=self.fetch):
            value=acquire.acquire(URL,root/'repo')
        self.assertEqual(value['merge_base'],self.merge)
        self.assert_snapshot_only(root/'repo')
        self.assertEqual((root/'snapshot/app.py').read_text(),'value = 2\n')
        self.assertFalse((root/'snapshot/asset.png').exists())
        self.assertFalse((root/'snapshot/unknown.dat').exists())
        self.assertFalse((root/'snapshot/link').exists())
        self.assertTrue((root/'snapshot/code.svg').exists())
        ctx=acquire.context(value,URL,root/'repo')
        runner=FakeRunner([{'findings':[]}])
        result=Engine(Config(persona_passes=1,runs_dir=str(self.root/'runs')),runner).review(ctx,prepared_checkout=root/'snapshot')
        self.assertEqual(result.passes_completed,4)
        self.assertTrue(all(r.source=='project' for r in result.criteria.rules))
        diff=(result.run_dir/'input/diff.txt').read_text()
        self.assertNotIn('target-only.txt',diff)
        self.assertIn('value = 2',diff)
        self.assertIn('unknown (not downloaded)',result.report_path.read_text())
        self.assertTrue(self.missing(root/'repo',self.binary))

    def test_host_uses_same_filtered_snapshots_and_does_not_clone_history(self):
        meta=dict(title='Example',body='',baseRefName='main',baseRefOid=self.base,headRefOid=self.head,state='OPEN')
        with patch.object(git_ops,'gh_pr_view',return_value=meta), patch.object(git_ops,'gh_compare_base',return_value=self.merge), \
             patch.object(git_ops,'source_transport',return_value=('https://github.com/example/demo.git',())), \
             patch.object(git_ops,'DATA_DIR',self.root/'host-data'), patch.object(snapshots,'fetch_objects',side_effect=self.fetch):
            ctx=pr_source.load_pr_context(git_ops.parse_pr_ref(URL),None,lambda *a,**kw:None)
        self.assert_snapshot_only(ctx.git_dir)
        self.assertEqual((ctx.base_sha,ctx.head_sha,ctx.merge_base),(self.base,self.head,self.merge))
        self.assertEqual(ctx.commit_messages,'')
        dest=self.root/'host-checkout'
        git_ops.export_tree(ctx.git_dir,self.head,dest)
        self.assertTrue((dest/'app.py').exists())
        self.assertFalse((dest/'asset.png').exists())
        self.assertIn('value = 2',git_ops.git_diff(ctx.git_dir,self.merge,self.head,
                      omitted_paths=[x['path'] for x in git_ops.binary_omissions(ctx)]))
        self.assertTrue(self.missing(ctx.git_dir,self.binary))

    def test_complete_existing_clone_is_reused_without_config_or_shallow_mutation(self):
        git(self.source,'remote','add','origin','https://github.com/example/demo.git')
        before=(self.source/'.git/config').read_bytes()
        meta=dict(title='Example',baseRefName='main',baseRefOid=self.base,headRefOid=self.head)
        with patch.object(git_ops,'gh_pr_view',return_value=meta), patch.object(git_ops,'gh_compare_base',return_value=self.merge), \
             patch.object(snapshots,'fetch_objects',side_effect=AssertionError('no transfer')):
            ctx=pr_source.load_pr_context(git_ops.parse_pr_ref(URL),self.source,lambda *a,**kw:None)
        self.assertEqual(ctx.git_dir.resolve(),self.source.resolve())
        self.assertEqual((self.source/'.git/config').read_bytes(),before)
        self.assertFalse((self.source/'.git/shallow').exists())

    def test_incomplete_local_clone_keeps_its_ssh_transport_without_mutation(self):
        local=self.root/'operator-clone'
        local.mkdir()
        git(local,'init','-q')
        git(local,'remote','add','origin','git@github.com:example/demo.git')
        before=(local/'.git/config').read_bytes()
        meta=dict(title='Example',baseRefName='main',baseRefOid=self.base,headRefOid=self.head)
        transfers=[]
        def transfer(repo,url,oids,**kwargs):
            transfers.append((url,kwargs['options']))
            self.fetch(repo,url,oids,**kwargs)
        with patch.object(git_ops,'gh_pr_view',return_value=meta), patch.object(git_ops,'gh_compare_base',return_value=self.merge), \
             patch.object(git_ops,'DATA_DIR',self.root/'ssh-cache'), patch.object(snapshots,'fetch_objects',side_effect=transfer):
            ctx=pr_source.load_pr_context(git_ops.parse_pr_ref(URL),local,lambda *a,**kw:None)
        self.assert_snapshot_only(ctx.git_dir)
        self.assertTrue(transfers)
        self.assertTrue(all(url=='git@github.com:example/demo.git' and 'protocol.ssh.allow=always' in opts
                            for url,opts in transfers))
        self.assertEqual((local/'.git/config').read_bytes(),before)
        self.assertFalse((local/'.git/shallow').exists())

    def test_host_transport_preferences_are_operator_owned_and_never_url_credentials(self):
        from unittest.mock import Mock
        ref=git_ops.parse_pr_ref(URL)
        for preference in ('ssh','https'):
            with patch.object(git_ops,'_run',return_value=Mock(returncode=0,stdout=preference)):
                url,options=git_ops.source_transport(ref)
            self.assertTrue(url.startswith('git@' if preference=='ssh' else 'https://'))
        local=git_ops.LocalRepo(self.root,'github.com','example','demo')
        with patch.object(git_ops,'_run',return_value=Mock(stdout='https://user:secret@github.com/example/demo.git')):
            url,options=git_ops.source_transport(ref,local)
        self.assertNotIn('secret',url)
        self.assertIn('credential.helper=!gh auth git-credential',options)

    def test_comparison_metadata_rejects_wrong_base_and_missing_merge_identity(self):
        for value in ({}, {'base_commit':{'sha':'0'*40},'merge_base_commit':{'sha':self.merge}},
                      {'base_commit':{'sha':self.base},'merge_base_commit':{'sha':'main'}}):
            with self.assertRaisesRegex(ValueError,'comparison'):
                snapshots.comparison_base(value,self.base,self.head)

    def test_filter_refusal_never_falls_back_to_an_unfiltered_fetch(self):
        git(self.source,'config','uploadpack.allowFilter','false')
        dst=self.root/'refused'
        git(self.source,'init','--bare','-q',str(dst))
        with self.assertRaisesRegex(ValueError,'filtering'):
            self.fetch(dst,'unused',[self.head],shallow=True)

    def test_missing_omitted_blob_never_initiates_lazy_network_read(self):
        dst=self.root/'no-lazy'
        git(self.source,'init','--bare','-q',str(dst))
        self.fetch(dst,'unused',[self.head],shallow=True)
        self.assertTrue(self.missing(dst,self.binary))
        # Even an accidentally retained promisor remote must not make reads fetch.
        git(dst,'config','remote.unused.url',self.source.as_uri())
        git(dst,'config','remote.unused.promisor','true')
        with self.assertRaises(ValueError):
            snapshots.read_git(dst,'cat-file','blob',self.binary)
        self.assertTrue(self.missing(dst,self.binary))
