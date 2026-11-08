"""Pinned snapshot objects and selective blob materialization; no history traversal."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import PurePosixPath

from .git_content import known_binary_path


def read_git(repo, *args, limit=2 * 1024 * 1024, input=None):
    # Never let a missing object initiate a promisor/lazy network fetch during reads.
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0')
    with tempfile.TemporaryFile() as out:
        proc = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                               '-c', 'protocol.allow=never', '-C', str(repo), *args],
                              stdout=out, stderr=subprocess.DEVNULL, timeout=60, env=env,
                              **({'stdin': subprocess.DEVNULL} if input is None else {'input': input}))
        if proc.returncode:
            raise ValueError('admitted Git operation failed')
        if out.tell() > limit:
            raise ValueError('Git output limit exceeded')
        out.seek(0)
        return out.read(limit + 1)


def tree(repo, sha, *, max_files=None):
    entries = {}
    for row in read_git(repo, 'ls-tree', '-rz', sha).split(b'\0'):
        if not row:
            continue
        fields, name = row.split(b'\t', 1)
        mode, kind, oid = fields.split()
        path = PurePosixPath(name.decode('utf-8', 'strict'))
        if path.is_absolute() or '..' in path.parts or not path.parts or len(name) > 4096:
            raise ValueError('tree path length limit exceeded')
        if not re.fullmatch(b'[a-f0-9]{40}|[a-f0-9]{64}', oid):
            raise ValueError('invalid Git object identity')
        entries[name] = dict(mode=mode, kind=kind, oid=oid.decode('ascii'))
        if max_files is not None and len(entries) > max_files:
            raise ValueError('tree file count limit exceeded')
    return entries


def sizes(repo, oids):
    """Local sizes only; missing omitted blobs have unknown size, not a network lookup."""
    oids = sorted(set(oids))
    if not oids:
        return {}
    output = read_git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)',
                      input=('\n'.join(oids) + '\n').encode())
    result = {}
    rows = output.splitlines()
    if len(rows) != len(oids):
        raise ValueError('invalid local object inventory')
    for expected, row in zip(oids, rows):
        parts = row.decode('ascii').split()
        if parts == [expected, 'missing']:
            result[expected] = None
        elif len(parts) == 3 and parts[:2] == [expected, 'blob'] and parts[2].isdigit():
            result[expected] = int(parts[2])
        else:
            raise ValueError('invalid local object inventory')
    return result


def materialize(repo, commits, fetch_blobs, *, max_files=None, criteria_only=None):
    """Fetch only snapshot text candidates, including docs/tests and symlink metadata."""
    wanted = set()
    for sha in dict.fromkeys(commits):
        if read_git(repo, 'rev-parse', '--verify', sha + '^{commit}').decode().strip() != sha:
            raise ValueError('fetched hash is not the pinned commit')
        for name, entry in tree(repo, sha, max_files=max_files).items():
            if sha == criteria_only and not name.lower().endswith(b'.md'):
                continue
            if entry['kind'] == b'blob' and not (entry['mode'] in (b'100644', b'100755') and known_binary_path(name)):
                wanted.add(entry['oid'])
    local = sizes(repo, wanted)
    missing = [oid for oid in sorted(wanted) if local[oid] is None]
    for start in range(0, len(missing), 512):
        fetch_blobs(missing[start:start + 512])
    if any(value is None for value in sizes(repo, wanted).values()):
        raise ValueError('required snapshot blobs are missing')


def comparison_base(value, base, head):
    """GitHub computes ancestry for exactly the requested immutable comparison."""
    if (not all(isinstance(x, str) and re.fullmatch(r'[a-f0-9]{40}', x) for x in (base, head))
            or not isinstance(value, dict) or not isinstance(value.get('base_commit'), dict)
            or value['base_commit'].get('sha') != base or not isinstance(value.get('merge_base_commit'), dict)):
        raise ValueError('invalid pinned comparison metadata')
    merge = value['merge_base_commit'].get('sha')
    if not isinstance(merge, str) or not re.fullmatch(r'[a-f0-9]{40}', merge):
        raise ValueError('invalid pinned comparison metadata')
    return merge


def fetch_objects(repo, url, oids, *, shallow=False, options=(), monitor=lambda: None, timeout=120):
    """One bounded filtered transfer; only code-owned URLs/options reach this helper."""
    import signal
    import time
    oids = list(dict.fromkeys(oids))
    if not oids or any(not isinstance(x, str) or not re.fullmatch(r'[a-f0-9]{40}', x) for x in oids):
        raise ValueError('invalid snapshot fetch identities')
    settings = ('core.hooksPath=/dev/null', 'core.fsmonitor=false', 'protocol.allow=never',
                'protocol.https.allow=always', 'protocol.version=2', 'http.followRedirects=false',
                'pack.threads=1', 'gc.auto=0', 'maintenance.auto=false', 'fetch.fsckObjects=true', *options)
    argv = ['git']
    for option in settings:
        argv.extend(['-c', option])
    argv.extend(['-C', str(repo), 'fetch', '--quiet', '--no-progress', '--no-tags',
                 '--no-recurse-submodules', '--no-write-fetch-head', '--filter=blob:none', '--stdin'])
    if shallow:
        argv.append('--depth=1')
    argv.append(url)
    with tempfile.TemporaryFile() as refs, tempfile.TemporaryFile() as errors:
        refs.write(('\n'.join(oids) + '\n').encode())
        refs.seek(0)
        proc = subprocess.Popen(argv, stdin=refs, stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
        try:
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                monitor()
                if errors.tell() > 8192:
                    raise ValueError('snapshot fetch diagnostics exceeded their limit')
                if time.monotonic() >= deadline:
                    raise TimeoutError('public Git acquisition deadline exceeded')
                time.sleep(.1)
            monitor()
            errors.seek(0)
            message = errors.read(8193)
            if b'filtering not recognized' in message or b'does not support filter' in message:
                raise ValueError('Git server did not honor snapshot filtering')
            if proc.returncode or len(message) > 8192:
                raise ValueError('pinned snapshot objects unavailable')
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            # git fetch records a promisor remote even for a literal URL. Remove
            # it so missing omitted objects cannot initiate a later lazy fetch.
            subprocess.run(['git', '-C', str(repo), 'config', '--local', '--remove-section', 'remote.' + url],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
