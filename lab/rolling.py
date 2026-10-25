"""An environment's Auth or REST replaced side by side after a release changes its pin. REST by default, Auth opt in.

Without it (SBARBASE_ROLLING_SERVICES=none), the start after an upgrade that changes the Auth or PostgREST pin replaces every
environment's container in place, all while the gateway is down for the restart. With
SBARBASE_ROLLING_SERVICES naming the service (`rest`, `auth` or `auth,rest`) a pin change no longer
does that:

- **The start keeps the previous image.** A kept container whose only difference from what this version
  would run is its pin is adopted as it is (defer, from Runtime.launch), and the plan (`rolling-plan.json`)
  records it: environment, service, the image it runs and the one this version pins. The start, the
  health round and the confirmation then run on the previous Auth and REST, which the upgrade's own way
  back also runs.
- **The supervisor replaces them once the upgrade is confirmed** (lab/dev.py schedule_rolling runs
  `rolling.py roll`), with the gateway serving: one environment per run, the canary first
  (SBARBASE_ROLLING_CANARY, or the first by name), each under the installation's operation lock (replace):
  1. the swap record (`rolling.json`) is written, then the login's and the database's connection limits
     are raised by connection_budget.SWAP_CONNECTIONS, since both pools run at once;
  2. the new container starts beside the old one under a temporary name (SUFFIX) and must answer its health;
  3. for REST, the platform's role settings are written again, every session of the new container is ended
     and the settings are read back exact, so none it serves with was opened before them
     (Runtime.rest_deadlines says why), and it must answer again;
  4. the record says `switched`, then the environment's address in the published endpoints is replaced in one rename,
     which the gateway reads on its next request (src/http/cached-json.ts);
  5. for the watch window (watch_seconds: REST's longest deadline and a margin, so whatever the old one
     still serves ends) the new one is asked for its health every CHECK_SECONDS while the old one keeps
     running. One answer missed and the address goes back to the old one and the new one is removed;
  6. otherwise the record says `retiring`, the old container is removed, the new one takes the canonical
     name and its settings file, the limits go back, and the record goes last.
- **An environment that is not running is skipped.** Nothing serves from it, so its entry is marked `skipped`
  with the reason, it stays on its previous pin (the by-name commands still refuse for it, and the next start
  replaces it in place) and the other environments go on. It is not permanent: `due` looks again (a bounded,
  rate limited check) and a roll takes the entry back to `old` once its container runs; `retry` asks at once.
  A paused, fenced or unpublished environment keeps waiting as `old` and makes `due` false until it returns.
- **The first failure stops the roll.** The environment is marked `failed` and the plan `stopped`: the rest
  keep the previous pin, `rolling.py status` and `upgrade.py status` say which environment runs which (the
  same is written to current.json, which the console does not show yet), and `rolling.py retry` starts the
  roll again. A replacement not admitted (memory, connection headroom) or REST
  settings that cannot be written beside the serving REST count as failures; nothing changed then.

A swap record in a state repair does not recognise (a container removed by hand) holds only that environment
(repair_or_hold, at a start and at every roll): the others start and roll, the held one and its reason are in
`status`, and `lab/rolling.py repair --force ENVIRONMENT` settles it, never removing a container it did not
create (repair_force); the environment resumes at the next supervisor start.

A crash leaves the swap record, and the next start, or the next roll, repairs from it first (repair):
before `switched` the new container goes; at `switched` the address goes back to the old one, which is
still there, and the new one goes; at `retiring` the replacement is finished. Exactly one container then
keeps the canonical name, with its own settings file, the limits go back and the address is published
again. Until then, and while the plan has an environment on its previous pin, the commands that stop or
start these containers by name refuse for it (require_settled).

Auth migrates its schema when it starts, so the old Auth serves on the schema the new one migrated while
both run: name `auth` only for a release whose Auth migrations in that range are additive.

With `rest` named, a start also leaves a kept REST container serving instead of stopping it
(keeps_serving): its role settings are written again and every session of its login is ended after that
write, so it reconnects with them. A planned restart then no longer restarts REST environment by
environment.
"""
import fcntl
import ipaddress
import json
import os
import re
import sys
import time
import urllib.parse

import connection_budget
import runtime_files

SWITCH = 'SBARBASE_ROLLING_SERVICES'
CANARY = 'SBARBASE_ROLLING_CANARY'
# The port and health path of each service that can be replaced side by side.
SERVICES = {'auth': (9999, '/health'), 'rest': (3000, '/')}
# The new container's name until the old one is gone; reconcile_auth's own is `-previous`.
SUFFIX = '-next'
NAME = 'rolling.json'
# The swap records repair_or_hold moved aside (an environment held, and why), for `status` (another process) and the guards.
HELD = 'rolling-held.json'
PLAN = 'rolling-plan.json'
PHASES = ('starting', 'switched', 'retiring')
# `skipped`: left on its previous pin on purpose, with the reason in the entry (an environment that is not running; asked again once it runs).
STATES = ('old', 'new', 'failed', 'skipped')
# due() looks at skipped entries (are they running again?) at most this often and for at most this many per look.
SKIP_RECHECK_SECONDS = 30
SKIP_RECHECK_MAX = 16
# due() reports an error it swallows at most this often.
DUE_LOG_SECONDS = 300
_due_logged = None
_skip_checked = None
# Added to REST's longest deadline: the old container keeps running at least this long after the switch.
DRAIN_MARGIN_SECONDS = 5
CHECK_SECONDS = 1
# Half seconds the new container may take to answer its health, as a start waits for one (wait_ready).
BOOT_ATTEMPTS = 60
ENVIRONMENT = re.compile(r'e_[a-f0-9]{24}')
# The bound every platform statement that writes a login's or a database's row keeps (REST_PIN_BOUNDS).
BOUNDS = "SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s'; "
# `roll` exits: the operation lock is held (asked again shortly), nothing can be done now.
BUSY, IDLE = 75, 3


class RollError(RuntimeError):
    """One environment's replacement did not happen; the previous container serves."""


class Unrecognised(RuntimeError):
    """A swap record and containers in a state `repair` does not recognise. Only `repair --force` (or a hand fix)
    gets past it; a start holds that one environment instead of refusing every start (repair_or_hold)."""


class Skipped(RollError):
    """One environment's service cannot be replaced now and that is not a failure of the roll: it is not
    running, so nothing serves from it. It is left on its previous pin with the reason recorded, and the others go on."""


def runtime_module():
    # Imported here: durable_runtime imports this module.
    import durable_runtime
    return durable_runtime


def chosen():
    """The services the switch names. Unset or empty means `rest` (Auth stays opt in: a schema migrating
    Auth cannot be rolled back by repair); `none`, `0`, `off` or `false` keeps every start as it was; a value naming
    anything else refuses the start rather than guessing."""
    value = os.environ.get(SWITCH, '').strip()
    if not value:
        return frozenset({'rest'})
    if value.lower() in ('none', '0', 'off', 'false'):
        return frozenset()
    names = {part.strip() for part in value.split(',')}
    if not names <= set(SERVICES):
        raise RuntimeError(f'{SWITCH} may be empty (rest), rest, auth, auth,rest, or none, 0, off or false to turn rolling off; '
                           f'got {value[:40]!r}')
    return frozenset(names)


def watch_seconds():
    """How long the old container keeps running after the switch: REST's longest deadline and a margin."""
    rt = runtime_module()
    return max(rt.REST_STATEMENT_SECONDS, rt.REST_TRANSACTION_SECONDS) + DRAIN_MARGIN_SECONDS


def canonical(e, service):
    return f'{runtime_module().PREFIX}-{e}-{service}'


def path():
    return runtime_module().STATE / NAME


def plan_path():
    return runtime_module().STATE / PLAN


def read():
    """The recorded swap, or None. A record that cannot be read refuses: which container serves is
    then unknown."""
    target = path()
    if not target.exists():
        return None
    try:
        record = json.loads(target.read_text())
    except (OSError, ValueError):
        record = None
    if (not isinstance(record, dict) or record.get('version') != 1 or not ENVIRONMENT.fullmatch(str(record.get('environment')))
            or record.get('service') not in SERVICES or record.get('phase') not in PHASES or not isinstance(record.get('old'), str)
            or not (record.get('new') is None or isinstance(record.get('new'), str))):
        raise RuntimeError('The record of a side by side replacement cannot be read; explicit reconciliation required')
    return record


def write(record):
    runtime_module().atomic(path(), record)


def settle(target):
    """Remove a record, the directory entry made durable."""
    target.unlink(missing_ok=True)
    runtime_module().effect_receipt.sync_directory(target.parent)


def read_plan():
    """The plan, or None. One that cannot be read refuses: which environment runs which pin is unknown."""
    target = plan_path()
    if not target.exists():
        return None
    try:
        plan = json.loads(target.read_text())
    except (OSError, ValueError):
        plan = None
    def entry(item):
        return (isinstance(item, dict) and item.get('state') in STATES and isinstance(item.get('from'), str)
                and isinstance(item.get('to'), str) and isinstance(item.get('reason', ''), str))
    if (not isinstance(plan, dict) or plan.get('version') != 1 or not isinstance(plan.get('environments'), dict)
            or not (plan.get('stopped') is None or isinstance(plan.get('stopped'), str))
            or not (plan.get('canary') is None or ENVIRONMENT.fullmatch(str(plan.get('canary'))))
            or not all(ENVIRONMENT.fullmatch(str(e)) and isinstance(services, dict) and services
                       and all(s in SERVICES and entry(item) for s, item in services.items())
                       for e, services in plan['environments'].items())):
        raise RuntimeError('The plan of side by side replacements cannot be read; explicit reconciliation required')
    return plan


def write_plan(plan):
    runtime_module().atomic(plan_path(), plan)


def require_settled(e=None, plan=True):
    """Refuse while a swap is recorded (this environment's, when named), or, with `plan`, while the plan
    has this environment on its previous pin: its containers are not what this version runs, nor always
    where their names say. The next start or roll finishes or undoes the swap; the roll, a retry or a way
    back settles the plan."""
    if path().exists():
        try:
            record = read()
        except RuntimeError:
            record = None
        if record is None or e is None or record['environment'] == e:
            raise RuntimeError('A side by side replacement of Auth or REST is unfinished; start Sbarbase once so it is repaired')
    held = held_environments()
    if e is None and held or e in held:
        raise RuntimeError('A side by side replacement of Auth or REST is held (lab/rolling.py status); '
                           'check its containers and run lab/rolling.py repair --force ENVIRONMENT')
    if plan and plan_path().exists():
        try:
            current = read_plan()
        except RuntimeError:
            current = None
        services = None if current is None else current['environments'].get(e) if e is not None else current['environments']
        if current is None or (e is not None and services and any(item['state'] != 'new' for item in services.values())):
            raise RuntimeError('Auth or REST of this environment still runs the previous pin until the supervisor replaces it '
                               '(lab/rolling.py status); try again after that, or roll back')


def begin(runtime):
    """At a start: the start of an upgrade or way back (its intent is recorded) begins a new plan, and the
    environments this start keeps on their previous pin (defer) fill it."""
    runtime.deferred = set()
    if runtime_module().UPGRADE_INTENT.exists():
        settle(plan_path())


def defer(runtime, name, component, actual, expected, image):
    """True when launch keeps this Auth or REST on its previous image: the switch names the service, the
    name is the canonical one, and either the recorded upgrade names this pin or the plan already holds
    exactly this replacement. Recorded in the plan before it returns."""
    if component not in chosen():
        return False
    match = re.fullmatch(re.escape(runtime_module().PREFIX) + r'-(e_[a-f0-9]{24})-(auth|rest)', name)
    if not match or match.group(2) != component:
        return False
    e = match.group(1)
    plan = read_plan() or {'version': 1, 'environments': {}, 'stopped': None, 'canary': None}
    entry = plan['environments'].get(e, {}).get(component)
    held = planned(name, component, actual, expected)
    if not held and not runtime_module().upgrade_allows(component, image):
        return False
    plan['environments'].setdefault(e, {})[component] = {
        'state': entry['state'] if held else 'old', 'from': actual.get('Image'), 'to': expected}
    write_plan(plan)
    if not hasattr(runtime, 'deferred'):
        runtime.deferred = set()
    runtime.deferred.add((e, component))
    return True


def planned(name, component, actual, expected):
    """True when the plan holds exactly this replacement: this container on its recorded previous image,
    towards the image this version pins. Launch then lets it go to the new pin even without the upgrade's
    intent (the confirmation removed it), as when the switch was turned off before the roll finished."""
    match = re.fullmatch(re.escape(runtime_module().PREFIX) + r'-(e_[a-f0-9]{24})-(auth|rest)', name)
    if not match or match.group(2) != component:
        return False
    plan = read_plan()
    entry = None if plan is None else plan['environments'].get(match.group(1), {}).get(component)
    return entry is not None and entry['state'] != 'new' and entry['from'] == actual.get('Image') and entry['to'] == expected


def finish_start(runtime):
    """After the start resumed every environment: the plan keeps only what this start deferred (anything
    else runs what this version pins, or is no longer resumed), and goes once nothing is left on a
    previous pin."""
    plan = read_plan()
    if plan is None:
        return
    deferred = getattr(runtime, 'deferred', set())
    environments = {}
    for e, services in plan['environments'].items():
        kept = {s: item for s, item in services.items() if (e, s) in deferred}
        if kept:
            environments[e] = kept
    if not environments:
        settle(plan_path())
        return
    plan['environments'] = environments
    if plan.get('canary') not in environments:
        plan['canary'] = None
    write_plan(plan)


def held_path():
    return runtime_module().STATE / HELD


def read_held():
    """{environment: {'reason', 'record'}} of the swap records repair_or_hold moved aside, so that the single
    record slot is free for the other environments' swaps. Never raises: it is for display, due and the guards."""
    try:
        data = json.loads(held_path().read_text())
        held = data['held']
        return {e: item for e, item in held.items() if ENVIRONMENT.fullmatch(str(e)) and isinstance(item, dict)
                and isinstance(item.get('reason'), str) and isinstance(item.get('record'), dict)}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}


def held_environments():
    """{environment: reason} for the environments repair_or_hold holds."""
    return {e: item['reason'] for e, item in read_held().items()}


def write_held(held):
    if held:
        runtime_module().atomic(held_path(), {'version': 1, 'held': held})
    else:
        settle(held_path())


def status():
    """Which environment runs which pin while a roll is not finished, for current.json and `status`; None
    when nothing waits and nothing is held. `skipped` is {count, reasons} for the entries left on their pin
    on purpose, `held` is {environment: reason} for an environment whose swap record repair does not
    recognise (it resumes at the first supervisor start after `repair --force`). A plan that cannot be
    read says so in `stopped`."""
    try:
        plan = read_plan()
    except RuntimeError as error:
        return {'stopped': str(error), 'environments': {}}
    held = held_environments()
    if plan is None and not held:
        return None
    environments = {} if plan is None else plan['environments']
    skipped = {f'{e} {s}': item.get('reason', '') for e, services in environments.items()
               for s, item in services.items() if item['state'] == 'skipped'}
    return {'stopped': None if plan is None else plan['stopped'], 'canary': None if plan is None else plan['canary'],
            'environments': {e: {s: item['state'] for s, item in services.items()} for e, services in environments.items()},
            'skipped': {'count': len(skipped), 'reasons': skipped},
            'held': held}


def log_due(error):
    """One line to stderr naming the error due() swallowed, at most once per DUE_LOG_SECONDS."""
    global _due_logged
    now = time.monotonic()
    if _due_logged is None or now - _due_logged >= DUE_LOG_SECONDS:
        _due_logged = now
        print(f'rolling.due: {type(error).__name__}: {error}; treated as nothing due', file=sys.stderr, flush=True)


def revivable(plan, limit=SKIP_RECHECK_MAX):
    """The (environment, service) pairs of skipped entries whose container runs again, looking at no more than
    `limit` of them."""
    rt = runtime_module()
    found = []
    for e, services in plan['environments'].items():
        for service, item in services.items():
            if item['state'] != 'skipped':
                continue
            if limit <= 0:
                return found
            limit -= 1
            container = rt.inspect('container', canonical(e, service))
            if container and container.get('State', {}).get('Running'):
                found.append((e, service))
    return found


def due():
    """True when the supervisor has a replacement to make now, or a swap record to repair (a roll repairs
    first). Not for a plan that cannot be read: that waits for an operator (status says why). A held
    environment (its record was moved aside) is not due: an operator settles it, and nor are its entries. A skipped entry is
    looked at again at most every SKIP_RECHECK_SECONDS and is due once its container runs. Any other
    error is logged (rate limited) and counts as nothing due."""
    global _skip_checked
    try:
        held = held_environments()
        if path().exists():
            return True
        plan = read_plan()
        if plan is None or plan['stopped'] is not None:
            return False
        # Only an environment a roll could take now: one that is paused or not published waits for its return
        # (a roll child would find nothing to do every minute).
        import write_pause
        rt = runtime_module()
        published = rt.published_endpoints()
        if any(item['state'] == 'old' and e in published and e not in held and not write_pause.is_paused(e)
               for e, services in plan['environments'].items() for item in services.values()):
            return True
        now = time.monotonic()
        if _skip_checked is None or now - _skip_checked >= SKIP_RECHECK_SECONDS:
            _skip_checked = now
            return any(e in published and e not in held and not write_pause.is_paused(e)
                       for e, _ in revivable(plan))
        return False
    except RuntimeError:
        # An unreadable plan: status says why.
        return False
    except Exception as error:
        log_due(error)
        return False


def order(plan):
    """The canary first (SBARBASE_ROLLING_CANARY when the plan holds it, else the first by name), then the rest by name."""
    names = sorted(plan['environments'])
    canary = plan.get('canary')
    if canary not in plan['environments']:
        wanted = os.environ.get(CANARY, '').strip()
        canary = wanted if wanted in plan['environments'] else (names[0] if names else None)
    return canary, [canary] + [e for e in names if e != canary] if canary else names


def admitted(runtime, e):
    """Room for a second container of this environment: the memory a new environment needs, and the
    connection limits (connection_budget.swap_fits). A measurement that fails admits nothing."""
    import resource_admission
    try:
        if resource_admission.refusal(resource_admission.snapshot()):
            return False
        available, promised = (int(value) for value in runtime.sql(
            "SELECT current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int "
            "- current_setting('reserved_connections')::int, coalesce(sum(greatest(datconnlimit, 0)), 0) "
            "FROM pg_database WHERE datallowconn AND datname <> 'template1';").stdout.strip().split('|'))
        return connection_budget.swap_fits(available, promised)
    except Exception:
        return False


def sessions_sql(e, service='rest', address=None):
    """End the login's sessions, or only those from one container's address."""
    if not ENVIRONMENT.fullmatch(e) or service not in SERVICES:
        raise RuntimeError('Invalid environment runtime identifier')
    where = ''
    if address is not None:
        where = f" AND a.client_addr = '{ipaddress.ip_address(address)}'::inet"
    return (f"SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(a.pid)) FROM pg_catalog.pg_stat_activity a "
            f"WHERE a.usename = '{e}_{service}' AND a.pid <> pg_catalog.pg_backend_pid(){where};")


def limits_sql(e, service, login, database, end_sessions=False):
    """The login's and the database's connection limits in one bounded transaction; with `end_sessions`
    the login's sessions are ended first, so one that holds the login's row cannot keep the limit raised."""
    if not ENVIRONMENT.fullmatch(e) or service not in SERVICES or any(type(v) is not int or v < 0 for v in (login, database)):
        raise RuntimeError('Invalid connection limit')
    ended = sessions_sql(e, service) + ' ' if end_sessions else ''
    return (f'BEGIN; {BOUNDS}{ended}ALTER ROLE {e}_{service} CONNECTION LIMIT {login}; '
            f'ALTER DATABASE {e} CONNECTION LIMIT {database}; COMMIT;')


def limit_lock(e):
    """Held while an environment database's connection limit is computed and written: by a swap from its
    record to its raised limits and from its narrowed limits to the record's removal, and by Studio (lab/studio.py),
    which counts a recorded swap (allowance). Each holder runs one bounded statement under it."""
    if not ENVIRONMENT.fullmatch(e):
        raise RuntimeError('Invalid environment runtime identifier')
    handle = (runtime_module().STATE / f'connection-limit-{e}.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
    except BaseException:
        handle.close()
        raise
    return handle


def allowance(e):
    """The connections a recorded swap of this environment added to its database's limit, which a limit
    written meanwhile (Studio) keeps. A record that cannot be read counts as one, the side the swap was
    admitted for."""
    if not path().exists():
        return 0
    try:
        record = read()
    except RuntimeError:
        return connection_budget.SWAP_CONNECTIONS
    return connection_budget.SWAP_CONNECTIONS if record is not None and record['environment'] == e else 0


def widen(runtime, e, service):
    runtime.sql(limits_sql(e, service, connection_budget.SERVICE_LIMIT + connection_budget.SWAP_CONNECTIONS,
                           runtime_module().current_database_limit(e) + connection_budget.SWAP_CONNECTIONS))


def narrow(runtime, e, service):
    """The limits back to what the environment runs now (Studio, Realtime, direct access and Scheduled
    jobs counted). Tried first beside the serving sessions, then after ending them."""
    login, database = connection_budget.SERVICE_LIMIT, runtime_module().current_database_limit(e)
    try:
        runtime.sql(limits_sql(e, service, login, database))
    except Exception:
        runtime.sql(limits_sql(e, service, login, database, end_sessions=True))


def host(url):
    value = urllib.parse.urlsplit(url).hostname
    return str(ipaddress.ip_address(value or ''))


def point(e, service, url):
    """Publish the address the gateway sends this environment's service requests to, in one rename."""
    rt = runtime_module()
    endpoints = runtime_files.read_endpoints(rt.STATE, strict=True)
    if e not in endpoints or endpoints[e].get(service) == url:
        return
    runtime_files.update_endpoints(e, lambda entry: entry.__setitem__(service, url), rt.STATE, writer=rt.atomic)


def attach(e, temporary, name):
    """The new container on the environment's Functions network too, answering to the canonical name there
    as well, since Edge Functions reach Auth and REST by that name (durable_runtime.functions_configuration)."""
    rt = runtime_module()
    network = rt.functions_network(e)
    if rt.inspect('network', network):
        rt.lab.docker('network', 'connect', '--alias', name, network, temporary)


def settings_file(name):
    return runtime_module().PRIVATE / (name + '.env')


def take_settings(temporary, name):
    """The new container's settings file under the canonical name, once the canonical container is the new
    one. Repeated by repair, so a crash between the rename and this leaves neither the old container's
    settings under the canonical name nor the new one's credentials under the temporary one."""
    source = settings_file(temporary)
    if source.exists():
        os.replace(source, settings_file(name))
        runtime_module().effect_receipt.sync_directory(source.parent)


def drop_settings(temporary):
    """The removed new container's settings file, which holds credentials."""
    settings_file(temporary).unlink(missing_ok=True)


def pin(runtime, e, url):
    """The REST login's role settings written again and every session of the new container ended after
    that, so none it will serve with was opened before them; read back exact, and the new container must
    answer again once it reconnected. Not ending the old container's sessions, which keep serving."""
    rt = runtime_module()
    try:
        runtime.rest_deadlines(e, rewrite=True)
        runtime.sql(sessions_sql(e, 'rest', host(url)))
        if runtime.sql(rt.rest_settings_sql(e)).stdout.strip() != rt.rest_exact(e):
            return False
        rt.wait_ready(url + '/', failure='The new REST did not reconnect', attempts=BOOT_ATTEMPTS)
    except Exception:
        return False
    return True


def healthy(url):
    try:
        return runtime_module().http(url)[0] == 200
    except Exception:
        return False


def replace(runtime, name, component, env, memory, cpus, tier, network, old):
    """Replace a running Auth or REST side by side (see the module) and return the new container's id.
    RollError when it did not happen: the old one serves, the new one is removed, nothing is recorded."""
    rt = runtime_module()
    e = name[len(rt.PREFIX) + 1:-len(component) - 1]
    temporary = name + SUFFIX
    port, health = SERVICES[component]
    if read() is not None:
        raise RuntimeError('A side by side replacement is already recorded; explicit reconciliation required')
    if rt.inspect('container', temporary):
        raise RuntimeError(f'{temporary} exists without a recorded replacement; explicit reconciliation required')
    if not admitted(runtime, e):
        raise RollError(f'there is no room to run two {component} containers at once')
    record = {'version': 1, 'environment': e, 'service': component, 'phase': 'starting', 'old': old['Id'], 'new': None,
              'at': int(time.time())}
    try:
        with limit_lock(e):
            write(record)
            widen(runtime, e, component)
    except Exception:
        if path().exists():
            repair(runtime)
        raise RollError(f'the connection limits for a second {component} could not be raised') from None
    try:
        record['new'], _ = runtime.launch(temporary, component, env, memory, cpus, tier=tier, network=network)
        write(record)
        attach(e, temporary, name)
        url = runtime.endpoint(temporary, port)
        rt.wait_ready(url + health, failure=f'The new {component} did not become ready', attempts=BOOT_ATTEMPTS)
    except Exception:
        repair(runtime)
        raise RollError(f'the new {component} did not become ready') from None
    if component == 'rest' and not pin(runtime, e, url):
        repair(runtime)
        raise RollError('the REST settings could not be written beside the serving REST')
    try:
        record['phase'] = 'switched'
        write(record)
        point(e, component, url)
    except Exception:
        # Switched or not, repair goes back to the old one, which still runs, and publishes its address.
        repair(runtime)
        raise RollError(f'the switch to the new {component} failed') from None
    # The watch window: the old container keeps running, and the gateway goes back to it on one missed answer.
    deadline = time.monotonic() + watch_seconds()
    while time.monotonic() < deadline:
        time.sleep(CHECK_SECONDS)
        if not healthy(url + health):
            repair(runtime)
            raise RollError(f'the new {component} stopped answering after the switch; the gateway went back to the old one')
    record['phase'] = 'retiring'
    write(record)
    rt.lab.docker('stop', old['Id'])
    rt.lab.docker('rm', '-f', old['Id'])
    rt.lab.docker('rename', temporary, name)
    take_settings(temporary, name)
    with limit_lock(e):
        narrow(runtime, e, component)
        settle(path())
    return record['new']


def repair(runtime):
    """Finish or undo a recorded swap, from what the record says and the containers are. Before the
    switch, and during the watch window, the new container goes and the old one stays; once retiring, the
    new one takes the canonical name. Anything else refuses. Then the address is published again, the
    limits go back and the record goes last. Run by every start before it resumes an environment, whether
    the switch is on or not, and by every roll before it replaces anything."""
    record = read()
    if record is None:
        return
    rt = runtime_module()
    e, service = record['environment'], record['service']
    name = canonical(e, service)
    temporary = name + SUFFIX
    current, upcoming = rt.inspect('container', name), rt.inspect('container', temporary)
    unknown = Unrecognised(f'The containers of the side by side replacement of {name} are not as recorded; '
                           'explicit reconciliation required')
    if upcoming and record['new'] is not None and upcoming['Id'] != record['new']:
        raise unknown
    if record['phase'] in ('starting', 'switched') and current and current['Id'] == record['old']:
        # Not yet proven: back to the old one, which is still there.
        if upcoming:
            rt.lab.docker('rm', '-f', upcoming['Id'])
        drop_settings(temporary)
    elif record['phase'] == 'retiring' and current and current['Id'] == record['old']:
        if upcoming:
            rt.lab.docker('stop', current['Id'])
            rt.lab.docker('rm', '-f', current['Id'])
            rt.lab.docker('rename', temporary, name)
            take_settings(temporary, name)
        # Otherwise the new one is gone, and the old one, still there, keeps the name.
    elif record['phase'] != 'starting' and current and record['new'] is not None and current['Id'] == record['new']:
        if upcoming:
            raise unknown
        take_settings(temporary, name)
    elif record['phase'] != 'starting' and not current and upcoming:
        rt.lab.docker('rename', temporary, name)
        take_settings(temporary, name)
    else:
        raise unknown
    item = rt.inspect('container', name)
    if item and item.get('State', {}).get('Running'):
        point(e, service, runtime.endpoint(name, SERVICES[service][0]))
    with limit_lock(e):
        narrow(runtime, e, service)
        settle(path())


def repair_or_hold(runtime):
    """The start's (and the roll's) repair: {environment: reason} for every environment held, which this start does
    not resume until `repair --force` or a hand fix. A record in a state `repair` does not recognise is moved
    aside into the held file (written first, then the record removed: a crash between repeats it), which frees
    the one record slot for the other environments' swaps. Any other failure still refuses the start: a record
    that cannot be read names no environment, and a Docker or database error is not a state of this record."""
    try:
        repair(runtime)
    except Unrecognised as error:
        record = read()
        reason = str(error)
        held = read_held()
        held[record['environment']] = {'reason': reason, 'record': record}
        write_held(held)
        settle(path())
        print(f'Environment {record["environment"]} is held: {reason}. The other environments start. '
              f'Run lab/rolling.py repair --force {record["environment"]} once you checked its containers; '
              'it resumes at the next supervisor start.', file=sys.stderr, flush=True)
    return held_environments()


def repair_force(runtime, e):
    """The operator's way past a record `repair` does not recognise, for the environment named, bounded:

    - it needs a record naming that environment, and tries the ordinary repair first (a record it can repair
      needs no force);
    - the canonical container is never removed, stopped or renamed, whatever it is: it serves;
    - the only container it removes is the `-next` one this replacement created (the id the record holds); a
      container of that name it did not create refuses, and nothing is changed for it;
    - with no canonical container, the one it created is removed too when the phase is `starting` (it was
      never proven); a later phase renames it into place as the ordinary repair does;
    - then the address is published again for a running canonical container, the limits go back and the record
      goes last. Each action is printed (the supervisor's journal), and the actions are returned.
    A step that fails leaves the record, as the ordinary repair does."""
    held = read_held()
    if e in held:
        # Put the record back in its slot to work on it: a step that fails leaves it there, and the next start holds it again.
        if path().exists():
            raise RuntimeError('Another side by side replacement is recorded; start Sbarbase once so it is repaired, then repeat')
        runtime_module().atomic(path(), held[e]['record'])
        del held[e]
        write_held(held)
    record = read()
    if record is None or record['environment'] != e:
        raise RuntimeError(f'No side by side replacement is recorded for {e}; nothing was changed')
    service = record['service']
    rt = runtime_module()
    name = canonical(e, service)
    temporary = name + SUFFIX
    did = []

    def say(text):
        did.append(text)
        print(f'rolling repair --force {name}: {text}', flush=True)
    try:
        repair(runtime)
        say(f'the recorded replacement (phase {record["phase"]}) was repaired the ordinary way; nothing forced')
        return did
    except Unrecognised:
        pass
    current, upcoming = rt.inspect('container', name), rt.inspect('container', temporary)
    created = bool(upcoming) and record['new'] is not None and upcoming['Id'] == record['new']
    if upcoming and not created:
        raise RuntimeError(f'{temporary} was not created by this replacement (the record names {record["new"]}); '
                           'it is not removed: check it and remove it by hand, nothing was changed')
    say(f'phase {record["phase"]}, canonical container {"present" if current else "absent"}, '
        f'{"the replacement own" if created else "no"} {temporary}')
    if current:
        if created:
            rt.lab.docker('rm', '-f', upcoming['Id'])
            drop_settings(temporary)
            say(f'removed {temporary} ({upcoming["Id"][:12]}), created by this replacement; {name} serves')
        elif record['new'] is not None and current['Id'] == record['new']:
            take_settings(temporary, name)
            say(f'{name} is the container of the replacement itself; it keeps the name')
        else:
            say(f'{name} ({current["Id"][:12]}) is kept as it is')
    elif created and record['phase'] == 'starting':
        rt.lab.docker('rm', '-f', upcoming['Id'])
        drop_settings(temporary)
        say(f'removed {temporary} ({upcoming["Id"][:12]}), created by this replacement and never proven; '
            f'the next start creates {name}')
    elif created:
        rt.lab.docker('rename', temporary, name)
        take_settings(temporary, name)
        say(f'{temporary} renamed to {name}')
    else:
        say(f'neither container exists; the next start creates {name}')
    item = rt.inspect('container', name)
    if item and item.get('State', {}).get('Running'):
        point(e, service, runtime.endpoint(name, SERVICES[service][0]))
        say(f'the address of {name} is published')
    with limit_lock(e):
        narrow(runtime, e, service)
        settle(path())
    say('the connection limits went back and the record was removed')
    return did


def swap(runtime, e, service, entry):
    """Replace one environment's service as the plan says, or raise RollError."""
    rt = runtime_module()
    name = canonical(e, service)
    old = rt.inspect('container', name)
    if not old or not old.get('State', {}).get('Running'):
        raise Skipped(f'{name} is not running')
    if old.get('Image') == entry['to']:
        return old['Id']
    if old.get('Image') != entry['from']:
        raise RollError(f'{name} does not run the image the plan recorded')
    if runtime.image(service)[1] != entry['to']:
        raise RollError(f'this version no longer pins the image the plan recorded for {service}')
    v = runtime.values['environments'][e]
    import mail_config
    env = runtime.service_configuration(e, v, service, mail_config.load(e))
    return replace(runtime, name, service, env, '256m', .25, 'production', rt.NETWORK, old)


def roll_once(runtime):
    """Replace the next environment of the plan (both its services). Returns IDLE when there is nothing
    to do now, else 0; a failure is recorded in the plan, which then stops."""
    rt = runtime_module()
    # One record repair does not recognise holds its environment only; the others roll.
    held = repair_or_hold(runtime)
    plan = read_plan()
    if plan is None or plan['stopped'] is not None:
        return IDLE
    # A skipped entry whose container runs again is asked for again.
    for e, service in revivable(plan):
        plan['environments'][e][service]['state'] = 'old'
        plan['environments'][e][service].pop('reason', None)
        write_plan(plan)
    canary, ordered = order(plan)
    plan['canary'] = canary
    published = rt.published_endpoints()
    import write_pause
    for e in ordered:
        services = plan['environments'][e]
        waiting = [s for s in ('auth', 'rest') if s in services and services[s]['state'] == 'old']
        if not waiting:
            continue
        # A paused environment's services are stopped on purpose; it waits for its resume. So does a held one.
        if e in held or e not in published or write_pause.is_paused(e):
            continue
        # Being moved or restored: it waits as a paused one does, and is rolled once the fence clears.
        if rt.source_fence.is_fenced(runtime.sql, e):
            continue
        # A console Data API change writes its settings and signals the serving REST under this lock alone
        # (durable_runtime.data_api_command): a new REST started meanwhile could miss them. So the roll holds
        # it for the environment, tried and never waited for under the operation lock.
        import data_api
        try:
            settings_lock = data_api.environment_lock(e)
        except BlockingIOError:
            return BUSY
        with settings_lock:
            return roll_environment(runtime, plan, e, services, waiting)
    write_plan(plan)
    return IDLE


def roll_environment(runtime, plan, e, services, waiting):
    """The waiting services of one environment, under its Data API lock (roll_once). 0 either way; a
    failure marks the service failed and stops the plan."""
    for service in waiting:
        try:
            swap(runtime, e, service, services[service])
        except Skipped as error:
            # Not running: nothing serves from it. Left on its previous pin and recorded; the plan goes on.
            services[service]['state'] = 'skipped'
            services[service]['reason'] = str(error)
            write_plan(plan)
            print(f'Environment {e}: {service} skipped ({error}); the other environments go on. '
                  'lab/rolling.py retry tries it again.', file=sys.stderr, flush=True)
            continue
        except RollError as error:
            services[service]['state'] = 'failed'
            plan['stopped'] = f'{e} {service}: {error}'
            write_plan(plan)
            print(f'Environment {e}: {service} kept its previous pin ({error}); the remaining environments '
                  'keep theirs. lab/rolling.py retry starts again.', file=sys.stderr, flush=True)
            return 0
        services[service]['state'] = 'new'
        write_plan(plan)
        print(f'Environment {e}: {service} replaced side by side.', flush=True)
    if all(item['state'] == 'new' for entries in plan['environments'].values() for item in entries.values()):
        settle(plan_path())
    return 0


def retry():
    """Start a stopped roll again: failed environments are tried again, from the canary on."""
    plan = read_plan()
    if plan is None:
        return False
    for services in plan['environments'].values():
        for item in services.values():
            if item['state'] in ('failed', 'skipped'):
                item['state'] = 'old'
                item.pop('reason', None)
    plan['stopped'] = None
    write_plan(plan)
    return True


def keeps_serving(runtime, e, item):
    """True when a start may leave this running REST serving while it writes the login's settings again:
    the switch names `rest`, a planned upgrade restart kept it, and the Data API guard is in place (a start
    that has to install it does so with the login's sessions gone)."""
    if 'rest' not in chosen() or not runtime.kept_running(item):
        return False
    import data_api
    try:
        return runtime.sql(data_api.guard_present_sql(e), e).stdout.strip() == 't'
    except Exception:
        return False


def pin_serving(runtime, e):
    """The role settings and password written again with the login's sessions ended, as a start does, then
    every session ended once more: one REST opened between the first end and the commit has the settings
    from before. False when that failed; the caller then stops REST and pins as before."""
    try:
        runtime.rest_deadlines(e, rewrite=True, end_sessions=True)
        runtime.sql(sessions_sql(e))
    except Exception:
        return False
    return True


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Auth and REST replaced side by side after a pin change (lab/rolling.py).')
    parser.add_argument('command', choices=('roll', 'status', 'retry', 'repair'))
    parser.add_argument('environment', nargs='?', help='repair --force: the environment whose record is forced')
    parser.add_argument('--force', action='store_true',
                        help='repair: get past a record in a state repair does not recognise (see repair_force)')
    args = parser.parse_args(argv)
    if args.force and args.command != 'repair':
        parser.error('--force is for repair')
    if args.command == 'repair' and args.force and not (args.environment and ENVIRONMENT.fullmatch(args.environment)):
        parser.error('repair --force names the environment (e_ and 24 hex digits)')
    if args.command == 'status':
        print(json.dumps(status(), indent=2, sort_keys=True))
        return 0
    rt = runtime_module()
    # The operation lock, and the backup lock a restore holds instead while it writes the published endpoints too.
    try:
        lock = rt.operation_lock()
    except BlockingIOError:
        return BUSY
    with lock, (rt.STATE / 'backup.lock').open('a') as restoring:
        try:
            fcntl.flock(restoring, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return BUSY
        if args.command == 'retry':
            return 0 if retry() else IDLE
        if args.command == 'repair':
            try:
                if args.force:
                    repair_force(rt.Runtime(), args.environment)
                else:
                    repair(rt.Runtime())
                return 0
            except Exception as error:
                print(f'Repair stopped: {error}', file=sys.stderr, flush=True)
                return 1
        try:
            rt.effect_receipt.require_settled(rt.STATE)
            return roll_once(rt.Runtime())
        except Exception as error:
            print(f'Side by side replacement stopped: {error}', file=sys.stderr, flush=True)
            return 1


if __name__ == '__main__':
    sys.exit(main())
