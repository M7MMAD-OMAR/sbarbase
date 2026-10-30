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


class RuntimeBase(unittest.TestCase):
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


class RuntimeTests(RuntimeBase):
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


class SleepWakeTests(RuntimeBase):
    """On demand mode's two halves in the runtime: a sleep removes Realtime and drops its slot but remembers the image it migrated with, and a wake
    starts it without the superuser window when that image is still the pinned one. Neither is ever started by anything else."""
    def setUp(self):
        super().setUp()
        self.written = []
        self.endpoints = {E: {'auth': {}, 'rest': {}, 'realtime': {'url': 'http://10.0.0.5:4000', 'tenantHost': 'a' * 24 + '.realtime', 'migrated': 'sha256:rt'}}}
        self.dockers = []
        self.runtime.values['environments'][E]['realtime'] = 'r' * 32
        answers = {'pg_hba_file_rules': '1', 'max_connections': '1000|10'}
        self.runtime.sql = lambda query, database='postgres', check=True: self.statements.append(query) or SimpleNamespace(
            stdout=next((value for key, value in answers.items() if key in query), ''), returncode=0)
        for item in [patch.object(durable_runtime, 'published_endpoints', side_effect=lambda: json.loads(json.dumps(self.endpoints))),
                     patch.object(durable_runtime.runtime_files, 'write_endpoints', side_effect=lambda value, state=None, writer=None: self.endpoints.update(value) or self.written.append(json.loads(json.dumps(value)))),
                     patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: {'Name': name}),
                     patch.object(durable_runtime.effect_receipt, 'require_settled'), patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False),
                     patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kw: (self.dockers.append(args), self.statements.append(('docker', *args)))),
                     patch.object(durable_runtime, 'restart_placement', return_value=(1, 1)),
                     patch.object(durable_runtime.resource_policy, 'restart_fits', return_value=True),
                     patch.object(durable_runtime, 'available_memory_bytes', return_value=2**40), patch.object(durable_runtime, 'owned_usage_bytes', return_value=0)]:
            item.start()
            self.addCleanup(item.stop)

    def test_a_sleep_removes_realtime_drops_its_slot_and_remembers_the_image(self):
        self.runtime.realtime_turn(E, on=False, sleep=True)
        self.assertIn(('rm', '-f', f'sbarbase-durable-{E}-realtime'), self.dockers)
        self.assertNotIn('realtime', self.endpoints[E])
        self.assertEqual(self.endpoints[E]['asleep'], {'realtime': {'migrated': 'sha256:rt'}})
        drops = [s for s in self.statements if isinstance(s, str) and 'pg_drop_replication_slot' in s]
        self.assertEqual(len(drops), 1)
        self.assertIn('NOT active', drops[0])
        # Only Realtime's own slots, never an application's.
        self.assertIn("slot_name LIKE 'supabase\\_realtime%'", drops[0])
        self.assertTrue(any(isinstance(s, str) and 'NOLOGIN' in s for s in self.statements))
        # The slot is dropped only after the container is gone and its sessions ended, or it would still be active.
        steps = ['rm' if isinstance(s, tuple) else 'login' if 'NOLOGIN' in s else 'end' if 'pg_terminate_backend(active_pid)' in s else 'drop' if 'pg_drop_replication_slot' in s
                 else 'verify' if 'count(*) FROM pg_replication_slots' in s else 'limit' for s in self.statements]
        self.assertEqual(steps, ['rm', 'login', 'end', 'drop', 'verify', 'limit'])

    def test_a_slot_that_will_not_go_is_reported_after_realtime_is_unpublished(self):
        self.runtime.sql = lambda query, database='postgres', check=True: self.statements.append(query) or SimpleNamespace(
            stdout='1' if 'count(*) FROM pg_replication_slots' in query else '', returncode=0)
        with patch.object(durable_runtime.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                self.runtime.realtime_turn(E, on=False, sleep=True)
        self.assertNotIn('realtime', self.endpoints[E])                # off and unpublished either way
        self.assertEqual(len([s for s in self.statements if isinstance(s, str) and 'count(*) FROM pg_replication_slots' in s]), 20)

    def test_a_manual_off_leaves_no_memory_of_the_image(self):
        self.runtime.realtime_turn(E, on=False)
        self.assertNotIn('asleep', self.endpoints[E])

    def test_a_wake_on_the_same_image_never_grants_superuser_and_publishes_realtime_again(self):
        self.runtime.realtime_turn(E, on=False, sleep=True)
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.6:4000'), \
             patch.object(durable_runtime, 'wait_ready'), patch.object(self.runtime, 'realtime_register') as register, \
             patch.object(self.runtime, 'realtime_database') as login:
            self.statements.clear()
            self.runtime.realtime_turn(E, on=True, wake=True)
        register.assert_not_called()
        login.assert_called_once()
        self.assertFalse(any('SUPERUSER' in s for s in self.statements if isinstance(s, str)))
        self.assertEqual(self.endpoints[E]['realtime']['url'], 'http://10.0.0.6:4000')
        self.assertNotIn('asleep', self.endpoints[E])

    def test_a_wake_after_an_image_change_migrates_again(self):
        self.runtime.realtime_turn(E, on=False, sleep=True)
        self.runtime.pins['realtime']['id'] = 'sha256:newer'
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.6:4000'), \
             patch.object(durable_runtime, 'wait_ready'), patch.object(self.runtime, 'realtime_register') as register, \
             patch.object(self.runtime, 'realtime_grants'), patch.object(self.runtime, 'realtime_database'):
            self.runtime.realtime_turn(E, on=True, wake=True)
        register.assert_called_once()

    def test_a_plain_on_always_migrates_and_a_wake_without_a_memory_does_too(self):
        self.endpoints[E].pop('realtime')
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.6:4000'), \
             patch.object(durable_runtime, 'wait_ready'), patch.object(self.runtime, 'realtime_register') as register, \
             patch.object(self.runtime, 'realtime_grants'), patch.object(self.runtime, 'realtime_database'):
            self.runtime.realtime_turn(E, on=True, wake=True)
            self.runtime.realtime_turn(E, on=True)
        self.assertEqual(register.call_count, 2)

    def test_a_wake_goes_through_the_same_admission_as_a_manual_start(self):
        self.runtime.realtime_turn(E, on=False, sleep=True)
        with patch.object(durable_runtime.resource_policy, 'restart_fits', return_value=False), patch.object(self.runtime, 'realtime_start') as start:
            with self.assertRaises(durable_runtime.AdmissionLimitError):
                self.runtime.realtime_turn(E, on=True, wake=True)
        start.assert_not_called()
        self.assertNotIn('realtime', self.endpoints[E])

    def test_a_functions_sleep_unpublishes_the_runtime_and_removes_its_network(self):
        self.endpoints[E]['functions'] = {'url': 'http://10.0.0.7:8081'}
        with patch.object(self.runtime, 'functions_network_remove') as network:
            self.runtime.functions_switch(E, False, f'sbarbase-durable-{E}-functions')
        network.assert_called_once_with(E)
        self.assertNotIn('functions', self.endpoints[E])
        self.assertEqual(durable_runtime.functions_count(), 0)
        self.assertFalse(durable_runtime.functions_on(E))

    def test_only_the_published_endpoints_start_anything_so_a_sleeping_service_is_never_started_by_a_restart_an_update_a_backup_or_a_restore(self):
        source = Path(durable_runtime.__file__).read_text()
        # A start brings back what the endpoints named, and nothing else.
        self.assertIn("previous = all_endpoints.get(e, {}).get('realtime')\n        if previous and not creating:", source)
        self.assertIn("if all_endpoints.get(e, {}).get('functions') and not creating:", source)
        root = Path(durable_runtime.__file__).parent
        # The side by side replacement of an update never starts either service.
        self.assertNotRegex((root / 'rolling.py').read_text(), r'realtime_start|functions_start|realtime_turn|functions_turn')
        # A backup and a restore name a container only while its endpoint is published.
        backup = (root / 'backup.py').read_text()
        self.assertIn("if published().get(e, {}).get('realtime'):", backup)
        self.assertIn("if service == 'realtime' and not endpoints[e].get('realtime'):", backup)
        self.assertNotRegex(backup, r'realtime_start|functions_start|realtime_turn|functions_turn')
        # Ending a write pause starts Functions only for an environment whose endpoint is published.
        pause = (root / 'write_pause.py').read_text()
        self.assertIn("if functions and endpoints.get(e, {}).get('functions'):", pause)
        self.assertIn("elif published.get(e, {}).get('functions'):", pause)

    def test_nothing_that_starts_with_the_environment_names_a_sleeping_realtime(self):
        self.runtime.realtime_turn(E, on=False, sleep=True)
        self.assertEqual(durable_runtime.realtime_count(), 0)
        self.assertFalse(durable_runtime.realtime_on(E))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalog = root / 'control.sqlite'
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute('CREATE TABLE realtime_settings(runtime TEXT PRIMARY KEY, desired TEXT, state TEXT, failure TEXT, actor TEXT, updated_at INTEGER)')
            database.execute("INSERT INTO realtime_settings VALUES (?, 'on', 'pending', NULL, 'alice', 0)", (E,))
            database.execute('CREATE TABLE provision_jobs(runtime TEXT PRIMARY KEY, environment TEXT)')
            database.execute("INSERT INTO provision_jobs VALUES (?, 'env-1')", (E,))
            database.execute('CREATE TABLE audit_events(actor TEXT, action TEXT, subject TEXT, detail TEXT, at INTEGER)')
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

    def test_a_lock_taken_after_the_check_leaves_the_request_pending(self):
        # With one child per environment, two can pass busy() together; the one that then finds the
        # operation lock held is asked again rather than reported as failed.
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=realtime.BUSY)):
            self.assertEqual(realtime.apply(E), 75)
        self.assertEqual(self.row(), ('pending', None))

    def test_the_busy_status_is_the_runtime_one_and_not_the_capacity_one(self):
        import auth_settings
        import durable_runtime
        self.assertEqual((realtime.BUSY, auth_settings.BUSY), (durable_runtime.BUSY, durable_runtime.BUSY))
        self.assertNotEqual(durable_runtime.BUSY, realtime.CAPACITY)
        self.assertEqual(set(durable_runtime.CONSOLE_CHANGES), set(realtime.SERVICES) | {'auth'})
        source = Path(durable_runtime.__file__).read_text()
        self.assertIn('        if args.command in CONSOLE_CHANGES:raise SystemExit(BUSY)\n', source)

    def test_turning_off_passes_off(self):
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute("UPDATE realtime_settings SET desired='off'")
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-1], '--off')
        self.assertEqual(self.row(), ('off', None))

    def request(self, actor, desired):
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute('UPDATE realtime_settings SET actor=?, desired=?', (actor, desired))

    def events(self):
        with closing(sqlite3.connect(self.catalog)) as database:
            return [(actor, action, subject, json.loads(detail)) for actor, action, subject, detail in database.execute('SELECT actor, action, subject, detail FROM audit_events')]

    def test_a_wake_asks_the_runtime_for_the_fast_start_and_a_sleep_for_the_remembered_stop(self):
        self.request('system:wake', 'on')
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-3:], ['realtime', E, '--wake'])
        self.request('system:kill-switch', 'on')
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-1], '--wake')
        self.request('system:idle', 'off')
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-2:], ['--off', '--sleep'])
        # A person's own switch never carries either flag.
        self.request('alice', 'on')
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-2:], ['realtime', E])
        self.request('alice', 'off')
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            realtime.apply(E)
        self.assertEqual(run.call_args[0][0][-1], '--off')

    def test_what_the_supervisor_did_is_an_audit_event_with_its_reason(self):
        for actor, desired, reason in (('system:wake', 'on', 'wake'), ('system:idle', 'off', 'idle'), ('system:kill-switch', 'on', 'kill_switch'), ('alice', 'on', 'user')):
            self.request(actor, desired)
            with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                realtime.apply(E)
        events = self.events()
        self.assertEqual([(e[0], e[1], e[2], e[3]['reason'], e[3]['state']) for e in events],
                         [('system:supervisor', 'realtime.applied', 'env-1', 'wake', 'on'), ('system:supervisor', 'realtime.applied', 'env-1', 'idle', 'off'),
                          ('system:supervisor', 'realtime.applied', 'env-1', 'kill_switch', 'on'), ('system:supervisor', 'realtime.applied', 'env-1', 'user', 'on')])

    def test_a_wake_that_the_server_cannot_hold_is_a_capacity_event_and_says_so(self):
        self.request('system:wake', 'on')
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=75)):
            self.assertEqual(realtime.apply(E), 1)
        self.assertEqual(self.events()[0][1:], ('realtime.apply_failed', 'env-1', {'want': 'on', 'reason': 'capacity', 'seconds': self.events()[0][3]['seconds']}))
        self.assertEqual(self.row()[0], 'failed')

    def test_a_busy_runtime_leaves_the_request_pending(self):
        with patch.object(realtime, 'busy', return_value=True), patch.object(realtime.subprocess, 'run') as run:
            self.assertEqual(realtime.apply(E), 75)
        run.assert_not_called()
        self.assertEqual(self.row()[0], 'pending')


if __name__ == '__main__':
    unittest.main()
