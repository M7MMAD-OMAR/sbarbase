"""What the service containers hold in memory, sampled once a minute for the console's Services page.

The console process has no Docker socket, so the supervisor reads `docker stats` and writes one small
private file (`.lab/upstream/service-stats.json`) that the control plane reads
(src/control/services.ts). The file names containers (without the installation prefix), their
resident memory and their ceiling, and what one more Realtime or Edge Functions container would ask for,
taken from the resource policy. It carries no environment variable, key or log.

The sampling runs in a thread: `docker stats` can take a second or two, and the supervisor's loop must
keep its quarter second beat. A sample that fails leaves the previous file in place, so its age
(`capturedAt`) tells the console how stale it is.
"""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import docker_profile
import resource_policy

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab' / 'upstream'
FILE = 'service-stats.json'
INTERVAL_SECONDS = 60
# Only the containers the installation owns: the shared ones and each environment's own.
WATCHED = re.compile(r'sbarbase-durable-((db|storage|management-auth)|(e_[a-f0-9]{24})-(auth|rest|realtime|functions|studio|meta))')
UNITS = {'b': 1, 'kb': 1000, 'mb': 1000**2, 'gb': 1000**3, 'tb': 1000**4,
         'kib': 1024, 'mib': 1024**2, 'gib': 1024**3, 'tib': 1024**4}


def size(text):
    """Bytes in a `docker stats` size such as `183.2MiB` or `1.5GiB`, or None."""
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*', text or '')
    if not match or match.group(2).lower() not in UNITS:
        return None
    return int(float(match.group(1)) * UNITS[match.group(2).lower()])


def parse(output):
    """{container: {memoryBytes, limitBytes}} from `docker stats --no-stream --format '{{.Name}}\\t{{.MemUsage}}'`.
    A line that does not read as a watched container with two sizes is left out."""
    containers = {}
    for line in output.splitlines():
        name, _, usage = line.partition('\t')
        match = WATCHED.fullmatch(name.strip())
        if not match:
            continue
        used, _, limit = usage.partition('/')
        memory, ceiling = size(used), size(limit)
        if memory is None or ceiling is None:
            continue
        containers[name.strip()[len('sbarbase-durable-'):]] = {'memoryBytes': memory, 'limitBytes': ceiling}
    return containers


def costs():
    """What turning one more service on asks for, from the resource policy, in bytes."""
    return {'realtime': resource_policy.memory_mib(resource_policy.TIERS[resource_policy.REALTIME_ROW].memory) * 2**20,
            'functions': resource_policy.memory_mib(resource_policy.TIERS[resource_policy.FUNCTIONS_ROW].memory) * 2**20}


def docker(*args):
    result = subprocess.run(docker_profile.docker_command(*args), capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('docker failed')
    return result.stdout


def write(path, document):
    """One rename, readable by the console's user only: the file holds numbers, not secrets, but not for the world."""
    temporary = path.with_name(path.name + '.pending')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(document, handle, separators=(',', ':'))
    os.replace(temporary, path)


class ServiceStats:
    def __init__(self, run=docker, clock=time.monotonic, wall=time.time, state=STATE, threaded=True):
        self.run = run
        self.clock = clock
        self.wall = wall
        self.path = Path(state) / FILE
        self.threaded = threaded
        self.next_at = 0.0
        self.worker = None

    def sample(self):
        """One sample, written. A failure leaves the previous file, so the console sees its age."""
        try:
            output = self.run('stats', '--no-stream', '--no-trunc', '--format', '{{.Name}}\t{{.MemUsage}}')
        except Exception:  # noqa: BLE001: the next minute asks again
            return False
        try:
            write(self.path, {'version': 1, 'capturedAt': int(self.wall() * 1000), 'containers': parse(output), 'costs': costs()})
        except (OSError, KeyError, ValueError):
            return False
        return True

    def step(self):
        """Called from the supervisor's loop; takes a sample once a minute without holding the loop."""
        now = self.clock()
        if now < self.next_at or (self.worker is not None and self.worker.is_alive()):
            return
        self.next_at = now + INTERVAL_SECONDS
        if not self.threaded:
            self.sample()
            return
        self.worker = threading.Thread(target=self.sample, name='service-stats', daemon=True)
        self.worker.start()
