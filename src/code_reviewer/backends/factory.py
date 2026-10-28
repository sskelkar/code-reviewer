"""Select a runner after operator options are resolved; preflight never reads credentials."""
from __future__ import annotations

import os

from ..config import ConfigError
from ..shared.claude_cli import ClaudeRunner
from .openai import OpenAIRunner, TokenPrices


def configuration_errors(cfg, *, dry_run=False):
    if cfg.provider != 'openai':
        return []
    models = [getattr(cfg, 'model_' + role) for role in ('reviewer', 'resolver', 'comparator', 'verifier')]
    errors = []
    if any(not name.strip() or name in ('haiku', 'sonnet', 'opus') for name in models):
        errors.append('select explicit OpenAI model IDs; Claude aliases are not mapped')
    if len(set(models)) != 1:
        errors.append('the current OpenAI adapter requires one model for all roles; use --model MODEL_ID')
    if not dry_run:
        try:
            prices(cfg)
        except ValueError:
            errors.append('set positive openai_input_price, openai_cached_input_price and openai_output_price (USD per million tokens)')
    return errors


def prices(cfg):
    return TokenPrices(cfg.openai_input_price, cfg.openai_cached_input_price, cfg.openai_output_price)


def create_runner(cfg):
    errors = configuration_errors(cfg)
    if errors:
        raise ConfigError('; '.join(errors))
    if cfg.provider == 'claude':
        return ClaudeRunner(cfg.claude_bin, cfg.isolation_flags)
    if cfg.provider != 'openai':
        raise ConfigError('unsupported provider; no fallback')
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ConfigError('set a valid OPENAI_API_KEY in the review process environment')
    return OpenAIRunner(key, model=cfg.model_reviewer, prices=prices(cfg))
