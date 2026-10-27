"""Own the Mac-to-Lima-to-Firecracker deployment check; never accept repository inputs or keys."""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import signal
import stat
import sys
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..config import REPO_ROOT
from .artifacts import Artifact
from .bundle import build_bundle
from .capsule import regular
from .connected import ready
from .launch import MAX_KERNEL_BYTES, collect
from .openai_smoke import RESULT_PREFIX as OPENAI_PREFIX, parse_result

# Previously verified upstream ARM64 artifacts. Changing versions requires requalification.
FIRECRACKER_SHA = 'fe726e0b43c04363ac07e358be4dee982c3947c65ed3ae10c770fef5e1cd756c'
ARCHIVE_SHA = 'e351ebe4f7a16b5873bbd51005d2e6767103cff4d5ebc829df2d3f95a93e2256'
KERNEL_SHA = '52748cec9d75865246d95906021fea6754bc0abc2cba0cfc792dac5ec7f46f83'
FIRECRACKER_MEMBER = 'release-v1.17.0-aarch64/firecracker-v1.17.0-aarch64'


def stage_inputs(artifacts, output):
    """Extract one bounded member, then hash the actual executable bytes before transferring."""
    compressed = regular(Path(artifacts) / 'firecracker.tgz', 16 * 1024 * 1024)
    if hashlib.sha256(compressed).hexdigest() != ARCHIVE_SHA:
        raise ValueError('Firecracker archive digest mismatch')
    with tarfile.open(fileobj=io.BytesIO(compressed), mode='r|gz') as archive:
        found, size, count = False, 0, 0
        for member in archive:
            size += member.size
            count += 1
            if count > 100 or size > 128 * 1024 * 1024:
                raise ValueError('Firecracker release archive limits exceeded')
            if member.name != FIRECRACKER_MEMBER:
                continue
            if found or not member.isfile() or not 0 < member.size <= MAX_KERNEL_BYTES:
                raise ValueError('invalid Firecracker release member')
            found = True
            data = archive.extractfile(member).read(MAX_KERNEL_BYTES + 1)
            if hashlib.sha256(data).hexdigest() != FIRECRACKER_SHA:
                raise ValueError('Firecracker release digest mismatch')
            with (output / 'firecracker').open('xb') as handle:
                handle.write(data)
        if not found:
            raise ValueError('pinned Firecracker binary missing from archive')
    Artifact(Path(artifacts) / 'vmlinux-6.1.188', KERNEL_SHA, MAX_KERNEL_BYTES).stage(output / 'kernel')


def validate_worker(row, instance):
    cfg = row.get('config', {})
    ssh, container = cfg.get('ssh', {}), cfg.get('containerd', {})
    if (row.get('name') != instance or row.get('status') != 'Stopped'
            or row.get('vmType') != 'vz' or row.get('arch') != 'aarch64'
            or not isinstance(row.get('memory'), int) or row['memory'] < 6 * 1024 ** 3
            or cfg.get('plain') is not True or cfg.get('nestedVirtualization') is not True
            or cfg.get('mounts') or cfg.get('propagateProxyEnv') is not False
            or any(ssh.get(k) is not False for k in ('forwardAgent', 'forwardX11', 'loadDotSSHPubKeys'))
            or any(container.get(k) is not False for k in ('system', 'user'))):
        raise ValueError('requires a stopped, dedicated ARM64 VZ worker: nested KVM, 6 GiB, plain, no mounts/forwarding/containerd')


class Worker:
    def __init__(self, home, instance):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', instance):
            raise ValueError('invalid Lima instance name')
        self.home, self.instance = Path(home).resolve(), instance
        self.env = {'PATH': os.environ.get('PATH', '/opt/homebrew/bin:/usr/bin:/bin'),
                    'HOME': str(Path.home()), 'LIMA_HOME': str(self.home), 'LANG': 'C.UTF-8'}

    def call(self, *args, timeout=180):
        return collect(['limactl', *map(str, args)], self.home, timeout, env=self.env)

    def shell(self, *args, timeout=180):
        return self.call('shell', self.instance, '--', *args, timeout=timeout)

    def state(self):
        return json.loads(self.call('list', self.instance, '--json', timeout=15))

    @contextmanager
    def session(self):
        # Separate invocations must not race to start/stop the same dedicated worker.
        lock_path = self.home / self.instance / '.reviewer-check.lock'
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'w') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise ValueError('unsafe worker lock')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            validate_worker(self.state(), self.instance)
            try:
                print('sandbox: starting dedicated Lima worker', file=sys.stderr)
                self.call('start', '--tty=false', self.instance)
                yield self
            finally:
                print('sandbox: stopping dedicated Lima worker', file=sys.stderr)
                self.call('stop', '--tty=false', self.instance, timeout=60)
                if self.state().get('status') != 'Stopped':
                    raise RuntimeError('could not confirm worker shutdown')


def boundary_result(text):
    from .check_worker import RESULT_PREFIX
    rows = [line[len(RESULT_PREFIX):] for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(rows) != 1:
        raise ValueError('expected exactly one boundary result')
    value = json.loads(rows[0])
    if not isinstance(value, dict) or value.get('network_cleanup') is not True:
        raise ValueError('host network cleanup not confirmed')
    ready({'kind': 'ready', 'checks': value.get('checks')})
    digest = value.get('initrd_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('invalid image digest')
    return value


def run_check(artifacts, lima_home, instance, output=None):
    if sys.platform != 'darwin':
        raise ValueError('this lifecycle command currently supports macOS with a prepared Lima worker')
    if output is not None and Path(output).exists():
        raise ValueError('output directory must be new')
    worker = Worker(lima_home, instance)
    job = '/tmp/reviewer-check-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='reviewer-check-stage-') as name:
        stage = Path(name)
        stage_inputs(artifacts, stage)
        source = build_bundle(REPO_ROOT, stage / 'source.tar')
        builder = regular(REPO_ROOT / 'sandbox/firecracker/build-offline-guest.sh', 64 * 1024)
        (stage / 'build.sh').write_bytes(builder)
        with worker.session():
            worker.shell('mkdir', '-m', '700', job)
            try:
                for filename in ('firecracker', 'kernel', 'source.tar', 'build.sh'):
                    worker.call('copy', stage / filename, worker.instance + ':' + job + '/' + filename)
                worker.shell('chmod', '500', job + '/firecracker')
                print('sandbox: building current reviewer images', file=sys.stderr)
                for directory, workload in (('offline', 'openai-offline'), ('connected', 'connected')):
                    worker.shell('sh', job + '/build.sh', job + '/source.tar', job + '/' + directory, workload)
                print('sandbox: running both flows with simulated OpenAI replies (no network)', file=sys.stderr)
                image = job + '/offline/initrd.cpio.gz'
                sha = worker.shell('sha256sum', image).split()[0]
                uid = int(worker.shell('id', '-u').strip())
                if not 1 <= uid <= 2 ** 31 - 1:
                    raise ValueError('expected an unprivileged worker operator')
                offline_text = worker.shell('sudo', '-n', 'setpriv', '--reuid', str(uid), '--regid', 'kvm',
                                            '--groups', 'kvm', '--no-new-privs', 'env', '-i',
                                            'PATH=/usr/bin:/bin', 'HOME=' + job,
                                            'PYTHONPATH=' + job + '/offline/root/opt/reviewer/src',
                                            'python3', '-m', 'code_reviewer.sandbox.launch',
                                            '--firecracker', job + '/firecracker', '--firecracker-sha256', FIRECRACKER_SHA,
                                            '--kernel', job + '/kernel', '--kernel-sha256', KERNEL_SHA,
                                            '--initrd', image, '--initrd-sha256', sha, '--workload', 'openai-offline')
                # launch already validates the guest envelope; validate it again across the worker boundary.
                offline = parse_result(OPENAI_PREFIX + offline_text.strip())
                print('sandbox: checking connected admission, file tools, egress and cleanup (no key)', file=sys.stderr)
                boundary = boundary_result(worker.shell('sudo', '-n', 'env',
                    'PYTHONPATH=' + job + '/connected/root/opt/reviewer/src', 'PATH=/usr/bin:/bin',
                    'HOME=' + job, 'GIT_CONFIG_NOSYSTEM=1', 'GIT_CONFIG_GLOBAL=/dev/null',
                    'python3', '-m', 'code_reviewer.sandbox.check_worker', '--root', job,
                    '--firecracker-sha256', FIRECRACKER_SHA, '--kernel-sha256', KERNEL_SHA, timeout=240))
            finally:
                worker.shell('sudo', '-n', 'rm', '-rf', '--', job, timeout=30)
    result = {'format': 1, 'kind': 'sandbox-check', 'live': False, 'production_qualified': False,
              'source_sha256': source['sha256'], 'firecracker_sha256': FIRECRACKER_SHA, 'kernel_sha256': KERNEL_SHA,
              'offline_initrd_sha256': sha, 'offline': offline, 'boundary': boundary, 'worker_stopped': True}
    if output is None:
        output = Path(tempfile.mkdtemp(prefix='reviewer-sandbox-result-'))
    else:
        output = Path(output)
        output.mkdir(mode=0o700)
    output.chmod(0o700)
    with os.fdopen(os.open(output / 'report.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    return output / 'report.json'


def cli(args):
    def interrupted(*_):
        raise KeyboardInterrupt
    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        report = run_check(args.artifacts, args.lima_home, args.instance, args.output)
        print('Sandbox check passed: both flows, boundary probes and cleanup. No key or model request used.')
        print('Lima worker stopped. This check does not qualify live reviews.')
        print('report: %s' % report)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('sandbox check failed: %s' % (str(exc) or 'interrupted'), file=sys.stderr)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
