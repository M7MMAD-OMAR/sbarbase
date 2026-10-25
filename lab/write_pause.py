"""Operator write pause for one environment (docs/guides/write-pause.md).

    write_pause.py pause <environment>
    write_pause.py resume <environment>
    write_pause.py status

A pause makes sure no application write reaches the environment's database while the operator
changes something whose way back restores a backup (an upgrade that migrates schemas, a restore).
In this order, after the record is written:

1. the gateway admits nothing new: the environment's routing is put in maintenance
   (src/control/catalog.ts changeRuntimeRouting), which the gateway checks on every request
   and every new Realtime socket, so it answers 503;
2. the direct database port and Studio close: Studio is stopped, and the environment's developer
   and Studio logins are turned off with their sessions ended (src/http/database-proxy.ts admits
   only the developer login), and so is the scheduler's login, so no scheduled job or outgoing
   request runs (docs/engineering/SCHEDULED-JOBS.md);
3. whatever was already in flight is cut off: once the gateway's body read window has passed, so
   every request admitted before the pause has reached a service, the environment's Auth, REST,
   Realtime and Functions containers and the shared Storage process are stopped, every session
   left in the environment's database is ended and waited out, and the writers except Functions
   start again (Functions stay stopped until resume, so nothing can call them past the gateway). Every
   running request, upload, function and open socket ends and its open transaction rolls back;
   what finished before the stop landed before the pause holds, so before the backup (its client
   may still see an error), and nothing lands after the pause holds. A failure on the way
   still starts the writers except Functions again, and pause can be run again to finish.

Only then is the pause recorded as held. The services run again, so an upgrade can migrate them and
its health checks still probe them directly (lab/upgrade_health.py). The pause holds across
restarts until ``resume``, which sets the developer login from the environment's direct database
access setting and resumes routing; Studio opens again only when someone starts it. Opening
Studio, turning direct database access on and turning Edge Functions on refuse once a pause is
recorded. A Studio start that has opened its login holds the pause lock, so the pause waits for it and then
closes it (an earlier one fails); any other runtime operation holds the operation lock, and pause and resume then refuse. It is not a fence against the operator. It closes the gateway and the
database port, not the internal network: the environment's Auth, REST and Realtime and the shared
Storage run again while it holds and stay reachable by container name from code running inside the
installation (another environment's Edge Functions).
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time

import durable_runtime as runtime
import rolling
import runtime_files
import scheduled_jobs
import run as lab

RUNTIME = re.compile(r'e_[a-f0-9]{24}')
# Longer than a Studio start, which holds the lock until Studio serves (lab/studio.py up: two
# readiness waits of up to 120 s and the route's 30 s), so a slow one is waited out, not refused.
LOCK_WAIT = 330


class PauseError(RuntimeError):
    pass


def folder(state=None):
    return runtime_files.pauses_folder(state or runtime.STATE)


def record_path(e, state=None):
    if not isinstance(e, str) or not RUNTIME.fullmatch(e):
        raise PauseError('Name an environment by its runtime id (e_ and 24 hex)')
    return runtime_files.pause_path(e, state or runtime.STATE)


def read_record(e, state=None):
    record_path(e, state)
    try:
        value = runtime_files.read_pause(e, state or runtime.STATE)
    except (OSError, ValueError):
        raise PauseError('The write pause record of ' + e + ' cannot be read; it needs review') from None
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('runtime') != e \
            or value.get('phase') not in ('pausing', 'held', 'resuming'):
        raise PauseError('The write pause record of ' + e + ' is malformed; it needs review')
    return value


def is_paused(e, state=None):
    """True while an operator write pause holds the environment, or one is being taken."""
    return read_record(e, state) is not None


def write_record(path, value):
    runtime_files.write_pause(path, value)


@contextlib.contextmanager
def pause_lock(state=None, wait=LOCK_WAIT, sleep=time.sleep, clock=time.monotonic):
    """Held by pause and resume for their whole length, and by anything that turns a login on, so a
    login can never be opened between a pause check and the pause closing it."""
    path = (state or runtime.STATE) / 'write-pause.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = clock() + wait
    with path.open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if clock() >= deadline:
                    raise PauseError('A write pause or a Studio start holds the pause lock; try again when it ends') from None
                sleep(.2)
        yield


@contextlib.contextmanager
def unpaused(e, action, state=None, wait=LOCK_WAIT):
    """For code that turns one of the environment's logins on: refuses while a pause holds, and holds
    the pause lock until the login is on, so a pause starting meanwhile waits and then closes it. A
    caller holding the operation lock passes wait=0 and asks again later (durable_runtime.database_command)."""
    with pause_lock(state, wait):
        if is_paused(e, state):
            raise PauseError(f'{e} is paused for writes; {action} after: write_pause.py resume {e}')
        yield


@contextlib.contextmanager
def operation_lock(state=None):
    """The lock every routing and runtime operation holds (lab/routing-operator.ts)."""
    path = (state or runtime.STATE) / 'operation.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PauseError('Another operation on the runtime is running; try again when it ends') from None
        yield


def die_with_parent():
    """In the routing operator's process: end it when this command dies, so a killed pause leaves no
    routing change running on without the operation lock it holds."""
    import ctypes
    import signal
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG


def operator(value):
    """One request to the local routing operator protocol, never an HTTP endpoint."""
    result = subprocess.run(['bun', 'lab/routing-operator.ts'], input=json.dumps(value), text=True,
                            capture_output=True, timeout=30, cwd=lab.ROOT, preexec_fn=die_with_parent)
    if result.returncode:
        raise PauseError('The routing operator refused the request')
    return json.loads(result.stdout)


def sql(query):
    result = lab.docker('exec', '-i', runtime.DB, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin',
                        '-d', 'postgres', data=query, check=False)
    if result.returncode:
        raise PauseError('A database statement failed')
    return result.stdout.strip()


def close_logins(e, query=sql, sleep=time.sleep, clock=time.monotonic, drain=30):
    """Turn the developer, Studio and scheduler logins off, end their sessions and wait until none is left."""
    names = [f'{e}_developer', f'{e}_studio', f'{e}_cron']
    quoted = ','.join(f"'{name}'" for name in names)
    # The scheduler login first, its sessions ended in the same bounded transaction: a job acting as the
    # developer could otherwise hold the developer's role row (its own password changed in a transaction it
    # keeps open) for the length of its run, against the unbounded statement below.
    query(scheduled_jobs.close_sql(e))
    query(f'DO $$ BEGIN ' + ''.join(f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN "
                                    f"ALTER ROLE {name} NOLOGIN; END IF; " for name in names[:2]) + 'END $$;')
    deadline = clock() + drain
    while True:
        query(f'SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename IN ({quoted});')
        if query(f'SELECT count(*) FROM pg_stat_activity WHERE usename IN ({quoted});') == '0':
            break
        if clock() >= deadline:
            raise PauseError('Sessions of the closed logins did not end; the pause is recorded, run pause again')
        sleep(.5)
    if query(f'SELECT count(*) FROM pg_roles WHERE rolname IN ({quoted}) AND rolcanlogin;') != '0':
        raise PauseError('A closed login can still log in; the pause is recorded, run pause again')


# The gateway reads a request body for at most this long before it forwards the request
# (src/gateway/handler.ts bodyReadTimeoutMs); a request admitted just before the pause has reached
# a service, where the restart cuts it off, once this has passed.
GATEWAY_WINDOW = 15


class Writers:
    """Stopping and starting what writes to an environment's database, through the same helpers a
    restore uses (lab/backup.py), so every address a restart changes is published again."""

    def __init__(self, query=sql, sleep=time.sleep, clock=time.monotonic):
        import backup
        self.backup, self.query, self.sleep, self.clock = backup, query, sleep, clock

    def stop_studio(self, e):
        import studio
        studio.down(e)
        studio.record(e, 'stopped')

    def names(self, e, endpoints):
        backup = self.backup
        functions = [f'{backup.PREFIX}-{e}-functions'] if endpoints.get(e, {}).get('functions') else []
        return backup.service_names(e) + functions

    @contextlib.contextmanager
    def backup_lock(self):
        """A pause never restarts Storage or an environment in the middle of a backup or restore."""
        path = self.backup.STATE / 'backup.lock'
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PauseError('A backup or restore is running; try again when it ends') from None
            yield

    def drain(self, e, drain=30):
        """End every session left in the environment's database while its writers are stopped:
        PostgreSQL finishes a statement whose client died, so this waits for none to remain."""
        deadline = self.clock() + drain
        while True:
            self.query(f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '{e}' "
                       f"AND pid <> pg_backend_pid();")
            if self.query(f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{e}' AND pid <> pg_backend_pid();") == '0':
                return
            if self.clock() >= deadline:
                raise PauseError('Sessions in the environment database did not end; the pause is recorded, run pause again')
            self.sleep(.5)

    def restart(self, e, endpoints):
        """Stop the writers, end what is left in the database, and always start them again."""
        backup = self.backup
        names = self.names(e, endpoints)
        with self.backup_lock():
            try:
                backup.run(['docker', 'stop', *names, backup.STORAGE_CONTAINER])
                self.drain(e)
            finally:
                # Edge Functions stay stopped while the pause holds: on the internal network they hold
                # the environment's service key and could be called past the gateway. Resume starts them.
                self.start(e, endpoints, functions=False)

    def start(self, e, endpoints, functions=True):
        """Start Storage and the environment's writers and wait until each answers; safe to repeat."""
        backup = self.backup
        backup.wait_storage(backup.start_storage())
        backup.start_services(e)
        backup.wait_healthy(e)
        self.wait_rest(e)
        if functions and endpoints.get(e, {}).get('functions'):
            self.start_functions(e)

    def wait_rest(self, e, timeout=120):
        """REST answers on its published address again (Auth is awaited by backup.wait_healthy)."""
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            url = runtime.published_endpoints().get(e, {}).get('rest')
            try:
                if url and runtime.http(url + '/')[0] == 200:
                    return
            except OSError:
                pass
            self.sleep(1)
        raise PauseError('REST did not come back after the restart')

    def start_functions(self, e):
        """Run the environment's Edge Functions again as a fresh container, the way turning them on
        does: the current image pin and signing secret, even when a Sbarbase start or a signing key
        rotation happened during the pause and left the old container stopped. Then publish it."""
        entry = runtime.Runtime().functions_start(e)
        runtime_files.read_endpoints(runtime.STATE, required=True)
        runtime_files.update_endpoints(e, lambda published: published.__setitem__('functions', entry), runtime.STATE)
        return entry

# Every failure after the record is written says so: the record refuses turning Studio, direct database
# access and Edge Functions on until the pause is finished or released, though what was on may still run.
RECORDED = ('the pause is recorded, so turning Studio, direct database access or Edge Functions on is refused, '
            'but it does not hold yet; run pause again, or resume')


def own_words(error):
    """The restore helpers this command shares (lab/backup.py) word their failures for a restore; no
    restore runs here, so their messages say restart instead."""
    return str(error).replace('after the restore', 'after the restart')


def owned(record, current):
    """Whether the environment's maintenance is this pause's: the routing revision it confirmed when it
    paused routing is still the current one, since every routing change moves the revision."""
    return record is not None and record.get('revision') is not None and record.get('revision') == current.get('revision')


def unsettled(e, record, current, path):
    """A routing change whose outcome this pause never read back: it stopped during the call, or the
    read-back failed. Without maintenance nothing is held, by this pause or anyone, so the intent is
    dropped and the run goes on. With maintenance it may be this pause's or another operation's, so
    nothing takes or releases it until the operator settles it."""
    if record is None or 'routing_intent' not in record:
        return
    if not current.get('maintenance'):
        del record['routing_intent']
        write_record(path, record)
        return
    raise PauseError(f'{e} is in maintenance, and this pause stopped before it could tell whether its own '
                     f'routing change made it so. If no other operation (a cutover export or a move) holds '
                     f'{e}, settle it with: write_pause.py pause {e} --adopt-maintenance')


def route_pause(e, record, current, path, route, clock):
    """Pause routing, and know whether it happened. Every routing writer holds the installation
    operation lock, as this command does, so a read right after a failed call shows only this change."""
    intended = current['revision'] + 1
    record['routing_intent'] = intended
    write_record(path, record)
    try:
        answered = route({'action': 'pause', 'runtimes': [e], 'revision': current['revision']})['revision']
    except Exception as error:
        after = route({'action': 'read', 'runtimes': [e]})[e]
        if not (after.get('maintenance') and after.get('revision') == intended):
            # It did not happen: nothing is this pause's, and a rerun routes again.
            del record['routing_intent']
            write_record(path, record)
            raise
        # It happened and its answer was lost; when exactly is unknown, so no routed_at.
        del record['routing_intent']
        record['revision'] = intended
        write_record(path, record)
        return
    if answered != intended:
        raise PauseError('The routing operator answered an unexpected revision')
    del record['routing_intent']
    record['revision'] = intended
    record['routed_at'] = clock()
    write_record(path, record)


def adopt(e, *, state=None, route=operator):
    """The operator's settlement of an unread routing change: the maintenance at the intended revision
    is taken as this pause's. Only after checking no other operation holds the environment."""
    path = record_path(e, state)
    with pause_lock(state):
        record = read_record(e, state)
        if record is None or 'routing_intent' not in record:
            raise PauseError(f'{e} has no routing change to settle')
        current = route({'action': 'read', 'runtimes': [e]})[e]
        if current.get('maintenance') and current.get('revision') == record['routing_intent']:
            record['revision'] = record['routing_intent']
        record.pop('routed_at', None)
        del record['routing_intent']
        write_record(path, record)


def pause(e, *, state=None, query=sql, route=operator, endpoints=None, writers=None, clock=time.time, sleep=time.sleep):
    """Pause writes for one environment; safe to run again after an interruption."""
    path = record_path(e, state)
    with pause_lock(state):
        # Read under the lock: turning Edge Functions on publishes them while holding it, and the
        # restart must see them to stop them.
        published = runtime.published_endpoints() if endpoints is None else endpoints
        if e not in published:
            raise PauseError('Only a published environment can be paused')
        # Before anything is recorded or stopped: the pause stops and starts Auth and REST by name, which an
        # unfinished side by side replacement (lab/rolling.py) does not leave where they say.
        try:
            rolling.require_settled(e)
        except RuntimeError as error:
            raise PauseError(str(error)) from None
        record = read_record(e, state)
        current = route({'action': 'read', 'runtimes': [e]})[e]
        if current.get('placement') is not None:
            raise PauseError(f'{e} has been moved to another placement; a write pause covers only an environment that never moved')
        unsettled(e, record, current, path)
        if current.get('maintenance') and not owned(record, current):
            raise PauseError(f'{e} is already in maintenance for another operation; that operation releases it')
        if record is not None and record['phase'] == 'held' and owned(record, current):
            # Already holding: nothing to stop again, and resume knows what it has to start.
            return record
        if record is None:
            record = {'version': 1, 'runtime': e, 'phase': 'pausing', 'paused_at': clock()}
            write_record(path, record)
        elif record['phase'] != 'pausing':
            # Before anything stops again, so a run that stops partway is never taken for one that held.
            record['phase'] = 'pausing'
            write_record(path, record)
        writers = writers or Writers(query)
        try:
            if not current.get('maintenance'):
                record.pop('revision', None)
                record.pop('routed_at', None)
                route_pause(e, record, current, path, route, clock)
            writers.stop_studio(e)
            close_logins(e, query)
            # Let what the gateway admitted before it paused reach a service, then cut it off there:
            # requests, uploads, functions and sockets, with whatever they left in the database.
            # When routing's answer was lost its time is unknown, so the whole window is waited from now.
            sleep(max(0.0, record.get('routed_at', clock()) + GATEWAY_WINDOW - clock()))
            writers.restart(e, published)
        except PauseError as error:
            if 'run pause again' in str(error):
                raise
            raise PauseError(f'{error}; {RECORDED}') from None
        except Exception as error:
            raise PauseError(f'The pause stopped before it held ({error.__class__.__name__}: {own_words(error)}); '
                             f'{RECORDED}') from None
        record['phase'] = 'held'
        record['held_at'] = clock()
        write_record(path, record)
        return record


def developer_on(e):
    runtime.Runtime().developer_on(e)


def resume(e, *, state=None, query=sql, route=operator, direct=None, endpoints=None, writers=None, access=None):
    """Release the pause: the writers running, routing, then the developer login as the access
    setting says. The record goes last, so a failure on the way leaves the environment paused."""
    path = record_path(e, state)
    with pause_lock(state):
        record = read_record(e, state)
        if record is None:
            raise PauseError(f'{e} is not paused for writes')
        held = record['phase'] == 'held'
        current = route({'action': 'read', 'runtimes': [e]})[e]
        unsettled(e, record, current, path)
        if current.get('maintenance') and not owned(record, current):
            raise PauseError(f'{e} is in maintenance for another operation; resume after that operation releases it')
        # Before anything reopens, so status never says held once writes may land again.
        record['phase'] = 'resuming'
        write_record(path, record)
        try:
            published = runtime.published_endpoints() if endpoints is None else endpoints
            writers = writers or Writers(query)
            if not held:
                # A pause that stopped midway may have left the writers stopped. Never in the middle
                # of a backup or restore, which hold this lock while they stop and start services.
                with writers.backup_lock():
                    writers.start(e, published)
            elif published.get(e, {}).get('functions'):
                writers.start_functions(e)
            if current.get('maintenance'):
                # The revision read above: a routing change since then fails as stale, and the next
                # resume finds the maintenance is not this pause's.
                route({'action': 'resume', 'runtimes': [e], 'revision': current['revision']})
            # Direct access may have been turned off during the pause; the setting decides, not a snapshot.
            if runtime.direct_on(e) if direct is None else direct:
                # The runtime's own step, the one turning access on uses: the password the console
                # saved last (a reset made during the pause included), grants and connection limit.
                (access or developer_on)(e)
            # Scheduled jobs run again when they are on (lab/scheduler.ts skips a paused environment until then).
            if published.get(e, {}).get('cron'):
                query(scheduled_jobs.open_sql(e))
        except Exception as error:
            raise PauseError(f'Resume stopped before it finished ({error.__class__.__name__}: {own_words(error)}); '
                             f'the pause is still recorded as resuming, run resume again') from None
        path.unlink()


def status(state=None):
    found = folder(state)
    return [read_record(path.stem, state) for path in sorted(found.glob('e_*.json'))] if found.is_dir() else []


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pause or resume application writes for one environment')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('pause', 'resume'):
        command = sub.add_parser(name)
        command.add_argument('environment')
        if name == 'pause':
            command.add_argument('--adopt-maintenance', action='store_true', dest='adopt',
                                 help='settle a routing change the pause could not read back, then finish it')
    sub.add_parser('status')
    args = parser.parse_args(argv)
    try:
        if args.command == 'status':
            held = status()
            for item in held:
                print(f"{item['runtime']} {item['phase']}")
            if not held:
                print('No environment is paused for writes')
            return 0
        with operation_lock():
            if args.command == 'pause':
                if args.adopt:
                    adopt(args.environment)
                pause(args.environment)
                print(f'{args.environment} is paused for writes until: write_pause.py resume {args.environment}')
            else:
                resume(args.environment)
                print(f'{args.environment} accepts writes again')
        return 0
    except PauseError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
