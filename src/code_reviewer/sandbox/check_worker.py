"""No-key connected check using only a generated fixture on the dedicated Linux worker."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from .artifacts import Artifact
from .capsule import prepare
from .connected import run
from .launch import MAX_INITRD_BYTES, MAX_KERNEL_BYTES
from .smoke import git

RESULT_PREFIX = 'REVIEWER_BOUNDARY_RESULT='


def check(root, binary_sha, kernel_sha):
    root = Path(root)
    if not re.fullmatch(r'/tmp/reviewer-check-[a-f0-9]{32}', str(root)):
        raise ValueError('expected a private check job directory')
    repo = root / 'fixture'
    repo.mkdir(mode=0o700)
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'app.py').write_text('def value():\n    return 1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'Synthetic base')
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'app.py').write_text('def value():\n    return 2\n')
    git(repo, 'commit', '-qam', 'Synthetic head')
    head = git(repo, 'rev-parse', 'HEAD')
    capsule = root / 'fixture.tar'
    prepare(repo, base, head, capsule, title='Sandbox boundary check')
    initrd = root / 'connected/initrd.cpio.gz'
    initrd_sha = hashlib.sha256(initrd.read_bytes()).hexdigest()
    settings = dict(model='synthetic-probe', prices=[1, .5, 2], mode='single-agent',
                    passes=1, parallel=1, budget=3, call_budget=.5, probe_only=True)
    result = run(Artifact(root / 'firecracker', binary_sha, MAX_KERNEL_BYTES),
                 Artifact(root / 'kernel', kernel_sha, MAX_KERNEL_BYTES),
                 Artifact(initrd, initrd_sha, MAX_INITRD_BYTES), capsule, root / 'boundary', settings,
                 timeout=180, key_reader=lambda _: (_ for _ in ()).throw(AssertionError('no key allowed')))
    # Check absence, rather than interpreting guest success as evidence of host cleanup.
    for cmd in (['/usr/sbin/ip', 'link', 'show', 'dev', 'cr-review0'],
                ['/usr/sbin/nft', 'list', 'table', 'inet', 'cr_reviewer']):
        state = subprocess.run(cmd, capture_output=True, timeout=10)
        if state.returncode != 1:
            raise RuntimeError('could not confirm removal of job network policy')
    if result['stop_reason'] != 'probes_passed':
        raise RuntimeError('boundary checks did not complete')
    checks = json.loads((root / 'boundary/boundary-report.json').read_text())
    return {'checks': checks, 'network_cleanup': True, 'initrd_sha256': initrd_sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--firecracker-sha256', required=True)
    parser.add_argument('--kernel-sha256', required=True)
    args = parser.parse_args()
    print(RESULT_PREFIX + json.dumps(check(args.root, args.firecracker_sha256, args.kernel_sha256)), flush=True)


if __name__ == '__main__':
    main()
