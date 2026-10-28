"""Own Mac-to-Lima deployment checks, with optional in-guest public acquisition; no keys."""
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
from .runtime import MAX_KERNEL_BYTES, collect
from .openai_smoke import RESULT_PREFIX as OPENAI_PREFIX, parse_result

# Previously verified upstream ARM64 artifacts. Changing versions requires requalification.
FIRECRACKER_SHA = 'fe726e0b43c04363ac07e358be4dee982c3947c65ed3ae10c770fef5e1cd756c'
JAILER_SHA = '4d8d2dd4dfc1d47932b2bd261479181dd0c54f0a2f32714421a7e7f9f07ddec8'
ARCHIVE_SHA = 'e351ebe4f7a16b5873bbd51005d2e6767103cff4d5ebc829df2d3f95a93e2256'
KERNEL_SHA = '52748cec9d75865246d95906021fea6754bc0abc2cba0cfc792dac5ec7f46f83'
FIRECRACKER_MEMBER = 'release-v1.17.0-aarch64/firecracker-v1.17.0-aarch64'
JAILER_MEMBER = 'release-v1.17.0-aarch64/jailer-v1.17.0-aarch64'


def stage_inputs(artifacts, output):
    """Extract one bounded member, then hash the actual executable bytes before transferring."""
    compressed = regular(Path(artifacts) / 'firecracker.tgz', 16 * 1024 * 1024)
    if hashlib.sha256(compressed).hexdigest() != ARCHIVE_SHA:
        raise ValueError('Firecracker archive digest mismatch')
    with tarfile.open(fileobj=io.BytesIO(compressed), mode='r|gz') as archive:
        found, size, count = set(), 0, 0
        expected = {FIRECRACKER_MEMBER: ('firecracker', FIRECRACKER_SHA), JAILER_MEMBER: ('jailer', JAILER_SHA)}
        for member in archive:
            size += member.size
            count += 1
            if count > 100 or size > 128 * 1024 * 1024:
                raise ValueError('Firecracker release archive limits exceeded')
            if member.name not in expected:
                continue
            if member.name in found or not member.isfile() or not 0 < member.size <= MAX_KERNEL_BYTES:
                raise ValueError('invalid Firecracker release member')
            found.add(member.name)
            name, digest = expected[member.name]
            data = archive.extractfile(member).read(MAX_KERNEL_BYTES + 1)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Firecracker release digest mismatch')
            with (output / name).open('xb') as handle:
                handle.write(data)
        if found != set(expected):
            raise ValueError('pinned Firecracker/jailer missing from archive')
    Artifact(Path(artifacts) / 'vmlinux-6.1.188', KERNEL_SHA, MAX_KERNEL_BYTES).stage(output / 'kernel')


def validate_worker(row, instance, *, stopped=True):
    cfg = row.get('config', {})
    ssh, container = cfg.get('ssh', {}), cfg.get('containerd', {})
    if (row.get('name') != instance or row.get('status') not in (('Stopped',) if stopped else ('Stopped', 'Running'))
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
    def session(self, *, recover=False):
        # Separate invocations must not race to start/stop the same dedicated worker.
        lock_path = self.home / self.instance / '.reviewer-check.lock'
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'w') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise ValueError('unsafe worker lock')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = self.state()
            validate_worker(state, self.instance, stopped=not recover)
            try:
                if recover and state['status'] == 'Running':
                    print('sandbox: stopping dedicated worker to discard abandoned workloads', file=sys.stderr)
                    self.call('stop', '--tty=false', self.instance, timeout=60)
                    if self.state().get('status') != 'Stopped':
                        raise RuntimeError('could not confirm worker shutdown before recovery')
                print('sandbox: starting dedicated Lima worker', file=sys.stderr)
                self.call('start', '--tty=false', self.instance)
                yield self
            finally:
                print('sandbox: stopping dedicated Lima worker', file=sys.stderr)
                self.call('stop', '--tty=false', self.instance, timeout=60)
                if self.state().get('status') != 'Stopped':
                    raise RuntimeError('could not confirm worker shutdown')


def boundary_result(text, *, offline=False):
    from .check_worker import RESULT_PREFIX
    rows = [line[len(RESULT_PREFIX):] for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(rows) != 1:
        raise ValueError('expected exactly one boundary result')
    value = json.loads(rows[0])
    if not isinstance(value, dict):
        raise ValueError('invalid VM check result')
    from .vm import CHECKS as VM_CHECKS
    checks = value.get('vm_checks')
    if not isinstance(checks, dict) or set(checks) != VM_CHECKS or any(flag is not True for flag in checks.values()):
        raise ValueError('host-observed VMM confinement not confirmed')
    if offline:
        parse_result(OPENAI_PREFIX + json.dumps(value.get('offline')))
    else:
        if value.get('network_cleanup') is not True:
            raise ValueError('host network cleanup not confirmed')
        ready({'kind': 'ready', 'checks': value.get('checks')})
        from .snapshot import CHECKS as ISOLATION_CHECKS
        checks = value['checks'].get('isolation')
        if not isinstance(checks, dict) or set(checks) != ISOLATION_CHECKS or any(flag is not True for flag in checks.values()):
            raise ValueError('native guest isolation not confirmed')
    digest = value.get('initrd_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('invalid image digest')
    return value


def prepared_images(text, source_sha, builder_sha):
    value = json.loads(text)
    if (not isinstance(value, dict) or value.get('source_sha256') != source_sha
            or value.get('builder_sha256') != builder_sha or not re.fullmatch('[a-f0-9]{64}', value.get('runtime_sha256', ''))
            or type(value.get('stale_directories_removed')) is not int or value['stale_directories_removed'] < 0):
        raise ValueError('invalid image preparation result')
    images = value.get('images')
    if not isinstance(images, dict) or set(images) != {'offline', 'connected'}:
        raise ValueError('both prepared image identities required')
    for image in images.values():
        if (not isinstance(image, dict) or type(image.get('reused')) is not bool
                or not re.fullmatch(r'/var/lib/code-reviewer/images/[a-f0-9]{64}', image.get('path', ''))
                or not re.fullmatch('[a-f0-9]{64}', image.get('sha256', ''))):
            raise ValueError('invalid prepared image identity')
    return value


def prepare_worker(worker, stage, job, bundle_sha, builder_sha, *, native=False):
    """Share trusted deployment preparation between checks and normal reviews."""
    filenames = ['firecracker', 'jailer', 'kernel', 'source.tar', 'build.sh']
    if native:
        filenames.append('claude')
    for filename in filenames:
        worker.call('copy', stage / filename, worker.instance + ':' + job + '/' + filename)
    print('sandbox: preparing pinned reviewer images (reuse unchanged inputs)', file=sys.stderr)
    uid = int(worker.shell('id', '-u').strip())
    if not 1 <= uid < 2**31:
        raise ValueError('expected an unprivileged worker operator')
    root_env = ('sudo', '-n', 'env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'HOME='+job,
                'SUDO_UID='+str(uid), 'PYTHONDONTWRITEBYTECODE=1', 'GIT_CONFIG_NOSYSTEM=1', 'GIT_CONFIG_GLOBAL=/dev/null')
    worker.shell(*root_env, 'sh', job+'/build.sh', job+'/source.tar', job+'/bootstrap', 'sources-only')
    extra = ('--claude', job+'/claude', '--claude-sha256', native) if native else ()
    preparation = prepared_images(worker.shell(*root_env,
        'PYTHONPATH='+job+'/bootstrap/root/opt/reviewer/src',
        'python3', '-m', 'code_reviewer.sandbox.prepare', '--source', job+'/source.tar',
        '--source-sha256', bundle_sha, '--builder', job+'/build.sh', '--builder-sha256', builder_sha,
        '--keep-job', job, *extra, timeout=600), bundle_sha, builder_sha)
    images = preparation['images']
    print('sandbox: images '+', '.join(label+(' reused' if image['reused'] else ' built')
                                      for label,image in images.items()), file=sys.stderr)
    return root_env, preparation


def run_check(artifacts, lima_home, instance, output=None, *, lifecycle=False, source=None):
    if source is not None:
        from .acquire import parse_pr
        parse_pr(source)  # Syntax only; no host target/network activity.
    if sys.platform != 'darwin':
        raise ValueError('this lifecycle command currently supports macOS with a prepared Lima worker')
    if output is not None and Path(output).exists():
        raise ValueError('output directory must be new')
    worker = Worker(lima_home, instance)
    job = '/tmp/reviewer-check-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='reviewer-check-stage-') as name:
        stage = Path(name)
        stage_inputs(artifacts, stage)
        bundle = build_bundle(REPO_ROOT, stage / 'source.tar')
        builder = regular(REPO_ROOT / 'sandbox/firecracker/build-offline-guest.sh', 64 * 1024)
        builder_sha = hashlib.sha256(builder).hexdigest()
        (stage / 'build.sh').write_bytes(builder)
        with worker.session():
            worker.shell('mkdir', '-m', '700', job)
            try:
                root_env, preparation = prepare_worker(worker, stage, job, bundle['sha256'], builder_sha)
                images = preparation['images']
                print('sandbox: running both flows with simulated OpenAI replies (no network)', file=sys.stderr)
                arguments = (*root_env, 'PYTHONPATH=' + images['connected']['path'] + '/root/opt/reviewer/src',
                    'python3', '-m', 'code_reviewer.sandbox.check_worker', '--root', job,
                    '--firecracker-sha256', FIRECRACKER_SHA, '--jailer-sha256', JAILER_SHA, '--kernel-sha256', KERNEL_SHA)
                offline = boundary_result(worker.shell(*arguments, '--offline', '--image-dir', images['offline']['path'],
                    '--initrd-sha256', images['offline']['sha256'], timeout=240), offline=True)
                if offline['initrd_sha256'] != images['offline']['sha256']:
                    raise ValueError('offline VM used an unexpected image')
                print('sandbox: checking connected admission, file tools, egress and cleanup (no key)', file=sys.stderr)
                boundary = boundary_result(worker.shell(*arguments, '--image-dir', images['connected']['path'],
                    '--initrd-sha256', images['connected']['sha256'], *(['--pr', source] if source else []),
                    timeout=420 if source else 240))
                if source:
                    from .acquire import pin
                    acquired = boundary.get('acquisition')
                    if (not isinstance(acquired, dict) or acquired.get('source_access_revoked') is not True
                            or boundary['checks'].get('source_access_revoked') is not True):
                        raise ValueError('source-access revocation not confirmed')
                    pin({k: v for k, v in acquired.items() if k != 'source_access_revoked'}, source)
                if boundary['initrd_sha256'] != images['connected']['sha256']:
                    raise ValueError('connected VM used an unexpected image')
                lifecycle_result = None
                if lifecycle:
                    print('sandbox: checking cancellation and supervisor SIGKILL/deadline (no key)', file=sys.stderr)
                    from .check_worker import RESULT_PREFIX
                    text = worker.shell(*arguments, '--lifecycle', '--image-dir', images['offline']['path'],
                        '--initrd-sha256', images['offline']['sha256'], timeout=120)
                    rows = [line[len(RESULT_PREFIX):] for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
                    if len(rows) != 1:
                        raise ValueError('expected one lifecycle check result')
                    lifecycle_result = json.loads(rows[0])
                    if (set(lifecycle_result) != {'cancel_stopped_vm','survived_supervisor_sigkill','deadline_stopped_vm','runtime_mount_removed'}
                            or any(flag is not True for flag in lifecycle_result.values())):
                        raise ValueError('lifecycle checks did not pass')
            finally:
                failing = sys.exc_info()[0] is not None
                try:
                    worker.shell('sudo', '-n', 'rm', '-rf', '--', job, timeout=30)
                except Exception:
                    if not failing:
                        raise
                    print('sandbox: remote scratch cleanup also failed; worker shutdown/recovery is required', file=sys.stderr)
    result = {'format': 1, 'kind': 'sandbox-check', 'live': False, 'production_qualified': False,
              'source_sha256': bundle['sha256'], 'firecracker_sha256': FIRECRACKER_SHA, 'jailer_sha256': JAILER_SHA,
              'kernel_sha256': KERNEL_SHA,
              'offline_initrd_sha256': offline['initrd_sha256'], 'offline': offline['offline'],
              'offline_vm_checks': offline['vm_checks'], 'boundary': boundary, 'worker_stopped': True,
              'image_preparation': preparation}
    if lifecycle_result is not None:
        result['lifecycle'] = lifecycle_result
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
        if args.sandbox_command == 'recover':
            from .recover import run_recovery
            count = run_recovery(args.lima_home, args.instance)
            print('Sandbox recovery completed: removed %d stale job directories; worker stopped.' % count)
            return 0
        report = run_check(args.artifacts, args.lima_home, args.instance, args.output,
                           lifecycle=args.lifecycle, source=args.pr)
        print('Sandbox check passed: both flows, boundary probes and cleanup. No key or model request used.')
        print('Lima worker stopped. This check does not qualify live reviews.')
        print('report: %s' % report)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('sandbox %s failed: %s' % (args.sandbox_command, str(exc) or 'interrupted'), file=sys.stderr)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
