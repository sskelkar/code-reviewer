"""`reviewer review --dry-run`: show exactly what the Reviewer would be given, without calling any model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from ...shared.claude_cli import build_command
from ...config import Config
from ...shared.models import PRContext
from .roles import Reviewer

MAX_SHOWN_CHARS = 4000


@dataclass
class DryRun:
    ctx: PRContext
    run_dir: Path
    files: int
    excluded: int
    changed_lines: int
    default_rules: int
    model: str
    tools: str
    command: List[str]
    redaction_enabled: bool = True
    redacted_emails: int = 0
    provider: str = 'claude'


def reviewer_command(cfg: Config) -> List[str]:
    """The Reviewer's `claude` command line, with the two large arguments replaced by placeholders."""
    if cfg.provider == 'openai':
        return ['POST', 'https://api.openai.com/v1/responses', 'model=' + cfg.model_reviewer]
    cmd = build_command(
        cfg.claude_bin, system_prompt="", schema={}, model=cfg.model_reviewer, tools=Reviewer.TOOLS,
        budget_usd=cfg.call_budget_usd, isolation_flags=cfg.isolation_flags)
    placeholders = {"--append-system-prompt": "<system prompt: input/reviewer.system.md>", "--json-schema": "<JSON schema of the findings>"}
    return [placeholders.get(cmd[i - 1], arg) if i else arg for i, arg in enumerate(cmd)]


def _indented(text: str, limit: int = MAX_SHOWN_CHARS) -> str:
    shown = text.strip() or "(none)"
    note = "\n    [... %d more characters, see input/pr.json]" % (len(shown) - limit) if len(shown) > limit else ""
    return "\n".join("    " + line for line in shown[:limit].splitlines()) + note


def render_dry_run(dry: DryRun) -> str:
    ctx = dry.ctx
    commits = [m for m in ctx.commit_messages.split("\n---\n") if m.strip()]
    lines = [
        "Dry run: nothing was sent to %s, nothing was posted, and no checkout was created." % ('OpenAI' if dry.provider == 'openai' else 'Claude'),
        "",
        "Pull request: %s · head %s · base %s (%s)" % (ctx.label, ctx.head_sha[:12], ctx.base_sha[:12], ctx.base_ref),
        "",
        "WHAT THE REVIEWER IS GIVEN",
        "  PR title: %s" % ctx.title,
        "  PR description (%d characters):" % len(ctx.body),
        _indented(ctx.body),
        "  Commit messages (%d):" % len(commits),
        _indented(ctx.commit_messages),
        ("  Email addresses in the PR title, description and commit messages are replaced with [email]: %d replaced above."
         % dry.redacted_emails) if dry.redaction_enabled else "  Email redaction is OFF (redact_emails = false): PR text is sent as written.",
        "  Diff: %d files, %d changed lines (%d excluded), saved as input/diff.txt" % (dry.files, dry.changed_lines, dry.excluded),
        "  Criteria: %d default rules. The project's own rules are found with a model call (the criteria resolver), so a dry "
        "run leaves them out; a real review adds them." % dry.default_rules,
        "  Files: a read-only copy of the PR head commit (files only, no git history) in a temporary directory",
        "  Tools: %s (no shell, no network, no GitHub access)" % dry.tools.replace(",", ", "),
        "  Model: %s" % dry.model,
        "",
        "WHAT IT IS NOT GIVEN",
        "  Pull request comments, review threads, issue comments, other reviews, or the findings of other passes. "
        "Everything it receives is in the two prompt files listed below.",
        "",
        "THE HTTPS REQUEST (credentials omitted)" if dry.provider == 'openai' else "THE COMMAND (the prompt itself is sent on stdin)",
        "  " + " ".join(dry.command),
        "",
        "Saved in %s:" % (dry.run_dir / "input"),
        "  reviewer.system.md and reviewer.user.md  the exact prompts (identical in every pass)",
        "  pr.json                                  the PR text exactly as fetched",
        "  diff.txt                                 the diff as the reviewer sees it",
    ]
    return "\n".join(lines) + "\n"
