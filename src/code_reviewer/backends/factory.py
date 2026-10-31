"""One adapter factory for host and guest; selection/validation precedes credentials."""
from __future__ import annotations

import os

from ..config import ConfigError
from .claude import ClaudeRunner, build_command
from .contract import Runner
from .openai import OpenAIRunner, TokenPrices

ENVIRONMENT = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TERM', 'SSL_CERT_FILE',
               'SSL_CERT_DIR', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'CLAUDE_CONFIG_DIR',
               'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'PYTHONDONTWRITEBYTECODE',
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'DISABLE_AUTOUPDATER')
CREDENTIALS = {('claude', 'api-key'): 'ANTHROPIC_API_KEY',
               ('claude', 'subscription'): 'CLAUDE_CODE_OAUTH_TOKEN',
               ('openai', 'api-key'): 'OPENAI_API_KEY'}


def configuration_errors(cfg, *, dry_run=False):
    errors = []
    if (cfg.provider, cfg.auth_mode) not in CREDENTIALS:
        errors.append('ChatGPT subscription login/streaming is not implemented; OpenAI currently supports auth_mode = "api-key" only; no billing fallback')
    if cfg.provider != 'openai':
        return errors
    models = [getattr(cfg, 'model_' + role) for role in ('reviewer', 'resolver', 'comparator', 'verifier')]
    if any(not name.strip() or name in ('haiku', 'sonnet', 'opus') for name in models):
        errors.append('select explicit OpenAI model IDs; Claude aliases are not mapped')
    if len(set(models)) != 1:
        errors.append('the current OpenAI adapter requires one model for all roles; use --model MODEL_ID')
    if not dry_run and cfg.auth_mode == 'api-key':
        try:
            prices(cfg)
        except ValueError:
            errors.append('set positive openai_input_price, openai_cached_input_price and openai_output_price (USD per million tokens)')
    return errors


def prices(cfg):
    return TokenPrices(cfg.openai_input_price, cfg.openai_cached_input_price, cfg.openai_output_price)


def credential_name(provider, auth='api-key'):
    try:
        return CREDENTIALS[(provider, auth)]
    except KeyError:
        raise ConfigError('unsupported provider/authentication combination; no fallback') from None


def credential(provider, auth='api-key'):
    name = credential_name(provider, auth)
    return validate_credential(os.environ.get(name, ''), name)


def validate_credential(value, name):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ConfigError('set a valid %s in the review process environment' % name)
    return value


def create_runner(cfg, *, selected_credential=None, env=None, transport=None) -> Runner:
    failures = configuration_errors(cfg)
    if failures:
        raise ConfigError('; '.join(failures))
    name = credential_name(cfg.provider, cfg.auth_mode)
    secret = credential(cfg.provider, cfg.auth_mode) if selected_credential is None else validate_credential(selected_credential, name)
    if cfg.provider == 'claude':
        source = os.environ if env is None else env
        # Never forward ambient keys, gateway switches, NODE_OPTIONS or loader
        # variables. In particular an API key must not override a subscription.
        native_env = {key: source[key] for key in ENVIRONMENT if key in source}
        native_env[name] = secret
        return ClaudeRunner(cfg.claude_bin, cfg.isolation_flags, env=native_env, billing_mode=cfg.auth_mode)
    return OpenAIRunner(secret, model=cfg.model_reviewer, prices=prices(cfg), transport=transport)


def preview_command(cfg, tools):
    """Credential-free request preview; review algorithms know no native flags."""
    if cfg.provider == 'openai':
        return ['POST', 'https://api.openai.com/v1/responses', 'model=' + cfg.model_reviewer]
    command = build_command(cfg.claude_bin, system_prompt='', schema={}, model=cfg.model_reviewer,
                            tools=tools, budget_usd=cfg.call_budget_usd, isolation_flags=cfg.isolation_flags)
    placeholders = {'--append-system-prompt': '<system prompt: input/reviewer.system.md>',
                    '--json-schema': '<JSON schema of the findings>'}
    return [placeholders.get(command[i-1], arg) if i else arg for i,arg in enumerate(command)]
