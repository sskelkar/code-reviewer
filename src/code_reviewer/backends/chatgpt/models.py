"""Bounded discovery of models explicitly visible to the selected subscription."""
from __future__ import annotations

import http.client
import json
import time

from ...config import ConfigError
from ..http_errors import diagnostics, error_message


def catalog(token):
    connection = http.client.HTTPSConnection('api.openai.com', timeout=20)
    try:
        connection.request('GET', '/v1/models', headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        if response.status != 200:
            raise ConfigError(error_message(diagnostics(response)))
        data = bytearray()
        deadline = time.monotonic() + 20
        while len(data) <= 1024 * 1024:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, 1024 * 1024 + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > 1024 * 1024:
            raise ValueError
        value = json.loads(data)
        models = value.get('models')
        if not isinstance(models, list) or len(models) > 2000:
            raise ValueError
        result = []
        for item in models:
            if not isinstance(item, dict):
                raise ValueError
            if item.get('visibility') == 'list':
                slug = item.get('slug')
                if not isinstance(slug, str) or not 1 <= len(slug) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in slug):
                    raise ValueError
                result.append(slug)
        return sorted(set(result))
    except (OSError, ValueError, AttributeError, RecursionError, http.client.HTTPException) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError('ChatGPT model catalog failed; response contents were not logged') from None
    finally:
        connection.close()
