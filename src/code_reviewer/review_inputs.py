"""Building what the Reviewer is given: the redacted PR text, the filtered diff, and the criteria.

Used by `Engine.review` and `Engine.dry_run`, so a dry run builds its inputs exactly as a real review does.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import criteria as criteria_mod
from . import git_ops
from .config import Config
from .diffview import (
    FileDiff, changed_line_count, mark_exclusions, parse_unified_diff, render_changed_files, render_diff_block, render_excluded,
    reviewable,
)
from .llm import ClaudeCaller
from .models import PRContext, ReviewInput, ReviewRefused
from .redact import redact_emails
from .roles import CriteriaResolver
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
        files = parse_unified_diff(git_ops.git_diff(ctx.git_dir, ctx.merge_base, ctx.head_sha))
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
        if self.cfg.criteria_mode == "project_only":
            return []
        return criteria_mod.load_default_rules(self.defaults_dir, self.cfg.disabled_default_rules)

    def default_criteria(self, files: List[FileDiff]) -> criteria_mod.CriteriaSet:
        """The criteria without the project's own rules. Resolving those needs a model call, which a dry run avoids."""
        return criteria_mod.assemble(self.default_rules(), criteria_mod.ProjectCriteria(), self.cfg.criteria_mode, [f.path for f in files])

    def resolve_criteria(self, ctx: PRContext, files: List[FileDiff], caller: ClaudeCaller, work_dir: Path) -> criteria_mod.CriteriaSet:
        """Default rules plus the project's own rules (from the base commit), merged with project precedence."""
        cfg = self.cfg
        project = criteria_mod.ProjectCriteria()
        if cfg.criteria_mode != "defaults_only":
            project = self._resolve_project_criteria(ctx, files, caller, work_dir)
        criteria = criteria_mod.assemble(self.default_rules(), project, cfg.criteria_mode, [f.path for f in files])
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

    def _resolve_project_criteria(self, ctx: PRContext, files: List[FileDiff], caller: ClaudeCaller, work_dir: Path) -> criteria_mod.ProjectCriteria:
        tree = git_ops.ls_tree(ctx.git_dir, ctx.base_sha)
        agents_text = git_ops.git_show(ctx.git_dir, ctx.base_sha, "AGENTS.md") if "AGENTS.md" in tree else None
        override: Optional[Dict[str, Any]] = None
        if self.cfg.criteria_manifest:
            override = json.loads(Path(self.cfg.criteria_manifest).expanduser().read_text(encoding="utf-8"))
        resolver = CriteriaResolver(caller, self.prompts["resolver"], self.cfg, ctx, work_dir, self.emit)
        return criteria_mod.resolve_project(
            agents_text, tree, lambda path: git_ops.git_show(ctx.git_dir, ctx.base_sha, path),
            resolver, override, self.cfg.criteria_max_chars, self.scope_paths(files),
        )

    @staticmethod
    def scope_paths(files: List[FileDiff]) -> List[str]:
        """The files whose directories decide which nested AGENTS.md and .claude/rules files apply: the ones under review.
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
