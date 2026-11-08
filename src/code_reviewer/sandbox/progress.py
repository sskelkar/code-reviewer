"""Bounded advisory frames on the existing private channel; never admission authority."""
from __future__ import annotations

import json
import re

from ..shared.events import Event, display_text
from .wire import receive_json, send_json

MAX_FRAME = 32 * 1024
MAX_EVENTS = 512
MAX_BYTES = 512 * 1024
PERSONAS = ('correctness', 'code-quality', 'test-quality', 'production-safety')
STAGES = {
    'staging': 'staging trusted runtime inputs',
    'starting_worker': 'starting dedicated Lima worker',
    'images': 'preparing pinned reviewer images',
    'booting': 'starting jailed Firecracker VM',
    'acquiring': 'fetching and pinning PR input inside the VM',
    'admitting': 'sealing read-only input and checking boundaries',
    'credentials': 'preparing selected model authentication',
    'checkout': 'review checkout: /work/snapshot (inside the disposable VM)',
    'criteria': 'resolving repository review criteria',
    'classifying': 'distributing project criteria among personas',
    'reviewing': 'reviewers are investigating the change',
    'consolidating': 'checking evidence and consolidating findings',
    'verifying': 'verifying review candidates',
    'reporting': 'writing review reports',
    'exporting': 'transferring bounded review results',
    'cleanup': 'cleaning up VM, network and temporary state',
    'stopping_worker': 'stopping dedicated Lima worker',
    'saving': 'validating and saving local reports',
}


def integer(value):
    return type(value) is int and 0 <= value <= 10**9


def valid_rows(rows, limit, *, identified=False):
    if not isinstance(rows, list) or len(rows) > limit:
        return False
    keys = {'severity', 'title', 'file', 'line'} | ({'id'} if identified else set())
    for row in rows:
        if (not isinstance(row, dict) or set(row) != keys
                or row['severity'] not in ('high', 'medium', 'low', 'nit')
                or not integer(row['line']) or row['line'] < 1
                or any(not isinstance(row[k], str) or len(row[k]) > 240
                       or display_text(row[k]) != row[k] for k in ('title', 'file'))):
            return False
        if identified and (not isinstance(row['id'], str) or not re.fullmatch(r'F-[0-9]{3,12}', row['id'])):
            return False
    return True


def validate(frame, *, allow_preview=False, secret=''):
    if not isinstance(frame, dict) or set(frame) != {'kind', 'event', 'data'} or frame['kind'] != 'progress':
        raise ValueError('invalid progress envelope')
    encoded = json.dumps(frame, ensure_ascii=True).encode()
    if len(encoded) > MAX_FRAME or (secret and secret.encode() in encoded):
        raise ValueError('progress content refused')
    event, data = frame['event'], frame['data']
    if not isinstance(data, dict):
        raise ValueError('invalid progress data')
    valid = False
    if event == 'stage':
        valid = set(data) == {'stage'} and isinstance(data['stage'], str) and data['stage'] in STAGES
    elif event in ('review_started', 'review_finished', 'comparison_started'):
        keys = {'persona', 'round'} | ({'ok'} if event == 'review_finished' else set())
        preview_keys = {'raised', 'self_rejected', 'findings'}
        glimpse = event == 'review_finished' and set(data) == keys | preview_keys
        valid = (set(data) == (keys | preview_keys if glimpse else keys)
                 and data['persona'] in PERSONAS and integer(data['round'])
                 and ('ok' not in data or type(data['ok']) is bool))
        if glimpse:
            valid = (valid and allow_preview and integer(data['raised']) and integer(data['self_rejected'])
                     and data['self_rejected'] <= data['raised'] and valid_rows(data['findings'], 3)
                     and len(data['findings']) <= data['raised'] - data['self_rejected'])
    elif event == 'round_preview' and allow_preview:
        keys = {'round', 'completed', 'failed', 'total', 'new', 'findings'}
        valid = (set(data) == keys and all(integer(data[k]) for k in ('round', 'completed', 'failed', 'total', 'new'))
                 and valid_rows(data['findings'], 10, identified=True)
                 and len(data['findings']) <= data['new'] <= data['total'])
    if not valid:
        raise ValueError('unsupported progress event or fields')
    if secret and any(secret in row[k] for row in data.get('findings', []) for k in ('title', 'file')):
        raise ValueError('progress credential content refused')
    return frame


class Stream:
    """Validate/count progress across all phases; final protocol frames pass through unchanged."""
    def __init__(self, callback=None, *, secret=''):
        self.callback, self.secret = callback, secret
        self.allow_preview = False
        self.count = self.bytes = 0

    def accept(self, frame, wire_bytes=None):
        validate(frame, allow_preview=self.allow_preview, secret=self.secret)
        size = wire_bytes if wire_bytes is not None else len(json.dumps(frame, ensure_ascii=True).encode())
        if size > MAX_FRAME:
            raise ValueError('progress frame limit exceeded')
        self.count += 1
        self.bytes += size
        if self.count > MAX_EVENTS or self.bytes > MAX_BYTES:
            raise ValueError('progress stream limit exceeded')
        if self.callback:
            self.callback(frame)

    def receive(self, sock, limit, *, reader=receive_json):
        while True:
            sizes = []
            value = reader(sock, limit, size_sink=sizes.append)
            if value.get('kind') != 'progress':
                return value
            self.accept(value, sizes[0] if sizes else None)


class GuestProgress:
    def __init__(self, sock):
        self.sock, self.secret = sock, ''
        self.count = self.bytes = 0

    def send(self, event, **data):
        # Model text appears only in explicitly bounded provisional previews.
        if 'findings' in data:
            data = dict(data, findings=[dict(row, **{k: row[k].replace(self.secret, '[redacted]')[:240]
                if self.secret else row[k] for k in ('title', 'file')}) for row in data['findings']])
        frame = dict(kind='progress', event=event, data=data)
        validate(frame, allow_preview=True, secret=self.secret)
        size = len(json.dumps(frame, ensure_ascii=True).encode())
        if self.count >= MAX_EVENTS - 8 or self.bytes + size > MAX_BYTES - 2 * MAX_FRAME:
            return  # Advisory output never expands the normal result allowance.
        self.count += 1
        self.bytes += size
        send_json(self.sock, frame, MAX_FRAME)

    def stage(self, name):
        self.send('stage', stage=name)

    def __call__(self, event: Event):
        kind, d = event.type, event.data
        stages = {'checkout_ready': 'checkout', 'resolver_started': 'criteria',
                  'classification_started': 'classifying',
                  'verification_started': 'verifying', 'report_ready': 'exporting', 'report_started': 'reporting'}
        if kind in stages:
            self.stage(stages[kind])
        elif kind == 'persona_pass_started':
            self.send('review_started', persona=d['persona'], round=d['round_no'])
        elif kind == 'persona_review_finished':
            self.send('review_finished', persona=d['persona'], round=d['round_no'], ok=d['ok'],
                      **{k: d[k] for k in ('raised', 'self_rejected', 'findings') if k in d})
        elif kind == 'comparator_started':
            self.send('comparison_started', persona=d['persona'],
                      round=d.get('round_no', d.get('pass_no', 1)))
        elif kind == 'round_preview':
            self.send('round_preview', **d)
