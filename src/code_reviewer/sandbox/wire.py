"""Length-framed private bootstrap/results; no pickle, tar extraction or console secrets."""
from __future__ import annotations

import json
import struct

META_LIMIT = 65536
RESULT_LIMIT = 12 * 1024 * 1024
BOOT_PORT = 1961


def exact(sock, count):
    chunks = []
    while count:
        data = sock.recv(min(count, 65536))
        if not data:
            raise ValueError('private channel ended early')
        count -= len(data)
        chunks.append(data)
    return b''.join(chunks)


def receive(sock, limit):
    count = struct.unpack('!Q', exact(sock, 8))[0]
    if count > limit:
        raise ValueError('private frame too large')
    return exact(sock, count)


def send(sock, value, limit):
    if len(value) > limit:
        raise ValueError('private frame too large')
    sock.sendall(struct.pack('!Q', len(value)) + value)


def receive_json(sock, limit=META_LIMIT):
    value = json.loads(receive(sock, limit))
    if not isinstance(value, dict):
        raise ValueError('expected a private object frame')
    return value


def send_json(sock, value, limit=META_LIMIT):
    send(sock, json.dumps(value, ensure_ascii=True).encode('utf-8'), limit)
