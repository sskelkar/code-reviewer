"""Harmless guest probes; never contain an API key or call a model."""
from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path

from ..backends.read_tools import ReadOnlyTools
from .network import PROXY


def network_probes():
    checks = {}
    for label, host, port in (('direct_public', '1.1.1.1', 443), ('metadata', '169.254.169.254', 80),
                              ('private', '192.168.1.1', 80), ('host_service', '192.0.2.1', 22)):
        try:
            with socket.create_connection((host, port), timeout=.4):
                checks[label] = False
        except OSError:
            checks[label] = True
    for label, destination in (('other_domain', 'example.com:443'), ('literal_ip', '1.1.1.1:443'),
                                ('wrong_port', 'api.openai.com:80'), ('private_proxy', '127.0.0.1:443')):
        with socket.create_connection(PROXY, timeout=3) as sock:
            sock.sendall(('CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n' % (destination, destination)).encode())
            status = sock.recv(4096).split(b'\r\n', 1)[0]
            checks[label] = b' 403 ' in status
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(.4)
        sock.sendto(b'\x00' * 12, ('1.1.1.1', 53))
        try:
            sock.recv(512)
            checks['direct_dns'] = False
        except OSError:
            checks['direct_dns'] = True
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.settimeout(.4)
            sock.connect(('2606:4700:4700::1111', 443))
            checks['ipv6'] = False
    except OSError:
        checks['ipv6'] = True
    connection = http.client.HTTPSConnection(PROXY[0], PROXY[1], timeout=20)
    try:
        connection.set_tunnel('api.openai.com', 443)
        connection.connect()  # verified TLS only, no HTTP API/model request
        checks['approved_tls'] = True
    finally:
        connection.close()
    if not all(checks.values()):
        raise ValueError('guest network boundary probe failed')
    return checks


def file_probes(root):
    root = Path(root)
    checkout = root / 'probe-checkout'
    checkout.mkdir()
    outside = root / 'probe-canary'
    outside.write_text('harmless outside canary')
    (checkout / 'link').symlink_to(outside)
    tools = ReadOnlyTools(checkout)
    checks = {}
    for name, path in (('parent', '../probe-canary'), ('absolute', str(outside)), ('symlink', 'link')):
        checks[name] = 'error' in json.loads(tools.execute('Read', {'path': path, 'offset': 1, 'limit': 10}))
    checks['no_exec_tool'] = 'error' in json.loads(tools.execute('Bash', {'command': 'true'}))
    checks['no_write_tool'] = 'error' in json.loads(tools.execute('Write', {'path': 'app.py', 'content': 'x'}))
    if not all(checks.values()):
        raise ValueError('guest file-tool boundary probe failed')
    return checks
