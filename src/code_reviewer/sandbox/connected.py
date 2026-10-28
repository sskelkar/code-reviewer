"""Linux VM supervisor, external network policy and private bootstrap/result transport."""
from __future__ import annotations

import argparse
import base64
import fcntl
import getpass
import json
import os
import pwd
import shutil
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

from ..shared.models import ReviewRefused
from .artifacts import Artifact
from .capsule import MAX_CAPSULE, regular
from .guest import ARTIFACTS, MAX_ARTIFACT, MAX_EXPORT, validate_settings
from .runtime import MAX_INITRD_BYTES, MAX_KERNEL_BYTES
from .network import Network, command
from .vm import VirtualMachine
from .wire import BOOT_PORT, RESULT_LIMIT, receive_json, send, send_json

NETWORK_CHECKS = {'direct_public', 'metadata', 'private', 'host_service', 'other_domain', 'literal_ip',
                  'wrong_port', 'private_proxy', 'direct_dns', 'ipv6', 'approved_tls'}
FILE_CHECKS = {'parent', 'absolute', 'symlink', 'no_exec_tool', 'no_write_tool'}


def ready(value):
    if not isinstance(value, dict) or value.get('kind') != 'ready':
        raise ValueError('guest did not pass admission/probes; no key released')
    checks = value.get('checks', {})
    for group, keys in (('network', NETWORK_CHECKS), ('file_tools', FILE_CHECKS)):
        row = checks.get(group)
        if not isinstance(row, dict) or set(row) != keys or any(flag is not True for flag in row.values()):
            raise ValueError('incomplete boundary probes; no key released')
    return value


def export_result(value, output, key, probe_only):
    if not isinstance(value, dict) or value.get('kind') != 'result':
        raise ValueError('guest returned no validated job result')
    ready({'kind': 'ready', 'checks': value.get('checks')})
    encoded = value.get('files')
    expected = {'boundary-report.json'} if probe_only else {'report.md', 'summary.md', 'stats.json'}
    if not isinstance(encoded, dict) or set(encoded) != expected:
        raise ValueError('unexpected report artifact names')
    decoded, total = {}, 0
    for name, body in encoded.items():
        if name not in ARTIFACTS or not isinstance(body, str):
            raise ValueError('invalid report artifact')
        data = base64.b64decode(body, validate=True)
        data.decode('utf-8')
        total += len(data)
        if len(data) > MAX_ARTIFACT or total > MAX_EXPORT or (key and key.encode() in data):
            raise ValueError('report byte/credential limit exceeded')
        if name.endswith('.json'):
            json.loads(data)
        decoded[name] = data
    allowed = {'probes_passed'} if probe_only else {'completed', 'converged', 'max_passes', 'budget_exceeded',
                                                  'incomplete', 'cancelled', 'failures'}
    if value.get('stop_reason') not in allowed:
        raise ValueError('invalid job completion state')
    output = Path(output)
    output.mkdir(mode=0o700)  # refuse to reuse/overwrite a previous result directory
    try:
        for name, data in decoded.items():
            with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
                handle.write(data)
    except BaseException:
        shutil.rmtree(output)
        raise
    return {'stop_reason': value['stop_reason'], 'files': sorted(decoded), 'output': str(output)}


def assign_output(result, output, owner):
    if owner == 0:
        return
    group = pwd.getpwuid(owner).pw_gid
    # Keep the directory inaccessible until all files belong to the invoking operator.
    for name in result['files']:
        os.chown(Path(output) / name, owner, group, follow_symlinks=False)
    os.chown(output, owner, group, follow_symlinks=False)


def connect_bootstrap(path, proc):
    for _ in range(1200):
        if proc.poll() is not None:
            raise RuntimeError('review VM exited before bootstrap')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(.5)
        try:
            sock.connect(str(path))
            sock.sendall(('CONNECT %d\n' % BOOT_PORT).encode())
            line = bytearray()
            while not line.endswith(b'\n') and len(line) < 64:
                chunk = sock.recv(1)
                if not chunk:
                    raise OSError('guest listener not ready')
                line.extend(chunk)
            if not line.startswith(b'OK '):
                raise OSError('guest listener not ready')
            sock.settimeout(900)
            return sock
        except OSError:
            sock.close()
            time.sleep(.05)
    raise TimeoutError('private bootstrap deadline exceeded')


def exchange(sock, settings, data, output, key_reader, *, network=None, source=None):
    send_json(sock, settings)
    acquired = None
    if source is not None:
        from .acquire import pin
        send_json(sock, {'kind': 'github-pr', 'url': source})
        value = receive_json(sock)
        if not isinstance(value, dict) or set(value) != {'kind', 'source'} or value['kind'] != 'acquired':
            raise ValueError('public acquisition did not complete')
        acquired = pin(value['source'], source)
        network.inference()
        send_json(sock, {'kind': 'source-access-revoked'})
    else:
        send(sock, data, MAX_CAPSULE)
    readiness = ready(receive_json(sock))
    if acquired and readiness['checks'].get('source_access_revoked') is not True:
        raise ValueError('source denial probes did not pass')
    print('sandbox: input and boundary probes passed', file=sys.stderr)
    command(['nft', 'list', 'table', 'inet', 'cr_reviewer'])
    key = '' if settings['probe_only'] else key_reader('Disposable OpenAI API key: ')
    if not settings['probe_only'] and (not isinstance(key, str) or not key or len(key) > 4096
                                       or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise ValueError('invalid runtime key')
    send(sock, key.encode(), 4096)
    value = receive_json(sock, RESULT_LIMIT)
    if acquired and (not isinstance(value.get('checks'), dict) or value['checks'].get('source_access_revoked') is not True):
        raise ValueError('result does not confirm source revocation')
    result = export_result(value, output, key, settings['probe_only'])
    if acquired:
        result['acquisition'] = dict(acquired, source_access_revoked=True)
    return result


def run(binary, kernel, initrd, capsule, output, settings, *, timeout=900, key_reader=None, jailer=None, source=None):
    validate_settings(settings)
    if not settings['probe_only']:
        from ..security import require_ready
        require_ready('openai')
        raise ReviewRefused('qualified credential/execution dispatch is not integrated; no legacy key path')
    if sys.platform != 'linux' or os.geteuid() != 0 or not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise ValueError('connected pilot requires a dedicated Linux/KVM host and root for scoped network setup')
    if Path(output).exists():
        raise ValueError('output directory must be new')
    if not settings['probe_only'] and key_reader is None and not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ValueError('interactive terminal or explicit private key descriptor required')
    if (capsule is None) == (source is None):
        raise ValueError('select exactly one capsule or public PR input')
    if source is not None:
        from .acquire import parse_pr
        parse_pr(source)
    data = regular(Path(capsule), MAX_CAPSULE) if capsule is not None else None
    owner = int(os.environ.get('SUDO_UID', str(os.geteuid())))
    pwd.getpwuid(owner)
    if not isinstance(jailer, Artifact):
        raise ValueError('matching pinned Firecracker jailer required')
    def expired(*args):
        raise TimeoutError('whole-job deadline exceeded')
    def interrupted(*args):
        raise KeyboardInterrupt
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGHUP)}
    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        with Path('/run/code-reviewer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one connected job per Linux worker
            with tempfile.TemporaryDirectory(prefix='reviewer-job-', dir='/tmp') as name:
                root = Path(name)
                root.chmod(0o711)
                with Network(root, 65534, timeout, acquisition=source is not None) as network:
                    print('sandbox: external network policy installed; starting jailed review VM', file=sys.stderr)
                    with VirtualMachine(root, binary, jailer, kernel, initrd, timeout=timeout) as vm:
                        with connect_bootstrap(vm.socket, vm.proc) as sock:
                            confinement = vm.confinement()
                            result = exchange(sock, settings, data, output, key_reader or getpass.getpass,
                                              network=network, source=source)
                            result['vm_checks'] = confinement
                            assign_output(result, output, owner)
                            return result
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('firecracker', 'jailer', 'kernel', 'initrd'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-sha256', required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input', type=Path)
    inputs.add_argument('--pr', help='public GitHub PR URL; fetched only inside the review VM')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--prices', nargs=3, type=float, required=True, metavar=('INPUT', 'CACHED', 'OUTPUT'))
    parser.add_argument('--mode', choices=('single-agent', 'agent-personas'), default='single-agent')
    parser.add_argument('--passes', type=int, default=1)
    parser.add_argument('--parallel', type=int, default=1)
    parser.add_argument('--budget', type=float, default=3)
    parser.add_argument('--call-budget', type=float, default=.5)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--probe-only', action='store_true')
    parser.add_argument('--key-fd', type=int, help='optional inherited private descriptor; otherwise use hidden prompt')
    args = parser.parse_args()
    if not 60 <= args.timeout <= 1800 or (args.probe_only and args.key_fd is not None):
        parser.error('deadline must be 60..1800; probe-only jobs accept no key')
    settings = dict(model=args.model, prices=args.prices, mode=args.mode, passes=args.passes,
                    parallel=args.parallel, budget=args.budget, call_budget=args.call_budget, probe_only=args.probe_only)
    try:
        binary = Artifact(args.firecracker, args.firecracker_sha256, MAX_KERNEL_BYTES)
        jailer = Artifact(args.jailer, args.jailer_sha256, MAX_KERNEL_BYTES)
        kernel = Artifact(args.kernel, args.kernel_sha256, MAX_KERNEL_BYTES)
        initrd = Artifact(args.initrd, args.initrd_sha256, MAX_INITRD_BYTES)
        def read_key(prompt):
            return os.read(args.key_fd, 4097).decode('ascii').rstrip('\r\n')
        result = run(binary, kernel, initrd, args.input, args.output, settings, timeout=args.timeout,
                     key_reader=read_key if args.key_fd is not None else None, jailer=jailer, source=args.pr)
        print(json.dumps(result))
        if not args.probe_only:
            print('Review VM stopped. Revoke the disposable test key after this test.', file=sys.stderr)
        return 0 if result['stop_reason'] in ('probes_passed', 'completed', 'converged', 'max_passes') else 1
    except ReviewRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):
        # Infrastructure/provider exceptions must never disclose a key or guest-controlled console bytes.
        print('sandbox job refused, failed or interrupted; inspect policy/input; no host fallback', file=sys.stderr)
        if not args.probe_only:
            print('If a key/request was released, usage may be unknown; revoke the disposable test key.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
