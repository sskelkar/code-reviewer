"""Bounded metadata tracing; standard-library only, including inside the review VM."""
from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import math
import re
import threading
import time
import uuid
from contextlib import contextmanager

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 4000
_active = contextvars.ContextVar('review_trace', default=None)
_parent = contextvars.ContextVar('review_span', default=None)
KINDS = ('span', 'agent', 'generation', 'tool')
NAMES = frozenset(('review.run', 'review.guest', 'review.prepare', 'review.flow', 'criteria.resolve',
                   'criteria.route', 'review.pass', 'review.round', 'review.consolidate', 'review.verify',
                   'review.ground', 'review.report', 'review.cleanup', 'model.call', 'model.attempt',
                   'model.turn', 'model.aggregate', 'http.attempt', 'tool.Read', 'tool.Grep', 'tool.Glob',
                   'vm.acquire', 'vm.admit', 'vm.images', 'vm.execute', 'vm.cleanup'))
ENUMS = {
    'role': ('reviewer', 'resolver', 'comparator', 'verifier', 'classifier'),
    'persona': ('correctness', 'code-quality', 'test-quality', 'production-safety'),
    'provider': ('openai', 'claude'), 'auth': ('api-key', 'subscription'),
    'execution': ('vm', 'host'),
    'outcome': ('ok', 'error', 'cancelled', 'incomplete'),
    'stop_reason': ('completed', 'budget_exceeded', 'incomplete', 'cancelled', 'failures'),
    'visibility': ('native-aggregate', 'model-turn'),
    'pool': ('criteria', 'comparator', 'verifier', 'correctness', 'code-quality', 'test-quality', 'production-safety'),
}
NUMBERS = frozenset(('pass', 'round', 'attempt', 'passes', 'parallel', 'input', 'output', 'cache_read',
                     'cache_creation', 'cache_write_reported', 'duration_ms', 'turns', 'bytes', 'rows',
                     'proposed', 'accepted', 'rejected', 'rules', 'conflicts', 'duplicates', 'status',
                     'cost_usd', 'cache_savings_usd', 'cache_read_share', 'allowance_usd', 'allowance_tokens'))
BOOLS = frozenset(('ok', 'usage_known', 'cache_read_known', 'cache_write_known', 'truncated', 'budget_exceeded'))


def attributes(values):
    """Closed schema: never stringify arbitrary arguments, exceptions or model text."""
    from ..backends.http_errors import CODES
    out = {}
    for key, value in values.items():
        if key in ENUMS and isinstance(value, str) and value in ENUMS[key]:
            out[key] = value
        elif key in NUMBERS and type(value) in (int, float) and 0 <= value <= 10**15 and math.isfinite(value):
            out[key] = value
        elif key in BOOLS and type(value) is bool:
            out[key] = value
        elif key == 'error_code' and isinstance(value, str) and value in CODES:
            out[key] = value
        elif key in ('prompt_hash', 'criteria_hash') and isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value):
            out[key] = value
        elif key == 'model' and isinstance(value, str) and re.fullmatch('[a-zA-Z0-9_.:/-]{1,128}', value):
            out[key] = value
    return out


class Observation:
    def __init__(self, row=None):
        self.row = row

    def update(self, **values):
        if self.row is not None:
            self.row['attributes'].update(attributes(values))


class Recorder:
    def __init__(self, trace_id=None, parent_id=None):
        self.trace_id = trace_id or uuid.uuid4().hex
        self.parent_id = parent_id
        self.records, self.dropped = [], 0
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            now = time.time_ns()
            rows = [dict(r, end_ns=r['end_ns'] or now, attributes=dict(r['attributes'])) for r in self.records]
            dropped = self.dropped
        value = dict(version=1, trace_id=self.trace_id, records=rows, dropped=dropped)
        return bound(value)


def current():
    return _active.get()


def annotate(**values):
    recorder = current()
    if recorder:
        with recorder.lock:
            for row in reversed(recorder.records):
                if row['id'] == _parent.get():
                    Observation(row).update(**values)
                    break


@contextmanager
def activate(recorder):
    token, parent = _active.set(recorder), _parent.set(recorder.parent_id)
    try:
        yield recorder
    finally:
        _parent.reset(parent)
        _active.reset(token)


@contextmanager
def observation(name, kind='span', **values):
    recorder = current()
    if recorder is None or name not in NAMES or kind not in KINDS:
        yield Observation()
        return
    row = dict(id=uuid.uuid4().hex[:16], parent=_parent.get(), name=name, kind=kind,
               start_ns=time.time_ns(), end_ns=None, attributes=attributes(values))
    with recorder.lock:
        for ancestor in reversed(recorder.records):
            if ancestor['id'] == row['parent']:
                inherited = {k: v for k, v in ancestor['attributes'].items() if k in ('role', 'persona', 'pass', 'round', 'auth')}
                row['attributes'] = dict(inherited, **row['attributes'])
                break
        if len(recorder.records) >= MAX_RECORDS:
            recorder.dropped += 1
            row = None
        else:
            recorder.records.append(row)
    token = _parent.set(row['id']) if row is not None else None
    span = Observation(row)
    try:
        yield span
    except BaseException as exc:
        span.update(outcome='cancelled' if isinstance(exc, KeyboardInterrupt) else 'error')
        raise
    else:
        if row is not None:
            row['attributes'].setdefault('outcome', 'ok')
    finally:
        if row is not None:
            row['end_ns'] = max(row['start_ns'], time.time_ns())
            _parent.reset(token)


def traced(name, kind='span'):
    """Instrument a boundary without capturing its arguments or return value."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with observation(name, kind):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def context():
    return dict(trace_id=current().trace_id, parent_id=_parent.get()) if current() else None


def bound(value):
    """Trim parent-first records in linear time, reserving envelope/count overhead."""
    rows, kept, size = value['records'], [], 256
    for row in rows:
        size += len(json.dumps(row, separators=(',', ':')).encode()) + 1
        if size > MAX_BYTES or len(kept) >= MAX_RECORDS:
            break
        kept.append(row)
    value['dropped'] += len(rows) - len(kept)
    value['records'] = kept
    return value


def event(name, data):
    """Attach numeric progress summaries; ignore titles, paths and warning text."""
    if not current():
        return
    values = {}
    if name == 'criteria_resolved':
        values['rules'] = sum(data.get(k, 0) for k in ('default_rules', 'project_rules'))
    elif name == 'reviewer_done':
        values = dict(proposed=data.get('raised'), accepted=data.get('usable'), rejected=data.get('ungrounded'))
    elif name == 'pass_finished':
        values = dict(accepted=data.get('new'), duplicates=data.get('matched'), rejected=data.get('ungrounded'))
    elif name == 'report_ready':
        values['stop_reason'] = data.get('stop_reason')
    if values:
        annotate(**values)


def validate(value, *, trace_id=None, parent_id=None, models=None):
    """Treat VM telemetry as untrusted; only return the exact allowlisted schema."""
    if not isinstance(value, dict) or set(value) != {'version', 'trace_id', 'records', 'dropped'}:
        raise ValueError('invalid trace envelope')
    if (type(value['version']) is not int or value['version'] != 1 or not isinstance(value['trace_id'], str)
            or not re.fullmatch('[0-9a-f]{32}', value['trace_id']) or int(value['trace_id'], 16) == 0):
        raise ValueError('invalid trace identity')
    if trace_id and value['trace_id'] != trace_id:
        raise ValueError('unexpected trace identity')
    rows = value['records']
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS or type(value['dropped']) is not int or not 0 <= value['dropped'] <= 10**9:
        raise ValueError('trace count limit')
    if len(json.dumps(value, separators=(',', ':')).encode()) > MAX_BYTES:
        raise ValueError('trace byte limit')
    seen, depths, roots = set(), {parent_id: 0}, 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'parent', 'name', 'kind', 'start_ns', 'end_ns', 'attributes'}:
            raise ValueError('invalid observation')
        id_ = row['id']
        if (not isinstance(id_, str) or not re.fullmatch('[0-9a-f]{16}', id_)
                or int(id_, 16) == 0 or id_ in seen or id_ == parent_id):
            raise ValueError('invalid observation identity')
        parent = row['parent']
        if (parent is not None and not isinstance(parent, str)) or parent not in depths:
            raise ValueError('invalid observation parent')
        roots += parent == parent_id
        if roots > 1:
            raise ValueError('multiple trace roots')
        if not isinstance(row['name'], str) or not isinstance(row['kind'], str) or row['name'] not in NAMES or row['kind'] not in KINDS:
            raise ValueError('unknown observation')
        if any(type(row[k]) is not int for k in ('start_ns', 'end_ns')) or not 0 < row['start_ns'] <= row['end_ns'] <= time.time_ns() + 300 * 10**9:
            raise ValueError('invalid observation time')
        if row['end_ns'] - row['start_ns'] > 86400 * 10**9:
            raise ValueError('trace duration limit')
        attrs = row['attributes']
        if not isinstance(attrs, dict) or attributes(attrs) != attrs or len(attrs) > 40:
            raise ValueError('invalid trace metadata')
        if models is not None and 'model' in attrs and attrs['model'] not in models:
            raise ValueError('unexpected model')
        depths[id_] = depths[parent] + 1
        if depths[id_] > 32:
            raise ValueError('trace depth limit')
        seen.add(id_)
    return value


def attach(stats, value, *, max_bytes=4 * 1024 * 1024):
    """Telemetry yields space to ordinary stats; never increase artifact limits."""
    stats = dict(stats)
    stats['trace'] = value
    if len(json.dumps(stats, indent=2, ensure_ascii=False).encode()) + 1 > max_bytes:
        rows, initial = list(value['records']), value['dropped']
        lo, hi = 0, len(rows)
        while lo < hi:
            middle = (lo + hi + 1) // 2
            value.update(records=rows[:middle], dropped=initial + len(rows) - middle)
            if len(json.dumps(stats, indent=2, ensure_ascii=False).encode()) + 1 <= max_bytes:
                lo = middle
            else:
                hi = middle - 1
        value.update(records=rows[:lo], dropped=initial + len(rows) - lo)
        if len(json.dumps(stats, indent=2, ensure_ascii=False).encode()) + 1 > max_bytes:
            stats.pop('trace')
    return stats


def result_usage(span, result):
    span.update(ok=result.ok, usage_known=result.usage_known, budget_exceeded=result.budget_exceeded,
                turns=result.num_turns, duration_ms=result.duration_ms,
                **result.tokens, **({'cost_usd': result.cost_usd} if result.billing_mode != 'subscription' else {}))
    if not result.ok:
        span.update(outcome='error')
    for error in result.http_errors[-1:]:
        span.update(error_code=error.get('code'), status=error.get('status'))
    usage = result.raw.get('usage') if isinstance(result.raw, dict) else None
    if isinstance(usage, dict):
        span.update(cache_read_known='cache_read_input_tokens' in usage,
                    cache_write_known='cache_creation_input_tokens' in usage)
        total_input = sum(result.tokens.get(k, 0) for k in ('input', 'cache_read', 'cache_creation'))
        if result.usage_known and 'cache_read_input_tokens' in usage:
            span.update(cache_read_share=result.tokens.get('cache_read', 0) / total_input if total_input else 0)


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()
