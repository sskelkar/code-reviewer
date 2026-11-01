"""Optional host-only Langfuse export in a short-lived, deadline-bounded subprocess."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .recorder import MAX_BYTES, validate

BASE_URL = 'http://127.0.0.1:4000'
EXPORT_TIMEOUT = 20


def export(value):
    """No SDK import or credential access until an enabled review has finished."""
    validate(value)
    keys = {name: os.environ.get(name, '') for name in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY')}
    if any(not v or len(v) > 256 or not v.isascii() or any(c.isspace() for c in v) for v in keys.values()):
        raise RuntimeError('set the local Langfuse project keys in the host environment')
    # No ambient OTLP exporters, proxy variables, model credentials or target paths.
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'SSL_CERT_FILE') if k in os.environ}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, '-m', 'code_reviewer.tracing.langfuse'],
                            input=json.dumps(dict(trace=value, keys=keys), separators=(',', ':')), text=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                            cwd=str(Path(__file__).resolve().parents[3]), timeout=EXPORT_TIMEOUT)
    if result.returncode:
        raise RuntimeError('local Langfuse export failed; check service, project keys and tracing extra')


def send(value, keys, *, exporter=None):
    """Public OTel timestamps + public Langfuse wrappers; no legacy ingestion API."""
    from langfuse import Langfuse, LangfuseSpan, LangfuseAgent, LangfuseGeneration, LangfuseTool
    from opentelemetry.context import Context
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.id_generator import IdGenerator
    from opentelemetry.sdk.trace.export import SpanExportResult, SpanExporter

    validate(value)
    trace_id = int(value['trace_id'], 16)

    class Ids(IdGenerator):
        id_ = 1
        def generate_trace_id(self):
            return trace_id
        def generate_span_id(self):
            return self.id_

    if exporter is None:
        import base64
        import requests
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        class LocalSession(requests.Session):
            def request(self, method, url, **kwargs):
                if url != BASE_URL + '/api/public/otel/v1/traces':
                    raise ValueError('nonlocal trace destination refused')
                kwargs['allow_redirects'] = False
                return super().request(method, url, **kwargs)
        session = LocalSession()
        session.trust_env = False
        auth = base64.b64encode(('%s:%s' % (keys['LANGFUSE_PUBLIC_KEY'], keys['LANGFUSE_SECRET_KEY'])).encode()).decode()
        exporter = OTLPSpanExporter(endpoint=BASE_URL + '/api/public/otel/v1/traces',
            headers={'Authorization': 'Basic ' + auth, 'x-langfuse-ingestion-version': '4'},
            timeout=3, session=session)

    class CheckedExporter(SpanExporter):
        failed = False
        def export(self, spans):
            status = exporter.export(spans)
            self.failed |= status != SpanExportResult.SUCCESS
            return status
        def shutdown(self):
            exporter.shutdown()

    checked, ids = CheckedExporter(), Ids()
    provider = TracerProvider(id_generator=ids, shutdown_on_exit=False)
    client = Langfuse(public_key=keys['LANGFUSE_PUBLIC_KEY'], secret_key=keys['LANGFUSE_SECRET_KEY'],
                      base_url=BASE_URL, tracing_enabled=True, environment='local', release='code-reviewer-0.1.0',
                      timeout=3, debug=False, sample_rate=1, tracer_provider=provider, span_exporter=checked,
                      should_export_span=lambda span: span.instrumentation_scope.name == 'code-reviewer')
    tracer = provider.get_tracer('code-reviewer')
    wrappers = dict(span=LangfuseSpan, agent=LangfuseAgent, generation=LangfuseGeneration, tool=LangfuseTool)
    try:
        pending = []
        for row in value['records']:
            ids.id_ = int(row['id'], 16)
            ctx = Context()
            if row['parent']:
                parent = SpanContext(trace_id, int(row['parent'], 16), is_remote=False, trace_flags=TraceFlags(1))
                ctx = set_span_in_context(NonRecordingSpan(parent), ctx)
            span = tracer.start_span(row['name'], context=ctx, start_time=row['start_ns'])
            attrs = row['attributes']
            args = dict(otel_span=span, langfuse_client=client, metadata=attrs,
                        environment='local', level='ERROR' if attrs.get('outcome') == 'error' else 'DEFAULT')
            if row['kind'] == 'generation':
                args['model'] = attrs.get('model')
                args['usage_details'] = {k: attrs[k] for k in ('input', 'output', 'cache_read', 'cache_creation') if k in attrs}
                # Suppress inferred invoice amounts for subscriptions or unknown usage.
                args['cost_details'] = {'total': attrs.get('cost_usd', 0.0)}
            observation = wrappers[row['kind']](**args)
            if row['parent'] is None:
                span.set_attribute('langfuse.trace.name', 'review.run')
                observation.update(input={k: attrs[k] for k in ('mode', 'provider', 'execution', 'passes') if k in attrs},
                                   output={k: attrs[k] for k in ('outcome', 'stop_reason') if k in attrs})
                span.set_attribute('langfuse.trace.metadata.dropped_observations', json.dumps(value['dropped']))
            pending.append((observation, row['end_ns']))
        # Keep parents alive while their children are created: Langfuse's root
        # detection tracks open parents, independently of stored parent IDs.
        for observation, end_time in reversed(pending):
            observation.end(end_time=end_time)
        if not provider.force_flush(timeout_millis=8000) or checked.failed:
            raise RuntimeError('trace export failed')
    finally:
        provider.shutdown()


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(MAX_BYTES + 4096))
        send(payload['trace'], payload['keys'])
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(main())
