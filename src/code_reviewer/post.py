"""Format the PR summary comment and post it through the gh CLI (only ever after human confirmation)."""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set

from .git_ops import GitError, PRRef, _run
from .master import confidence, tier
from .report import TIERS, fence

MARKER = "<!-- code-reviewer:v0 -->"
MAX_COMMENT_CHARS = 60000


def select_findings(result: Any, tiers: Sequence[str], dropped: Sequence[str]) -> List[Dict[str, Any]]:
    cfg = result.cfg
    chosen = []
    for item in result.master.scored():
        level = tier(confidence(item, result.passes_completed), cfg.tier_high, cfg.tier_medium)
        if level in tiers and item["id"] not in dropped:
            chosen.append((TIERS.index(level), item))
    chosen.sort(key=lambda pair: (pair[0], pair[1]["id"]))
    return [item for _, item in chosen]


def format_comment(result: Any, tiers: Sequence[str], dropped: Sequence[str]) -> str:
    cfg, ctx = result.cfg, result.ctx
    items = select_findings(result, tiers, dropped)
    lines = [
        MARKER,
        "## AI-generated code review (experimental)",
        "",
        "Reviewed commit `%s` with %d independent pass(es) of `%s`. Confidence is the share of passes that found an issue. "
        "This review is advisory and has not been verified by a human reviewer." % (ctx.head_sha[:12], result.passes_completed, cfg.model_reviewer),
        "",
    ]
    if not items:
        lines.append("No findings at the selected confidence levels (%s)." % ", ".join(tiers))
    for item in items:
        canon = item["canonical"]
        level = tier(confidence(item, result.passes_completed), cfg.tier_high, cfg.tier_medium)
        location = "%s:%d" % (canon["file"], canon["line"]) if canon.get("line") else canon["file"]
        lines += [
            "### %s · %s severity · %s confidence" % (item["id"], canon["severity"], level),
            "**%s**" % canon["title"],
            "`%s` · seen in %d/%d passes" % (location, len(item["seen_in_passes"]), result.passes_completed),
            "",
            canon["description"],
        ]
        if canon.get("quote"):
            lines += ["", fence(canon["quote"])]
        if canon.get("suggestion"):
            lines += ["", "**Suggestion:** %s" % canon["suggestion"]]
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
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
