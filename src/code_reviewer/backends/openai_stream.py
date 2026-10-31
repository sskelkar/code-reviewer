"""Bounded Responses SSE completion parser; partial output never authorizes tools."""
from __future__ import annotations

import json
import http.client

from .http_errors import CODES


class StreamFailure(ValueError):
    """A stream cannot be replayed automatically after output has started."""


def _error_code(value):
    if not isinstance(value, dict):
        return None
    response = value.get('response')
    error = value.get('error') or (response.get('error') if isinstance(response, dict) else None)
    error = error if isinstance(error, dict) else value
    code = error.get('code')
    return code if isinstance(code, str) and code in CODES else None


def unexpected_stream(response, read_chunk, *, initial=b'', sniffed_json=False):
    """Classify a protocol mismatch without retaining headers or response text."""
    raw = response.getheader('Content-Type')
    media = raw.split(';', 1)[0].strip().lower() if isinstance(raw, str) and len(raw) <= 256 else ''
    label = media if media in ('application/json', 'text/html', 'text/plain') else ('other' if raw else 'missing')
    code = None
    if media == 'application/json' or sniffed_json:
        data = bytearray(initial)
        try:
            while len(data) <= 8192:
                chunk = read_chunk(8193 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) <= 8192:
                code = _error_code(json.loads(data))
        except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException):
            pass
    suffix = ' (' + code + ')' if code else ''
    if sniffed_json:
        label += ', body format JSON'
    raise StreamFailure('OpenAI subscription expected text/event-stream; received HTTP 200, media type '
                        + label + suffix + '; response rejected; no automatic replay')


def read_untyped_stream(response, read_chunk, limit):
    """A missing MIME header cannot establish success or disqualify valid SSE."""
    try:
        first = read_chunk(8193)
        if first.lstrip().startswith((b'{', b'[')):
            unexpected_stream(response, read_chunk, initial=first, sniffed_json=True)
        prefix = [first]

        def buffered():
            return prefix.pop() if prefix else read_chunk()

        return read_stream(buffered, limit)
    except StreamFailure as exc:
        raise StreamFailure(str(exc) + '; Content-Type missing') from None
    except (OSError, http.client.HTTPException):
        raise StreamFailure('OpenAI headerless response interrupted; no automatic replay') from None


def read_stream(read_chunk,limit):
    pending=bytearray()
    data=[]
    size=events=0
    event_name=None
    completed=None
    done_items = {}
    def dispatch():
        nonlocal completed,events,event_name
        if not data:
            event_name=None
            return
        events+=1
        if events>20000:
            raise StreamFailure('OpenAI stream event limit exceeded')
        raw=b'\n'.join(data)
        data.clear()
        if raw==b'[DONE]':
            if completed is None:
                raise StreamFailure('OpenAI stream ended without response.completed')
            return
        try:
            value=json.loads(raw)
        except (ValueError,RecursionError):
            raise StreamFailure('invalid OpenAI stream event') from None
        if not isinstance(value,dict):
            raise StreamFailure('invalid OpenAI stream event')
        type_=value.get('type')
        if event_name is not None and type_!=event_name:
            raise StreamFailure('OpenAI stream event type mismatch')
        event_name=None
        if type_ in ('error','response.failed','response.incomplete'):
            code = _error_code(value)
            suffix = ' (' + code + ')' if code else ''
            raise StreamFailure('OpenAI stream failed'+suffix+'; output was not accepted; no automatic replay')
        if completed is not None:
            raise StreamFailure('OpenAI stream continued after completion')
        if type_ == 'response.output_item.done':
            index, item = value.get('output_index'), value.get('item')
            if type(index) is not int or not 0 <= index < 128 or not isinstance(item, dict) or index in done_items:
                raise StreamFailure('invalid or duplicate completed OpenAI output item')
            done_items[index] = item
        if type_=='response.completed':
            response=value.get('response')
            if not isinstance(response,dict) or response.get('status')!='completed':
                raise StreamFailure('invalid OpenAI stream completion')
            completed=response
    try:
        while True:
            chunk=read_chunk()
            if not chunk:
                break
            size+=len(chunk)
            if size>limit:
                raise StreamFailure('OpenAI stream byte limit exceeded')
            pending.extend(chunk)
            while b'\n' in pending:
                line,_,rest=pending.partition(b'\n')
                pending[:]=rest
                line=line.rstrip(b'\r')
                if not line:
                    dispatch()
                elif line.startswith(b'data:'):
                    data.append(line[5:].lstrip(b' '))
                elif line.startswith(b'event:'):
                    event_name=line[6:].strip().decode('ascii')
                elif line.startswith(b':') or line.startswith((b'id:',b'retry:')):
                    continue
                else:
                    raise StreamFailure('invalid OpenAI event-stream field')
        if pending or data:
            raise StreamFailure('OpenAI stream ended inside an event')
        if completed is None:
            raise StreamFailure('OpenAI stream ended without response.completed (received %d bytes, %d events)' % (size, events))
        if (completed.get('output') is None or completed.get('output') == []) and done_items:
            if set(done_items) != set(range(len(done_items))):
                raise StreamFailure('OpenAI completed output items have missing indices')
            # Finalized items can be delivered separately from terminal metadata.
            # Never assemble from partial deltas or expose items before completion.
            completed = dict(completed, output=[done_items[n] for n in range(len(done_items))])
        return completed
    except (OSError,UnicodeError,TypeError,KeyError,AttributeError,TimeoutError,http.client.HTTPException):
        raise StreamFailure('OpenAI stream interrupted; output was not accepted; no automatic replay') from None
