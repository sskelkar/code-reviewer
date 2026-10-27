"""Exercise the OpenAI adapter and both flows offline; never reads a real API key."""
from __future__ import annotations

import json

from ..backends.openai import OpenAIRunner, TokenPrices
from .fake_claude import answer
from .smoke import run_smoke

RESULT_PREFIX = 'REVIEWER_OPENAI_RESULT='


def offline_response(payload, key, timeout):
    """A stateless protocol fixture, including a real local Read dispatch per Reviewer."""
    schema = payload['text']['format']['schema']
    history = payload['input']
    is_review = 'findings' in schema.get('required', [])
    if is_review and not any(item.get('type') == 'function_call_output' for item in history):
        output = [{'type': 'function_call', 'name': 'Read', 'call_id': 'synthetic-read',
                   'arguments': json.dumps({'path': 'app.py', 'offset': 1, 'limit': 20})}]
    else:
        if is_review:
            tool_reply = json.loads(history[-1]['output'])
            if 'return 2' not in tool_reply.get('text', ''):
                raise ValueError('synthetic Read failed')
        output = [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': json.dumps(answer(schema, history[0]['content']))}]}]
    return {'status': 'completed', 'output': output,
            'usage': {'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 10}, 'output_tokens': 50}}


def run_offline():
    runner = OpenAIRunner('synthetic-placeholder', model='synthetic-openai', prices=TokenPrices(1, .5, 2),
                          transport=offline_response)
    result = run_smoke(runner, model=runner.model, provider='openai')
    result.update(provider='openai', live=False)
    return result


def parse_result(text):
    from .launch import parse_result as validate_smoke
    from .smoke import RESULT_PREFIX as SMOKE_PREFIX
    records = [line[len(RESULT_PREFIX):] for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(records) != 1:
        raise ValueError('expected one offline OpenAI result')
    data = json.loads(records[0])
    if not isinstance(data, dict) or data.get('provider') != 'openai' or data.get('live') is not False:
        raise ValueError('expected an offline OpenAI result')
    base = {key: value for key, value in data.items() if key not in ('provider', 'live')}
    validate_smoke(SMOKE_PREFIX + json.dumps(base))
    return data


if __name__ == '__main__':
    print(RESULT_PREFIX + json.dumps(run_offline(), sort_keys=True), flush=True)
