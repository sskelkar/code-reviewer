"""Git and GitHub CLI operations. Everything here is read-only with respect to the user's working tree."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .git_content import binary_sample, blob_is_binary, known_binary_path, SAMPLE_BYTES
from .snapshots import read_git, tree, sizes
from ..config import DATA_DIR


class GitError(RuntimeError):
    """Raised when a git or gh command fails."""


class GitContentTooLarge(GitError):
    """An immutable Git blob exceeds the requested read allowance."""


@dataclass(frozen=True)
class PRRef:
    host: str
    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return "%s/%s" % (self.owner, self.repo)

    @property
    def gh_repo(self) -> str:
        return self.slug if self.host == "github.com" else "%s/%s" % (self.host, self.slug)

    def __str__(self) -> str:
        return "%s#%d" % (self.slug, self.number)


@dataclass(frozen=True)
class LocalRepo:
    toplevel: Path
    host: str
    owner: str
    repo: str


_PR_URL = re.compile(r"^https?://([^/\s]+)/([^/\s]+)/([^/\s]+)/pull/(\d+)(?:[/?#].*)?$")
_PR_SHORT = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)$")
_REMOTE_PATTERNS = [
    re.compile(r"^git@([^:]+):([^/]+)/(.+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://git@([^/]+)/([^/]+)/(.+?)(?:\.git)?/?$"),
    re.compile(r"^https?://(?:[^@/]+@)?([^/]+)/([^/]+)/(.+?)(?:\.git)?/?$"),
]


def parse_pr_ref(text: str, default_host: str = "github.com") -> PRRef:
    text = text.strip()
    match = _PR_URL.match(text)
    if match:
        return PRRef(match.group(1), match.group(2), match.group(3), int(match.group(4)))
    match = _PR_SHORT.match(text)
    if match:
        return PRRef(default_host, match.group(1), match.group(2), int(match.group(3)))
    raise GitError("not a pull request URL or owner/repo#number: %s" % text)


def parse_remote_url(url: str) -> Optional[Tuple[str, str, str]]:
    for pattern in _REMOTE_PATTERNS:
        match = pattern.match(url.strip())
        if match:
            return match.group(1), match.group(2), match.group(3)
    return None


def _run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitError("command not found: %s" % cmd[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("command timed out: %s" % " ".join(cmd[:4])) from exc
    if check and proc.returncode != 0:
        raise GitError("%s failed: %s" % (" ".join(cmd[:4]), (proc.stderr or proc.stdout).strip()[-500:]))
    return proc


def local_repo_info(path: Optional[Path] = None) -> Optional[LocalRepo]:
    """Describe the git repository containing `path` (default: cwd), if it has a parseable origin."""
    cwd = Path(path) if path else Path.cwd()
    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, check=False)
    if top.returncode != 0:
        return None
    remote = _run(["git", "remote", "get-url", "origin"], cwd=cwd, check=False)
    if remote.returncode != 0:
        return None
    parsed = parse_remote_url(remote.stdout.strip())
    if not parsed:
        return None
    return LocalRepo(Path(top.stdout.strip()), parsed[0], parsed[1], parsed[2])


def same_repo(local: LocalRepo, ref: PRRef) -> bool:
    return (local.host.lower(), local.owner.lower(), local.repo.lower()) == (
        ref.host.lower(), ref.owner.lower(), ref.repo.lower())


def gh_pr_view(ref: PRRef) -> Dict[str, object]:
    proc = _run([
        "gh", "pr", "view", str(ref.number), "-R", ref.gh_repo, "--json",
        "title,body,baseRefName,baseRefOid,headRefName,headRefOid,url,state,author,isCrossRepository",
    ], timeout=120)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise GitError("could not parse gh pr view output") from exc


def ensure_snapshot_store(ref: PRRef, cache_dir: Optional[Path] = None) -> Path:
    """Private object cache, initialized empty rather than cloning repository history."""
    cache = (cache_dir or DATA_DIR / "cache")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = cache / ("%s__%s__%s.git" % (ref.host, ref.owner, ref.repo))
    if not path.exists():
        path.mkdir(mode=0o700)
        _run(['git', 'init', '--bare', '--quiet', '--template=', str(path)])
    return path


def source_transport(ref: PRRef, local: Optional[LocalRepo] = None):
    """Respect operator Git transport preferences without copying credentials into URLs."""
    protocol = None
    if local is not None:
        origin = _run(['git', '-C', str(local.toplevel), 'remote', 'get-url', 'origin']).stdout.strip()
        protocol = 'ssh' if origin.startswith(('git@', 'ssh://')) else 'https'
    else:
        preference = _run(['gh', 'config', 'get', 'git_protocol', '--host', ref.host], check=False, timeout=30)
        protocol = preference.stdout.strip() if preference.returncode == 0 else 'https'
    if protocol == 'ssh':
        return 'git@%s:%s.git' % (ref.host, ref.slug), ('protocol.ssh.allow=always',)
    if protocol != 'https':
        raise GitError('unsupported operator Git transport; configure gh git_protocol as https or ssh')
    return 'https://%s/%s.git' % (ref.host, ref.slug), ('credential.helper=', 'credential.helper=!gh auth git-credential')


def gh_compare_base(ref: PRRef, base: str, head: str) -> str:
    from .snapshots import comparison_base
    # Changed-file patches appear only on page 1. We need just comparison metadata.
    endpoint = 'repos/%s/compare/%s...%s?per_page=1&page=2' % (ref.slug, base, head)
    result = _run(['gh', 'api', '--hostname', ref.host, endpoint], timeout=120)
    if len(result.stdout.encode()) > 512 * 1024:
        raise GitError('comparison metadata exceeded its limit')
    try:
        return comparison_base(json.loads(result.stdout), base, head)
    except ValueError as exc:
        raise GitError('invalid pinned comparison metadata') from exc


def git_diff(git_dir: Path, from_sha: str, to_sha: str, omitted_paths=()) -> str:
    return _run([
        "git", "-C", str(git_dir), "-c", "protocol.allow=never", "-c", "core.quotepath=false", "diff",
        "--no-color", "--no-ext-diff", "--no-textconv", "-M", "-U3", from_sha, to_sha,
        "--", ".", *(":(exclude,literal)" + path for path in omitted_paths),
    ], timeout=300).stdout


def ls_tree(git_dir: Path, sha: str) -> Dict[str, str]:
    """Map path -> file mode for every blob at `sha`."""
    proc = subprocess.run(
        ["git", "-C", str(git_dir), "ls-tree", "-r", "-z", sha],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise GitError("git ls-tree failed: %s" % proc.stderr.decode("utf-8", "replace").strip()[-300:])
    tree: Dict[str, str] = {}
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == b"blob":
            tree[path.decode("utf-8", "replace")] = parts[0].decode()
    return tree


def git_show(git_dir: Path, sha: str, path: str, *, max_bytes: Optional[int] = None) -> Optional[str]:
    if max_bytes is not None:
        # Query the immutable blob size before capturing content, including on the host.
        size = subprocess.run(["git", "-c", "protocol.allow=never", "-C", str(git_dir), "cat-file", "-s", "%s:%s" % (sha, path)],
                              capture_output=True, timeout=60)
        if size.returncode:
            return None
        if int(size.stdout) > max_bytes:
            raise GitContentTooLarge('Git content exceeds the requested read limit')
    proc = subprocess.run(["git", "-c", "protocol.allow=never", "-C", str(git_dir), "show", "%s:%s" % (sha, path)], capture_output=True, timeout=60)
    if proc.returncode != 0:
        return None
    if binary_sample(proc.stdout[:SAMPLE_BYTES]):
        return None
    return proc.stdout.decode("utf-8", "replace")


def export_tree(git_dir: Path, sha: str, dest: Path) -> Dict[str, int]:
    """Export local regular text blobs; omitted assets never enter an archive."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    stats = {"files": 0, "skipped_links": 0, "skipped_binaries": 0}
    entries = tree(git_dir, sha)
    local = sizes(git_dir, [e['oid'] for e in entries.values() if e['kind'] == b'blob'])
    for name, entry in entries.items():
        if entry['mode'] not in (b'100644', b'100755'):
            stats['skipped_links'] += 1
            continue
        size = local[entry['oid']]
        if known_binary_path(name) or (size is not None and blob_is_binary(str(git_dir), entry['oid'], size)):
            stats['skipped_binaries'] += 1
            continue
        if size is None:
            raise GitError('required snapshot blobs are missing')
        target = (root / name.decode('utf-8')).resolve()
        if root not in target.parents:
            raise GitError('unsafe exported path')
        target.parent.mkdir(parents=True, exist_ok=True)
        data = read_git(git_dir, 'cat-file', 'blob', entry['oid'], limit=size)
        with target.open('xb') as out:
            out.write(data)
        stats['files'] += 1
    return stats


def binary_omissions(ctx):
    """Head checkout omissions plus binary changes from the merge base, including deletions.

    VM input has already passed tree admission. The explicit trusted-host path
    uses the same metadata cap and prefix classifier without VM text limits.
    """
    trees = []
    for sha in (ctx.merge_base, ctx.head_sha):
        entries = tree(ctx.git_dir, sha)
        local = sizes(ctx.git_dir, [e['oid'] for e in entries.values() if e['kind'] == b'blob'])
        current = {}
        for name, entry in entries.items():
            if entry['mode'] not in (b'100644', b'100755'):
                continue
            size = local[entry['oid']]
            binary = known_binary_path(name)
            if not binary:
                if size is None:
                    raise GitError('required snapshot blobs are missing')
                binary = blob_is_binary(str(ctx.git_dir), entry['oid'], size)
            current[name] = (entry['oid'], size, binary)
        trees.append(current)
    base, head = trees
    result = []
    for path in sorted(set(base) | set(head)):
        old, new = base.get(path), head.get(path)
        if not ((old and old[2]) or (new and new[2])):
            continue
        status = 'added' if old is None else 'deleted' if new is None else 'unchanged' if old[0] == new[0] else 'modified'
        result.append(dict(path=path.decode('utf-8', 'replace'), status=status,
                           base_bytes=old[1] if old else None, head_bytes=new[1] if new else None))
    return result
