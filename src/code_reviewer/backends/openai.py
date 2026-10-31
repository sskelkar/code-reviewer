"""Responses API adapter shared by execution paths; no SDK or model command tools."""
from __future__ import annotations
from ..tracing import observation

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
from ..config import ConfigError
from .read_tools import ReadOnlyTools, definitions
from .http_errors import diagnostics, error_message, temporary
from .openai_stream import read_stream, read_untyped_stream, unexpected_stream, StreamFailure

MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_TURNS = 8
MAX_TOOL_CALLS = 32
MAX_OUTPUT_TOKENS = 2048
MAX_HTTP_ATTEMPTS = 3
MAX_RETRY_DELAY = 60.0


class ProviderError(ValueError):
    pass


class OutputJSONError(ProviderError):
    """A completed model answer can use the shared bounded output retry."""


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


def request_response(payload, key, timeout, *, proxy=None, cancelled=None):
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
                           {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                            'Accept': 'text/event-stream' if payload.get('stream') is True else 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ProviderHTTPError(diagnostics(response))
        def read_chunk(max_bytes=65536):
            if cancelled is not None and cancelled.is_set():
                raise KeyboardInterrupt
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(min(remaining,30))
            return response.read1(max_bytes)
        if payload.get('stream') is True:
            media = (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
            if not media:
                return read_untyped_stream(response, read_chunk, MAX_RESPONSE)
            if media != 'text/event-stream':
                unexpected_stream(response, read_chunk)
            return read_stream(read_chunk,MAX_RESPONSE)
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
    def __init__(self, api_key, *, model, prices=None, transport=None, billing_mode='api-key', token_source=None, proxy=None, token_limit=200000):
        if not isinstance(api_key, str) or not api_key.strip() or any(c in api_key for c in '\r\n'):
            raise ValueError('a nonempty runtime API key is required')
        if not isinstance(model, str) or not model.strip() or model in ('haiku', 'sonnet', 'opus'):
            raise ValueError('select an explicit OpenAI model')
        if billing_mode not in ('api-key','subscription'):
            raise ValueError('invalid OpenAI authentication mode')
        if billing_mode=='api-key' and not isinstance(prices, TokenPrices):
            raise ValueError('explicit token prices are required')
        self._api_key, self.model, self.prices = api_key, model, prices
        self._transport = transport
        self.billing_mode, self.token_source, self.proxy, self.token_limit = billing_mode, token_source, proxy, token_limit

    def version(self):
        return 'OpenAI Responses adapter v1 (experimental)'

    def run(self, **kwargs):
        return self.run_cancellable(None, **kwargs)

    def _trace_usage(self, span, reply, subscription):
        usage = reply.get('usage') if isinstance(reply, dict) else None
        details = usage.get('input_tokens_details') if isinstance(usage, dict) else None
        span.update(usage_known=False, cache_read_known=False, cache_write_known=False)
        if not isinstance(usage, dict) or not isinstance(details, dict):
            return
        input_, output, cached = usage.get('input_tokens'), usage.get('output_tokens'), details.get('cached_tokens')
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in (input_, output, cached)) or cached > input_:
            return
        values = dict(input=input_ - cached, output=output, cache_read=cached, usage_known=True,
                      cache_read_known=True, cache_read_share=cached / input_ if input_ else 0)
        writes = details.get('cache_write_tokens')
        if type(writes) is int and 0 <= writes <= input_ - cached:
            values.update(cache_write_reported=writes, cache_write_known=True)
        if not subscription:
            values['cost_usd'] = self.prices.cost(input_, cached, output)
            # No write-price assumption: only claim net savings when writes explicitly equal zero.
            if type(writes) is int and writes == 0:
                values['cache_savings_usd'] = max(0, self.prices.cost(input_, 0, output) - values['cost_usd'])
        span.update(**values)

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
                if self.token_source is not None:
                    self._api_key = self.token_source()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    if cancelled is not None and cancelled.is_set():
                        raise KeyboardInterrupt
                with observation('http.attempt', attempt=attempt) as request_span:
                    try:
                        if self._transport is not None:
                            return self._transport(payload,self._api_key,remaining)
                        return request_response(payload,self._api_key,remaining,proxy=self.proxy,cancelled=cancelled)
                    except ProviderHTTPError as exc:
                        request_span.update(error_code=exc.details.get('code'), status=exc.details.get('status'))
                        raise
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
                        budget_usd, timeout_s, add_dirs=(), token_limit=None):
        result = LLMResult(ok=False, model=self.model, cost_usd=0.0, billing_mode=self.billing_mode, usage_known=False)
        started = time.monotonic()
        pending = False
        http_errors = []
        subscription=self.billing_mode=='subscription'
        token_limit=self.token_limit if token_limit is None else token_limit
        phase = 'configuration'
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
                if subscription:
                    payload.pop('max_output_tokens')
                    payload['stream']=True
                    if names:
                        payload['tools']=[dict(type='namespace',name='reviewer',description='Read-only repository tools',tools=definitions(names))]
                request_size = len(json.dumps(payload).encode('utf-8'))
                if request_size > MAX_REQUEST:
                    raise ProviderError('OpenAI request size limit exceeded')
                # Bytes plus overhead is a conservative admission estimate, not a provider-enforced cap.
                if subscription:
                    if type(token_limit) is not int or token_limit<1:
                        raise ProviderError('invalid subscription token allowance')
                    if result.total_tokens+request_size+1024>token_limit:
                        result.budget_exceeded=True
                        raise ProviderError('subscription token allowance insufficient for another request')
                else:
                    reserve = self.prices.cost(request_size + 1024, 0, MAX_OUTPUT_TOKENS)
                    if result.cost_usd + reserve > budget_usd:
                        result.budget_exceeded = True
                        raise ProviderError('OpenAI call allowance insufficient for another request')
                pending = True
                phase = 'transport'
                with observation('model.turn', 'generation', model=self.model, auth='subscription' if subscription else 'api-key',
                                 visibility='model-turn') as span:
                    try:
                        reply = self._request(payload, started + timeout_s, cancelled, http_errors)
                    except BaseException:
                        span.update(usage_known=False)
                        for error in http_errors[-1:]:
                            span.update(error_code=error.get('code'), status=error.get('status'))
                        raise
                    self._trace_usage(span, reply, subscription)
                phase = 'usage'
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
                if not subscription:
                    result.cost_usd += self.prices.cost(input_, cached, output)
                result.num_turns = turn + 1
                pending = False
                if time.monotonic() - started >= timeout_s:
                    raise TimeoutError
                if cancelled is not None and cancelled.is_set():
                    raise KeyboardInterrupt
                if (subscription and result.total_tokens > token_limit) or (not subscription and result.cost_usd > budget_usd):
                    result.budget_exceeded = True
                    raise ProviderError('subscription token allowance exceeded' if subscription else 'OpenAI estimated call allowance exceeded')
                if reply.get('status') != 'completed' or not isinstance(reply.get('output'), list):
                    raise ProviderError('OpenAI response did not complete')
                items = reply['output']
                phase = 'output items'
                if not items:
                    result.retryable = False
                    raise ProviderError('OpenAI completed response contains no output items; no automatic replay')
                if len(items) > 128 or any(not isinstance(item, dict) for item in items):
                    result.retryable = False
                    raise ProviderError('invalid OpenAI output item collection')
                calls = [item for item in items if isinstance(item, dict) and item.get('type') == 'function_call']
                if calls:
                    tool_count += len(calls)
                    if tool_count > MAX_TOOL_CALLS:
                        raise ProviderError('OpenAI tool-call limit exceeded')
                    # Preserve output items, including encrypted reasoning, for a stateless continuation.
                    history.extend(items)
                    for call in calls:
                        if (subscription and call.get('namespace')!='reviewer') or call.get('name') not in names or not isinstance(call.get('call_id'), str):
                            raise ProviderError('OpenAI requested an unavailable tool')
                        phase = 'tool arguments'
                        raw_arguments = call.get('arguments')
                        if not isinstance(raw_arguments, str):
                            result.retryable = False
                            raise ProviderError('OpenAI function-call arguments must be a JSON string; tool call refused')
                        try:
                            arguments = json.loads(raw_arguments)
                        except (ValueError, RecursionError):
                            result.retryable = False
                            raise ProviderError('OpenAI function-call arguments contain invalid JSON; tool call refused') from None
                        if not isinstance(arguments, dict):
                            result.retryable = False
                            raise ProviderError('OpenAI function-call arguments must decode to an object; tool call refused')
                        phase = 'tool execution'
                        with observation('tool.' + call['name'], 'tool') as tool_span:
                            tool_output = files.execute(call['name'], arguments)
                            tool_value = json.loads(tool_output)
                            tool_span.update(ok='error' not in tool_value, truncated=tool_value.get('truncated'),
                                             bytes=len(tool_output.encode()), rows=len(tool_value.get('text', '').splitlines()))
                        history.append({'type': 'function_call_output', 'call_id': call['call_id'], 'output': tool_output})
                    continue
                phase = 'message content'
                texts = []
                for item in items:
                    if not isinstance(item, dict):
                        raise ProviderError('invalid OpenAI output item')
                    if item.get('type') == 'message':
                        content_parts = item.get('content')
                        if not isinstance(content_parts, list):
                            result.retryable = False
                            raise ProviderError('OpenAI message content must be a list')
                        for content in content_parts:
                            if not isinstance(content, dict):
                                result.retryable = False
                                raise ProviderError('invalid OpenAI message content part')
                            if content.get('type') == 'refusal':
                                raise ProviderError('OpenAI refused the request')
                            if content.get('type') == 'output_text':
                                if not isinstance(content.get('text'), str):
                                    result.retryable = False
                                    raise ProviderError('OpenAI output_text is missing string text')
                                texts.append(content['text'])
                if not texts:
                    result.retryable = False
                    raise ProviderError('OpenAI completed response has no function calls or output_text (items=%d, messages=%d); no automatic replay' %
                                        (len(items), sum(item.get('type') == 'message' for item in items)))
                text = ''.join(texts)
                phase = 'final JSON'
                try:
                    parsed = json.loads(text)
                except (ValueError, RecursionError):
                    raise OutputJSONError('OpenAI final output is not valid JSON (text parts=%d, characters=%d); content omitted' %
                                        (len(texts), len(text))) from None
                if not isinstance(parsed, dict):
                    raise OutputJSONError('OpenAI result must be a JSON object')
                result.ok, result.parsed, result.text = True, parsed, text
                break
            else:
                raise ProviderError('OpenAI turn limit exceeded')
        except (TimeoutError, socket.timeout):
            result.error, result.timed_out = 'OpenAI call timed out', True
            if http_errors:
                result.retryable = False
        except ConfigError:
            result.error = 'ChatGPT session unavailable; run reviewer auth login for the selected runtime/account'
            result.retryable = False
        except StreamFailure as exc:
            result.error=str(exc)
            result.retryable=False
        except ProviderHTTPError as exc:
            result.error = str(exc)
            if http_errors and http_errors[-1]['retry'].startswith('deferred'):
                result.error += '; server retry delay exceeds call deadline or 60-second wait limit; retry later'
            # The transport already applied bounded retries, or requires operator
            # action. Never let the shared JSON-validation retry multiply them.
            result.retryable = False
        except OutputJSONError as exc:
            result.error = str(exc)
        except ProviderError as exc:
            result.error = str(exc)
            result.retryable = False
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, http.client.HTTPException) as exc:
            kind = next((name for cls, name in ((OSError, 'OS error'), (http.client.HTTPException, 'HTTP error'),
                        (ValueError, 'value error'), (TypeError, 'type error'), (KeyError, 'missing field'),
                        (AttributeError, 'unexpected field type'), (RecursionError, 'nesting limit')) if isinstance(exc, cls)), 'error')
            result.error = 'OpenAI %s failed (%s); response content omitted' % (phase, kind)
            result.retryable = False
        finally:
            result.usage_known = not pending and result.num_turns is not None
            if pending or subscription:
                result.cost_usd = None
            result.duration_ms = int((time.monotonic() - started) * 1000)
            # Never log request headers, credentials, unvalidated provider error bodies or reasoning.
            result.raw = {'provider': 'openai', 'model': self.model, 'cost_source': 'subscription (no API billing)' if subscription else 'operator_token_rates',
                          'cost_complete': result.cost_usd is not None, 'turns': result.num_turns,
                          'structured_output': result.parsed}
            if http_errors:
                result.http_errors = http_errors
                result.raw['http_errors'] = http_errors
        return result
