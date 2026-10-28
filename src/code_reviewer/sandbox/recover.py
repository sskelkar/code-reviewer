"""Explicit recovery of one dedicated worker, never arbitrary host paths or processes."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

JOB = re.compile(r'reviewer-(?:check-[a-f0-9]{32}|job-[a-z0-9_]{8})\Z')


def cleanup_stale(root=Path('/tmp'), owner=None, keep=None):
    """Only after a fresh worker boot: do not delete around live mounts or jobs."""
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('descriptor-safe directory removal is required')
    owner = int(os.environ.get('SUDO_UID', '0')) if owner is None else owner
    entries = []
    with os.scandir(root) as directory:
        for number, entry in enumerate(directory, 1):
            if number > 10000:
                raise ValueError('temporary directory inventory is too large')
            if JOB.fullmatch(entry.name):
                entries.append(root/entry.name)
    count = 0
    for path in entries:
        if keep is not None and path == keep:
            continue
        if not JOB.fullmatch(path.name):
            continue
        info = path.lstat()
        if info.st_uid not in (0, owner):
            continue
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
            raise ValueError('unsafe stale job entry; inspect manually')
        shutil.rmtree(path)
        count += 1
    return count


def verify_idle():
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}
    units = subprocess.run(['systemctl', 'list-units', '--state=active,activating,deactivating', '--no-legend',
                            '--plain', 'reviewer-vm-*.service'], capture_output=True, text=True, timeout=15, env=env)
    if units.returncode or units.stdout.strip():
        raise ValueError('review VM services still exist; recovery refuses cleanup')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        target = line.split()[4]
        if target.startswith(('/tmp/reviewer-job-', '/tmp/reviewer-check-')):
            raise ValueError('review job mount still exists; recovery refuses cleanup')
    for cmd in (['ip', 'link', 'show', 'dev', 'cr-review0'], ['nft', 'list', 'table', 'inet', 'cr_reviewer']):
        result = subprocess.run(cmd, capture_output=True, timeout=15, env=env)
        if result.returncode != 1:
            raise ValueError('review network state remains; recovery refuses cleanup')


def run_recovery(home, instance):
    if sys.platform != 'darwin':
        raise ValueError('worker recovery currently supports the dedicated Mac/Lima deployment')
    from .check import Worker
    worker = Worker(home, instance)
    remote = '/tmp/reviewer-recovery-' + uuid.uuid4().hex + '.py'
    with worker.session(recover=True):
        try:
            worker.call('copy', Path(__file__), instance + ':' + remote)
            uid = int(worker.shell('id','-u').strip())
            if not 1 <= uid < 2**31:
                raise ValueError('expected an unprivileged worker operator')
            text = worker.shell('sudo', '-n', 'env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                                'PYTHONDONTWRITEBYTECODE=1', 'SUDO_UID='+str(uid), 'python3', remote)
            result = json.loads(text)
            if set(result) != {'removed'} or type(result['removed']) is not int or result['removed'] < 0:
                raise ValueError('invalid recovery result')
        finally:
            worker.shell('rm', '-f', '--', remote, timeout=15)
    return result['removed']


if __name__ == '__main__':
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('requires root on the dedicated Linux worker')
    verify_idle()
    print(json.dumps({'removed': cleanup_stale()}))
