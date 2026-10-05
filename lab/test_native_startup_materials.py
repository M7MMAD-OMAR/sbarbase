"""Meaningful FD/private-file tests, executed only in the baked verifier image."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_startup_materials as materials


class PasswordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def provision(self):
        with materials.PasswordFile(str(self.directory)) as value:
            return value.provision()

    def test_provision_verify_closes_descriptors_and_matches_witness(self):
        expected = self.provision()
        guard = materials.PasswordFile(str(self.directory))
        with guard:
            self.assertEqual(expected, guard.verify(expected))
        self.assertEqual(guard.fds, [])
        self.assertIsNone(guard.fd)

    def test_exclusive_destination_preserves_existing_file(self):
        expected = self.provision()
        with materials.PasswordFile(str(self.directory)) as guard:
            with self.assertRaises(materials.MaterialError):
                guard.provision()
            self.assertEqual(guard.verify(), expected)

    def test_changed_expected_witness_refused(self):
        expected = self.provision()
        expected['inode'] += 1
        with materials.PasswordFile(str(self.directory)) as guard:
            with self.assertRaises(materials.MaterialError):
                guard.verify(expected)

    def test_pending_requires_reconciliation(self):
        (self.directory / (materials.PENDING + 'old')).touch()
        with materials.PasswordFile(str(self.directory)) as guard:
            with self.assertRaises(materials.MaterialError):
                guard.provision()

    def test_symlink_refused(self):
        (self.directory / 'password').symlink_to('/dev/null')
        with materials.PasswordFile(str(self.directory)) as guard:
            with self.assertRaises(materials.MaterialError):
                guard.verify()

    def test_hardlink_refused(self):
        self.provision()
        os.link(self.directory / 'password', self.directory / 'other')
        with materials.PasswordFile(str(self.directory)) as guard:
            with self.assertRaises(materials.MaterialError):
                guard.verify()

    def test_wrong_mode_size_and_format_refused(self):
        self.provision()
        target = self.directory / 'password'
        for mode, value in ((0o644, b'0' * 64), (0o600, b'0' * 63), (0o600, b'Z' * 64)):
            target.write_bytes(value)
            target.chmod(mode)
            with materials.PasswordFile(str(self.directory)) as guard:
                with self.assertRaises(materials.MaterialError):
                    guard.verify()

    def test_zero_write_refused_without_completed_destination(self):
        with patch.object(materials.os, 'write', return_value=0):
            with materials.PasswordFile(str(self.directory)) as guard:
                with self.assertRaises(materials.MaterialError):
                    guard.provision()
        self.assertFalse((self.directory / 'password').exists())

    def test_short_writes_complete(self):
        original = os.write
        with patch.object(materials.os, 'write', side_effect=lambda fd, value: original(fd, value[:3])):
            self.assertEqual(self.provision()['size'], 64)

    def test_fsync_failure_refuses(self):
        with patch.object(materials.os, 'fsync', side_effect=OSError('private error must not escape')):
            with materials.PasswordFile(str(self.directory)) as guard:
                with self.assertRaises(materials.MaterialError):
                    guard.provision()

    def test_parent_path_rebound_refuses(self):
        self.provision()
        with materials.PasswordFile(str(self.directory)) as guard:
            moved = self.directory.with_name(self.directory.name + '-moved')
            self.directory.rename(moved)
            self.directory.mkdir(mode=0o700)
            try:
                with self.assertRaises(materials.MaterialError):
                    guard.verify()
            finally:
                self.directory.rmdir()
                moved.rename(self.directory)

    def test_public_frame_only_after_descriptor_closure(self):
        self.provision()
        original = materials.PasswordFile.close
        closed = []
        def close(value):
            original(value)
            closed.append(True)
        with patch.object(materials.PasswordFile, 'close', close):
            frame = materials.material_frame('password', 'verify', str(self.directory))
        self.assertEqual(closed, [True])
        self.assertEqual(set(frame), {'status', 'kind', 'operation', 'metadata'})
        self.assertNotIn('value', frame)

    def test_close_uncertainty_emits_no_success_frame(self):
        self.provision()
        original = materials.PasswordFile.close
        def close(value):
            original(value)
            raise materials.MaterialError('DESCRIPTOR_CLOSE_FAILED_RECONCILIATION_REQUIRED')
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(materials.PasswordFile, 'close', close), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = materials.main(['password', 'verify', str(self.directory)])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(json.loads(stderr.getvalue()), {'status': 'MATERIAL_REFUSED'})

    def test_parent_must_be_private(self):
        self.directory.chmod(0o755)
        with self.assertRaises(materials.MaterialError):
            materials.PasswordFile(str(self.directory))
