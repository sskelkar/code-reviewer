"""One adapter factory for host and guest; selection/validation precedes credentials."""
from __future__ import annotations

import os

from ..config import ConfigError
from .claude import ClaudeRunner, build_command, require_subscription_login
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


def credential(provider, auth='api-key', *, cfg=None, min_ttl=60):
    if (provider, auth) == ('openai', 'subscription'):
        if cfg is None:
            raise ConfigError('ChatGPT runtime configuration required; no credential fallback')
        from .chatgpt.store import Store, runtime_key
        return Store().access(runtime_key(cfg), cfg.chatgpt_account, min_ttl)
    name = credential_name(provider, auth)
    return validate_credential(os.environ.get(name, ''), name)


def validate_credential(value, name, limit=4096):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ConfigError('set a valid %s in the review process environment' % name)
    return value


def create_runner(cfg, *, selected_credential=None, env=None, transport=None, proxy=None) -> Runner:
    failures = configuration_errors(cfg)
    if failures:
        raise ConfigError('; '.join(failures))
    subscription = cfg.provider == 'openai' and cfg.auth_mode == 'subscription'
    if subscription:
        token_source = (lambda: credential('openai', 'subscription', cfg=cfg, min_ttl=cfg.call_timeout_s + 120)) if selected_credential is None else None
        secret = token_source() if token_source else validate_credential(selected_credential, 'ChatGPT access token', 16384)
        return OpenAIRunner(secret, model=cfg.model_reviewer, transport=transport, proxy=proxy,
                            billing_mode='subscription', token_source=token_source, token_limit=cfg.call_budget_tokens)
    name = credential_name(cfg.provider, cfg.auth_mode)
    if cfg.provider == 'claude':
        source = os.environ if env is None else env
        # Never forward ambient keys, gateway switches, NODE_OPTIONS or loader
        # variables. In particular an API key must not override a subscription.
        native_env = {key: source[key] for key in ENVIRONMENT if key in source}
        native_login = (not cfg.sandbox and cfg.auth_mode == 'subscription'
                        and selected_credential is None and name not in os.environ)
        if native_login:
            require_subscription_login(cfg.claude_bin, native_env, cfg.isolation_flags)
        else:
            native_env[name] = (credential(cfg.provider, cfg.auth_mode) if selected_credential is None
                                else validate_credential(selected_credential, name))
        return ClaudeRunner(cfg.claude_bin, cfg.isolation_flags, env=native_env, billing_mode=cfg.auth_mode)
    secret = credential(cfg.provider, cfg.auth_mode) if selected_credential is None else validate_credential(selected_credential, name)
    return OpenAIRunner(secret, model=cfg.model_reviewer, prices=prices(cfg), transport=transport, proxy=proxy)


def preview_command(cfg, tools):
    """Credential-free request preview; review algorithms know no native flags."""
    if cfg.provider == 'openai':
        return ['POST', 'https://api.openai.com/v1/responses', 'model=' + cfg.model_reviewer]
    command = build_command(cfg.claude_bin, system_prompt='', schema={}, model=cfg.model_reviewer,
                            tools=tools, budget_usd=None if cfg.auth_mode == 'subscription' else cfg.call_budget_usd, isolation_flags=cfg.isolation_flags)
    placeholders = {'--append-system-prompt': '<system prompt: input/reviewer.system.md>',
                    '--json-schema': '<JSON schema of the findings>'}
    return [placeholders.get(command[i-1], arg) if i else arg for i,arg in enumerate(command)]
