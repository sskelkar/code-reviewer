"""Linux-only controlled OpenAI pilot: one VM, mandatory external egress, private key bootstrap."""
from __future__ import annotations

import argparse
import base64
import fcntl
import getpass
import grp
import json
import os
import pwd
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .capsule import MAX_CAPSULE, regular
from .guest import ARTIFACTS, MAX_ARTIFACT, MAX_EXPORT, validate_settings
from .launch import MAX_INITRD_BYTES, MAX_KERNEL_BYTES, boot_config, verify_file
from .network import Network, TAP, command
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


def exchange(sock, settings, data, output, key_reader):
    send_json(sock, settings)
    send(sock, data, MAX_CAPSULE)
    ready(receive_json(sock))
    print('sandbox: input and boundary probes passed', file=sys.stderr)
    command(['nft', 'list', 'table', 'inet', 'cr_reviewer'])
    key = '' if settings['probe_only'] else key_reader('Disposable OpenAI API key: ')
    if not settings['probe_only'] and (not isinstance(key, str) or not key or len(key) > 4096
                                       or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise ValueError('invalid runtime key')
    send(sock, key.encode(), 4096)
    value = receive_json(sock, RESULT_LIMIT)
    return export_result(value, output, key, settings['probe_only'])


def run(binary, kernel, initrd, capsule, output, settings, *, timeout=900, key_reader=None):
    validate_settings(settings)
    if sys.platform != 'linux' or os.geteuid() != 0 or not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise ValueError('connected pilot requires a dedicated Linux/KVM host and root for scoped network setup')
    if Path(output).exists():
        raise ValueError('output directory must be new')
    if not settings['probe_only'] and key_reader is None and not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ValueError('interactive terminal or explicit private key descriptor required')
    data = regular(Path(capsule), MAX_CAPSULE)
    owner = int(os.environ.get('SUDO_UID', str(os.geteuid())))
    pwd.getpwuid(owner)
    uid = owner if owner else 65534
    if uid == 0:
        uid = 65534
    kvm_gid = grp.getgrnam('kvm').gr_gid
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
                vm = root / 'vm'
                vm.mkdir(mode=0o700)
                for source, dest in ((binary, vm / 'firecracker'), (kernel, vm / 'kernel'), (initrd, vm / 'initrd')):
                    shutil.copyfile(source, dest)
                    dest.chmod(0o500 if dest.name == 'firecracker' else 0o400)
                    os.chown(dest, uid, kvm_gid)
                os.chown(vm, uid, kvm_gid)
                with Network(root, uid, timeout):
                    print('sandbox: external network policy installed; starting review VM', file=sys.stderr)
                    config = boot_config(vm / 'kernel', vm / 'initrd', runtime=True)
                    config['network-interfaces'] = [{'iface_id': 'review', 'host_dev_name': TAP,
                                                     'guest_mac': '06:00:00:00:00:02'}]
                    config['vsock'] = {'guest_cid': 3, 'uds_path': str(vm / 'bootstrap.sock')}
                    path = vm / 'config.json'
                    path.write_text(json.dumps(config))
                    os.chown(path, uid, kvm_gid)
                    path.chmod(0o400)
                    def demote():
                        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
                        os.setgroups([kvm_gid])
                        os.setgid(kvm_gid)
                        os.setuid(uid)
                    proc = subprocess.Popen(['/usr/bin/timeout', '--signal=KILL', str(timeout), str(vm / 'firecracker'), '--no-api', '--config-file', str(path)],
                                            cwd=str(vm), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=demote,
                                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
                    try:
                        with connect_bootstrap(vm / 'bootstrap.sock', proc) as sock:
                            result = exchange(sock, settings, data, output, key_reader or getpass.getpass)
                            assign_output(result, output, owner)
                            return result
                    finally:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('firecracker', 'kernel', 'initrd'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--input', type=Path, required=True)
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
        binary = verify_file(args.firecracker, args.firecracker_sha256, MAX_KERNEL_BYTES)
        kernel = verify_file(args.kernel, args.kernel_sha256, MAX_KERNEL_BYTES)
        initrd = verify_file(args.initrd, args.initrd_sha256, MAX_INITRD_BYTES)
        def read_key(prompt):
            return os.read(args.key_fd, 4097).decode('ascii').rstrip('\r\n')
        result = run(binary, kernel, initrd, args.input, args.output, settings, timeout=args.timeout,
                     key_reader=read_key if args.key_fd is not None else None)
        print(json.dumps(result))
        if not args.probe_only:
            print('Review VM stopped. Revoke the disposable test key after this test.', file=sys.stderr)
        return 0 if result['stop_reason'] in ('probes_passed', 'completed', 'converged', 'max_passes') else 1
    except (Exception, KeyboardInterrupt):
        # Infrastructure/provider exceptions must never disclose a key or guest-controlled console bytes.
        print('sandbox job refused, failed or interrupted; inspect policy/input; no host fallback', file=sys.stderr)
        if not args.probe_only:
            print('If a key/request was released, usage may be unknown; revoke the disposable test key.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
