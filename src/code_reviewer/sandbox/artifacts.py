"""Hash the bytes actually staged for execution, not a path checked earlier."""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    limit: int

    def __post_init__(self):
        if (not isinstance(self.sha256, str) or len(self.sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.sha256)
                or type(self.limit) is not int or self.limit < 1):
            raise ValueError('invalid trusted artifact specification')

    def stage(self, destination: Path) -> Path:
        """Caller supplies a private operator-owned destination directory."""
        source = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        created = False
        try:
            with os.fdopen(source, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > self.limit:
                    raise ValueError('requires a bounded regular artifact without links')
                digest, size = hashlib.sha256(), 0
                target = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                created = True
                with os.fdopen(target, 'wb') as out:
                    while True:
                        chunk = handle.read(min(65536, self.limit + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.limit:
                            raise ValueError('artifact exceeded its byte limit')
                        digest.update(chunk)
                        out.write(chunk)
                if digest.hexdigest() != self.sha256:
                    raise ValueError('staged artifact digest mismatch')
            return destination
        except BaseException:
            if created:
                destination.unlink()
            raise
