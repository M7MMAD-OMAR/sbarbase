"""Fixture-only checks of the anchored runtime private directory boundary."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import private_directories as private


class PrivateDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'checkout'
        self.root.mkdir()

    def test_fresh_and_restart_preserve_private_namespace_and_content(self):
        leaf = private.prepare(self.root)
        directories = [leaf.parent, leaf]
        before = [p.stat().st_ino for p in directories]
        for p in directories:
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700)
            self.assertEqual(p.stat().st_uid, os.geteuid())
        sentinel = leaf / 'fixture'; sentinel.write_text('retained')
        self.assertEqual(private.prepare(self.root), leaf)
        self.assertEqual([p.stat().st_ino for p in directories], before)
        self.assertEqual(sentinel.read_text(), 'retained')

    def test_old_recursive_creation_reproduces_first_operator_refusal(self):
        leaf = self.root / '.secrets' / 'upstream'
        old_umask = os.umask(0o022)
        try:
            leaf.mkdir(mode=0o700, parents=True)
        finally:
            os.umask(old_umask)
        self.assertEqual(stat.S_IMODE(leaf.parent.stat().st_mode), 0o755)
        with self.assertRaisesRegex(private.PrivateDirectoryRefusal, 'ownership or mode'):
            private.prepare(self.root)
        self.assertEqual(stat.S_IMODE(leaf.parent.stat().st_mode), 0o755)

    def test_unsafe_existing_components_are_not_repaired(self):
        for component in ('.secrets', 'upstream'):
            with self.subTest(component=component):
                root = self.base / component; root.mkdir()
                target = root / '.secrets'
                target.mkdir(mode=0o700 if component == 'upstream' else 0o755)
                if component == 'upstream':
                    target = target / 'upstream'; target.mkdir(mode=0o755)
                before = target.stat()
                with self.assertRaises(private.PrivateDirectoryRefusal): private.prepare(root)
                self.assertEqual(target.stat().st_ino, before.st_ino)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_wrong_owner_refused_without_adoption(self):
        target = self.root / '.secrets'; target.mkdir(mode=0o700)
        with patch.object(private.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(private.PrivateDirectoryRefusal, 'ownership or mode'):
                private.prepare(self.root)
        self.assertFalse((target / 'upstream').exists())
        self.assertEqual(target.stat().st_uid, os.geteuid())

    def test_symlinks_in_root_and_private_components_never_followed(self):
        foreign = self.base / 'foreign'; foreign.mkdir()
        for component in ('root', '.secrets', 'upstream'):
            with self.subTest(component=component):
                root = self.base / ('case-' + component); root.mkdir()
                if component == 'root':
                    root.rmdir(); root.symlink_to(foreign, target_is_directory=True)
                elif component == '.secrets':
                    (root / component).symlink_to(foreign, target_is_directory=True)
                else:
                    (root / '.secrets').mkdir(mode=0o700)
                    (root / '.secrets' / component).symlink_to(foreign, target_is_directory=True)
                with self.assertRaises(private.PrivateDirectoryRefusal): private.prepare(root)
                self.assertEqual(list(foreign.iterdir()), [])

    def test_missing_relative_parent_traversal_and_files_refused(self):
        for root in (self.base / 'missing', Path('relative'), self.root / '..', Path('/')):
            with self.subTest(root=str(root)):
                with self.assertRaises(private.PrivateDirectoryRefusal): private.prepare(root)
        (self.root / '.secrets').write_text('fixture')
        with self.assertRaises(private.PrivateDirectoryRefusal): private.prepare(self.root)
        self.assertEqual((self.root / '.secrets').read_text(), 'fixture')
        self.assertFalse((self.base / 'missing').exists())

    def test_checkout_replacement_during_creation_refused_without_foreign_write(self):
        foreign = self.base / 'foreign'; foreign.mkdir()
        original_mkdir = os.mkdir
        def replace(name, mode=0o777, *, dir_fd=None):
            original_mkdir(name, mode, dir_fd=dir_fd)
            if name == '.secrets':
                self.root.rename(self.base / 'retained')
                self.root.symlink_to(foreign, target_is_directory=True)
        with patch.object(private.os, 'mkdir', side_effect=replace):
            with self.assertRaisesRegex(private.PrivateDirectoryRefusal, 'identity changed'):
                private.prepare(self.root)
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertFalse((self.base / 'retained' / '.secrets' / 'upstream').exists())

    def test_private_mode_change_during_creation_refused(self):
        original_mkdir = os.mkdir
        def change(name, mode=0o777, *, dir_fd=None):
            original_mkdir(name, mode, dir_fd=dir_fd)
            if name == 'upstream': (self.root / '.secrets').chmod(0o755)
        with patch.object(private.os, 'mkdir', side_effect=change):
            with self.assertRaisesRegex(private.PrivateDirectoryRefusal, 'ownership or mode'):
                private.prepare(self.root)


if __name__ == '__main__': unittest.main()
