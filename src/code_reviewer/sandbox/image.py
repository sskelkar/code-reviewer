"""Construct images only after every archive stage succeeds."""
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def build_initrd(root, output, *, cpio='cpio'):
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError('image output must be new')
    with tempfile.TemporaryDirectory(dir=str(output.parent)) as name:
        stage = Path(name)
        with (stage / 'names').open('w+b') as names, (stage / 'archive').open('w+b') as archive:
            subprocess.run(['find', '.', '-print0'], cwd=str(root), stdout=names, check=True)
            names.seek(0)
            subprocess.run([cpio, '--null', '-o', '--format=newc', '--owner=0:0', '--reproducible'],
                           cwd=str(root), stdin=names, stdout=archive, check=True)
            archive.seek(0)
            with (stage / 'compressed').open('wb') as target:
                with gzip.GzipFile(fileobj=target, mode='wb', mtime=0, compresslevel=1) as compressed:
                    shutil.copyfileobj(archive, compressed)
        os.rename(str(stage / 'compressed'), str(output))


if __name__ == '__main__':
    build_initrd(*sys.argv[1:])
