"""Public GitHub acquisition in the guest; no host Git, credentials or target settings."""
from __future__ import annotations

import http.client
import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..config import ConfigError
from ..shared.models import PRContext
from .git_input import TREE_BYTES, TREE_FILES, TreeFileTooLarge, admit_tree, bounded_export, git
from .network import PROXY
from ..shared.snapshots import comparison_base, fetch_objects, materialize

META_BYTES = 512 * 1024
GIT_BYTES = 128 * 1024 * 1024
GIT_FILES = 10000


class MetadataUnavailable(ConfigError):
    """Only a numeric public-source HTTP status may cross the private failure channel."""
    def __init__(self, status):
        if type(status) is not int or not 300 <= status <= 599:
            raise ValueError('invalid metadata HTTP status')
        self.status = status
        super().__init__('GitHub public PR metadata returned HTTP %d; check that the PR exists and is publicly accessible, '
                         'and check unauthenticated API rate limits. Private source authentication is unsupported in VM mode.' % status)

    def frame(self):
        return {'kind': 'failed', 'code': 'github_metadata_http', 'http_status': self.status}


SOURCE_REASONS = {
    'git': 'Git could not read/init the admitted repository or find its merge base',
    'fetch': 'pinned snapshot objects could not be fetched',
    'filter': 'the Git server did not honor filtered snapshot acquisition',
    'git-bytes': 'snapshot Git storage exceeded the 128 MiB limit',
    'git-files': 'snapshot Git storage exceeded the 10,000-entry limit',
    'comparison': 'GitHub comparison metadata did not identify the pinned merge base',
    'objects': 'required snapshot text objects are missing',
    'tree': 'the repository exceeded archive/export limits',
    'file-bytes': 'a repository text file exceeds the current 2 MiB per-file admission limit',
    'tree-bytes': f'a repository text tree exceeds the current {TREE_BYTES // (1024 * 1024)} MiB total admission limit',
    'tree-count': f'a repository tree exceeds the current {TREE_FILES:,}-file admission limit',
    'tree-path': 'a repository path exceeds the current 4096-byte path limit',
    'metadata': 'public PR metadata did not satisfy the requested repository/text limits',
    'permission': 'guest filesystem permissions prevented public acquisition',
    'missing-file': 'a required guest file or executable is missing',
    'timeout': 'public acquisition exceeded its deadline',
    'unexpected': 'public acquisition failed before admission completed',
}


class AcquisitionUnavailable(ConfigError):
    def __init__(self, reason, file=None, size=None):
        if not isinstance(reason, str) or reason not in SOURCE_REASONS:
            raise ValueError('invalid acquisition failure code')
        self.reason, self.file, self.size = reason, file, size
        if file is not None or size is not None:
            if (reason != 'file-bytes' or not isinstance(file, str) or not 1 <= len(file) <= 256
                    or any(ord(c) < 32 or ord(c) > 126 for c in file)
                    or type(size) is not int or not 0 < size < 2**63):
                raise ValueError('invalid bounded file metadata')
        detail = ('; file %s (%d bytes)' % (file, size)) if file is not None else ''
        super().__init__('VM public acquisition failed: ' + SOURCE_REASONS[reason] + detail + '; no model credential was released')

    def frame(self):
        value = {'kind': 'failed', 'code': 'public_acquisition', 'reason': self.reason}
        if self.file is not None:
            value.update(file=self.file, bytes=self.size)
        return value


def acquisition_error(exc):
    if isinstance(exc, TreeFileTooLarge):
        return AcquisitionUnavailable('file-bytes', exc.label, exc.size)
    if isinstance(exc, subprocess.TimeoutExpired) or isinstance(exc, TimeoutError):
        return AcquisitionUnavailable('timeout')
    if isinstance(exc, OSError):
        if exc.errno in (errno.EACCES, errno.EPERM):
            return AcquisitionUnavailable('permission')
        if exc.errno == errno.ENOENT:
            return AcquisitionUnavailable('missing-file')
    # Only code-owned messages are mapped; arbitrary exception/server text is never exported.
    reasons = {
        'admitted Git operation failed': 'git', 'Git output limit exceeded': 'git',
        'pinned commits unavailable within acquisition limits': 'fetch',
        'public Git acquisition deadline exceeded': 'timeout',
        'acquired Git bytes exceeded their limit': 'git-bytes', 'acquired Git file count exceeded its limit': 'git-files',
        'Git server did not honor snapshot filtering': 'filter', 'pinned snapshot objects unavailable': 'fetch',
        'invalid pinned comparison metadata': 'comparison', 'required snapshot blobs are missing': 'objects',
        'tree single file byte limit exceeded': 'file-bytes', 'tree total byte limit exceeded': 'tree-bytes',
        'tree file count limit exceeded': 'tree-count', 'tree path length limit exceeded': 'tree-path',
        'archive generation failed or exceeded limits': 'tree', 'export byte/count limit exceeded': 'tree',
        'invalid PR metadata': 'metadata', 'only the requested public repository is supported': 'metadata',
        'invalid PR commit hash': 'metadata', 'PR text exceeded its limit': 'metadata',
        'PR metadata exceeded its limit': 'metadata',
    }
    if isinstance(exc, ValueError) and str(exc) in reasons:
        return AcquisitionUnavailable(reasons[str(exc)])
    return AcquisitionUnavailable('unexpected')


def source_failure(value):
    if isinstance(value, dict) and value.get('kind') == 'failed' and value.get('code') == 'github_metadata_http':
        if set(value) != {'kind', 'code', 'http_status'}:
            raise ValueError('invalid metadata failure frame')
        return MetadataUnavailable(value['http_status'])
    if isinstance(value, dict) and value.get('kind') == 'failed' and value.get('code') == 'public_acquisition':
        if set(value) not in ({'kind', 'code', 'reason'}, {'kind', 'code', 'reason', 'file', 'bytes'}):
            raise ValueError('invalid acquisition failure frame')
        return AcquisitionUnavailable(value['reason'], value.get('file'), value.get('bytes'))
    return None


def parse_pr(url):
    if not isinstance(url, str) or len(url) > 256:
        raise ValueError('expected a public GitHub PR URL')
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/'
                         r'([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})(?:/changes)?/?', url)
    if not match or match[2] in ('.', '..'):
        raise ValueError('expected a public GitHub PR URL')
    return match[1] + '/' + match[2], int(match[3])


def pin(value, url):
    repo, number = parse_pr(url)
    keys = {'repo', 'number', 'base', 'head', 'merge_base'}
    if (not isinstance(value, dict) or set(value) != keys or value['repo'] != repo
            or type(value['number']) is not int or value['number'] != number
            or any(not isinstance(value[k], str) or not re.fullmatch('[a-f0-9]{40}', value[k])
                   for k in ('base', 'head', 'merge_base'))):
        raise ValueError('invalid acquired commit identity')
    return value


def metadata(url, *, compare=None):
    repo, number = parse_pr(url)
    path = '/repos/%s/pulls/%d' % (repo, number)
    if compare is not None:
        if (not isinstance(compare, tuple) or len(compare) != 2 or
                any(not isinstance(x, str) or not re.fullmatch('[a-f0-9]{40}', x) for x in compare)):
            raise ValueError('invalid pinned comparison metadata')
        path = '/repos/%s/compare/%s...%s?per_page=1&page=2' % (repo, *compare)
    connection = http.client.HTTPSConnection(*PROXY, timeout=20)
    try:
        connection.set_tunnel('api.github.com', 443)
        connection.request('GET', path, headers={
            'Accept': 'application/vnd.github+json', 'User-Agent': 'code-reviewer',
            'X-GitHub-Api-Version': '2022-11-28'})
        response = connection.getresponse()
        if response.status != 200:  # Redirects, authentication and rate limits fail closed.
            raise MetadataUnavailable(response.status)
        data = response.read(META_BYTES + 1)
        if len(data) > META_BYTES:
            raise ValueError('PR metadata exceeded its limit')
        return json.loads(data)
    finally:
        connection.close()


def describe(value, url):
    repo, number = parse_pr(url)
    if (not isinstance(value, dict) or type(value.get('number')) is not int or value['number'] != number
            or not isinstance(value.get('base'), dict) or not isinstance(value.get('head'), dict)):
        raise ValueError('invalid PR metadata')
    base_repo = value['base'].get('repo')
    if (not isinstance(base_repo, dict) or base_repo.get('full_name', '').lower() != repo.lower()
            or base_repo.get('private') is not False):
        raise ValueError('only the requested public repository is supported')
    for key in ('base', 'head'):
        sha = value[key].get('sha')
        if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{40}', sha):
            raise ValueError('invalid PR commit hash')
    title, body = value.get('title'), value.get('body') or ''
    if not isinstance(title, str) or len(title) > 1024 or not isinstance(body, str) or len(body) > 32768:
        raise ValueError('PR text exceeded its limit')
    return repo, number, value['base']['sha'], value['head']['sha'], title, body


def inventory(root):
    total = count = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        count += len(dirs) + len(files)
        if count > GIT_FILES:
            raise ValueError('acquired Git file count exceeded its limit')
        for name in files:
            total += (Path(folder) / name).lstat().st_size
            if total > GIT_BYTES:
                raise ValueError('acquired Git bytes exceeded their limit')


def fetch(root, repo, oids, *, shallow=False, timeout=120):
    options = ('credential.helper=', 'http.sslVerify=true',
               'http.sslCAInfo=/etc/ssl/certs/ca-certificates.crt',
               'http.proxy=http://%s:%d' % PROXY, 'http.maxRequests=1')
    fetch_objects(root, 'https://github.com/%s.git' % repo, oids, shallow=shallow,
                  options=options, monitor=lambda: inventory(root), timeout=timeout)


def acquire(url, root):
    repo, number, base, head, title, body = describe(metadata(url), url)
    merge_base = comparison_base(metadata(url, compare=(base, head)), base, head)
    root = Path(root)
    root.mkdir(mode=0o700)
    git(root, 'init', '--bare', '--quiet', '--template=')
    deadline = time.monotonic() + 120
    def transfer(oids, *, shallow=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('public Git acquisition deadline exceeded')
        fetch(root, repo, oids, shallow=shallow, timeout=remaining)
    commits = list(dict.fromkeys((base, head, merge_base)))
    transfer(commits, shallow=True)
    criteria_only = base if base not in (head, merge_base) else None
    materialize(root, commits, transfer, max_files=TREE_FILES, criteria_only=criteria_only)
    for sha in commits:
        admit_tree(root, sha, criteria_only=sha == criteria_only)
    pin(dict(repo=repo, number=number, base=base, head=head, merge_base=merge_base), url)
    bounded_export(root, head, root.parent / 'snapshot')
    return dict(repo=repo, number=number, base=base, head=head, merge_base=merge_base, title=title, body=body)


def context(value, url, root):
    if not isinstance(value, dict) or set(value) != {'repo', 'number', 'base', 'head', 'merge_base', 'title', 'body'}:
        raise ValueError('invalid acquisition reply')
    pin({k: value[k] for k in ('repo', 'number', 'base', 'head', 'merge_base')}, url)
    if (not isinstance(value['title'], str) or len(value['title']) > 1024
            or not isinstance(value['body'], str) or len(value['body']) > 32768):
        raise ValueError('invalid acquisition text')
    return PRContext(repo=value['repo'], number=value['number'], url=url, title=value['title'], body=value['body'],
                     commit_messages='', base_ref='pinned-base', base_sha=value['base'], head_sha=value['head'],
                     merge_base=value['merge_base'], git_dir=Path(root))


def in_guest(url, root):
    parse_pr(url)
    from .snapshot import drop_privileges
    # Never parse target Git/API bytes with the initial guest setup privileges.
    env = dict(PATH='/usr/bin:/bin', HOME='/work/home', TMPDIR='/work', LANG='C.UTF-8',
               PYTHONPATH='/opt/reviewer/src', PYTHONDONTWRITEBYTECODE='1', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
               GIT_EXEC_PATH='/usr/lib/git-core', SSL_CERT_FILE='/etc/ssl/certs/ca-certificates.crt')
    with tempfile.TemporaryFile() as out:
        proc = subprocess.run(['/usr/local/bin/python3.13', '-m', 'code_reviewer.sandbox.acquire', str(root)],
                              input=url.encode(), stdout=out, stderr=subprocess.DEVNULL, timeout=180,
                              env=env, preexec_fn=drop_privileges)
        if proc.returncode or out.tell() > 65536:
            raise ValueError('isolated public acquisition failed')
        out.seek(0)
        value = json.loads(out.read(65537))
        failure = source_failure(value)
        if failure:
            raise failure
        return context(value, url, root)


def main():
    if os.geteuid() != 65534 or len(sys.argv) != 2:
        raise ValueError('acquisition only runs as the unprivileged guest user')
    url = sys.stdin.buffer.read(257).decode('ascii')
    try:
        value = acquire(url, Path(sys.argv[1]))
    except MetadataUnavailable as exc:
        value = exc.frame()
    except Exception as exc:
        value = acquisition_error(exc).frame()
    print(json.dumps(value))


if __name__ == '__main__':
    main()
