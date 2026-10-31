"""Host review trace lifecycle; telemetry failures never replace the review outcome."""
from __future__ import annotations

import functools
import json
import os
import sys

from .recorder import Recorder, activate, attach, observation, validate, bound


def _save_stats(directory, stats, trace_id):
    temporary = directory / ('.trace-' + trace_id)
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
            json.dump(stats, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(temporary, directory / 'stats.json')
    finally:
        temporary.unlink(missing_ok=True)


def finish(recorder, cfg, result=None):
    try:
        value = recorder.snapshot()
        directory = result.run_dir if result is not None else cfg.runs_path() / ('trace-' + recorder.trace_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / 'stats.json'
        stats = json.loads(path.read_text()) if path.exists() else {}
        guest = stats.pop('trace', None)
        if guest is not None:
            try:
                context = getattr(recorder, 'guest_context', {})
                validate(guest, trace_id=recorder.trace_id, parent_id=context.get('parent_id'),
                         models={getattr(cfg, 'model_' + role) for role in ('reviewer', 'resolver', 'comparator', 'verifier')})
                host_ids = {row['id'] for row in value['records']}
                if context.get('parent_id') not in host_ids or host_ids.intersection(row['id'] for row in guest['records']):
                    raise ValueError('guest trace collides with host observations')
                # Host spans precede imported children; public IDs/times survive the export.
                value['records'].extend(guest['records'])
                value['dropped'] += guest['dropped']
            except (ValueError, TypeError, OverflowError, RecursionError):
                value['dropped'] += 1
                print('warning: invalid VM trace metadata omitted', file=sys.stderr)
        bound(value)
        validate(value)
        stats = attach(stats, dict(value, records=list(value['records'])))
        stats['tracing'] = dict(trace_id=recorder.trace_id, exported=False, url=None)
        # Persist before optional network export so interruption cannot lose the trace.
        _save_stats(directory, stats, recorder.trace_id)
        try:
            from .langfuse import export
            export(value)
            url = 'http://localhost:4000/project/reviewer/traces/' + recorder.trace_id
            stats['tracing'].update(exported=True, url=url)
            _save_stats(directory, stats, recorder.trace_id)
            print('Trace: ' + url, file=sys.stderr)
        except Exception:
            print('warning: local Langfuse export unavailable; trace metadata saved with run stats', file=sys.stderr)
    except Exception:
        print('warning: tracing failed; review result is unchanged', file=sys.stderr)


def review_trace(fn):
    @functools.wraps(fn)
    def wrapped(cfg, *args, **kwargs):
        options = kwargs.get('options', args[2] if len(args) > 2 else None)
        if not cfg.tracing or getattr(options, 'preflight', False) or getattr(options, 'dry_run', False):
            return fn(cfg, *args, **kwargs)
        recorder, outcome = Recorder(), None
        try:
            with activate(recorder), observation('review.run', mode=cfg.review_mode, provider=cfg.provider,
                    auth=cfg.auth_mode, execution='vm' if cfg.sandbox else 'host',
                    passes=cfg.persona_passes if cfg.review_mode == 'agent-personas' else cfg.max_passes,
                    parallel=cfg.persona_parallel) as root:
                outcome = fn(cfg, *args, **kwargs)
                if outcome.result is not None:
                    root.update(stop_reason=outcome.result.stop_reason)
                    if outcome.result.stop_reason in ('failures', 'incomplete', 'budget_exceeded', 'cancelled'):
                        root.update(outcome='incomplete')
                return outcome
        finally:
            finish(recorder, cfg, outcome.result if outcome else None)
    return wrapped
