"""Git and GitHub CLI operations. Everything here is read-only with respect to the user's working tree."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import DATA_DIR


class GitError(RuntimeError):
    """Raised when a git or gh command fails."""


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
        "title,body,baseRefName,headRefName,headRefOid,url,state,author,isCrossRepository",
    ], timeout=120)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise GitError("could not parse gh pr view output") from exc


def ensure_cache_clone(ref: PRRef, cache_dir: Optional[Path] = None) -> Path:
    cache = (cache_dir or DATA_DIR / "cache")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = cache / ("%s__%s__%s.git" % (ref.host, ref.owner, ref.repo))
    if not path.exists():
        _run(["gh", "repo", "clone", ref.gh_repo, str(path), "--", "--bare", "--quiet"], timeout=1800)
    return path


def fetch_pr_refs(git_dir: Path, number: int, base_ref: str) -> Tuple[str, str]:
    """Fetch the PR head and the base branch tip into a private ref namespace; return (base_sha, head_sha)."""
    head_ref = "refs/code-reviewer/pr-%d-head" % number
    base_local = "refs/code-reviewer/pr-%d-base" % number
    # Explicit destinations alone still allow configured remote-tracking updates.
    # Ignore those mappings so IDE/background fetches do not contend with this review.
    _run([
        "git", "-C", str(git_dir), "fetch", "--quiet", "--no-tags", "--refmap=", "origin",
        "+refs/pull/%d/head:%s" % (number, head_ref),
        "+refs/heads/%s:%s" % (base_ref, base_local),
    ], timeout=1800)
    head = _run(["git", "-C", str(git_dir), "rev-parse", head_ref]).stdout.strip()
    base = _run(["git", "-C", str(git_dir), "rev-parse", base_local]).stdout.strip()
    return base, head


def merge_base(git_dir: Path, base_sha: str, head_sha: str) -> str:
    proc = _run(["git", "-C", str(git_dir), "merge-base", base_sha, head_sha], check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else base_sha


def git_diff(git_dir: Path, from_sha: str, to_sha: str) -> str:
    return _run([
        "git", "-C", str(git_dir), "-c", "core.quotepath=false", "diff",
        "--no-color", "--no-ext-diff", "--no-textconv", "-M", "-U3", from_sha, to_sha,
    ], timeout=300).stdout


def commit_messages(git_dir: Path, from_sha: str, to_sha: str, max_chars: int = 6000) -> str:
    proc = _run(["git", "-C", str(git_dir), "log", "--format=%B%x1e", "%s..%s" % (from_sha, to_sha)], check=False)
    messages = [m.strip() for m in proc.stdout.split("\x1e") if m.strip()]
    text = "\n---\n".join(messages)
    return text[:max_chars] + ("\n[truncated]" if len(text) > max_chars else "")


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


def git_show(git_dir: Path, sha: str, path: str) -> Optional[str]:
    proc = subprocess.run(["git", "-C", str(git_dir), "show", "%s:%s" % (sha, path)], capture_output=True, timeout=60)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def export_tree(git_dir: Path, sha: str, dest: Path) -> Dict[str, int]:
    """Export the tree at `sha` into `dest` using git archive. Symlinks and special files are skipped."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    stats = {"files": 0, "skipped_links": 0}
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "tree.tar")
        _run(["git", "-C", str(git_dir), "archive", "--format=tar", "-o", archive, sha], timeout=900)
        with tarfile.open(archive) as tar:
            for member in tar:
                if member.issym() or member.islnk() or member.isdev():
                    stats["skipped_links"] += 1
                    continue
                target = (root / member.name).resolve()
                if root != target and root not in target.parents:
                    continue
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    if source is None:
                        continue
                    with open(target, "wb") as out:
                        out.write(source.read())
                    stats["files"] += 1
    return stats
