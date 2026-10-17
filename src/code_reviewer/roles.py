"""The three LLM roles. Each class owns its prompt variables and its output validation, and nothing else."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from .claude_cli import ClaudeResult
from .config import Config
from .events import Event
from .findings import compact
from .llm import CallSpec, ClaudeCaller, requires_lists
from .master import validate_decisions
from .models import PRContext, ReviewInput
from .templates import PromptSet, escape_untrusted, render

NEW_DECISION = {"match": "new", "same_as": None}


class CriteriaResolver:
    """Asks the model which in-repo files hold the project's review criteria. The model only proposes."""

    def __init__(self, caller: ClaudeCaller, prompt: PromptSet, cfg: Config, ctx: PRContext,
                 work_dir: Path, emit: Callable[..., None]):
        self.caller, self.prompt, self.cfg, self.ctx = caller, prompt, cfg, ctx
        self.work_dir, self.emit = work_dir, emit

    def __call__(self, candidates: List[str], agents_text: str) -> Dict[str, Any]:
        ctx = self.ctx
        variables = {
            "repo": ctx.repo,
            "base_ref": ctx.base_ref,
            "base_sha": ctx.base_sha[:12],
            "agents_md_content": escape_untrusted(agents_text),
            "candidate_paths_list": escape_untrusted("\n".join(candidates)) if candidates else "(none)",
        }
        spec = CallSpec(
            role="resolver", pass_no=None, system=render(self.prompt.system, {}, {}), user=render(self.prompt.user, variables),
            schema=self.prompt.schema, model=self.cfg.model_resolver, tools="", cwd=self.work_dir,
        )
        self.emit("resolver_started", candidates=len(candidates))
        result = self.caller.call(spec, requires_lists("criteria_files", "inline_sections"), self.work_dir / "resolver.raw.json")
        return result.parsed or {}


class Reviewer:
    """Reviews the PR in a fresh session. Sees the PR, criteria and a read-only checkout; never earlier passes."""

    TOOLS = "Read,Grep,Glob"

    def __init__(self, caller: ClaudeCaller, prompt: PromptSet, cfg: Config, inputs: ReviewInput):
        self.caller, self.prompt, self.cfg, self.inputs = caller, prompt, cfg, inputs

    def _variables(self, pass_no: int) -> Dict[str, Any]:
        ctx = self.inputs.ctx
        return {
            "pr_number": ctx.number,
            "repo": ctx.repo,
            "pass_number": pass_no,
            "base_ref": ctx.base_ref,
            "base_sha": ctx.base_sha[:12],
            "head_sha": ctx.head_sha[:12],
            "pr_title": escape_untrusted(ctx.title),
            "pr_body": escape_untrusted(ctx.body) if ctx.body.strip() else "(no description)",
            "commit_messages": escape_untrusted(ctx.commit_messages) if ctx.commit_messages.strip() else "(none)",
            "criteria_block": self.inputs.criteria_block,
            "changed_files_list": escape_untrusted(self.inputs.changed_files),
            "excluded_files_list": escape_untrusted(self.inputs.excluded_files),
            "diff_block": escape_untrusted(self.inputs.diff_block),
        }

    def review(self, pass_no: int, raw_path: Path) -> ClaudeResult:
        spec = CallSpec(
            role="reviewer", pass_no=pass_no,
            system=render(self.prompt.system, {}, {"self_check": self.cfg.self_check}),
            user=render(self.prompt.user, self._variables(pass_no)),
            schema=self.prompt.schema, model=self.cfg.model_reviewer, tools=self.TOOLS, cwd=self.inputs.checkout,
        )
        return self.caller.call(spec, requires_lists("findings"), raw_path)


class Comparator:
    """Maps this pass's findings onto the master list. It sees findings only, never the code, and has no tools."""

    def __init__(self, caller: ClaudeCaller, prompt: PromptSet, cfg: Config, work_dir: Path):
        self.caller, self.prompt, self.cfg, self.work_dir = caller, prompt, cfg, work_dir

    @staticmethod
    def is_trivial(findings: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]]) -> bool:
        """With nothing to compare against (or no findings) every finding is new and no model call is needed."""
        return not findings or (not candidates and len(findings) == 1)

    @staticmethod
    def all_new(findings: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {f["local_id"]: dict(NEW_DECISION) for f in findings}

    def compare(self, pass_no: int, findings: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]],
                raw_path: Path) -> Dict[str, Dict[str, Any]]:
        new_json = json.dumps([compact(f, f["local_id"], "local_id") for f in findings], indent=2, ensure_ascii=False)
        cand_json = json.dumps([compact(c["canonical"], c["id"], "id") for c in candidates], indent=2, ensure_ascii=False)
        user = render(self.prompt.user, {
            "pass_number": pass_no,
            "new_findings_json": escape_untrusted(new_json),
            "candidate_findings_json": escape_untrusted(cand_json),
        })
        candidate_ids = {c["id"] for c in candidates}
        spec = CallSpec(
            role="comparator", pass_no=pass_no, system=render(self.prompt.system, {}, {}), user=user,
            schema=self.prompt.schema, model=self.cfg.model_comparator, tools="", cwd=self.work_dir,
        )
        result = self.caller.call(spec, lambda parsed: validate_decisions(findings, candidate_ids, parsed)[1], raw_path)
        decisions, _ = validate_decisions(findings, candidate_ids, result.parsed)
        return decisions
