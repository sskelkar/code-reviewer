"""Linux side of the normal review dispatch; stdin/stdout are private frames only."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .acquire import AcquisitionUnavailable, MetadataUnavailable
from .artifacts import Artifact
from .channel import Channel
from .check import FIRECRACKER_SHA, JAILER_SHA, KERNEL_SHA
from .connected import run
from .guest import collect_artifacts
from .recover import verify_idle
from .runtime import MAX_INITRD_BYTES, MAX_KERNEL_BYTES
from .settings import config_from_job
from .wire import RESULT_LIMIT, receive, receive_json, send_json


def dispatch(channel, root, image, image_sha, timeout):
    request = receive_json(channel)
    if set(request) != {'settings', 'source'}:
        raise ValueError('invalid worker review request')
    settings = request['settings']
    cfg = config_from_job(settings, Path('/work'))
    def read_key(prompt):
        send_json(channel, {'kind': 'credential', 'provider': cfg.provider, 'auth': settings['auth']})
        return receive(channel, 4096).decode('ascii')
    output = root / 'result'
    result = run(Artifact(root/'firecracker', FIRECRACKER_SHA, MAX_KERNEL_BYTES),
                 Artifact(root/'kernel', KERNEL_SHA, MAX_KERNEL_BYTES),
                 Artifact(image/'initrd.cpio.gz', image_sha, MAX_INITRD_BYTES),
                 None, output, settings, timeout=timeout, key_reader=read_key,
                 jailer=Artifact(root/'jailer', JAILER_SHA, MAX_KERNEL_BYTES), source=request['source'])
    # This executes after run's VM/network context managers have exited.
    verify_idle()
    files = collect_artifacts({name: output/name for name in result['files']}, '')
    # Export the envelope already checked by connected, without rereading target objects.
    return {'kind': 'finished', 'result': result, 'files': files, 'cleanup': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--image-sha256', required=True)
    parser.add_argument('--timeout', type=int, required=True)
    args = parser.parse_args()
    channel = Channel(sys.stdin.fileno(), sys.stdout.fileno(), args.timeout + 30)
    try:
        value = dispatch(channel, args.root, args.image, args.image_sha256, args.timeout)
    except (MetadataUnavailable, AcquisitionUnavailable) as exc:
        value = exc.frame()
    except BaseException:
        value = {'kind': 'failed', 'error': 'VM admission, review or cleanup failed'}
    send_json(channel, value, RESULT_LIMIT)


if __name__ == '__main__':
    main()
