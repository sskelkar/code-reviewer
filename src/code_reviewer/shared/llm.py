"""One place where claude calls are made: retry, usage accounting and raw-output logging."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .claude_cli import ClaudeResult
from ..config import Config
from .models import PassFailed
from .run_store import dump_json
from .stats import Stats

Validator = Callable[[Dict[str, Any]], List[str]]


@dataclass
class CallSpec:
    """Everything that defines one claude call."""

    role: str
    pass_no: Optional[int]
    system: str
    user: str
    schema: Dict[str, Any]
    model: str
    tools: str
    cwd: Path


def requires_lists(*keys: str) -> Validator:
    """Validator: the output must be an object whose given keys are lists."""

    def validate(parsed: Dict[str, Any]) -> List[str]:
        return ["output must contain a '%s' list" % key for key in keys if not isinstance(parsed.get(key), list)]

    return validate


class ClaudeCaller:
    """Runs claude calls. A call is retried once if it fails or its output does not validate."""

    def __init__(self, runner: Any, cfg: Config, stats: Stats):
        self.runner = runner
        self.cfg = cfg
        self.stats = stats

    def call(self, spec: CallSpec, validate: Validator, raw_path: Path) -> ClaudeResult:
        """Return a validated result, or raise PassFailed. Every attempt is logged to `raw_path`."""
        raw_log: List[Any] = []
        try:
            return self._attempts(spec, validate, raw_log)
        finally:
            dump_json(raw_path, raw_log)

    def _attempts(self, spec: CallSpec, validate: Validator, raw_log: List[Any]) -> ClaudeResult:
        feedback = ""
        problems: List[str] = ["unknown error"]
        for attempt in (1, 2):
            result = self.runner.run(
                system_prompt=spec.system, user_prompt=spec.user + feedback, schema=spec.schema, model=spec.model,
                tools=spec.tools, cwd=spec.cwd, budget_usd=self.cfg.call_budget_usd, timeout_s=self.cfg.call_timeout_s,
            )
            self.stats.record(spec.role, spec.pass_no, result, attempt, spec.model)
            raw_log.append({"attempt": attempt, "ok": result.ok, "error": result.error, "envelope": result.raw})

            problems = [result.error or "call failed"] if not result.ok else validate(result.parsed or {})
            if not problems:
                return result
            if result.budget_exceeded:
                break  # retrying would only spend more
            if result.ok:  # the model answered but badly: tell it what was wrong
                feedback = "\n\nYour previous answer was rejected: %s. Return a corrected JSON object." % "; ".join(problems)[:500]
        raise PassFailed("%s: %s" % (spec.role, "; ".join(problems)))
