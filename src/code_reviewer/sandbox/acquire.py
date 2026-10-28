"""Public GitHub acquisition in the guest; no host Git, credentials or target settings."""
from __future__ import annotations

import http.client
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..shared.models import PRContext
from .git_input import admit_tree, bounded_export, git
from .network import PROXY

META_BYTES = 512 * 1024
GIT_BYTES = 128 * 1024 * 1024
GIT_FILES = 10000
DEPTH = 256


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


def metadata(url):
    repo, number = parse_pr(url)
    connection = http.client.HTTPSConnection(*PROXY, timeout=20)
    try:
        connection.set_tunnel('api.github.com', 443)
        connection.request('GET', '/repos/%s/pulls/%d' % (repo, number), headers={
            'Accept': 'application/vnd.github+json', 'User-Agent': 'code-reviewer',
            'X-GitHub-Api-Version': '2022-11-28'})
        response = connection.getresponse()
        if response.status != 200:  # Redirects, authentication and rate limits fail closed.
            raise ValueError('public PR metadata unavailable')
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


def fetch(root, repo, base, head):
    # Configuration and URL are code-owned; no remote helpers, hooks, submodules,
    # credentials, redirects or target-chosen protocol/branch names are admitted.
    options = ['core.hooksPath=/dev/null', 'core.fsmonitor=false', 'credential.helper=',
               'protocol.allow=never', 'protocol.https.allow=always', 'http.followRedirects=false',
               'http.sslVerify=true', 'http.sslCAInfo=/etc/ssl/certs/ca-certificates.crt',
               'http.proxy=http://%s:%d' % PROXY, 'http.maxRequests=1',
               'pack.threads=1', 'gc.auto=0', 'maintenance.auto=false', 'fetch.fsckObjects=true']
    argv = ['git']
    for option in options:
        argv.extend(['-c', option])
    argv.extend(['-C', str(root), 'fetch', '--quiet', '--no-progress', '--no-tags',
                 '--no-recurse-submodules', '--depth=%d' % DEPTH,
                 'https://github.com/%s.git' % repo, base, head])
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 120
        while proc.poll() is None:
            inventory(root)
            if time.monotonic() >= deadline:
                raise TimeoutError('public Git acquisition deadline exceeded')
            time.sleep(.1)
        if proc.returncode:
            raise ValueError('pinned commits unavailable within acquisition limits')
        inventory(root)
    finally:
        # The HTTPS transport helper must not outlive either success or failure.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def acquire(url, root):
    repo, number, base, head, title, body = describe(metadata(url), url)
    root = Path(root)
    root.mkdir(mode=0o700)
    templates = root.parent / 'empty-templates'
    templates.mkdir(mode=0o700)
    git(root, 'init', '--bare', '--quiet', '--template=' + str(templates))
    fetch(root, repo, base, head)
    for sha in (base, head):
        if git(root, 'rev-parse', '--verify', sha + '^{commit}').decode().strip() != sha:
            raise ValueError('fetched hash is not the pinned commit')
        admit_tree(root, sha)
    merge_base = git(root, 'merge-base', base, head).decode().strip()
    pin(dict(repo=repo, number=number, base=base, head=head, merge_base=merge_base), url)
    admit_tree(root, merge_base)
    # All parsing/export runs unprivileged, before the setup parent seals mounts.
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
        proc = subprocess.run(['/usr/bin/python3', '-m', 'code_reviewer.sandbox.acquire', str(root)],
                              input=url.encode(), stdout=out, stderr=subprocess.DEVNULL, timeout=180,
                              env=env, preexec_fn=drop_privileges)
        if proc.returncode or out.tell() > 65536:
            raise ValueError('isolated public acquisition failed')
        out.seek(0)
        return context(json.loads(out.read(65537)), url, root)


def main():
    if os.geteuid() != 65534 or len(sys.argv) != 2:
        raise ValueError('acquisition only runs as the unprivileged guest user')
    url = sys.stdin.buffer.read(257).decode('ascii')
    print(json.dumps(acquire(url, Path(sys.argv[1]))))


if __name__ == '__main__':
    main()
