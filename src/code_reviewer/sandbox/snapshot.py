"""Seal admitted input before dropping guest privileges; no privileged model helpers."""
from __future__ import annotations

import ctypes
import errno
import os
import subprocess
from pathlib import Path

from .git_input import bounded_export

CHECKS = {'non_root', 'no_new_privileges', 'readonly_mount', 'write_denied', 'chmod_denied', 'runtime_write_denied'}

def readonly(path):
    for arguments in (['--bind', str(path), str(path)],
                      ['-o', 'remount,bind,ro,nosuid,nodev,noexec', str(path)]):
        subprocess.run(['/bin/busybox', 'mount', *arguments], check=True, timeout=10,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env={'PATH': '/usr/bin:/bin'})
    if not os.statvfs(path).f_flag & os.ST_RDONLY:
        raise ValueError('input mount did not become read-only')


def prepare(ctx, root, *, exported=False):
    if os.geteuid() != 0:
        raise ValueError('input sealing requires the initial guest setup process')
    path = root / 'snapshot'
    if not exported:
        bounded_export(ctx.git_dir, ctx.head_sha, path)
        for entry in path.rglob('*'):
            entry.chmod(0o555 if entry.is_dir() else 0o444)
        path.chmod(0o555)
    readonly(path)
    readonly(ctx.git_dir)
    # Only this admitted, read-only bare repository is trusted by subsequent Git
    # reads. Never disable safe-directory checking globally with a wildcard.
    os.environ.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=str(ctx.git_dir))
    drop_privileges()
    return path


def drop_privileges():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS, inherited by all later helpers
        raise OSError(ctypes.get_errno(), 'could not disable guest privilege escalation')
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)


def probes(path):
    status = Path('/proc/self/status').read_text()
    result = {'non_root': os.geteuid() == 65534, 'no_new_privileges': '\nNoNewPrivs:\t1\n' in status,
              'readonly_mount': bool(os.statvfs(path).f_flag & os.ST_RDONLY)}
    # Actual native operations, not requests to the model's file-tool dispatcher.
    sample = next(p for p in path.rglob('*') if p.is_file())
    operations = {'write_denied': lambda: os.open(path / '.write-probe', os.O_WRONLY | os.O_CREAT, 0o600),
                  'chmod_denied': lambda: os.chmod(sample, 0o600),
                  'runtime_write_denied': lambda: os.open(Path(__file__), os.O_WRONLY)}
    for name, operation in operations.items():
        try:
            fd = operation()
        except OSError as exc:
            result[name] = exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS)
        else:
            if isinstance(fd, int):
                os.close(fd)
            result[name] = False
    if not all(result.values()):
        raise ValueError('native guest isolation probes failed')
    return result
