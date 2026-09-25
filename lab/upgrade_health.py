"""The health checks that confirm an upgrade (lab/upgrade.py), after the supervisor serves.

A pending upgrade or rollback is confirmed only when, within DEADLINE seconds of the console
process starting, one round of probes all answer 200: the console's own /health (which reads
the control catalog and the key store), the management Auth realm, each environment's Auth,
REST and Storage directly, and each environment's REST and Auth once more through the gateway,
the way an application reaches them (routing, key resolution, the proxy). The gateway holds
application traffic until confirmation; only these requests pass it, carrying the per-start
token lab/upgrade.py writes (src/gateway/hold-bypass.ts).
"""
import concurrent.futures
import contextlib
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import durable_runtime as runtime

DEADLINE = 120
INTERVAL = 2
TIMEOUT = 5
# lab/upgrade.py probe_token(); never printed.
PROBE_TOKEN = runtime.STATE.parent / 'upgrades' / 'probe-token'
PROBE_HEADER = 'x-sbarbase-upgrade-probe'


def fetch(url, headers=None, timeout=TIMEOUT):
    """The HTTP status of one GET. A proxy variable in the environment must not carry a probe away."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def routed(state):
    """Runtimes the gateway serves from their published endpoints: provisioned, not deleted, not
    paused for maintenance and not moved to another placement. endpoints.json can still list a
    deleted or moved runtime, and its old address answering nothing must not fail an upgrade."""
    path = state / 'control.sqlite'
    with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)) as database:
        return {row[0] for row in database.execute(
            "SELECT runtime FROM provision_jobs WHERE state='succeeded' AND runtime IS NOT NULL "
            "AND runtime NOT IN (SELECT runtime FROM deleted_runtimes) "
            "AND runtime NOT IN (SELECT runtime FROM runtime_routing WHERE maintenance=1 OR placement IS NOT NULL)")}


def probe_token(path=None):
    try:
        return (path or PROBE_TOKEN).read_text().strip()
    except FileNotFoundError:
        raise ValueError('the confirmation probe token is missing') from None


def probes(state, server_pid, secrets, serving=None, token=None):
    """(name, url, headers) of every probe, or raises ValueError naming what is missing."""
    server = read(state / 'server.json')
    if not isinstance(server, dict) or not isinstance(server.get('url'), str):
        raise ValueError('the console has not published its address yet')
    # A crashed earlier run can leave server.json behind: only this supervisor's server counts.
    if server_pid is None or server.get('pid') != server_pid:
        raise ValueError('server.json does not name the console this supervisor started')
    console = server['url'].rstrip('/')
    found = [('console', console + '/health', None)]
    management = read(state / 'management.json')
    if isinstance(management, dict) and management.get('auth'):
        found.append(('management auth', management['auth'] + '/health', None))
    serving = routed(state) if serving is None else serving
    for e, item in sorted((read(state / 'endpoints.json') or {}).items()):
        if e not in serving:
            continue
        found.append((f'{e} auth', item['auth'] + '/health', None))
        found.append((f'{e} rest', item['rest'] + '/', None))
        storage = item.get('storage')
        if storage:
            # The probe the runtime's own start waits on, with the environment's tenant.
            jwt = secrets['environments'][e]['jwt']
            found.append((f'{e} storage', storage['url'] + '/bucket',
                          {'authorization': 'Bearer ' + runtime.token(jwt, 'service_role'),
                           'x-forwarded-host': storage['tenantHost']}))
        # The same environment as an application reaches it, through the gateway and its key
        # resolution, past the hold (src/gateway/hold-bypass.ts). REST and Auth only: the token
        # never goes to a route that hands the caller's headers to user code.
        token = probe_token() if token is None else token
        through = {'apikey': token, PROBE_HEADER: token}
        found.append((f'{e} gateway rest', f'{console}/{e}/rest/v1/', through))
        found.append((f'{e} gateway auth', f'{console}/{e}/auth/v1/health', through))
    return found


def private_values():
    return json.loads((runtime.PRIVATE / 'runtime.json').read_text())


def check(state, server_pid, secrets=None, get=fetch, serving=None, token=None):
    """(healthy, detail) for one round of probes. Never raises; detail never holds a secret."""
    try:
        rows = probes(state, server_pid, secrets if secrets is not None else private_values(), serving, token)
    except ValueError as error:
        return False, f'health probes unavailable: {error}'
    except (OSError, KeyError, TypeError, AttributeError, sqlite3.Error) as error:
        return False, f'health probes unavailable ({error.__class__.__name__})'
    for name, url, headers in rows:
        try:
            status = get(url, headers)
        except Exception as error:
            return False, f'{name} did not answer ({error.__class__.__name__})'
        if status != 200:
            return False, f'{name} answered HTTP {status}'
    return True, f'{len(rows)} health probe(s) answered'


def background(function):
    """Run one round off the supervisor's loop, in a daemon thread, so a probe that hangs until
    its timeout neither delays the loop nor keeps the process alive at exit."""
    future = concurrent.futures.Future()

    def run():
        try:
            future.set_result(function())
        except BaseException as error:
            future.set_exception(error)
    threading.Thread(target=run, daemon=True).start()
    return future


class Confirmation:
    """Polled once per supervisor turn while an upgrade waits. poll() returns True once a round
    passed and confirmed() returned true, False while waiting, and raises RuntimeError once the
    deadline passed without that, so the supervisor stops and the way back runs.

    confirmed() returns true only once the confirmation is saved. A passing round whose
    confirmation could not be saved is not a confirmation: the hold stays, the worker does not
    start, and the next round tries again within the same deadline."""

    def __init__(self, probe, confirmed, deadline=DEADLINE, interval=INTERVAL, clock=time.monotonic, submit=background):
        self.probe, self.confirmed, self.clock, self.submit = probe, confirmed, clock, submit
        self.interval = interval
        self.deadline = deadline
        # The clock starts at the first poll, once the console process exists: the supervisor's
        # setup before it (the Studio reset) must not use up the deadline.
        self.until = self.next = None
        self.pending = None
        self.detail = 'no health probe has finished yet'

    def poll(self):
        if self.until is None:
            self.next = self.clock()
            self.until = self.next + self.deadline
        if self.pending is not None and self.pending.done():
            try:
                healthy, self.detail = self.pending.result()
            except Exception as error:
                healthy, self.detail = False, f'health probe failed ({error.__class__.__name__})'
            self.pending = None
            if healthy:
                try:
                    saved = self.confirmed()
                except Exception as error:
                    saved = False
                    self.detail = f'the health checks passed, but the confirmation was not saved ({error.__class__.__name__})'
                else:
                    if not saved:
                        self.detail = 'the health checks passed, but the confirmation was not saved'
                if saved:
                    return True
            self.next = self.clock() + self.interval
        if self.clock() >= self.until:
            raise RuntimeError(f'The new version did not become healthy within {self.deadline} s: {self.detail}')
        if self.pending is None and self.clock() >= self.next:
            self.pending = self.submit(self.probe)
        return False
