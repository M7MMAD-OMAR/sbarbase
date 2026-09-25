"""The health checks that confirm an upgrade (lab/upgrade.py), after the supervisor serves.

A pending upgrade or rollback is confirmed only when, within DEADLINE seconds of the console
process starting, one round of probes all answer 200: the console's own /health (which reads
the control catalog), the management Auth realm, and each environment's Auth, REST and Storage.
The probes go straight to the upstream services, never through the gateway, which holds
application traffic until confirmation.
"""
import concurrent.futures
import json
import threading
import time
import urllib.error
import urllib.request

import durable_runtime as runtime

DEADLINE = 120
INTERVAL = 2
TIMEOUT = 5


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


def probes(state, server_pid, secrets):
    """(name, url, headers) of every probe, or raises ValueError naming what is missing."""
    server = read(state / 'server.json')
    if not isinstance(server, dict) or not isinstance(server.get('url'), str):
        raise ValueError('the console has not published its address yet')
    # A crashed earlier run can leave server.json behind: only this supervisor's server counts.
    if server_pid is None or server.get('pid') != server_pid:
        raise ValueError('server.json does not name the console this supervisor started')
    found = [('console', server['url'].rstrip('/') + '/health', None)]
    management = read(state / 'management.json')
    if isinstance(management, dict) and management.get('auth'):
        found.append(('management auth', management['auth'] + '/health', None))
    for e, item in sorted((read(state / 'endpoints.json') or {}).items()):
        found.append((f'{e} auth', item['auth'] + '/health', None))
        found.append((f'{e} rest', item['rest'] + '/', None))
        storage = item.get('storage')
        if storage:
            # The probe the runtime's own start waits on, with the environment's tenant.
            jwt = secrets['environments'][e]['jwt']
            found.append((f'{e} storage', storage['url'] + '/bucket',
                          {'authorization': 'Bearer ' + runtime.token(jwt, 'service_role'),
                           'x-forwarded-host': storage['tenantHost']}))
    return found


def private_values():
    return json.loads((runtime.PRIVATE / 'runtime.json').read_text())


def check(state, server_pid, secrets=None, get=fetch):
    """(healthy, detail) for one round of probes. Never raises; detail never holds a secret."""
    try:
        rows = probes(state, server_pid, secrets if secrets is not None else private_values())
    except ValueError as error:
        return False, f'health probes unavailable: {error}'
    except (OSError, KeyError, TypeError, AttributeError) as error:
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
    passed (after calling confirmed), False while waiting, and raises RuntimeError past the
    deadline so the supervisor stops and the way back runs."""

    def __init__(self, probe, confirmed, deadline=DEADLINE, interval=INTERVAL, clock=time.monotonic, submit=background):
        self.probe, self.confirmed, self.clock, self.submit = probe, confirmed, clock, submit
        self.interval = interval
        self.deadline = deadline
        self.until = clock() + deadline
        self.next = clock()
        self.pending = None
        self.detail = 'no health probe has finished yet'

    def poll(self):
        if self.pending is not None and self.pending.done():
            try:
                healthy, self.detail = self.pending.result()
            except Exception as error:
                healthy, self.detail = False, f'health probe failed ({error.__class__.__name__})'
            self.pending = None
            if healthy:
                self.confirmed()
                return True
            self.next = self.clock() + self.interval
        if self.clock() >= self.until:
            raise RuntimeError(f'The new version did not become healthy within {self.deadline} s: {self.detail}')
        if self.pending is None and self.clock() >= self.next:
            self.pending = self.submit(self.probe)
        return False
