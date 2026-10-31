"""One unprivileged guest job; bootstrap and reports stay off the VM console."""
from __future__ import annotations

import base64
import json
import math
import os
import resource
import socket
from pathlib import Path

from ..backends.openai import TokenPrices, request_response
from ..backends.factory import create_runner
from ..config import Config
from ..engine import Engine
from ..shared import git_ops
from .acquire import AcquisitionUnavailable, MetadataUnavailable
from .capsule import MAX_CAPSULE
from .git_input import bounded_export, prepare_repository
from .network import PROXY
from .probes import file_probes, network_probes
from .wire import BOOT_PORT, RESULT_LIMIT, receive, receive_json, send_json

ARTIFACTS = {'report.md', 'summary.md', 'stats.json', 'boundary-report.json'}
MAX_ARTIFACT = 4 * 1024 * 1024
MAX_EXPORT = 8 * 1024 * 1024


def validate_settings(value):
    if isinstance(value, dict) and 'config' in value:
        from .settings import config_from_job
        config_from_job(value, Path('/work'))
        return value
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


def run_job(sock, root, *, probe_network=network_probes, seal=None):
    settings = validate_settings(receive_json(sock))
    from .settings import provider
    selected = provider(settings)
    if sock is not None:
        sock.settimeout(settings.get('timeout_s', 900))
    data = receive(sock, MAX_CAPSULE)
    acquired = None
    if data.startswith(b'{'):
        from .acquire import in_guest, pin
        source = json.loads(data)
        if not isinstance(source, dict) or set(source) != {'kind', 'url'} or source['kind'] != 'github-pr':
            raise ValueError('invalid public acquisition request')
        ctx = in_guest(source['url'], root / 'repo')
        acquired = pin(dict(repo=ctx.repo, number=ctx.number, base=ctx.base_sha,
                            head=ctx.head_sha, merge_base=ctx.merge_base), source['url'])
        from .probes import source_tunnel
        with source_tunnel(selected) as tunnel:
            send_json(sock, {'kind': 'acquired', 'source': acquired})
            if receive_json(sock) != {'kind': 'source-access-revoked'}:
                raise ValueError('source access revocation not acknowledged')
            if tunnel.recv(1) != b'':
                raise ValueError('established source tunnel survived revocation')
    else:
        ctx = prepare_repository(data, root / 'repo')
    snapshot = None
    if seal is not None:
        snapshot = seal(ctx, root, exported=True) if acquired else seal(ctx, root)
    checks = {'file_tools': file_probes(root), 'network': probe_network(selected) if probe_network is network_probes else probe_network()}
    if acquired:
        from .probes import source_denied
        checks['source_access_revoked'] = source_denied()
    if snapshot is not None:
        from .snapshot import probes
        checks['isolation'] = probes(snapshot)
    send_json(sock, {'kind': 'ready', 'checks': checks})
    secret = receive(sock, 4096).decode('ascii')
    if settings['probe_only']:
        if secret:
            raise ValueError('probe-only job must not receive credentials')
        files = {'boundary-report.json': base64.b64encode(json.dumps(checks).encode()).decode()}
        return {'kind': 'result', 'stop_reason': 'probes_passed', 'files': files, 'checks': checks}
    key = secret
    if 'config' in settings:
        from .settings import config_from_job
        cfg = config_from_job(settings, root)
    else:
        cfg = Config(provider='openai', review_mode=settings['mode'], model_reviewer=settings['model'], model_comparator=settings['model'],
                     model_resolver=settings['model'], model_verifier=settings['model'], criteria_mode='merge',
                     min_passes=1, max_passes=settings['passes'], persona_passes=settings['passes'],
                     persona_parallel=settings['parallel'], run_budget_usd=settings['budget'],
                     call_budget_usd=settings['call_budget'], call_timeout_s=120, runs_dir=str(root / 'runs'))
    if 'config' not in settings:
        cfg.openai_input_price, cfg.openai_cached_input_price, cfg.openai_output_price = settings['prices']
    env = {name: os.environ[name] for name in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL',
           'SSL_CERT_FILE', 'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL') if name in os.environ}
    env.update({'HTTPS_PROXY': 'http://%s:%d' % PROXY,
                'CLAUDE_CONFIG_DIR': str(root / 'home' / '.claude'),
                'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1'})
    runner = create_runner(cfg, selected_credential=key, env=env,
                           transport=lambda payload, token, timeout: request_response(payload, token, timeout, proxy=PROXY))
    if snapshot is not None:
        result = Engine(cfg, runner).review(ctx, prepared_checkout=snapshot)
    else:
        # Unprivileged fixture seam: ordinary tests never mount or change UID.
        original = git_ops.export_tree
        git_ops.export_tree = bounded_export
        try:
            result = Engine(cfg, runner).review(ctx)
        finally:
            git_ops.export_tree = original
    with result.report_path.open('a', encoding='utf-8') as report:
        report.write('\n## VM export\n\nOnly report.md, summary.md and stats.json are retained locally. '
                     'Artifact paths elsewhere in this report refer to ephemeral guest files; '
                     'raw prompts, replies and checkout files are destroyed with the VM.\n')
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
    with socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM) as server:
        server.bind((socket.VMADDR_CID_ANY, BOOT_PORT))
        server.listen(1)
        server.settimeout(120)
        sock, _ = server.accept()
        server.close()
        with sock:
            sock.settimeout(900)
            try:
                from .snapshot import prepare
                result = run_job(sock, root, seal=prepare)
            except (MetadataUnavailable, AcquisitionUnavailable) as exc:
                result = exc.frame()
            except Exception:
                # Never send exception text: Git/provider errors may contain target data or credentials.
                result = {'kind': 'failed', 'error': 'guest admission, probes or review failed'}
            send_json(sock, result, RESULT_LIMIT)


if __name__ == '__main__':
    main()
