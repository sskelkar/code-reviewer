"""Explicit execution dispatch; preflight never prepares target code or credentials."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .shared.claude_cli import ClaudeError
from .config import Config, ConfigError
from .shared.git_ops import GitError, PRRef
from .shared.models import ReviewRefused
from .backends import factory
from .engine import Engine
from .progress import ProgressPrinter

# What a review can fail with in an expected way. Frontends catch these and report them as an error message.
REVIEW_ERRORS = (GitError, ConfigError, ReviewRefused, ClaudeError)


@dataclass
class ReviewOutcome:
    """Shared frontend presentation data for preflight, dry-run or completed review."""

    dry_run: Optional[Any] = None
    result: Optional[Any] = None
    preflight: Optional[Any] = None


def run_review(cfg: Config, runner: Any, ref: PRRef, options: Any, local_path: Path) -> ReviewOutcome:
    """Select the operator's boundary before Git or credentials; VM requests never fall back."""
    from .security import readiness
    report = readiness(cfg, ref, options)
    report['request'] = {'repository': ref.slug, 'pr': ref.number, 'host': ref.host,
                         'mode': cfg.review_mode, 'model': cfg.model_reviewer,
                         'passes': cfg.persona_passes if cfg.review_mode == 'agent-personas' else cfg.max_passes,
                         'parallel': cfg.persona_parallel if cfg.review_mode == 'agent-personas' else 1,
                         'run_budget_usd': cfg.run_budget_usd, 'call_budget_usd': cfg.call_budget_usd,
                         'models': {role: getattr(cfg, 'model_' + role)
                                    for role in ('reviewer', 'resolver', 'comparator', 'verifier')}}
    if getattr(options, 'preflight', False):
        return ReviewOutcome(preflight=report)
    if cfg.sandbox:
        if report['configuration_errors']:
            raise ReviewRefused('VM review unavailable: ' + '; '.join(report['configuration_errors']) + '; no host fallback')
        from .sandbox.review import run
        return ReviewOutcome(result=run(cfg, ref, options))
    if report['configuration_errors']:
        raise ConfigError('; '.join(report['configuration_errors']))
    dry_run = getattr(options, 'dry_run', False)
    if runner is None and not dry_run:
        runner = factory.create_runner(cfg)
    engine = Engine(cfg, runner, emit=ProgressPrinter(verbose=getattr(options, 'verbose', False)))
    ctx = engine.prepare(ref, local_path)
    if dry_run:
        return ReviewOutcome(dry_run=engine.dry_run(ctx))
    return ReviewOutcome(result=engine.review(ctx, keep_checkout=getattr(options, 'keep_checkout', False)))
