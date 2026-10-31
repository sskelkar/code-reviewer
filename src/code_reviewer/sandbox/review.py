"""Normal Mac review dispatch using the shared jailed VM and review algorithms."""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError, REPO_ROOT
from ..shared.models import ReviewRefused
from . import settings
from .artifacts import Artifact
from .bundle import build_bundle
from .capsule import regular
from .channel import Channel
from .check import Worker, prepare_worker, stage_inputs
from .connected import export_result, ready
from .wire import RESULT_LIMIT, receive_json, send, send_json


@dataclass
class ExportedReview:
    cfg: object
    run_dir: Path
    stop_reason: str
    summary_text: str

    @property
    def report_path(self):
        return self.run_dir / 'report.md'


def source_url(ref):
    from .acquire import parse_pr
    url = 'https://%s/%s/pull/%d' % (ref.host, ref.slug, ref.number)
    parse_pr(url)
    return url


def configuration_errors(cfg, ref=None, options=None):
    result = settings.errors(cfg)
    try:
        settings.config_from_job(settings.job_settings(cfg), Path('/work'))
        if ref is not None:
            source_url(ref)
    except ValueError as exc:
        result.append(str(exc))
    if options is not None:
        if getattr(options, 'dry_run', False):
            result.append('VM dry-run is not implemented; preflight performs no target or credential activity')
        if getattr(options, 'local_path', None):
            result.append('VM review currently acquires public GitHub PRs inside the VM; --local-path is host-only')
        if getattr(options, 'keep_checkout', False):
            result.append('VM checkouts are ephemeral; --keep-checkout is host-only')
    return result


def converse(channel, cfg, source, key_reader):
    send_json(channel, {'settings': settings.job_settings(cfg), 'source': source})
    value = receive_json(channel, RESULT_LIMIT)
    from .acquire import source_failure
    failure = source_failure(value)
    if failure:
        raise failure
    if value != {'kind': 'credential', 'provider': cfg.provider, 'auth': settings.job_settings(cfg)['auth']}:
        raise ValueError('worker did not complete admission and boundary checks; no credential released')
    key = key_reader(cfg.provider, settings.job_settings(cfg)['auth'])
    send(channel, key.encode('ascii'), 4096)
    value = receive_json(channel, RESULT_LIMIT)
    if not isinstance(value, dict) or set(value) != {'kind', 'result', 'files', 'cleanup'} or value['kind'] != 'finished' or value['cleanup'] is not True:
        raise ValueError('VM review or cleanup failed')
    metadata = value['result']
    if not isinstance(metadata, dict):
        raise ValueError('invalid worker metadata')
    from .vm import CHECKS
    checks = metadata.get('vm_checks')
    if not isinstance(checks, dict) or set(checks) != CHECKS or any(v is not True for v in checks.values()):
        raise ValueError('VMM confinement not confirmed')
    from .snapshot import CHECKS as ISOLATION
    checks = metadata.get('checks', {})
    isolation = checks.get('isolation')
    if not isinstance(isolation, dict) or set(isolation) != ISOLATION or any(v is not True for v in isolation.values()):
        raise ValueError('native isolation not confirmed')
    if checks.get('source_access_revoked') is not True:
        raise ValueError('source access revocation not confirmed')
    from .acquire import pin
    acquired = metadata.get('acquisition')
    if not isinstance(acquired, dict) or acquired.get('source_access_revoked') is not True:
        raise ValueError('missing pinned PR identity')
    pin({k: v for k,v in acquired.items() if k != 'source_access_revoked'}, source)
    ready({'kind': 'ready', 'checks': checks})
    return {'kind': 'result', 'stop_reason': metadata.get('stop_reason'), 'checks': checks, 'files': value['files']}, key


def execute(worker, arguments, cfg, source, key_reader):
    # Explicit no-TTY and a clean launcher environment. SSH carries framed private
    # stdin/stdout; stderr is deliberately discarded rather than echoing secrets.
    proc = subprocess.Popen(['limactl', '--tty=false', 'shell', worker.instance, '--', *arguments],
                            cwd=str(worker.home), env=worker.env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
    try:
        value = converse(Channel(proc.stdout.fileno(), proc.stdin.fileno(), cfg.vm_timeout_s + 60),
                         cfg, source, key_reader)
        proc.stdin.close()
        if proc.wait(timeout=30) != 0:
            raise ValueError('worker review process failed')
        return value
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdin.close()
        proc.stdout.close()


def runtime_inputs(cfg):
    """Check installed inputs only for real execution; preflight stays IO-free."""
    required = [(Path(cfg.vm_artifacts) / 'firecracker.tgz', 'Firecracker archive', 'vm_artifacts'),
                (Path(cfg.vm_artifacts) / 'vmlinux-6.1.188', 'guest kernel', 'vm_artifacts'),
                (Path(cfg.vm_lima_home) / cfg.vm_instance / 'lima.yaml', 'Lima worker definition', 'vm_lima_home/vm_instance')]
    if cfg.provider == 'claude':
        required.append((Path(cfg.vm_claude_binary), 'native CLI binary', 'vm_claude_binary'))
    for path, label, setting in required:
        if not path.is_file():
            raise ConfigError('VM setup missing %s; check %s and provision the dedicated runtime '
                              'in a persistent directory (see docs/sandbox-check.md)' % (label, setting))


def run(cfg, ref, options, *, key_reader=settings.credential):
    failures = configuration_errors(cfg, ref, options)
    if failures:
        raise ReviewRefused('VM review unavailable: ' + '; '.join(failures) + '; no host fallback')
    runtime_inputs(cfg)
    worker = Worker(cfg.vm_lima_home, cfg.vm_instance)
    job = '/tmp/reviewer-check-' + uuid.uuid4().hex
    source = source_url(ref)
    phase = 'artifact staging'
    try:
        with tempfile.TemporaryDirectory(prefix='reviewer-stage-') as name:
            stage = Path(name)
            stage_inputs(cfg.vm_artifacts, stage)
            phase = 'reviewer source bundling'
            bundle = build_bundle(REPO_ROOT, stage/'source.tar')
            builder = regular(REPO_ROOT/'sandbox/firecracker/build-offline-guest.sh', 65536)
            (stage/'build.sh').write_bytes(builder)
            native = cfg.vm_claude_sha256 if cfg.provider == 'claude' else False
            if native:
                Artifact(Path(cfg.vm_claude_binary), native, 300000000).stage(stage/'claude')
            phase = 'dedicated worker startup'
            with worker.session():
                worker.shell('mkdir', '-m', '700', job)
                try:
                    phase = 'runtime image preparation'
                    env, preparation = prepare_worker(worker, stage, job, bundle['sha256'], hashlib.sha256(builder).hexdigest(), native=native)
                    image = preparation['images']['connected']
                    arguments = (*env, 'PYTHONPATH='+image['path']+'/root/opt/reviewer/src', 'python3',
                                 '-m', 'code_reviewer.sandbox.review_worker', '--root', job, '--image', image['path'],
                                 '--image-sha256', image['sha256'], '--timeout', str(cfg.vm_timeout_s))
                    print('sandbox: reviewing pinned PR inside the VM; waiting for boundary checks', file=sys.stderr)
                    phase = 'VM admission, review or result validation'
                    value, key = execute(worker, arguments, cfg, source, key_reader)
                finally:
                    if sys.exc_info()[0] is None:
                        phase = 'remote scratch cleanup'
                    worker.shell('sudo', '-n', 'rm', '-rf', '--', job, timeout=30)
                phase = 'dedicated worker shutdown'
            # Worker shutdown is confirmed before any result is published locally.
            phase = 'local report export'
            runs = cfg.runs_path()
            runs.mkdir(mode=0o700, parents=True, exist_ok=True)
            output = runs / ('vm-' + uuid.uuid4().hex)
            result = export_result(value, output, key, False)
            return ExportedReview(cfg, output, result['stop_reason'], (output/'summary.md').read_text(encoding='utf-8'))
    except ConfigError:
        raise
    except (Exception, KeyboardInterrupt):
        # Do not propagate SSH, native-client or provider text to the host console.
        raise ReviewRefused('VM review failed during %s; no host fallback. '
                            'Check the dedicated worker with reviewer sandbox check/recover.' % phase) from None
