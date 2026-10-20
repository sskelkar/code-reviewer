"""Turn a PR reference into a PRContext pinned to exact commits, using gh and git."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import git_ops
from .models import PRContext


def load_pr_context(ref: git_ops.PRRef, local_path: Optional[Path], emit: Callable[..., None]) -> PRContext:
    """Fetch the PR head and base into a private ref namespace. Never touches the working tree or any branch."""
    emit("prepare_started", ref=str(ref))
    meta = git_ops.gh_pr_view(ref)
    git_dir = _object_store(ref, local_path, emit)
    base_ref = str(meta["baseRefName"])
    base_sha, head_sha = git_ops.fetch_pr_refs(git_dir, ref.number, base_ref)
    merge_base = git_ops.merge_base(git_dir, base_sha, head_sha)
    author = meta.get("author")
    ctx = PRContext(
        repo=ref.slug,
        number=ref.number,
        url=str(meta.get("url", "")),
        title=str(meta.get("title", ""))[:300],
        body=str(meta.get("body", "") or "")[:8000],
        commit_messages=git_ops.commit_messages(git_dir, merge_base, head_sha),
        base_ref=base_ref,
        base_sha=base_sha,
        head_sha=head_sha,
        merge_base=merge_base,
        git_dir=git_dir,
        host=ref.host,
        author=str(author.get("login", "")) if isinstance(author, dict) else "",
        state=str(meta.get("state", "")),
    )
    if ctx.state and ctx.state != "OPEN":
        emit("prepare_note", message="PR state is %s" % ctx.state)
    emit("prepare_done", head=head_sha, base=base_sha)
    return ctx


def _object_store(ref: git_ops.PRRef, local_path: Optional[Path], emit: Callable[..., None]) -> Path:
    """Use the local clone when it is the same repository, otherwise a cached bare clone."""
    local = git_ops.local_repo_info(local_path) if local_path else None
    if local is not None and git_ops.same_repo(local, ref):
        return local.toplevel
    emit("prepare_note", message="using a cached clone of %s" % ref.slug)
    return git_ops.ensure_cache_clone(ref)
