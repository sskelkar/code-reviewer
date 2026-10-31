"""Bounded HTTP diagnostics: export closed codes and validated headers, never error text."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

MAX_ERROR_BYTES = 8192
CODES = frozenset(('rate_limit_exceeded', 'slow_down', 'insufficient_quota',
                  'credit_balance_exhausted', 'organization_spend_limit_exceeded',
                  'project_spend_limit_exceeded', 'organization_usage_limit_exceeded',
                  'server_is_overloaded', 'invalid_api_key'))
QUOTA_CODES = CODES - {'rate_limit_exceeded', 'slow_down', 'server_is_overloaded', 'invalid_api_key'}
TYPES = frozenset(('rate_limit_error', 'insufficient_quota', 'service_unavailable_error',
                  'invalid_request_error', 'authentication_error'))
HEADERS = ('x-ratelimit-limit-requests', 'x-ratelimit-limit-tokens',
           'x-ratelimit-remaining-requests', 'x-ratelimit-remaining-tokens',
           'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens',
           'x-ratelimit-limit-project-tokens', 'x-ratelimit-remaining-project-tokens',
           'x-ratelimit-reset-project-tokens')


def retry_after(value):
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        if re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,3})?', value):
            seconds = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        return seconds if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def diagnostics(response):
    code = type_ = None
    # Redirect bodies are never read. Other errors are bounded before JSON parsing;
    # message/param/body/headers outside the closed sets are never retained.
    if response.status in (400, 401, 403, 429, 500, 502, 503, 504):
        data = response.read(MAX_ERROR_BYTES + 1)
        if len(data) <= MAX_ERROR_BYTES:
            try:
                value = json.loads(data)
                error = value.get('error') if isinstance(value, dict) else None
                if isinstance(error, dict):
                    c, t = error.get('code'), error.get('type')
                    code = c if isinstance(c, str) and c in CODES else None
                    type_ = t if isinstance(t, str) and t in TYPES else None
            except (ValueError, UnicodeError, RecursionError):
                pass
    get = getattr(response, 'getheader', lambda name: None)
    headers = {}
    for name in HEADERS:
        value = get(name)
        pattern = r'(?:[0-9]{1,6}(?:\.[0-9]{1,3})?(?:ms|s|m|h|d)){1,5}' if '-reset-' in name else r'[0-9]{1,15}'
        if isinstance(value, str) and len(value) <= 40 and re.fullmatch(pattern, value):
            headers[name] = value
    delay = retry_after(get('Retry-After'))
    if delay is not None:
        headers['retry_after_s'] = delay
    return {'status': response.status, 'code': code, 'type': type_, 'headers': headers}


def temporary(details):
    if details['code'] in QUOTA_CODES or details['type'] == 'insufficient_quota':
        return False
    return details['status'] in (429, 500, 502, 503, 504)


def error_message(details):
    message = 'OpenAI HTTP status %d' % details['status']
    if details['code']:
        message += ' (' + details['code'] + ')'
    elif details['type']:
        message += ' (' + details['type'] + ')'
    if details['code'] in QUOTA_CODES or details['type'] == 'insufficient_quota':
        message += '; check API credits and organization/project limits; retrying will not restore access'
    elif details['status'] == 429 and not details['code'] and not details['type']:
        message += '; rate limit or quota could not be distinguished'
    return message
