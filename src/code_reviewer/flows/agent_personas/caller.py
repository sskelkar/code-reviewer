"""Enforce persona run limits at every invocation and save actual requests, including retries."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ...shared.llm import ModelCaller
from ...shared.run_store import dump_json
from ...shared.models import PassFailed

from .budget import Budget, RunStopped


class BudgetRunner:
    def __init__(self, runner: Any, cfg: Any, stats: Any, budget: Any):
        self.runner, self.cfg, self.stats = runner, cfg, stats
        self.budget, self.pool = budget, 'criteria'
        self.requests: List[Dict[str, Any]] = []
        self.path: Any = None

    def check(self) -> None:
        self.budget.check(self.pool)

    def run(self, **kwargs: Any) -> Any:
        self.check()
        self.requests.append({**{k: str(v) if isinstance(v, Path) else v for k, v in kwargs.items()}, 'invoked': False})
        dump_json(self.path, self.requests)
        if len(kwargs['system_prompt']) + len(kwargs['user_prompt']) > self.cfg.persona_prompt_max_chars:
            raise PassFailed('retry prompt exceeds persona_prompt_max_chars; not sent or truncated')
        allowance = self.budget.reserve(self.pool, kwargs['budget_usd'])
        kwargs['budget_usd'] = allowance
        self.requests[-1]['budget_usd'] = allowance
        self.requests[-1]['invoked'] = True
        dump_json(self.path, self.requests)
        result = None
        try:
            result = self.runner.run(**kwargs)
            return result
        finally:
            self.budget.settle(self.pool, allowance, result)


class PersonaCaller(ModelCaller):
    def __init__(self, runner: Any, cfg: Any, stats: Any, budget: Any = None, persona: str = ''):
        self.persona = persona
        self.budget = budget or Budget(cfg)
        self.guard = BudgetRunner(runner, cfg, stats, self.budget)
        super().__init__(self.guard, cfg, stats)
        self.diagnostics: List[Dict[str, Any]] = []

    def call(self, spec: Any, validate: Any, raw_path: Path) -> Any:
        self.guard.pool = self.persona if spec.role == 'reviewer' else spec.role if spec.role in ('comparator', 'verifier') else 'criteria'
        self.guard.path = raw_path.with_name(raw_path.stem + '.requests.json')
        self.guard.requests = []
        if len(spec.system) + len(spec.user) > self.cfg.persona_prompt_max_chars:
            message = '%s prompt exceeds persona_prompt_max_chars; complete input was not sent or truncated' % spec.role
            self.diagnostics.append({'role': spec.role, 'pass': spec.pass_no, 'errors': [message]})
            raise PassFailed(message)

        def checked(payload: Any) -> List[str]:
            errors = validate(payload)
            if errors:
                self.diagnostics.append({'role': spec.role, 'pass': spec.pass_no, 'errors': errors})
            return errors

        try:
            return super().call(spec, checked, raw_path)
        except RunStopped as exc:
            if exc.pool == 'criteria':
                raise PassFailed(str(exc)) from exc
            raise
