"""Foreground local installation runner. Owns only its children and labelled lab."""
import collections
import datetime
import console_build_check
import fcntl
import json
import notification_producers
import os
from pathlib import Path
import signal
import sqlite3
from contextlib import closing
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab/upstream'


def notify_installation(kind, catalog=None):
    """One installation lifecycle event, from the durable stage that already completed.

    Called only after a stage returned success (the runtime is up, or the runtime is
    stopped). emit() never raises, so the installation's outcome and exit status are
    unchanged by a notification that cannot be written.
    """
    return notification_producers.emit(kind, 'info', kind + '|installation', {}, 'system:supervisor',
                                       'operator_request', {'stage': 'runtime'}, catalog=catalog)


def child_status(process):
    """Observe our child without releasing its PID reservation by reaping it."""
    if process.returncode is not None:
        return process.returncode
    result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if result is None:
        return None
    return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status


def terminate_group(process, grace=20):
    """Clean an owned session before reaping its leader. Never use saved PIDs.

    Caller exclusively owns child waiting. Already reaped leaders no longer
    authorize group signals; their descendants require separate containment.
    """
    if process.returncode is not None:
        return
    try:
        status = child_status(process)
    except ChildProcessError:
        return
    if status is None:
        # Popen.terminate() polls internally, which could reap the leader.
        os.kill(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while child_status(process) is None and time.monotonic() < deadline:
            time.sleep(.02)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            deadline = time.monotonic() + 2
            while child_status(process) is None and time.monotonic() < deadline:
                time.sleep(.02)
    process.wait()


def backup_hour(environment=os.environ):
    """The UTC hour of the daily backup, or None when SBARBASE_BACKUP_HOUR is 'off'."""
    value = environment.get('SBARBASE_BACKUP_HOUR', '3').strip()
    if value == 'off':
        return None
    hour = int(value)
    if not 0 <= hour <= 23:
        raise ValueError('SBARBASE_BACKUP_HOUR must be 0 to 23 or off')
    return hour


def backup_keep(environment=os.environ):
    keep = int(environment.get('SBARBASE_BACKUP_KEEP', '7'))
    if keep < 1:
        raise ValueError('SBARBASE_BACKUP_KEEP must be at least 1')
    return keep


def backup_due(now, last_day, hour):
    """Once per UTC day, at or after the configured hour; a start later that day catches up."""
    return hour is not None and now.hour >= hour and last_day != now.date().isoformat()


class Supervisor:
    def __init__(self, stop_event=None, worker_fd=None, catalog=None):
        self.stop_event = stop_event or threading.Event()
        self.server = None
        self.worker = None
        self.backup = None
        self.studios = {}
        # One sign-in apply at a time, and a pause after one that found the runtime busy.
        self.sign_in = None
        self.sign_in_after = 0.0
        # Realtime and Edge Functions: one change at a time for each, and a pause after one that
        # found the runtime busy.
        self.toggles = {'realtime': None, 'functions': None, 'database': None}
        self.toggles_after = {'realtime': 0.0, 'functions': 0.0, 'database': 0.0}
        self.backup_hour = backup_hour()
        self.backup_keep = backup_keep()
        self.restarts = collections.deque()
        self.worker_fd = worker_fd
        # The control catalog the operator's installation drains. None selects the
        # default upstream path; a test passes a private temporary catalog.
        self.catalog = catalog

    def spawn(self, command):
        return subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),*command], cwd=ROOT, start_new_session=True)

    def descriptor(self):
        record = {'pid': os.getpid(), 'serverPid': self.server.pid if self.server else None,
                  'workerPid': self.worker.pid if self.worker else None,
                  'workerRestarts': len(self.restarts)}
        temporary = STATE/'supervisor.pending'
        temporary.write_text(json.dumps(record))
        temporary.replace(STATE/'supervisor.json')

    def start_worker(self):
        if self.worker_fd is None:
            raise RuntimeError('Supervisor requires an exclusive worker lock')
        self.worker = subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),'/usr/bin/python3', 'lab/worker.py', '--upstream', '--watch'],
                                       cwd=ROOT, start_new_session=True, pass_fds=(self.worker_fd,),
                                       env=dict(os.environ, SBARBASE_WORKER_FD=str(self.worker_fd)))
        self.descriptor()

    def check(self):
        if child_status(self.server) is not None:
            raise RuntimeError('Local API exited; stopping the installation')
        if child_status(self.worker) is not None:
            terminate_group(self.worker, grace=0)
            now = time.monotonic()
            while self.restarts and now-self.restarts[0] > 60:
                self.restarts.popleft()
            if len(self.restarts) >= 3:
                # The worker exited and the limit was reached: that is the durable state
                # change. emit() never raises, so it cannot change the RuntimeError below.
                self.record_worker_event('worker.restart_limit', 'worker_restart_limit')
                raise RuntimeError('Worker restart limit reached; inspect retained state')
            self.restarts.append(now)
            print('Provisioning worker exited; reconciling retained operations.', flush=True)
            self.start_worker()
            # The descriptor now records the new workerRestarts count, so the durable
            # state change exists before the event is written.
            self.record_worker_event('worker.restart', 'worker_restart')

    def schedule_backup(self, now=None):
        """Start the daily backup when it is due, and report it when it ends."""
        if self.backup is not None:
            status = child_status(self.backup)
            if status is None:
                return
            terminate_group(self.backup, grace=0)
            self.backup = None
            path = STATE/'endpoints.json'
            count = len(json.loads(path.read_text())) if path.exists() else 0
            if status == 0:
                notification_producers.emit('backup.completed', 'info', 'backup.completed|installation', {},
                                            'system:supervisor', 'export_completed', {'environments': count},
                                            catalog=self.catalog)
            else:
                notification_producers.emit('backup.failed', 'critical', 'backup.failed|installation', {},
                                            'system:supervisor', 'export_failed', {'failed': status},
                                            catalog=self.catalog)
            return
        now = now or datetime.datetime.now(datetime.UTC)
        record = STATE/'backup-schedule.json'
        last = json.loads(record.read_text()).get('day') if record.exists() else None
        if not backup_due(now, last, self.backup_hour):
            return
        # Recorded before the run starts, so a failing backup is reported once, not retried all day.
        temporary = STATE/'backup-schedule.pending'
        temporary.write_text(json.dumps({'day': now.date().isoformat(), 'started_at': now.isoformat(timespec='seconds')}))
        temporary.replace(record)
        print('Daily backup started.', flush=True)
        self.backup = self.spawn(['/usr/bin/python3', 'lab/backup.py', 'create', 'all', '--keep', str(self.backup_keep)])

    def studio_requests(self):
        """(runtime, desired, state, failure) of every Studio row in the catalog."""
        path = STATE/'control.sqlite'
        if not path.exists():
            return []
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)) as database, database:
                return database.execute('SELECT runtime, desired, state, failure FROM studio_sessions').fetchall()
        except sqlite3.Error:
            return []

    def schedule_studios(self):
        """Start or stop each environment's Studio as the console asked, one child per environment."""
        for runtime, process in list(self.studios.items()):
            if child_status(process) is not None:
                terminate_group(process, grace=0)
                del self.studios[runtime]
        for runtime, desired, state, failure in self.studio_requests():
            if runtime in self.studios:
                continue
            if desired == 'running' and (state == 'stopped' or (state == 'failed' and failure is None)):
                self.studios[runtime] = self.spawn(['/usr/bin/python3', 'lab/studio.py', 'up', runtime])
            elif desired == 'stopped' and state in ('running', 'starting', 'failed'):
                self.studios[runtime] = self.spawn(['/usr/bin/python3', 'lab/studio.py', 'down', runtime])

    def sign_in_requests(self):
        """Runtimes whose saved sign-in settings wait to be applied, oldest first."""
        path = STATE/'control.sqlite'
        if not path.exists():
            return []
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)) as database, database:
                return [row[0] for row in database.execute("SELECT runtime FROM auth_settings WHERE state='pending' ORDER BY updated_at")]
        except sqlite3.Error:
            return []

    def schedule_sign_in(self):
        """Apply saved sign-in settings (lab/auth_settings.py), one environment at a time."""
        if self.sign_in is not None:
            status = child_status(self.sign_in)
            if status is None:
                return
            terminate_group(self.sign_in, grace=0)
            self.sign_in = None
            if status == 75:
                # Another runtime operation held the lock; ask again shortly.
                self.sign_in_after = time.monotonic() + 5
        if time.monotonic() < self.sign_in_after:
            return
        pending = self.sign_in_requests()
        if pending:
            self.sign_in = self.spawn(['/usr/bin/python3', 'lab/auth_settings.py', 'apply', pending[0]])

    def toggle_requests(self, service):
        """Runtimes whose Realtime, Edge Functions or database access should be turned on or off, oldest first."""
        path = STATE/'control.sqlite'
        if not path.exists():
            return []
        table = {'realtime': 'realtime_settings', 'functions': 'functions_settings', 'database': 'database_access'}[service]
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)) as database, database:
                return [row[0] for row in database.execute(f"SELECT runtime FROM {table} WHERE state='pending' ORDER BY updated_at")]
        except sqlite3.Error:
            return []

    def schedule_toggles(self):
        """Turn an environment's Realtime, Edge Functions or database access on or off (lab/realtime.py), one at a time for each."""
        for service, process in self.toggles.items():
            if process is not None:
                status = child_status(process)
                if status is None:
                    continue
                terminate_group(process, grace=0)
                self.toggles[service] = None
                if status == 75:
                    self.toggles_after[service] = time.monotonic() + 5
            if time.monotonic() < self.toggles_after[service]:
                continue
            pending = self.toggle_requests(service)
            if pending:
                self.toggles[service] = self.spawn(['/usr/bin/python3', 'lab/realtime.py', 'apply', pending[0], '--service', service])

    def reset_studios(self):
        """No Studio outlives a restart: browser sessions are gone and the login must close."""
        subprocess.run(['/usr/bin/python3', 'lab/studio.py', 'reset'], cwd=ROOT, timeout=300, check=False)
        path = STATE/'control.sqlite'
        if path.exists():
            try:
                with closing(sqlite3.connect(path, timeout=5)) as database, database:
                    database.execute("UPDATE studio_sessions SET desired='stopped', state='stopped', failure=NULL")
            except sqlite3.Error:
                pass

    def record_worker_event(self, kind, reason):
        """One supervisor event, from the worker exit the supervisor already recorded."""
        return notification_producers.emit(kind, 'critical' if kind == 'worker.restart_limit' else 'warning',
                                           kind+'|installation', {}, 'system:supervisor', reason,
                                           {'restarts': len(self.restarts)}, catalog=self.catalog)

    def run(self):
        try:
            self.reset_studios()
            self.server = self.spawn(['bun', 'lab/upstream-server.ts'])
            self.start_worker()
            while not self.stop_event.wait(.25):
                self.check()
                self.schedule_backup()
                self.schedule_studios()
                self.schedule_sign_in()
                self.schedule_toggles()
        finally:
            if self.backup:
                terminate_group(self.backup)
            for process in self.studios.values():
                terminate_group(process)
            if self.sign_in:
                terminate_group(self.sign_in)
            for process in self.toggles.values():
                if process:
                    terminate_group(process)
            # Stop new HTTP mutations first, then drain the active worker effect.
            if self.server:
                terminate_group(self.server)
            if self.worker:
                terminate_group(self.worker)
            path = STATE/'supervisor.json'
            if path.exists() and json.loads(path.read_text()).get('pid') == os.getpid():
                path.unlink()


def run_stage(command, stop_event, timeout=180, pass_fds=(), env=None):
    process = subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),*command], cwd=ROOT, start_new_session=True, pass_fds=pass_fds, env=env)
    deadline = time.monotonic()+timeout
    try:
        while child_status(process) is None:
            if stop_event.wait(.1):
                raise InterruptedError('Local installation startup cancelled')
            if time.monotonic() >= deadline:
                raise RuntimeError('Local installation stage timed out')
        return child_status(process)
    finally:
        # A failed stage leader may leave a Docker CLI child holding a lock.
        terminate_group(process, grace=2)


def upgrade_outcome(started):
    """Confirms a pending upgrade or rollback, or moves a failed upgrade back (lab/upgrade.py)."""
    try:
        import upgrade
        return upgrade.after_start(started)
    except Exception as error:
        print(f'Upgrade bookkeeping failed: {error}', file=sys.stderr)
        return False


def main():
    if sys.argv[1:]:
        if sys.argv[1:] in (['--help'], ['-h']):
            print(__doc__+'\nRun from a terminal; Ctrl+C stops the console, worker and owned runtime while preserving volumes.')
            return
        raise SystemExit('Usage: /usr/bin/python3 lab/dev.py')
    os.chdir(ROOT)
    STATE.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    with (STATE/'supervisor.lock').open('a') as lock, (STATE/'worker.lock').open('a') as worker_lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another local installation runner is active.')
        # A one-shot/manual worker belongs to its caller, not this supervisor.
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Stop the existing manual worker before starting the runner.')
        started = False
        try:
            if run_stage(['/usr/bin/python3','lab/worker.py','--upstream','--settle-only'],stop_event,
                         pass_fds=(worker_lock.fileno(),),env=dict(os.environ,SBARBASE_WORKER_FD=str(worker_lock.fileno()))):
                raise RuntimeError('Provisioning receipt requires reconciliation before startup')
            fresh,detail=console_build_check.is_fresh()
            if fresh:
                # Rebuilding an up-to-date page costs a few hundred MiB and a minute.
                print('console build up to date: '+detail)
            elif run_stage(['bun', 'run', 'build:ui'], stop_event):
                raise RuntimeError('Console build failed')
            if stop_event.is_set():
                return
            started = True
            if run_stage(['/usr/bin/python3', 'lab/installation_runtime.py', 'up'], stop_event,
                         pass_fds=(worker_lock.fileno(),),env=dict(os.environ,SBARBASE_WORKER_FD=str(worker_lock.fileno()))):
                raise RuntimeError('Runtime startup failed; the installation runtime reported its own reason above')
            # The runtime start stage returned success, so the installation runtime is
            # durable: that is the state change this event records. A stage that fails
            # leaves no durable start, and no event is emitted for it here.
            notify_installation('installation.started')
            upgrade_outcome(True)
            if not stop_event.is_set():
                Supervisor(stop_event, worker_lock.fileno()).run()
        except InterruptedError:
            print('Local installation startup cancelled.', file=sys.stderr)
        except RuntimeError as error:
            # No event here: a stage failure is a return code and a stderr line, and this
            # path owns no durable state change to emit from. The runtime's own refusal, if
            # there was one, is emitted where its 0600 diagnostic is written.
            print(str(error), file=sys.stderr)
            if upgrade_outcome(False):
                print('The new version did not start, so the checkout moved back to the previous version. '
                      'It starts again on that version; lab/upgrade.py status shows the outcome.', file=sys.stderr)
            raise SystemExit(1)
        finally:
            if started:
                result = run_stage(['/usr/bin/python3', 'lab/installation_runtime.py', 'stop'], threading.Event(), timeout=90)
                if result:
                    print('Owned runtime stop failed; inspect its current container state.', file=sys.stderr)
                else:
                    # The stop stage returned success, so the owned runtime is down and
                    # that is the durable state change. emit() never raises, so the
                    # installation's exit status is unaffected.
                    notify_installation('installation.stopped')


if __name__ == '__main__':
    main()
