"""Responses API adapter shared by execution paths; no SDK or model command tools."""
from __future__ import annotations

import copy
import http.client
import json
import math
import random
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from .contract import LLMResult
from .read_tools import ReadOnlyTools, definitions
from .http_errors import diagnostics, error_message, temporary

MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_TURNS = 8
MAX_TOOL_CALLS = 32
MAX_OUTPUT_TOKENS = 2048
MAX_HTTP_ATTEMPTS = 3
MAX_RETRY_DELAY = 60.0


class ProviderError(ValueError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(self, details):
        self.details = details
        super().__init__(error_message(details))


@dataclass(frozen=True)
class TokenPrices:
    """Operator-supplied USD per million tokens; estimates, never invoice amounts."""
    input: float
    cached_input: float
    output: float

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
               for v in (self.input, self.cached_input, self.output)):
            raise ValueError('all token prices must be finite and positive')

    def cost(self, input_, cached, output):
        return ((input_ - cached) * self.input + cached * self.cached_input + output * self.output) / 1000000


def strict_schema(schema):
    """Require every declared field without mutating the shared prompt schema."""
    result = copy.deepcopy(schema)
    def visit(value):
        if isinstance(value, dict):
            if value.get('type') == 'object':
                value['additionalProperties'] = False
                value['required'] = list(value.get('properties', {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result)
    return result


def request_response(payload, key, timeout, *, proxy=None):
    """Fixed TLS endpoint; ignore ambient proxy/base-URL settings and refuse redirects."""
    body = json.dumps(payload).encode('utf-8')
    if len(body) > MAX_REQUEST:
        raise ProviderError('OpenAI request size limit exceeded')
    started = time.monotonic()
    if proxy is None:
        connection = http.client.HTTPSConnection('api.openai.com', timeout=min(timeout, 30))
    else:
        if proxy != ('192.0.2.1', 3128):
            raise ProviderError('unsupported trusted proxy route')
        connection = http.client.HTTPSConnection(proxy[0], proxy[1], timeout=min(timeout, 30))
        connection.set_tunnel('api.openai.com', 443)
    try:
        connection.request('POST', '/v1/responses', body,
                           {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ProviderHTTPError(diagnostics(response))
        chunks, size = [], 0
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(min(remaining, 30))
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise ProviderError('OpenAI response size limit exceeded')
        data = json.loads(b''.join(chunks))
        if not isinstance(data, dict):
            raise ProviderError('invalid OpenAI response envelope')
        return data
    finally:
        connection.close()


class OpenAIRunner:
    def __init__(self, api_key, *, model, prices, transport=None):
        if not isinstance(api_key, str) or not api_key.strip() or any(c in api_key for c in '\r\n'):
            raise ValueError('a nonempty runtime API key is required')
        if not isinstance(model, str) or not model.strip() or model in ('haiku', 'sonnet', 'opus'):
            raise ValueError('select an explicit OpenAI model')
        if not isinstance(prices, TokenPrices):
            raise ValueError('explicit token prices are required')
        self._api_key, self.model, self.prices = api_key, model, prices
        self._transport = transport or request_response

    def version(self):
        return 'OpenAI Responses adapter v1 (experimental)'

    def run(self, **kwargs):
        return self.run_cancellable(None, **kwargs)

    def _request(self, payload, deadline, cancelled, errors):
        # Retry the same HTTP request, not a fresh agent conversation. All waits
        # share the existing call deadline and no extra budget is allocated.
        for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
            if cancelled is not None and cancelled.is_set():
                raise KeyboardInterrupt
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                return self._transport(payload, self._api_key, remaining)
            except ProviderHTTPError as exc:
                detail = dict(exc.details, turn_attempt=attempt)
                errors.append(detail)
                if not temporary(exc.details) or attempt == MAX_HTTP_ATTEMPTS:
                    detail['retry'] = 'stopped'
                    raise
                hint = exc.details['headers'].get('retry_after_s')
                delay = (hint if hint is not None else 2 ** (attempt - 1)) + random.uniform(0, .25)
                if delay > MAX_RETRY_DELAY or delay >= deadline - time.monotonic():
                    detail['retry'] = 'deferred: delay exceeds call deadline or 60-second wait limit'
                    raise
                detail.update(retry='waiting', retry_delay_s=delay)
                if cancelled is None:
                    time.sleep(delay)
                elif cancelled.wait(delay):
                    raise KeyboardInterrupt

    def run_cancellable(self, cancelled, *, system_prompt, user_prompt, schema, model, tools, cwd,
                        budget_usd, timeout_s, add_dirs=()):
        result = LLMResult(ok=False, model=self.model, cost_usd=0.0, billing_mode='api-key')
        started = time.monotonic()
        pending = False
        http_errors = []
        try:
            if model != self.model or add_dirs or not math.isfinite(budget_usd) or budget_usd <= 0 or timeout_s <= 0:
                raise ProviderError('invalid OpenAI call configuration')
            names = [name for name in tools.split(',') if name]
            if len(set(names)) != len(names) or set(names) - {'Read', 'Grep', 'Glob'}:
                raise ProviderError('only Read, Grep and Glob are permitted')
            files = ReadOnlyTools(Path(cwd)) if names and cwd is not None else None
            if names and files is None:
                raise ProviderError('file tools require a checkout')
            history = [{'role': 'user', 'content': user_prompt}]
            tool_count = 0
            for turn in range(MAX_TURNS):
                if cancelled is not None and cancelled.is_set():
                    raise KeyboardInterrupt
                remaining = timeout_s - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError
                payload = {'model': self.model, 'instructions': system_prompt, 'input': history,
                           'store': False, 'include': ['reasoning.encrypted_content'],
                           'tools': definitions(names), 'parallel_tool_calls': False,
                           'max_output_tokens': MAX_OUTPUT_TOKENS,
                           'text': {'format': {'type': 'json_schema', 'name': 'review_result',
                                               'strict': True, 'schema': strict_schema(schema)}}}
                request_size = len(json.dumps(payload).encode('utf-8'))
                if request_size > MAX_REQUEST:
                    raise ProviderError('OpenAI request size limit exceeded')
                # Bytes plus overhead is a conservative admission estimate, not a provider-enforced cap.
                reserve = self.prices.cost(request_size + 1024, 0, MAX_OUTPUT_TOKENS)
                if result.cost_usd + reserve > budget_usd:
                    result.budget_exceeded = True
                    raise ProviderError('OpenAI call allowance insufficient for another request')
                pending = True
                reply = self._request(payload, started + timeout_s, cancelled, http_errors)
                if not isinstance(reply, dict):
                    raise ProviderError('invalid OpenAI response envelope')
                usage = reply.get('usage')
                if not isinstance(usage, dict):
                    raise ProviderError('OpenAI usage missing; cost unknown')
                input_, output = usage.get('input_tokens'), usage.get('output_tokens')
                details = usage.get('input_tokens_details') or {}
                if not isinstance(details, dict):
                    raise ProviderError('invalid OpenAI token usage')
                cached = details.get('cached_tokens', 0)
                if any(type(v) is not int or v < 0 for v in (input_, cached, output)) or cached > input_:
                    raise ProviderError('invalid OpenAI token usage')
                result.tokens['input'] += input_ - cached
                result.tokens['cache_read'] += cached
                result.tokens['output'] += output
                result.cost_usd += self.prices.cost(input_, cached, output)
                result.num_turns = turn + 1
                pending = False
                if time.monotonic() - started >= timeout_s:
                    raise TimeoutError
                if cancelled is not None and cancelled.is_set():
                    raise KeyboardInterrupt
                if result.cost_usd > budget_usd:
                    result.budget_exceeded = True
                    raise ProviderError('OpenAI estimated call allowance exceeded')
                if reply.get('status') != 'completed' or not isinstance(reply.get('output'), list):
                    raise ProviderError('OpenAI response did not complete')
                items = reply['output']
                calls = [item for item in items if isinstance(item, dict) and item.get('type') == 'function_call']
                if calls:
                    tool_count += len(calls)
                    if tool_count > MAX_TOOL_CALLS:
                        raise ProviderError('OpenAI tool-call limit exceeded')
                    # Preserve output items, including encrypted reasoning, for a stateless continuation.
                    history.extend(items)
                    for call in calls:
                        if call.get('name') not in names or not isinstance(call.get('call_id'), str):
                            raise ProviderError('OpenAI requested an unavailable tool')
                        arguments = json.loads(call.get('arguments', ''))
                        history.append({'type': 'function_call_output', 'call_id': call['call_id'],
                                        'output': files.execute(call['name'], arguments)})
                    continue
                texts = []
                for item in items:
                    if not isinstance(item, dict):
                        raise ProviderError('invalid OpenAI output item')
                    if item.get('type') == 'message':
                        for content in item.get('content', []):
                            if content.get('type') == 'refusal':
                                raise ProviderError('OpenAI refused the request')
                            if content.get('type') == 'output_text':
                                texts.append(content['text'])
                text = ''.join(texts)
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ProviderError('OpenAI result must be a JSON object')
                result.ok, result.parsed, result.text = True, parsed, text
                break
            else:
                raise ProviderError('OpenAI turn limit exceeded')
        except (TimeoutError, socket.timeout):
            result.error, result.timed_out = 'OpenAI call timed out', True
            if http_errors:
                result.retryable = False
        except ProviderHTTPError as exc:
            result.error = str(exc)
            if http_errors and http_errors[-1]['retry'].startswith('deferred'):
                result.error += '; server retry delay exceeds call deadline or 60-second wait limit; retry later'
            # The transport already applied bounded retries, or requires operator
            # action. Never let the shared JSON-validation retry multiply them.
            result.retryable = False
        except ProviderError as exc:
            result.error = str(exc)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, http.client.HTTPException):
            result.error = 'OpenAI transport or reply validation failed'
        finally:
            if pending:
                result.cost_usd = None
            result.duration_ms = int((time.monotonic() - started) * 1000)
            # Never log request headers, credentials, unvalidated provider error bodies or reasoning.
            result.raw = {'provider': 'openai', 'model': self.model, 'cost_source': 'operator_token_rates',
                          'cost_complete': result.cost_usd is not None, 'turns': result.num_turns,
                          'structured_output': result.parsed}
            if http_errors:
                result.http_errors = http_errors
                result.raw['http_errors'] = http_errors
        return result
