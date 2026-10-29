"""Explicit operator settings cross the boundary; host paths and credentials do not."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from ..config import Config, ConfigError, _coerce
from ..backends.factory import configuration_errors

# All algorithm options travel together. Infrastructure and host executable paths
# are deliberately absent, even when new Config fields are added later.
CORE = ('provider', 'review_mode', 'model_reviewer', 'model_comparator', 'model_resolver', 'model_verifier',
        'self_check', 'criteria_mode', 'criteria_manifest', 'disabled_default_rules', 'min_passes', 'max_passes',
        'quiet_k', 'max_consecutive_failures', 'tier_high', 'tier_medium', 'candidate_window',
        'candidate_all_threshold', 'candidate_max', 'max_diff_lines', 'criteria_max_chars', 'exclude_patterns',
        'extra_exclude_patterns', 'grounding_line_window', 'redact_emails', 'call_budget_usd', 'call_timeout_s',
        'run_budget_usd', 'run_budget_tokens', 'post_tiers', 'persona_prompt_max_chars', 'persona_passes',
        'persona_parallel', 'openai_input_price', 'openai_cached_input_price', 'openai_output_price')
ENDPOINTS = {'openai': ('api.openai.com',), 'claude': ('api.anthropic.com', 'platform.claude.com')}


def credential_name(provider, auth='api-key'):
    if provider == 'openai':
        return 'OPENAI_API_KEY'
    if provider == 'claude' and auth in ('api-key', 'oauth'):
        return 'ANTHROPIC_API_KEY' if auth == 'api-key' else 'CLAUDE_CODE_OAUTH_TOKEN'
    raise ValueError('unsupported credential selection')


def credential(provider, auth='api-key'):
    name = credential_name(provider, auth)
    value = os.environ.get(name, '')
    if not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ConfigError('set a valid %s in the review process environment' % name)
    return value


def errors(cfg):
    result = []
    if sys.platform != 'darwin':
        result.append('VM review currently requires macOS and the dedicated ARM64 Lima worker')
    for name in ('vm_artifacts', 'vm_lima_home') + (('vm_claude_binary',) if cfg.provider == 'claude' else ()):
        value = getattr(cfg, name)
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            result.append('set %s to an absolute operator path' % name)
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', cfg.vm_instance):
        result.append('invalid vm_instance')
    if type(cfg.vm_timeout_s) is not int or not 60 <= cfg.vm_timeout_s <= 1800:
        result.append('vm_timeout_s must be 60..1800')
    if cfg.provider == 'claude':
        if not re.fullmatch('[a-f0-9]{64}', cfg.vm_claude_sha256):
            result.append('set vm_claude_sha256 to the verified Linux ARM64 binary digest')
        if cfg.vm_claude_auth not in ('api-key', 'oauth'):
            result.append('vm_claude_auth must be api-key or oauth; no automatic billing fallback')
    return result


def job_settings(cfg):
    return {'config': {name: getattr(cfg, name) for name in CORE}, 'auth': cfg.vm_claude_auth if cfg.provider == 'claude' else 'api-key',
            'probe_only': False, 'timeout_s': cfg.vm_timeout_s}


def config_from_job(value, root):
    if not isinstance(value, dict) or set(value) != {'config', 'auth', 'probe_only', 'timeout_s'} or value['probe_only'] is not False:
        raise ValueError('invalid review job settings')
    if type(value['timeout_s']) is not int or not 60 <= value['timeout_s'] <= 1800:
        raise ValueError('invalid whole-job timeout')
    raw = value['config']
    if not isinstance(raw, dict) or set(raw) != set(CORE) or value['auth'] not in ('api-key', 'oauth'):
        raise ValueError('incomplete review job settings')
    cfg = Config()
    for name in CORE:
        setattr(cfg, name, _coerce(name, raw[name], getattr(cfg, name)))
    cfg.validate()
    failures = configuration_errors(cfg)
    if failures:
        raise ConfigError('; '.join(failures))
    if cfg.criteria_manifest:
        raise ValueError('criteria_manifest is host-only; VM reviews discover criteria from the pinned base commit')
    cfg.claude_bin = '/usr/bin/claude'
    cfg.runs_dir = str(Path(root) / 'runs')
    return cfg


def provider(value):
    return value['config']['provider'] if 'config' in value else 'openai'
