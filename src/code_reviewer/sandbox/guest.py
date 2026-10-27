"""One unprivileged guest job; bootstrap and reports stay off the VM console."""
from __future__ import annotations

import base64
import json
import math
import os
import resource
import socket
from pathlib import Path

from ..backends.openai import OpenAIRunner, TokenPrices, request_response
from ..config import Config
from ..engine import Engine
from ..shared import git_ops
from .capsule import MAX_CAPSULE
from .git_input import bounded_export, prepare_repository
from .network import PROXY
from .probes import file_probes, network_probes
from .wire import BOOT_PORT, RESULT_LIMIT, receive, receive_json, send_json

ARTIFACTS = {'report.md', 'summary.md', 'stats.json', 'boundary-report.json'}
MAX_ARTIFACT = 4 * 1024 * 1024
MAX_EXPORT = 8 * 1024 * 1024


def validate_settings(value):
    keys = {'model', 'prices', 'mode', 'passes', 'parallel', 'budget', 'call_budget', 'probe_only'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('invalid trusted settings')
    if not isinstance(value['model'], str) or not 1 <= len(value['model']) <= 128:
        raise ValueError('explicit model required')
    if value['mode'] not in ('single-agent', 'agent-personas') or type(value['probe_only']) is not bool:
        raise ValueError('invalid mode')
    for name, max_ in (('passes', 10), ('parallel', 4)):
        if type(value[name]) is not int or not 1 <= value[name] <= max_:
            raise ValueError('invalid slot limits')
    for name in ('budget', 'call_budget'):
        amount = value[name]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not .01 <= amount <= 100:
            raise ValueError('invalid cost allowance')
    rates = value['prices']
    if not isinstance(rates, list) or len(rates) != 3:
        raise ValueError('explicit token rates required')
    TokenPrices(*rates)
    return value


def collect_artifacts(paths, key):
    result, total = {}, 0
    for name, path in paths.items():
        if name not in ARTIFACTS or Path(path).is_symlink():
            raise ValueError('unexpected export artifact')
        from .capsule import regular
        data = regular(Path(path), MAX_ARTIFACT)
        data.decode('utf-8')
        total += len(data)
        if total > MAX_EXPORT or (key and key.encode() in data):
            raise ValueError('export bytes refused')
        result[name] = base64.b64encode(data).decode('ascii')
    return result


def run_job(sock, root, *, probe_network=network_probes):
    settings = validate_settings(receive_json(sock))
    ctx = prepare_repository(receive(sock, MAX_CAPSULE), root / 'repo')
    checks = {'file_tools': file_probes(root), 'network': probe_network()}
    send_json(sock, {'kind': 'ready', 'checks': checks})
    secret = receive(sock, 4096).decode('ascii')
    if settings['probe_only']:
        if secret:
            raise ValueError('probe-only job must not receive credentials')
        files = {'boundary-report.json': base64.b64encode(json.dumps(checks).encode()).decode()}
        return {'kind': 'result', 'stop_reason': 'probes_passed', 'files': files, 'checks': checks}
    key = secret
    runner = OpenAIRunner(key, model=settings['model'], prices=TokenPrices(*settings['prices']),
                          transport=lambda payload, token, timeout: request_response(payload, token, timeout, proxy=PROXY))
    cfg = Config(provider='openai', review_mode=settings['mode'], model_reviewer=runner.model, model_comparator=runner.model,
                 model_resolver=runner.model, model_verifier=runner.model, criteria_mode='merge',
                 min_passes=1, max_passes=settings['passes'], persona_passes=settings['passes'],
                 persona_parallel=settings['parallel'], run_budget_usd=settings['budget'],
                 call_budget_usd=settings['call_budget'], call_timeout_s=120, runs_dir=str(root / 'runs'))
    original = git_ops.export_tree
    git_ops.export_tree = bounded_export
    try:
        result = Engine(cfg, runner).review(ctx)
    finally:
        git_ops.export_tree = original
    paths = {'report.md': result.report_path, 'summary.md': result.run_dir / 'summary.md',
             'stats.json': result.run_dir / 'stats.json'}
    return {'kind': 'result', 'stop_reason': result.stop_reason, 'checks': checks,
            'files': collect_artifacts(paths, key)}


def main():
    # init has already configured the NIC and mounted bounded scratch. No model tools can execute these operations.
    root = Path('/work')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_CPU, (900, 900))
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    with socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM) as server:
        server.bind((socket.VMADDR_CID_ANY, BOOT_PORT))
        server.listen(1)
        server.settimeout(120)
        sock, _ = server.accept()
        server.close()
        with sock:
            sock.settimeout(900)
            try:
                result = run_job(sock, root)
            except Exception:
                # Never send exception text: Git/provider errors may contain target data or credentials.
                result = {'kind': 'failed', 'error': 'guest admission, probes or review failed'}
            send_json(sock, result, RESULT_LIMIT)


if __name__ == '__main__':
    main()
