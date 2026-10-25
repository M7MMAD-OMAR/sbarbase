"""A short, bounded, held window for a change that must restart PostgreSQL. Off by default.

Behind SBARBASE_DATABASE_WINDOW=1 (the same pattern as SBARBASE_ROLLING_SERVICES and
SBARBASE_HANDOVER); without it nothing here runs and nothing else changes. Upstream currency P2
and the study docs/engineering/plans/2026-10-23-upgrades-without-outage.md (PostgreSQL restarts).

  /usr/bin/python3 lab/database_window.py run --setting max_connections [--setting NAME ...] [--seconds N]
  /usr/bin/python3 lab/database_window.py status

`run` takes the settings a change touches and does, in this order:

  1. refuses when no named setting needs a restart: PostgreSQL applies every setting whose
     pg_settings.context is not `postmaster` with `SELECT pg_reload_conf()`, and that is not
     done here. An unknown name is refused too (fail closed);
  2. CHECKPOINT, so the restart's own shutdown checkpoint is short;
  3. holds application traffic: the window marker (.lab/upgrades/database-window, `{since,
     until}`) makes the gateway answer 503 with Retry-After, pauses management changes, and
     closes the direct and external database listeners to new sessions (src/gateway/hold.ts
     windowOpen), then waits HOLD_SETTLE_SECONDS for every reader's cached answer to expire;
  4. restarts the database container (`docker restart`, bounded by what is left of the window);
  5. checks that the database answers, until the window ends;
  6. releases the hold.

The whole of steps 3 to 6 lasts at most the window (SBARBASE_DATABASE_WINDOW_SECONDS, default
DEFAULT_SECONDS, never more than HARD_SECONDS). A failure at any step, or the window ending, releases
the hold and reports; a window never stays open: the marker is removed in a `finally`, and its own
`until` makes the gateway stop holding even when this process is killed. Writes are not paused
(lab/write_pause.py is not used), so there is nothing to leave paused. One window at a time (a
lock), and none while an upgrade waits for its confirmation or holds its marker.

Limits, as built: the direct database listeners refuse new sessions during the window (as every
hold does), they do not queue them; sessions already open die with the restart, and each service
reconnects on its own (not measured). A request already past the gateway when the window opens is
not waited for. A timeout releases the hold while the database may still be starting: the report
says so. Proven by unit tests only against stubbed Docker and database; never run on a server.
"""
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time

import docker_profile
import durable_runtime as runtime
import run as lab
import upgrade

SWITCH = 'SBARBASE_DATABASE_WINDOW'
SECONDS = 'SBARBASE_DATABASE_WINDOW_SECONDS'
DEFAULT_SECONDS = 30
HARD_SECONDS = 120
# The gateway rereads its hold marker at most once a second (src/gateway/hold.ts), so the restart
# waits for that, plus a margin, after the marker is written.
HOLD_SETTLE_SECONDS = 1.5
CHECKPOINT_SECONDS = runtime.CHECKPOINT_SECONDS
STEP_SECONDS = 10
POLL_SECONDS = 0.5
MARKER = upgrade.UPGRADES / 'database-window'
LOCK = upgrade.UPGRADES / 'database-window.lock'
# What the database says while it is ready (the same question runtime.Runtime.wait_database asks).
READY_SQL = "SELECT to_regrole('supabase_privileged_role') IS NOT NULL;"


class WindowError(RuntimeError):
    """A refusal or a failed step. The hold is already released when it reaches the caller."""


def enabled(environ=os.environ):
    return environ.get(SWITCH) == '1'


def window_seconds(environ=os.environ):
    """The window's length: the configured one when it is a whole number from 1 to HARD_SECONDS
    (more is cut to HARD_SECONDS), DEFAULT_SECONDS for anything else."""
    try:
        value = int(environ.get(SECONDS, ''))
    except ValueError:
        return DEFAULT_SECONDS
    return min(value, HARD_SECONDS) if value >= 1 else DEFAULT_SECONDS


class Docker:
    """The real database: every call is bounded."""

    def psql(self, query, timeout=STEP_SECONDS):
        try:
            result = subprocess.run(docker_profile.docker_command('exec', '-i', runtime.DB, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                                                                  '-U', 'supabase_admin', '-d', 'postgres'),
                                    input=query, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise WindowError('A database statement did not answer in time') from None
        if result.returncode:
            raise WindowError('A database statement failed')
        return result.stdout.strip()

    def settings(self, names):
        """{name: context} for the names pg_settings knows."""
        quoted = ', '.join("'" + name + "'" for name in names)  # names are validated by `valid` first
        rows = self.psql(f'SELECT name || E\'\\t\' || context FROM pg_settings WHERE name IN ({quoted});')
        return dict(row.split('\t', 1) for row in rows.splitlines() if '\t' in row)

    def checkpoint(self):
        # Like runtime.checkpoint: bounded, and a CHECKPOINT that does not finish is not a failure.
        try:
            self.psql('CHECKPOINT;', timeout=CHECKPOINT_SECONDS)
        except WindowError:
            pass

    def restart(self, timeout):
        try:
            result = subprocess.run(docker_profile.docker_command('restart', '-t', str(runtime.STOP_DATABASE_SECONDS), runtime.DB),
                                    capture_output=True, text=True, timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            raise WindowError('The database container did not restart within the window') from None
        if result.returncode:
            raise WindowError('The database container did not restart')

    def ready(self, timeout=STEP_SECONDS):
        try:
            if self.psql(READY_SQL, timeout=timeout) != 't':
                return False
            return subprocess.run(docker_profile.docker_command('exec', runtime.DB, 'pg_isready', '-h', '127.0.0.1'),
                                  capture_output=True, timeout=timeout).returncode == 0
        except (WindowError, subprocess.TimeoutExpired):
            return False


def valid(name):
    """A setting name as PostgreSQL spells them: lower case letters, digits, underscore and dot."""
    return bool(name) and len(name) <= 80 and all(c.islower() or c.isdigit() or c in '_.' for c in name)


def needs_restart(names, database):
    """Which of the named settings only a restart applies. Raises WindowError when none does."""
    bad = [name for name in names if not valid(name)]
    if not names or bad:
        raise WindowError('Name the settings the change touches, each as PostgreSQL spells it (--setting NAME)'
                          if not names else f'Not a PostgreSQL setting name: {bad[0][:40]}')
    contexts = database.settings(names)
    unknown = [name for name in names if name not in contexts]
    if unknown:
        raise WindowError(f'PostgreSQL does not know the setting {unknown[0]}; nothing was changed')
    restart = [name for name in names if contexts[name] == 'postmaster']
    if not restart:
        raise WindowError('No named setting needs a restart (none has context postmaster): a reload applies them, '
                          'run SELECT pg_reload_conf(); nothing was restarted and nothing was held')
    return restart


@contextlib.contextmanager
def exclusive(lock=None, upgrade_lock=None, backup_lock=None):
    """One window at a time: a second one is refused, not queued. It also takes the upgrade lock, so no start,
    rollback or confirmation runs while the database restarts, and it is refused while a backup holds its lock."""
    lock = lock or LOCK
    upgrade_lock = upgrade_lock or upgrade.LOCK
    backup_lock = backup_lock or lab.STATE / 'backup.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WindowError('Another database window is open') from None
        if upgrade.held(backup_lock):
            raise WindowError('A backup or restore holds its lock; wait for it, nothing was changed')
        try:
            with upgrade.exclusive(upgrade_lock, 'An upgrade or rollback is running; wait for it, nothing was changed'):
                yield
        except upgrade.UpgradeError as error:
            raise WindowError(str(error)) from None


def open_hold(seconds, marker=None, wall=time.time):
    """Writes the marker the gateway reads; its `until` ends the hold by itself."""
    marker = marker or MARKER
    now = wall()
    marker.parent.mkdir(parents=True, exist_ok=True)
    lab.atomic(marker, {'since': upgrade.now(), 'until': int((now + seconds) * 1000)})


def release_hold(marker=None):
    (marker or MARKER).unlink(missing_ok=True)


def refusal(environ=os.environ):
    """Why a window must not open now, or None."""
    if not enabled(environ):
        return f'{SWITCH} is not 1; nothing was changed'
    if upgrade.HOLD.exists():
        return 'An upgrade holds application traffic or waits for its confirmation; wait for it, nothing was changed'
    return None


def run(names, *, environ=os.environ, database=None, hold=open_hold, release=release_hold, lock=exclusive,
        clock=time.monotonic, sleep=time.sleep, report=print):
    """The window. Returns the seconds the hold lasted; raises WindowError after releasing the hold."""
    why = refusal(environ)
    if why:
        raise WindowError(why)
    database = database or Docker()
    seconds = window_seconds(environ)
    with lock():
        needs_restart(names, database)
        database.checkpoint()
        started = clock()
        deadline = started + seconds
        try:
            hold(seconds)
            sleep(HOLD_SETTLE_SECONDS)
            left = deadline - clock()
            if left <= 0:
                raise WindowError(f'The window ({seconds} s) ended before the restart began')
            database.restart(left)
            while True:
                if database.ready(max(1, min(STEP_SECONDS, deadline - clock()))):
                    break
                if clock() >= deadline:
                    raise WindowError(f'The database was not ready when the window ({seconds} s) ended; the hold is released '
                                      'and the database may still be starting: check it before anything else')
                sleep(POLL_SECONDS)
        finally:
            release()
        held = clock() - started
    report(f'The database restarted and answers; the hold lasted {held:.1f} s of at most {seconds} s.')
    return held


def status(marker=None):
    path = marker or MARKER
    if not path.exists():
        return 'No database window is open.'
    try:
        until = json.loads(path.read_text())['until'] / 1000
    except (OSError, ValueError, KeyError, TypeError):
        return 'A window marker is present but unreadable; the gateway ignores it.'
    left = until - time.time()
    return f'A window is open, {left:.0f} s left.' if left > 0 else 'A window marker is present but ended; the gateway ignores it.'


def main(argv=None):
    parser = argparse.ArgumentParser(description='A bounded, held window for a PostgreSQL restart (off by default)')
    sub = parser.add_subparsers(dest='command', required=True)
    running = sub.add_parser('run')
    running.add_argument('--setting', action='append', default=[], help='a setting the change touches (repeat)')
    running.add_argument('--seconds', type=int, help=f'the window, at most {HARD_SECONDS}')
    sub.add_parser('status')
    args = parser.parse_args(argv)
    os.chdir(upgrade.ROOT)
    if args.command == 'status':
        print(status())
        return 0
    environ = dict(os.environ)
    if args.seconds is not None:
        environ[SECONDS] = str(args.seconds)
    try:
        run(args.setting, environ=environ)
        return 0
    except WindowError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
