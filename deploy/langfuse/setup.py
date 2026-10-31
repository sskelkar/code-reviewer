"""Generate local-only Langfuse secrets outside Git; never print their values."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


def main():
    root = Path.home() / '.code-reviewer' / 'langfuse'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError('Langfuse directory must be private and operator-owned')
    if any((parent / '.git').exists() for parent in (root.resolve(), *root.resolve().parents)):
        raise ValueError('Langfuse settings must be outside Git working trees')
    path = root / 'local.env'
    values = {key: secrets.token_hex(32) for key in ('POSTGRES_PASSWORD', 'CLICKHOUSE_PASSWORD', 'REDIS_AUTH',
              'MINIO_ROOT_PASSWORD', 'SALT', 'ENCRYPTION_KEY', 'NEXTAUTH_SECRET', 'LANGFUSE_USER_PASSWORD')}
    values.update(LANGFUSE_PUBLIC_KEY='pk-lf-' + secrets.token_hex(16), LANGFUSE_SECRET_KEY='sk-lf-' + secrets.token_hex(32))
    values['LANGFUSE_USER_EMAIL'] = 'reviewer@localhost.invalid'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                    or info.st_nlink != 1 or info.st_size > 65536):
                raise ValueError('existing local.env must be a private, bounded operator-owned regular file')
        print('Existing local.env preserved; no keys or passwords changed.')
        return
    with os.fdopen(fd, 'w') as handle:
        handle.write(''.join('%s=%s\n' % pair for pair in values.items()))
    print('Created private Langfuse deployment settings: ' + str(path))
    print('Local login: reviewer@localhost.invalid; password is LANGFUSE_USER_PASSWORD in that file.')


if __name__ == '__main__':
    main()
