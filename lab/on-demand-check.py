#!/usr/bin/python3
"""Live check of On demand mode on the local Docker lab: how long a sleeping Realtime or Edge Functions takes to answer again, and that
Realtime's replication slot is gone after a sleep and back after a wake (docs/engineering/OPTIONAL-SERVICES.md, S3).

Usage: /usr/bin/python3 lab/on-demand-check.py [--runs N] [--evidence PATH]

It builds its own disposable installation of one environment (class Harness: a private network, a PostgreSQL of the pinned distribution image,
placeholder Auth and REST) and never touches the retained lab, `.lab`, `.secrets` or a volume; it refuses to run while the retained lab network exists.
It needs enough free memory (it refuses below MIN_AVAILABLE_MIB) and handles one service container at a time. It calls the same runtime functions the
supervisor calls for a wake and a sleep (`Runtime.realtime_turn`, `Runtime.functions_turn`), so what it times is the supervisor's own work; the
gateway's few milliseconds and the supervisor's quarter second beat come on top and are named in the evidence. Everything it made is removed.

Each wake is timed to the first real answer, not to the container: for Realtime, the tenant's health API through Realtime's own port; for Edge
Functions, a call to a function that boots a worker. Runs are not averaged away: every run is in the evidence.
"""
import argparse
import fcntl
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import durable_runtime as dr  # noqa: E402
import runtime_files  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIN_AVAILABLE_MIB = 3072
HELLO = 'export default async()=>new Response("hello");\nDeno.serve(()=>new Response("hello"));\n'


def available_mib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) // 1024
    return 0


def slots(runtime, e):
    out = runtime.sql(f"SELECT coalesce(string_agg(slot_name || ':' || active::text, ','), '') FROM pg_replication_slots WHERE database = '{e}';").stdout.strip()
    return [item for item in out.split(',') if item]


def timed(call):
    start = time.monotonic()
    result = call()
    return round(time.monotonic() - start, 2), result


def http(url, headers=None, timeout=10):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def until(check, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return True
        except OSError:
            pass
        time.sleep(.1)
    return False


def join_channel(base, token, tenant, timeout=30):
    """A browser client's first act on Realtime: open the socket and join a channel that listens to database changes. Returns once Realtime
    answers the join (so it has opened its own connection to the database and created what it needs there), or None after `timeout` seconds."""
    import base64
    import socket
    host, port = base.split('//')[1].split(':')
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f'GET /socket/websocket?apikey={token}&vsn=1.0.0 HTTP/1.1\r\nHost: {tenant}.realtime\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                  f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
    head = b''
    while b'\r\n\r\n' not in head:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        head += chunk
    if b' 101 ' not in head.split(b'\r\n')[0]:
        return None
    message = json.dumps({'topic': 'realtime:odcheck', 'event': 'phx_join', 'ref': '1', 'join_ref': '1', 'payload': {
        'config': {'broadcast': {'ack': False, 'self': False}, 'presence': {'key': ''}, 'postgres_changes': [{'event': '*', 'schema': 'public', 'table': '*'}]},
        'access_token': token}}).encode()
    mask = os.urandom(4)
    frame = bytes([0x81]) + (bytes([0x80 | len(message)]) if len(message) < 126 else bytes([0x80 | 126]) + len(message).to_bytes(2, 'big')) + mask + bytes(
        byte ^ mask[index % 4] for index, byte in enumerate(message))
    sock.sendall(frame)
    deadline = time.monotonic() + timeout
    buffer = head.split(b'\r\n\r\n', 1)[1]
    sock.settimeout(1)
    while time.monotonic() < deadline:
        while len(buffer) >= 2:
            length, offset = buffer[1] & 0x7F, 2
            if length == 126:
                if len(buffer) < 4:
                    break
                length, offset = int.from_bytes(buffer[2:4], 'big'), 4
            if len(buffer) < offset + length:
                break
            payload, buffer = buffer[offset:offset + length], buffer[offset + length:]
            try:
                reply = json.loads(payload)
            except ValueError:
                continue
            if reply.get('event') == 'phx_reply' and reply.get('topic') == 'realtime:odcheck':
                sock.close()
                return reply.get('payload', {}).get('status') == 'ok'
        try:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buffer += chunk
        except OSError:
            pass
    sock.close()
    return None


def summary(values):
    return {'runs': len(values), 'min': min(values), 'median': statistics.median(values), 'max': max(values), 'mean': round(statistics.mean(values), 2)}


class Harness:
    """A disposable installation of one environment: its own network, a PostgreSQL of the pinned distribution image, placeholder Auth and REST
    containers (Edge Functions join their network), and the real `Runtime` code with its names and its state folder moved aside. Nothing of the
    retained lab is read or written: no `.lab`, no `.secrets`, no volume. `close` removes every container and network it made."""
    PREFIX = 'sbarbase-odcheck'

    def __init__(self):
        self.e = 'e_' + os.urandom(12).hex()
        self.directory = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'sbarbase-odcheck'
        self.state = self.directory / 'state'
        self.made = []

    def docker(self, *args, check=True, data=None):
        return dr.lab.docker(*args, check=check, data=data)

    def open(self):
        if self.docker('network', 'ls', '--filter', 'name=^sbarbase-durable-net$', '--format', '{{.Name}}').stdout.strip():
            raise SystemExit('The retained lab network exists (sbarbase-durable-net); this check will not share it. Stop the lab first.')
        self.directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(['rm', '-rf', str(self.state)], check=False)
        (self.state / 'functions' / self.e).mkdir(parents=True)
        (self.state / 'private' / 'functions' / self.e).mkdir(parents=True, mode=0o700)
        for name in ('STATE',):
            setattr(dr, name, self.state)
        dr.PREFIX, dr.DB, dr.NETWORK, dr.EGRESS = self.PREFIX, self.PREFIX + '-db', 'sbarbase-durable-net', self.PREFIX + '-outbound'
        dr.PRIVATE = self.state / 'private'
        self.docker('network', 'create', '--label', 'io.sbarbase.owner=durable-upstream', 'sbarbase-durable-net')
        self.made.append(('network', 'sbarbase-durable-net'))
        pins = json.loads((ROOT / 'lab' / 'distro-image.lock.json').read_text())
        reference = pins['digests'][0]
        self.docker('run', '-d', '--name', dr.DB, '--network', 'sbarbase-durable-net', '--memory', '1g', '--cpus', '1', '--label', 'io.sbarbase.owner=durable-upstream',
                    '-e', 'POSTGRES_PASSWORD=odcheck-' + os.urandom(6).hex(), reference)
        self.made.append(('container', dr.DB))
        if not until(lambda: self.docker('exec', dr.DB, 'pg_isready', '-U', 'supabase_admin', check=False).returncode == 0, 120):
            raise SystemExit('PostgreSQL did not start')
        time.sleep(3)
        for name in (f'{self.PREFIX}-storage', f'{self.PREFIX}-{self.e}-auth', f'{self.PREFIX}-{self.e}-rest'):
            self.docker('run', '-d', '--name', name, '--network', 'sbarbase-durable-net', '--memory', '16m', '--label', 'io.sbarbase.owner=durable-upstream',
                        'ubuntu:24.04', 'sleep', '3600')
            self.made.append(('container', name))
        runtime = object.__new__(dr.Runtime)
        runtime.pins = json.loads((ROOT / 'lab/images.lock.json').read_text())
        for component, filename in [('db', 'distro-image.lock.json'), ('storage', 'storage-image.lock.json'),
                                    ('realtime', 'realtime-image.lock.json'), ('functions', 'functions-image.lock.json')]:
            runtime.pins[component] = json.loads((ROOT / 'lab' / filename).read_text())
        runtime.hba_writer, runtime.kept, runtime.touched = None, None, set()
        runtime.path = self.state / 'private' / 'runtime.json'
        runtime.values = {'environments': {self.e: {'jwt': os.urandom(32).hex(), 'auth': os.urandom(8).hex(), 'rest': os.urandom(8).hex(),
                                                    'realtime': os.urandom(32).hex(), 'realtime_api': os.urandom(32).hex(), 'realtime_base': os.urandom(64).hex(),
                                                    'realtime_enc': os.urandom(8).hex()}}}
        runtime.sql(f'CREATE DATABASE {self.e};')
        runtime.sql('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN;', check=False)
        runtime_files.write_endpoints({self.e: {'auth': 'http://auth:9999', 'rest': 'http://rest:3000'}}, self.state)
        # What a console start does not do here: the HBA rule and the admission numbers are answered as an installation with room would.
        for target, value in (('effect_receipt.require_settled', lambda *a, **k: None), ('source_fence.is_fenced', lambda *a, **k: False),
                              ('restart_placement', lambda *a, **k: (1, 1)), ('available_memory_bytes', lambda: 2**40), ('owned_usage_bytes', lambda: 0)):
            owner, _, attribute = target.rpartition('.')
            setattr(getattr(dr, owner) if owner else dr, attribute, value)
        dr.resource_policy.restart_fits = lambda *a, **k: True
        original = runtime.sql
        runtime.sql = lambda query, database='postgres', check=True: (type('R', (), {'stdout': '1\n', 'returncode': 0})()
                                                                       if 'pg_hba_file_rules' in query else
                                                                       type('R', (), {'stdout': '1000|10\n', 'returncode': 0})() if "max_connections')::int" in query
                                                                       else original(query, database, check))
        self.runtime = runtime
        return runtime

    def close(self):
        for kind, name in reversed(self.made):
            self.docker(kind, 'rm', '-f', name, check=False) if kind == 'container' else None
        for name in (f'{self.PREFIX}-{self.e}-realtime', f'{self.PREFIX}-{self.e}-functions'):
            self.docker('rm', '-f', name, check=False)
        for network in (dr.functions_network(self.e), dr.EGRESS, 'sbarbase-durable-net'):
            self.docker('network', 'rm', network, check=False)
        subprocess.run(['rm', '-rf', str(self.state)], check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--keep', action='store_true', help='leave the disposable containers for inspection (remove them with --clean)')
    parser.add_argument('--clean', action='store_true')
    parser.add_argument('--evidence', default='docs/evidence/optional-services-2026-10-30/cold-start.json')
    args = parser.parse_args()
    if args.clean:
        for name in dr.lab.docker('ps', '-aq', '--filter', 'name=' + Harness.PREFIX).stdout.split():
            dr.lab.docker('rm', '-f', name, check=False)
        for network in ('sbarbase-durable-net', Harness.PREFIX + '-outbound'):
            dr.lab.docker('network', 'rm', network, check=False)
        for network in dr.lab.docker('network', 'ls', '--format', '{{.Name}}', '--filter', 'label=io.sbarbase.functions').stdout.split():
            dr.lab.docker('network', 'rm', network, check=False)
        return 0
    if available_mib() < MIN_AVAILABLE_MIB:
        print(f'Only {available_mib()} MiB of memory is available; this check needs {MIN_AVAILABLE_MIB}. Free some and run it again.', file=sys.stderr)
        return 2
    evidence = {'check': 'on-demand', 'recorded': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'scope': 'Disposable containers of the pinned images on a developer workstation (one PostgreSQL, one Realtime or one Edge Functions at a time, '
                         'placeholder Auth and REST), driven by the same Runtime code the supervisor runs for a wake and a sleep. Not the retained lab, not a server.',
                'host_available_mib_at_start': available_mib(), 'runs': args.runs, 'realtime': {}, 'functions': {}, 'checks': []}

    def check(name, ok, detail=''):
        evidence['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        print(('ok:   ' if ok else 'FAIL: ') + name + ('  ' + detail if detail else ''))
        return ok

    harness = Harness()
    try:
        runtime = harness.open()
        e = harness.e
        v = runtime.values['environments'][e]
        # ---- Realtime
        phases = {}
        for method in ('realtime_database', 'realtime_start', 'launch', 'realtime_register'):
            original = getattr(runtime, method)

            def wrapped(*a, _o=original, _m=method, **k):
                begun = time.monotonic()
                try:
                    return _o(*a, **k)
                finally:
                    phases.setdefault(_m, []).append(round(time.monotonic() - begun, 2))
            setattr(runtime, method, wrapped)
        first, _ = timed(lambda: runtime.realtime_turn(e, on=True))
        evidence['realtime']['first_start_seconds'] = first
        evidence['realtime']['first_start_phases_seconds'] = {k: list(v_) for k, v_ in phases.items()}
        phases.clear()
        tenant = dr.realtime_tenant(e)
        header = {'authorization': 'Bearer ' + dr.service_token(v['realtime_api'], {'role': 'service_role'}), 'host': tenant + '.realtime'}
        wakes, sleeps, ready, slot_rows, join_times = [], [], [], [], []
        for run in range(args.runs):
            slept, _ = timed(lambda: runtime.realtime_turn(e, on=False, sleep=True))
            sleeps.append(slept)
            after_sleep = slots(runtime, e)
            start = time.monotonic()
            woke, _ = timed(lambda: runtime.realtime_turn(e, on=True, wake=True))
            url = published_url(e, 'realtime')
            answered = until(lambda: http(f'{url}/api/tenants/{tenant}/health', header) == 200, 30)
            seconds = round(time.monotonic() - start, 2)
            wakes.append(woke)
            ready.append(seconds if answered else None)
            joined = join_channel(url, dr.service_token(v['jwt'], {'role': 'anon', 'iss': 'sbarbase'}, 3600), tenant)
            joined_seconds = round(time.monotonic() - start, 2)
            slot_present = until(lambda: bool(slots(runtime, e)), 20)
            join_times.append(joined_seconds if joined else None)
            slot_rows.append({'run': run + 1, 'after_sleep': after_sleep, 'after_wake': slots(runtime, e), 'slot_recreated': slot_present, 'channel_joined': bool(joined)})
            print(f'realtime run {run + 1}: sleep {slept}s, wake {woke}s, first answer {seconds}s, channel joined {joined_seconds}s, slots after sleep {after_sleep}')
        evidence['realtime']['wake_phases_seconds'] = {k: list(v_) for k, v_ in phases.items()}
        evidence['realtime'].update({'sleep_seconds': sleeps, 'wake_seconds': wakes, 'wake_to_first_answer_seconds': ready, 'wake_to_channel_joined_seconds': join_times, 'slots': slot_rows})
        if all(item is not None for item in join_times):
            evidence['realtime']['wake_to_channel_joined_summary'] = summary(join_times)
        if all(item is not None for item in ready):
            evidence['realtime']['wake_to_first_answer_summary'] = summary(ready)
        check('Realtime: every sleep left no replication slot of the environment', all(row['after_sleep'] == [] for row in slot_rows), json.dumps([row['after_sleep'] for row in slot_rows]))
        check('Realtime: the slot was recreated by every wake', all(row['slot_recreated'] for row in slot_rows), 'the slot appears once a client joins a channel that listens to database changes')
        check('Realtime: every wake answered', all(item is not None for item in ready))
        check('Realtime: a client joined a channel after every wake', all(row['channel_joined'] for row in slot_rows))
        runtime.realtime_turn(e, on=False)
        # ---- Edge Functions
        code = dr.functions_code(e)
        folder = code / 'v-on-demand-check' / 'on-demand-hello'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'index.ts').write_text(HELLO)
        (code / 'functions.json').write_text(json.dumps({'functions': {'on-demand-hello': {'version': 'v-on-demand-check', 'verify_jwt': False,
                                                                                         'updated_at': int(time.time() * 1000), 'size': len(HELLO)}}}))
        for path in [code, code / 'v-on-demand-check', folder, folder / 'index.ts', code / 'functions.json']:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        first, _ = timed(lambda: runtime.functions_turn(e, on=True))
        evidence['functions']['first_start_seconds'] = first
        wakes, sleeps, ready = [], [], []
        for run in range(args.runs):
            slept, _ = timed(lambda: runtime.functions_turn(e, on=False))
            sleeps.append(slept)
            start = time.monotonic()
            woke, _ = timed(lambda: runtime.functions_turn(e, on=True))
            url = published_url(e, 'functions')
            answered = until(lambda: http(f'{url}/on-demand-hello') == 200, 30)
            seconds = round(time.monotonic() - start, 2)
            wakes.append(woke)
            ready.append(seconds if answered else None)
            print(f'functions run {run + 1}: sleep {slept}s, wake {woke}s, first call answered {seconds}s')
        evidence['functions'].update({'sleep_seconds': sleeps, 'wake_seconds': wakes, 'wake_to_first_call_seconds': ready})
        if all(item is not None for item in ready):
            evidence['functions']['wake_to_first_call_summary'] = summary(ready)
        check('Edge Functions: every wake answered a real call', all(item is not None for item in ready))
        evidence['host_available_mib_at_end'] = available_mib()
    finally:
        if not args.keep:
            harness.close()
    evidence['passed'] = bool(evidence['checks']) and all(item['ok'] for item in evidence['checks'])
    evidence['not_included'] = ('The supervisor looks for a request every quarter second (0.25 s at most), the gateway adds milliseconds, and a Realtime client '
                                'joins a channel after its socket opens. The numbers are the work the supervisor does, to the first answer of the service itself.')
    path = ROOT / args.evidence
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + '\n')
    print(f'evidence: {path}\non-demand check: {"passed" if evidence["passed"] else "failed"}')
    return 0 if evidence['passed'] else 1


def published_url(e, service):
    entry = runtime_files.read_endpoints(dr.STATE).get(e, {}).get(service)
    return entry.get('url') if isinstance(entry, dict) else None


if __name__ == '__main__':
    sys.exit(main())
