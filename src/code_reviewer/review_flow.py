"""One release boundary for CLI and REPL; preflight never prepares target code."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .shared.claude_cli import ClaudeError
from .config import Config, ConfigError
from .shared.git_ops import GitError, PRRef
from .shared.models import ReviewRefused

# What a review can fail with in an expected way. Frontends catch these and report them as an error message.
REVIEW_ERRORS = (GitError, ConfigError, ReviewRefused, ClaudeError)


@dataclass
class ReviewOutcome:
    """Frontend presentation data; production currently returns only preflight or refusal."""

    dry_run: Optional[Any] = None
    result: Optional[Any] = None
    preflight: Optional[Any] = None


def run_review(cfg: Config, runner: Any, ref: PRRef, options: Any, local_path: Path) -> ReviewOutcome:
    """Check the shared release boundary before Git, target IO, model calls or credentials."""
    from .security import readiness, require_ready
    report = readiness(cfg.provider)
    report['request'] = {'repository': ref.slug, 'pr': ref.number, 'host': ref.host,
                         'mode': cfg.review_mode, 'model': cfg.model_reviewer,
                         'passes': cfg.persona_passes if cfg.review_mode == 'agent-personas' else cfg.max_passes,
                         'parallel': cfg.persona_parallel if cfg.review_mode == 'agent-personas' else 1,
                         'run_budget_usd': cfg.run_budget_usd, 'call_budget_usd': cfg.call_budget_usd,
                         'models': {role: getattr(cfg, 'model_' + role)
                                    for role in ('reviewer', 'resolver', 'comparator', 'verifier')}}
    report['configuration_errors'] = []
    if cfg.provider == 'openai' and any(name in ('haiku', 'sonnet', 'opus') for name in report['request']['models'].values()):
        report['configuration_errors'].append('select explicit OpenAI model IDs; Claude aliases are not mapped')
        report['ready'] = False
        report['blockers'].append('model-configuration')
    if getattr(options, 'preflight', False):
        return ReviewOutcome(preflight=report)
    require_ready(cfg.provider)
    # No qualified execution backend exists yet. Never resume the legacy host
    # prepare/review path merely because a release-status row changes.
    raise ReviewRefused('qualified sandbox workflow is not integrated; no host fallback')
