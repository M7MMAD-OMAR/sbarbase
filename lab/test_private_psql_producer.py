"""Focused proposed baked regressions; no psql, Docker or private input.

Namespace fixtures use their real temporary FIFO objects and fd I/O. Only
UID/GID metadata for those exact objects is mapped to native 100:101, allowing
baked UID10001 tests without claiming real native ownership execution.
"""
import os
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy' / 'verify'))

import private_psql_producer as transport


class EncodingTests(unittest.TestCase):
    def test_all_printable_bytes_have_exact_single_hex_escapes(self):
        value = bytes(range(32, 127))
        expected = b"'" + b''.join(bytes((92, 120)) + ('%02x' % byte).encode() for byte in value) + b"'"
        self.assertEqual(transport.bind_ascii(value), expected)
        self.assertEqual(len(expected), 4 * len(value) + 2)

    def test_shell_and_psql_metacharacters_are_never_raw(self):
        self.assertEqual(transport.bind_ascii(bytes((39, 92, 96, 59, 36, 58))),
                         bytes.fromhex('275c7832375c7835635c7836305c7833625c7832345c78336127'))

    def test_exact_512_boundary_and_invalid_values_refuse(self):
        self.assertEqual(len(transport.bind_ascii(b'a' * 512)), 2050)
        for value in (b'', b'a' * 513, b'\n', b'\x00', b'\x7f', 'private', None):
            with self.subTest(kind=type(value).__name__, length=len(value) if value is not None else 0):
                with self.assertRaises(transport.TransportRefusal):
                    transport.bind_ascii(value)

    def test_template_preserves_public_fixed_bytes_and_bound_parameter(self):
        selector = transport.Selector(1, 1, 'MUST_TRUE_INVARIANT',
                                      bytes.fromhex('53454c4543542024310a5c62696e6420'),
                                      bytes.fromhex('0a5c670a'), True)
        self.assertEqual(selector.encode(b'A'),
                         bytes.fromhex('53454c4543542024310a5c62696e6420275c783431270a5c670a'))

    def test_fixed_selector_refuses_parameter_and_request_overflow(self):
        selector = transport.Selector(2, 2, 'CLOSED_OBJECT_STATE', b'SELECT true;\n')
        with self.assertRaises(transport.TransportRefusal):
            selector.encode(b'not allowed')
        with self.assertRaises(transport.TransportRefusal):
            transport.Selector(2, 2, 'CLOSED_OBJECT_STATE', b'x' * 8193).encode(None)


class ControlTests(unittest.TestCase):
    def fresh(self):
        control = transport.Control()
        control.feed(b'READY V2\n')
        return control

    def test_fragmented_exact_frames_and_normal_stop(self):
        control = self.fresh()
        control.begin(1)
        for byte in b'START 000001\nOPENED 000001\nDONE 000001 000\n':
            control.feed(bytes((byte,)))
        self.assertTrue(control.done)
        control.feed(b'STOPPED\nDRIVER_EXIT 000\n', stopping=True)
        self.assertTrue(control.driver_exit)

    def test_opened_before_start_refuses_irreversibly(self):
        control = self.fresh()
        control.begin(1)
        with self.assertRaises(transport.TransportRefusal):
            control.feed(b'OPENED 000001\n')
        with self.assertRaises(transport.TransportRefusal):
            control.feed(b'START 000001\n')

    def test_nonzero_done_and_wrong_sequence_refuse(self):
        for frame in (b'DONE 000001 124\n', b'DONE 000001 137\n', b'DONE 000002 000\n'):
            control = self.fresh()
            control.begin(1)
            control.feed(b'START 000001\nOPENED 000001\n')
            with self.assertRaises(transport.TransportRefusal):
                control.feed(frame)
            self.assertTrue(control.failed)

    def test_partial_frame_prevents_next_request_and_latches(self):
        control = self.fresh()
        control.feed(b'STA')
        with self.assertRaises(transport.TransportRefusal):
            control.begin(1)
        self.assertTrue(control.failed)

    def test_duplicate_ready_and_invalid_encoding_refuse(self):
        for frame in (b'READY V2\n', b'\x00', b'\r', b'\xff', b'x' * 129):
            control = self.fresh()
            with self.assertRaises(transport.TransportRefusal):
                control.feed(frame)
            self.assertTrue(control.failed)

    def test_next_sequence_cannot_start_before_done(self):
        control = self.fresh()
        control.begin(1)
        with self.assertRaises(transport.TransportRefusal):
            control.begin(2)
        self.assertTrue(control.failed)

    def test_boolean_and_out_of_range_sequences_refuse(self):
        for sequence in (True, 0, 257, 1.0):
            control = self.fresh()
            with self.assertRaises(transport.TransportRefusal):
                control.begin(sequence)
            self.assertTrue(control.failed)


class BudgetTests(unittest.TestCase):
    def test_actual_partial_counts_are_counted_once(self):
        budget = transport.TransferBudget()
        budget.record('S', 9, lifecycle=True)
        budget.begin(1)
        budget.record('Q', 1024)
        budget.record('Q', 7)
        budget.record('R', 2)
        self.assertEqual(budget.run_bytes, 1033)
        self.assertEqual(budget.channels['Q'], 1031)
        self.assertEqual(budget.lifecycle_bytes, 9)

    def test_first_stderr_byte_refuses_and_stays_failed(self):
        budget = transport.TransferBudget()
        budget.begin(1)
        with self.assertRaises(transport.TransportRefusal):
            budget.record('E', 1)
        with self.assertRaises(transport.TransportRefusal):
            budget.begin(2)

    def test_each_channel_boundary_plus_one_refuses(self):
        for channel, maximum in (('Q', 8192), ('R', 16), ('C', 128), ('S', 512)):
            budget = transport.TransferBudget()
            budget.begin(1)
            budget.record(channel, maximum)
            with self.assertRaises(transport.TransportRefusal):
                budget.record(channel, 1)
            self.assertTrue(budget.failed)

    def test_run_budget_cannot_reset_between_requests(self):
        budget = transport.TransferBudget()
        for sequence in range(1, 33):
            budget.begin(sequence)
            budget.record('Q', 8192)
        budget.begin(33)
        with self.assertRaises(transport.TransportRefusal):
            budget.record('Q', 1)

    def test_lifecycle_cap_and_invalid_count_refuse(self):
        budget = transport.TransferBudget()
        budget.record('C', 1024, lifecycle=True)
        with self.assertRaises(transport.TransportRefusal):
            budget.record('S', 1, lifecycle=True)
        for count in (-1, True, 1.0):
            with self.assertRaises(transport.TransportRefusal):
                transport.TransferBudget().record('S', count, lifecycle=True)


class NamespaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        os.chmod(self.path, 0o700)
        self.root = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.root)
        self.real_fstat, self.real_stat = os.fstat, os.stat
        def metadata(info):
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                                   st_uid=100, st_gid=101, st_nlink=info.st_nlink)
        def belongs(info):
            if info.st_ino == self.real_fstat(self.root).st_ino:
                return True
            for name in transport.NAMES:
                try:
                    value = self.real_stat(name, dir_fd=self.root, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (value.st_dev, value.st_ino) == (info.st_dev, info.st_ino):
                    return True
            return False
        def fstat(fd):
            value = self.real_fstat(fd)
            return metadata(value) if belongs(value) else value
        def local_stat(path, *args, **kwargs):
            value = self.real_stat(path, *args, **kwargs)
            return metadata(value) if kwargs.get('dir_fd') == self.root and path in transport.NAMES else value
        self.addCleanup(patch.stopall)
        patch.object(transport.os, 'fstat', side_effect=fstat).start()
        patch.object(transport.os, 'stat', side_effect=local_stat).start()

    def test_real_fifo_io_noninheritance_and_actual_eof(self):
        namespace = transport.Namespace(self.root)
        reader = namespace.open('R', os.O_RDONLY)
        writer = namespace.open('R', os.O_WRONLY)
        self.addCleanup(os.close, reader)
        self.assertFalse(os.get_inheritable(reader))
        self.assertFalse(os.get_inheritable(writer))
        os.write(writer, b't\n')
        self.assertEqual(os.read(reader, 16), b't\n')
        os.close(writer)
        self.assertEqual(os.read(reader, 16), b'')
        namespace.renew()

    def test_failed_admission_and_uncertain_close_latch_before_cleanup(self):
        namespace = transport.Namespace(self.root)
        self.assertIsNone(namespace.uncertain_close_fd)
        real_close = os.close
        attempts = []
        def uncertain_close(fd):
            attempts.append(fd)
            self.addCleanup(real_close, fd)
            self.assertTrue(namespace.failed)
            raise OSError('controlled fixture close uncertainty')
        with patch.object(transport.os, 'get_inheritable', return_value=True):
            with patch.object(transport.os, 'close', side_effect=uncertain_close):
                with self.assertRaises(transport.TransportRefusal) as refusal:
                    namespace.open('R', os.O_RDONLY)
        self.assertEqual(str(refusal.exception), 'ENDPOINT_ADMISSION_REFUSED')
        self.assertTrue(namespace.failed)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(namespace.uncertain_close_fd, attempts[0])
        with patch.object(transport.os, 'open') as later_open:
            with self.assertRaises(transport.TransportRefusal) as later_refusal:
                namespace.open('R', os.O_RDONLY)
        self.assertEqual(str(later_refusal.exception), 'ENDPOINT_REFUSED')
        later_open.assert_not_called()
        self.assertEqual(namespace.uncertain_close_fd, attempts[0])

    def test_existing_namespace_is_not_adopted(self):
        transport.Namespace(self.root)
        with self.assertRaises(transport.TransportRefusal):
            transport.Namespace(self.root)

    def test_fifo_replacement_is_rejected(self):
        namespace = transport.Namespace(self.root)
        os.mkfifo(self.path / 'replacement', 0o600)
        os.unlink(self.path / 'Q')
        os.rename(self.path / 'replacement', self.path / 'Q')
        with self.assertRaises(transport.TransportRefusal):
            namespace.renew()
        self.assertTrue(namespace.failed)

    def test_mode_drift_and_extra_entry_are_rejected(self):
        namespace = transport.Namespace(self.root)
        os.chmod(self.path / 'Q', 0o640)
        with self.assertRaises(transport.TransportRefusal):
            namespace.renew()
        self.assertTrue(namespace.failed)

    def test_private_readwrite_or_foreign_endpoint_is_rejected(self):
        namespace = transport.Namespace(self.root)
        for name, flags in (('Q', os.O_RDWR), ('R', os.O_RDWR), ('foreign', os.O_RDONLY)):
            with self.assertRaises(transport.TransportRefusal):
                namespace.open(name, flags)

    def test_root_permissions_are_not_overridden(self):
        os.chmod(self.path, 0o750)
        with self.assertRaises(transport.TransportRefusal):
            transport.Namespace(self.root)


class ClosureTests(unittest.TestCase):
    def test_one_close_failure_does_not_skip_other_owned_descriptors(self):
        attempts = []
        def close(fd):
            attempts.append(fd)
            if fd == 11:
                raise OSError('controlled fixture close failure')
        handles = {'one': 11, 'two': 12}
        with patch.object(transport.os, 'close', side_effect=close):
            with self.assertRaises(transport.TransportRefusal):
                transport.close_all(handles)
        self.assertEqual(attempts, [11, 12])
        self.assertEqual(handles, {})
