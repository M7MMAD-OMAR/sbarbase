"""The supervisor's watch over the host: disk space and the service containers.

Two conditions had no notification: a filesystem filling up (backups, then the database,
stop when it is full) and a service container that stopped. The supervisor calls step()
from its loop; once a minute it looks, and it reports through the operator channels
(lab/notification_producers.py), whose window keeps one event per condition from repeating
every minute.

Disk: the checkout's filesystem (state, backups) and Docker's data root (database volumes,
images). Below 10 % or 5 GiB available is a warning, below 5 % or 2 GiB is critical.

Containers: only the services every installation runs, the database, Storage, the
management Auth, and each environment's Auth, REST and Realtime. A container counts when it
exists and is not running for five minutes in a row: a restore or a write pause stops some of
them for seconds, and Edge Functions stay stopped by design while an environment is paused, so
they are not watched. Nothing here starts, stops or changes anything.
"""
import re
import shutil
import subprocess
import time
from pathlib import Path

import docker_profile
import notification_producers

ROOT = Path(__file__).resolve().parents[1]
OWNER = 'durable-upstream'
INTERVAL_SECONDS = 60
STOPPED_SECONDS = 300
WATCHED = re.compile(r'sbarbase-durable-(db|storage|management-auth|e_[a-f0-9]{24}-(auth|rest|realtime))')
GIB = 2**30


def disk_level(available, total):
    """'critical', 'warning' or None for one filesystem."""
    if total <= 0:
        return None
    share = available / total
    if available < 2 * GIB or share < .05:
        return 'critical'
    if available < 5 * GIB or share < .10:
        return 'warning'
    return None


def docker(*args):
    result = subprocess.run(docker_profile.docker_command(*args), capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('docker failed')
    return result.stdout


def stopped(listing):
    """Names of watched containers that are not running, from ``docker ps -a`` lines of
    ``<name>\\t<state>``."""
    names = []
    for line in listing.splitlines():
        name, _, state = line.partition('\t')
        if WATCHED.fullmatch(name) and state.strip() != 'running':
            names.append(name)
    return sorted(names)


class HostWatch:
    def __init__(self, catalog=None, emit=None, clock=time.monotonic, usage=shutil.disk_usage, run=docker):
        self.catalog = catalog
        self.emit = emit or notification_producers.emit
        self.clock = clock
        self.usage = usage
        self.run = run
        self.next_at = 0.0
        self.since = {}
        self.docker_root = None

    def filesystems(self):
        paths = {'checkout': ROOT}
        if self.docker_root is None:
            try:
                self.docker_root = self.run('info', '--format', '{{.DockerRootDir}}').strip() or ''
            except Exception:  # noqa: BLE001: retried on the next look
                self.docker_root = None
        if self.docker_root:
            paths['docker'] = Path(self.docker_root)
        return paths

    def check_disks(self):
        for label, path in self.filesystems().items():
            try:
                usage = self.usage(path)
            except OSError:
                continue
            level = disk_level(usage.free, usage.total)
            if level:
                self.emit('host.disk_low', level, f'host.disk_low|{label}|{level}', {}, 'system:supervisor', 'disk_low',
                          {'filesystem': label, 'available_mib': usage.free // 2**20,
                           'percent': int(usage.free * 100 // usage.total)}, catalog=self.catalog)

    def check_containers(self, now):
        try:
            down = set(stopped(self.run('ps', '-a', '--filter', f'label=io.sbarbase.owner={OWNER}',
                                        '--format', '{{.Names}}\t{{.State}}')))
        except Exception:  # noqa: BLE001: an unreachable daemon is not a stopped container
            return
        self.since = {name: self.since.get(name, now) for name in down}
        for name, first in self.since.items():
            if now - first >= STOPPED_SECONDS:
                # The service part of the name: the full one is long enough to read as a key to
                # the notifier's credential scan, which would refuse the message.
                service = name.removeprefix('sbarbase-durable-')
                self.emit('runtime.container_stopped', 'critical', f'runtime.container_stopped|{service}', {},
                          'system:supervisor', 'container_stopped',
                          {'container': service, 'minutes': int((now - first) // 60)}, catalog=self.catalog)

    def step(self):
        """Look once a minute. Never raises: the watch must not stop the supervisor."""
        now = self.clock()
        if now < self.next_at:
            return
        self.next_at = now + INTERVAL_SECONDS
        try:
            self.check_disks()
            self.check_containers(now)
        except Exception:  # noqa: BLE001
            pass
