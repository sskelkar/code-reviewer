"""Admit bounded Git objects, never repository config/hooks or host-side Git execution."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath

MAX_CAPSULE = 128 * 1024 * 1024
MAX_OBJECT = 32 * 1024 * 1024
MAX_FILES = 10000
OBJECT = re.compile(r'objects/(?:[a-f0-9]{2}/[a-f0-9]{38}|[a-f0-9]{2}/[a-f0-9]{62}|pack/pack-[a-f0-9]{40,64}\.(?:pack|idx|rev))\Z')
SHA = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')


def validate_job(value):
    if (not isinstance(value, dict) or set(value) - {'format', 'base', 'head', 'title', 'body'}
            or not {'format', 'base', 'head'} <= set(value)
            or type(value['format']) is not int or value['format'] != 1
            or not all(isinstance(value[k], str) and SHA.fullmatch(value[k]) for k in ('base', 'head'))
            or len(value['base']) != len(value['head'])):
        raise ValueError('input requires pinned full base/head hashes')
    for name, default, limit in (('title', 'Personal repository review', 1000), ('body', '', 20000)):
        text = value.get(name, default)
        if not isinstance(text, str) or len(text) > limit:
            raise ValueError('metadata text limit exceeded')
        value[name] = text
    return value


def regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError('requires a bounded regular file without links')
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('input grew beyond its limit')
    return data


def prepare(repo, base, head, output, *, title='Personal repository review', body=''):
    job = validate_job({'format': 1, 'base': base, 'head': head, 'title': title, 'body': body})
    repo = Path(repo)
    if repo.is_symlink():
        raise ValueError('repository directory must not be a link')
    git = repo / '.git'
    if not git.is_dir() or git.is_symlink():
        raise ValueError('requires a normal local clone with a regular .git directory')
    objects = git / 'objects'
    if objects.is_symlink() or not objects.is_dir():
        raise ValueError('regular Git object directory required')
    entries, total, count = [], 0, 0
    def walk(directory, prefix='objects/', depth=0):
        nonlocal count, total
        with os.scandir(directory) as files:
            for entry in files:
                count += 1
                if count > MAX_FILES:
                    raise ValueError('object entry limit exceeded')
                name = prefix + entry.name
                if entry.is_symlink():
                    raise ValueError('object links are not admitted')
                if entry.is_dir(follow_symlinks=False):
                    if depth != 0 or not re.fullmatch(r'(?:[a-f0-9]{2}|pack|info)', entry.name):
                        raise ValueError('unexpected object directory')
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        yield from walk(child, name + '/', depth + 1)
                    finally:
                        os.close(child)
                elif entry.name in ('alternates', 'http-alternates'):
                    raise ValueError('alternate object stores are not admitted')
                elif OBJECT.fullmatch(name):
                    fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(fd, 'rb') as handle:
                        info = os.fstat(handle.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_OBJECT:
                            raise ValueError('invalid object file')
                        data = handle.read(MAX_OBJECT + 1)
                    total += len(data)
                    if len(data) > MAX_OBJECT or total > MAX_CAPSULE - 4 * 1024 * 1024:
                        raise ValueError('object bytes exceed input allowance')
                    yield name, data
                elif not name.startswith('objects/info/'):
                    raise ValueError('unexpected Git object file')
    base_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        git_fd = os.open('.git', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
        try:
            directory = os.open('objects', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=git_fd)
            try:
                entries.extend(walk(directory))
            finally:
                os.close(directory)
        finally:
            os.close(git_fd)
    finally:
        os.close(base_fd)
    metadata = json.dumps(job).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in [('job.json', metadata), *entries]:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o600
            archive.addfile(member, io.BytesIO(data))
    data = buffer.getvalue()
    if len(data) > MAX_CAPSULE:
        raise ValueError('capsule too large')
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
        handle.write(data)
    return {'objects': len(entries), 'bytes': len(data)}


def unpack(data, repo):
    if len(data) > MAX_CAPSULE:
        raise ValueError('capsule too large')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        members, seen, total = [], set(), 0
        for member in archive:
            if len(members) >= MAX_FILES + 1:
                raise ValueError('too many input members')
            members.append(member)
            total += member.size
            name = member.name
            if (not member.isfile() or name in seen or member.size < 0 or member.size > MAX_OBJECT
                    or total > MAX_CAPSULE or (name != 'job.json' and not OBJECT.fullmatch(name))
                    or str(PurePosixPath(name)) != name):
                raise ValueError('invalid capsule member')
            seen.add(name)
        if 'job.json' not in seen:
            raise ValueError('missing pinned job metadata')
        member = archive.getmember('job.json')
        if member.size > 65536:
            raise ValueError('metadata too large')
        job = validate_job(json.loads(archive.extractfile(member).read()))
        for member in members:
            if member.name == 'job.json':
                continue
            path = Path(repo) / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as handle:
                handle.write(archive.extractfile(member).read())
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--title', default='Personal repository review')
    parser.add_argument('--description', default='', help='optional non-sensitive change intent')
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.base, args.head, args.output, title=args.title, body=args.description)))


if __name__ == '__main__':
    main()
