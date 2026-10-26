"""Boot an offline smoke or runtime-probe guest; no real-review or credential interface exists yet."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .smoke import RESULT_PREFIX

MAX_OUTPUT_BYTES = 1024 * 1024
MAX_INITRD_BYTES = 256 * 1024 * 1024
MAX_KERNEL_BYTES = 64 * 1024 * 1024


def verify_file(path: Path, expected: str, limit: int) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('expected a bounded regular artifact: %s' % path)
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('expected a lowercase SHA-256 digest')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('artifact digest mismatch: %s' % path)
    return path.resolve()


def boot_config(kernel: Path, initrd: Path, *, runtime=False):
    return {'boot-source': {'kernel_image_path': str(kernel), 'initrd_path': str(initrd),
                           'boot_args': 'console=ttyS0 reboot=k panic=1 pci=off rdinit=/init'},
            'machine-config': {'vcpu_count': 2, 'mem_size_mib': 4096 if runtime else 1024},
            'drives': [], 'network-interfaces': []}


def collect(cmd, cwd, timeout_s, *, env=None, allowed_codes=(0,)):
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True, env=env if env is not None else {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    output = bytearray()
    started = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            selector.register(proc.stderr, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() - started >= timeout_s:
                    raise TimeoutError('offline guest deadline exceeded')
                for key, _ in selector.select(.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            raise ValueError('offline guest output limit exceeded')
            remaining = timeout_s - (time.monotonic() - started)
            proc.wait(timeout=max(.01, remaining))
        if proc.returncode not in allowed_codes:
            raise RuntimeError('offline guest exited with status %d: %s' %
                               (proc.returncode, output[-2000:].decode('utf-8', 'replace')))
        return output.decode('utf-8', 'replace')
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def parse_result(output):
    records = [line[len(RESULT_PREFIX):] for line in output.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(records) != 1:
        raise ValueError('guest must return exactly one synthetic result: ' + output[-2000:])
    data = json.loads(records[0])
    if not isinstance(data, dict) or type(data.get('format')) is not int or data.get('format') != 1 or data.get('synthetic') is not True:
        raise ValueError('invalid synthetic result envelope')
    flows = data.get('flows')
    if not isinstance(flows, dict) or set(flows) != {'single-agent', 'agent-personas'}:
        raise ValueError('both flow results required')
    for mode, count, calls in (('single-agent', 2, 3), ('agent-personas', 4, 8)):
        row = flows[mode]
        if (not isinstance(row, dict) or type(row.get('completed')) is not int or type(row.get('findings')) is not int
                or type(row.get('calls')) is not int or row.get('calls') != calls
                or row.get('completed') != count or row.get('findings') != 1):
            raise ValueError('incomplete synthetic flow: %s' % mode)
        if row.get('stop_reason') != ('max_passes' if mode == 'single-agent' else 'completed'):
            raise ValueError('unexpected synthetic stop reason')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for artifact in ('firecracker', 'kernel', 'initrd'):
        parser.add_argument('--' + artifact, required=True, type=Path)
        parser.add_argument('--' + artifact + '-sha256', required=True)
    parser.add_argument('--workload', choices=('smoke', 'runtime', 'openai-offline'), default='smoke')
    parser.add_argument('--timeout', type=int, default=180, metavar='1..600')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error('--timeout must be between 1 and 600 seconds')
    if sys.platform != 'linux' or not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('requires a Linux test host with read/write KVM access; no fallback')
    binary = verify_file(args.firecracker, args.firecracker_sha256, MAX_KERNEL_BYTES)
    kernel = verify_file(args.kernel, args.kernel_sha256, MAX_KERNEL_BYTES)
    initrd = verify_file(args.initrd, args.initrd_sha256, MAX_INITRD_BYTES)
    with tempfile.TemporaryDirectory(prefix='reviewer-offline-boot-') as name:
        root = Path(name)
        config = root / 'firecracker.json'
        config.write_text(json.dumps(boot_config(kernel, initrd, runtime=args.workload == 'runtime')))
        text = collect([str(binary), '--no-api', '--config-file', str(config)], root, args.timeout)
        if args.workload == 'runtime':
            from .runtime_probe import parse_result as validate
        elif args.workload == 'openai-offline':
            from .openai_smoke import parse_result as validate
        else:
            validate = parse_result
        print(json.dumps(validate(text), sort_keys=True))


if __name__ == '__main__':
    main()
