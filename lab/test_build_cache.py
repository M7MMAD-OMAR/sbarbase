"""Clearing Docker's build cache from the console (lab/build_cache.py): the size read first, one prune and nothing else, the outcome recorded."""
import json
import re
import sqlite3
import tempfile
import unittest
import unittest.mock
from contextlib import closing
from pathlib import Path

import build_cache

DF = 'Images\t2.3GB\t1.1GB (47%)\nContainers\t10MB\t0B (0%)\nLocal Volumes\t5GB\t1GB (20%)\nBuild Cache\t466MB\t466MB\n'


class SizeTests(unittest.TestCase):
    def test_the_build_cache_row_is_read_in_bytes(self):
        self.assertEqual(build_cache.size(lambda *args: DF), {'totalBytes': 466_000_000, 'reclaimableBytes': 466_000_000})

    def test_a_reclaimable_figure_with_a_percentage_is_read(self):
        self.assertEqual(build_cache.to_bytes('1.1GB (47%)'), 1_100_000_000)
        self.assertEqual(build_cache.to_bytes('0B'), 0)
        self.assertIsNone(build_cache.to_bytes('lots'))

    def test_an_unreadable_size_is_unknown_never_zero(self):
        self.assertIsNone(build_cache.size(lambda *args: 'Images\t1GB\t0B\n'))
        self.assertIsNone(build_cache.size(lambda *args: 'Build Cache\tmany\tsome\n'))

        def broken(*args):
            raise RuntimeError('docker failed')
        self.assertIsNone(build_cache.size(broken))


class ClearTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.catalog = Path(directory.name) / 'control.sqlite'
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute('CREATE TABLE host_actions(action TEXT PRIMARY KEY, state TEXT, actor TEXT, requested_at INTEGER, finished_at INTEGER, reclaimed_bytes INTEGER, failure TEXT)')
            database.execute("INSERT INTO host_actions(action, state, actor, requested_at) VALUES ('build_cache_clear', 'pending', 'alice', 1)")
            database.execute('CREATE TABLE audit_events(actor TEXT, action TEXT, subject TEXT, detail TEXT, at INTEGER)')

    def row(self):
        with closing(sqlite3.connect(self.catalog)) as database:
            return database.execute('SELECT state, reclaimed_bytes, failure FROM host_actions').fetchone()

    def test_one_prune_and_nothing_else_ever_runs(self):
        calls = []
        sizes = iter([DF, 'Build Cache\t100MB\t100MB\n'])

        def run(*args):
            calls.append(args)
            return next(sizes) if args == build_cache.DF else ''
        self.assertEqual(build_cache.clear(run, self.catalog), 0)
        removed = [call for call in calls if call != build_cache.DF]
        self.assertEqual(removed, [('builder', 'prune', '--force')])
        # No image, container, volume or system prune can be asked for by this file.
        for call in calls:
            self.assertNotIn('image', call)
            self.assertNotIn('volume', call)
            self.assertNotEqual(call[:2], ('system', 'prune'))
        self.assertEqual(self.row(), ('done', 366_000_000, None))

    def test_what_it_freed_is_recorded_as_an_audit_event_of_the_supervisor(self):
        sizes = iter([DF, 'Build Cache\t100MB\t100MB\n'])
        build_cache.clear(lambda *args: next(sizes) if args == build_cache.DF else '', self.catalog)
        with closing(sqlite3.connect(self.catalog)) as database:
            action, actor, detail = database.execute('SELECT action, actor, detail FROM audit_events').fetchone()
        self.assertEqual((action, actor, json.loads(detail)), ('host.build_cache_clear_done', 'system:supervisor', {'reclaimed_bytes': 366_000_000}))

    def test_a_failed_prune_is_recorded_in_words_and_changes_nothing_else(self):
        def run(*args):
            if args == build_cache.PRUNE:
                raise RuntimeError('docker failed')
            return DF
        self.assertEqual(build_cache.clear(run, self.catalog), 1)
        state, reclaimed, failure = self.row()
        self.assertEqual((state, reclaimed), ('failed', None))
        self.assertIn('Nothing else was touched', failure)

    def test_the_command_takes_exactly_clear(self):
        self.assertEqual(build_cache.main(['prune']), 2)
        self.assertEqual(build_cache.main(['clear', '--all']), 2)
        self.assertEqual(build_cache.main([]), 2)

    def test_the_source_has_one_removing_command_and_it_is_the_builder_prune(self):
        source = Path(build_cache.__file__).read_text()
        self.assertEqual(re.findall(r"'(?:image|volume|container|system|builder)', '(prune|rm)'", source), ['prune'])
        self.assertNotIn("'image'", source)
        self.assertNotIn("'volume'", source)



class SchedulingTests(unittest.TestCase):
    """The supervisor runs the clear the console asked for: one child at a time, never during an update, and only what HOST_ACTIONS names."""
    def setUp(self):
        import dev
        self.dev = dev
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        with closing(sqlite3.connect(self.state / 'control.sqlite')) as database, database:
            database.execute('CREATE TABLE host_actions(action TEXT PRIMARY KEY, state TEXT, actor TEXT, requested_at INTEGER, finished_at INTEGER, failure TEXT)')
        self.supervisor = dev.Supervisor.__new__(dev.Supervisor)
        self.supervisor.host_action = None
        self.supervisor.host_action_after = 0.0
        self.supervisor.spawn = unittest.mock.Mock(return_value='child')
        self.supervisor.paused = lambda: False
        patcher = unittest.mock.patch.object(dev, 'STATE', self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, state='pending', action='build_cache_clear'):
        with closing(sqlite3.connect(self.state / 'control.sqlite')) as database, database:
            database.execute('INSERT OR REPLACE INTO host_actions(action, state, actor, requested_at) VALUES (?, ?, ?, ?)', (action, state, 'alice', 1))

    def test_nothing_runs_without_a_request(self):
        self.supervisor.schedule_host_actions()
        self.supervisor.spawn.assert_not_called()

    def test_a_pending_clear_runs_the_one_command(self):
        self.request()
        self.supervisor.schedule_host_actions()
        self.supervisor.spawn.assert_called_once_with(['/usr/bin/python3', 'lab/build_cache.py', 'clear'])

    def test_not_while_an_update_drains_the_supervisor_and_not_twice_at_once(self):
        self.request()
        self.supervisor.paused = lambda: True
        self.supervisor.schedule_host_actions()
        self.supervisor.spawn.assert_not_called()
        self.supervisor.paused = lambda: False
        with unittest.mock.patch.object(self.dev, 'child_status', return_value=None):
            self.supervisor.host_action = 'running child'
            self.supervisor.schedule_host_actions()
        self.supervisor.spawn.assert_not_called()

    def test_a_row_that_is_running_or_done_or_names_another_action_runs_nothing(self):
        for state, action in (('running', 'build_cache_clear'), ('done', 'build_cache_clear'), ('pending', 'image_prune')):
            self.request(state, action)
        self.supervisor.schedule_host_actions()
        self.supervisor.spawn.assert_not_called()
        self.assertEqual(set(self.dev.HOST_ACTIONS), {'build_cache_clear'})

    def test_a_child_that_ended_without_recording_is_asked_again_only_after_half_a_minute(self):
        self.request()
        self.supervisor.host_action = 'old child'
        self.supervisor.host_action_after = 0.0
        with unittest.mock.patch.object(self.dev, 'child_status', return_value=1), unittest.mock.patch.object(self.dev, 'terminate_group'), \
             unittest.mock.patch.object(self.dev.time, 'monotonic', return_value=100.0):
            self.supervisor.schedule_host_actions()
            self.assertEqual(self.supervisor.host_action_after, 130.0)
            self.supervisor.spawn.assert_not_called()
        with unittest.mock.patch.object(self.dev.time, 'monotonic', return_value=131.0):
            self.supervisor.schedule_host_actions()
        self.supervisor.spawn.assert_called_once()

    def test_a_running_row_left_by_a_stopped_supervisor_is_failed_in_words_at_the_next_start(self):
        self.request('running')
        self.supervisor.reset_host_actions()
        with closing(sqlite3.connect(self.state / 'control.sqlite')) as database:
            self.assertEqual(database.execute('SELECT state FROM host_actions').fetchone(), ('failed',))
        self.request('pending')
        self.supervisor.reset_host_actions()
        with closing(sqlite3.connect(self.state / 'control.sqlite')) as database:
            self.assertEqual(database.execute('SELECT state FROM host_actions').fetchone(), ('pending',))

    def test_the_supervisor_is_not_idle_while_the_child_runs(self):
        self.supervisor.host_action = 'child'
        source = Path(self.dev.__file__).read_text()
        self.assertIn('or self.studios or self.host_action is not None:', source)

    def test_a_finished_child_is_collected_and_the_next_request_runs(self):
        self.request()
        self.supervisor.host_action = 'old child'
        with unittest.mock.patch.object(self.dev, 'child_status', return_value=0), unittest.mock.patch.object(self.dev, 'terminate_group') as end:
            self.supervisor.schedule_host_actions()
        end.assert_called_once()
        self.supervisor.spawn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
