#!/usr/bin/python3
"""Synthetic headless executable for the offline VM smoke test; never calls a model."""
from __future__ import annotations

import json
import re
import sys


def block(text, name):
    match = re.search(r'<%s>\s*(.*?)\s*</%s>' % (name, name), text, re.S)
    return json.loads(match.group(1)) if match else []


def answer(schema, text):
    required = schema.get('required', [])
    if 'findings' in required:
        return {'findings': [{
            'title': 'Synthetic changed return value', 'description': 'Synthetic smoke-test finding.',
            'severity': 'low', 'kind': 'present', 'rule_id': 'none', 'file': 'app.py', 'line': 2,
            'quote': 'return 2', 'suggestion': 'Synthetic suggestion.',
            'self_check': {'verdict': 'confirmed', 'reason': 'Synthetic fixture evidence.'},
        }]}
    if 'decisions' in required:
        candidates = block(text, 'candidate_findings')
        match = candidates[0]['id'] if candidates else 'new'
        decisions = [{'local_id': f['local_id'], 'match': match, 'same_as': None}
                     for f in block(text, 'new_findings')]
        if 'conflicts' in required:
            for decision in decisions:
                decision['categories'] = ['correctness']
            return {'decisions': decisions, 'conflicts': []}
        return {'decisions': decisions}
    raise ValueError('the offline smoke test supports Reviewer/Comparator only')


def main():
    if '--version' in sys.argv:
        print('synthetic-claude-v1')
        return
    schema = json.loads(sys.argv[sys.argv.index('--json-schema') + 1])
    payload = answer(schema, sys.stdin.read())
    print(json.dumps({'structured_output': payload, 'total_cost_usd': .001,
                      'usage': {'input_tokens': 10, 'output_tokens': 10},
                      'duration_ms': 1, 'num_turns': 1}))


if __name__ == '__main__':
    main()
