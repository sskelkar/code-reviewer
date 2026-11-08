"""Turn a PR reference into a PRContext pinned to exact commits, using gh and git."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import git_ops
from .models import PRContext


def load_pr_context(ref: git_ops.PRRef, local_path: Optional[Path], emit: Callable[..., None]) -> PRContext:
    """Pin GitHub's head/base/merge-base; materialize snapshots without history."""
    import re
    import fcntl
    import time
    from .snapshots import fetch_objects, materialize
    emit("prepare_started", ref=str(ref))
    meta = git_ops.gh_pr_view(ref)
    base, head = meta.get('baseRefOid'), meta.get('headRefOid')
    if any(not isinstance(x, str) or not re.fullmatch('[a-f0-9]{40}', x) for x in (base, head)):
        raise git_ops.GitError('GitHub did not return pinned base/head hashes')
    merge_base = git_ops.gh_compare_base(ref, base, head)
    commits = list(dict.fromkeys((base, head, merge_base)))
    criteria_only = base if base not in (head, merge_base) else None
    local = git_ops.local_repo_info(local_path) if local_path else None
    git_dir = None
    if local is not None and git_ops.same_repo(local, ref):
        try:
            # Reuse complete local objects without fetching or making the user's clone shallow.
            def unavailable(_):
                raise ValueError('local snapshot incomplete')
            materialize(local.toplevel, commits, unavailable, criteria_only=criteria_only)
            git_dir = local.toplevel
        except ValueError:
            pass
    if git_dir is None:
        git_dir = git_ops.ensure_snapshot_store(ref)
        emit('prepare_note', message='using snapshot object cache at %s' % git_dir)
        lock = git_dir.parent / (git_dir.name + '.lock')
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                url, options = git_ops.source_transport(ref, local if local and git_ops.same_repo(local, ref) else None)
                deadline = time.monotonic() + 120
                def transfer(oids, *, shallow=False):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise git_ops.GitError('snapshot acquisition exceeded its deadline')
                    fetch_objects(git_dir, url, oids, shallow=shallow, options=options, timeout=remaining)
                transfer(commits, shallow=True)
                materialize(git_dir, commits, transfer, criteria_only=criteria_only)
            except ValueError as exc:
                raise git_ops.GitError(str(exc)) from exc
    author = meta.get("author")
    ctx = PRContext(repo=ref.slug, number=ref.number, url=str(meta.get("url", "")),
        title=str(meta["title"])[:300], body=str(meta.get("body", "") or "")[:8000],
        commit_messages='', base_ref=str(meta["baseRefName"]), base_sha=base, head_sha=head,
        merge_base=merge_base, git_dir=git_dir, host=ref.host,
        author=str(author.get("login", "")) if isinstance(author, dict) else "", state=str(meta.get("state", "")))
    if ctx.state and ctx.state != "OPEN":
        emit("prepare_note", message="PR state is %s" % ctx.state)
    emit("prepare_done", head=head, base=base)
    return ctx
