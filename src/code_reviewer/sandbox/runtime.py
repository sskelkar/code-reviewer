"""Bounded subprocess collection, VM configuration and offline result validation."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from .smoke import RESULT_PREFIX

MAX_OUTPUT_BYTES = 1024 * 1024
MAX_INITRD_BYTES = 256 * 1024 * 1024
MAX_KERNEL_BYTES = 64 * 1024 * 1024


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
