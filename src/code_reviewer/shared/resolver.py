"""Project-criteria discovery reused by review flows."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from ..config import Config
from .llm import CallSpec, ClaudeCaller, requires_lists
from .models import PRContext
from .templates import PromptSet, escape_untrusted, render


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
