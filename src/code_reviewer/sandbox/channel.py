"""Deadline-bounded framed SSH stdio; credentials never enter argv, files or logs."""
from __future__ import annotations

import os
import select
import time


class Channel:
    def __init__(self, reader, writer, timeout):
        self.reader, self.writer = reader, writer
        self.deadline = time.monotonic() + timeout
        os.set_blocking(reader, False)
        os.set_blocking(writer, False)

    def wait(self, fd, *, writing=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('private worker channel deadline exceeded')
        readable, writable, _ = select.select([] if writing else [fd], [fd] if writing else [], [], remaining)
        if not (readable or writable):
            raise TimeoutError('private worker channel deadline exceeded')

    def recv(self, count):
        while True:
            self.wait(self.reader)
            try:
                return os.read(self.reader, min(count, 65536))
            except BlockingIOError:
                continue

    def sendall(self, data):
        view = memoryview(data)
        while view:
            self.wait(self.writer, writing=True)
            try:
                count = os.write(self.writer, view[:65536])
            except BlockingIOError:
                continue
            if count < 1:
                raise ValueError('private worker channel closed')
            view = view[count:]
