#!/usr/bin/env python3
"""Pulse client: steady probes of a running Sbarbase, for the live upgrade measurement.

  python3 lab/pulse.py run --base URL --env-file FILE --out FILE.jsonl [--interval 0.2] [--timeout 5]
  python3 lab/pulse.py mark --out FILE.jsonl NAME

`run` sends two requests every `--interval` seconds, each on a new connection (so a stalled accept
queue shows up, which a reused keep-alive connection would hide):

  env     one authenticated read of the environment API through the console gateway:
          GET BASE + apiPath + /rest/v1/TABLE?select=*&limit=1, with the environment's publishable key
          as `apikey`. FILE is a private JSON file {"apiPath": ..., "key": ..., "table": ...}; the key is
          never put on the command line or in the output.
  health  one anonymous GET BASE/health.

Each request becomes one JSON line: `t` (wall clock, seconds since the epoch, at the start), `ms`
(latency, milliseconds), `kind`, `status` (null when nothing answered), `error` (the exception class
name, or null) and `verdict`:

  ok      the expected answer (200, and a JSON list for `env`, status ok for `health`)
  held    503: the gateway's hold (an upgrade or a database window), a refused request that says so
  wrong   anything else that was answered: another status, or a 200 whose body is not what it must be
  error   no answer: refused, reset or timed out

Requests run on their own threads, so one that hangs for its timeout does not delay the next tick.
`mark` appends a `{"kind": "mark", "t", "name"}` line, so the scenarios of the measurement are in the
same file as the readings. Only the standard library is used.
"""
import argparse
import http.client
import json
import os
import signal
import sys
import threading
import time
from urllib.parse import urlsplit

DEFAULT_INTERVAL = 0.2
DEFAULT_TIMEOUT = 5.0


def judge(kind, status, body, error):
    """The verdict of one request: 'ok', 'held', 'wrong' or 'error'."""
    if error is not None or status is None:
        return 'error'
    if status == 503:
        return 'held'
    if status != 200:
        return 'wrong'
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return 'wrong'
    if kind == 'env':
        return 'ok' if isinstance(value, list) and value else 'wrong'
    if kind == 'health':
        return 'ok' if isinstance(value, dict) and value.get('status') == 'ok' else 'wrong'
    return 'wrong'


def fetch(base, path, headers, timeout):
    """(status, body text, error class name) of one GET on a new connection; no proxy is used."""
    parts = urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        connection.request('GET', path, headers={'connection': 'close', **headers})
        response = connection.getresponse()
        return response.status, response.read(65536).decode('utf-8', 'replace'), None
    except (OSError, http.client.HTTPException) as error:
        return None, '', error.__class__.__name__
    finally:
        connection.close()


def probe(kind, base, path, headers, timeout, clock=time.time, monotonic=time.monotonic, get=fetch):
    """One reading: a dict ready to be written as a JSON line."""
    started, begun = clock(), monotonic()
    status, body, error = get(base, path, headers, timeout)
    milliseconds = round((monotonic() - begun) * 1000, 1)
    return {'t': round(started, 3), 'ms': milliseconds, 'kind': kind, 'status': status, 'error': error,
            'verdict': judge(kind, status, body, error)}


def load_target(path):
    """(apiPath, key, table) from the private environment file."""
    if os.stat(path).st_mode & 0o077:
        raise SystemExit('the environment file must be private (mode 600)')
    with open(path) as handle:
        value = json.load(handle)
    api, key, table = value.get('apiPath'), value.get('key'), value.get('table')
    if not (isinstance(api, str) and api.startswith('/') and isinstance(key, str) and key and isinstance(table, str) and table):
        raise SystemExit('the environment file needs apiPath, key and table')
    return api.rstrip('/'), key, table


class Sink:
    """Appends JSON lines to one file, one write each, so `mark` from another process interleaves safely."""

    def __init__(self, path):
        self.descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.lock = threading.Lock()

    def write(self, record):
        data = (json.dumps(record, separators=(',', ':')) + '\n').encode()
        with self.lock:
            os.write(self.descriptor, data)

    def close(self):
        os.close(self.descriptor)


def run(base, target, out, interval=DEFAULT_INTERVAL, timeout=DEFAULT_TIMEOUT, stop=None, ticks=None, get=fetch):
    """Probes until `stop` is set (or `ticks` rounds passed); returns the number of readings written."""
    api, key, table = target
    stop = stop or threading.Event()
    sink = Sink(out)
    plan = [('env', f'{api}/rest/v1/{table}?select=*&limit=1', {'apikey': key}),
            ('health', '/health', {})]
    workers, count, lock = [], [0], threading.Lock()

    def one(kind, path, headers):
        sink.write(probe(kind, base, path, headers, timeout, get=get))
        with lock:
            count[0] += 1

    sink.write({'kind': 'start', 't': round(time.time(), 3), 'interval': interval, 'timeout': timeout, 'base': base})
    begun, tick = time.monotonic(), 0
    while not stop.is_set() and (ticks is None or tick < ticks):
        for kind, path, headers in plan:
            worker = threading.Thread(target=one, args=(kind, path, headers), daemon=True)
            worker.start()
            workers.append(worker)
        tick += 1
        workers = [worker for worker in workers if worker.is_alive()]
        # A tick that ran late is not made up for: the schedule follows the clock, not the count.
        wait = begun + tick * interval - time.monotonic()
        if wait > 0:
            stop.wait(wait)
    for worker in workers:
        worker.join(timeout + 1)
    sink.write({'kind': 'end', 't': round(time.time(), 3), 'ticks': tick})
    sink.close()
    return count[0]


def mark(out, name):
    sink = Sink(out)
    sink.write({'kind': 'mark', 't': round(time.time(), 3), 'name': name})
    sink.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    running = sub.add_parser('run')
    running.add_argument('--base', required=True)
    running.add_argument('--env-file', required=True)
    running.add_argument('--out', required=True)
    running.add_argument('--interval', type=float, default=DEFAULT_INTERVAL)
    running.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT)
    marking = sub.add_parser('mark')
    marking.add_argument('--out', required=True)
    marking.add_argument('name')
    args = parser.parse_args(argv)
    if args.command == 'mark':
        mark(args.out, args.name)
        return 0
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    written = run(args.base.rstrip('/'), load_target(args.env_file), args.out, args.interval, args.timeout, stop)
    print(f'pulse: {written} readings', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
