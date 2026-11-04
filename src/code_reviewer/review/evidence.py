"""Bounded shared JSON contracts and repository-grounded evidence; no model authority."""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from ..shared.findings import find_quote


def shape_errors(value, schema):
    """Validate the small JSON Schema subset used by the shared role contract."""
    definitions = schema.get('$defs', {})

    def check(v, s, depth=0):
        if depth > 20:
            return False
        if '$ref' in s:
            return check(v, definitions[s['$ref'].split('/')[-1]], depth + 1)
        if 'anyOf' in s:
            return any(check(v, option, depth + 1) for option in s['anyOf'])
        if 'enum' in s and v not in s['enum']:
            return False
        types = s.get('type', [])
        types = [types] if isinstance(types, str) else types
        matches = {'object': type(v) is dict, 'array': type(v) is list, 'string': type(v) is str,
                   'integer': type(v) is int, 'null': v is None}
        if types and not any(matches.get(t, False) for t in types):
            return False
        if type(v) is dict:
            props = s.get('properties', {})
            return (set(s.get('required', [])) <= set(v) and
                    (s.get('additionalProperties', True) or set(v) <= set(props)) and
                    all(check(item, props[k], depth + 1) for k, item in v.items() if k in props))
        if type(v) is list:
            if not s.get('minItems', 0) <= len(v) <= s.get('maxItems', 10000):
                return False
            if s.get('uniqueItems') and len({json.dumps(x, sort_keys=True) for x in v}) != len(v):
                return False
            return all(check(item, s.get('items', {}), depth + 1) for item in v)
        if type(v) is str:
            return (s.get('minLength', 0) <= len(v) <= s.get('maxLength', 1000000) and
                    (not s.get('minLength') or bool(v.strip())) and
                    ('pattern' not in s or re.fullmatch(s['pattern'], v) is not None))
        if type(v) is int:
            return v >= s.get('minimum', v) and v <= s.get('maximum', v)
        return True

    return [] if check(value, schema) else ['response violates the closed, bounded evidence contract']


def safe_relative(path):
    return (isinstance(path, str) and bool(path) and len(path) <= 4096 and '\x00' not in path
            and not path.startswith('/') and '..' not in PurePosixPath(path).parts
            and '.git' not in PurePosixPath(path).parts)


def evidence_errors(entries, prefix, cache, criteria):
    ids = set()
    for entry in entries:
        if entry['source'] not in ('head', 'criterion'):
            return ['unsupported evidence source']
        ident = entry['id']
        if not ident.startswith(prefix) or ident in ids:
            return ['evidence IDs must be unique and use the correct S/V namespace']
        ids.add(ident)
        if entry['source'] == 'head':
            path, line = entry['file'], entry['line']
            if not safe_relative(path) or type(line) is not int or entry['rule_id'] is not None:
                return ['head evidence requires a safe path, line and null rule_id']
            lines = cache.lines(path)
            if lines is None or line not in find_quote(lines, entry['quote']):
                return ['evidence quote/line is not grounded in the pinned head']
        else:
            rule = criteria.get(entry['rule_id'])
            if entry['file'] is not None or entry['line'] is not None or rule is None:
                return ['criterion evidence requires an accepted rule_id and null file/line']
            lines = [(n, ' '.join(text.split())) for n, text in enumerate(rule['text'].splitlines(), 1) if text.strip()]
            if not find_quote(lines, entry['quote']):
                return ['criterion quote is not in the frozen accepted rule']
    return []


def reviewer_errors(payload, schema, self_check):
    errors = shape_errors(payload, schema)
    if errors:
        return errors
    for finding in payload['findings']:
        if not safe_relative(finding['file']):
            return ['finding location must be checkout-relative']
        if self_check != (finding['self_check'] is not None):
            return ['self_check must match the configured setting']
        if finding['kind'] == 'absent':
            anchor = finding['anchor']
            if (not anchor or not finding['expected'] or finding['quote'] is not None
                    or not safe_relative(anchor['file']) or anchor['line_start'] > anchor['line_end']
                    or anchor['file'] != finding['file'] or anchor['line_start'] != finding['line']):
                return ['absence requires a consistent changed anchor and expected behavior']
        elif not finding['quote'] or finding['anchor'] is not None:
            return ['present/cross_file findings require a primary quote and null anchor']
    return []
