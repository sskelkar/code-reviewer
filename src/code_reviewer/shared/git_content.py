"""Content-based binary omission, with bounded Git reads and deterministic reporting."""
from __future__ import annotations

import codecs
import html
import json
import os
import re
import selectors
import subprocess
import time
from functools import lru_cache

SAMPLE_BYTES = 8000
MAX_DETAILS = 200


# Filename filtering avoids transferring common binary assets. Unknown extensions
# still require a bounded content sample; SVG, JSON, docs and source remain text candidates.
BINARY_SUFFIXES = frozenset(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.ico', '.bmp', '.tif', '.tiff',
    '.pdf', '.woff', '.woff2', '.ttf', '.otf', '.eot', '.mp3', '.mp4', '.mov', '.avi', '.wav', '.ogg', '.webm',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.psd', '.icns', '.heic',
    '.zip', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.rar', '.tar', '.jar', '.war', '.class', '.pyc', '.pyo',
    '.exe', '.dll', '.so', '.dylib', '.a', '.o', '.wasm', '.bin', '.db', '.sqlite', '.sqlite3', '.parquet'))


def known_binary_path(path):
    from pathlib import PurePosixPath
    name = path.decode('utf-8', 'replace') if isinstance(path, bytes) else path
    return PurePosixPath(name).suffix.lower() in BINARY_SUFFIXES


def binary_sample(data):
    """NUL or invalid UTF-8 in the first 8000 bytes; a split final character is OK.

    This is a content heuristic, not file-format validation. UTF-16 is omitted too.
    """
    try:
        codecs.getincrementaldecoder('utf-8')().decode(data, final=False)
    except UnicodeDecodeError:
        return True
    return b'\0' in data


@lru_cache(maxsize=15000)
def blob_is_binary(repo, oid, size):
    # Object IDs, never target filenames/options. Stop reading after a fixed prefix;
    # even a highly compressed huge binary must not expand onto temporary storage.
    if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', oid) or size < 0:
        raise ValueError('invalid blob identity')
    proc = subprocess.Popen(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never',
                             '-C', str(repo), 'cat-file', 'blob', oid], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    data = bytearray()
    wanted = min(SAMPLE_BYTES, size)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 60
            while len(data) < wanted:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('Git blob sampling deadline exceeded')
                chunk = os.read(proc.stdout.fileno(), wanted - len(data))
                if not chunk:
                    raise ValueError('admitted Git operation failed')
                data.extend(chunk)
        return binary_sample(bytes(data))
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.stdout.close()
        proc.wait()


def omission_notice(ctx):
    count = len(ctx.binary_omissions)
    return ('Binary contents were not reviewed (%d file%s omitted). See the report for metadata.' %
            (count, '' if count == 1 else 's')) if count else ''


def omission_section(ctx):
    if not ctx.binary_omissions:
        return ''
    lines = ['\n## Binary contents omitted\n', omission_notice(ctx),
             'Known binary suffixes are filtered before transfer; other binary contents are detected from an 8000-byte prefix. Both are heuristics. '
             'Sizes refer to Git blobs. Unchanged binaries are listed for checkout completeness.\n']
    for item in ctx.binary_omissions[:MAX_DETAILS]:
        path = html.escape(json.dumps(item['path'], ensure_ascii=True)[:256], quote=False)
        lines.append('- <code>%s</code>: %s; base %s bytes, head %s bytes.' %
                     (path, item['status'], item['base_bytes'] if item['base_bytes'] is not None else ('absent' if item['status'] == 'added' else 'unknown (not downloaded)'),
                      item['head_bytes'] if item['head_bytes'] is not None else ('absent' if item['status'] == 'deleted' else 'unknown (not downloaded)')))
    if len(ctx.binary_omissions) > MAX_DETAILS:
        lines.append('- Detail list capped at %d; %d additional omissions. Full metadata is in stats.json.' %
                     (MAX_DETAILS, len(ctx.binary_omissions) - MAX_DETAILS))
    return '\n'.join(lines) + '\n'
