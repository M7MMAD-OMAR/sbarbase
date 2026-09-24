"""Turn one environment's Realtime on or off, as the console asked, and record the outcome.

Usage: /usr/bin/python3 lab/realtime.py apply <environment runtime id>

The console records the request in the catalog; the supervisor runs this for each pending
row, one at a time. It calls `durable_runtime.py realtime <runtime> [--off]`, which starts or
removes that environment's own Realtime (docs/engineering/REALTIME.md), and writes the result
back to the same row for the console to show.
"""
import argparse
import fcntl
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / '.lab' / 'upstream' / 'control.sqlite'
OPERATION_LOCK = ROOT / '.lab' / 'upstream' / 'operation.lock'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
CAPACITY = 75
FAILURES = {CAPACITY: 'This server does not have the memory to run Realtime for another environment.'}


def desired(e):
    with closing(sqlite3.connect(f'file:{CATALOG}?mode=ro', uri=True, timeout=5)) as database:
        row = database.execute('SELECT desired FROM realtime_settings WHERE runtime=?', (e,)).fetchone()
    return row[0] if row else None


def record(e, state, failure=None):
    try:
        with closing(sqlite3.connect(CATALOG, timeout=5)) as database, database:
            database.execute('UPDATE realtime_settings SET state=?, failure=?, updated_at=? WHERE runtime=?',
                             (state, failure, int(time.time() * 1000), e))
    except sqlite3.Error:
        pass


def busy():
    try:
        with OPERATION_LOCK.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except BlockingIOError:
        return True


def apply(e):
    want = desired(e)
    if want not in ('on', 'off'):
        return 1
    if busy():
        # Another runtime operation is running; the supervisor asks again shortly.
        return 75
    command = ['/usr/bin/python3', 'lab/durable_runtime.py', 'realtime', e] + ([] if want == 'on' else ['--off'])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        record(e, 'failed', FAILURES.get(result.returncode, 'Realtime could not be ' + ('started' if want == 'on' else 'stopped') +
                                         '. The environment keeps working without it.'))
        return 1
    record(e, want)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply one environment's Realtime setting")
    parser.add_argument('command', choices=('apply',))
    parser.add_argument('environment')
    args = parser.parse_args(argv)
    if not RUNTIME.fullmatch(args.environment):
        print('Invalid environment', file=sys.stderr)
        return 2
    return apply(args.environment)


if __name__ == '__main__':
    sys.exit(main())
