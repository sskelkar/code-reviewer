"""The review engine. `Engine.review` reads as the story of the agent:

    pin the PR -> diff -> read-only checkout -> resolve criteria -> loop of passes -> report

Every step lives in its own module; this file only wires them together and emits progress events.
"""
from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import criteria as criteria_mod
from . import git_ops
from .config import DEFAULT_CRITERIA_DIR, PROMPTS_DIR, Config
from .diffview import (
    DiffIndex, FileDiff, changed_line_count, mark_exclusions, parse_unified_diff, render_changed_files,
    render_diff_block, render_excluded, reviewable,
)
from .events import Event, EventSink, null_sink
from .findings import FileCache, FindingsPreparer
from .llm import ClaudeCaller
from .master import MasterList
from .models import PassFailed, PassRecord, PRContext, ReviewInput, ReviewRefused, RunResult
from .passes import PassRunner
from .pr_source import load_pr_context
from .report import render_report
from .roles import Comparator, CriteriaResolver, Reviewer
from .run_store import RunStore
from .stats import Stats
from .stop_policy import CANCELLED, CONVERGED, StopPolicy
from .summary import render_summary
from .templates import ROLES, load_prompt, prompt_hashes

__all__ = ["Engine", "PRContext", "ReviewRefused", "RunResult"]


class Engine:
    def __init__(
        self,
        cfg: Config,
        runner: Any,
        emit: Optional[EventSink] = None,
        prompts_dir: Path = PROMPTS_DIR,
        defaults_dir: Path = DEFAULT_CRITERIA_DIR,
    ):
        self.cfg = cfg
        self.runner = runner
        self.sink = emit or null_sink
        self.prompts_dir = prompts_dir
        self.defaults_dir = defaults_dir
        self.prompts = {role: load_prompt(role, prompts_dir) for role in ROLES}

    def emit(self, type_: str, **data: Any) -> None:
        self.sink(Event(type_, data))

    def prepare(self, ref: git_ops.PRRef, local_path: Optional[Path] = None) -> PRContext:
        """Pin a PR reference to exact commits (uses gh and git)."""
        return load_pr_context(ref, local_path, self.emit)

    # ------------------------------------------------------------------ the story

    def review(self, ctx: PRContext, keep_checkout: bool = False) -> RunResult:
        cfg = self.cfg
        self.emit("run_started", repo=ctx.repo, number=ctx.number, title=ctx.title, head=ctx.head_sha, base=ctx.base_sha)
        stats = Stats()
        store = RunStore.create(cfg.runs_path(), ctx)
        store.dump("config.json", self._run_metadata(ctx))

        files, diff_summary = self._load_diff(ctx)
        self.emit("checkout_ready", **git_ops.export_tree(ctx.git_dir, ctx.head_sha, store.checkout))

        caller = ClaudeCaller(self.runner, cfg, stats)
        criteria = self._resolve_criteria(ctx, [f.path for f in files], caller, store)
        review_input = ReviewInput(
            ctx=ctx, checkout=store.checkout, criteria_block=criteria_mod.render_criteria_block(criteria.rules),
            diff_block=render_diff_block(files), changed_files=render_changed_files(files), excluded_files=render_excluded(files),
        )
        store.save_inputs(review_input, criteria)

        master = MasterList()
        pass_runner = PassRunner(
            cfg,
            Reviewer(caller, self.prompts["reviewer"], cfg, review_input),
            Comparator(caller, self.prompts["comparator"], cfg, store.input_dir),
            FindingsPreparer(criteria.index(), cfg.self_check, FileCache(store.checkout), DiffIndex(files), cfg.grounding_line_window),
            master, stats, store, self.emit,
        )
        passes, completed, stop_reason = self._run_passes(pass_runner, stats)

        result = RunResult(
            ctx=ctx, cfg=cfg, run_dir=store.root, master=master, stats=stats, stop_reason=stop_reason, passes=passes,
            passes_completed=completed, criteria=criteria, report_path=store.report_path, diff_summary=diff_summary,
        )
        store.save_outputs(master.to_dict(), {**stats.to_dict(), "passes": [p.to_dict() for p in passes], "stop_reason": stop_reason})
        store.report_path.write_text(render_report(result), encoding="utf-8")
        store.summary_path.write_text(render_summary(result, cfg.post_tiers, []), encoding="utf-8")
        if not keep_checkout:
            store.cleanup_checkout()
        self.emit("report_ready", path=str(store.report_path), stop_reason=stop_reason)
        return result

    # ------------------------------------------------------------------ steps

    def _run_metadata(self, ctx: PRContext) -> Dict[str, Any]:
        versions = {"python": platform.python_version()}
        if hasattr(self.runner, "version"):
            versions["claude_code"] = self.runner.version()
        return {
            "config": self.cfg.to_dict(),
            "versions": versions,
            "prompt_hashes": prompt_hashes(self.prompts_dir),
            "pull_request": {
                "repo": ctx.repo, "number": ctx.number, "url": ctx.url, "title": ctx.title, "base_ref": ctx.base_ref,
                "base_sha": ctx.base_sha, "head_sha": ctx.head_sha, "merge_base": ctx.merge_base, "author": ctx.author,
            },
        }

    def _load_diff(self, ctx: PRContext) -> Tuple[List[FileDiff], Dict[str, int]]:
        """Parse the merge-base diff, drop lockfiles/generated files, and refuse PRs that are empty or too large."""
        files = parse_unified_diff(git_ops.git_diff(ctx.git_dir, ctx.merge_base, ctx.head_sha))
        mark_exclusions(files, self.cfg.exclude_patterns)
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

    def _resolve_criteria(self, ctx: PRContext, changed_paths: List[str], caller: ClaudeCaller, store: RunStore) -> criteria_mod.CriteriaSet:
        """Default rules plus the project's own rules (from the base commit), merged with project precedence."""
        cfg = self.cfg
        defaults: List[criteria_mod.Rule] = []
        if cfg.criteria_mode != "project_only":
            defaults = criteria_mod.load_default_rules(self.defaults_dir, cfg.disabled_default_rules)
        project = criteria_mod.ProjectCriteria()
        if cfg.criteria_mode != "defaults_only":
            project = self._resolve_project_criteria(ctx, caller, store)
        criteria = criteria_mod.assemble(defaults, project, cfg.criteria_mode, changed_paths)
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

    def _resolve_project_criteria(self, ctx: PRContext, caller: ClaudeCaller, store: RunStore) -> criteria_mod.ProjectCriteria:
        tree = git_ops.ls_tree(ctx.git_dir, ctx.base_sha)
        agents_text = git_ops.git_show(ctx.git_dir, ctx.base_sha, "AGENTS.md") if "AGENTS.md" in tree else None
        override: Optional[Dict[str, Any]] = None
        if self.cfg.criteria_manifest:
            override = json.loads(Path(self.cfg.criteria_manifest).expanduser().read_text(encoding="utf-8"))
        resolver = CriteriaResolver(caller, self.prompts["resolver"], self.cfg, ctx, store.input_dir, self.emit)
        return criteria_mod.resolve_project(
            agents_text, tree, lambda path: git_ops.git_show(ctx.git_dir, ctx.base_sha, path),
            resolver, override, self.cfg.criteria_max_chars,
        )

    def _run_passes(self, pass_runner: PassRunner, stats: Stats) -> Tuple[List[PassRecord], int, str]:
        """Run passes until the stop policy says stop. Returns (pass records, completed passes, stop reason)."""
        cfg = self.cfg
        policy = StopPolicy(cfg)
        passes: List[PassRecord] = []
        reason: Optional[str] = None
        try:
            for pass_no in range(1, cfg.max_passes + 1):
                reason = policy.before_pass(self._over_budget(stats))
                if reason:
                    break
                self.emit("pass_started", pass_no=pass_no, max_passes=cfg.max_passes)
                try:
                    record = pass_runner.run(pass_no)
                except PassFailed as exc:
                    passes.append(PassRecord(pass_no=pass_no, ok=False, error=str(exc)))
                    self.emit("pass_failed", pass_no=pass_no, error=str(exc), cost=stats.total_cost())
                    reason = policy.after_failure()
                else:
                    passes.append(record)
                    reason = policy.after_success(record.new_scored)
                    self.emit(
                        "pass_finished", pass_no=pass_no, new=record.new_scored, matched=record.matched,
                        ungrounded=record.ungrounded + record.invalid_rule, cost=stats.total_cost(),
                        quiet_streak=policy.quiet_streak, quiet_k=cfg.quiet_k,
                    )
                if reason:
                    break
        except KeyboardInterrupt:
            reason = CANCELLED
        stop_reason = policy.final_reason(reason)
        if stop_reason == CONVERGED:
            self.emit("converged", passes=policy.completed, quiet_k=cfg.quiet_k)
        return passes, policy.completed, stop_reason

    def _over_budget(self, stats: Stats) -> bool:
        return stats.total_cost() >= self.cfg.run_budget_usd or stats.budget_tokens() >= self.cfg.run_budget_tokens
