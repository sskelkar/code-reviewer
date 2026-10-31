"""The single-agent workflow. `SingleAgentFlow.review` is the composition root:

    pin the PR -> diff -> read-only checkout -> resolve criteria -> loop of passes -> report

Every step lives in its own module; this file only wires them together and emits progress events.
"""
from __future__ import annotations

from ...shared.git_content import omission_section

import platform
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...shared import git_ops
from ...config import DEFAULT_CRITERIA_DIR, PROMPTS_DIR, Config
from ...shared.diffview import DiffIndex
from .dry_run import DryRun, reviewer_command
from ...shared.events import Event, EventSink, null_sink
from ...shared.findings import FileCache, FindingsPreparer
from ...shared.llm import ModelCaller
from .master import MasterList
from ...shared.models import PassFailed, PassRecord, PRContext
from .models import RunResult
from .passes import PassRunner
from ...shared.pr_source import load_pr_context
from .report import render_report
from ...shared.review_inputs import InputBuilder
from .roles import Comparator, Reviewer
from ...shared.run_store import RunStore
from ...shared.stats import Stats
from .stop_policy import CANCELLED, CONVERGED, StopPolicy
from .summary import render_summary
from .prompt_contract import ROLES, load_prompt, prompt_hashes

__all__ = ["SingleAgentFlow"]


class SingleAgentFlow:
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
        self.inputs = InputBuilder(cfg, self.prompts, defaults_dir, self.emit)

    def emit(self, type_: str, **data: Any) -> None:
        self.sink(Event(type_, data))

    def prepare(self, ref: git_ops.PRRef, local_path: Optional[Path] = None) -> PRContext:
        """Pin a PR reference to exact commits (uses gh and git)."""
        return load_pr_context(ref, local_path, self.emit)

    # ------------------------------------------------------------------ the story

    def review(self, ctx: PRContext, keep_checkout: bool = False, prepared_checkout: Optional[Path] = None) -> RunResult:
        cfg = self.cfg
        ctx, redacted_emails = self.inputs.redact(ctx)
        self.emit("run_started", repo=ctx.repo, number=ctx.number, title=ctx.title, head=ctx.head_sha, base=ctx.base_sha)
        stats = Stats()
        store = RunStore.create(cfg.runs_path(), ctx)
        store.dump("config.json", self._run_metadata(ctx))
        store.save_pr_text(ctx, redacted_emails)

        files, diff_summary = self.inputs.load_diff(ctx)
        checkout = Path(prepared_checkout) if prepared_checkout is not None else store.prepare_checkout()
        if prepared_checkout is not None and (checkout.is_symlink() or not checkout.is_dir()):
            raise ValueError('prepared checkout must be a regular admitted directory')
        try:
            self.emit("checkout_ready", path=str(checkout), kept=keep_checkout,
                      **({} if prepared_checkout is not None else git_ops.export_tree(ctx.git_dir, ctx.head_sha, checkout)))

            caller = ModelCaller(self.runner, cfg, stats)
            criteria = self.inputs.resolve_criteria(ctx, files, caller, store.input_dir)
            review_input = self.inputs.review_input(ctx, files, criteria, checkout)
            store.save_inputs(review_input, criteria)
            store.save_prompts(*Reviewer.render(self.prompts["reviewer"], cfg, review_input))

            master = MasterList()
            pass_runner = PassRunner(
                cfg,
                Reviewer(caller, self.prompts["reviewer"], cfg, review_input),
                Comparator(caller, self.prompts["comparator"], cfg, store.input_dir),
                FindingsPreparer(criteria.index(), cfg.self_check, FileCache(checkout), DiffIndex(files), cfg.grounding_line_window,
                                 cfg.all_exclude_patterns()),
                master, stats, store, self.emit,
            )
            passes, completed, stop_reason = self._run_passes(pass_runner, stats)

            result = RunResult(
                ctx=ctx, cfg=cfg, run_dir=store.root, master=master, stats=stats, stop_reason=stop_reason, passes=passes,
                passes_completed=completed, criteria=criteria, report_path=store.report_path, diff_summary=diff_summary,
            )
            store.save_outputs(master.to_dict(), {**stats.to_dict(), "passes": [p.to_dict() for p in passes], "stop_reason": stop_reason})
            store.report_path.write_text(render_report(result) + omission_section(ctx), encoding="utf-8")
            store.summary_path.write_text(render_summary(result, cfg.post_tiers, []), encoding="utf-8")
            store.save_binary_omissions(ctx)
            self.emit("report_ready", path=str(store.report_path), stop_reason=stop_reason)
            return result
        finally:
            if not keep_checkout:  # also on errors: the checkout never outlives the run unless asked
                store.cleanup_checkout()

    def dry_run(self, ctx: PRContext) -> DryRun:
        """Show what the Reviewer would be given, without calling any model and without creating the checkout.

        The project's own criteria need a model call to resolve, so only the default rules are included here.
        """
        cfg = self.cfg
        ctx, redacted_emails = self.inputs.redact(ctx)
        store = RunStore.create(cfg.runs_path(), ctx, label="dry-run")
        store.save_pr_text(ctx, redacted_emails)
        files, summary = self.inputs.load_diff(ctx)
        criteria = self.inputs.default_criteria(files)
        review_input = self.inputs.review_input(ctx, files, criteria, Path("<checkout>"))
        store.save_prompts(*Reviewer.render(self.prompts["reviewer"], cfg, review_input))
        (store.input_dir / "diff.txt").write_text(review_input.diff_block + "\n", encoding="utf-8")
        return DryRun(
            ctx=ctx, run_dir=store.root, files=summary["files"], excluded=summary["excluded"], changed_lines=summary["changed_lines"],
            default_rules=len(criteria.rules), model=cfg.model_reviewer, tools=Reviewer.TOOLS, command=reviewer_command(cfg),
            redaction_enabled=cfg.redact_emails, redacted_emails=redacted_emails, provider=cfg.provider,
        )

    def _run_metadata(self, ctx: PRContext) -> Dict[str, Any]:
        versions = {"python": platform.python_version()}
        if hasattr(self.runner, "version"):
            versions["adapter"] = self.runner.version()
        return {
            "config": self.cfg.to_dict(),
            "versions": versions,
            "prompt_hashes": prompt_hashes(self.prompts_dir),
            "pull_request": {
                "repo": ctx.repo, "number": ctx.number, "url": ctx.url, "title": ctx.title, "base_ref": ctx.base_ref,
                "base_sha": ctx.base_sha, "head_sha": ctx.head_sha, "merge_base": ctx.merge_base, "author": ctx.author,
            },
        }

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
                        ungrounded=record.ungrounded, cost=stats.total_cost(),
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
