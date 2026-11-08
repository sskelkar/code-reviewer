"""Parse target objects only inside the guest, with bounded tree admission/export."""
from __future__ import annotations

import json
from pathlib import Path

from ..shared.git_content import blob_is_binary, known_binary_path
from ..shared.snapshots import read_git as git, tree, sizes
from ..shared.models import PRContext
from .capsule import unpack

TREE_BYTES = 64 * 1024 * 1024
FILE_BYTES = 2 * 1024 * 1024
TREE_FILES = 10000


class TreeFileTooLarge(ValueError):
    def __init__(self, path, size):
        # Escaped, bounded filename metadata only; contents are never included here.
        self.label = json.dumps(path.decode('utf-8', 'replace'), ensure_ascii=True)[:256]
        self.size = size
        super().__init__('tree single file byte limit exceeded')


def admit_tree(repo, sha, *, criteria_only=False):
    entries = tree(repo, sha, max_files=TREE_FILES)
    local = sizes(repo, [e['oid'] for e in entries.values() if e['kind'] == b'blob'])
    total = 0
    for path, entry in entries.items():
        if entry['kind'] != b'blob' or (criteria_only and not path.lower().endswith(b'.md')):
            continue
        size = local[entry['oid']]
        binary = entry['mode'] in (b'100644', b'100755') and known_binary_path(path)
        if not binary:
            if size is None:
                raise ValueError('required snapshot blobs are missing')
            binary = entry['mode'] in (b'100644', b'100755') and blob_is_binary(str(repo), entry['oid'], size)
        entry.update(size=size, binary=binary)
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
