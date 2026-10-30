"""Docker's build cache: its size, and clearing it when an operator asks from the console.

The console process has no Docker socket, so the request is a row (`host_actions`, action `build_cache_clear`) that the supervisor applies
by running this file (lab/dev.py schedule_host_actions): `python3 lab/build_cache.py clear`. Clearing runs `docker builder prune --force` and nothing
else: never `docker image prune`, never `docker system prune`, never a volume. Images stay, because the way back of an update needs
them (docs/engineering/UPGRADES.md); the console says so beside the button. The size shown before the click is what `size()` read from `docker system df`.
"""
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import docker_profile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / '.lab' / 'upstream' / 'control.sqlite'
ACTION = 'build_cache_clear'
UNITS = {'b': 1, 'kb': 1000, 'mb': 1000**2, 'gb': 1000**3, 'tb': 1000**4, 'kib': 1024, 'mib': 1024**2, 'gib': 1024**3}
# Fixed argument list: the one command this file may run to remove anything.
PRUNE = ('builder', 'prune', '--force')
DF = ('system', 'df', '--format', '{{.Type}}\t{{.Size}}\t{{.Reclaimable}}')


def docker(*args, timeout=600):
    result = subprocess.run(docker_profile.docker_command(*args), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('docker failed')
    return result.stdout


def to_bytes(text):
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*(?:\(.*\))?\s*', text or '')
    if not match or match.group(2).lower() not in UNITS:
        return None
    return int(float(match.group(1)) * UNITS[match.group(2).lower()])


def size(run=docker):
    """{total, reclaimable} bytes of the build cache from `docker system df`, or None when it cannot be read."""
    try:
        for line in run(*DF).splitlines():
            kind, _, rest = line.partition('\t')
            if kind.strip() == 'Build Cache':
                total, _, reclaimable = rest.partition('\t')
                total, reclaimable = to_bytes(total), to_bytes(reclaimable)
                return {'totalBytes': total, 'reclaimableBytes': reclaimable} if total is not None and reclaimable is not None else None
    except Exception:  # noqa: BLE001: an unreadable size is shown as unknown, never as zero
        return None
    return None


def record(state, reclaimed=None, failure=None, catalog=CATALOG):
    try:
        with closing(sqlite3.connect(catalog, timeout=5)) as database, database:
            database.execute('UPDATE host_actions SET state=?, reclaimed_bytes=?, failure=?, finished_at=? WHERE action=?',
                             (state, reclaimed, failure, int(time.time() * 1000) if state in ('done', 'failed') else None, ACTION))
            if state in ('done', 'failed'):
                database.execute("INSERT INTO audit_events(actor, action, subject, detail, at) VALUES ('system:supervisor', ?, 'installation', ?, ?)",
                                 (f'host.build_cache_clear_{state}', '{"reclaimed_bytes":%d}' % (reclaimed or 0), int(time.time() * 1000)))
    except sqlite3.Error:
        pass


def clear(run=docker, catalog=CATALOG):
    """Runs the one prune and records what it freed. Returns 0 or 1."""
    record('running', catalog=catalog)
    before = size(run)
    try:
        run(*PRUNE)
    except Exception:  # noqa: BLE001
        record('failed', failure='Docker did not clear the build cache. Nothing else was touched.', catalog=catalog)
        return 1
    after = size(run)
    freed = max(0, before['totalBytes'] - after['totalBytes']) if before and after else None
    record('done', freed, catalog=catalog)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv != ['clear']:
        print('usage: build_cache.py clear', file=sys.stderr)
        return 2
    return clear()


if __name__ == '__main__':
    sys.exit(main())
