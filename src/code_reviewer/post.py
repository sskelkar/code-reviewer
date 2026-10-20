"""Format the PR summary comment and post it through the gh CLI (only ever after human confirmation)."""
from __future__ import annotations

import os
import tempfile
from typing import Any, List, Sequence

from .shared.git_ops import PRRef, _run
from .flows.single_agent.summary import render_summary

MARKER = "<!-- code-reviewer:v0 -->"
MAX_COMMENT_CHARS = 60000


def format_comment(result: Any, tiers: Sequence[str], dropped: Sequence[str]) -> str:
    """The PR comment: the shareable summary plus a hidden marker so a re-run updates this comment instead of adding one."""
    body = MARKER + "\n" + render_summary(result, tiers, dropped)
    if len(body) > MAX_COMMENT_CHARS:
        body = body[:MAX_COMMENT_CHARS] + "\n\n_[comment truncated]_\n"
    return body


def _hostname_args(ref: PRRef) -> List[str]:
    return ["--hostname", ref.host] if ref.host != "github.com" else []


def post_comment(ref: PRRef, body: str) -> str:
    """Create the review comment, or update this tool's earlier comment on the PR. Returns the comment URL."""
    login = _run(["gh", "api", *_hostname_args(ref), "user", "--jq", ".login"]).stdout.strip()
    listing = _run([
        "gh", "api", *_hostname_args(ref), "--paginate",
        "repos/%s/%s/issues/%d/comments" % (ref.owner, ref.repo, ref.number),
        "--jq", '.[] | select(.user.login=="%s" and (.body | contains("%s"))) | .id' % (login, MARKER),
    ]).stdout.split()
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(body)
        path = handle.name
    try:
        if listing:
            proc = _run([
                "gh", "api", *_hostname_args(ref), "-X", "PATCH",
                "repos/%s/%s/issues/comments/%s" % (ref.owner, ref.repo, listing[-1]),
                "-F", "body=@%s" % path, "--jq", ".html_url",
            ])
        else:
            proc = _run(["gh", "pr", "comment", str(ref.number), "-R", ref.gh_repo, "--body-file", path])
        return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    finally:
        os.unlink(path)
