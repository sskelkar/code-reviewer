"""Building what the Reviewer is given: the redacted PR text, the filtered diff, and the criteria.

Shared by the review pipeline. Dry runs build the pinned diff and default criteria but omit project discovery and model calls.
"""
from __future__ import annotations
from ..tracing import traced

from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from . import criteria as criteria_mod
from . import git_ops
from ..config import Config
from .diffview import FileDiff, changed_line_count, mark_exclusions, parse_unified_diff, render_changed_files, render_diff_block, render_excluded, reviewable
from .models import PRContext, ReviewInput, ReviewRefused
from .redact import redact_emails
from .templates import PromptSet


class InputBuilder:
    def __init__(self, cfg: Config, prompts: Dict[str, PromptSet], defaults_dir: Path, emit: Callable[..., None]):
        self.cfg, self.prompts, self.defaults_dir, self.emit = cfg, prompts, defaults_dir, emit

    # ------------------------------------------------------------------ PR text

    def redact(self, ctx: PRContext) -> Tuple[PRContext, int]:
        """The PR text with email addresses replaced (unless disabled). Applied at the start of every review and dry run,
        so there is no path that sends them. Returns the new context and the number of addresses replaced."""
        if not self.cfg.redact_emails:
            return ctx, 0
        title, in_title = redact_emails(ctx.title)
        body, in_body = redact_emails(ctx.body)
        commits, in_commits = redact_emails(ctx.commit_messages)
        count = in_title + in_body + in_commits
        if count:
            self.emit("pii_redacted", emails=count)
        return replace(ctx, title=title, body=body, commit_messages=commits), count

    # ------------------------------------------------------------------ diff

    def load_diff(self, ctx: PRContext) -> Tuple[List[FileDiff], Dict[str, int]]:
        """Parse the merge-base diff, drop lockfiles/generated files, and refuse PRs that are empty or too large."""
        ctx.binary_omissions = git_ops.binary_omissions(ctx)
        files = parse_unified_diff(git_ops.git_diff(ctx.git_dir, ctx.merge_base, ctx.head_sha,
            omitted_paths=[item["path"] for item in ctx.binary_omissions]))
        existing = {f.path for f in files}
        for item in ctx.binary_omissions:
            if item["path"] not in existing and item["status"] != "unchanged":
                files.append(FileDiff(path=item["path"], status=item["status"], binary=True))
        omitted = {item['path'] for item in ctx.binary_omissions}
        # Target .gitattributes may force binary blobs to appear as text. Never
        # send those hunks merely because Git's diff driver called them text.
        for file in files:
            if file.path in omitted or file.old_path in omitted:
                file.binary = True
                file.hunks = []
                file.added = file.removed = 0
        mark_exclusions(files, self.cfg.all_exclude_patterns())
        included = reviewable(files)
        lines = changed_line_count(files)
        if not included:
            raise ReviewRefused("the pull request has no reviewable changes (all files excluded or no diff)")
        if lines > self.cfg.max_diff_lines:
            raise ReviewRefused("the diff has %d changed lines (limit %d after exclusions); refusing to review" % (
                lines, self.cfg.max_diff_lines))
        summary = {"files": len(included), "excluded": len(files) - len(included), "changed_lines": lines}
        self.emit("diff_ready", **summary)
        return files, summary

    # ------------------------------------------------------------------ criteria

    def default_rules(self) -> List[criteria_mod.Rule]:
        return criteria_mod.load_default_rules(self.defaults_dir, self.cfg.disabled_default_rules)

    def default_criteria(self, files: List[FileDiff]) -> criteria_mod.CriteriaSet:
        """Baseline-only preview; dry runs omit project discovery and model calls."""
        return criteria_mod.assemble(self.default_rules(), criteria_mod.ProjectCriteria(), [f.path for f in files])

    @traced('criteria.resolve')
    def resolve_criteria(self, ctx: PRContext, files: List[FileDiff]) -> criteria_mod.CriteriaSet:
        """Explicit base-commit project rules, or enabled baseline rules when none load."""
        self.emit("criteria_discovery_started")
        project = self._resolve_project_criteria(ctx, files)
        criteria = criteria_mod.assemble(self.default_rules(), project, [f.path for f in files])
        for warning in criteria.warnings:
            self.emit("warning", message=warning)
        self.emit(
            "criteria_resolved",
            default_rules=sum(1 for r in criteria.rules if r.source == "default"),
            project_rules=sum(1 for r in criteria.rules if r.source == "project"),
            project_files=len(project.files_used),
            ignored=len(project.ignored),
        )
        return criteria

    def _resolve_project_criteria(self, ctx: PRContext, files: List[FileDiff]) -> criteria_mod.ProjectCriteria:
        tree = git_ops.ls_tree(ctx.git_dir, ctx.base_sha)
        from .review_guidance import SOURCE_BYTES, source_label

        def show(path):
            try:
                return git_ops.git_show(ctx.git_dir, ctx.base_sha, path, max_bytes=SOURCE_BYTES)
            except git_ops.GitContentTooLarge as exc:
                raise criteria_mod.CriteriaLimitExceeded(
                    'review guidance source %s exceeds the %d-byte discovery file limit; shorten or split the source; '
                    'no review passes ran' % (source_label(path), SOURCE_BYTES)) from exc

        return criteria_mod.resolve_project(tree, show, self.cfg.criteria_max_chars, self.scope_paths(files))

    @staticmethod
    def scope_paths(files: List[FileDiff]) -> List[str]:
        """The files whose directories decide which ancestor README/AGENTS instructions apply: the ones under review.
        A change in an excluded file (a planning document, a lockfile) does not pull in the rules of its directory."""
        return [f.path for f in reviewable(files)]

    # ------------------------------------------------------------------ what the Reviewer sees

    @staticmethod
    def review_input(ctx: PRContext, files: List[FileDiff], criteria: criteria_mod.CriteriaSet, checkout: Path) -> ReviewInput:
        """Everything the Reviewer is given that stays the same in every pass."""
        return ReviewInput(
            ctx=ctx, checkout=checkout, criteria_block=criteria_mod.render_criteria_block(criteria.rules),
            diff_block=render_diff_block(files), changed_files=render_changed_files(files), excluded_files=render_excluded(files),
        )
