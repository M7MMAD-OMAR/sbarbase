"""The runtime state directory is private to this account (lab/run.py private_dir)."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

import run


class PrivateStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        previous = os.umask(0o002)
        self.addCleanup(os.umask, previous)

    def mode(self, path):
        return stat.S_IMODE(os.lstat(path).st_mode)

    def test_a_new_directory_is_private_under_a_loose_umask(self):
        path = run.private_dir(self.root / '.lab' / 'upstream')
        self.assertEqual(self.mode(path), 0o700)

    def test_a_directory_left_open_to_the_group_is_closed(self):
        # A server installed under umask 002 had .lab/upstream at 775, and restore refused.
        path = self.root / 'upstream'
        path.mkdir()
        path.chmod(0o775)
        (path / 'kept.json').write_text('{}')
        run.private_dir(path)
        self.assertEqual(self.mode(path), 0o700)
        self.assertEqual((path / 'kept.json').read_text(), '{}')

    def test_a_link_or_a_file_is_refused_and_left_alone(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        target.chmod(0o755)
        link = self.root / 'upstream'
        link.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'directory of this account'):
            run.private_dir(link)
        self.assertEqual(self.mode(target), 0o755)
        file = self.root / 'file'
        file.write_text('')
        with self.assertRaises(RuntimeError):
            run.private_dir(file)


class CallerTests(unittest.TestCase):
    def test_every_creator_of_the_state_directory_makes_it_private(self):
        lab = Path(run.__file__).parent
        for name, call in (('durable_runtime.py', 'lab.private_dir(STATE)'), ('installation_runtime.py', 'lab.private_dir(runtime.STATE)'),
                           ('dev.py', 'run.private_dir(STATE)'), ('install_server.py', 'run.private_dir(STATE)')):
            source = (lab / name).read_text()
            self.assertIn(call, source, name)
            self.assertNotIn('STATE.mkdir(', source, name)


if __name__ == '__main__':
    unittest.main()
