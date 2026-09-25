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
import traceback
import updates
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab/upstream'
# The exit status after an upgrade or rollback moved the checkout: the service manager starts
# Sbarbase again on the version the checkout now holds. systemd restarts on it
# (RestartForceExitStatus= in deploy/sbarbase.service), Docker's `restart: unless-stopped` on
# any exit. Neither restarts a clean exit 0, so this is never 0.
RESTART_FOR_UPGRADE = 42
# Read without importing lab/upgrade.py, so a start can tell that an upgrade is pending even
# when that module (the new version's code) cannot be imported.
UPGRADE_STATE = ROOT / '.lab/upgrades/state.json'
# Before an upgrade or rollback from the console moves the checkout, the supervisor drains
# itself: the worker claims no new job and exits once the one in hand settled (the marker
# 'worker-drain' in STATE, read by lab/worker.ts), and nothing else new starts. When that
# takes longer than this, the request fails and nothing moves.
DRAIN_SECONDS = 600
# The update scheduling looks past the request file (the verdict in current.json, whether a
# check is due, the automatic decision) at most this often.
UPDATES_EVERY = datetime.timedelta(seconds=15)


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
        self.toggles = {'realtime': None, 'functions': None, 'database': None, 'signing': None}
        self.toggles_after = {'realtime': 0.0, 'functions': 0.0, 'database': 0.0, 'signing': 0.0}
        self.backup_hour = backup_hour()
        self.backup_keep = backup_keep()
        self.restarts = collections.deque()
        self.worker_fd = worker_fd
        # The control catalog the operator's installation drains. None selects the
        # default upstream path; a test passes a private temporary catalog.
        self.catalog = catalog
        # While an upgrade waits for its health checks (lab/upgrade_health.Confirmation): polled
        # each turn, True once confirmed, RuntimeError past its deadline. Until then only the
        # console runs: no worker, backup, Studio, sign-in or toggle may leave an effect that
        # the way back, which restores the control state, would no longer know about.
        self.confirm = None
        # The update channel (lab/updates.py): at most one child at a time (a check, an upgrade or
        # a rollback), current.json as last published, the version this process runs, the last
        # rollback verdict with the record it judged, when this supervisor began scheduling
        # updates, when the scheduling next looks past the request file, a pause after the
        # scheduling itself failed, and whether this process exits with RESTART_FOR_UPGRADE once
        # it has stopped.
        self.update = None
        self.current = None
        self.running = None
        self.verdict = None
        self.updates_since = None
        self.updates_next = None
        self.updates_after = 0.0
        self.restart_for_upgrade = False
        # An upgrade or rollback waiting for the supervisor to drain (see DRAIN_SECONDS): the
        # child it runs once everything settled, and until when it waits.
        self.drain = None

    def spawn(self, command, **options):
        """A child bound to this process (lab/parent_bound.py) in a session of its own; `options`
        go to Popen (the output, the environment, descriptors to pass)."""
        return subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),*command], cwd=ROOT,
                                start_new_session=True, **options)

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
        self.worker = self.spawn(['/usr/bin/python3', 'lab/worker.py', '--upstream', '--watch'], pass_fds=(self.worker_fd,),
                                 env=dict(os.environ, SBARBASE_WORKER_FD=str(self.worker_fd)))
        self.descriptor()

    def drain_marker(self):
        return STATE / 'worker-drain'

    def paused(self):
        """True while an upgrade or rollback is prepared or runs. Children already running finish
        and are reaped; nothing new starts that the moving checkout could leave half done, or
        that would run the new version's scripts under this old supervisor: no provisioning job,
        Studio, sign-in or toggle apply, and no backup."""
        return self.drain is not None or (self.update is not None and self.update['kind'] != 'check')

    def check(self):
        if child_status(self.server) is not None:
            raise RuntimeError('Local API exited; stopping the installation')
        if self.worker is not None and child_status(self.worker) is not None:
            terminate_group(self.worker, grace=0)
            if self.paused():
                # Drained for an update: it stopped claiming jobs and exited on purpose.
                self.worker = None
                self.descriptor()
                return
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
            self.refresh_current()
            path = STATE/'endpoints.json'
            count = len(json.loads(path.read_text())) if path.exists() else 0
            if status == 3:
                # Local backups succeeded; the off-host copy failed and backup.py already
                # reported that as backup.failed, so a 'completed' notice would contradict it.
                return
            if status == 0:
                notification_producers.emit('backup.completed', 'info', 'backup.completed|installation', {},
                                            'system:supervisor', 'export_completed', {'environments': count},
                                            catalog=self.catalog)
            else:
                notification_producers.emit('backup.failed', 'critical', 'backup.failed|installation', {},
                                            'system:supervisor', 'export_failed', {'failed': status},
                                            catalog=self.catalog)
            return
        if self.paused():
            # An upgrade or rollback is prepared or runs: it takes its own backup first, and the
            # daily one starts after it (or on the new version), never at the same time.
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
        self.refresh_current()

    def refresh_current(self):
        """current.json again after something its verdict reads changed (the daily backup holds the
        backup lock), once this process has published it at all."""
        if self.running is not None:
            self.publish_current(rejudge=False)

    def spawn_logged(self, command, log):
        """Like spawn, with the child's output in a private log that is read when it ends. A file,
        never a pipe: a pipe nobody drains fills up and stops the child."""
        log.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            return self.spawn(command, stdout=handle, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUNBUFFERED='1'))

    def publish_current(self, rejudge=True):
        """What runs now, whether the console may offer a rollback and whether the release on offer
        can be installed now (lab/updates.py publish_current), written only when that changed.

        The running version is read once per process. The rollback verdict (which may read Git and
        the catalog) is judged again with `rejudge`, at start and after confirmation, and whenever
        the upgrade record changed; otherwise the last one is reused, so the refresh every turn
        of the update scheduling stays cheap."""
        try:
            import release_channel
            import upgrade
            state = upgrade.load_state()
            record = state if isinstance(state, dict) else {}
            judged = (record.get('phase'), record.get('started_at'))
            if rejudge or self.verdict is None or judged != self.verdict[0]:
                self.verdict = judged, updates.rollback_verdict(state)
            if self.running is None:
                self.running = release_channel.current_version()
            self.current = updates.publish_current(state, running=self.running, rollback=self.verdict[1],
                                                   blockers=updates.install_blockers(own_backup=self.backup is not None),
                                                   previous=None if rejudge else self.current)
        except Exception as error:
            print(f'Update status could not be recorded: {error}', file=sys.stderr, flush=True)

    def settle_updates(self):
        """A request the previous process left running or never picked up (lab/updates.py settle)."""
        try:
            import upgrade
            updates.settle(upgrade.load_state())
        except Exception as error:
            print(f'Update requests could not be settled: {error}', file=sys.stderr, flush=True)

    def schedule_updates(self, moment=None):
        """Checks for a newer release, carries out the console's requests and the automatic
        updates, one child at a time. Runs only once a pending upgrade is confirmed.

        Every turn follows the child, the drain and the request file; the rest (the verdict in
        current.json, whether a check is due, the automatic decision) at most every
        UPDATES_EVERY, which is soon enough for all of them and keeps a turn to one small read."""
        moment = moment or updates.now()
        if self.updates_since is None:
            self.updates_since = moment
        if self.update is not None:
            status = child_status(self.update['process'])
            if status is None:
                return
            terminate_group(self.update['process'], grace=0)
            job, self.update = self.update, None
            self.update_finished(job, status, moment)
            # A check found a release, or an upgrade or rollback now waits for its restart.
            self.refresh_current()
            return
        if self.drain is not None:
            self.continue_drain(moment)
            return
        import upgrade
        request = updates.read_request()
        if request is not None and request.get('state') != 'requested':
            # Finished, or running without a child of this process: it cannot be followed.
            final = request.get('state') in updates.FINAL
            updates.finish_request(request, request['state'] if final else 'failed',
                                   request.get('detail') if final else updates.MESSAGES['interrupted'], moment)
            return
        if request is not None:
            self.start_request(request, upgrade.load_state(), moment)
            return
        # A clock set back starts the interval again rather than waiting it out.
        if self.updates_next is not None and self.updates_next - UPDATES_EVERY <= moment < self.updates_next:
            return
        self.updates_next = moment + UPDATES_EVERY
        # A start whose first publish failed tries it whole again.
        self.publish_current(rejudge=self.running is None)
        state = upgrade.load_state()
        settings, document = updates.load_settings(), updates.read('available.json')
        if settings['check'] and updates.check_due(updates.read('check.json'), moment, self.updates_since, document is not None):
            self.start_update('check', None, moment)
            return
        release = updates.automatic_release(settings, document, state, moment, self.backup is not None)
        if release is not None and updates.blocked() is None:
            # Counted before the request exists. A try that passed its point of no return is
            # never repeated; one that stopped before it is tried again, a bounded number of
            # times (lab/updates.py spend and automatic_release).
            updates.begin_automatic(release['version'], moment)
            if updates.create_request('apply', release['version'], 'automatic', moment):
                print(f"Automatic update to Sbarbase {release['version']} requested.", flush=True)

    def start_request(self, request, state, moment):
        """Carries out one request, after checking it again: the file is never trusted."""
        kind = request.get('kind')
        if kind == 'check':
            self.start_update('check', request, moment)
            return
        if self.backup is not None:
            # The daily backup runs; the upgrade starts after it.
            return
        if kind == 'apply':
            automatic = request.get('trigger') == 'automatic'
            # Only an operator acknowledges the warning an attended release carries; automatic
            # mode never installs one.
            acknowledged = request.get('acknowledged') is True and not automatic
            document = updates.read('available.json')
            refusal = updates.apply_refusal(request.get('version'), document, state, acknowledged)
            if refusal:
                updates.finish_request(request, 'failed', refusal, moment)
                return
            # The release the check verified, named by its version; upgrade.py verifies it again.
            release = document['available']
            command = ['/usr/bin/python3', 'lab/upgrade.py', 'start', '--release', 'v' + release['version'],
                       '--trigger', 'automatic' if automatic else 'console']
            if release.get('class') == 'attended' and acknowledged:
                command += ['--allow-class', 'attended']
        else:
            possible, reason = updates.rollback_verdict(state)
            if not possible:
                updates.finish_request(request, 'failed', reason, moment)
                return
            command = ['/usr/bin/python3', 'lab/upgrade.py', 'rollback']
        self.begin_drain(kind, request, moment, command)

    def begin_drain(self, kind, request, moment, command):
        """Before an upgrade or rollback moves the checkout, this supervisor quiesces itself: the
        worker finishes the job in hand and claims no other, running children finish, nothing new
        starts (paused), and no operation record may be left unsettled. Only then does the child
        run, so no effect is in flight when the checkout moves and no script of the new version
        is started by this old supervisor. A marker is written only for a worker that runs."""
        if request is not None:
            request = updates.update_request(request, state='running', started_at=updates.stamp(moment)) or request
        if self.worker is not None:
            marker = self.drain_marker()
            marker.write_text('{}')
        self.drain = {'kind': kind, 'request': request, 'command': command,
                      'until': time.monotonic() + DRAIN_SECONDS}
        print('Finishing provisioning and other work before the '
              + ('update.' if kind == 'apply' else 'rollback.'), flush=True)
        self.continue_drain(moment)

    def idle(self):
        """Nothing is in flight that moving the checkout could interrupt: the worker stopped, no
        Studio, sign-in, toggle or backup child runs, and no operation record (a provisioning
        receipt, an HBA journal or migration) waits to be settled."""
        if self.worker is not None or self.backup is not None or self.sign_in is not None or self.studios:
            return False
        if any(process is not None for process in self.toggles.values()):
            return False
        import upgrade
        return not any(upgrade.present(upgrade.UPSTREAM / name) for name in upgrade.UNSETTLED)

    def continue_drain(self, moment):
        job = self.drain
        if self.idle():
            self.drain = None
            self.start_update(job['kind'], job['request'], moment, job['command'])
            return
        if time.monotonic() < job['until']:
            return
        self.drain = None
        detail = (f'Provisioning or another operation did not finish within {DRAIN_SECONDS // 60} minutes, '
                  'so nothing was changed. Try again later.')
        print(f"The {'update' if job['kind'] == 'apply' else 'rollback'} did not go ahead: {detail}", flush=True)
        if job['request'] is not None:
            updates.finish_request(job['request'], 'failed', detail, moment)
        self.resume_work()

    def resume_work(self):
        """After an upgrade or rollback that did not go ahead: provisioning continues."""
        self.drain_marker().unlink(missing_ok=True)
        if self.worker is None and self.confirm is None and self.worker_fd is not None:
            self.start_worker()

    def start_update(self, kind, request, moment, command=None):
        if kind == 'check':
            command = ['/usr/bin/python3', 'lab/upgrade.py', 'channel', '--json']
        if request is not None:
            request = updates.update_request(request, state='running', started_at=updates.stamp(moment)) or request
        # The child records how it ended under this id (lab/upgrade.py record_outcome).
        run = request['id'] if request is not None else uuid.uuid4().hex
        print({'check': 'Checking for a newer Sbarbase release.', 'apply': 'Update to a newer Sbarbase release started.',
               'rollback': 'Rollback to the previous Sbarbase version started.'}[kind], flush=True)
        self.update = {'kind': kind, 'request': request, 'run': run,
                       'process': self.spawn_logged([*command, '--request', run], updates.path(kind + '.log'))}

    def update_finished(self, job, status, moment):
        """Records how a check, an upgrade or a rollback ended, from the outcome the child
        recorded. After an upgrade or rollback that moved the checkout, this process stops and
        exits with RESTART_FOR_UPGRADE."""
        kind, request = job['kind'], job['request']
        try:
            output = updates.path(kind + '.log').read_text(errors='replace')
        except OSError:
            output = ''
        # The child's output goes to the journal once it ended (the log file is replaced each run).
        for line in output.splitlines()[-40:]:
            print('  ' + line, flush=True)
        result = updates.outcome(job['run'])
        if kind == 'check':
            error, document = updates.finish_check(status, result, moment)
            if error is None:
                updates.announce_available(document, catalog=self.catalog)
            else:
                print('The update check did not finish: ' + error, flush=True)
            if request is not None:
                updates.finish_request(request, 'failed' if error else 'done', error, moment)
            return
        if request is not None:
            updates.spend(request, result, moved=status == 0)
        if status != 0:
            detail = updates.failure(result)
            print(f"The {'update' if kind == 'apply' else 'rollback'} did not go ahead: {detail}", flush=True)
            if request is not None:
                updates.finish_request(request, 'failed', detail, moment)
            self.resume_work()
            return
        if kind == 'apply':
            detail = f"The checkout moved to Sbarbase {(request or {}).get('version', 'the new version')}. Sbarbase restarts on it now."
        else:
            # The ledger learns of the way back when the previous version confirms it (announce_outcome).
            detail = 'The checkout moved back to the previous version. Sbarbase restarts on it now.'
        if request is not None:
            updates.finish_request(request, 'done', detail, moment)
        print(detail, flush=True)
        self.restart_for_upgrade = True
        self.stop_event.set()

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
        if self.paused():
            return
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
        if time.monotonic() < self.sign_in_after or self.paused():
            return
        pending = self.sign_in_requests()
        if pending:
            self.sign_in = self.spawn(['/usr/bin/python3', 'lab/auth_settings.py', 'apply', pending[0]])

    def toggle_requests(self, service):
        """Runtimes whose Realtime, Edge Functions or database access should be turned on or off, oldest first."""
        path = STATE/'control.sqlite'
        if not path.exists():
            return []
        table = {'realtime': 'realtime_settings', 'functions': 'functions_settings', 'database': 'database_access',
                 'signing': 'signing_keys'}[service]
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
            if time.monotonic() < self.toggles_after[service] or self.paused():
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
            self.settle_updates()
            self.publish_current()
            # A drain left by the process before (an update that moved the checkout, or a crash)
            # must not keep this worker from claiming jobs.
            self.drain_marker().unlink(missing_ok=True)
            self.server = self.spawn(['bun', 'lab/upstream-server.ts'])
            if self.confirm is None:
                self.start_worker()
            else:
                # supervisor.json names the server, which the health check and wait-console read.
                self.descriptor()
            while not self.stop_event.wait(.25):
                self.check()
                if self.confirm is not None:
                    if not self.confirm.poll():
                        continue
                    self.confirm = None
                    self.start_worker()
                    # Confirmed: the console may now offer a rollback.
                    self.publish_current()
                self.schedule_backup()
                self.schedule_studios()
                self.schedule_sign_in()
                self.schedule_toggles()
                if time.monotonic() >= self.updates_after:
                    try:
                        self.schedule_updates()
                    except Exception as error:
                        # The update channel never stops the installation; it pauses and tries again.
                        print(f'Update scheduling failed: {error}', file=sys.stderr, flush=True)
                        self.updates_after = time.monotonic() + 60
        finally:
            if self.update:
                terminate_group(self.update['process'])
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


def upgrade_outcome(started, catalog=None, reason=None):
    """Confirms a pending upgrade or rollback, or moves a failed upgrade back (lab/upgrade.py),
    then emits the notification that outcome earns (lab/updates.py announce_outcome). The
    automatic way back has restored the control state by then, so its event lands in the
    catalog the previous version opens."""
    try:
        import upgrade
        before = upgrade.load_state()
        result = upgrade.after_start(started, reason)
    except Exception as error:
        print(f'Upgrade bookkeeping failed: {error}', file=sys.stderr)
        return False
    try:
        updates.announce_outcome(before, upgrade.load_state(), catalog=catalog)
    except Exception as error:
        print(f'Upgrade notification failed: {error}', file=sys.stderr)
    return result


def upgrade_notices(catalog=None):
    """Emits the outcomes the guard recorded (lab/upgrade_guard.py notice): a way back it took, or
    a rollback_failed, which no process with the notification machinery saw happen. Each goes
    through announce_outcome as if this process had made the change, so the notification and the
    ledger entry are the same as for one it made; then the notices leave the record. Never raises."""
    try:
        import upgrade
        state = upgrade.load_state()
        notices = state.get('notices') if isinstance(state, dict) else None
        if not isinstance(notices, list) or not notices:
            return 0
        for item in notices:
            if isinstance(item, dict):
                updates.announce_outcome({**state, 'phase': item.get('was')}, {**state, 'phase': item.get('phase')},
                                         catalog=catalog)
        upgrade.clear_notices(len(notices))
        return len(notices)
    except Exception as error:
        print(f'Upgrade notification failed: {error}', file=sys.stderr)
        return 0


def upgrade_confirmed(catalog=None):
    """Records that a pending upgrade or rollback passed its health checks. True only once that
    is saved: until then the hold stays, the worker does not start, and the supervisor tries
    again on its next round (lab/upgrade_health.Confirmation)."""
    try:
        import upgrade
        before = upgrade.load_state()
        upgrade.after_start(True)
        after = upgrade.load_state()
    except Exception as error:
        print(f'The upgrade confirmation was not saved: {error}', file=sys.stderr, flush=True)
        return False
    if isinstance(after, dict) and after.get('phase') in upgrade.PENDING:
        return False
    try:
        updates.announce_outcome(before, after, catalog=catalog)
    except Exception as error:
        print(f'Upgrade notification failed: {error}', file=sys.stderr)
    return True


def upgrade_pending():
    """Whether state.json says an upgrade or rollback waits for confirmation, read directly."""
    try:
        state = json.loads(UPGRADE_STATE.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get('phase') in ('applied', 'rolling_back')


def upgrade_prepare():
    """True when this start confirms a pending upgrade or rollback (lab/upgrade.py before_start).

    A control state snapshot that cannot be taken, or a checkout that is not the version being
    confirmed, is a failed start, so the way back runs before the new version touched anything.
    Any other bookkeeping failure is a failed start too while an upgrade is pending (an untrusted
    version must never run ungated), and leaves the start ungated otherwise, as it was before
    health-gated confirmation existed.
    """
    try:
        import upgrade
        return upgrade.before_start()
    except Exception as error:
        if error.__class__.__name__ == 'UpgradeError':
            raise RuntimeError(f'The pending upgrade cannot start: {error}') from None
        if upgrade_pending():
            raise RuntimeError(f'The pending upgrade cannot start: its bookkeeping failed ({error.__class__.__name__}: {error})') from None
        print(f'Upgrade bookkeeping failed: {error}', file=sys.stderr)
        return False


def upgrade_close_attempt():
    """A start that stopped cleanly before its verdict closes its attempt (lab/upgrade.py
    close_attempt), so the next start's guard does not take the stop for a crash."""
    try:
        import upgrade
        upgrade.close_attempt()
    except Exception as error:
        print(f'Upgrade bookkeeping failed: {error}', file=sys.stderr)


def checkout_head():
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def run_guard(environment=os.environ):
    """The upgrade guard (lab/upgrade_guard.py) is the first step of every start. The systemd
    unit and the container's start script run it before this process (and say so with
    SBARBASE_GUARDED=1); a start from a terminal runs it here, before taking any lock, since the
    guard takes the supervisor lock itself. When it moved the checkout back, nothing more of
    this version runs: the process exits so the previous version starts."""
    if environment.get('SBARBASE_GUARDED') == '1':
        return
    copy = ROOT / '.lab' / 'upgrades' / 'guard.py'
    script = copy if copy.is_file() else ROOT / 'lab' / 'upgrade_guard.py'
    before = checkout_head()
    if subprocess.run(['/usr/bin/python3', str(script), str(ROOT)], cwd=ROOT).returncode:
        raise SystemExit('The upgrade guard stopped this start; its reason is above.')
    if checkout_head() != before:
        print('The upgrade guard moved the checkout back to the previous version. Under systemd or Docker '
              'Sbarbase starts again by itself; in a terminal, start it again: /usr/bin/python3 lab/dev.py',
              file=sys.stderr, flush=True)
        raise SystemExit(RESTART_FOR_UPGRADE)


def upgrade_confirmation(supervisor):
    """Confirms the pending upgrade from inside the supervisor once its health checks pass."""
    import upgrade_health
    return upgrade_health.Confirmation(
        lambda: upgrade_health.check(STATE, supervisor.server.pid if supervisor.server else None),
        lambda: upgrade_confirmed(supervisor.catalog))


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
        restart = False
        gated = False
        failed = False
        try:
            # Before the settle stage, which may open and migrate the control catalog.
            gated = upgrade_prepare()
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
            # A way back the guard took before this start is announced now, in this version's catalog.
            upgrade_notices()
            if not gated:
                upgrade_outcome(True)
            if not stop_event.is_set():
                supervisor = Supervisor(stop_event, worker_lock.fileno())
                if gated:
                    # Confirmed only once the console and every environment answer; a deadline
                    # passed raises RuntimeError below, and the way back runs.
                    supervisor.confirm = upgrade_confirmation(supervisor)
                supervisor.run()
                restart = getattr(supervisor, 'restart_for_upgrade', False)
        except InterruptedError:
            print('Local installation startup cancelled.', file=sys.stderr)
        except KeyboardInterrupt:
            # Ctrl+C normally arrives as the stop event above; either way it is a stop, not a
            # failure, so it never takes the way back.
            print('Local installation stopped.', file=sys.stderr)
        except BaseException as error:
            # Every failure of this start, not only the RuntimeError its own stages raise: an
            # exception in new startup code (a TypeError, an ImportError, a Studio reset that
            # timed out) must take the way back too, never leave the start restarting with
            # application traffic held.
            # No event here: a stage failure is a return code and a stderr line, and this
            # path owns no durable state change to emit from. The runtime's own refusal, if
            # there was one, is emitted where its 0600 diagnostic is written.
            failed = True
            if not isinstance(error, (RuntimeError, SystemExit)):
                traceback.print_exc()
            detail = str(error) or error.__class__.__name__
            print(detail, file=sys.stderr)
            if upgrade_outcome(False, reason=detail):
                print('The new version did not start, so the checkout moved back to the previous version. '
                      'It starts again on that version; lab/upgrade.py status shows the outcome.', file=sys.stderr)
            raise SystemExit(1)
        finally:
            if gated and not failed:
                # Stopped before the health checks decided (a stop signal, Ctrl+C): not a crash.
                upgrade_close_attempt()
            if started:
                result = run_stage(['/usr/bin/python3', 'lab/installation_runtime.py', 'stop'], threading.Event(), timeout=90)
                if result:
                    print('Owned runtime stop failed; inspect its current container state.', file=sys.stderr)
                else:
                    # The stop stage returned success, so the owned runtime is down and
                    # that is the durable state change. emit() never raises, so the
                    # installation's exit status is unaffected.
                    notify_installation('installation.stopped')
    if restart:
        # After the owned runtime stopped and the locks are released: the service manager
        # starts the version the checkout moved to.
        print('Sbarbase moved to another version and stopped so it can start on it. Under systemd or '
              'Docker it starts again by itself; in a terminal, start it again: /usr/bin/python3 lab/dev.py',
              file=sys.stderr, flush=True)
        raise SystemExit(RESTART_FOR_UPGRADE)


if __name__ == '__main__':
    if not sys.argv[1:]:
        # Before main() takes any lock: the guard takes the supervisor lock itself (run_guard).
        run_guard()
    main()
