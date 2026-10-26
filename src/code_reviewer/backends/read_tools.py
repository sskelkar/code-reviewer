"""Bounded POSIX file tools: traverse directory descriptors, never invoke a shell."""
from __future__ import annotations

import fnmatch
import json
import os
import stat
from pathlib import Path, PurePosixPath

MAX_FILE = 1024 * 1024
MAX_ENTRIES = 10000
MAX_SCAN = 16 * 1024 * 1024
MAX_TEXT = 16000


def definitions(names):
    fields = {
        'Read': {'path': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 1},
                 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}},
        'Grep': {'pattern': {'type': 'string'}},
        'Glob': {'pattern': {'type': 'string'}},
    }
    descriptions = {'Read': 'Read lines from a checkout-relative regular UTF-8 file.',
                    'Grep': 'Find literal text, case-sensitive, in checkout files; not a regular expression.',
                    'Glob': 'Find checkout-relative paths using shell-style fnmatch patterns.'}
    return [{'type': 'function', 'name': name, 'description': descriptions[name], 'strict': True,
             'parameters': {'type': 'object', 'properties': fields[name],
                            'required': list(fields[name]), 'additionalProperties': False}}
            for name in names]


class ReadOnlyTools:
    def __init__(self, root: Path):
        if os.name != 'posix' or root.is_symlink():
            raise ValueError('tools require a regular POSIX checkout directory')
        self.root = root

    def _open(self, path):
        if not isinstance(path, str) or len(path) > 4096 or '\x00' in path:
            raise ValueError('invalid relative path')
        parts = PurePosixPath(path).parts
        if not parts or path.startswith('/') or any(p in ('..', '.git') for p in parts):
            raise ValueError('path outside admitted checkout')
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            os.close(fd)
            raise ValueError('requires a bounded regular file with no hardlinks')
        return fd

    def _read(self, path):
        with os.fdopen(self._open(path), 'rb') as handle:
            data = handle.read(MAX_FILE + 1)
        if len(data) > MAX_FILE or b'\x00' in data:
            raise ValueError('file exceeds text limits')
        return data.decode('utf-8')

    def _files(self):
        count = 0
        # Open each directory relative to its parent descriptor; do not follow links.
        def visit(directory, prefix, depth):
            nonlocal count
            if depth > 32:
                raise ValueError('checkout depth limit exceeded')
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_ENTRIES:
                        raise ValueError('checkout entry limit exceeded')
                    if entry.name == '.git' or entry.is_symlink():
                        continue
                    path = prefix + entry.name
                    if entry.is_dir(follow_symlinks=False):
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                        try:
                            yield from visit(child, path + '/', depth + 1)
                        finally:
                            os.close(child)
                    elif entry.is_file(follow_symlinks=False):
                        yield path
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield from visit(fd, '', 0)
        finally:
            os.close(fd)

    def execute(self, name, args):
        try:
            limited = False
            if not isinstance(args, dict):
                raise ValueError('arguments must be an object')
            if name == 'Read':
                if set(args) != {'path', 'offset', 'limit'}:
                    raise ValueError('invalid Read arguments')
                offset, limit = args['offset'], args['limit']
                if type(offset) is not int or type(limit) is not int or not 1 <= offset <= MAX_FILE or not 1 <= limit <= 200:
                    raise ValueError('invalid line bounds')
                rows = self._read(args['path']).splitlines()
                limited = len(rows) > offset - 1 + limit
                text = '\n'.join('%d: %s' % (n + 1, rows[n]) for n in range(offset - 1, min(len(rows), offset - 1 + limit)))
            elif name in ('Grep', 'Glob'):
                if set(args) != {'pattern'} or not isinstance(args['pattern'], str) or not 1 <= len(args['pattern']) <= 256:
                    raise ValueError('expected a bounded nonempty pattern')
                rows, scanned = [], 0
                for path in self._files():
                    if name == 'Glob':
                        if fnmatch.fnmatchcase(path, args['pattern']):
                            rows.append(path)
                    else:
                        try:
                            content = self._read(path)
                        except (UnicodeError, ValueError):
                            continue
                        scanned += len(content.encode('utf-8'))
                        if scanned > MAX_SCAN:
                            raise ValueError('text scan limit exceeded')
                        for n, line in enumerate(content.splitlines(), 1):
                            if args['pattern'] in line:
                                rows.append('%s:%d: %s' % (path, n, line[:1000]))
                            if len(rows) >= 100:
                                limited = True
                                break
                    if len(rows) >= 100:
                        limited = True
                        break
                text = '\n'.join(rows)
            else:
                raise ValueError('tool not allowed')
            return json.dumps({'text': text[:MAX_TEXT], 'truncated': limited or len(text) > MAX_TEXT})
        except (OSError, ValueError, UnicodeError):
            # Do not echo rejected paths, host filenames or raw OS diagnostics.
            return json.dumps({'error': 'read-only tool refused the request or reached a limit'})
