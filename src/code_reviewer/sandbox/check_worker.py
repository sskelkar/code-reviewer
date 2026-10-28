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
from .connected import connect_bootstrap, run
from .runtime import MAX_INITRD_BYTES, MAX_KERNEL_BYTES
from .smoke import git
from .vm import VirtualMachine
from .wire import receive_json, send

RESULT_PREFIX = 'REVIEWER_BOUNDARY_RESULT='


def offline(root, binary_sha, kernel_sha, jailer_sha, image_dir=None, image_sha=None):
    root = Path(root)
    if not re.fullmatch(r'/tmp/reviewer-check-[a-f0-9]{32}', str(root)):
        raise ValueError('expected a private check job directory')
    initrd = (Path(image_dir) if image_dir else root/'offline') / 'initrd.cpio.gz'
    digest = image_sha or hashlib.sha256(initrd.read_bytes()).hexdigest()
    job = root / 'offline-vm'
    job.mkdir(mode=0o711)
    try:
        with VirtualMachine(job, Artifact(root/'firecracker', binary_sha, MAX_KERNEL_BYTES),
                            Artifact(root/'jailer', jailer_sha, MAX_KERNEL_BYTES),
                            Artifact(root/'kernel', kernel_sha, MAX_KERNEL_BYTES),
                            Artifact(initrd, digest, MAX_INITRD_BYTES), timeout=180, network=False, memory=1024) as vm:
            with connect_bootstrap(vm.socket, vm.proc) as sock:
                checks = vm.confinement()
                result = receive_json(sock)
                send(sock, b'', 1)
    finally:
        import shutil
        shutil.rmtree(job)
    return {'offline': result, 'vm_checks': checks, 'initrd_sha256': digest}


def check(root, binary_sha, kernel_sha, jailer_sha, image_dir=None, image_sha=None, *, source=None):
    root = Path(root)
    if not re.fullmatch(r'/tmp/reviewer-check-[a-f0-9]{32}', str(root)):
        raise ValueError('expected a private check job directory')
    capsule = None
    if source is None:
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
    initrd = (Path(image_dir) if image_dir else root/'connected') / 'initrd.cpio.gz'
    initrd_sha = image_sha or hashlib.sha256(initrd.read_bytes()).hexdigest()
    settings = dict(model='synthetic-probe', prices=[1, .5, 2], mode='single-agent',
                    passes=1, parallel=1, budget=3, call_budget=.5, probe_only=True)
    result = run(Artifact(root / 'firecracker', binary_sha, MAX_KERNEL_BYTES),
                 Artifact(root / 'kernel', kernel_sha, MAX_KERNEL_BYTES),
                 Artifact(initrd, initrd_sha, MAX_INITRD_BYTES), capsule, root / 'boundary', settings,
                 timeout=360 if source else 180, source=source,
                 jailer=Artifact(root / 'jailer', jailer_sha, MAX_KERNEL_BYTES),
                 key_reader=lambda _: (_ for _ in ()).throw(AssertionError('no key allowed')))
    # Check absence, rather than interpreting guest success as evidence of host cleanup.
    for cmd in (['/usr/sbin/ip', 'link', 'show', 'dev', 'cr-review0'],
                ['/usr/sbin/nft', 'list', 'table', 'inet', 'cr_reviewer']):
        state = subprocess.run(cmd, capture_output=True, timeout=10)
        if state.returncode != 1:
            raise RuntimeError('could not confirm removal of job network policy')
    if result['stop_reason'] != 'probes_passed':
        raise RuntimeError('boundary checks did not complete')
    checks = json.loads((root / 'boundary/boundary-report.json').read_text())
    value = {'checks': checks, 'network_cleanup': True, 'initrd_sha256': initrd_sha, 'vm_checks': result['vm_checks']}
    if source is not None:
        value['acquisition'] = result['acquisition']
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--firecracker-sha256', required=True)
    parser.add_argument('--kernel-sha256', required=True)
    parser.add_argument('--jailer-sha256', required=True)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--image-dir', type=Path)
    parser.add_argument('--initrd-sha256')
    parser.add_argument('--lifecycle', action='store_true')
    parser.add_argument('--pr')
    parser.add_argument('--abandon', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.image_dir) != bool(args.initrd_sha256):
        parser.error('--image-dir and --initrd-sha256 must be supplied together')
    if not re.fullmatch(r'/tmp/reviewer-check-[a-f0-9]{32}', str(args.root)):
        parser.error('expected the private check job directory')
    if args.abandon or args.lifecycle:
        from .lifecycle import abandoned_child, exercise
        if args.abandon:
            abandoned_child(args)
            return
        print(RESULT_PREFIX+json.dumps(exercise(args)),flush=True)
        return
    if args.pr and (args.offline or args.lifecycle or args.abandon):
        parser.error('--pr is only used for the connected no-key workload')
    kwargs = {} if args.offline else {'source': args.pr}
    result = (offline if args.offline else check)(args.root, args.firecracker_sha256, args.kernel_sha256,
                                                args.jailer_sha256, args.image_dir, args.initrd_sha256, **kwargs)
    print(RESULT_PREFIX + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
