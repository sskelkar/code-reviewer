"""Parse target objects only inside the guest, with bounded tree admission/export."""
from __future__ import annotations

import subprocess
import json
import tempfile
from pathlib import Path

from ..shared.git_content import blob_is_binary
from ..shared.models import PRContext
from .capsule import unpack

TREE_BYTES = 32 * 1024 * 1024
FILE_BYTES = 2 * 1024 * 1024
TREE_FILES = 5000


class TreeFileTooLarge(ValueError):
    def __init__(self, path, size):
        # Escaped, bounded filename metadata only; contents are never included here.
        self.label = json.dumps(path.decode('utf-8', 'replace'), ensure_ascii=True)[:256]
        self.size = size
        super().__init__('tree single file byte limit exceeded')


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
    entries = {}
    for row in output.split(b'\x00'):
        if not row:
            continue
        fields, path = row.split(b'\t', 1)
        mode, kind, object_, size = fields.split()
        count += 1
        if count > TREE_FILES:
            raise ValueError('tree file count limit exceeded')
        if len(path) > 4096:
            raise ValueError('tree path length limit exceeded')
        if kind == b'blob':
            size = int(size)
            binary = mode in (b'100644', b'100755') and blob_is_binary(str(repo), object_.decode('ascii'), size)
            entries[path] = dict(mode=mode, oid=object_.decode('ascii'), size=size, binary=binary)
            if binary:
                continue
            total += size
            if size > FILE_BYTES:
                raise TreeFileTooLarge(path, size)
            if total > TREE_BYTES:
                raise ValueError('tree total byte limit exceeded')

    return entries


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
    # Export admitted regular text blobs by object ID. Do not generate an archive:
    # omitted binaries must never expand into an unbounded temporary tar file.
    from pathlib import PurePosixPath
    entries = admit_tree(git_dir, sha)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stats = {'files': 0, 'skipped_links': 0, 'skipped_binaries': 0}
    for name, entry in entries.items():
        path = PurePosixPath(name.decode('utf-8', 'strict'))
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError('unsafe exported path')
        if entry['mode'] not in (b'100644', b'100755'):
            stats['skipped_links'] += 1
            continue
        if entry['binary']:
            stats['skipped_binaries'] += 1
            continue
        target = dest.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = git(git_dir, 'cat-file', 'blob', entry['oid'], limit=FILE_BYTES)
        if len(data) != entry['size']:
            raise ValueError('admitted Git operation failed')
        with target.open('xb') as out:
            out.write(data)
        target.chmod(0o400)
        stats['files'] += 1
    return stats
