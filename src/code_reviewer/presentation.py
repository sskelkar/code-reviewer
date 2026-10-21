"""Frontends dispatch presentation once; each workflow owns how its result is shown."""
from __future__ import annotations

from typing import Any

TIERS = ('High', 'Medium', 'Low')


def _renderer(mode: str) -> Any:
    if mode == 'agent-personas':
        from .flows.agent_personas import report
        return report
    from .flows.single_agent import summary
    return summary


def render_summary(result: Any, tiers: Any, dropped: Any) -> str:
    return _renderer(result.cfg.review_mode).render_summary(result, tiers, dropped)


def run_line(result: Any) -> str:
    return _renderer(result.cfg.review_mode).run_line(result)


def render_dry_run(dry: Any) -> str:
    if getattr(dry, 'review_mode', '') == 'agent-personas':
        return _renderer('agent-personas').render_dry_run(dry)
    from .flows.single_agent.dry_run import render_dry_run as render
    return render(dry)


def render_finding(result: Any, item: Any) -> str:
    if result.cfg.review_mode == 'agent-personas':
        return _renderer('agent-personas').render_finding(result, item)
    from .flows.single_agent.report import render_finding as render
    return render(item, result.passes_completed, result.criteria.index())
