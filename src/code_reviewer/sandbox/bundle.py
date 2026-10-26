"""Package only trusted reviewer sources and assets, with stable archive metadata."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any, Dict

from ..config import REPO_ROOT

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
SUFFIXES = {'.py', '.md', '.json'}


def build_bundle(root: Path, output: Path) -> Dict[str, Any]:
    source = root / 'src/code_reviewer'
    if (root / 'src').is_symlink() or source.is_symlink() or not source.is_dir():
        raise ValueError('expected a regular trusted src/code_reviewer directory')
    entries = []
    total = 0
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('source symlinks are not supported: %s' % path.relative_to(root))
        if not path.is_file() or path.suffix not in SUFFIXES or '__pycache__' in path.parts:
            continue
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError('trusted source bundle exceeds size limits')
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError('source changed while bundling')
        entries.append((path.relative_to(root).as_posix(), data))
    if not entries:
        raise ValueError('no reviewer source files found')
    manifest = {'format': 1, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries}}
    entries.append(('bundle-manifest.json', (json.dumps(manifest, sort_keys=True) + '\n').encode()))
    # Build in memory before creating output: a partial archive is never mistaken for a bundle.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    with output.open('xb') as handle:
        handle.write(buffer.getvalue())
    return {'files': len(manifest['files']), 'sha256': hashlib.sha256(buffer.getvalue()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build_bundle(REPO_ROOT, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
