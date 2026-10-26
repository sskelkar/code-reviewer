"""Parse target objects only inside the guest, with bounded tree admission/export."""
from __future__ import annotations

import subprocess
import tarfile
import tempfile
from pathlib import Path

from ..shared import git_ops
from ..shared.models import PRContext
from .capsule import unpack

TREE_BYTES = 32 * 1024 * 1024
FILE_BYTES = 2 * 1024 * 1024
TREE_FILES = 5000


def git(repo, *args, limit=2 * 1024 * 1024):
    with tempfile.TemporaryFile() as out:
        proc = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                               '-C', str(repo), *args], stdout=out, stderr=subprocess.DEVNULL, timeout=60)
        if proc.returncode:
            raise ValueError('admitted Git operation failed')
        if out.tell() > limit:
            raise ValueError('Git output limit exceeded')
        out.seek(0)
        return out.read(limit + 1)


def admit_tree(repo, sha):
    output = git(repo, 'ls-tree', '-rzl', sha)
    count = total = 0
    for row in output.split(b'\x00'):
        if not row:
            continue
        fields, path = row.split(b'\t', 1)
        mode, kind, object_, size = fields.split()
        count += 1
        if count > TREE_FILES or len(path) > 4096:
            raise ValueError('tree path/count limit exceeded')
        if kind == b'blob':
            size = int(size)
            total += size
            if size > FILE_BYTES or total > TREE_BYTES:
                raise ValueError('tree byte limits exceeded')


def prepare_repository(data, root):
    root = Path(root)
    root.mkdir()
    job = unpack(data, root)
    templates = root.parent / 'empty-templates'
    templates.mkdir()
    git(root, 'init', '--bare', '--quiet', '--template=' + str(templates),
        '--object-format=' + ('sha256' if len(job['head']) == 64 else 'sha1'))
    for key in ('base', 'head'):
        commit = git(root, 'rev-parse', '--verify', job[key] + '^{commit}').decode().strip()
        if commit != job[key]:
            raise ValueError('input hash is not a commit')
        admit_tree(root, commit)
    merge_base = git(root, 'merge-base', job['base'], job['head']).decode().strip()
    admit_tree(root, merge_base)
    return PRContext(repo='personal/review', number=1, url='', title=job['title'],
                     body=job['body'], commit_messages='', base_ref='pinned-base', base_sha=job['base'],
                     head_sha=job['head'], merge_base=merge_base, git_dir=root)


def bounded_export(git_dir, sha, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stats = {'files': 0, 'skipped_links': 0}
    total = 0
    with tempfile.TemporaryFile() as archive:
        proc = subprocess.run(['git', '-C', str(git_dir), 'archive', '--format=tar', sha],
                              stdout=archive, stderr=subprocess.DEVNULL, timeout=60)
        if proc.returncode or archive.tell() > TREE_BYTES + 8 * 1024 * 1024:
            raise ValueError('archive generation failed or exceeded limits')
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode='r:') as tar:
            for member in tar:
                from pathlib import PurePosixPath
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('unsafe exported path')
                target = dest / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    total += member.size
                    stats['files'] += 1
                    if member.size > FILE_BYTES or total > TREE_BYTES or stats['files'] > TREE_FILES:
                        raise ValueError('export byte/count limit exceeded')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as out:
                        out.write(tar.extractfile(member).read())
                    target.chmod(0o400)
                else:
                    stats['skipped_links'] += 1
    return stats
