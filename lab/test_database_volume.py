"""Which volume holds the database: the constant until a rebuilt cluster is taken into use."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database_volume as volume

NEW = 'sbarbase-durable-pgdata-0123456789ab'


class VolumeRecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.path = self.state / volume.NAME

    def write(self, value, mode=0o600):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))
        os.chmod(self.path, mode)

    def test_without_a_record_it_is_the_constant(self):
        self.assertEqual(volume.current(self.state), 'sbarbase-durable-pgdata')
        self.assertEqual(volume.DEFAULT, 'sbarbase-durable-pgdata')

    def test_a_published_volume_is_read_back_and_publishing_it_again_changes_nothing(self):
        self.assertEqual(volume.publish(self.state, NEW), NEW)
        self.assertEqual(volume.current(self.state), NEW)
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), oct(0o600))
        before = self.path.stat().st_mtime_ns
        volume.publish(self.state, NEW)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertFalse((self.state / (volume.NAME + '.pending')).exists())

    def test_only_database_volume_names_are_accepted(self):
        for name in ('postgres-data', 'sbarbase-durable-storage', 'sbarbase-restore-0123456789ab-pgdata', '', None,
                     'sbarbase-durable-pgdata-0123456789ab2', 'sbarbase-durable-pgdata-XYZ'):
            with self.subTest(name=name), self.assertRaises(volume.VolumeError):
                volume.publish(self.state, name)
        self.assertFalse(self.path.exists())

    def test_a_record_that_cannot_be_trusted_refuses_rather_than_falls_back(self):
        cases = [('not json', 0o600), ({'version': 1}, 0o600), ({'version': 2, 'volume': NEW}, 0o600),
                 ({'version': True, 'volume': NEW}, 0o600), ({'version': 1.0, 'volume': NEW}, 0o600),
                 ({'version': 1, 'volume': 'other'}, 0o600), ({'version': 1, 'volume': NEW, 'x': 1}, 0o600),
                 ({'version': 1, 'volume': NEW}, 0o644)]
        for value, mode in cases:
            with self.subTest(value=value, mode=oct(mode)):
                self.write(value, mode)
                with self.assertRaises(volume.VolumeError):
                    volume.current(self.state)

    def test_a_record_owned_by_another_account_is_refused(self):
        self.write({'version': 1, 'volume': NEW})
        with patch.object(volume.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(volume.VolumeError):
                volume.current(self.state)
        self.assertEqual(volume.current(self.state), NEW)

    def test_a_record_larger_than_the_limit_is_refused_even_when_it_starts_as_valid_json(self):
        self.write('{"version": 1, "volume": "%s"}' % NEW + ' ' * 5000 + 'garbage')
        with self.assertRaises(volume.VolumeError):
            volume.current(self.state)

    def test_a_symlink_is_refused(self):
        target = self.state / 'elsewhere.json'
        target.write_text(json.dumps({'version': 1, 'volume': NEW}))
        os.chmod(target, 0o600)
        self.path.symlink_to(target)
        with self.assertRaises(volume.VolumeError):
            volume.current(self.state)

    def test_a_left_pending_file_does_not_count(self):
        (self.state / (volume.NAME + '.pending')).write_text('{"version": 1, "volume": "%s"}' % NEW)
        self.assertEqual(volume.current(self.state), volume.DEFAULT)
        self.assertEqual(volume.publish(self.state, NEW), NEW)



class RuntimeWiringTests(unittest.TestCase):
    """The runtime and the move use the recorded volume."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)

    def test_the_database_mount_follows_the_record(self):
        import durable_runtime as runtime
        with patch.object(runtime, 'STATE', self.state):
            self.assertEqual(runtime.database_mounts(), [(volume.DEFAULT, '/var/lib/postgresql/data')])
            volume.publish(self.state, NEW)
            self.assertEqual(runtime.database_mounts(), [(NEW, '/var/lib/postgresql/data')])

    def test_a_retained_database_on_the_old_volume_is_refused_after_a_move(self):
        import json as _json
        from types import SimpleNamespace
        import durable_runtime as runtime
        pin = {'id': 'sha256:' + 'a' * 64, 'tag': 'postgres:x', 'digests': ['docker.io/library/postgres@sha256:' + 'a' * 64]}
        source = runtime.Runtime.__new__(runtime.Runtime)
        source.pins = {'db': pin}
        actual = {'Id': 'db-container', 'Image': 'sha256:' + 'b' * 64, 'Config': {'Env': ['A=1']},
                  'Mounts': [{'Name': volume.DEFAULT, 'Destination': '/var/lib/postgresql/data'}],
                  'NetworkSettings': {'Networks': {runtime.NETWORK: {}}}}

        def docker(*args, **kwargs):
            if args[:2] == ('image', 'inspect'):
                return SimpleNamespace(returncode=0, stdout=_json.dumps([{'Id': 'sha256:' + 'b' * 64,
                                                                           'RepoDigests': pin['digests']}]), stderr='')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        def launch():
            with patch.object(runtime.lab, 'docker', docker), patch.object(runtime, 'inspect', return_value=actual), \
                    patch.object(runtime, 'PRIVATE', self.state), patch.object(runtime, 'UPGRADE_INTENT', self.state / 'intent.json'):
                return source.launch('db', 'db', {'A': '1'}, '1024m', 1, runtime.database_mounts(), tier='system.db')

        with patch.object(runtime, 'STATE', self.state):
            self.assertEqual(launch(), ('db-container', False))
            volume.publish(self.state, NEW)
            with self.assertRaisesRegex(RuntimeError, 'volume mismatch'):
                launch()

    def test_start_launches_the_database_on_the_recorded_volume(self):
        import durable_runtime as runtime
        from types import SimpleNamespace
        from unittest.mock import Mock

        class Launched(Exception):
            pass

        seen = []

        def launch(name, component, env, memory, cpus, volumes=(), command=(), **kwargs):
            seen.append((name, component, list(volumes)))
            raise Launched()

        source = runtime.Runtime.__new__(runtime.Runtime)
        source.values = {'admin': 'x', 'environments': {}}
        source.hba_writer = Mock()
        source.launch = launch
        meminfo = Mock(read_text=Mock(return_value='MemAvailable: 99999999 kB\n'))
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime.effect_receipt, 'require_settled'), \
                patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='', returncode=0)), \
                patch.object(runtime.lab, 'Path', return_value=meminfo), patch.object(runtime, 'inspect', return_value={'x': 1}), \
                patch.object(runtime, 'realtime_count', return_value=0), patch.object(runtime, 'functions_count', return_value=0):
            volume.publish(self.state, NEW)
            with self.assertRaises(Launched):
                source.start()
        self.assertEqual(seen, [(runtime.DB, 'db', [(NEW, '/var/lib/postgresql/data')])])

    def test_the_move_runs_the_migration_on_the_recorded_volume(self):
        import database_move as move
        with patch.object(move.runtime, 'STATE', self.state):
            command = move.migration_command(False)
            self.assertEqual(command[command.index('--volume') + 1], volume.DEFAULT)
            volume.publish(self.state, NEW)
            command = move.migration_command(False)
            self.assertEqual(command[command.index('--volume') + 1], NEW)

    def test_only_a_rebuilt_volume_is_a_move_target(self):
        self.assertFalse(volume.rebuilt(volume.DEFAULT))
        self.assertTrue(volume.rebuilt(NEW))
        self.assertFalse(volume.rebuilt('sbarbase-durable-storage'))


if __name__ == '__main__':
    unittest.main()
