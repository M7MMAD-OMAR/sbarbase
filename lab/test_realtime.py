"""Realtime per environment: its login, its superuser window, the placement and the console's apply."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import connection_budget
import durable_runtime
import realtime
import resource_policy

E = 'e_' + 'a' * 24


class PolicyTests(unittest.TestCase):
    def test_the_access_rule_admits_realtime_to_its_own_database_only(self):
        lines = durable_runtime.hba_content([E]).splitlines()
        self.assertIn(f'host {E} {E}_realtime 0.0.0.0/0 scram-sha-256', lines)
        self.assertFalse(any(line.startswith('host all ') and 'realtime' in line for line in lines))

    def test_each_environment_with_realtime_adds_its_own_row(self):
        base, cpus = resource_policy.start_placement(2)
        with_one, cpus_one = resource_policy.start_placement(2, 1)
        self.assertEqual(with_one - base, resource_policy.memory_mib(resource_policy.TIERS['production.realtime'].memory))
        self.assertGreater(cpus_one, cpus)
        self.assertEqual(resource_policy.start_placement(2, 0), (base, cpus))

    def test_the_database_limit_grows_only_while_studio_or_realtime_runs(self):
        self.assertEqual(connection_budget.database_limit(), connection_budget.ENVIRONMENT_LIMIT)
        self.assertEqual(connection_budget.database_limit(realtime=True),
                         connection_budget.ENVIRONMENT_LIMIT + connection_budget.REALTIME_CONNECTIONS)
        self.assertEqual(connection_budget.database_limit(studio=True, realtime=True),
                         connection_budget.ENVIRONMENT_LIMIT + connection_budget.STUDIO_CONNECTIONS + connection_budget.REALTIME_CONNECTIONS)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.statements = []
        self.runtime = object.__new__(durable_runtime.Runtime)
        self.runtime.values = {'environments': {E: {'jwt': 'j' * 64}}}
        self.runtime.path = root / 'runtime.json'
        self.runtime.pins = {'realtime': {'id': 'sha256:rt'}}
        self.runtime.sql = lambda query, database='postgres', check=True: self.statements.append(query) or SimpleNamespace(stdout='', returncode=0)
        for item in [patch.object(durable_runtime, 'STATE', root), patch.object(durable_runtime, 'inspect', return_value=None),
                     patch.object(durable_runtime, 'atomic')]:
            item.start()
            self.addCleanup(item.stop)

    def test_realtime_signs_in_with_its_own_login_and_its_own_slot_names(self):
        v = self.runtime.realtime_values(E)
        config = self.runtime.realtime_configuration(E, v)
        self.assertEqual(config['DB_USER'], f'{E}_realtime')
        self.assertEqual(config['DB_NAME'], E)
        self.assertEqual(config['SLOT_NAME_SUFFIX'], 'a' * 12)
        self.assertLessEqual(len('supabase_realtime_messages_replication_slot_' + config['SLOT_NAME_SUFFIX']), 63)
        self.assertEqual(len(config['DB_ENC_KEY']), 16)
        self.assertNotIn('supabase_admin', config.values())
        self.assertNotEqual(config['API_JWT_SECRET'], v['jwt'])

    def test_the_login_is_a_superuser_only_while_realtime_migrates(self):
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.5:4000'), \
             patch.object(durable_runtime, 'wait_ready'), patch.object(self.runtime, 'realtime_register') as register, \
             patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kw: self.statements.append(('docker', *args))) as docker:
            entry = self.runtime.realtime_start(E, migrate=True)
        register.assert_called_once()
        # Realtime restarts after the rights are gone, so no connection opened as a superuser survives.
        restarts = [i for i, s in enumerate(self.statements) if isinstance(s, tuple) and 'restart' in s]
        self.assertEqual(len(restarts), 1)
        docker.assert_called_once_with('restart', '-t', '10', f'sbarbase-durable-{E}-realtime')
        self.assertTrue(any(isinstance(s, str) and f'GRANT ALL ON ALL TABLES IN SCHEMA realtime TO {E}_realtime' in s for s in self.statements))
        granted = [i for i, s in enumerate(self.statements) if isinstance(s, str) and f'ALTER ROLE {E}_realtime SUPERUSER' in s]
        revoked = [i for i, s in enumerate(self.statements) if isinstance(s, str) and f'ALTER ROLE {E}_realtime NOSUPERUSER' in s]
        self.assertEqual((len(granted), len(revoked)), (1, 1))
        self.assertLess(granted[0], revoked[0])
        self.assertIn('pg_terminate_backend', self.statements[revoked[0]])
        self.assertLess(revoked[0], restarts[0])
        self.assertEqual(entry, {'url': 'http://10.0.0.5:4000', 'tenantHost': 'a' * 24 + '.realtime', 'migrated': 'sha256:rt'})
        created = next(s for s in self.statements if isinstance(s, str) and 'CREATE ROLE' in s and '_realtime' in s)
        self.assertNotRegex(created, r'CREATE ROLE \S+ [^;]*SUPERUSER')
        self.assertIn('NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', created)
        self.assertIn('WITH INHERIT FALSE, SET TRUE', created)
        self.assertIn(f'GRANT SET ON PARAMETER log_min_messages TO {E}_realtime', created)
        self.assertNotIn('ALTER SYSTEM', created)

    def test_a_failed_start_still_takes_the_superuser_away(self):
        with patch.object(self.runtime, 'launch', side_effect=RuntimeError('Docker operation run failed; exit 125')):
            with self.assertRaises(RuntimeError):
                self.runtime.realtime_start(E, migrate=True)
        self.assertIn(f'ALTER ROLE {E}_realtime NOSUPERUSER', self.statements[-1])

    def test_a_restart_on_the_same_image_never_grants_superuser(self):
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.5:4000'), \
             patch.object(durable_runtime, 'wait_ready'), patch.object(self.runtime, 'realtime_register') as register:
            self.runtime.realtime_start(E, migrate=False)
        register.assert_not_called()
        self.assertFalse(any('SUPERUSER' in s for s in self.statements))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalog = root / 'control.sqlite'
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute('CREATE TABLE realtime_settings(runtime TEXT PRIMARY KEY, desired TEXT, state TEXT, failure TEXT, actor TEXT, updated_at INTEGER)')
            database.execute("INSERT INTO realtime_settings VALUES (?, 'on', 'pending', NULL, 'alice', 0)", (E,))
        for item in [patch.object(realtime, 'CATALOG', self.catalog), patch.object(realtime, 'OPERATION_LOCK', root / 'operation.lock')]:
            item.start()
            self.addCleanup(item.stop)

    def row(self):
        with closing(sqlite3.connect(self.catalog)) as database:
            return database.execute('SELECT state, failure FROM realtime_settings').fetchone()

    def test_a_good_apply_records_realtime_on(self):
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(realtime.apply(E), 0)
        self.assertEqual(run.call_args[0][0][-2:], ['realtime', E])
        self.assertEqual(self.row(), ('on', None))

    def test_a_full_server_says_so(self):
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=75)):
            self.assertEqual(realtime.apply(E), 1)
        state, failure = self.row()
        self.assertEqual(state, 'failed')
        self.assertIn('room', failure)

    def test_turning_off_passes_off(self):
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute("UPDATE realtime_settings SET desired='off'")
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-1], '--off')
        self.assertEqual(self.row(), ('off', None))

    def test_a_busy_runtime_leaves_the_request_pending(self):
        with patch.object(realtime, 'busy', return_value=True), patch.object(realtime.subprocess, 'run') as run:
            self.assertEqual(realtime.apply(E), 75)
        run.assert_not_called()
        self.assertEqual(self.row()[0], 'pending')


if __name__ == '__main__':
    unittest.main()
