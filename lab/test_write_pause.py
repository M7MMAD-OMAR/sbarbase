"""Operator write pause: the record comes first, then routing, Studio and the logins close, and the
writers restart to cut off what was in flight; resume follows the access setting, never a snapshot."""
import contextlib
import fcntl
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import durable_runtime
import studio
import upgrade_health
import write_pause

E = 'e_' + 'a' * 24


DEVELOPER_ON = write_pause.developer_on


class Database:
    """The developer and Studio logins and their sessions, as SQL sees them."""

    def __init__(self, logins, sessions=1, stubborn=False):
        self.logins, self.sessions, self.stubborn, self.queries = dict(logins), sessions, stubborn, []
        self.passwords = {}

    def __call__(self, query):
        self.queries.append(query)
        if query.startswith('DO $$ BEGIN'):
            for name, state, password in re.findall(r"ALTER ROLE (\w+) (NOLOGIN|LOGIN)(?: PASSWORD '([\w-]+)')?;", query):
                if name in self.logins:
                    self.logins[name] = state == 'LOGIN'
                    if password:
                        self.passwords[name] = password
            return ''
        if 'pg_terminate_backend' in query:
            if not self.stubborn:
                self.sessions = 0
            return str(self.sessions)
        if query.startswith('SELECT count(*) FROM pg_stat_activity'):
            return str(self.sessions)
        if query.startswith('SELECT count(*) FROM pg_roles'):
            return str(sum(self.logins.values()))
        raise AssertionError('Unexpected SQL: ' + query)


class Routing:
    def __init__(self, maintenance=False, placement=None):
        self.row = {'revision': 4, 'maintenance': maintenance, 'placement': placement}
        self.calls = []

    def __call__(self, value):
        self.calls.append(value['action'])
        if value['action'] == 'read':
            return {E: dict(self.row)}
        assert value['revision'] == self.row['revision'], 'stale revision'
        self.row = dict(self.row, revision=self.row['revision'] + 1, maintenance=value['action'] == 'pause')
        return {'revision': self.row['revision']}


class Writers:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    def stop_studio(self, e):
        self.log.append(('studio down', e))

    def restart(self, e, endpoints):
        self.log.append(('restart', e, bool(endpoints[e].get('functions'))))
        if self.fail:
            raise write_pause.PauseError('injected')

    def start(self, e, endpoints):
        self.log.append(('start', e))

    @contextlib.contextmanager
    def backup_lock(self):
        self.log.append(('backup lock', True))
        yield

    class backup:
        PREFIX = 'sbarbase-durable'

    def start_functions(self, e):
        self.log.append(('start functions', e))


class PauseFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.endpoints = {E: {'auth': 'http://10.0.0.3:9999', 'rest': 'http://10.0.0.4:3000'}}
        self.log = []
        self.turned = []
        test = self

        # The runtime's developer_on, as resume calls it: the login comes on with its saved password.
        patcher = patch.object(write_pause, 'developer_on', test.turned.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pause(self, database, routing, endpoints=None, fail=False):
        return write_pause.pause(E, state=self.state, query=database, route=routing, endpoints=endpoints or self.endpoints,
                                 writers=Writers(self.log, fail), clock=lambda: 1000.0, sleep=self.log_sleep)

    def log_sleep(self, seconds):
        self.log.append(('wait', seconds))


class PauseTests(PauseFixture):
    def test_the_record_comes_first_then_routing_studio_logins_and_a_writer_restart(self):
        database = Database({E + '_developer': True, E + '_studio': True})
        routing = Routing()
        order = []
        original = write_pause.write_record

        def recorded(path, value):
            order.append((value['phase'], list(routing.calls), list(self.log)))
            original(path, value)
        with patch.object(write_pause, 'write_record', recorded):
            record = self.pause(database, routing)
        # Written before routing changed, and held only after the writers restarted.
        self.assertEqual(order[0], ('pausing', ['read'], []))
        self.assertEqual(order[-1][0], 'held')
        # The gateway's body read window passes between the routing pause and the restart.
        self.assertEqual(order[-1][2], [('studio down', E), ('wait', write_pause.GATEWAY_WINDOW), ('restart', E, False)])
        self.assertEqual(routing.calls, ['read', 'pause'])
        self.assertTrue(routing.row['maintenance'])
        self.assertEqual(database.logins, {E + '_developer': False, E + '_studio': False})
        self.assertEqual(database.sessions, 0)
        self.assertEqual((record['phase'], record['revision']), ('held', 5))
        self.assertTrue(write_pause.is_paused(E, self.state))
        self.assertEqual([item['runtime'] for item in write_pause.status(self.state)], [E])

    def test_a_writer_restart_that_fails_leaves_the_pause_unheld_and_a_rerun_finishes_it(self):
        routing = Routing()
        with self.assertRaises(write_pause.PauseError):
            self.pause(Database({E + '_developer': True}), routing, fail=True)
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'pausing')
        self.pause(Database({E + '_developer': True}), routing)
        self.assertEqual(routing.calls, ['read', 'pause', 'read'], 'routing is paused once')
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'held')
        # The rerun kept the routing it confirmed, so resume releases it.
        write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=routing, direct=False,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertFalse(routing.row['maintenance'])
        self.assertFalse(write_pause.is_paused(E, self.state))

    def test_sessions_that_do_not_end_or_a_login_left_open_stop_before_held(self):
        with self.assertRaisesRegex(write_pause.PauseError, 'run pause again'):
            write_pause.close_logins(E, Database({E + '_developer': True}, stubborn=True), sleep=lambda _: None, drain=0)

        class Reopened(Database):
            def __call__(self, query):
                if query.startswith('SELECT count(*) FROM pg_roles'):
                    return '1'
                return super().__call__(query)
        with self.assertRaisesRegex(write_pause.PauseError, 'can still log in'):
            write_pause.close_logins(E, Reopened({E + '_developer': True}))

    def test_closing_the_logins_ends_exactly_their_sessions(self):
        database = Database({E + '_developer': True, E + '_studio': True})
        write_pause.close_logins(E, database, sleep=lambda _: None)
        logins = f"'{E}_developer','{E}_studio'"
        self.assertEqual(database.queries[1:3], [
            f'SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename IN ({logins});',
            f'SELECT count(*) FROM pg_stat_activity WHERE usename IN ({logins});'])

    def test_another_operations_maintenance_or_placement_is_never_taken_over(self):
        for routing, reason in ((Routing(maintenance=True), 'another operation'),
                                (Routing(placement={'kind': 'target'}), 'moved to another placement')):
            with self.subTest(row=routing.row), self.assertRaisesRegex(write_pause.PauseError, reason):
                self.pause(Database({}), routing)
            self.assertEqual(routing.calls, ['read'])
            self.assertFalse(write_pause.is_paused(E, self.state))
        with self.assertRaisesRegex(write_pause.PauseError, 'published'):
            self.pause(Database({}), Routing(), endpoints={'e_' + 'b' * 24: {}})
        self.assertEqual(self.log, [])

    def test_resume_follows_the_access_setting_and_leaves_studio_closed(self):
        for direct, developer in ((True, True), (False, False)):
            with self.subTest(direct=direct):
                database = Database({E + '_developer': True, E + '_studio': True})
                routing = Routing()
                self.pause(database, routing)
                self.turned.clear()
                write_pause.resume(E, state=self.state, query=database, route=routing, direct=direct, endpoints=self.endpoints, writers=Writers(self.log))
                # Access turned off during the pause stays off; Studio opens only when started.
                self.assertEqual(self.turned, [E] if developer else [])
                self.assertEqual(database.logins, {E + '_developer': False, E + '_studio': False})
                self.assertEqual(routing.calls[-2:], ['read', 'resume'])
                self.assertFalse(routing.row['maintenance'])
                self.assertFalse(write_pause.is_paused(E, self.state))
        with self.assertRaisesRegex(write_pause.PauseError, 'not paused'):
            write_pause.resume(E, state=self.state, query=database, route=routing, direct=True, endpoints=self.endpoints)

    def test_resume_turns_access_on_through_the_runtime_step_with_the_saved_password(self):
        database = Database({E + '_developer': True})
        routing = Routing()
        self.pause(database, routing)
        turned = []
        write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                           endpoints=self.endpoints, writers=Writers(self.log), access=turned.append)
        self.assertEqual(turned, [E])
        # No login SQL of its own: the runtime's developer_on applies password, grants and limit.
        self.assertFalse(any('LOGIN' in query and 'NOLOGIN' not in query for query in database.queries))

    def test_resume_uses_the_runtime_developer_on_by_default(self):
        calls = []

        class Runtime:
            def developer_on(self, e):
                calls.append(e)
        with patch.object(write_pause.runtime, 'Runtime', Runtime):
            DEVELOPER_ON(E)
        self.assertEqual(calls, [E])
        database = Database({E + '_developer': True})
        routing = Routing()
        self.pause(database, routing)
        write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertEqual(self.turned, [E])

    def test_a_resume_that_must_start_the_writers_holds_the_backup_lock(self):
        routing = Routing()
        with self.assertRaises(write_pause.PauseError):
            self.pause(Database({}), routing, fail=True)
        self.log.clear()
        write_pause.resume(E, state=self.state, query=Database({}), route=routing, direct=False,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertEqual(self.log[:2], [('backup lock', True), ('start', E)])

        class Busy(Writers):
            @contextlib.contextmanager
            def backup_lock(self):
                raise write_pause.PauseError('A backup or restore is running; try again when it ends')
                yield
        with self.assertRaises(write_pause.PauseError):
            self.pause(Database({}), routing, fail=True)
        with self.assertRaisesRegex(write_pause.PauseError, 'backup or restore is running.*run resume again'):
            write_pause.resume(E, state=self.state, query=Database({}), route=routing, direct=False,
                               endpoints=self.endpoints, writers=Busy(self.log))
        self.assertTrue(write_pause.is_paused(E, self.state))

    def test_console_requests_to_turn_things_on_wait_for_resume_without_holding_others(self):
        import sqlite3
        import threading
        import dev
        other = 'e_' + 'b' * 24
        catalog = self.state / 'control.sqlite'
        with sqlite3.connect(catalog) as database:
            for table in ('database_access', 'functions_settings', 'realtime_settings'):
                database.execute(f'CREATE TABLE {table}(runtime TEXT, desired TEXT, state TEXT, updated_at INTEGER)')
                database.executemany(f'INSERT INTO {table} VALUES (?, ?, ?, ?)',
                                     [(E, 'on', 'pending', 1), (other, 'on', 'pending', 2)])
            database.execute("INSERT INTO database_access VALUES ('e_" + 'c' * 24 + "', 'off', 'pending', 0)")
        (self.state / 'write-pauses').mkdir()
        (self.state / 'write-pauses' / f'{E}.json').write_text('{}')
        supervisor = dev.Supervisor.__new__(dev.Supervisor)
        with patch.object(dev, 'STATE', self.state):
            self.assertEqual(supervisor.toggle_requests('database'), ['e_' + 'c' * 24, other])
            self.assertEqual(supervisor.toggle_requests('functions'), [other])
            # Realtime is not refused by the pause, so its request is not held back.
            self.assertEqual(supervisor.toggle_requests('realtime'), [E, other])
            (self.state / 'write-pauses' / f'{E}.json').unlink()
            self.assertEqual(supervisor.toggle_requests('database'), ['e_' + 'c' * 24, E, other])

    def test_a_malformed_record_or_name_is_refused_not_guessed(self):
        with self.assertRaises(write_pause.PauseError):
            write_pause.record_path('postgres;DROP', self.state)
        path = write_pause.record_path(E, self.state)
        path.parent.mkdir(parents=True)
        for value in ('{broken', json.dumps({'version': 1, 'runtime': E, 'phase': 'waiting'}),
                      json.dumps({'version': 2, 'runtime': E, 'phase': 'held'}),
                      json.dumps({'version': 1, 'runtime': 'e_' + 'b' * 24, 'phase': 'held'})):
            path.write_text(value)
            with self.subTest(value=value), self.assertRaisesRegex(write_pause.PauseError, 'review'):
                write_pause.is_paused(E, self.state)

    def test_a_login_cannot_be_opened_while_a_pause_holds_or_is_being_taken(self):
        self.pause(Database({E + '_developer': True}), Routing())
        with self.assertRaisesRegex(write_pause.PauseError, 'paused for writes'):
            with write_pause.unpaused(E, 'open Studio', self.state):
                self.fail('the body must not run')
        write_pause.record_path(E, self.state).unlink()
        # While another process holds the pause lock (a pause being taken), opening waits, then gives up.
        with (self.state / 'write-pause.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch.object(write_pause, 'LOCK_WAIT', 0), self.assertRaisesRegex(write_pause.PauseError, 'a Studio start holds the pause lock'):
                with write_pause.pause_lock(self.state, wait=0):
                    pass
        with write_pause.unpaused(E, 'open Studio', self.state):
            pass

    def test_studio_and_direct_database_access_go_through_the_pause_guard(self):
        self.pause(Database({E + '_developer': True}), Routing())
        with patch.object(write_pause.runtime, 'STATE', self.state):
            (self.state / 'endpoints.json').write_text(json.dumps(self.endpoints))
            with patch.object(studio.source_fence, 'is_fenced', return_value=False), \
                    self.assertRaisesRegex(studio.StudioError, 'paused for writes'):
                studio.up(E)

            class Runtime(durable_runtime.Runtime):
                def __init__(self):
                    self.values = {'environments': {E: {}}}

                def sql(self, query, database='postgres'):
                    raise AssertionError('no statement may run while paused: ' + query)
            with patch.object(durable_runtime, 'inspect', return_value={'Id': 'x'}), \
                    patch.object(durable_runtime.effect_receipt, 'require_settled'), \
                    patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False), \
                    self.assertRaises(durable_runtime.OperationBusy):
                durable_runtime.database_command(E, True, runtime=Runtime())
            with patch.object(durable_runtime, 'inspect', return_value={'Id': 'x'}), \
                    patch.object(durable_runtime.effect_receipt, 'require_settled'), \
                    patch.object(Runtime, 'functions_switch', side_effect=AssertionError('must not start')), \
                    self.assertRaisesRegex(RuntimeError, 'paused for writes'):
                Runtime().functions_turn(E, True)


class RecoveryTests(PauseFixture):
    def test_a_pause_that_stopped_midway_is_resumed_with_its_writers_started_again(self):
        routing = Routing()
        with self.assertRaises(write_pause.PauseError):
            self.pause(Database({E + '_developer': True}), routing, fail=True)
        self.log.clear()
        write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=routing,
                           direct=False, endpoints=self.endpoints, writers=Writers(self.log))
        self.assertEqual(self.log, [('backup lock', True), ('start', E)])
        self.assertFalse(write_pause.is_paused(E, self.state))

    def test_a_resume_whose_routing_fails_keeps_the_pause_recorded(self):
        routing = Routing()
        self.pause(Database({E + '_developer': True}), routing)

        def broken(value):
            if value['action'] == 'resume':
                raise RuntimeError('catalog locked')
            return routing(value)
        with self.assertRaisesRegex(write_pause.PauseError, 'run resume again'):
            write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=broken,
                               direct=True, endpoints=self.endpoints, writers=Writers(self.log))
        self.assertTrue(write_pause.is_paused(E, self.state))

    def test_a_resume_that_fails_after_routing_reopens_never_shows_held(self):
        routing = Routing()
        self.pause(Database({E + '_developer': True}), routing)

        def broken(e):
            raise RuntimeError('A database statement failed')
        with self.assertRaisesRegex(write_pause.PauseError, 'run resume again'):
            write_pause.resume(E, state=self.state, query=Database({}), route=routing, direct=True,
                               endpoints=self.endpoints, writers=Writers(self.log), access=broken)
        self.assertFalse(routing.row['maintenance'])
        self.assertEqual([item['phase'] for item in write_pause.status(self.state)], ['resuming'])
        # Run again: the writers start again (safe to repeat) and the pause is released.
        write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=routing, direct=True,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertFalse(write_pause.is_paused(E, self.state))

    def test_every_failure_after_the_record_says_it_is_recorded(self):
        class Refusing(Routing):
            def __call__(self, value):
                if value['action'] == 'pause':
                    raise write_pause.PauseError('The routing operator refused the request')
                return super().__call__(value)
        with self.assertRaisesRegex(write_pause.PauseError, 'refused the request; the pause is recorded.*does not hold yet; run pause again, or resume'):
            self.pause(Database({}), Refusing())
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'pausing')
        # A writer failure with its own wording gets the same notice.
        with self.assertRaisesRegex(write_pause.PauseError, '^injected; the pause is recorded'):
            self.pause(Database({}), Routing(), fail=True)

    def test_resume_without_a_given_setting_reads_the_environment_access_setting(self):
        for direct, developer in ((True, True), (False, False)):
            with self.subTest(direct=direct):
                database = Database({E + '_developer': True})
                routing = Routing()
                self.pause(database, routing)
                with patch.object(write_pause.runtime, 'direct_on', return_value=direct) as setting:
                    write_pause.resume(E, state=self.state, query=database, route=routing, endpoints=self.endpoints,
                                       writers=Writers(self.log))
                setting.assert_called_once_with(E)
                self.assertEqual(self.turned[-1:] == [E], developer)
                self.turned.clear()

    def test_the_wait_counts_from_when_routing_paused_on_a_retry(self):
        class RefusingOnce(Routing):
            refused = False

            def __call__(self, value):
                if value['action'] == 'pause' and not self.refused:
                    self.refused = True
                    raise write_pause.PauseError('The routing operator refused the request')
                return super().__call__(value)
        routing = RefusingOnce()
        with self.assertRaises(write_pause.PauseError):
            self.pause(Database({}), routing)
        # The retry, later, routes; the gateway window starts then, not when the record was written.
        write_pause.pause(E, state=self.state, query=Database({}), route=routing, endpoints=self.endpoints,
                          writers=Writers(self.log), clock=lambda: 1100.0, sleep=self.log_sleep)
        self.assertIn(('wait', write_pause.GATEWAY_WINDOW), self.log)

    def test_the_gateway_window_outlasts_the_gateway_body_read(self):
        import re
        root = Path(write_pause.__file__).resolve().parent.parent
        handler = (root / 'src/gateway/handler.ts').read_text()
        managed = (root / 'src/gateway/managed.ts').read_text()
        default = int(re.search(r'bodyReadTimeoutMs=([\d_]+)', handler)[1].replace('_', ''))
        used = int(re.search(r"keys.resolve\(environment,key\):null,([\d_]+),concurrency", managed)[1].replace('_', ''))
        # A margin past the deadline, for the hop from the gateway to the service.
        self.assertGreaterEqual(write_pause.GATEWAY_WINDOW * 1000, max(default, used) + 3000)

    def test_a_routing_change_whose_answer_was_lost_is_read_back_and_kept(self):
        class LostAnswer(Routing):
            def __call__(self, value):
                answer = super().__call__(value)
                if value['action'] == 'pause':
                    raise subprocess.TimeoutExpired('bun', 30)
                return answer
        routing = LostAnswer()
        database = Database({E + '_developer': True})
        record = write_pause.pause(E, state=self.state, query=database, route=routing, endpoints=self.endpoints,
                                   writers=Writers(self.log), clock=lambda: 5000.0, sleep=self.log_sleep)
        # Read back under the operation lock: it happened, so it is this pause's, and since when is
        # unknown the whole gateway window is waited.
        self.assertEqual((record['phase'], record['revision']), ('held', 5))
        self.assertNotIn('routed_at', record)
        self.assertIn(('wait', write_pause.GATEWAY_WINDOW), self.log)
        write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertEqual(routing.calls.count('pause'), 1)
        self.assertFalse(routing.row['maintenance'])

    def test_a_routing_change_that_did_not_happen_never_claims_the_next_one(self):
        class Refusing(Routing):
            def __call__(self, value):
                if value['action'] == 'pause':
                    raise subprocess.TimeoutExpired('bun', 30)
                return super().__call__(value)
        routing = Refusing()
        database = Database({E + '_developer': True})
        with self.assertRaises(write_pause.PauseError):
            self.pause(database, routing)
        self.assertNotIn('revision', write_pause.read_record(E, self.state))
        # Another operation then pauses routing from the same revision, to the same next number.
        routing.row = dict(routing.row, revision=routing.row['revision'] + 1, maintenance=True)
        with self.assertRaisesRegex(write_pause.PauseError, 'another operation'):
            write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                               endpoints=self.endpoints, writers=Writers(self.log))
        with self.assertRaisesRegex(write_pause.PauseError, 'another operation'):
            self.pause(database, routing)
        self.assertTrue(routing.row['maintenance'])
        self.assertEqual(routing.row['revision'], 5)

    def test_a_routing_change_that_cannot_be_read_back_waits_for_the_operator(self):
        class Silent(Routing):
            silent = True

            def __call__(self, value):
                if self.silent and value['action'] in ('pause', 'read') and 'pause' in self.calls:
                    self.calls.append(value['action'])
                    raise subprocess.TimeoutExpired('bun', 30)
                if value['action'] == 'pause':
                    super().__call__(value)
                    raise subprocess.TimeoutExpired('bun', 30)
                return super().__call__(value)
        routing = Silent()
        database = Database({E + '_developer': True})
        with self.assertRaises(write_pause.PauseError):
            self.pause(database, routing)
        self.assertEqual(write_pause.read_record(E, self.state)['routing_intent'], 5)
        routing.silent = False
        for run in (lambda: self.pause(database, routing),
                    lambda: write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                                               endpoints=self.endpoints, writers=Writers(self.log))):
            with self.assertRaisesRegex(write_pause.PauseError, 'adopt-maintenance'):
                run()
        self.assertTrue(routing.row['maintenance'])
        # The operator checked no other operation holds it, and settles it.
        write_pause.adopt(E, state=self.state, route=routing)
        self.assertEqual(self.pause(database, routing)['phase'], 'held')
        self.assertEqual(routing.calls.count('pause'), 1)

    def killed_during_routing(self, committed):
        """The record a pause leaves when it is killed while its routing call runs."""
        routing = Routing()
        if committed:
            routing.row = dict(routing.row, revision=5, maintenance=True)
        write_pause.write_record(write_pause.record_path(E, self.state),
                                 {'version': 1, 'runtime': E, 'phase': 'pausing', 'paused_at': 900.0, 'routing_intent': 5})
        return routing

    def test_a_kill_before_routing_changed_settles_itself_on_the_rerun(self):
        routing = self.killed_during_routing(committed=False)
        record = self.pause(Database({E + '_developer': True}), routing)
        self.assertEqual((record['phase'], record['revision']), ('held', 5))
        self.assertNotIn('routing_intent', record)
        self.assertEqual(routing.calls, ['read', 'pause'])
        # Resume settles it the same way.
        routing = self.killed_during_routing(committed=False)
        write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=routing, direct=False,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertFalse(write_pause.is_paused(E, self.state))
        self.assertEqual(routing.calls, ['read'])

    def test_a_failed_resume_still_drops_an_intent_that_never_changed_routing(self):
        routing = self.killed_during_routing(committed=False)

        class Failing(Writers):
            def start(self, e, endpoints):
                raise RuntimeError('injected')
        with self.assertRaisesRegex(write_pause.PauseError, 'run resume again'):
            write_pause.resume(E, state=self.state, query=Database({}), route=routing, direct=False,
                               endpoints=self.endpoints, writers=Failing(self.log))
        self.assertNotIn('routing_intent', write_pause.read_record(E, self.state))
        # Another operation then pauses routing to the number the intent had: it is never claimed.
        routing.row = dict(routing.row, revision=5, maintenance=True)
        with self.assertRaisesRegex(write_pause.PauseError, 'another operation'):
            self.pause(Database({}), routing)

    def test_a_kill_after_routing_changed_waits_for_the_operator(self):
        routing = self.killed_during_routing(committed=True)
        database = Database({E + '_developer': True})
        for run in (lambda: self.pause(database, routing),
                    lambda: write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                                               endpoints=self.endpoints, writers=Writers(self.log))):
            with self.assertRaisesRegex(write_pause.PauseError, 'stopped before it could tell.*adopt-maintenance'):
                run()
        self.assertTrue(routing.row['maintenance'])
        write_pause.adopt(E, state=self.state, route=routing)
        self.assertEqual(self.pause(database, routing)['phase'], 'held')
        self.assertNotIn('pause', routing.calls)

    def test_an_old_routing_time_never_shortens_the_wait_after_routing_changes_again(self):
        class LostAnswer(Routing):
            def __call__(self, value):
                answer = super().__call__(value)
                if value['action'] == 'pause':
                    raise subprocess.TimeoutExpired('bun', 30)
                return answer
        # Left by an earlier pause whose routing someone resumed since.
        write_pause.write_record(write_pause.record_path(E, self.state),
                                 {'version': 1, 'runtime': E, 'phase': 'pausing', 'paused_at': 900.0,
                                  'revision': 3, 'routed_at': 4990.0})
        write_pause.pause(E, state=self.state, query=Database({}), route=LostAnswer(), endpoints=self.endpoints,
                          writers=Writers(self.log), clock=lambda: 5000.0, sleep=self.log_sleep)
        self.assertIn(('wait', write_pause.GATEWAY_WINDOW), self.log)

    def test_a_killed_resume_of_a_pause_that_never_held_starts_the_services_again(self):
        routing = Routing()
        with self.assertRaises(write_pause.PauseError):
            self.pause(Database({}), routing, fail=True)
        record = write_pause.read_record(E, self.state)
        write_pause.write_record(write_pause.record_path(E, self.state), {**record, 'phase': 'resuming'})
        write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=routing, direct=False,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertIn(('start', E), self.log)
        self.assertFalse(write_pause.is_paused(E, self.state))

    def test_settling_never_claims_maintenance_at_another_revision(self):
        routing = self.killed_during_routing(committed=True)
        # Another operation resumed it and took it again since: the revision moved past the intent.
        routing.row = dict(routing.row, revision=7, maintenance=True)
        write_pause.adopt(E, state=self.state, route=routing)
        record = write_pause.read_record(E, self.state)
        self.assertNotIn('revision', record)
        self.assertNotIn('routing_intent', record)
        with self.assertRaisesRegex(write_pause.PauseError, 'another operation'):
            self.pause(Database({}), routing)
        self.assertEqual((routing.row['revision'], routing.row['maintenance']), (7, True))

    def test_pausing_again_while_held_stops_nothing(self):
        routing = Routing()
        database = Database({E + '_developer': True})
        self.pause(database, routing)
        self.log.clear()
        record = self.pause(database, routing, fail=True)
        self.assertEqual(record['phase'], 'held')
        self.assertEqual(self.log, [])

    def test_a_rerun_that_stops_partway_is_never_left_marked_held(self):
        routing = Routing()
        database = Database({E + '_developer': True})
        self.pause(database, routing)
        # Someone resumed routing by hand; the rerun takes it again and restarts the writers, and fails.
        routing.row = dict(routing.row, revision=routing.row['revision'] + 1, maintenance=False)
        with self.assertRaises(write_pause.PauseError):
            self.pause(database, routing, fail=True)
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'pausing')
        self.log.clear()
        write_pause.resume(E, state=self.state, query=database, route=routing, direct=False,
                           endpoints=self.endpoints, writers=Writers(self.log))
        # Resume starts what the failed rerun stopped.
        self.assertIn(('start', E), self.log)

    def test_a_pause_run_over_a_killed_resume_starts_from_pausing(self):
        routing = Routing()
        database = Database({E + '_developer': True})
        self.pause(database, routing)
        record = write_pause.read_record(E, self.state)
        write_pause.write_record(write_pause.record_path(E, self.state), {**record, 'phase': 'resuming'})
        with self.assertRaises(write_pause.PauseError):
            self.pause(database, routing, fail=True)
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'pausing')

    def test_a_shared_restore_helper_failure_is_reported_as_a_restart(self):
        class Failing(Writers):
            def restart(self, e, endpoints):
                raise RuntimeError('Auth did not come back after the restore')
        with self.assertRaises(write_pause.PauseError) as raised:
            write_pause.pause(E, state=self.state, query=Database({}), route=Routing(), endpoints=self.endpoints,
                              writers=Failing(self.log), clock=lambda: 1000.0, sleep=lambda _: None)
        self.assertIn('Auth did not come back after the restart', str(raised.exception))
        self.assertNotIn('restore', str(raised.exception))

    def test_turning_access_off_during_a_pause_is_applied_at_once(self):
        import sqlite3
        import dev
        catalog = self.state / 'control.sqlite'
        with sqlite3.connect(catalog) as database:
            for table in ('database_access', 'functions_settings'):
                database.execute(f'CREATE TABLE {table}(runtime TEXT, desired TEXT, state TEXT, updated_at INTEGER)')
                database.execute(f"INSERT INTO {table} VALUES (?, 'off', 'pending', 1)", (E,))
        (self.state / 'write-pauses').mkdir()
        (self.state / 'write-pauses' / f'{E}.json').write_text('{}')
        supervisor = dev.Supervisor.__new__(dev.Supervisor)
        with patch.object(dev, 'STATE', self.state):
            # Not held back like a request to turn on, so resume never reopens what was turned off.
            self.assertEqual(supervisor.toggle_requests('database'), [E])
            self.assertEqual(supervisor.toggle_requests('functions'), [E])

    def test_the_command_settles_only_when_asked_and_then_finishes_the_pause(self):
        import contextlib
        import io
        calls = []
        with patch.object(write_pause, 'operation_lock', contextlib.nullcontext), \
                patch.object(write_pause, 'adopt', lambda e: calls.append(('adopt', e))), \
                patch.object(write_pause, 'pause', lambda e: calls.append(('pause', e))), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(write_pause.main(['pause', E]), 0)
            self.assertEqual(write_pause.main(['pause', E, '--adopt-maintenance']), 0)
        self.assertEqual(calls, [('pause', E), ('adopt', E), ('pause', E)])

    def test_maintenance_another_operation_took_over_is_never_released_or_taken(self):
        routing = Routing()
        database = Database({E + '_developer': True})
        with self.assertRaises(write_pause.PauseError):
            self.pause(database, routing, fail=True)
        # Another operation resumes routing and pauses it again as its own.
        routing.row = dict(routing.row, revision=routing.row['revision'] + 2, maintenance=True)
        calls = len(routing.calls)
        with self.assertRaisesRegex(write_pause.PauseError, 'another operation'):
            write_pause.resume(E, state=self.state, query=database, route=routing, direct=True,
                               endpoints=self.endpoints, writers=Writers(self.log))
        with self.assertRaisesRegex(write_pause.PauseError, 'another operation'):
            self.pause(database, routing)
        self.assertEqual(routing.calls[calls:], ['read', 'read'])
        self.assertTrue(routing.row['maintenance'])
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'pausing')
        self.assertNotIn(('start', E), self.log)

    def test_an_unexpected_failure_is_reported_as_run_pause_again(self):
        class Broken(Writers):
            def stop_studio(self, e):
                raise OSError('docker gone')
        with self.assertRaisesRegex(write_pause.PauseError, 'run pause again'):
            write_pause.pause(E, state=self.state, query=Database({}), route=Routing(), endpoints=self.endpoints,
                              writers=Broken(self.log), clock=lambda: 1000.0, sleep=lambda _: None)
        self.assertEqual(write_pause.read_record(E, self.state)['phase'], 'pausing')

    def test_the_command_refuses_while_another_operation_holds_the_runtime(self):
        with patch.object(write_pause.runtime, 'STATE', self.state), \
                (self.state / 'operation.lock').open('a') as held, patch('sys.stderr'):
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch.object(write_pause, 'pause', side_effect=AssertionError('must not run')):
                self.assertEqual(write_pause.main(['pause', E]), 1)

    def test_opening_a_login_holds_the_pause_lock_for_its_whole_length(self):
        with write_pause.unpaused(E, 'open Studio', self.state):
            with (self.state / 'write-pause.lock').open('a') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_studio_checks_the_pause_again_under_the_lock_before_its_login_opens(self):
        answers = iter([False, True])
        opened = []
        (self.state / 'endpoints.json').write_text(json.dumps(self.endpoints))
        (self.state / 'runtime.json').write_text(json.dumps({'environments': {E: {'jwt': 'x' * 64}}}))
        with patch.object(write_pause.runtime, 'STATE', self.state), patch.object(write_pause.runtime, 'PRIVATE', self.state), \
                patch.object(write_pause, 'is_paused', side_effect=lambda *a: next(answers)), \
                patch.object(studio.source_fence, 'is_fenced', return_value=False), \
                patch.object(studio, 'require_rule'), patch.object(studio, 'ensure_images', return_value={}), \
                patch.object(studio, 'headroom'), patch.object(studio, 'remove'), patch.object(studio, 'open_connections'), \
                patch.object(studio, 'runtime_sql', side_effect=opened.append), \
                patch.object(studio.runtime, 'token', return_value='t'):
            with self.assertRaisesRegex(studio.StudioError, 'paused for writes'):
                studio.up(E)
        self.assertEqual(opened, [], 'the Studio login never opened')


class LockTests(PauseFixture):
    def lock_is_held(self):
        with (self.state / 'write-pause.lock').open('a') as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(other, fcntl.LOCK_UN)
            return False

    def test_turning_a_login_on_waits_for_a_pause_that_is_starting(self):
        import threading
        holder = (self.state / 'write-pause.lock').open('a')
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX)
        release = threading.Timer(0.5, lambda: fcntl.flock(holder, fcntl.LOCK_UN))
        release.start()
        self.addCleanup(release.join)
        # The default wait, not a refusal: the check runs once the pause has finished.
        with write_pause.unpaused(E, 'open Studio', self.state):
            self.assertTrue(self.lock_is_held())
        self.assertGreaterEqual(write_pause.LOCK_WAIT, write_pause.GATEWAY_WINDOW * 4)
        # A Studio start holds the lock through two readiness waits and the route's 30 s.
        self.assertGreater(write_pause.LOCK_WAIT, 2 * studio.wait_ready.__defaults__[0] + 30)

    def test_pause_reads_the_published_services_under_the_pause_lock(self):
        seen = []
        endpoints = {E: dict(self.endpoints[E], functions='http://10.0.0.5:9000')}

        def published():
            seen.append(self.lock_is_held())
            return endpoints
        with patch.object(write_pause.runtime, 'published_endpoints', published):
            write_pause.pause(E, state=self.state, query=Database({}), route=Routing(), writers=Writers(self.log),
                              clock=lambda: 1000.0, sleep=lambda _: None)
        # Functions turned on just before the pause took the lock are stopped with the others.
        self.assertEqual(seen, [True])
        self.assertIn(('restart', E, True), self.log)

    def test_pause_and_resume_hold_the_pause_lock_while_logins_change(self):
        seen = []
        test = self

        class Watching(Writers):
            def stop_studio(self, e):
                seen.append(('pause', test.lock_is_held()))
        routing = Routing()
        write_pause.pause(E, state=self.state, query=Database({E + '_developer': True}), route=routing,
                          endpoints=self.endpoints, writers=Watching(self.log), clock=lambda: 1000.0, sleep=lambda _: None)

        def route(value):
            seen.append(('resume', self.lock_is_held()))
            return routing(value)
        write_pause.resume(E, state=self.state, query=Database({E + '_developer': False}), route=route, direct=False,
                           endpoints=self.endpoints, writers=Writers(self.log))
        self.assertTrue(seen and all(held for _, held in seen), seen)
        self.assertFalse(self.lock_is_held())

    def test_resume_starts_the_functions_the_pause_left_stopped(self):
        endpoints = {E: dict(self.endpoints[E], functions={'url': 'http://10.0.0.6:9000'})}
        routing = Routing()
        write_pause.pause(E, state=self.state, query=Database({}), route=routing, endpoints=endpoints,
                          writers=Writers(self.log), clock=lambda: 1000.0, sleep=lambda _: None)
        self.log.clear()
        write_pause.resume(E, state=self.state, query=Database({}), route=routing, direct=False, endpoints=endpoints,
                           writers=Writers(self.log))
        self.assertEqual(self.log, [('start functions', E)])


class RestartTests(unittest.TestCase):
    def test_a_start_or_key_rotation_leaves_functions_stopped_while_paused(self):
        target = durable_runtime.Runtime.__new__(durable_runtime.Runtime)
        target.functions_start = lambda e: 'http://10.0.0.7:9000'
        for paused, expected in ((True, 'http://old:9000'), (False, 'http://10.0.0.7:9000')):
            with self.subTest(paused=paused), patch.object(write_pause, 'is_paused', return_value=paused):
                self.assertEqual(target.functions_back(E, 'http://old:9000'), expected)

    def test_every_start_and_rotation_brings_functions_back_through_the_pause_check(self):
        import inspect
        # activate_services serves both a Sbarbase start and a signing key rotation.
        source = inspect.getsource(durable_runtime.Runtime.activate_services)
        self.assertIn("self.functions_back(e, all_endpoints[e]['functions'])", source)
        self.assertNotIn('functions_start', source)

    def test_the_routing_operator_ends_when_the_command_is_killed(self):
        import os
        import signal
        import sys
        import time
        parent = subprocess.Popen([sys.executable, '-c', (
            'import subprocess, sys, write_pause\n'
            'child = subprocess.Popen(["sleep", "60"], preexec_fn=write_pause.die_with_parent)\n'
            'print(child.pid, flush=True)\n'
            'child.wait()\n')], cwd=Path(write_pause.__file__).parent, stdout=subprocess.PIPE, text=True)
        child = int(parent.stdout.readline())
        parent.stdout.close()
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            if Path(f'/proc/{child}/stat').read_text().split()[2] == 'Z':
                break
            time.sleep(.1)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail('the child outlived the killed command')

    def test_the_operator_call_dies_with_the_command(self):
        seen = {}

        def run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, '{"revision": 5}', '')
        with patch.object(write_pause.subprocess, 'run', run):
            write_pause.operator({'action': 'read', 'runtimes': [E]})
        self.assertIs(seen['preexec_fn'], write_pause.die_with_parent)


class FunctionsAddressTests(unittest.TestCase):
    def test_functions_come_back_as_a_fresh_container_with_the_current_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'endpoints.json').write_text(json.dumps({E: {'rest': 'http://10.0.0.4:3000',
                                                                  'functions': {'url': 'http://10.0.0.6:9000', 'image': 'sha256:old'}}}))
            launched = []

            class Runtime:
                def functions_start(self, e):
                    launched.append(e)
                    return {'url': 'http://10.0.0.7:9000', 'image': 'sha256:new'}
            asked = []
            writers = write_pause.Writers.__new__(write_pause.Writers)
            writers.sleep, writers.clock = lambda _: None, lambda: 0
            with patch.object(durable_runtime, 'STATE', state), patch.object(durable_runtime, 'Runtime', Runtime), \
                    patch.object(durable_runtime, 'http', side_effect=lambda url, *a, **k: (asked.append(url), (200, b''))[1]), \
                    patch.object(durable_runtime, 'atomic', lambda path, value: path.write_text(json.dumps(value))):
                writers.start_functions(E)
                writers.wait_rest(E)
            published = json.loads((state / 'endpoints.json').read_text())[E]['functions']
        # Recreated, as turning them on does, never the old container started again.
        self.assertEqual(launched, [E])
        self.assertEqual(published, {'url': 'http://10.0.0.7:9000', 'image': 'sha256:new'})
        self.assertIn('http://10.0.0.4:3000/', asked)

    def test_turning_functions_on_and_resume_share_one_launch(self):
        import inspect
        source = inspect.getsource(write_pause.Writers.start_functions)
        self.assertIn('functions_start(e)', source)
        self.assertNotIn("'docker', 'start'", source)


class WriterTests(unittest.TestCase):
    """The real restart, with Docker and SQL as doubles."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.calls = []
        self.stuck = False
        test = self

        class Backup:
            PREFIX = 'sbarbase-durable'
            STORAGE_CONTAINER = 'sbarbase-durable-storage'
            STATE = test.state

            @staticmethod
            def service_names(e):
                # The real helper adds Realtime when the environment runs it.
                return [f'sbarbase-durable-{e}-auth', f'sbarbase-durable-{e}-rest', f'sbarbase-durable-{e}-realtime']

            @staticmethod
            def run(argv, **kwargs):
                test.calls.append(('run', tuple(argv)))

            @staticmethod
            def start_storage():
                test.calls.append(('start storage',))
                return '10.0.0.9'

            @staticmethod
            def wait_storage(address):
                test.calls.append(('wait storage', address))

            @staticmethod
            def start_services(e):
                test.calls.append(('start services', e))

            @staticmethod
            def wait_healthy(e):
                test.calls.append(('wait auth', e))

        def query(text):
            test.calls.append(('sql', 'terminate' if 'pg_terminate_backend' in text else 'count'))
            return '3' if test.stuck else '0'
        writers = write_pause.Writers.__new__(write_pause.Writers)
        writers.backup, writers.query, writers.sleep, writers.clock = Backup, query, lambda _: None, lambda: 0
        writers.wait_rest = lambda e: test.calls.append(('wait rest', e))
        writers.start_functions = lambda e: test.calls.append(('start functions', e))
        self.writers = writers

    def test_starting_the_writers_brings_back_functions_only_when_published(self):
        self.writers.start(E, {E: {'functions': 'http://10.0.0.5:9000'}})
        self.assertEqual(self.calls[-1], ('start functions', E))
        self.calls.clear()
        self.writers.start(E, {E: {}})
        self.writers.start(E, {E: {'functions': 'http://10.0.0.5:9000'}}, functions=False)
        self.assertNotIn('start functions', [call[0] for call in self.calls])

    def test_the_writers_stop_together_the_database_drains_and_they_start_again(self):
        self.writers.restart(E, {E: {'functions': {'url': 'http://10.0.0.6:9000'}}})
        self.assertEqual(self.calls[0], ('run', ('docker', 'stop', f'sbarbase-durable-{E}-auth', f'sbarbase-durable-{E}-rest',
                                                 f'sbarbase-durable-{E}-realtime', f'sbarbase-durable-{E}-functions',
                                                 'sbarbase-durable-storage')))
        self.assertEqual(self.calls[1:3], [('sql', 'terminate'), ('sql', 'count')], 'the database drains while writers are down')
        # Functions stay stopped while the pause holds; resume starts them.
        self.assertEqual(self.calls[3:], [('start storage',), ('wait storage', '10.0.0.9'), ('start services', E),
                                          ('wait auth', E), ('wait rest', E)])

    def test_a_database_that_does_not_drain_still_starts_the_writers_again(self):
        self.stuck = True
        with self.assertRaisesRegex(write_pause.PauseError, 'run pause again'):
            self.writers.drain = lambda e: write_pause.Writers.drain(self.writers, e, drain=0)
            self.writers.restart(E, {E: {}})
        self.assertIn(('start services', E), self.calls)
        self.assertIn(('start storage',), self.calls)

    def test_the_drain_ends_only_sessions_in_the_environment_database(self):
        queries = []

        def query(sql):
            queries.append(sql)
            return '0'
        writers = write_pause.Writers(query)
        writers.drain(E)
        self.assertTrue(queries)
        # Exactly these statements: every other session in the environment's database, nothing narrower.
        self.assertEqual(queries, [
            f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '{E}' AND pid <> pg_backend_pid();",
            f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{E}' AND pid <> pg_backend_pid();"])

    def test_a_running_backup_or_restore_refuses_the_restart(self):
        with (self.state / 'backup.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(write_pause.PauseError, 'backup or restore is running'):
                self.writers.restart(E, {E: {}})
        self.assertEqual(self.calls, [])

    def test_stopping_studio_also_records_it_stopped_for_the_console(self):
        done = []
        with patch.object(studio, 'down', side_effect=lambda e: done.append(('down', e))), \
                patch.object(studio, 'record', side_effect=lambda e, state: done.append((state, e))):
            self.writers.stop_studio(E)
        self.assertEqual(done, [('down', E), ('stopped', E)])


class HealthTests(unittest.TestCase):
    def test_a_paused_environment_is_probed_directly_but_not_through_the_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:4000', 'pid': 42}))
            (state / 'endpoints.json').write_text(json.dumps({E: {'auth': 'http://10.0.0.3:9999', 'rest': 'http://10.0.0.4:3000'}}))
            (state / 'write-pauses').mkdir()
            (state / 'write-pauses' / (E + '.json')).write_text('{}')
            self.assertEqual(upgrade_health.write_paused(state), {E})
            urls = [url for _, url, _ in upgrade_health.probes(state, 42, {'environments': {}}, serving=set(), token='t')]
        self.assertIn('http://10.0.0.3:9999/health', urls)
        self.assertIn('http://10.0.0.4:3000/', urls)
        self.assertFalse(any('127.0.0.1:4000/' + E in url for url in urls))


if __name__ == '__main__':
    unittest.main()
