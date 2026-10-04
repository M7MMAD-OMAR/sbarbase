"""Atomic root key failure and admission tests, run only in baked Docker."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_root_key as keys


class RootKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='root-key-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name) / 'custom'
        self.parent.mkdir(mode=0o700)
        self.path = self.parent / keys.NAME

    def fixture(self, value=b'a' * 64, mode=0o600):
        self.path.write_bytes(value)
        self.path.chmod(mode)

    def refused(self, operation, reason=None):
        with self.assertRaises(keys.RootKeyError) as caught:
            operation()
        if reason is not None:
            self.assertEqual(str(caught.exception), reason)
        return caught.exception

    def test_cold_publish_and_witness_are_metadata_only(self):
        with keys.RootKey(str(self.parent)) as key:
            result = key.provision()
            self.assertEqual(key.verify(result), result)
            self.assertEqual(set(result), {'device', 'inode', 'mode', 'uid', 'gid',
                                          'nlink', 'size', 'mtime_ns', 'ctime_ns'})
            self.assertEqual((result['size'], result['mode'], result['nlink']), (64, 0o600, 1))
        self.assertEqual(set(os.listdir(self.parent)), {keys.NAME})

    def test_existing_key_is_never_replaced_or_adopted(self):
        self.fixture()
        before = keys.metadata(self.path.stat())
        with keys.RootKey(str(self.parent)) as key:
            self.refused(key.provision, 'EXISTING_KEY_REFUSED')
        self.assertEqual(keys.metadata(self.path.stat()), before)
        self.assertTrue(self.path.read_bytes() == b'a' * 64)

    def test_symlink_and_dangling_symlink_refuse(self):
        for target in ('missing', str(self.parent)):
            with self.subTest(target=target):
                self.path.symlink_to(target)
                before = self.path.lstat()
                with keys.RootKey(str(self.parent)) as key:
                    self.refused(key.provision, 'EXISTING_KEY_REFUSED')
                    self.refused(key.verify, 'KEY_UNAVAILABLE')
                self.assertEqual(self.path.lstat(), before)
                self.path.unlink()

    def test_hardlink_refuses(self):
        self.fixture()
        os.link(self.path, self.parent / 'other')
        with keys.RootKey(str(self.parent)) as key:
            self.refused(key.verify, 'KEY_METADATA_REFUSED')

    def test_fifo_refuses_without_blocking(self):
        os.mkfifo(self.path, 0o600)
        with keys.RootKey(str(self.parent)) as key:
            self.refused(key.verify, 'KEY_METADATA_REFUSED')

    def test_directory_leaf_refuses(self):
        self.path.mkdir(mode=0o700)
        with keys.RootKey(str(self.parent)) as key:
            self.refused(key.verify, 'KEY_METADATA_REFUSED')

    def test_bad_modes_and_owner_refuse(self):
        self.fixture()
        with keys.RootKey(str(self.parent)) as key:
            for mode in (0o644, 0o400, 0o660, 0o1600):
                with self.subTest(mode=mode):
                    self.path.chmod(mode)
                    self.refused(key.verify, 'KEY_METADATA_REFUSED')
            self.path.chmod(0o600)
            with patch.object(keys.os, 'getegid', return_value=os.getegid() + 1):
                self.refused(key.verify, 'KEY_METADATA_REFUSED')
            with patch.object(keys.os, 'geteuid', return_value=os.geteuid() + 1):
                self.refused(key.verify, 'KEY_METADATA_REFUSED')

    def test_invalid_size_and_format_refuse(self):
        for value in (b'', b'a' * 63, b'a' * 65, b'A' * 64,
                      b'g' * 64, b'a' * 63 + b'\n', b'\x00' * 64):
            with self.subTest(length=len(value)):
                self.fixture(value)
                with keys.RootKey(str(self.parent)) as key:
                    self.refused(key.verify)
                self.path.unlink()

    def test_missing_key_refuses(self):
        with keys.RootKey(str(self.parent)) as key:
            self.refused(key.verify, 'KEY_UNAVAILABLE')
        self.assertFalse(self.path.exists())

    def test_guard_pread_is_bounded(self):
        self.fixture()
        real = os.pread
        calls = []
        def read(fd, count, offset):
            calls.append((count, offset))
            return real(fd, count, offset)
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'pread', read):
            key.verify()
        self.assertEqual(calls, [(65, 0)])

    def test_growth_during_read_refuses(self):
        self.fixture()
        real = os.pread
        def grow(fd, count, offset):
            value = real(fd, count, offset)
            with self.path.open('ab') as output:
                output.write(b'x')
            return value
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'pread', grow):
            self.refused(key.verify, 'KEY_BINDING_CHANGED')

    def test_namespace_replacement_during_read_refuses(self):
        self.fixture()
        real = os.pread
        replacement = self.parent / 'replacement'
        replacement.write_bytes(b'b' * 64)
        replacement.chmod(0o600)
        def replace(fd, count, offset):
            value = real(fd, count, offset)
            os.replace(replacement, self.path)
            return value
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'pread', replace):
            self.refused(key.verify, 'KEY_BINDING_CHANGED')

    def test_metadata_time_change_during_read_refuses(self):
        self.fixture()
        real = os.pread
        def change(fd, count, offset):
            value = real(fd, count, offset)
            previous = self.path.stat()
            os.utime(self.path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1))
            return value
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'pread', change):
            self.refused(key.verify, 'KEY_BINDING_CHANGED')

    def test_wrong_witness_and_boolean_integer_refuse(self):
        self.fixture()
        with keys.RootKey(str(self.parent)) as key:
            witness = key.verify()
            changed = dict(witness, inode=witness['inode'] + 1)
            self.refused(lambda: key.verify(changed), 'KEY_WITNESS_DIFFERS')
            self.refused(lambda: key.verify(dict(witness, nlink=True)), 'KEY_WITNESS_DIFFERS')
            self.refused(lambda: key.verify(dict(witness, extra=0)), 'KEY_WITNESS_DIFFERS')

    def test_parent_symlink_and_unsafe_paths_refuse(self):
        link = Path(self.temporary.name) / 'link'
        link.symlink_to(self.parent)
        for path in (str(link), str(link / 'child'), str(self.parent) + '/',
                     str(self.parent) + '/..', str(self.parent) + '//x', '.', '/'):
            with self.subTest(path=path):
                self.refused(lambda: keys.RootKey(path))

    def test_parent_mode_and_namespace_changes_refuse(self):
        self.parent.chmod(0o777)
        self.refused(lambda: keys.RootKey(str(self.parent)), 'PARENT_POLICY_REFUSED')
        self.parent.chmod(0o700)
        with keys.RootKey(str(self.parent)) as key:
            self.parent.rename(self.parent.with_name('moved'))
            self.parent.mkdir(mode=0o700)
            self.refused(key.verify)

    def test_pending_artifact_requires_reconciliation(self):
        pending = self.parent / (keys.PENDING + 'a' * 32)
        pending.write_bytes(b'')
        with keys.RootKey(str(self.parent)) as key:
            self.refused(key.provision, 'PENDING_RECONCILIATION_REQUIRED')
            self.refused(key.verify, 'PENDING_RECONCILIATION_REQUIRED')

    def test_partial_writes_complete_without_extra_material(self):
        real = os.write
        calls = []
        def write(fd, value):
            calls.append(len(value))
            return real(fd, value[:7])
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'write', write):
            result = key.provision()
        self.assertEqual(result['size'], 64)
        self.assertEqual(len(calls), 10)

    def test_zero_write_refuses_and_removes_owned_temporary(self):
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'write', return_value=0):
            self.refused(key.provision, 'KEY_WRITE_REFUSED')
        self.assertEqual(os.listdir(self.parent), [])

    def test_file_fsync_failure_does_not_publish(self):
        real = os.fsync
        calls = 0
        def sync(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('synthetic file sync failure')
            return real(fd)
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'fsync', sync):
            self.refused(key.provision, 'PROVISION_FAILED_RECONCILIATION_REQUIRED')
        self.assertEqual(os.listdir(self.parent), [])

    def test_namespace_fsync_failures_keep_published_key_and_refuse_rekey(self):
        real = os.fsync
        for failing in (2, 3):
            with self.subTest(stage=failing):
                calls = 0
                def sync(fd):
                    nonlocal calls
                    calls += 1
                    if calls == failing:
                        raise OSError('synthetic directory sync failure')
                    return real(fd)
                with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'fsync', sync):
                    self.refused(key.provision, 'PROVISION_FAILED_RECONCILIATION_REQUIRED')
                with keys.RootKey(str(self.parent)) as key:
                    key.verify()
                    self.refused(key.provision, 'EXISTING_KEY_REFUSED')
                self.path.unlink()

    def test_atomic_destination_race_preserves_winner(self):
        real = os.link
        def collide(source, target, **kwargs):
            self.fixture(b'c' * 64)
            return real(source, target, **kwargs)
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'link', collide):
            self.refused(key.provision, 'PROVISION_FAILED_RECONCILIATION_REQUIRED')
        self.assertTrue(self.path.read_bytes() == b'c' * 64)
        self.assertEqual(set(os.listdir(self.parent)), {keys.NAME})

    def test_cleanup_never_unlinks_substituted_temporary(self):
        def substitute(source, target, **kwargs):
            stale = self.parent / source
            # Keep the original inode allocated to prevent immediate inode reuse.
            stale.rename(self.parent / 'held-original')
            stale.write_bytes(b'public replacement')
            raise OSError('synthetic publication failure')
        with keys.RootKey(str(self.parent)) as key, patch.object(keys.os, 'link', substitute):
            self.refused(key.provision, 'PROVISION_FAILED_RECONCILIATION_REQUIRED')
        replacements = [p for p in self.parent.iterdir() if p.name.startswith(keys.PENDING)]
        self.assertEqual(len(replacements), 1)
        self.assertTrue(replacements[0].read_bytes() == b'public replacement')
        self.assertFalse(self.path.exists())

    def test_typed_failure_after_publication_requires_reconciliation(self):
        with keys.RootKey(str(self.parent)) as key:
            with patch.object(key, 'verify', side_effect=keys.RootKeyError('KEY_METADATA_REFUSED')):
                self.refused(key.provision, 'PROVISION_FAILED_RECONCILIATION_REQUIRED')
            key.verify()
            self.refused(key.provision, 'EXISTING_KEY_REFUSED')

    def test_close_failure_is_sanitized_and_all_descriptors_attempted(self):
        key = keys.RootKey(str(self.parent))
        expected = list(reversed(key.fds))
        real = os.close
        calls = []
        def close(fd):
            calls.append(fd)
            real(fd)
            if len(calls) == 1:
                raise OSError('synthetic close failure')
        with patch.object(keys.os, 'close', close):
            self.refused(key.close, 'DESCRIPTOR_CLOSE_FAILED_RECONCILIATION_REQUIRED')
        self.assertEqual(calls, expected)
        self.assertEqual(key.fds, [])
        self.assertIsNone(key.fd)

    def test_secret_descriptor_close_failure_requires_reconciliation(self):
        real = os.close
        failed = False
        def close(fd):
            nonlocal failed
            real(fd)
            if not failed:
                failed = True
                raise OSError('synthetic secret descriptor close failure')
        with keys.RootKey(str(self.parent)) as key:
            with patch.object(keys.os, 'close', close):
                self.refused(key.provision, 'PROVISION_FAILED_RECONCILIATION_REQUIRED')
            key.verify()

    def test_cli_close_failure_returns_sanitized_json(self):
        real = keys.RootKey.close
        def close(key):
            real(key)
            raise keys.RootKeyError('DESCRIPTOR_CLOSE_FAILED_RECONCILIATION_REQUIRED')
        output, errors = io.StringIO(), io.StringIO()
        self.fixture()
        with patch.object(keys.RootKey, 'close', close):
            with patch.object(keys.sys, 'argv', ['root-key', 'verify', str(self.parent)]):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(keys.main(), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(json.loads(errors.getvalue()),
                         {'passed': False, 'reason': 'DESCRIPTOR_CLOSE_FAILED_RECONCILIATION_REQUIRED'})

    def test_cli_outputs_metadata_and_fixed_failure_reason_only(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(keys.sys, 'argv', ['root-key', 'provision', str(self.parent)]):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(keys.main(), 0)
        success = json.loads(output.getvalue())
        self.assertEqual(set(success), {'passed', 'metadata'})
        self.assertEqual(errors.getvalue(), '')
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(keys.sys, 'argv', ['root-key', 'provision', str(self.parent)]):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(keys.main(), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(json.loads(errors.getvalue()),
                         {'passed': False, 'reason': 'EXISTING_KEY_REFUSED'})


if __name__ == '__main__':
    unittest.main()
