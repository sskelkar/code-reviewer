"""Build once from pinned reviewer/OS inputs; cache only clean root-owned runtime images."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from .artifacts import Artifact
from .capsule import regular
from .runtime import MAX_INITRD_BYTES, collect

CACHE = Path('/var/lib/code-reviewer/images')
RUNTIME_INPUTS = ('/bin/busybox', '/usr/bin/python3', '/usr/bin/git', '/usr/lib/aarch64-linux-gnu',
                  '/usr/lib/python3.12', '/usr/lib/locale', '/usr/lib/ld-linux-aarch64.so.1',
                  '/etc/ssl/certs/ca-certificates.crt', '/usr/lib/git-core/git-remote-http')
WORKLOADS = {'offline': 'openai-private', 'connected': 'connected'}


def runtime_digest(inputs=RUNTIME_INPUTS):
    """Fingerprint bytes and links actually copied by the builder, not version labels."""
    digest, total, count = hashlib.sha256(), 0, 0
    for root in map(Path, inputs):
        paths = [root] + (sorted(root.rglob('*')) if root.is_dir() else [])
        for path in paths:
            info = path.lstat()
            count += 1
            if count > 100000:
                raise ValueError('runtime file inventory exceeded its limit')
            digest.update((str(path) + '\0' + oct(stat.S_IMODE(info.st_mode)) + '\0').encode())
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
                # Top-level executables can be distribution symlinks. cp follows
                # them; directory contents are copied with cp -a instead.
                if path != root:
                    continue
                digest.update(oct(stat.S_IMODE(path.stat().st_mode)).encode())
            if path.is_file():
                total += path.stat().st_size
                if total > 1024 ** 3:
                    raise ValueError('runtime inputs exceeded their byte limit')
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(65536), b''):
                        digest.update(chunk)
            elif not path.is_dir():
                raise ValueError('unexpected runtime input type')
    return digest.hexdigest()


def private_cache(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('image cache must be a private root-owned directory')


def prepare_images(source, source_sha, builder, builder_sha, *, cache=CACHE, claude=None, claude_sha=None):
    if os.geteuid() != 0:
        raise ValueError('image preparation requires root in the dedicated Linux worker')
    private_cache(cache)
    fd = os.open(cache/'.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'w') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0:
            raise ValueError('unsafe image preparation lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime_sha = runtime_digest()
        images = {}
        with tempfile.TemporaryDirectory(prefix='.inputs-', dir=cache) as name:
            inputs = Path(name)
            Artifact(Path(source), source_sha, 20*1024**2).stage(inputs/'source.tar')
            Artifact(Path(builder), builder_sha, 65536).stage(inputs/'build.sh')
            if claude is not None:
                Artifact(Path(claude), claude_sha, 300000000).stage(inputs/'claude')
            for label, workload in WORKLOADS.items():
                identity = dict(format=1, source_sha256=source_sha, builder_sha256=builder_sha,
                                runtime_sha256=runtime_sha, workload=workload)
                if claude is not None and label == 'connected':
                    identity['claude_sha256'] = claude_sha
                key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                entry = cache/key
                reused = entry.exists()
                if not reused:
                    with tempfile.TemporaryDirectory(prefix='.build-', dir=cache) as staging:
                        stage = Path(staging)
                        extra = [str(inputs/'claude'), claude_sha] if claude is not None and label == 'connected' else []
                        collect(['sh', str(inputs/'build.sh'), str(inputs/'source.tar'), str(stage/'image'), workload, *extra],
                                inputs, 180, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
                        if runtime_digest() != runtime_sha:
                            raise ValueError('OS runtime changed during image preparation; retry after updates finish')
                        image = stage/'image'
                        image_sha = hashlib.sha256(regular(image/'initrd.cpio.gz', MAX_INITRD_BYTES)).hexdigest()
                        (image/'image.json').write_text(json.dumps(dict(identity, initrd_sha256=image_sha)))
                        image.rename(entry)
                private_cache(entry)
                metadata = json.loads(regular(entry/'image.json', 65536))
                if any(metadata.get(k) != v for k,v in identity.items()):
                    raise ValueError('cached image identity mismatch')
                actual = hashlib.sha256(regular(entry/'initrd.cpio.gz', MAX_INITRD_BYTES)).hexdigest()
                if actual != metadata.get('initrd_sha256'):
                    raise ValueError('cached image digest mismatch; rebuild explicitly')
                images[label] = dict(path=str(entry), sha256=actual, reused=reused)
        return dict(source_sha256=source_sha, builder_sha256=builder_sha, runtime_sha256=runtime_sha, images=images)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('source', 'builder'):
        parser.add_argument('--'+field, type=Path, required=True)
        parser.add_argument('--'+field+'-sha256', required=True)
    parser.add_argument('--claude', type=Path)
    parser.add_argument('--claude-sha256')
    parser.add_argument('--keep-job', type=Path, required=True)
    args = parser.parse_args()
    from .recover import JOB, cleanup_stale, verify_idle
    if args.keep_job.parent != Path('/tmp') or not JOB.fullmatch(args.keep_job.name):
        raise ValueError('expected the current dedicated worker job directory')
    verify_idle()
    removed = cleanup_stale(keep=args.keep_job)
    if (args.claude is None) != (args.claude_sha256 is None):
        raise ValueError('native runtime path and digest required together')
    result = prepare_images(args.source, args.source_sha256, args.builder, args.builder_sha256,
                            claude=args.claude, claude_sha=args.claude_sha256)
    result['stale_directories_removed'] = removed
    print(json.dumps(result))


if __name__ == '__main__':
    main()
