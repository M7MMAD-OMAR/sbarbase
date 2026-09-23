"""The producer side of the operator notifications: one event per durable state change.

Every producer named in docs/engineering/OPERATOR-NOTIFICATIONS.md section 8, step 7 to 9 that is not
the catalog itself is exercised here through its real entry point:

- lab/dev.py: installation started, installation stopped, and the supervisor's worker
  restart and restart limit.
- lab/installation_runtime.py: a start that failed.
- lab/source_fence.py: a fence applied and a fence released.
- lab/recovery-export.py: an export completed and an export failed.
- lab/recovery-restore-db.py: a restore verified and a restore failed.

Each test drives the real state change the producer reports: the fence through the fence
primitive, the worker restart through disposable child processes, the failed start through
the diagnostic path, the cleanup failure through the real cleanup_target with disposable
fake docker results. The catalog is always a private temporary file created through the
real TypeScript Catalog, never the retained .lab/ or .secrets/ trees.

The transaction boundary is asserted twice. First positively: the producer's own durable
catalog record and the notification_outbox row exist together. Then negatively: when the
outbox insert is refused by a SQLite trigger, the record rolls back with it and the
operation still succeeds. A producer that moved its enqueue out of the transaction fails
the negative test, because the record would then survive.
"""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dev
import notify
import notification_producers
from source_fence import fence, unfence

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = load('recovery_export_producers', 'recovery-export.py')
restore = load('recovery_restore_producers', 'recovery-restore-db.py')

CREATE = """
import {{Catalog}} from '{root}/src/control/catalog';
const c=new Catalog({catalog});
c.close();
"""


def bun(source):
    if shutil.which('bun') is None:
        raise AssertionError('bun is required to exercise the real catalog')
    result = subprocess.run(['bun', '-e', source], capture_output=True, text=True, cwd=ROOT)
    if result.returncode:
        raise AssertionError('catalog driver failed: ' + result.stderr[-400:])
    return result.stdout.strip()


class ProducerCase(unittest.TestCase):
    """One private temporary catalog per test, created by the real catalog schema."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='sbarbase-producers-'))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.catalog = self.directory / 'control.sqlite'
        bun(CREATE.format(root=ROOT, catalog=json.dumps(str(self.catalog))))

    def rows(self, sql, parameters=()):
        with closing(sqlite3.connect(self.catalog)) as database:
            return database.execute(sql, parameters).fetchall()

    def refuse_outbox(self):
        """Make the outbox insert abort, so a shared transaction must roll back."""
        with closing(sqlite3.connect(self.catalog)) as database:
            database.execute('CREATE TRIGGER refuse_outbox BEFORE INSERT ON notification_outbox '
                             "BEGIN SELECT RAISE(ABORT,'refused for the producer test'); END;")
            database.commit()

    def outbox(self):
        return self.rows('SELECT kind,severity,reason,environment FROM notification_outbox')


class FenceProducerTests(ProducerCase):
    """lab/source_fence.py. The state change is the retained database fence."""

    def sql(self):
        state = {'blocked': False}

        def run(query):
            if query.startswith('ALTER DATABASE'):
                state['blocked'] = 'false' in query
                return SimpleNamespace(stdout='')
            if query.startswith('SELECT NOT'):
                return SimpleNamespace(stdout='t' if state['blocked'] else 'f')
            if 'count(*) FROM pg_database' in query:
                return SimpleNamespace(stdout='1')
            return SimpleNamespace(stdout='0')

        return run

    def environment(self):
        return 'e_' + 'a' * 24

    def test_a_retained_fence_emits_the_applied_event_in_the_same_transaction(self):
        e = self.environment()
        self.assertEqual(fence(self.sql(), e, catalog=self.catalog), {'connections_refused': True, 'sessions': 0})
        self.assertEqual(self.outbox(), [('fence.applied', 'critical', 'operator_request', e)])
        self.assertEqual(self.rows('SELECT action,subject FROM audit_events'), [('fence.applied', e)])
        self.assertEqual(self.rows('SELECT channel,state,attempts FROM notification_delivery ORDER BY channel'),
                         [('email', 'pending', 0), ('webhook', 'pending', 0)])

    def test_an_explicit_rollback_emits_the_released_event(self):
        e = self.environment()
        self.assertIsNone(unfence(self.sql(), e, catalog=self.catalog))
        self.assertEqual(self.outbox(), [('fence.released', 'warning', 'operator_request', e)])

    def test_a_refused_enqueue_rolls_back_the_record_and_never_fails_the_fence(self):
        """The same-transaction assertion. Moving the enqueue out breaks this test."""
        self.refuse_outbox()
        e = self.environment()
        # The fence still succeeds: a notification failure must not change the operation.
        self.assertEqual(fence(self.sql(), e, catalog=self.catalog), {'connections_refused': True, 'sessions': 0})
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.rows('SELECT count(*) FROM audit_events'), [(0,)])

    def test_a_fence_that_is_not_retained_emits_nothing(self):
        # The database never reports the fence, so the primitive refuses before the emit.
        def sql(query):
            if 'count(*) FROM pg_database' in query:
                return SimpleNamespace(stdout='1')
            if query.startswith('SELECT NOT'):
                return SimpleNamespace(stdout='f')
            return SimpleNamespace(stdout='0')
        with self.assertRaises(RuntimeError):
            fence(sql, self.environment(), catalog=self.catalog)
        self.assertEqual(self.outbox(), [])


class InstallationProducerTests(ProducerCase):
    """lab/dev.py and lab/installation_runtime.py. The state change is a completed stage."""

    def setUp(self):
        super().setUp()
        self.state = self.directory / 'state'
        self.state.mkdir()
        saved = (dev.STATE, notification_producers.CATALOG)
        dev.STATE = self.state
        notification_producers.CATALOG = self.catalog
        self.addCleanup(self.restore_state, saved)

    def restore_state(self, saved):
        dev.STATE, notification_producers.CATALOG = saved

    def stub(self, up_status=0):
        stages = []

        def run_stage(command, stop_event, timeout=180, pass_fds=(), env=None):
            stages.append(command)
            if 'lab/installation_runtime.py' in command and 'up' in command:
                return up_status
            return 0

        class StubSupervisor:
            def __init__(self, stop_event, worker_fd, catalog=None):
                pass

            def run(self):
                return None

        return stages, run_stage, StubSupervisor

    def main(self, run_stage, supervisor):
        with patch.object(dev, 'run_stage', run_stage), \
             patch.object(dev.console_build_check, 'is_fresh', return_value=(True, 'fresh')), \
             patch.object(dev, 'Supervisor', supervisor), \
             patch.object(dev.os, 'chdir'), \
             patch.object(dev.sys, 'argv', ['dev.py']):
            dev.main()

    def test_a_completed_start_and_stop_emit_their_events(self):
        stages, run_stage, supervisor = self.stub()
        self.main(run_stage, supervisor)
        self.assertTrue(any('lab/installation_runtime.py' in command for command in stages))
        self.assertEqual(self.rows('SELECT action FROM audit_events ORDER BY sequence'),
                         [('installation.started',), ('installation.stopped',)])
        self.assertEqual(self.rows('SELECT kind,severity FROM notification_outbox ORDER BY rowid'),
                         [('installation.started', 'info'), ('installation.stopped', 'info')])

    def test_a_failed_start_stage_emits_no_start(self):
        _, run_stage, supervisor = self.stub(up_status=1)
        with self.assertRaises(SystemExit):
            self.main(run_stage, supervisor)
        self.assertEqual(self.rows("SELECT kind FROM notification_outbox "
                                   "WHERE kind='installation.started'"), [])
        # The runtime was stopped in the finally block, so the stop is still recorded.
        self.assertEqual(self.rows("SELECT kind FROM notification_outbox "
                                   "WHERE kind='installation.stopped'"), [('installation.stopped',)])

    def test_a_restarted_worker_emits_a_restart_event_from_the_recorded_descriptor(self):
        supervisor = dev.Supervisor(catalog=self.catalog)
        supervisor.server = subprocess.Popen(['/usr/bin/python3', '-c', 'import time; time.sleep(60)'],
                                             start_new_session=True)
        supervisor.worker = subprocess.Popen(['/usr/bin/python3', '-c', 'pass'], start_new_session=True)
        supervisor.worker.wait(timeout=5)
        try:
            with patch.object(dev.Supervisor, 'start_worker', lambda self: None):
                supervisor.check()
        finally:
            dev.terminate_group(supervisor.server, grace=0)
        self.assertEqual(self.outbox(), [('worker.restart', 'warning', 'worker_restart', None)])
        self.assertEqual(self.rows('SELECT action FROM audit_events'), [('worker.restart',)])

    def test_a_reached_restart_limit_emits_before_it_stops_the_installation(self):
        supervisor = dev.Supervisor(catalog=self.catalog)
        supervisor.server = subprocess.Popen(['/usr/bin/python3', '-c', 'import time; time.sleep(60)'],
                                             start_new_session=True)
        supervisor.worker = subprocess.Popen(['/usr/bin/python3', '-c', 'pass'], start_new_session=True)
        supervisor.worker.wait(timeout=5)
        supervisor.restarts = __import__('collections').deque([1e9, 1e9, 1e9])
        try:
            with self.assertRaisesRegex(RuntimeError, 'restart limit'):
                supervisor.check()
            self.assertIsNone(supervisor.server.poll())
        finally:
            dev.terminate_group(supervisor.server, grace=0)
        self.assertEqual(self.outbox(), [('worker.restart_limit', 'critical', 'worker_restart_limit', None)])

    def test_a_failed_start_emits_only_for_a_start_not_a_stop(self):
        self.assertIsNotNone(installation_runtime_start_failed(self.catalog))
        self.assertEqual(self.outbox(), [('installation.start_failed', 'critical', 'installation_failed', None)])
        self.assertIsNone(installation_runtime_start_failed(self.catalog, command='stop'))

    def test_a_refused_enqueue_rolls_back_the_installation_record(self):
        self.refuse_outbox()
        self.assertIsNone(dev.notify_installation('installation.started', catalog=self.catalog))
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.rows('SELECT count(*) FROM audit_events'), [(0,)])


def installation_runtime_start_failed(catalog, command='up'):
    import installation_runtime
    return installation_runtime.notify_start_failed(command, catalog=catalog)


class ExportProducerTests(ProducerCase):
    """lab/recovery-export.py. The state change is the archive and the fence record."""

    def setUp(self):
        super().setUp()
        export.NOTIFY_CATALOG = str(self.catalog)
        self.addCleanup(setattr, export, 'NOTIFY_CATALOG', None)

    def environment(self):
        return 'e_' + 'c' * 24

    def test_a_completed_export_commits_with_the_exporters_record(self):
        e = self.environment()
        self.assertIsNotNone(export.notify(
            'backup.export_completed', 'info', 'backup.export_completed|' + e,
            {'environment': e, 'runtime': e}, 'export_completed', {'phase': 'exported-and-fenced'}))
        self.assertEqual(self.outbox(), [('backup.export_completed', 'info', 'export_completed', e)])
        self.assertEqual(self.rows('SELECT action,subject FROM audit_events'), [('backup.export_completed', e)])

    def test_a_failed_export_is_recorded_only_from_a_retained_fence_record(self):
        e = self.environment()
        # No fence record exists, so there is no durable state change to report.
        self.assertIsNone(export.notify_export_failed(e, state=self.directory))
        self.assertEqual(self.outbox(), [])
        (self.directory / ('export-fence-' + e + '.json')).write_text('{}')
        self.assertIsNotNone(export.notify_export_failed(e, state=self.directory))
        self.assertEqual(self.outbox(), [('backup.export_failed', 'critical', 'export_failed', e)])
        # A failed export without a known environment is never guessed at.
        self.assertIsNone(export.notify_export_failed(None, state=self.directory))

    def test_a_refused_enqueue_rolls_back_the_export_record(self):
        self.refuse_outbox()
        self.assertIsNone(export.notify(
            'backup.export_completed', 'info', 'backup.export_completed|x',
            {'environment': self.environment()}, 'export_completed', {'phase': 'exported'}))
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.rows('SELECT count(*) FROM audit_events'), [(0,)])


class RestoreProducerTests(ProducerCase):
    """lab/recovery-restore-db.py. The state change is the target descriptor."""

    def setUp(self):
        super().setUp()
        restore.NOTIFY_CATALOG = str(self.catalog)
        self.addCleanup(setattr, restore, 'NOTIFY_CATALOG', None)
        self.environment = 'e_' + 'f' * 24
        self.saved = []

    def cleanup(self, docker):
        descriptor = {'status': 'database-verified', 'stage': 'verified', 'environment': self.environment}
        with patch.object(restore.lab, 'docker', docker), \
             patch.object(restore.runtime, 'atomic', lambda path, value: self.saved.append(dict(value))):
            restore.cleanup_target(descriptor, Path('unused'), 'helper', 'db', catalog=self.catalog)
        return descriptor

    def test_a_verified_restore_commits_with_the_restorers_record(self):
        self.assertIsNotNone(restore.notify(
            'restore.verified', 'critical', 'restore.verified|' + self.environment,
            {'environment': self.environment, 'runtime': self.environment},
            'restore_verified', {'status': 'database-verified'}))
        self.assertEqual(self.outbox(), [('restore.verified', 'critical', 'restore_verified', self.environment)])
        self.assertEqual(self.rows('SELECT action,subject FROM audit_events'),
                         [('restore.verified', self.environment)])

    def test_a_cleanup_failure_records_the_failure_and_never_changes_cleanup(self):
        def missing(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout='')
        with self.assertRaises(RuntimeError):
            self.cleanup(missing)
        self.assertEqual(self.saved[-1]['status'], 'cleanup-failed')
        self.assertEqual(self.outbox(), [('restore.failed', 'critical', 'restore_failed', self.environment)])

    def test_a_successful_cleanup_records_no_failure(self):
        def stopped(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps([{
                'Config': {'Labels': {'io.sbarbase.owner': 'recovery-target'}}, 'State': {'Running': False}}]))
        self.cleanup(stopped)
        self.assertEqual(self.saved[-1]['status'], 'database-restored')
        self.assertEqual(self.outbox(), [])

    def test_a_refused_enqueue_rolls_back_the_restore_record_and_still_cleans_up(self):
        self.refuse_outbox()

        def missing(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout='')
        with self.assertRaises(RuntimeError):
            self.cleanup(missing)
        self.assertEqual(self.saved[-1]['status'], 'cleanup-failed')
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.rows('SELECT count(*) FROM audit_events'), [(0,)])


class BoundaryTests(ProducerCase):
    """The two honest boundaries of the design, asserted directly."""

    def environment(self):
        return 'e_' + '9' * 24

    def test_every_producer_returns_none_and_raises_nothing_when_the_catalog_is_unreachable(self):
        e = self.environment()
        missing = self.directory / 'absent.sqlite'
        junk = self.directory / 'junk.sqlite'
        junk.write_text('not a database')
        calls = [
            ('fence.applied', {'phase': 'fenced'}),
            ('installation.started', {'stage': 'runtime'}),
            ('backup.export_completed', {'phase': 'exported'}),
            ('restore.verified', {'status': 'database-verified'}),
        ]
        for kind, detail in calls:
            for catalog in (missing, junk):
                with self.subTest(kind=kind, catalog=catalog.name):
                    self.assertIsNone(notification_producers.emit(
                        kind, 'critical', kind + '|' + e, {'environment': e},
                        'system:operator', notify_reason(kind), detail, catalog=catalog))
        # A missing catalog is never created as a side effect.
        self.assertFalse(missing.exists())

    def test_a_vocabulary_violation_is_a_refusal_not_an_exception(self):
        e = self.environment()
        self.assertIsNone(notification_producers.emit(
            'fence.applied', 'critical', 'fence.applied|' + e, {'environment': e},
            'system:operator', 'operator_request', {}, catalog=self.catalog))
        self.assertIsNone(notification_producers.emit(
            'fence.applied', 'critical', 'fence.applied|' + e, {'environment': e},
            'system:operator', 'not_a_reason', {'phase': 'fenced'}, catalog=self.catalog))
        self.assertIsNone(notification_producers.emit(
            'not.a.kind', 'critical', 'k', {'environment': e}, 'system:operator',
            'operator_request', {'phase': 'fenced'}, catalog=self.catalog))
        self.assertEqual(self.outbox(), [])

    def test_the_python_vocabulary_and_the_typescript_catalog_agree(self):
        text = (ROOT / 'src/control/catalog.ts').read_text()
        for kind in sorted(notify.KINDS):
            self.assertIn("'" + kind + "'", text, kind)
        for reason in sorted(notify.REASONS):
            self.assertIn("'" + reason + "'", text, reason)


def notify_reason(kind):
    return {
        'fence.applied': 'operator_request',
        'installation.started': 'operator_request',
        'backup.export_completed': 'export_completed',
        'restore.verified': 'restore_verified',
    }[kind]


if __name__ == '__main__':
    unittest.main()