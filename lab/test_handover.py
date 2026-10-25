"""The in-place handover of the console process (lab/handover.py, docs/engineering/HANDOVER.md).

Unit tests with fixture /proc trees, real child processes and a real `execve`; no Docker and no
installation. The kernel and Bun mechanism of two console processes steering a port is
tests/handover-process.test.ts; the Promotion here is also driven against two real Bun processes
(RealProcessTests). What none of it shows is the whole thing on a server: that is the live gate."""
import concurrent.futures
import contextlib
import io
import json
import os
import signal
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import console_build_check
import dev
import handover
import kept_runtime
import release_channel
import test_upgrade
import upgrade
import upgrade_health

PYTHON = '/usr/bin/python3'
ROOT = Path(__file__).resolve().parents[1]
FROM = 'a' * 40
DEFAULT = object()


def quiet():
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return stack


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def immediately(function):
    """Runs a round at once, as the stand-in for the background thread."""
    future = concurrent.futures.Future()
    try:
        future.set_result(function())
    except BaseException as error:
        future.set_exception(error)
    return future


class Proc:
    """A fixture /proc tree: listening sockets in /proc/net/tcp and tcp6, and processes with their
    descriptors, resident memory and stat line."""
    HEADER = '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'

    def __init__(self, root):
        self.root = Path(root)
        (self.root / 'net').mkdir(parents=True, exist_ok=True)
        self.rows = {'tcp': [], 'tcp6': []}
        self.sync()

    def sync(self):
        for name, rows in self.rows.items():
            (self.root / 'net' / name).write_text(self.HEADER + ''.join(rows))

    def listen(self, host, port, inode, state='0A'):
        name, address = handover.hex_address(host)
        self.rows[name].append(f'   0: {address}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 00000000  1000        0 {inode} 1 0 100 0 0 10 0\n')
        self.sync()

    def unlisten(self, inode):
        for name in self.rows:
            self.rows[name] = [row for row in self.rows[name] if f' {inode} 1 ' not in row]
        self.sync()

    def process(self, pid, inodes=(), rss_kib=100_000, parent=1, started=1234):
        folder = self.root / str(pid)
        (folder / 'fd').mkdir(parents=True, exist_ok=True)
        for entry in (folder / 'fd').iterdir():
            entry.unlink()
        for number, inode in enumerate(inodes, 10):
            os.symlink(f'socket:[{inode}]', folder / 'fd' / str(number))
        (folder / 'status').write_text(f'Name:\tbun\nVmRSS:\t{rss_kib} kB\n')
        (folder / 'stat').write_text(f'{pid} (bun) S {parent} 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 {started}\n')
        return folder


def listeners_record(pid, names=(('console', '127.0.0.1', 7001), ('database', '127.0.0.1', 7002)), role='serving', frozen=True, steered=False,
                     protocol=handover.PROTOCOL):
    return {'pid': pid, 'protocol': protocol, 'role': role, 'frozen': frozen,
            'listeners': [{'name': name, 'host': host, 'port': port, 'steered': steered} for name, host, port in names]}


class Folder(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.state = self.folder / 'upstream'
        self.state.mkdir()
        self.proc = Proc(self.folder / 'proc')


class FormatTests(unittest.TestCase):
    def test_the_recorded_hash_is_the_hash_of_the_declared_required_keys(self):
        declared = handover.current_declared()
        self.assertEqual(handover.FORMATS[handover.PROTOCOL], handover.formats_hash(declared))

    def test_the_hash_covers_the_required_keys_their_types_and_meanings_only(self):
        def copy():
            return json.loads(json.dumps(handover.current_declared()))
        name = '.lab/upstream/management.json'
        base = handover.formats_hash(copy())

        def optional_added(d):
            d[name]['optional']['extra'] = {'type': 'string', 'meaning': 'a new optional key'}

        def scope_reworded(d):
            d[name]['scope'] = 'reworded'
        for change in (optional_added, scope_reworded):
            with self.subTest(change=change.__name__):
                declared = copy()
                change(declared)
                self.assertEqual(handover.formats_hash(declared), base)
        for change in (lambda d: d[name]['required']['auth'].update(type='number'),
                       lambda d: d[name]['required']['auth'].update(meaning='another meaning'),
                       lambda d: d[name]['required'].update(extra={'type': 'string', 'meaning': 'x'}),
                       lambda d: d[name]['required'].pop('auth'),
                       lambda d: d.pop(name)):
            with self.subTest(change=change):
                declared = copy()
                change(declared)
                self.assertNotEqual(handover.formats_hash(declared), base)

    def test_protocol_and_hash_are_read_as_text_from_exactly_one_line(self):
        text = Path(handover.__file__).read_text()
        self.assertEqual(handover.text_protocol(text), handover.PROTOCOL)
        self.assertEqual(handover.text_format_hash(text, handover.PROTOCOL), handover.FORMATS[handover.PROTOCOL])
        for broken in (text + '\nPROTOCOL = 2\n', text.replace('PROTOCOL = 1\n', '', 1), text.replace('PROTOCOL = 1', 'PROTOCOL = one')):
            with self.subTest(broken=broken[-30:]):
                with self.assertRaises(handover.Refused):
                    handover.text_protocol(broken)
        with self.assertRaises(handover.Refused):
            handover.text_format_hash(text, 9)
        with self.assertRaises(handover.Refused):
            handover.text_format_hash(text.replace("    1: '", "    1: '0", 1), 1)
        twice = text.replace("    1: '" + handover.FORMATS[1] + "',\n", "    1: '" + handover.FORMATS[1] + "',\n    1: '" + '0' * 64 + "',\n", 1)
        self.assertNotEqual(twice, text)
        with self.assertRaises(handover.Refused):
            handover.text_format_hash(twice, 1)
        # A comment or a docstring that mentions the line does not count as a second one.
        self.assertEqual(handover.text_protocol('# PROTOCOL = 5 in a comment\nPROTOCOL = 3\n'), 3)

    def test_a_target_may_only_add_optional_keys(self):
        current = handover.current_declared()
        name = '.lab/upstream/endpoints.json'
        same = json.loads(json.dumps(current))
        self.assertTrue(handover.declares_all(current, same))
        added = json.loads(json.dumps(current))
        added[name]['optional']['quota'] = {'type': 'number', 'meaning': 'new'}
        self.assertTrue(handover.declares_all(current, added))
        for label, change in (
                ('an optional key changed type', lambda d: d[name]['optional']['storage'].update(type='string')),
                ('an optional key changed meaning', lambda d: d[name]['optional']['storage'].update(meaning='other')),
                ('an optional key removed', lambda d: d[name]['optional'].pop('storage')),
                ('a required key changed type', lambda d: d[name]['required']['auth'].update(type='object')),
                ('a required key removed', lambda d: d[name]['required'].pop('rest')),
                ('a required key added', lambda d: d[name]['required'].update(quota={'type': 'number', 'meaning': 'x'})),
                ('a file no longer declared', lambda d: d.pop(name)),
        ):
            with self.subTest(label):
                target = json.loads(json.dumps(current))
                change(target)
                self.assertFalse(handover.declares_all(current, target))
        self.assertFalse(handover.declares_all(current, []))
        self.assertFalse(handover.declares_all(current, {**current, name: 'text'}))

    def test_the_declaration_names_the_files_both_processes_read_while_they_overlap(self):
        self.assertEqual(set(handover.current_declared()), {
            '.lab/upstream/endpoints.json', '.lab/upstream/database.json', '.lab/upstream/studio.json',
            '.lab/upstream/write-pauses/e_*.json', '.lab/upstream/management.json', '.secrets/upstream/runtime.json'})


class FileTests(Folder):
    def test_a_record_is_private_whole_and_leaves_no_pending_file(self):
        path = self.state / 'handover.json'
        handover.write_json(path, {'a': 1})
        self.assertEqual(json.loads(path.read_text()), {'a': 1})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(sorted(entry.name for entry in self.state.iterdir()), ['handover.json'])
        handover.write_json(path, {'a': 2})
        self.assertEqual(json.loads(path.read_text()), {'a': 2})

    def test_the_record_is_durable_before_and_after_the_rename(self):
        order = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(descriptor):
            order.append('dir' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file')
            return real_fsync(descriptor)

        def replace(source, target):
            order.append('rename')
            return real_replace(source, target)
        with patch.object(handover.os, 'fsync', fsync), patch.object(handover.os, 'replace', replace):
            handover.write_json(self.state / 'handover.json', {'a': 1})
        self.assertEqual(order, ['file', 'rename', 'dir'])

    def test_a_host_that_is_not_an_address_is_a_refusal_in_every_check_not_an_os_error(self):
        # H2 and H4 catch only Refused: an OSError would take the way back of a healthy release.
        record = {'listeners': [{'name': 'database-external', 'host': 'nope.invalid', 'port': 6545}]}
        for call in (lambda: handover.listening('nope.invalid', 6545, self.state), lambda: handover.clean_group(record, 1, self.state),
                     lambda: handover.group_state(record, record, {}, 1, 1, self.state)):
            with self.assertRaises(handover.Refused):
                call()

    def test_a_pending_file_cannot_be_a_link_someone_planted(self):
        victim = self.folder / 'victim'
        victim.write_text('keep')
        os.symlink(victim, self.state / 'handover.json.pending')
        with self.assertRaises(OSError):
            handover.write_json(self.state / 'handover.json', {'a': 1})
        self.assertEqual(victim.read_text(), 'keep')

    def test_a_failed_handover_is_remembered_for_its_commit_only(self):
        self.assertFalse(handover.failed_for('b' * 40, self.state))
        handover.write_failed('b' * 40, 'H4', 'a third member', self.state, now=5)
        self.assertEqual(json.loads((self.state / 'handover-failed.json').read_text()),
                         {'to': 'b' * 40, 'step': 'H4', 'reason': 'a third member', 'at': 5})
        self.assertEqual(os.stat(self.state / 'handover-failed.json').st_mode & 0o777, 0o600)
        self.assertTrue(handover.failed_for('b' * 40, self.state))
        self.assertFalse(handover.failed_for('c' * 40, self.state))
        (self.state / 'handover-failed.json').write_text('not json')
        self.assertFalse(handover.failed_for('b' * 40, self.state))

    def test_the_wait_for_the_old_process_follows_the_drain_deadline(self):
        self.assertAlmostEqual(handover.retire_wait_seconds({}), 20.3)
        self.assertAlmostEqual(handover.retire_wait_seconds({'SBARBASE_DRAIN_MS': '19000'}), 24.3)
        self.assertAlmostEqual(handover.retire_wait_seconds({'SBARBASE_DRAIN_MS': '0'}), 5.3)
        # A value the process refuses is the default for it too.
        self.assertAlmostEqual(handover.retire_wait_seconds({'SBARBASE_DRAIN_MS': '19001'}), 20.3)
        self.assertAlmostEqual(handover.retire_wait_seconds({'SBARBASE_DRAIN_MS': 'x'}), 20.3)

    def test_the_admission_credit_is_a_plain_number_or_nothing(self):
        self.assertEqual(handover.credit_mib({handover.CREDIT: '350'}), 350)
        for value in (None, '', '-5', '3.5', 'x', '1e3', ' 5', '12345678', '\u00b2', '+5', '5\n'):
            self.assertEqual(handover.credit_mib({handover.CREDIT: value} if value is not None else {}), 0)

    def test_the_switch_is_exactly_one(self):
        self.assertTrue(handover.enabled({'SBARBASE_HANDOVER': '1'}))
        for value in ('', '0', 'true', 'yes', '11'):
            self.assertFalse(handover.enabled({'SBARBASE_HANDOVER': value}))
        self.assertFalse(handover.enabled({}))


class AddressTests(unittest.TestCase):
    def test_addresses_are_printed_as_proc_net_prints_them(self):
        self.assertEqual(handover.hex_address('127.0.0.1'), ('tcp', '0100007F'))
        self.assertEqual(handover.hex_address('0.0.0.0'), ('tcp', '00000000'))
        self.assertEqual(handover.hex_address('10.20.30.40'), ('tcp', '281E140A'))
        self.assertEqual(handover.hex_address('::'), ('tcp6', '0' * 32))
        self.assertEqual(handover.hex_address('::1'), ('tcp6', '0' * 24 + '01000000'))
        self.assertEqual(handover.hex_address('fe80::1:2'), ('tcp6', '000080FE000000000000000002000100'))
        self.assertEqual(handover.hex_address('localhost'), ('tcp', '0100007F'))
        for bad in ('nope', '', '1.2.3', 'fe80::g', None):
            with self.subTest(bad=bad), self.assertRaises(handover.Refused):
                handover.hex_address(bad)

    def test_the_real_kernel_prints_what_the_helper_expects(self):
        for family, host in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
            try:
                listener = socket.socket(family)
                listener.bind((host, 0))
            except OSError:
                self.skipTest(f'{host} is not available')
            listener.listen(1)
            self.addCleanup(listener.close)
            port = listener.getsockname()[1]
            inodes = handover.listening(host, port)
            self.assertEqual(len(inodes), 1)
            self.assertIn(inodes[0], handover.descriptors(os.getpid()))


class GroupTests(Folder):
    def test_a_clean_group_is_one_listening_socket_that_the_process_holds(self):
        self.proc.listen('127.0.0.1', 7001, 501)
        self.proc.listen('127.0.0.1', 7002, 502)
        self.proc.process(4001, [501, 502])
        self.assertEqual(handover.clean_group(listeners_record(4001), 4001, self.proc.root), {'console': 501, 'database': 502})

    def test_every_other_group_is_refused(self):
        record = listeners_record(4001)
        cases = {
            'a listener nobody holds': lambda: (self.proc.process(4001, [502]),),
            'a socket another process holds': lambda: (self.proc.process(4001, [502]), self.proc.process(4003, [501])),
            'two sockets on the address': lambda: (self.proc.listen('127.0.0.1', 7001, 503), self.proc.process(4001, [501, 502, 503])),
            'a socket that is not listening': lambda: (self.proc.unlisten(501), self.proc.listen('127.0.0.1', 7001, 501, state='01')),
            'no process directory': lambda: (__import__('shutil').rmtree(self.proc.root / '4001'),),
        }
        for label, setup in cases.items():
            with self.subTest(label):
                self.proc = Proc(self.folder / ('proc-' + label.replace(' ', '-')))
                self.proc.listen('127.0.0.1', 7001, 501)
                self.proc.listen('127.0.0.1', 7002, 502)
                self.proc.process(4001, [501, 502])
                setup()
                with self.assertRaises(handover.Refused):
                    handover.clean_group(record, 4001, self.proc.root)

    def test_a_malformed_record_is_refused(self):
        self.proc.process(4001)
        for record in ({}, {'listeners': 'x'}, {'listeners': [{'name': 'a', 'host': 'h', 'port': '1'}]},
                       {'listeners': [{'name': 'a', 'host': '127.0.0.1', 'port': 1}, {'name': 'a', 'host': '127.0.0.1', 'port': 2}]}, []):
            with self.subTest(record=record), self.assertRaises(handover.Refused):
                handover.clean_group(record, 4001, self.proc.root)


class Shared(Folder):
    """Two Bun processes in the same groups, as H4 finds them after the promotion."""

    def build(self):
        self.proc = Proc(self.folder / 'shared')
        for host, port, old, new in (('127.0.0.1', 7001, 501, 601), ('127.0.0.1', 7002, 502, 602)):
            self.proc.listen(host, port, old)
            self.proc.listen(host, port, new)
        self.proc.process(4001, [501, 502])
        self.proc.process(4002, [601, 602])
        self.old = listeners_record(4001)
        self.new = listeners_record(4002, role='candidate', steered=True)
        self.inode_old = {'console': 501, 'database': 502}

    def group(self):
        return handover.group_state(self.old, self.new, self.inode_old, 4001, 4002, self.proc.root)

    def test_two_members_one_of_them_the_old_one_is_the_group_that_was_proved(self):
        self.build()
        self.assertEqual(self.group(), {'console': (501, 601), 'database': (502, 602)})

    def test_a_listener_only_the_candidate_has_is_one_socket_the_candidate_holds(self):
        self.build()
        self.proc.listen('127.0.0.1', 7003, 603)
        self.proc.process(4002, [601, 602, 603])
        self.new['listeners'].append({'name': 'studio-internal', 'host': '127.0.0.1', 'port': 7003, 'steered': False})
        self.assertEqual(self.group()['studio-internal'], (None, 603))

    def test_a_listener_only_the_old_process_has_closes_with_it_and_is_not_checked(self):
        self.build()
        self.new['listeners'] = [item for item in self.new['listeners'] if item['name'] != 'database']
        self.assertEqual(self.group(), {'console': (501, 601)})

    def test_every_other_state_is_refused(self):
        def third(self_):
            self_.proc.listen('127.0.0.1', 7001, 700)
            self_.proc.process(4003, [700])

        def foreign_replaces_candidate(self_):
            self_.proc.unlisten(601)
            self_.proc.listen('127.0.0.1', 7001, 700)
            self_.proc.process(4003, [700])

        def swapped(self_):
            self_.proc.process(4001, [601, 502])
            self_.proc.process(4002, [501, 602])

        def old_left(self_):
            self_.proc.unlisten(501)

        def old_inode_changed(self_):
            self_.proc.unlisten(501)
            self_.proc.listen('127.0.0.1', 7001, 510)
            self_.proc.process(4001, [510, 502])

        def candidate_only(self_):
            self_.proc.process(4002, [602])

        def old_socket_held_by_another(self_):
            self_.proc.process(4001, [502])
            self_.proc.process(4003, [501])

        def only_one(self_):
            self_.proc.unlisten(601)
            self_.proc.process(4002, [602])

        def moved(self_):
            self_.new['listeners'][0]['port'] = 7009

        def candidate_only_shared(self_):
            self_.new['listeners'].append({'name': 'studio-internal', 'host': '127.0.0.1', 'port': 7003, 'steered': False})
            self_.proc.listen('127.0.0.1', 7003, 603)
            self_.proc.listen('127.0.0.1', 7003, 604)
            self_.proc.process(4002, [601, 602, 603])

        for label, change in (('a third member', third), ('a foreign member in place of the candidate', foreign_replaces_candidate),
                              ('inodes swapped between the processes', swapped), ('the old socket gone', old_left),
                              ('the old socket now held by another process', old_socket_held_by_another),
                              ('the old socket replaced by another', old_inode_changed), ('the candidate holds one only', candidate_only),
                              ('only one socket', only_one), ('a listener moved', moved),
                              ('two sockets on a candidate only listener', candidate_only_shared)):
            with self.subTest(label):
                self.build()
                change(self)
                with self.assertRaises(handover.Refused):
                    self.group()


class SteeringTests(unittest.TestCase):
    def test_the_program_attaches_and_every_new_connection_goes_to_the_second_member(self):
        self.assertTrue(handover.steering_works())

    def test_the_trial_looks_at_enough_connections_that_a_kernel_which_ignores_the_program_is_found_out(self):
        self.assertGreaterEqual(handover.STEERING_TRIAL_CONNECTIONS, 32)

    def test_the_program_is_one_instruction_in_a_sixteen_byte_fprog(self):
        program, keep = handover.steering_program()
        self.assertEqual(len(program), 16)
        self.assertEqual(int.from_bytes(program[:2], 'little'), 1)
        self.assertEqual(int.from_bytes(program[8:], 'little'), __import__('ctypes').addressof(keep))
        self.assertEqual((keep.code, keep.jt, keep.jf, keep.k), (0x06, 0, 0, 1))
        self.assertEqual((handover.SO_ATTACH_REUSEPORT_CBPF, handover.SO_DETACH_REUSEPORT_BPF, handover.SOL_SOCKET), (51, 68, 1))

    def test_a_kernel_that_refuses_the_option_is_reported_as_not_working(self):
        real = socket.socket.setsockopt

        def refuse(self_, level, option, value, *rest):
            if option == handover.SO_ATTACH_REUSEPORT_CBPF:
                raise PermissionError('seccomp')
            return real(self_, level, option, value, *rest)
        with patch.object(socket.socket, 'setsockopt', refuse):
            self.assertFalse(handover.steering_works())

    def test_without_the_program_connections_are_not_all_the_second_members(self):
        # The trial would pass for a kernel that ignores the attach if it did not look at both members.
        real = socket.socket.setsockopt

        def ignore(self_, level, option, value, *rest):
            if option == handover.SO_ATTACH_REUSEPORT_CBPF:
                return None
            return real(self_, level, option, value, *rest)
        with patch.object(socket.socket, 'setsockopt', ignore):
            results = {handover.steering_works(wait=1) for _ in range(3)}
        self.assertEqual(results, {False})

    def test_the_detach_form_is_the_four_byte_int_that_the_kernel_accepts(self):
        first = socket.socket()
        first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        first.bind(('127.0.0.1', 0))
        first.listen(1)
        self.addCleanup(first.close)
        program, keep = handover.steering_program()
        first.setsockopt(handover.SOL_SOCKET, handover.SO_ATTACH_REUSEPORT_CBPF, program)
        handover.detach_works(first)
        with self.assertRaises(OSError) as raised:
            handover.detach_works(first)
        self.assertEqual(raised.exception.errno, 2)
        # The reviewer's measurement: a missing or short option is EINVAL before the option is read.
        with self.assertRaises(OSError) as raised:
            first.setsockopt(handover.SOL_SOCKET, handover.SO_DETACH_REUSEPORT_BPF, None, 0)
        self.assertEqual(raised.exception.errno, 22)
        del keep


class MemoryTests(Folder):
    def meminfo(self, kib):
        path = self.folder / 'meminfo'
        path.write_text(f'MemTotal: 99999999 kB\nMemAvailable: {kib} kB\n')
        return path

    def cgroup(self, maximum, current):
        (self.folder / 'cg').mkdir(exist_ok=True)
        (self.folder / 'cg/memory.max').write_text(maximum + '\n')
        (self.folder / 'cg/memory.current').write_text(f'{current}\n')
        (self.folder / 'cgroup').write_text('0::/cg\n')
        return {'cgroup_file': self.folder / 'cgroup', 'cgroup_root': self.folder}

    def test_the_host_needs_the_old_processes_memory_plus_the_margin(self):
        need = 200 + handover.MEMORY_MARGIN_MIB
        files = self.cgroup('max', 0)
        self.assertIsNone(handover.memory_refusal(200, self.meminfo(need * 1024), **files))
        self.assertIn('MemAvailable', handover.memory_refusal(200, self.meminfo(need * 1024 - 1024), **files))

    def test_a_limited_cgroup_needs_the_room_too(self):
        need = 200 + handover.MEMORY_MARGIN_MIB
        meminfo = self.meminfo(10_000_000)
        self.assertIsNone(handover.memory_refusal(200, meminfo, **self.cgroup(str(2 << 30), 1 << 30)))
        reason = handover.memory_refusal(200, meminfo, **self.cgroup(str((need + 100) << 20), 101 << 20))
        self.assertIn('cgroup', reason)
        self.assertIsNone(handover.memory_refusal(200, meminfo, **self.cgroup(str((need + 101) << 20), 100 << 20)))

    def test_an_unreadable_cgroup_is_no_limit_and_an_unreadable_meminfo_is_a_refusal(self):
        meminfo = self.meminfo(10_000_000)
        self.assertIsNone(handover.memory_refusal(200, meminfo, cgroup_file=self.folder / 'none', cgroup_root=self.folder))
        (self.folder / 'bare').write_text('MemTotal: 5 kB\n')
        self.assertIn('cannot be read', handover.memory_refusal(200, self.folder / 'bare', cgroup_file=self.folder / 'none'))

    def test_the_old_processes_resident_memory_is_read_from_its_status(self):
        self.proc.process(4001, rss_kib=2049)
        self.assertEqual(handover.vm_rss_mib(4001, self.proc.root), 3)
        with self.assertRaises(handover.Refused):
            handover.vm_rss_mib(4999, self.proc.root)


class Qualification(Folder):
    """Q1 to Q12 against a throwaway repository, fixture /proc and state, with the release channel
    and the console build check stood in for."""

    def setUp(self):
        super().setUp()
        self.repo = self.folder / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'test')
        self.commit_files()
        self.to = self.git('rev-parse', 'HEAD')
        self.upgrade_state = self.folder / 'state.json'
        self.reset()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit_files(self, handover_text=None, formats=None):
        (self.repo / 'lab').mkdir(exist_ok=True)
        (self.repo / 'src/control').mkdir(parents=True, exist_ok=True)
        (self.repo / 'lab/handover.py').write_text(handover_text if handover_text is not None else Path(handover.__file__).read_text())
        (self.repo / 'src/control/handover-formats.json').write_text(
            (formats if isinstance(formats, str) else json.dumps(formats)) if formats is not None
            else (ROOT / 'src/control/handover-formats.json').read_text())
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'files')
        return self.git('rev-parse', 'HEAD')

    def reset(self):
        """Everything holds: Q1 to Q9, Q11 and Q12."""
        self.environ = {'SBARBASE_HANDOVER': '1', 'SBARBASE_GUARDED': '1', 'SBARBASE_CONSOLE_PORT': '7001'}
        self.asked = threading.Event()
        self.server = SimpleNamespace(pid=4001)
        self.supervisor = SimpleNamespace(server=self.server, head=FROM)
        self.exited = None
        self.upgrade_state.write_text(json.dumps({'phase': 'applied', 'moved': True, 'from': FROM, 'to': self.to, 'changes': [],
                                                  'release': {'class': 'safe', 'version': '1.2.3'}}))
        (self.state / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:7001', 'pid': 4001, 'protocol': handover.PROTOCOL}))
        (self.state / 'listeners-4001.json').write_text(json.dumps(listeners_record(4001, names=(('console', '127.0.0.1', 7001),))))
        self.proc = Proc(self.folder / 'qproc')
        self.proc.listen('127.0.0.1', 7001, 501)
        self.proc.process(4001, [501], rss_kib=200 * 1024)
        self.meminfo = self.folder / 'meminfo'
        self.meminfo.write_text('MemAvailable: 8000000 kB\n')
        self.trial = lambda: True
        self.fresh = (True, 'fresh')
        self.patches = [patch.object(release_channel, 'pin_diffs', side_effect=lambda a, b: iter(self.pins)),
                        patch.object(release_channel, 'classify', side_effect=lambda a, b: (self.kind, [])),
                        patch.object(release_channel, 'stores_changed', side_effect=lambda a, b: self.stores),
                        patch.object(console_build_check, 'is_fresh', side_effect=lambda out=None: self.fresh if out is not None else self.fresh_here)]
        self.pins, self.kind, self.stores, self.fresh_here = [], 'safe', False, (True, 'fresh')
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def why(self, kind='apply', completed=True, head=DEFAULT, **override):
        arguments = dict(environ=self.environ, root=self.repo, state=self.state, upgrade_state=self.upgrade_state, proc=self.proc.root,
                         meminfo=self.meminfo, cgroup={'cgroup_file': self.folder / 'no-cgroup', 'cgroup_root': self.folder}, trial=self.trial)
        arguments.update(override)
        return handover.refusal(self.supervisor, kind, completed, self.asked, lambda process: self.exited,
                                self.to if head is DEFAULT else head, **arguments)

    def state_record(self, **change):
        record = json.loads(self.upgrade_state.read_text())
        record.update(change)
        self.upgrade_state.write_text(json.dumps(record))

    def test_every_condition_holding_gives_the_plan_that_names_the_old_process_and_its_listeners(self):
        reason, plan = self.why()
        self.assertIsNone(reason)
        self.assertIs(plan.server, self.server)
        self.assertEqual(plan.to, self.to)
        self.assertEqual(plan.record, {'protocol': handover.PROTOCOL, 'from': FROM, 'to': self.to, 'pid': 4001, 'console_port': 7001,
                                       'listeners': [{'name': 'console', 'host': '127.0.0.1', 'port': 7001, 'steered': False}]})

    def refused(self, prefix, **override):
        reason, plan = self.why(**override)
        self.assertIsNone(plan)
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(prefix + ':'), reason)
        return reason

    def test_q1_the_switch_an_upgrade_and_its_completion(self):
        for value in (None, '', '0', 'true'):
            with self.subTest(switch=value):
                self.environ.pop('SBARBASE_HANDOVER', None)
                if value is not None:
                    self.environ['SBARBASE_HANDOVER'] = value
                self.refused('Q1')
        self.environ['SBARBASE_HANDOVER'] = '1'
        for kind in ('rollback', 'check', 'anything'):
            self.refused('Q1', kind=kind)
        self.refused('Q1', completed=False)

    def test_q2_a_service_manager_no_stop_and_no_recovery_target(self):
        for value in (None, '', '0'):
            with self.subTest(guarded=value):
                self.environ.pop('SBARBASE_GUARDED', None)
                if value is not None:
                    self.environ['SBARBASE_GUARDED'] = value
                self.refused('Q2')
        self.environ['SBARBASE_GUARDED'] = '1'
        self.asked.set()
        self.refused('Q2')
        self.asked.clear()
        (self.state / 'cutover-operation.json').write_text('{}')
        self.refused('Q2')

    def test_q11_a_commit_the_handover_failed_for_is_never_tried_again(self):
        handover.write_failed(self.to, 'H4', 'x', self.state)
        self.refused('Q11')
        (self.state / 'handover-failed.json').unlink()
        handover.write_failed('f' * 40, 'H4', 'x', self.state)
        self.assertIsNone(self.why()[0])
        self.refused('Q11', head=None)

    def test_q3_the_target_declares_the_same_protocol_formats_and_keys(self):
        text = Path(handover.__file__).read_text()
        declared = json.loads((ROOT / 'src/control/handover-formats.json').read_text())
        name = '.lab/upstream/endpoints.json'
        cases = {
            'no handover module': lambda: self.git('rm', '-q', 'lab/handover.py') or self.git('commit', '-q', '-m', 'x'),
            'another protocol': lambda: self.commit_files(text.replace('PROTOCOL = 1\n', 'PROTOCOL = 2\n', 1)),
            'two protocol lines': lambda: self.commit_files(text + '\nPROTOCOL = 1\n'),
            'a hash edited in place': lambda: self.commit_files(text.replace(handover.FORMATS[1], '0' * 64)),
            'a hash line missing': lambda: self.commit_files(text.replace("    1: '", "    7: '", 1)),
            'no formats file': lambda: self.git('rm', '-q', 'src/control/handover-formats.json') or self.git('commit', '-q', '-m', 'x'),
            'an unreadable formats file': lambda: self.commit_files(formats='{'),
        }
        changed = json.loads(json.dumps(declared))
        changed[name]['optional']['storage']['meaning'] = 'reworded'
        cases['an optional key changed past the hash'] = lambda: self.commit_files(formats=changed)
        required = json.loads(json.dumps(declared))
        required[name]['required']['quota'] = {'type': 'number', 'meaning': 'new'}
        cases['a required key added'] = lambda: self.commit_files(formats=required)
        for label, make in cases.items():
            with self.subTest(label):
                self.git('reset', '-q', '--hard', self.to)
                make()
                head = self.git('rev-parse', 'HEAD')
                self.state_record(to=head)
                try:
                    reason, plan = self.why(head=head)
                except (handover.Refused, ValueError):
                    continue
                self.assertIsNone(plan)
                self.assertTrue(reason.startswith('Q3:'), reason)
        self.git('reset', '-q', '--hard', self.to)
        # A release that only adds an optional key is handed over to.
        added = json.loads(json.dumps(declared))
        added[name]['optional']['quota'] = {'type': 'number', 'meaning': 'new'}
        head = self.commit_files(formats=added)
        self.state_record(to=head)
        self.assertIsNone(self.why(head=head)[0])

    def test_q3_failure_to_read_the_target_is_a_refusal_through_qualifies(self):
        self.git('rm', '-q', 'lab/handover.py')
        self.git('commit', '-q', '-m', 'x')
        with quiet():
            plan = handover.qualifies(self.supervisor, 'apply', True, self.asked, lambda process: None, head=self.git('rev-parse', 'HEAD'),
                                      environ=self.environ, root=self.repo, state=self.state, upgrade_state=self.upgrade_state,
                                      proc=self.proc.root, meminfo=self.meminfo, trial=self.trial)
        self.assertIsNone(plan)

    def test_q4_a_code_only_move_to_this_commit_from_the_commit_this_supervisor_runs(self):
        cases = {
            'not applied': dict(phase='rolling_back'), 'not moved': dict(moved=False), 'moved unset': dict(moved='yes'),
            'another target': dict(to='f' * 40), 'another source': dict(**{'from': 'f' * 40}), 'changes listed': dict(changes=[{'image': 'x'}]),
            'changes missing': dict(changes=None), 'a release class that is not safe': dict(release={'class': 'attended'}),
            'a release class that is unknown': dict(release={'class': 'weird'}), 'a class of null': dict(release={'class': None}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.reset()
                if label == 'changes missing':
                    record = json.loads(self.upgrade_state.read_text())
                    record.pop('changes')
                    self.upgrade_state.write_text(json.dumps(record))
                else:
                    self.state_record(**change)
                self.refused('Q4')
        self.reset()
        self.upgrade_state.write_text('not json')
        self.refused('Q4')
        self.upgrade_state.unlink()
        self.refused('Q4')

    def test_q4_a_release_that_carries_no_class_or_a_safe_one_is_fine_but_nothing_else_is(self):
        record = json.loads(self.upgrade_state.read_text())
        record.pop('release')
        self.upgrade_state.write_text(json.dumps(record))
        self.assertIsNone(self.why()[0])
        self.state_record(release={'version': '1.2.3'})
        self.assertIsNone(self.why()[0])

    def test_q4_pins_classes_and_stores_exclude_the_handover(self):
        self.pins = [('images.lock.json', 'rest', {}, {})]
        self.refused('Q4')
        self.pins = []
        for kind in ('attended', 'rebuild', 'manual', 'unknown', ''):
            self.kind = kind
            with self.subTest(kind=kind):
                self.refused('Q4')
        self.kind = 'safe'
        self.stores = True
        self.refused('Q4')
        self.stores = False
        self.assertIsNone(self.why()[0])

    def test_q4_reads_the_state_without_importing_the_module_the_move_replaced(self):
        self.assertNotIn('import upgrade', Path(handover.__file__).read_text().replace('import upgrade_health', ''))

    def test_q5_the_console_port_is_pinned(self):
        for value in (None, '', '0', '00', 'abc', '70.1', '-7001', '700000'):
            with self.subTest(port=value):
                self.environ.pop('SBARBASE_CONSOLE_PORT', None)
                if value is not None:
                    self.environ['SBARBASE_CONSOLE_PORT'] = value
                self.refused('Q5')

    def test_q6_the_running_version_declares_the_protocol(self):
        self.exited = 0
        self.refused('Q6')
        self.exited = None
        self.supervisor.server = None
        self.refused('Q6')
        self.supervisor.server = self.server
        port = json.dumps({'url': 'http://127.0.0.1:7001', 'pid': 4001, 'protocol': handover.PROTOCOL})
        for label, text in (('server.json names another pid', port.replace('4001', '4009')),
                            ('server.json declares no protocol', json.dumps({'url': 'x', 'pid': 4001})),
                            ('server.json declares another protocol', port.replace(f'"protocol": {handover.PROTOCOL}', '"protocol": 9')),
                            ('server.json is not json', '{'), ('server.json is a list', '[]')):
            with self.subTest(label):
                (self.state / 'server.json').write_text(text)
                self.refused('Q6')
        (self.state / 'server.json').write_text(port)
        good = listeners_record(4001, names=(('console', '127.0.0.1', 7001),))
        for label, record in (('another pid', {**good, 'pid': 4009}), ('another protocol', {**good, 'protocol': 9}),
                              ('a candidate record', {**good, 'role': 'candidate'}), ('no role', {k: v for k, v in good.items() if k != 'role'})):
            with self.subTest(label):
                (self.state / 'listeners-4001.json').write_text(json.dumps(record))
                self.refused('Q6')
        (self.state / 'listeners-4001.json').unlink()
        self.refused('Q6')

    def test_q7_the_old_process_is_alone_in_each_group(self):
        self.proc.listen('127.0.0.1', 7001, 777)
        self.proc.process(4003, [777])
        self.refused('Q7')
        self.proc.unlisten(777)
        self.proc.unlisten(501)
        self.refused('Q7')
        self.proc.listen('127.0.0.1', 7001, 501)
        self.proc.process(4001, [])
        self.refused('Q7')

    def test_q8_steering_has_to_work_on_this_host(self):
        self.trial = lambda: False
        self.refused('Q8')

    def test_q8_is_tried_by_the_real_two_socket_trial_by_default(self):
        calls = []
        with patch.object(handover, 'steering_works', side_effect=lambda: calls.append(1) or True):
            self.assertIsNone(self.why(trial=None)[0])
        self.assertEqual(calls, [1])

    def test_q9_room_for_two_bun_processes(self):
        self.meminfo.write_text(f'MemAvailable: {(200 + handover.MEMORY_MARGIN_MIB - 1) * 1024} kB\n')
        self.refused('Q9')
        self.meminfo.write_text(f'MemAvailable: {(200 + handover.MEMORY_MARGIN_MIB) * 1024} kB\n')
        self.assertIsNone(self.why()[0])
        (self.folder / 'cg').mkdir()
        (self.folder / 'cg/memory.max').write_text(f'{300 << 20}\n')
        (self.folder / 'cg/memory.current').write_text(f'{200 << 20}\n')
        (self.folder / 'cgroup').write_text('0::/cg\n')
        self.refused('Q9', cgroup={'cgroup_file': self.folder / 'cgroup', 'cgroup_root': self.folder})

    def test_q9_an_old_process_whose_memory_cannot_be_read_is_a_refusal_not_a_guess(self):
        (self.proc.root / '4001/status').unlink()
        with self.assertRaises(handover.Refused):
            self.why()

    def test_q12_the_start_builds_nothing(self):
        self.fresh, self.fresh_here = (False, 'stale'), (True, 'fresh')
        self.assertIsNone(self.why()[0])
        self.fresh, self.fresh_here = (True, 'staged'), (False, 'stale')
        self.assertIsNone(self.why()[0])
        self.fresh, self.fresh_here = (False, 'stale'), (False, 'stale')
        self.refused('Q12')

    def test_the_staged_build_is_asked_about_by_its_own_folder(self):
        seen = []
        with patch.object(console_build_check, 'is_fresh', side_effect=lambda out=None: seen.append(out) or (False, 'x')):
            self.why()
        self.assertEqual(seen, [console_build_check.STAGED, None])

    def test_qualifies_returns_nothing_and_says_why_when_a_condition_fails(self):
        self.environ['SBARBASE_CONSOLE_PORT'] = '0'
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            plan = handover.qualifies(self.supervisor, 'apply', True, self.asked, lambda process: None, head=self.to, environ=self.environ,
                                      root=self.repo, state=self.state, upgrade_state=self.upgrade_state, proc=self.proc.root,
                                      meminfo=self.meminfo, trial=self.trial)
        self.assertIsNone(plan)
        self.assertIn('Q5', out.getvalue())

    def test_qualifies_counts_any_exception_as_a_condition_that_does_not_hold(self):
        with quiet():
            for broken in (lambda: setattr(self.supervisor, 'head', None), lambda: self.proc.process(4001, [])):
                broken()
                self.assertIsNone(handover.qualifies(self.supervisor, 'apply', True, self.asked, lambda process: None, head=self.to,
                                                     environ=self.environ, root=self.repo, state=self.state, upgrade_state=self.upgrade_state,
                                                     proc=self.proc.root, meminfo=self.meminfo, trial=self.trial))
            with patch.object(handover, 'refusal', side_effect=RuntimeError('boom')):
                self.assertIsNone(handover.qualifies(self.supervisor, 'apply', True, self.asked, lambda process: None, head=self.to,
                                                     environ=self.environ))

    def test_with_the_switch_off_nothing_is_read_at_all(self):
        with patch.object(handover, 'refusal', side_effect=AssertionError('read')), patch.object(handover, 'supervisor_head', side_effect=AssertionError('read')):
            self.assertIsNone(handover.qualifies(self.supervisor, 'apply', True, self.asked, lambda process: None, environ={}))

    def test_a_plan_is_returned_by_qualifies_when_everything_holds(self):
        with quiet():
            plan = handover.qualifies(self.supervisor, 'apply', True, self.asked, lambda process: None, head=self.to, environ=self.environ,
                                      root=self.repo, state=self.state, upgrade_state=self.upgrade_state, proc=self.proc.root,
                                      meminfo=self.meminfo, trial=self.trial)
        self.assertIs(plan.server, self.server)


TO = 'b' * 40


def sleeper(ignore_term=False, group=True):
    """A stand-in console process: a child in a session of its own, as the supervisor spawns Bun."""
    code = ('import signal,time\n' + ('signal.signal(signal.SIGTERM, signal.SIG_IGN)\n' if ignore_term else '') + 'print("up", flush=True)\ntime.sleep(120)\n')
    child = subprocess.Popen([PYTHON, '-c', code], stdout=subprocess.PIPE, start_new_session=group)
    child.stdout.readline()
    child.stdout.close()
    return child


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return handover.unreaped(pid) != 'exited' if handover.unreaped(pid) != 'gone' else True


class Exec(BaseException):
    """What a successful execve does to the caller: it never returns."""


class ExecTests(Folder):
    """A5 to A9: the old supervisor replaces its program with the new version's, or does not."""

    def setUp(self):
        super().setUp()
        self.server = SimpleNamespace(pid=4001)
        self.plan = handover.Plan(self.server, TO, {'protocol': handover.PROTOCOL, 'from': FROM, 'to': TO, 'pid': 4001, 'console_port': 7001,
                                                    'listeners': []})
        self.calls = []
        self.asked = threading.Event()
        self.status = None

    def go(self, execve=None, environ=None, **extra):
        def terminate(process):
            self.calls.append(('terminate', process.pid))
        def succeed(program, args, env):
            self.calls.append(('execve', program, list(args), dict(env)))
            raise Exec
        arguments = dict(sigmask=lambda how, signals: self.calls.append(('mask', how, set(signals))), state=self.state,
                         environ=environ if environ is not None else {'SBARBASE_GUARDED': '1', 'HOME': '/x'},
                         flush=lambda: self.calls.append('flush'), execve=execve or succeed)
        arguments.update(extra)
        with patch.object(handover, 'start_time', return_value=777), quiet(), contextlib.suppress(Exec):
            return handover.leave_for_exec(self.plan, self.asked, lambda process: self.status, terminate, **arguments)

    def test_the_record_is_written_and_streams_flushed_between_the_mask_and_the_exec(self):
        self.go()
        names = [call if isinstance(call, str) else call[0] for call in self.calls]
        self.assertEqual(names, ['mask', 'flush', 'execve'])
        self.assertEqual(self.calls[0], ('mask', signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}))
        record = json.loads((self.state / 'handover.json').read_text())
        self.assertEqual({key: record[key] for key in ('protocol', 'from', 'to', 'pid', 'console_port', 'listeners', 'started')},
                         {'protocol': handover.PROTOCOL, 'from': FROM, 'to': TO, 'pid': 4001, 'console_port': 7001, 'listeners': [], 'started': 777})
        self.assertIsInstance(record['at'], int)
        self.assertEqual(os.stat(self.state / 'handover.json').st_mode & 0o777, 0o600)

    def test_the_new_program_is_this_checkouts_dev_py_with_the_marker_and_the_guarded_flag_kept(self):
        self.go()
        _, program, args, env = self.calls[-1]
        self.assertEqual((program, args), ('/usr/bin/python3', ['/usr/bin/python3', str(ROOT / 'lab/dev.py')]))
        self.assertEqual(env, {'SBARBASE_GUARDED': '1', 'HOME': '/x', handover.EXEC_MARKER: '1'})

    def cancelled(self):
        names = [call if isinstance(call, str) else call[0] for call in self.calls]
        self.assertNotIn('execve', names)
        self.assertEqual(self.calls[-1], ('terminate', 4001))
        self.assertFalse((self.state / 'handover.json').exists())

    def test_a_stop_that_arrived_before_the_mask_cancels_the_exec_and_ends_the_old_process(self):
        self.asked.set()
        self.assertIsNone(self.go())
        self.cancelled()
        self.assertEqual([call for call in self.calls if call[0] == 'mask'],
                         [('mask', signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}), ('mask', signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})])

    def test_an_old_process_that_exited_cancels_the_exec(self):
        self.status = 1
        self.go()
        self.cancelled()

    def test_a_record_that_cannot_be_written_lifts_the_mask_removes_the_record_and_stops_the_old_process(self):
        (self.state / 'handover.json').write_text('stale')
        with patch.object(handover, 'write_json', side_effect=OSError('disk full')):
            self.go()
        self.cancelled()
        self.assertIn(('mask', signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT}), self.calls)

    def test_an_exec_that_fails_does_the_same_and_the_record_is_gone(self):
        def refuse(program, args, env):
            self.calls.append('tried')
            raise OSError('exec format error')
        self.go(execve=refuse)
        self.assertIn('tried', self.calls)
        self.cancelled()
        self.assertEqual(self.calls[-2], ('mask', signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT}))

    def test_the_exec_is_only_made_from_the_main_thread(self):
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(self.go()))
        thread.start()
        thread.join()
        self.cancelled()
        self.assertNotIn(('mask', signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}), self.calls)

    def test_the_mask_is_unblocked_only_when_it_was_blocked(self):
        def broken(how, signals):
            self.calls.append(('mask', how))
            raise OSError('no')
        self.go(sigmask=broken)
        self.assertEqual([call for call in self.calls if call[0] == 'mask'], [('mask', signal.SIG_BLOCK)])

    def test_the_marker_is_removed_by_the_new_program_and_one_exec_does_not_leave_it_for_the_next(self):
        environ = {handover.EXEC_MARKER: '1', 'A': 'b'}
        marked = handover.enter(environ, threading.Event(), threading.Event(), lambda *a: None, sigmask=lambda *a: None)
        self.assertTrue(marked)
        self.assertEqual(environ, {'A': 'b'})
        self.assertFalse(handover.enter(environ, threading.Event(), threading.Event(), lambda *a: None, sigmask=lambda *a: None))

    def test_without_the_marker_nothing_is_installed_and_nothing_unblocked(self):
        calls = []
        for value in (None, '', '0', 'yes'):
            environ = {} if value is None else {handover.EXEC_MARKER: value}
            self.assertFalse(handover.enter(environ, threading.Event(), threading.Event(), lambda *a: calls.append('install'),
                                            sigmask=lambda *a: calls.append('mask')))
        self.assertEqual(calls, [])

    def test_the_handlers_are_installed_before_the_unblock(self):
        calls = []
        handover.enter({handover.EXEC_MARKER: '1'}, threading.Event(), threading.Event(), lambda *a: calls.append('install'),
                       sigmask=lambda how, signals: calls.append(('mask', how, set(signals))))
        self.assertEqual(calls, ['install', ('mask', signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})])


SCRIPT_A = '''
import os, signal, subprocess, sys, threading, time
sys.path.insert(0, {lab!r})
import handover
state = {state!r}
code = 'import time; print(1, flush=True); time.sleep(120)'
launcher = [{python!r}, {launcher!r}, str(os.getpid()), {python!r}, '-c', code] if {bound!r} else [{python!r}, '-c', code]
child = subprocess.Popen(launcher, stdout=subprocess.PIPE, start_new_session=True)
child.stdout.readline()
open({pidfile!r}, 'w').write(str(child.pid))
real = signal.pthread_sigmask
def mask(how, signals):
    real(how, signals)
    if how == signal.SIG_BLOCK and {pending!r}:
        os.kill(os.getpid(), signal.SIGTERM)
plan = handover.Plan(child, {to!r}, {{'protocol': handover.PROTOCOL, 'from': {frm!r}, 'to': {to!r}, 'pid': child.pid, 'console_port': 7001, 'listeners': []}})
handover.leave_for_exec(plan, threading.Event(), lambda process: None, lambda process: None, sigmask=mask, state=state,
                        environ=dict(os.environ, SBARBASE_GUARDED='1'), program=[{python!r}, {script_b!r}])
sys.exit(7)
'''

SCRIPT_B = '''
import json, os, signal, subprocess, sys, threading, time
sys.path.insert(0, {lab!r})
import handover
if {mode!r} == 'die':
    os._exit(3)
if {mode!r} == 'hang':
    open({marker!r}, 'w').write('up')
    time.sleep(120)

def blocked():
    out = subprocess.run([{python!r}, '-c', 'print([l for l in open("/proc/self/status") if l.startswith("SigBlk")][0])'], capture_output=True, text=True).stdout
    return int(out.split()[1], 16)
stop, asked = threading.Event(), threading.Event()
def install(stop_event, asked_event):
    signal.signal(signal.SIGTERM, lambda *_: (asked_event.set(), stop_event.set()))
    signal.signal(signal.SIGINT, lambda *_: (asked_event.set(), stop_event.set()))
before = blocked()
marked = handover.enter(os.environ, stop, asked, install)
retiring = handover.adopt({to!r}, marked, {state!r}) if marked else handover.Retiring()
result = dict(marked=marked, asked=asked.is_set(), stop=stop.is_set(), marker_left=handover.EXEC_MARKER in os.environ,
              guarded=os.environ.get('SBARBASE_GUARDED'), blocked_before=before, blocked_after=blocked(), pid=os.getpid(),
              adopted=retiring.pid, ours=handover.unreaped(retiring.pid) != 'gone')
start = time.monotonic()
retiring.end(grace=5)
result.update(ended=handover.unreaped(retiring.pid) == 'gone', took=time.monotonic() - start, record=os.path.exists(os.path.join({state!r}, 'handover.json')))
json.dump(result, open({out!r}, 'w'))
'''


class RealExecTests(Folder):
    """A real `execve`: the old supervisor's program replaced by the new one, in the same process,
    with the old console process still its child."""

    def scripts(self, pending=False, bound=False, mode='adopt'):
        self.out = self.folder / 'out.json'
        self.pidfile = self.folder / 'child.pid'
        self.marker = self.folder / 'marker'
        script_b = self.folder / 'b.py'
        script_b.write_text(SCRIPT_B.format(lab=str(ROOT / 'lab'), python=PYTHON, to=TO, state=str(self.state), out=str(self.out), mode=mode,
                                            marker=str(self.marker)))
        script_a = self.folder / 'a.py'
        script_a.write_text(SCRIPT_A.format(lab=str(ROOT / 'lab'), python=PYTHON, to=TO, frm=FROM, state=str(self.state), pending=pending,
                                            script_b=str(script_b), bound=bound, launcher=str(ROOT / 'lab/parent_bound.py'),
                                            pidfile=str(self.pidfile)))
        return script_a

    def run_exec(self, pending, bound=False):
        script_a = self.scripts(pending, bound)
        result = subprocess.run([PYTHON, str(script_a)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.out.read_text()), result

    def gone(self, pid, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            if handover.stat_fields(pid) is None:
                return True
            time.sleep(.05)
        return False

    def test_the_console_process_the_supervisor_really_spawns_survives_the_exec_and_is_adopted(self):
        # lab/parent_bound.py binds the child to its exact parent with PR_SET_PDEATHSIG: the exec of the
        # parent from its main thread must not trigger it (the design's measurement, repeated here).
        seen, _ = self.run_exec(False, bound=True)
        self.assertTrue(seen['ours'])
        self.assertTrue(seen['ended'])
        self.assertIsNotNone(seen['adopted'])

    def test_b1_a_new_program_that_dies_before_it_can_end_the_old_process_takes_it_with_it(self):
        script_a = self.scripts(bound=True, mode='die')
        result = subprocess.run([PYTHON, str(script_a)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3, result.stderr)
        pid = int(self.pidfile.read_text())
        self.assertTrue(self.gone(pid), 'the old console process outlived the program that died')

    def test_a_supervisor_that_is_killed_after_the_exec_takes_the_old_process_with_it(self):
        script_a = self.scripts(bound=True, mode='hang')
        process = subprocess.Popen([PYTHON, str(script_a)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (process.kill(), process.wait()))
        deadline = time.monotonic() + 30
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(self.marker.exists())
        pid = int(self.pidfile.read_text())
        self.assertIsNone(handover.stat_fields(pid) and None)
        self.assertFalse(self.gone(pid, seconds=.5))
        process.kill()
        process.wait()
        self.assertTrue(self.gone(pid), 'the old console process outlived the supervisor that was killed')

    def test_the_new_program_is_the_same_process_and_adopts_the_old_console_process_as_its_child(self):
        seen, _ = self.run_exec(False)
        self.assertTrue(seen['marked'])
        self.assertFalse(seen['marker_left'])
        self.assertEqual(seen['guarded'], '1')
        self.assertIsNotNone(seen['adopted'])
        self.assertNotEqual(seen['adopted'], seen['pid'])
        self.assertTrue(seen['ours'])
        self.assertFalse(seen['asked'])
        self.assertTrue(seen['ended'])
        self.assertFalse(seen['record'])

    def test_a_stop_pending_across_the_exec_reaches_the_new_handler_and_children_start_unmasked(self):
        seen, _ = self.run_exec(True)
        self.assertTrue(seen['asked'])
        self.assertTrue(seen['stop'])
        # The mask was inherited across the exec, which is why the unblock matters; after it no child has it.
        self.assertNotEqual(seen['blocked_before'], 0)
        self.assertEqual(seen['blocked_after'], 0)

    def test_the_old_console_process_is_ended_within_the_grace(self):
        seen, _ = self.run_exec(False)
        self.assertLess(seen['took'], 5)


class AdoptTests(Folder):
    def setUp(self):
        super().setUp()
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                child.wait(timeout=10)

    def child(self, **options):
        child = sleeper(**options)
        self.children.append(child)
        return child

    def write(self, child, **change):
        record = {'protocol': handover.PROTOCOL, 'to': TO, 'pid': child.pid, 'started': kept_runtime.started(child.pid)}
        record.update(change)
        (self.state / 'handover.json').write_text(json.dumps(record))

    def test_without_a_record_the_start_is_ordinary(self):
        retiring = handover.adopt(TO, True, self.state)
        self.assertFalse(retiring)
        self.assertEqual(sorted(self.state.iterdir()), [])

    def test_a_valid_record_gives_the_old_process_as_a_child_to_end(self):
        child = self.child()
        self.write(child)
        retiring = handover.adopt(TO, True, self.state)
        self.assertTrue(retiring)
        self.assertEqual(retiring.pid, child.pid)
        self.assertTrue((self.state / 'handover.json').exists())
        self.assertNotEqual(handover.unreaped(retiring.pid), 'gone')

    def invalid(self, label, **change):
        with self.subTest(label):
            child = self.child()
            self.write(child, **change)
            marked = change.pop('marked', True)
            retiring = handover.adopt(TO, marked, self.state)
            self.assertFalse(retiring)
            # A live child of ours that the record does not validly name is stopped and reaped, and the record removed.
            self.assertEqual(child.wait(timeout=30) is not None, True)
            self.assertFalse((self.state / 'handover.json').exists())

    def test_a_record_that_is_not_valid_ends_a_child_it_names_and_is_removed(self):
        self.invalid('another protocol', protocol=handover.PROTOCOL + 1)
        self.invalid('another target', to='c' * 40)

    def test_a_valid_record_without_the_exec_marker_is_not_adopted(self):
        child = self.child()
        self.write(child)
        self.assertFalse(handover.adopt(TO, False, self.state))
        child.wait(timeout=30)
        self.assertFalse((self.state / 'handover.json').exists())

    def test_a_start_time_that_does_not_match_is_not_the_process_the_record_named(self):
        child = self.child()
        self.write(child, started=kept_runtime.started(child.pid) + 1)
        self.assertFalse(handover.adopt(TO, True, self.state))
        self.assertIsNone(child.poll())
        self.assertFalse((self.state / 'handover.json').exists())

    def test_a_dead_pid_leaves_only_the_record_to_remove(self):
        dead = subprocess.Popen([PYTHON, '-c', 'pass'])
        dead.wait()
        self.write(dead, started=1)
        self.assertFalse(handover.adopt(TO, True, self.state))
        self.assertFalse((self.state / 'handover.json').exists())

    def test_an_unreadable_record_is_removed(self):
        for text in ('{', '[]', '{"pid": "x"}', '{"pid": 1}'):
            with self.subTest(text):
                (self.state / 'handover.json').write_text(text)
                self.assertFalse(handover.adopt(TO, True, self.state))
                self.assertFalse((self.state / 'handover.json').exists())

    def test_a_live_process_that_is_not_our_child_means_a_handover_is_in_progress_and_the_start_refuses(self):
        child = self.child()
        self.write(child)
        for marked in (True, False):
            with self.subTest(marked=marked):
                with self.assertRaisesRegex(SystemExit, 'in progress'):
                    handover.adopt(TO, marked, self.state, parent=os.getpid() + 1)
                self.assertTrue((self.state / 'handover.json').exists())
                self.assertIsNone(child.poll())

    def test_the_parent_is_read_from_the_process_not_trusted_from_the_record(self):
        self.proc.process(5000, parent=77, started=9)
        (self.state / 'handover.json').write_text(json.dumps({'protocol': 1, 'to': TO, 'pid': 5000, 'started': 9}))
        with self.assertRaisesRegex(SystemExit, 'in progress'):
            handover.adopt(TO, True, self.state, parent=78, proc=self.proc.root)
        self.assertTrue(handover.adopt(TO, True, self.state, parent=77, proc=self.proc.root))


class RetiringTests(Folder):
    def setUp(self):
        super().setUp()
        self.children = []
        self.addCleanup(lambda: [os.killpg(c.pid, signal.SIGKILL) for c in self.children if c.poll() is None and unreaped_ok(c)])

    def retiring(self, **options):
        child = sleeper(**options)
        self.children.append(child)
        retiring = handover.Retiring(child.pid, kept_runtime.started(child.pid), self.state)
        # The child is a Popen of the test; the Retiring reaps it by pid, so the Popen must not.
        return child, retiring

    def test_both_processes_get_the_signal_at_once_and_share_one_grace(self):
        old, retiring = self.retiring(ignore_term=True)
        new = sleeper(ignore_term=True)
        self.children.append(new)
        started = time.monotonic()
        retiring.end(also=new, grace=2)
        took = time.monotonic() - started
        # One grace for both: two successive graces would take twice as long.
        self.assertGreaterEqual(took, 1.9)
        self.assertLess(took, 3.9)
        self.assertEqual(new.returncode, -signal.SIGKILL)
        self.assertEqual(handover.unreaped(old.pid), 'gone')
        self.assertTrue(retiring.reaped)

    def test_a_process_that_exits_on_sigterm_ends_the_wait_at_once(self):
        old, retiring = self.retiring()
        new = sleeper()
        self.children.append(new)
        started = time.monotonic()
        retiring.end(also=new, grace=20)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(new.returncode, -signal.SIGTERM)

    def test_the_records_of_both_are_removed(self):
        old, retiring = self.retiring()
        new = sleeper()
        self.children.append(new)
        for name in (f'listeners-{old.pid}.json', f'listeners-{new.pid}.json', 'handover.json', 'candidate.json'):
            (self.state / name).write_text('{}')
        retiring.end(also=new, grace=5)
        self.assertEqual(sorted(entry.name for entry in self.state.iterdir()), [])

    def test_a_process_that_exits_on_sigterm_is_not_sent_sigkill_and_its_group_is_left_alone(self):
        old, retiring = self.retiring()
        new = sleeper()
        self.children.append(new)
        with patch.object(handover.os, 'killpg', side_effect=AssertionError('SIGKILL to a group')):
            retiring.end(also=new, grace=20)
        self.assertEqual(new.returncode, -signal.SIGTERM)

    def test_a_reaped_retiring_whose_id_now_belongs_to_another_child_is_never_signalled(self):
        stranger = sleeper()
        self.children.append(stranger)
        retiring = handover.Retiring(stranger.pid, 1, self.state)
        retiring.reaped = True
        retiring.end(grace=.2)
        self.assertIsNone(stranger.poll())
        self.assertEqual(handover.unreaped(stranger.pid), 'running')
        retiring.kill_group()
        self.assertEqual(handover.unreaped(stranger.pid), 'running')

    def test_a_reaped_popen_whose_id_now_belongs_to_another_child_is_never_signalled(self):
        stranger = sleeper()
        self.children.append(stranger)
        reaped = SimpleNamespace(pid=stranger.pid, returncode=0, wait=lambda: 0)
        inert = handover.Retiring(state=self.state)
        inert.end(also=reaped, grace=.2)
        self.assertIsNone(stranger.poll())
        self.assertEqual(handover.unreaped(stranger.pid), 'running')

    def test_kill_group_leaves_a_process_that_already_exited_alone_and_only_reaps_it(self):
        child = subprocess.Popen([PYTHON, '-c', 'pass'], start_new_session=True)
        retiring = handover.Retiring(child.pid, 1, self.state)
        deadline = time.monotonic() + 10
        while handover.unreaped(child.pid) == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        with patch.object(handover.os, 'killpg', side_effect=AssertionError('SIGKILL to the group of an exited process')):
            retiring.kill_group()
        self.assertTrue(retiring.reaped)
        self.assertEqual(handover.unreaped(child.pid), 'gone')

    def test_kill_group_ends_the_whole_session_not_only_its_leader(self):
        script = ('import subprocess,sys,time\n'
                  'g=subprocess.Popen(["/bin/sleep","120"])\n'
                  'print(g.pid,flush=True)\n'
                  'time.sleep(120)\n')
        leader = subprocess.Popen([PYTHON, '-c', script], stdout=subprocess.PIPE, text=True, start_new_session=True)
        grandchild = int(leader.stdout.readline())
        leader.stdout.close()
        self.children.append(leader)
        retiring = handover.Retiring(leader.pid, 1, self.state)
        retiring.kill_group()
        # A killed process is gone once it is reaped; until then (init may be slow on a starved host) it is a zombie.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not self.zombie(grandchild):
            time.sleep(.05)
        self.assertTrue(self.zombie(grandchild), 'the leader\'s child survived the SIGKILL')

    @staticmethod
    def zombie(pid):
        try:
            return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] in ('Z', 'X')
        except OSError:
            return True

    def test_ending_twice_never_signals_a_reaped_id_again(self):
        old, retiring = self.retiring()
        retiring.end(grace=5)
        with patch.object(handover.os, 'kill', side_effect=AssertionError('signalled')), patch.object(handover.os, 'killpg', side_effect=AssertionError('signalled')):
            retiring.end(grace=5)
            retiring.kill_group()

    def test_a_process_that_exited_on_its_own_is_only_reaped(self):
        child = subprocess.Popen([PYTHON, '-c', 'pass'], start_new_session=True)
        retiring = handover.Retiring(child.pid, 1, self.state)
        deadline = time.monotonic() + 10
        while handover.unreaped(child.pid) == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        with patch.object(handover.os, 'kill', side_effect=AssertionError('signalled')):
            retiring.end(grace=5)
        self.assertEqual(handover.unreaped(child.pid), 'gone')

    def test_a_pid_that_is_not_our_child_is_never_signalled(self):
        stranger = subprocess.Popen([PYTHON, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(lambda: (stranger.kill(), stranger.wait()))
        # Not a child of this process: a process id reused by something else must not be killed.
        retiring = handover.Retiring(1, 1, self.state)
        with patch.object(handover.os, 'kill', side_effect=AssertionError('signalled')), patch.object(handover.os, 'killpg', side_effect=AssertionError('signalled')):
            retiring.end(grace=.1)
        self.assertIsNone(stranger.poll())

    def test_an_inert_retiring_touches_nothing(self):
        (self.state / 'handover.json').write_text('{}')
        handover.Retiring(state=self.state).end()
        self.assertTrue((self.state / 'handover.json').exists())
        self.assertFalse(handover.Retiring())

    def test_it_reaps_a_process_that_exits_on_its_own_and_kills_the_group_of_one_that_will_not(self):
        old, retiring = self.retiring(ignore_term=True)
        self.assertFalse(retiring.exited())
        retiring.kill_group()
        self.assertTrue(retiring.reaped)
        self.assertTrue(retiring.exited())
        done = subprocess.Popen([PYTHON, '-c', 'pass'], start_new_session=True)
        quick = handover.Retiring(done.pid, 1, self.state)
        deadline = time.monotonic() + 10
        while not quick.exited() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(quick.reaped)


def unreaped_ok(child):
    return handover.unreaped(child.pid) != 'gone'


class FakeRetiring:
    """The old console process as the Promotion sees it, scripted."""

    def __init__(self, pid=4001, events=None):
        self.pid, self.reaped, self.events = pid, False, events if events is not None else []
        self.exits_after = 1
        self.polls = 0
        self.killed = False

    def exited(self):
        self.polls += 1
        if self.reaped:
            return True
        if self.exits_after is not None and self.polls > self.exits_after:
            self.reaped = True
            self.events.append('reaped')
        return self.reaped

    def kill_group(self):
        self.killed = True
        self.reaped = True
        self.events.append('killed')

    def remove_records(self, also=None):
        self.events.append('records removed')


class Scenario(Folder):
    """A Promotion over fixture /proc and state, with the candidate's side played by `candidate_joins`."""

    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.events = []
        self.sent = []
        self.exit_code = None
        self.confirm_results = [True]
        self.confirm_calls = 0
        self.probe_result = (True, 'ok')
        self.probes = 0
        self.verify_result = (True, 'answered')
        self.verified = []
        self.logged = []
        self.later_calls = []
        self.proc.listen('127.0.0.1', 7001, 501)
        self.proc.listen('127.0.0.1', 7002, 502)
        self.proc.process(4001, [501, 502])
        self.proc.process(4002, [])
        (self.state / 'listeners-4001.json').write_text(json.dumps(listeners_record(4001)))
        (self.state / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:7001', 'pid': 4001, 'protocol': 1}))
        (self.state / 'candidate.json').write_text(json.dumps({'url': 'http://127.0.0.1:50000', 'pid': 4002, 'protocol': 1}))
        self.retiring = FakeRetiring(4001, self.events)
        self.supervisor = SimpleNamespace(server=SimpleNamespace(pid=4002), retired=lambda: self.events.append('retired'))
        self.joins = True
        self.environ = {}
        self.make()

    def make(self):
        def probe():
            self.probes += 1
            return self.probe_result

        def verify(port, pid):
            self.verified.append((port, pid))
            return self.verify_result
        self.promotion = handover.Promotion(self.supervisor, self.retiring, self.confirm, state=self.state, clock=self.clock, submit=immediately,
                                            probe=probe, verify=verify, status=lambda process: self.exit_code, kill=self.kill, proc=self.proc.root,
                                            environ=self.environ, log=self.logged.append, later=lambda s, f: self.later_calls.append((s, f)))

    def confirm(self):
        self.confirm_calls += 1
        self.events.append('confirm')
        result = self.confirm_results[min(self.confirm_calls - 1, len(self.confirm_results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    def kill(self, pid, number):
        self.sent.append((pid, number))
        if pid == 4002 and number == signal.SIGUSR2 and len([1 for item in self.sent if item == (4002, signal.SIGUSR2)]) == 1 and self.joins:
            self.candidate_joins()

    def candidate_joins(self, steered=True, publish=True):
        """What the candidate does on its first SIGUSR2: binds beside the old process, writes its record, publishes server.json."""
        self.proc.listen('127.0.0.1', 7001, 601)
        self.proc.listen('127.0.0.1', 7002, 602)
        self.proc.process(4002, [601, 602])
        (self.state / 'listeners-4002.json').write_text(json.dumps(listeners_record(4002, role='candidate', steered=steered)))
        if publish:
            (self.state / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:7001', 'pid': 4002, 'protocol': 1,
                                                                'private': 'http://127.0.0.1:50000', 'promoted': True}))

    def turn(self, seconds=.25):
        self.clock.advance(seconds)
        return self.promotion.poll()

    def until(self, done, limit=2000):
        for _ in range(limit):
            result = self.turn()
            if done(result):
                return result
        self.fail('did not get there: ' + self.promotion.phase)

    def finish(self):
        return self.until(lambda result: result is True)


class PromotionTests(Scenario):
    def test_the_whole_sequence_in_order(self):
        self.finish()
        self.assertEqual(self.sent, [(4002, signal.SIGUSR2), (4001, signal.SIGTERM), (4002, signal.SIGUSR2)])
        # The confirmation is saved only after the old process was reaped and its records removed, and the
        # descriptor stops naming it first.
        self.assertEqual(self.events, ['reaped', 'records removed', 'retired', 'confirm'])
        self.assertEqual(self.verified, [(7001, 4002)])
        self.assertEqual(self.promotion.phase, 'done')

    def test_nothing_is_signalled_until_a_round_has_passed(self):
        self.probe_result = (False, 'rest answered HTTP 503')
        for _ in range(100):
            self.turn()
        self.assertEqual(self.sent, [])
        self.assertGreater(self.probes, 5)
        self.assertEqual(self.promotion.phase, 'probe')

    def test_rounds_are_an_interval_apart_and_one_at_a_time(self):
        self.probe_result = (False, 'x')
        for _ in range(40):
            self.turn()
        # Ten seconds of 0.25 s turns: a round every INTERVAL (2 s), about five, never one per turn.
        self.assertTrue(4 <= self.probes <= 6, self.probes)

    def test_h1_no_round_passing_within_the_deadline_is_the_versions_failure(self):
        self.probe_result = (False, 'rest answered HTTP 503')
        with self.assertRaisesRegex(RuntimeError, r'did not become healthy within 120 s: rest answered HTTP 503'):
            for _ in range(1000):
                self.turn()
        self.assertEqual(self.sent, [])
        self.assertLess(self.clock.now - 1000, 125)

    def test_h1_the_deadline_starts_at_the_first_poll(self):
        self.clock.advance(500)
        self.probe_result = (False, 'x')
        for _ in range(20):
            self.turn()
        self.assertEqual(self.promotion.phase, 'probe')

    def test_h1_a_probe_that_raises_is_a_failed_round_not_a_crash(self):
        def broken():
            raise ValueError('boom')
        self.promotion.rounds.probe = broken
        for _ in range(10):
            self.turn()
        self.assertEqual(self.sent, [])
        self.assertIn('health probe failed (ValueError)', self.promotion.detail)

    def test_h1_the_probe_is_the_candidates_private_address_with_its_pid_and_never_server_json(self):
        calls = []
        with patch.object(upgrade_health, 'check', side_effect=lambda *a, **k: calls.append((a, k)) or (True, 'ok')):
            handover.Promotion.round(self.promotion)
        (state, pid), options = calls[0]
        self.assertEqual((state, pid), (self.state, 4002))
        self.assertEqual(options, {'server_file': 'candidate.json', 'expect_pid': 4002})

    def test_h2_the_old_process_must_have_frozen_its_listeners_but_is_given_time(self):
        (self.state / 'listeners-4001.json').write_text(json.dumps(listeners_record(4001, frozen=False)))
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H2')
        self.assertEqual(self.sent, [])
        waited = self.clock.now - 1000
        self.assertGreaterEqual(waited, handover.FREEZE_WAIT_SECONDS)
        self.assertLess(waited, handover.FREEZE_WAIT_SECONDS + 5)

    def test_h2_a_record_that_freezes_late_is_still_in_time(self):
        (self.state / 'listeners-4001.json').write_text(json.dumps(listeners_record(4001, frozen=False)))
        for _ in range(12):
            self.turn()
        self.assertEqual(self.sent, [])
        (self.state / 'listeners-4001.json').write_text(json.dumps(listeners_record(4001)))
        self.finish()
        self.assertEqual(len(self.sent), 3)

    def test_h2_the_freeze_flag_must_be_true_not_merely_present(self):
        for value in (None, 'yes', 1, False):
            with self.subTest(value=value):
                self.setUp()
                record = listeners_record(4001)
                record['frozen'] = value
                (self.state / 'listeners-4001.json').write_text(json.dumps(record))
                with self.assertRaises(handover.HandoverFailed):
                    self.finish()

    def test_h2_a_group_that_is_not_clean_is_a_handover_failure(self):
        self.proc.listen('127.0.0.1', 7001, 700)
        self.proc.process(4003, [700])
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H2')
        self.assertEqual(self.sent, [])

    def test_h2_a_missing_record_is_a_handover_failure(self):
        (self.state / 'listeners-4001.json').unlink()
        with self.assertRaises(handover.HandoverFailed):
            self.finish()

    def test_h3_a_candidate_exit_after_the_signal_is_the_handovers_to_judge(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.assertTrue(self.promotion.owns_exit())
        self.exit_code = 75
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.turn()
        self.assertEqual((raised.exception.step, '75' in raised.exception.reason), ('H3', True))

    def test_h3_the_window_opens_with_the_signal_and_closes_when_the_promoted_file_was_seen(self):
        self.assertFalse(self.promotion.owns_exit())
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.assertTrue(self.promotion.owns_exit())
        self.candidate_joins()
        self.turn()
        self.assertFalse(self.promotion.owns_exit())

    def test_h3_a_signal_the_candidate_cannot_receive_is_a_handover_failure(self):
        def gone(pid, number):
            raise ProcessLookupError
        self.promotion.kill = gone
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H3')

    def test_h3_no_promoted_file_in_the_allowed_time_is_a_handover_failure(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        started = self.clock.now
        with self.assertRaises(handover.HandoverFailed) as raised:
            for _ in range(200):
                self.turn()
        self.assertEqual(raised.exception.step, 'H3')
        self.assertGreaterEqual(self.clock.now - started, handover.PROMOTE_SECONDS)
        self.assertLess(self.clock.now - started, handover.PROMOTE_SECONDS + 1)

    def test_h3_only_the_candidates_own_promoted_file_counts(self):
        for label, text in (('the old server.json', {'url': 'x', 'pid': 4001, 'protocol': 1}),
                            ('not promoted', {'url': 'x', 'pid': 4002, 'protocol': 1, 'promoted': False}),
                            ('promoted: the string', {'url': 'x', 'pid': 4002, 'promoted': 'true'}),
                            ('another process', {'url': 'x', 'pid': 4009, 'promoted': True})):
            with self.subTest(label):
                self.setUp()
                self.joins = False
                self.until(lambda result: self.promotion.phase == 'promoting')
                self.candidate_joins(publish=False)
                (self.state / 'server.json').write_text(json.dumps(text))
                for _ in range(10):
                    self.turn()
                self.assertEqual(self.promotion.phase, 'promoting')

    def test_h4_to_h6_groups(self):
        def third(self_):
            self_.proc.listen('127.0.0.1', 7001, 700)
            self_.proc.process(4003, [700])

        def foreign(self_):
            self_.proc.unlisten(601)
            self_.proc.listen('127.0.0.1', 7001, 700)
            self_.proc.process(4003, [700])

        def swapped(self_):
            self_.proc.process(4001, [601, 502])
            self_.proc.process(4002, [501, 602])

        def old_gone(self_):
            self_.proc.unlisten(501)
        for label, change in (('a third member', third), ('a foreign member', foreign), ('swapped inodes', swapped), ('the old socket gone', old_gone)):
            with self.subTest(label):
                self.setUp()
                self.joins = False
                self.until(lambda result: self.promotion.phase == 'promoting')
                self.candidate_joins()
                change(self)
                with self.assertRaises(handover.HandoverFailed) as raised:
                    self.finish()
                self.assertEqual(raised.exception.step, 'H4')
                self.assertNotIn((4001, signal.SIGTERM), self.sent)

    def test_h4_a_candidate_without_a_record_is_a_handover_failure(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.candidate_joins()
        (self.state / 'listeners-4002.json').unlink()
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H4')

    def test_h4_a_listener_only_the_old_process_has_is_left_to_close_with_it(self):
        record = listeners_record(4001)
        (self.state / 'listeners-4001.json').write_text(json.dumps(record))
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.candidate_joins()
        (self.state / 'listeners-4002.json').write_text(json.dumps(listeners_record(4002, names=(('console', '127.0.0.1', 7001),),
                                                                                    role='candidate', steered=True)))
        self.finish()
        self.assertIn((4001, signal.SIGTERM), self.sent)

    def test_h5_a_shared_listener_not_marked_steered_is_a_handover_failure(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.candidate_joins(steered=False)
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H5')
        self.assertNotIn((4001, signal.SIGTERM), self.sent)

    def test_h5_a_listener_only_the_candidate_has_needs_no_steering(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.candidate_joins()
        self.proc.listen('127.0.0.1', 7003, 603)
        self.proc.process(4002, [601, 602, 603])
        record = listeners_record(4002, role='candidate', steered=True)
        record['listeners'].append({'name': 'studio-internal', 'host': '127.0.0.1', 'port': 7003, 'steered': False})
        (self.state / 'listeners-4002.json').write_text(json.dumps(record))
        self.finish()

    def test_h6_a_listener_rebound_between_the_checks_is_a_handover_failure(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.candidate_joins()
        self.until(lambda result: self.promotion.phase == 'recheck')
        # The candidate's socket on the console port is replaced by another of its own.
        self.proc.unlisten(601)
        self.proc.listen('127.0.0.1', 7001, 611)
        self.proc.process(4002, [611, 602])
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H6')
        self.assertNotIn((4001, signal.SIGTERM), self.sent)

    def test_h6_the_recheck_comes_on_a_later_turn_than_the_first_check(self):
        self.joins = False
        self.until(lambda result: self.promotion.phase == 'promoting')
        self.candidate_joins()
        self.turn()
        self.assertEqual(self.promotion.phase, 'check')
        self.turn()
        self.assertEqual(self.promotion.phase, 'recheck')
        self.turn()
        self.assertEqual(self.promotion.phase, 'verify')

    def test_h8_an_old_process_reaped_while_the_verification_ran_is_never_signalled_again(self):
        pending = concurrent.futures.Future()
        self.promotion.submit = lambda function: pending if function.__name__ == '<lambda>' else immediately(function)
        self.until(lambda result: self.promotion.phase == 'verify')
        # Supervisor.check reaped it: it died on its own while the verification was out.
        self.retiring.reaped = True
        pending.set_result((True, 'ok'))
        self.turn()
        self.assertNotIn((4001, signal.SIGTERM), self.sent)
        self.assertEqual(self.promotion.phase, 'retire')
        self.finish()

    def test_h4_the_candidates_record_must_say_candidate_and_carry_the_protocol(self):
        for label, change in (('a serving role', {'role': 'serving'}), ('another protocol', {'protocol': handover.PROTOCOL + 1}),
                              ('no role', {'role': None}), ('no protocol', {'protocol': None})):
            with self.subTest(label):
                self.setUp()
                self.joins = False
                self.until(lambda result: self.promotion.phase == 'promoting')
                self.candidate_joins()
                record = json.loads((self.state / 'listeners-4002.json').read_text())
                record.update(change)
                (self.state / 'listeners-4002.json').write_text(json.dumps(record))
                with self.assertRaises(handover.HandoverFailed) as raised:
                    self.finish()
                self.assertEqual(raised.exception.step, 'H4')

    def test_the_default_timer_for_the_steering_check_is_a_daemon_that_does_not_hold_the_exit(self):
        made = []

        class Timer:
            def __init__(self, seconds, function):
                made.append(self)
                self.seconds, self.function, self.daemon, self.started = seconds, function, False, False

            def start(self):
                self.started = True
        with patch.object(handover.threading, 'Timer', Timer):
            handover.Promotion.start_timer(2, lambda: None)
        self.assertEqual([(t.seconds, t.daemon, t.started) for t in made], [(2, True, True)])

    def test_h7_one_answer_from_the_old_process_or_none_is_a_handover_failure_and_the_old_is_not_signalled(self):
        for label, answer in (('another process', (False, 'a fresh connection was answered by process 4001')), ('none', (False, 'timed out'))):
            with self.subTest(label):
                self.setUp()
                self.verify_result = answer
                with self.assertRaises(handover.HandoverFailed) as raised:
                    self.finish()
                self.assertEqual(raised.exception.step, 'H7')
                self.assertNotIn((4001, signal.SIGTERM), self.sent)

    def test_h7_a_verification_that_raises_is_a_handover_failure(self):
        self.promotion.verify = lambda port, pid: 1 / 0
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.finish()
        self.assertEqual(raised.exception.step, 'H7')

    def test_h7_the_verification_runs_off_the_loop_and_is_waited_for_on_later_turns(self):
        pending = concurrent.futures.Future()
        self.promotion.submit = lambda function: pending if function.__name__ == '<lambda>' else immediately(function)
        self.until(lambda result: self.promotion.phase == 'verify')
        for _ in range(30):
            self.turn()
        self.assertEqual(self.promotion.phase, 'verify')
        self.assertNotIn((4001, signal.SIGTERM), self.sent)
        pending.set_result((True, 'ok'))
        self.turn()
        self.assertIn((4001, signal.SIGTERM), self.sent)

    def test_h7_the_console_port_it_connects_to_is_the_published_shared_one(self):
        self.finish()
        self.assertEqual(self.verified[0][0], 7001)

    def test_h8_h9_the_old_process_that_does_not_exit_is_killed_after_the_wait_and_not_before(self):
        self.retiring.exits_after = 10 ** 9
        self.until(lambda result: self.promotion.phase == 'retire')
        started = self.clock.now
        before = None
        for _ in range(1000):
            self.turn()
            if self.retiring.killed:
                break
            before = self.clock.now - started
        self.assertTrue(self.retiring.killed)
        self.assertGreaterEqual(self.clock.now - started, handover.retire_wait_seconds({}) - .3)
        self.assertLess(before, handover.retire_wait_seconds({}))
        self.finish()
        self.assertEqual(self.events[:3], ['killed', 'records removed', 'retired'])

    def test_h8_the_wait_follows_the_drain_deadline(self):
        self.environ['SBARBASE_DRAIN_MS'] = '2000'
        self.make()
        self.retiring.exits_after = 10 ** 9
        self.until(lambda result: self.promotion.phase == 'retire')
        started = self.clock.now
        while not self.retiring.killed:
            self.turn()
        self.assertLess(self.clock.now - started, 8)

    def test_h9_an_old_process_that_crashes_while_draining_is_reaped_and_the_sequence_goes_on(self):
        self.retiring.exits_after = 3
        self.finish()
        self.assertFalse(self.retiring.killed)
        self.assertEqual(self.events[0], 'reaped')

    def test_h9a_the_steering_is_released_after_the_old_process_is_reaped_and_the_record_looked_at_later(self):
        self.finish()
        self.assertEqual(self.sent[-1], (4002, signal.SIGUSR2))
        self.assertEqual(len(self.later_calls), 1)
        self.assertEqual(self.later_calls[0][0], handover.RECORD_SECONDS)
        self.assertEqual(self.logged, [])
        self.later_calls[0][1]()
        self.assertEqual(len(self.logged), 1)
        self.assertIn('still steers', self.logged[0])

    def test_h9a_a_record_that_says_released_is_not_complained_about(self):
        self.finish()
        (self.state / 'listeners-4002.json').write_text(json.dumps(listeners_record(4002, role='candidate', steered=False)))
        self.later_calls[0][1]()
        self.assertEqual(self.logged, [])

    def test_h10_a_confirmation_that_was_not_saved_is_tried_again_after_a_passing_round(self):
        self.confirm_results = [False, False, True]
        self.finish()
        self.assertEqual(self.confirm_calls, 3)
        self.assertEqual(self.events.count('confirm'), 3)
        # No more signals: the old process is already gone and the release was sent once.
        self.assertEqual(self.sent.count((4002, signal.SIGUSR2)), 2)

    def test_h10_retries_wait_for_the_interval_and_for_a_passing_round(self):
        self.confirm_results = [False, True]
        self.until(lambda result: self.promotion.phase == 'confirm')
        self.turn()
        self.assertEqual(self.confirm_calls, 1)
        self.probe_result = (False, 'rest answered HTTP 503')
        for _ in range(30):
            self.turn()
        self.assertEqual(self.confirm_calls, 1)
        self.probe_result = (True, 'ok')
        self.finish()
        self.assertEqual(self.confirm_calls, 2)

    def test_h10_a_confirmation_that_raises_is_not_saved(self):
        self.confirm_results = [OSError('disk full'), True]
        self.finish()
        self.assertEqual(self.confirm_calls, 2)

    def test_h10_the_handover_has_ninety_seconds_from_the_passing_round_and_then_the_version_has_failed(self):
        self.confirm_results = [False]
        with self.assertRaisesRegex(RuntimeError, r'did not finish within 90 s: the health checks passed, but the confirmation was not saved'):
            for _ in range(2000):
                self.turn()
        self.assertGreater(self.promotion.limit - 1000, 0)
        self.assertLess(self.clock.now - self.promotion.limit, 1)

    def test_the_limit_counts_from_the_round_that_passed_not_from_the_start(self):
        self.probe_result = (False, 'x')
        for _ in range(100):
            self.turn()
        self.assertIsNone(self.promotion.limit)
        passed = self.clock.now
        self.probe_result = (True, 'ok')
        self.finish()
        self.assertGreaterEqual(self.promotion.limit, passed + handover.HANDOVER_SECONDS)

    def test_the_polls_never_block(self):
        started = time.monotonic()
        self.finish()
        self.assertLess(time.monotonic() - started, 5)


from test_updates import at
from test_upgrade_drain import DrainFixture, ended, sleeper as drain_sleeper


class SupervisorIntegration(DrainFixture):
    """lab/dev.py's Supervisor: A1, A2, B11, W1 (1) and the loop's check."""

    def finished(self, kind='apply', status=0, moved=True, qualifies=None):
        job = {'kind': kind, 'request': None, 'run': 'run-1', 'process': None}
        with patch.object(self.supervisor, 'moved_for_good', return_value=moved), \
                patch.object(handover, 'qualifies', qualifies or (lambda *a, **k: None)) as stand_in, quiet():
            self.supervisor.update_finished(job, status, at(12))
        return stand_in

    def test_a1_a_completed_upgrade_asks_for_a_plan_and_keeps_the_stop_as_today(self):
        asked = threading.Event()
        self.supervisor.asked = asked
        calls = []
        plan = object()
        self.finished(qualifies=lambda *a, **k: calls.append((a, k)) or plan)
        self.assertEqual(calls, [((self.supervisor, 'apply', True, asked, dev.child_status), {})])
        self.assertIs(self.supervisor.handover, plan)
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertTrue(self.supervisor.keep_runtime)
        self.assertTrue(self.supervisor.stop_event.is_set())

    def test_a1_no_plan_leaves_today_exactly(self):
        self.finished()
        self.assertIsNone(self.supervisor.handover)
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertTrue(self.supervisor.keep_runtime)

    def test_a1_a_move_that_did_not_complete_asks_for_nothing(self):
        calls = []
        self.finished(status=1, moved=False, qualifies=lambda *a, **k: calls.append(a) or object())
        self.assertEqual(calls, [])
        self.assertIsNone(self.supervisor.handover)
        self.assertFalse(getattr(self.supervisor, 'keep_runtime', False))

    def test_a1_a_move_that_completed_through_the_state_alone_counts(self):
        calls = []
        self.finished(status=1, moved=True, qualifies=lambda *a, **k: calls.append(a[2]) or None)
        self.assertEqual(calls, [True])

    def test_a1_with_the_switch_off_the_real_qualifies_reads_nothing(self):
        job = {'kind': 'apply', 'request': None, 'run': 'run-1', 'process': None}
        with patch.object(self.supervisor, 'moved_for_good', return_value=True), patch.dict(os.environ, {}, clear=False), quiet():
            os.environ.pop('SBARBASE_HANDOVER', None)
            self.supervisor.update_finished(job, 0, at(12))
        self.assertIsNone(self.supervisor.handover)

    def run_supervisor(self):
        self.supervisor.stop_event.set()
        with patch.object(self.supervisor, 'reset_studios'), patch.object(self.supervisor, 'settle_updates'), \
                patch.object(self.supervisor, 'publish_current'), patch.object(self.supervisor, 'spawn', return_value=self.supervisor.server), quiet():
            self.supervisor.run()

    def test_a2_with_a_plan_the_console_process_keeps_serving_through_run_s_finally(self):
        self.supervisor.handover = object()
        self.run_supervisor()
        self.assertIsNone(dev.child_status(self.supervisor.server))
        # Everything else is stopped as today.
        # The worker is stopped as today.
        self.assertIsNotNone(self.supervisor.worker.returncode)

    def test_without_a_plan_run_s_finally_stops_the_console_process_as_today(self):
        self.run_supervisor()
        self.assertIsNotNone(self.supervisor.server.returncode)

    def test_w1_with_a_retiring_process_both_console_processes_end_together_before_the_worker(self):
        order = []
        self.supervisor.retiring = SimpleNamespace(end=lambda also=None: order.append(('end', also)), reaped=False)
        original = dev.terminate_group
        with patch.object(dev, 'terminate_group', side_effect=lambda process, *a, **k: order.append(('terminate', process)) or original(process, 0)):
            self.run_supervisor()
        self.assertEqual(order[0], ('end', self.supervisor.server))
        self.assertEqual([item[0] for item in order], ['end', 'terminate'])
        self.assertIs(order[1][1], self.supervisor.worker)

    def test_w1_a_plan_does_not_keep_the_console_process_when_a_retiring_one_exists(self):
        ended_with = []
        self.supervisor.handover = object()
        self.supervisor.retiring = SimpleNamespace(end=lambda also=None: ended_with.append(also), reaped=False)
        self.run_supervisor()
        self.assertEqual(ended_with, [self.supervisor.server])

    def spawned_with(self, retiring):
        calls = []
        self.supervisor.retiring = retiring
        self.supervisor.stop_event.set()
        with patch.object(self.supervisor, 'reset_studios'), patch.object(self.supervisor, 'settle_updates'), \
                patch.object(self.supervisor, 'publish_current'), patch.object(self.supervisor, 'spawn', side_effect=lambda *a, **k: calls.append((a, k)) or self.supervisor.server), \
                patch.object(self.supervisor, 'start_worker'), patch.object(dev, 'terminate_group'), quiet():
            self.supervisor.run()
        return calls

    def test_b11_the_candidate_is_started_with_its_marker_and_today_s_start_is_unchanged(self):
        calls = self.spawned_with(None)
        self.assertEqual(calls, [((['bun', 'lab/upstream-server.ts'],), {})])
        calls = self.spawned_with(handover.Retiring(4001, 1))
        (command,), options = calls[0]
        self.assertEqual(command, ['bun', 'lab/upstream-server.ts'])
        self.assertEqual(options['env'][handover.CANDIDATE_MARKER], '1')
        self.assertEqual({key: value for key, value in options['env'].items() if key != handover.CANDIDATE_MARKER},
                         {key: value for key, value in os.environ.items() if key != handover.CANDIDATE_MARKER})

    def test_b11_the_descriptor_names_the_retiring_process_until_it_is_reaped(self):
        supervisor = dev.Supervisor(threading.Event(), worker_fd=99, catalog=self.folder / 'absent.sqlite')
        supervisor.server = SimpleNamespace(pid=4002)
        with patch.object(dev, 'STATE', self.upstream):
            supervisor.descriptor()
            plain = json.loads((self.upstream / 'supervisor.json').read_text())
            self.assertEqual(plain, {'pid': os.getpid(), 'serverPid': 4002, 'workerPid': None, 'workerRestarts': 0})
            supervisor.retiring = handover.Retiring(4001, 1)
            supervisor.descriptor()
            self.assertEqual(json.loads((self.upstream / 'supervisor.json').read_text())['retiringPid'], 4001)
            supervisor.retiring.reaped = True
            supervisor.retired()
            self.assertNotIn('retiringPid', json.loads((self.upstream / 'supervisor.json').read_text()))

    def test_a_confirmed_handover_leaves_no_retiring_process_so_the_next_stop_or_handover_is_ordinary(self):
        retiring = handover.Retiring(4001, 1)
        retiring.reaped = True
        self.supervisor.retiring = retiring
        stop = self.supervisor.stop_event

        class Confirmed:
            def poll(self_):
                stop.set()
                return True
        self.supervisor.confirm = Confirmed()
        self.supervisor.handover = object()
        schedules = [patch.object(self.supervisor, name) for name in dir(self.supervisor) if name.startswith('schedule_')]
        with patch.object(self.supervisor, 'reset_studios'), patch.object(self.supervisor, 'settle_updates'), \
                patch.object(self.supervisor, 'publish_current'), patch.object(self.supervisor, 'spawn', return_value=self.supervisor.server), \
                patch.object(self.supervisor, 'host_watch'), contextlib.ExitStack() as stack, quiet():
            for item in schedules:
                stack.enter_context(item)
            self.supervisor.run()
        self.assertIsNone(self.supervisor.retiring)
        self.assertIsNone(self.supervisor.confirm)
        # The console process is the one a later handover keeps serving: run's finally leaves it alone.
        self.assertIsNone(dev.child_status(self.supervisor.server))

    def test_check_leaves_a_candidate_exit_to_the_promotion_only_while_it_owns_it(self):
        self.supervisor.confirm = SimpleNamespace(owns_exit=lambda: True)
        ended(self.supervisor.server)
        self.supervisor.check()
        self.supervisor.confirm = SimpleNamespace(owns_exit=lambda: False)
        with self.assertRaisesRegex(RuntimeError, 'Local API exited'):
            self.supervisor.check()

    def test_check_raises_as_today_for_a_confirmation_that_knows_nothing_of_handovers(self):
        ended(self.supervisor.server)
        self.supervisor.confirm = object()
        with self.assertRaisesRegex(RuntimeError, 'Local API exited'):
            self.supervisor.check()
        self.supervisor.confirm = None
        with self.assertRaisesRegex(RuntimeError, 'Local API exited'):
            self.supervisor.check()

    def test_check_reaps_an_old_console_process_that_exited_on_its_own_before_the_retirement(self):
        old = drain_sleeper()
        self.addCleanup(dev.terminate_group, old, 0)
        self.supervisor.retiring = handover.Retiring(old.pid, 1)
        self.supervisor.confirm = SimpleNamespace(retired_at=None)
        with quiet():
            self.supervisor.check()
            self.assertFalse(self.supervisor.retiring.reaped)
            os.kill(old.pid, signal.SIGKILL)
            deadline = time.monotonic() + 10
            while handover.unreaped(old.pid) == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
            self.supervisor.check()
        self.assertTrue(self.supervisor.retiring.reaped)
        self.assertEqual(handover.unreaped(old.pid), 'gone')
        old.returncode = 0

    def test_check_leaves_the_old_process_to_the_promotion_once_it_was_asked_to_retire(self):
        old = drain_sleeper()
        self.addCleanup(dev.terminate_group, old, 0)
        self.supervisor.retiring = handover.Retiring(old.pid, 1)
        self.supervisor.confirm = SimpleNamespace(retired_at=5)
        os.kill(old.pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while handover.unreaped(old.pid) == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        self.supervisor.check()
        self.assertFalse(self.supervisor.retiring.reaped)
        old.returncode = 0
        handover.Retiring(old.pid, 1).kill_group()


class CloseTests(Folder):
    def close(self, keep, leave, owned=True):
        order = []
        with patch.object(dev, 'STATE', self.state), patch.object(dev, 'leave_runtime', side_effect=lambda origin: order.append('leave') or leave), \
                patch.object(dev, 'run_stage', side_effect=lambda *a, **k: order.append('stop') or 0), patch.object(dev, 'notify_installation'), \
                patch.object(dev.kept_runtime, 'discard', side_effect=lambda state: order.append('discard')), quiet():
            dev.close_runtime(owned, keep, 'b' * 40, before_stop=lambda: order.append('before_stop'))
        return order

    def test_a3_the_console_process_is_stopped_before_the_runtime_is_and_only_then(self):
        self.assertEqual(self.close(False, True), ['before_stop', 'discard', 'stop'])
        self.assertEqual(self.close(True, False), ['leave', 'before_stop', 'discard', 'stop'])

    def test_a3_a_runtime_that_is_left_is_left_under_a_serving_console_process(self):
        self.assertEqual(self.close(True, True), ['leave'])

    def test_a3_nothing_owned_stops_nothing(self):
        self.assertEqual(self.close(True, True, owned=False), [])
        self.assertEqual(self.close(False, False, owned=False), [])

    def test_without_before_stop_today_s_close_is_unchanged(self):
        order = []
        with patch.object(dev, 'STATE', self.state), patch.object(dev, 'leave_runtime', return_value=False), \
                patch.object(dev, 'run_stage', side_effect=lambda *a, **k: order.append('stop') or 0), patch.object(dev, 'notify_installation'), \
                patch.object(dev.kept_runtime, 'discard'), quiet():
            dev.close_runtime(True, True, 'b' * 40)
        self.assertEqual(order, ['stop'])


class StubRetiring:
    """The adopted old console process as main sees it."""

    def __init__(self, order, pid=4001):
        self.pid, self.reaped, self.order = pid, False, order

    def __bool__(self):
        return True

    def end(self, also=None, **options):
        self.order.append('retiring.end')

    def exited(self):
        return False


class MainHarness(Folder):
    """dev.main with the host, Docker and the stages stood in for, as the kept runtime tests do, and
    the handover's own pieces recorded in the order they happen."""

    def main(self, retiring=None, run=lambda supervisor: None, stage=None, fresh=True, prepare=None, asked=False, plan=None, env=None,
             attempt_open=False, outcome=False, restart=True, keep=True, ask_during=False, exec_hook=None, close_hook=None, record_error=None):
        order = []
        if retiring is not None and hasattr(retiring, 'order'):
            retiring.order = order
        record = {'stages': [], 'prepare': [], 'closed': [], 'failed': [], 'exec': [], 'withdrawn': [], 'closed_attempt': [], 'narrow': [],
                  'terminated': [], 'order': order}
        events = (threading.Event(), threading.Event())
        if asked:
            events[1].set()
            events[0].set()

        class Supervisor:
            def __init__(self, *args, **kwargs):
                self.restart_for_upgrade = restart
                self.keep_runtime = keep
                self.handover = plan
                self.confirm = None

            def run(self):
                order.append('supervisor.run')
                record['supervisor'] = self
                if ask_during:
                    # The service is stopped while the supervisor runs.
                    events[1].set()
                    events[0].set()
                run(self)

        def run_stage(command, stop_event, timeout=180, pass_fds=(), env=None):
            order.append('stage:' + command[-1])
            record['stages'].append((command, env))
            return stage(command) if stage else 0

        def upgrade_prepare(handover=False):
            order.append('prepare')
            record['prepare'].append(handover)
            if isinstance(prepare, Exception):
                raise prepare
            return True if prepare is None else prepare

        def outcome_stub(started, catalog=None, reason=None):
            order.append('outcome:' + str(started))
            return outcome
        patches = [patch.object(dev, 'STATE', self.state), patch.object(dev.docker_profile, 'require_supported'), patch.object(dev.os, 'chdir'),
                   patch.object(dev.signal, 'signal'), patch.object(dev, 'settle_leftover', return_value='kept'),
                   patch.object(dev.kept_runtime, 'claim'), patch.object(dev.kept_runtime, 'head', return_value='b' * 40),
                   patch.object(dev.kept_runtime, 'discard'),
                   patch.object(dev, 'close_runtime', side_effect=lambda owned, keep_, origin, **k: (order.append('close_runtime'),
                                                                                                   record['closed'].append((owned, keep_, origin, k)),
                                                                                                   close_hook(owned, keep_, origin, **k) if close_hook else None)),
                   patch.object(dev, 'upgrade_prepare', side_effect=upgrade_prepare), patch.object(dev, 'run_stage', side_effect=run_stage),
                   patch.object(dev.console_build_check, 'adopt_staged', return_value=False),
                   patch.object(dev.console_build_check, 'is_fresh', return_value=(fresh, 'fresh' if fresh else 'stale')),
                   patch.object(dev, 'notify_installation'), patch.object(dev, 'upgrade_notices'),
                   patch.object(dev, 'upgrade_outcome', side_effect=outcome_stub), patch.object(dev, 'Supervisor', Supervisor),
                   patch.object(dev, 'checkout_head', return_value='b' * 40),
                   patch.object(dev, 'upgrade_close_attempt', side_effect=lambda: (order.append('close_attempt'), record['closed_attempt'].append(1))),
                   patch.object(dev, 'upgrade_withdraw_attempt', side_effect=lambda: (order.append('withdraw'), record['withdrawn'].append(1), True)[2]),
                   patch.object(dev, 'narrow_hold', side_effect=lambda scope: (order.append('narrow'), record['narrow'].append(scope))),
                   patch.object(dev, 'terminate_group', side_effect=lambda process, *a, **k: (order.append('terminate'), record['terminated'].append(process))),
                   patch.object(handover, 'write_failed', side_effect=lambda to, step, reason, *a, **k: (record['failed'].append((to, step)),
                                                                                                         record_error and (_ for _ in ()).throw(record_error))),
                   patch.object(handover, 'leave_for_exec', side_effect=lambda *args, **k: (order.append('exec'), record['exec'].append(args),
                                                                                             exec_hook(*args) if exec_hook else None)),
                   patch.object(handover, 'vm_rss_mib', return_value=321),
                   patch.dict(os.environ, env or {}, clear=False)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        import run as lab_run
        with patch.object(lab_run, 'private_dir'), patch.object(dev.sys, 'argv', ['lab/dev.py']), \
                patch.object(dev, 'HANDOVER_START', (retiring, events, attempt_open)), quiet():
            try:
                dev.main()
                code = None
            except SystemExit as exit:
                code = exit.code
        record['events'] = events
        return code, record


class HandoverMainTests(MainHarness):
    def test_b7_the_start_gates_run_inside_main_with_the_old_processes_memory_credited(self):
        order = []
        code, record = self.main(StubRetiring(order), env={'SBARBASE_CONTAINER': ''})
        first = record['stages'][0]
        self.assertEqual(first[0], ['/usr/bin/python3', 'lab/install_server.py', 'check'])
        self.assertEqual(first[1][handover.CREDIT], '321')

    def test_b7_in_the_container_the_images_check_runs_instead(self):
        code, record = self.main(StubRetiring([]), env={'SBARBASE_CONTAINER': '1'})
        self.assertEqual(record['stages'][0][0], ['/usr/bin/python3', 'lab/install_server.py', 'images'])

    def test_b7_a_refusal_is_a_withdrawn_attempt_and_the_same_version_restarted_with_the_runtime_kept(self):
        order = []
        code, record = self.main(StubRetiring(order), stage=lambda command: 3 if command[-1] in ('check', 'images') else 0)
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        # A refusal at a start gate may be gone next time: it is no reason never to hand over to this commit.
        self.assertEqual(record['failed'], [])
        self.assertEqual(record['withdrawn'], [1])
        self.assertEqual(record['closed_attempt'], [])
        self.assertNotIn('outcome:False', record['order'])
        self.assertEqual(record['closed'], [(True, True, 'b' * 40, {})])
        self.assertEqual(record['prepare'], [])
        self.assertLess(record['order'].index('retiring.end'), record['order'].index('withdraw') + 1)

    def test_b7_a_refusal_after_a_stop_was_asked_ends_as_a_stop(self):
        code, record = self.main(StubRetiring([]), asked=True, attempt_open=True, restart=False, keep=False,
                                 stage=lambda command: 3 if command[-1] in ('check', 'images') else 0)
        self.assertIn(code, (None, 0))
        self.assertEqual(record['withdrawn'], [])
        self.assertEqual(record['closed'], [(True, False, 'b' * 40, {})])
        self.assertEqual(record['failed'], [])
        self.assertEqual(record['closed_attempt'], [1])

    def test_without_a_retiring_process_no_gate_and_no_credit_and_today_s_prepare(self):
        code, record = self.main(None)
        self.assertEqual(record['prepare'], [False])
        self.assertTrue(all(env is None or handover.CREDIT not in env for _, env in record['stages']))
        self.assertEqual([command[-1] for command, _ in record['stages']], ['--settle-only', 'up'])
        self.assertEqual(record['narrow'], [])
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)

    def test_b8_a_handover_start_prepares_with_the_hold_before_the_snapshot(self):
        code, record = self.main(StubRetiring([]))
        self.assertEqual(record['prepare'], [True])

    def test_b9_a_stale_build_is_never_built_beside_two_processes(self):
        code, record = self.main(StubRetiring([]), fresh=False)
        self.assertEqual(record['failed'], [])
        self.assertNotIn('build:ui', [command[-1] for command, _ in record['stages']])
        self.assertEqual(record['withdrawn'], [1])
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)

    def test_b9_an_ordinary_start_builds_a_stale_console_as_today(self):
        code, record = self.main(None, fresh=False)
        self.assertIn('build:ui', [command[-1] for command, _ in record['stages']])

    def test_b9_the_runtime_start_is_credited_and_the_hold_narrows_only_after_it(self):
        code, record = self.main(StubRetiring([]))
        up = [item for item in record['stages'] if item[0][-1] == 'up'][0]
        self.assertEqual(up[1][handover.CREDIT], '321')
        self.assertIn('SBARBASE_WORKER_FD', up[1])
        self.assertEqual(record['narrow'], ['management'])
        self.assertLess(record['order'].index('stage:up'), record['order'].index('narrow'))
        self.assertLess(record['order'].index('narrow'), record['order'].index('supervisor.run'))

    def test_b9_a_runtime_start_that_fails_does_not_narrow_and_takes_the_way_back_after_both_processes_end(self):
        code, record = self.main(StubRetiring([]), stage=lambda command: 1 if command[-1] == 'up' else 0)
        self.assertEqual(code, 1)
        self.assertEqual(record['narrow'], [])
        self.assertLess(record['order'].index('retiring.end'), record['order'].index('outcome:False'))
        self.assertEqual(record['failed'], [])

    def test_w1_2_every_failure_ends_the_old_process_before_the_way_back_restores_anything(self):
        def broken(supervisor):
            raise RuntimeError('Local API exited')
        code, record = self.main(StubRetiring([]), run=broken)
        self.assertEqual(code, 1)
        order = record['order']
        self.assertLess(order.index('retiring.end'), order.index('outcome:False'))
        self.assertLess(order.index('outcome:False'), order.index('close_runtime'))

    def test_b8_a_handover_start_with_nothing_pending_goes_back_to_todays_restart_and_builds_no_supervisor(self):
        code, record = self.main(StubRetiring([]), prepare=False)
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertNotIn('supervisor.run', record['order'])
        self.assertEqual(record['failed'], [])
        self.assertEqual(record['withdrawn'], [1])
        self.assertEqual(record['closed'], [(True, True, 'b' * 40, {})])
        self.assertNotIn('outcome:False', record['order'])

    def test_b8_a_snapshot_that_fails_ends_the_old_process_before_the_way_back_and_takes_it(self):
        code, record = self.main(StubRetiring([]), prepare=RuntimeError('The pending upgrade cannot start: disk full'))
        self.assertEqual(code, 1)
        self.assertLess(record['order'].index('retiring.end'), record['order'].index('outcome:False'))
        self.assertEqual(record['failed'], [])
        self.assertEqual(record['withdrawn'], [])
        self.assertNotIn('supervisor.run', record['order'])

    def test_w1_3_main_s_finally_ends_it_before_the_runtime_is_closed(self):
        code, record = self.main(StubRetiring([]))
        order = record['order']
        self.assertLess(order.index('retiring.end'), order.index('close_runtime'))

    def test_w3_a_handover_failure_from_the_loop_takes_no_way_back_and_restarts_the_same_version(self):
        def failed(supervisor):
            raise handover.HandoverFailed('H4', 'a third member')
        code, record = self.main(StubRetiring([]), run=failed)
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(record['failed'], [('b' * 40, 'H4')])
        self.assertNotIn('outcome:False', record['order'])
        self.assertEqual(record['withdrawn'], [1])
        self.assertEqual(record['closed_attempt'], [])
        self.assertEqual(record['closed'], [(True, True, 'b' * 40, {})])
        self.assertEqual(record['exec'], [])

    def test_w5_with_a_stop_asked_it_ends_as_w4(self):
        def failed(supervisor):
            raise handover.HandoverFailed('H7', 'no answer')
        # The stop arrives while the supervisor runs; the handover fails with it.
        code, record = self.main(StubRetiring([]), ask_during=True, run=failed)
        self.assertIn(code, (None, 0))
        self.assertEqual(record['withdrawn'], [])
        self.assertEqual(record['closed_attempt'], [1])
        self.assertEqual(record['closed'], [(True, False, 'b' * 40, {})])
        self.assertEqual(record['failed'], [('b' * 40, 'H7')])

    def test_a_failed_handover_that_cannot_be_recorded_still_restarts(self):
        def failed(supervisor):
            raise handover.HandoverFailed('H4', 'x')
        code, record = self.main(StubRetiring([]), run=failed, record_error=OSError('read only'))
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(record['withdrawn'], [1])

    def test_a_stop_before_the_hold_closes_the_attempt_the_guard_opened(self):
        # B5 opened an attempt; main never reached the gate (prepare says not gated).
        code, record = self.main(StubRetiring([]), prepare=False, attempt_open=True, asked=True, restart=False, keep=False)
        self.assertEqual(record['closed_attempt'], [1])

    def test_an_ordinary_ungated_start_closes_no_attempt(self):
        code, record = self.main(None, prepare=False, restart=False, keep=False)
        self.assertEqual(record['closed_attempt'], [])

    def test_b10_a_handover_start_confirms_through_the_promotion(self):
        seen = []

        def look(supervisor):
            seen.append(supervisor.confirm)
        code, record = self.main(StubRetiring([]), run=look)
        self.assertIsInstance(seen[0], handover.Promotion)
        self.assertIs(seen[0].supervisor, record['supervisor'])
        # The adopted old process: the promotion reads its listener record and signals it, so it must be the very object.
        self.assertIsNotNone(seen[0].retiring)
        self.assertIs(seen[0].retiring, record['supervisor'].retiring)
        self.assertTrue(record['supervisor'].retiring)
        self.assertIsNotNone(record['supervisor'].asked)
        # The design's limits, not stretched ones; the state folder and the clock are the module's own.
        self.assertEqual((seen[0].promote_seconds, seen[0].handover_seconds), (handover.PROMOTE_SECONDS, handover.HANDOVER_SECONDS))
        self.assertEqual(seen[0].state, handover.STATE)
        self.assertIs(seen[0].clock, time.monotonic)
        self.assertIs(seen[0].kill, os.kill)
        self.assertEqual(seen[0].proc, '/proc')

    def test_b10_the_promotion_main_builds_confirms_through_the_upgrade_and_sees_a_candidate_exit_at_once(self):
        built = []
        code, record = self.main(StubRetiring([]), run=lambda supervisor: built.append(supervisor.confirm))
        promotion, supervisor = built[0], record['supervisor']
        # Its confirmation is the upgrade's (saved state, hold released), asked about this supervisor's catalog, and its answer is the promotion's.
        catalog = object()
        supervisor.catalog = catalog
        asked = []
        for answer in (True, False):
            with patch.object(dev, 'upgrade_confirmed', side_effect=lambda given, answer=answer: asked.append(given) or answer):
                self.assertIs(promotion.confirmed(), answer)
        self.assertEqual(asked, [catalog, catalog])
        # Its status is how this process observes a child, so a candidate exit after the promote signal is seen on the next turn.
        exited = subprocess.Popen([PYTHON, '-c', 'pass'], start_new_session=True)
        deadline = time.monotonic() + 10
        while dev.child_status(exited) is None and time.monotonic() < deadline:
            time.sleep(.01)
        running = drain_sleeper()
        self.addCleanup(dev.terminate_group, running, 0)
        self.assertEqual(promotion.status(exited), 0)
        self.assertIsNone(promotion.status(running))
        supervisor.server = exited
        promotion.phase, promotion.sent, promotion.promote_until = 'promoting', True, time.monotonic() + 100
        with self.assertRaises(handover.HandoverFailed) as raised:
            promotion.poll()
        self.assertEqual(raised.exception.step, 'H3')
        self.addCleanup(exited.wait)

    def test_b10_an_ordinary_gated_start_confirms_as_today(self):
        seen = []
        code, record = self.main(None, run=lambda supervisor: seen.append(supervisor.confirm))
        self.assertIsInstance(seen[0], upgrade_health.Confirmation)
        self.assertIsNone(record['supervisor'].retiring)


class ExecFlowTests(MainHarness):
    """A3 to A8 as main lays them out."""

    def plan(self):
        return handover.Plan(SimpleNamespace(pid=4001), 'b' * 40, {})

    def test_a4_the_exec_comes_after_the_runtime_was_left_and_the_locks_were_released(self):
        plan = self.plan()
        locks = []

        def exec_(*args):
            import fcntl
            for name in ('supervisor.lock', 'worker.lock'):
                with open(self.state / name, 'a') as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locks.append('free')
        code, record = self.main(None, plan=plan, exec_hook=exec_)
        self.assertEqual(locks, ['free', 'free'])
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)

    def test_a3_a_plan_is_handed_to_the_exec_with_the_stop_event_and_the_runtime_is_left_under_a_serving_console(self):
        plan = self.plan()
        code, record = self.main(None, plan=plan)
        self.assertEqual(len(record['exec']), 1)
        self.assertIs(record['exec'][0][0], plan)
        # The supervisor's own `asked`: a stop its handler consumed before the mask must cancel the exec (A6).
        self.assertIs(record['exec'][0][1], record['events'][1])
        self.assertEqual(record['terminated'], [])
        owned, keep, origin, options = record['closed'][0]
        self.assertTrue(keep)
        self.assertIn('before_stop', options)
        self.assertLess(record['order'].index('close_runtime'), record['order'].index('exec'))
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)

    def test_a3_when_the_runtime_is_not_left_the_console_process_is_stopped_first_and_nothing_is_exec_ed(self):
        plan = self.plan()

        def not_left(owned, keep, origin, before_stop=None):
            before_stop()
        code, record = self.main(None, plan=plan, close_hook=not_left)
        self.assertEqual(record['terminated'], [plan.server])
        self.assertEqual(record['exec'], [])
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)

    def test_a3_a_stop_asked_means_no_exec_and_the_console_process_is_stopped(self):
        plan = self.plan()
        code, record = self.main(None, plan=plan, ask_during=True)
        self.assertEqual(record['terminated'], [plan.server])
        self.assertEqual(record['exec'], [])
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(record['closed'][0][1], False)
        self.assertEqual(record['closed'][0][3], {})

    def test_a3_a_failed_start_with_a_plan_stops_the_console_process_too(self):
        plan = self.plan()

        def broken(supervisor):
            raise RuntimeError('worker restart limit')
        code, record = self.main(None, plan=plan, run=broken)
        self.assertEqual(code, 1)
        self.assertEqual(record['terminated'], [plan.server, plan.server])
        self.assertEqual(record['exec'], [])
        # The way back restores a snapshot: nothing may still have the stores open then, so the console
        # process that was to survive the exec is gone before it, not after.
        self.assertLess(record['order'].index('terminate'), record['order'].index('outcome:False'))

    def test_no_plan_no_exec_and_today_s_close(self):
        code, record = self.main(None)
        self.assertEqual(record['exec'], [])
        self.assertEqual(record['closed'], [(True, True, 'b' * 40, {})])

    def test_two_handovers_in_a_row_keep_the_guarded_flag_and_each_leaves_the_runtime(self):
        # The first exec's environment carries the marker and the flag; the new program removes the marker,
        # keeps the flag, and the next exec sets the marker again: the flag is what leave_runtime reads.
        seen = []
        for _ in range(2):
            environ = {'SBARBASE_GUARDED': '1', 'SBARBASE_HANDOVER': '1'}

            def succeed(program, args, env):
                seen.append(dict(env))
                raise Exec
            with patch.object(handover, 'start_time', return_value=1), quiet(), contextlib.suppress(Exec):
                handover.leave_for_exec(self.plan(), threading.Event(), lambda process: None, lambda process: None, state=self.state,
                                        environ=environ, sigmask=lambda *a: None, flush=lambda: None, execve=succeed)
            new_program = dict(seen[-1])
            self.assertTrue(handover.enter(new_program, threading.Event(), threading.Event(), lambda *a: None, sigmask=lambda *a: None))
            self.assertEqual(new_program['SBARBASE_GUARDED'], '1')
            code, record = self.main(None, plan=self.plan(), env={'SBARBASE_GUARDED': '1'})
            self.assertEqual(record['closed'][0][:3], (True, True, 'b' * 40))


class GuardTests(unittest.TestCase):
    """B5 through the real run_guard: only the guard process itself is stood in for."""

    def run_guard(self, environment, **options):
        calls = []
        with patch.object(dev.subprocess, 'run', side_effect=lambda command, **kw: calls.append((command, kw)) or SimpleNamespace(returncode=0)), \
                patch.object(dev, 'checkout_head', return_value=TO):
            dev.run_guard(environment, **options)
        return calls

    def test_the_guard_runs_for_a_handover_start_although_the_service_manager_ran_it_before(self):
        calls = self.run_guard({'SBARBASE_GUARDED': '1'}, force=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], PYTHON)
        self.assertTrue(calls[0][0][1].endswith('upgrade_guard.py'))
        # A stuck guard exits at once here: this process never sleeps 300 s with a stop unanswered.
        self.assertEqual(calls[0][1]['env']['SBARBASE_GUARD_WAIT'], '0')

    def test_without_force_a_guarded_start_does_not_run_it_again(self):
        self.assertEqual(self.run_guard({'SBARBASE_GUARDED': '1'}), [])

    def test_an_unguarded_terminal_start_runs_it_as_today_with_or_without_force(self):
        self.assertEqual(len(self.run_guard({})), 1)
        self.assertEqual(len(self.run_guard({}, force=True)), 1)

    def test_a_guard_that_moves_the_checkout_back_exits_for_a_restart(self):
        heads = iter([TO, 'c' * 40])
        with patch.object(dev.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(dev, 'checkout_head', side_effect=lambda: next(heads)), quiet():
            with self.assertRaises(SystemExit) as raised:
                dev.run_guard({'SBARBASE_GUARDED': '1'}, force=True)
        self.assertEqual(raised.exception.code, dev.RESTART_FOR_UPGRADE)

    def test_a_guard_that_refuses_stops_the_start(self):
        with patch.object(dev.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), patch.object(dev, 'checkout_head', return_value=TO):
            with self.assertRaises(SystemExit):
                dev.run_guard({'SBARBASE_GUARDED': '1'}, force=True)


class OrdinaryStartTests(Folder):
    """B2 for a start no exec made, through the real handover.adopt and the real entry function."""

    def start(self, argv=('lab/dev.py',)):
        record = self.record = {'order': []}
        with patch.object(handover, 'STATE', self.state), patch.object(dev.sys, 'argv', list(argv)), \
                patch.object(dev.docker_profile, 'require_or_exit', side_effect=lambda: record['order'].append('profile')), \
                patch.object(dev, 'run_guard', side_effect=lambda *a, **k: record['order'].append('guard')), \
                patch.object(dev, 'main', side_effect=lambda: record['order'].append('main')):
            dev.ordinary_start()
        return record

    @staticmethod
    def stranger():
        """A live process that is not a child of this one: started by a shell that has exited."""
        out = subprocess.run(['/bin/sh', '-c', 'sleep 120 >/dev/null 2>&1 & echo $!'], capture_output=True, text=True, timeout=30)
        return int(out.stdout.strip())

    def test_the_entry_point_calls_it_for_every_start_the_exec_did_not_make(self):
        text = (ROOT / 'lab/dev.py').read_text()
        main = text[text.index("if __name__ == '__main__':"):]
        self.assertIn('ordinary_start()', main)
        self.assertEqual(main.count('start_after_handover(events)'), 1)

    def test_a_stale_record_is_removed_before_anything_else_runs(self):
        dead = subprocess.Popen([PYTHON, '-c', 'pass'])
        dead.wait()
        handover.write_json(self.state / 'handover.json', {'protocol': 1, 'to': TO, 'pid': dead.pid, 'started': 1})
        record = self.start()
        self.assertFalse((self.state / 'handover.json').exists())
        self.assertEqual(record['order'], ['profile', 'guard', 'main'])

    def test_a_record_of_a_live_process_that_is_not_our_child_refuses_before_admission_guard_or_locks(self):
        pid = self.stranger()
        self.addCleanup(os.kill, pid, signal.SIGKILL)
        handover.write_json(self.state / 'handover.json', {'protocol': 1, 'to': TO, 'pid': pid, 'started': kept_runtime.started(pid)})
        with self.assertRaisesRegex(SystemExit, 'in progress'):
            self.start()
        self.assertTrue((self.state / 'handover.json').exists())
        self.assertEqual(self.record['order'], [])

    def test_a_pid_that_now_belongs_to_another_process_is_not_the_one_the_record_named(self):
        pid = self.stranger()
        self.addCleanup(os.kill, pid, signal.SIGKILL)
        handover.write_json(self.state / 'handover.json', {'protocol': 1, 'to': TO, 'pid': pid, 'started': kept_runtime.started(pid) + 5})
        record = self.start()
        self.assertEqual(record['order'], ['profile', 'guard', 'main'])
        self.assertFalse((self.state / 'handover.json').exists())

    def test_with_no_record_it_does_nothing_and_reads_no_commit(self):
        with patch.object(dev, 'checkout_head', side_effect=AssertionError('git asked')):
            record = self.start()
        self.assertEqual(record['order'], ['profile', 'guard', 'main'])

    def test_help_and_usage_are_unchanged(self):
        record = self.start(('lab/dev.py', '--help'))
        self.assertEqual(record['order'], ['main'])


class StartTests(Folder):
    """B2 to B5: the new program before main."""

    def start(self, stage=lambda command: 0, guard=None, pending=True, events=None, container=False, main=None, retiring=None):
        events = events or (threading.Event(), threading.Event())
        record = {'stages': [], 'guard': [], 'main': [], 'order': []}
        retiring = retiring or StubRetiring(record['order'])
        retiring.order = record['order']

        def run_stage(command, stop_event, **kwargs):
            record['order'].append('stage')
            record['stages'].append(command)
            return stage(command)

        def run_guard(environment=os.environ, force=False):
            record['order'].append('guard')
            record['guard'].append(force)
            if guard:
                guard()

        def run_main():
            record['order'].append('main')
            record['main'].append(dev.HANDOVER_START)
            if main:
                main()
        environ = {'SBARBASE_CONTAINER': '1'} if container else {}
        with patch.object(dev.handover, 'adopt', return_value=retiring) as adopt, patch.object(dev, 'checkout_head', return_value=TO), \
                patch.object(dev.docker_profile, 'require_or_exit', side_effect=lambda: record['order'].append('profile')), \
                patch.object(dev, 'run_stage', side_effect=run_stage), patch.object(dev, 'run_guard', side_effect=run_guard), \
                patch.object(dev, 'upgrade_pending', return_value=pending), patch.object(dev, 'main', side_effect=run_main), \
                patch.dict(os.environ, environ, clear=False), quiet():
            if not container:
                os.environ.pop('SBARBASE_CONTAINER', None)
            try:
                dev.start_after_handover(events)
                code = None
            except SystemExit as exit:
                code = exit.code
        record['adopt'] = adopt.call_args
        record['events'] = events
        record['retiring'] = retiring
        return code, record

    def test_b2_the_record_is_adopted_with_the_exec_marker_and_the_checkouts_head(self):
        code, record = self.start()
        self.assertEqual(record['adopt'].args, (TO, True))

    def test_b4_to_b5_the_order_is_profile_admission_then_guard_then_main(self):
        code, record = self.start()
        self.assertEqual(record['order'], ['profile', 'stage', 'guard', 'main', 'retiring.end'])
        self.assertEqual(record['stages'], [['/bin/sh', str(ROOT / 'deploy/host-preflight.sh'), '--runtime']])

    def test_b4_in_the_container_the_profile_checks_run_instead(self):
        code, record = self.start(container=True)
        script = '/usr/local/lib/sbarbase/docker_profile.py'
        self.assertEqual(record['stages'], [['/usr/bin/python3', script, 'check'], ['/usr/bin/python3', script, 'runtime', str(ROOT)]])

    def test_b5_the_guard_runs_although_the_service_manager_ran_it_before(self):
        code, record = self.start()
        self.assertEqual(record['guard'], [True])

    def test_b5_the_attempt_the_guard_opened_is_told_to_main(self):
        for pending in (True, False):
            code, record = self.start(pending=pending)
            (handed,) = record['main']
            self.assertEqual(handed[2], pending)
            self.assertIs(handed[0], record['retiring'])
            self.assertIs(handed[1], record['events'])

    def test_b4_a_refusal_ends_the_old_process_and_exits_for_a_restart_before_anything_ran(self):
        code, record = self.start(stage=lambda command: 2)
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(record['order'], ['profile', 'stage', 'retiring.end'])
        self.assertEqual(record['main'], [])

    def test_b4_a_refusal_with_a_stop_asked_exits_clean(self):
        events = (threading.Event(), threading.Event())
        events[1].set()
        code, record = self.start(stage=lambda command: 2, events=events)
        self.assertEqual(code, 0)

    def test_b4_a_stop_while_the_admission_runs_exits_clean_and_ends_the_old_process(self):
        def stopped(command):
            raise InterruptedError('cancelled')
        code, record = self.start(stage=stopped)
        self.assertEqual(code, 0)
        self.assertEqual(record['order'][-1], 'retiring.end')
        self.assertEqual(record['main'], [])

    def test_b5_a_guard_that_moves_back_or_refuses_ends_the_old_process_first(self):
        def moved():
            raise SystemExit(dev.RESTART_FOR_UPGRADE)
        code, record = self.start(guard=moved)
        self.assertEqual(code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(record['order'], ['profile', 'stage', 'guard', 'retiring.end'])

    def test_b3_whatever_main_does_the_old_process_is_ended_at_the_end(self):
        def broken():
            raise RuntimeError('boom')
        with self.assertRaises(RuntimeError):
            self.start(main=broken)

    def test_b3_a_profile_error_from_main_is_an_exit_message_and_still_ends_the_old_process(self):
        def refused():
            raise dev.docker_profile.ProfileError('wrong_socket')
        code, record = self.start(main=refused)
        self.assertIn('Docker profile refused', str(code))
        self.assertEqual(record['order'][-1], 'retiring.end')

    def test_b2_an_in_progress_handover_refuses_before_any_lock(self):
        with patch.object(dev.handover, 'adopt', side_effect=SystemExit('A handover of the console process is in progress')), \
                patch.object(dev, 'checkout_head', return_value=TO), patch.object(dev, 'run_stage', side_effect=AssertionError('ran')), \
                patch.object(dev, 'main', side_effect=AssertionError('ran')):
            with self.assertRaisesRegex(SystemExit, 'in progress'):
                dev.start_after_handover((threading.Event(), threading.Event()))

    def test_the_gates_and_the_admission_are_what_the_service_manager_runs(self):
        self.assertEqual(handover.host_admission({}, ROOT), [['/bin/sh', str(ROOT / 'deploy/host-preflight.sh'), '--runtime']])
        self.assertEqual(handover.host_admission({'SBARBASE_CONTAINER': '1'}, ROOT)[1][-1], str(ROOT))
        self.assertEqual(handover.start_gates({}), [['/usr/bin/python3', 'lab/install_server.py', 'check']])
        self.assertEqual(handover.start_gates({'SBARBASE_CONTAINER': '1'}), [['/usr/bin/python3', 'lab/install_server.py', 'images']])
        unit = (ROOT / 'deploy/sbarbase.service').read_text()
        self.assertIn('ExecStartPre=/bin/sh /opt/sbarbase/deploy/host-preflight.sh --runtime', unit)
        self.assertIn('ExecStartPre=/usr/bin/python3 /opt/sbarbase/lab/install_server.py check', unit)
        script = (ROOT / 'deploy/container/start.sh').read_text()
        self.assertIn('docker_profile.py check', script)
        self.assertIn('docker_profile.py runtime "$PWD"', script)
        self.assertIn('lab/install_server.py images', script)

    def test_the_entry_point_takes_the_handover_path_only_with_the_marker(self):
        text = (ROOT / 'lab/dev.py').read_text()
        main = text[text.index("if __name__ == '__main__':"):]
        self.assertIn('handover.enter(os.environ, *events, install_signal_handlers)', main)
        self.assertIn('start_after_handover(events)', main)
        self.assertLess(main.index('handover.enter'), main.index('ordinary_start()'))

    def test_a_run_of_the_entry_point_without_the_marker_is_today_s(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([PYTHON, str(ROOT / 'lab/dev.py'), '--help'], capture_output=True, text=True, timeout=60,
                                    env={**os.environ, 'PYTHONPATH': str(ROOT / 'lab')}, cwd=directory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Foreground local installation runner', result.stdout)


class SettleTests(test_upgrade.Checkout):
    """lab/upgrade.py for a handover start: the hold first, the wait, then the snapshot."""

    def setUp(self):
        super().setUp()
        self.catalog = self.upstream / 'control.sqlite'
        self.keys = self.secrets / 'managed-keys.sqlite'
        test_upgrade.store(self.catalog, 2, 3)
        test_upgrade.store(self.keys, 0, 1)
        upgrade.start(self.second)
        self.seen = []
        real = upgrade.snapshot

        def watched(commit):
            self.seen.append(('snapshot', upgrade.HOLD.exists(), upgrade.probe_token().exists(), list(sleeps)))
            self.token_at_snapshot = upgrade.probe_token().read_text() if upgrade.probe_token().exists() else None
            return real(commit)
        sleeps = self.sleeps = []
        for item in (patch.object(upgrade, 'snapshot', side_effect=watched),
                     patch.object(upgrade.time, 'sleep', side_effect=lambda seconds: sleeps.append(seconds))):
            item.start()
            self.addCleanup(item.stop)

    def test_the_hold_is_written_and_waited_out_before_the_snapshot(self):
        self.assertTrue(upgrade.before_start(settle=True))
        self.assertEqual(self.seen, [('snapshot', True, True, [upgrade.HOLD_SETTLE_SECONDS])])
        state = upgrade.load_state()
        self.assertTrue(state['attempted_at'])
        self.assertEqual(sorted(json.loads(upgrade.HOLD.read_text())), ['phase', 'since'])
        self.assertTrue(upgrade.probe_token().exists())
        # The hold is written once: the token the old console process may already have read is still the one.
        self.assertEqual(upgrade.probe_token().read_text(), self.token_at_snapshot)

    def test_the_wait_is_longer_than_the_readers_answer_cache(self):
        self.assertEqual(upgrade.HOLD_SETTLE_SECONDS, 1.5)
        cache = int(__import__('re').search(r'ttlMs=(\d+)', (ROOT / 'src/gateway/hold.ts').read_text()).group(1))
        self.assertGreater(upgrade.HOLD_SETTLE_SECONDS * 1000, cache)

    def test_without_settle_the_snapshot_comes_first_as_today(self):
        self.assertTrue(upgrade.before_start())
        self.assertEqual(self.seen, [('snapshot', False, False, [])])
        self.assertEqual(self.sleeps, [])
        self.assertTrue(upgrade.HOLD.exists())

    def test_a_later_start_of_the_same_version_keeps_its_snapshot_and_does_not_wait(self):
        upgrade.before_start(settle=True)
        first = upgrade.load_state()['snapshot']
        self.seen.clear()
        self.sleeps.clear()
        self.assertTrue(upgrade.before_start(settle=True))
        self.assertEqual(self.seen, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(upgrade.load_state()['snapshot'], first)
        self.assertTrue(upgrade.HOLD.exists())

    def test_a_record_the_previous_version_left_unsettled_stops_the_start_before_any_hold(self):
        (self.upstream / 'worker-effect.json').write_text('{}')
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.before_start(settle=True)
        self.assertFalse(upgrade.HOLD.exists())
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.seen, [])

    def test_a_snapshot_that_fails_leaves_the_hold_for_the_way_back_to_release(self):
        with patch.object(upgrade, 'snapshot', side_effect=upgrade.UpgradeError('disk full')):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.before_start(settle=True)
        self.assertTrue(upgrade.HOLD.exists())
        self.assertTrue(upgrade.after_start(False))
        self.assertFalse(upgrade.HOLD.exists())

    def test_a_stale_marker_without_a_pending_start_is_cleared_in_both_forms(self):
        upgrade.before_start(settle=True)
        upgrade.after_start(True)
        upgrade.HOLD.write_text('{}')
        self.assertFalse(upgrade.before_start(settle=True))
        self.assertFalse(upgrade.HOLD.exists())

    def test_the_supervisor_asks_for_the_settled_form_only_in_a_handover_start(self):
        calls = []
        with patch.object(upgrade, 'before_start', side_effect=lambda **kwargs: calls.append(kwargs) or True):
            self.assertTrue(dev.upgrade_prepare())
            self.assertTrue(dev.upgrade_prepare(handover=True))
        self.assertEqual(calls, [{}, {'settle': True}])


class NarrowTests(test_upgrade.Checkout):
    def setUp(self):
        super().setUp()
        test_upgrade.store(self.upstream / 'control.sqlite', 2, 3)
        test_upgrade.store(self.secrets / 'managed-keys.sqlite', 0, 1)
        upgrade.start(self.second)
        upgrade.before_start()

    def test_the_marker_is_rewritten_with_a_scope_and_keeps_its_phase_and_time(self):
        before = json.loads(upgrade.HOLD.read_text())
        upgrade.narrow_hold('management')
        after = json.loads(upgrade.HOLD.read_text())
        self.assertEqual(after, {**before, 'scope': 'management'})
        self.assertEqual(os.stat(upgrade.HOLD).st_mode & 0o777, 0o600)
        self.assertEqual([entry.name for entry in upgrade.UPGRADES.iterdir() if entry.name.startswith('hold')], ['hold'])

    def test_a_marker_that_cannot_be_narrowed_is_an_error_and_is_left_as_it_was(self):
        upgrade.HOLD.write_text('not json')
        with self.assertRaises(ValueError):
            upgrade.narrow_hold('management')
        self.assertEqual(upgrade.HOLD.read_text(), 'not json')
        upgrade.HOLD.write_text('[]')
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.narrow_hold('management')
        upgrade.HOLD.write_text(json.dumps({'since': 1}))
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.narrow_hold('management')
        upgrade.HOLD.unlink()
        with self.assertRaises(OSError):
            upgrade.narrow_hold('management')
        self.assertFalse(upgrade.HOLD.exists())

    def test_the_supervisor_reports_a_narrowing_that_failed_and_goes_on(self):
        upgrade.HOLD.unlink()
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            dev.narrow_hold('management')
        self.assertIn('stays full', out.getvalue())

    def test_the_marker_a_narrowed_hold_leaves_is_what_the_console_process_reads_as_management(self):
        upgrade.narrow_hold('management')
        script = ("import {holdScope} from './src/gateway/hold';\n"
                  f"console.log(holdScope(require('node:fs').readFileSync({str(upgrade.HOLD)!r},'utf8')));")
        result = subprocess.run(['bun', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), 'management', result.stderr)


class WithdrawTests(test_upgrade.Checkout):
    def setUp(self):
        super().setUp()
        test_upgrade.store(self.upstream / 'control.sqlite', 2, 3)
        test_upgrade.store(self.secrets / 'managed-keys.sqlite', 0, 1)
        upgrade.start(self.second)

    def guard(self, **change):
        state = upgrade.load_state()
        state['guard'] = {'phase': 'applied', 'attempts': 2, 'open': True, **change}
        upgrade.save_state(state)

    def test_it_runs_under_the_upgrade_lock(self):
        self.guard()
        held = []

        class Lock:
            def __init__(self, *args, **kwargs):
                held.append(args)

            def __enter__(self):
                held.append('in')

            def __exit__(self, *exc):
                held.append('out')
        with patch.object(upgrade, 'exclusive', Lock):
            self.assertTrue(upgrade.withdraw_attempt())
        self.assertEqual(held[0][0], upgrade.LOCK)
        self.assertEqual(held[1:], ['in', 'out'])

    def test_the_supervisor_s_wrapper_reports_what_the_upgrade_module_did_and_never_raises(self):
        with patch.object(upgrade, 'withdraw_attempt', return_value=True):
            self.assertTrue(dev.upgrade_withdraw_attempt())
        with patch.object(upgrade, 'withdraw_attempt', return_value=False):
            self.assertFalse(dev.upgrade_withdraw_attempt())
        with patch.object(upgrade, 'withdraw_attempt', side_effect=OSError('x')), quiet():
            self.assertFalse(dev.upgrade_withdraw_attempt())

    def test_the_attempt_is_closed_and_taken_back_out_of_the_count(self):
        self.guard()
        self.assertTrue(upgrade.withdraw_attempt())
        self.assertEqual(upgrade.load_state()['guard'], {'phase': 'applied', 'attempts': 1, 'open': False})

    def test_the_count_never_goes_below_zero(self):
        self.guard(attempts=0)
        self.assertTrue(upgrade.withdraw_attempt())
        self.assertEqual(upgrade.load_state()['guard']['attempts'], 0)

    def test_a_closed_attempt_is_still_taken_back(self):
        self.guard(open=False, attempts=3)
        upgrade.withdraw_attempt()
        self.assertEqual(upgrade.load_state()['guard'], {'phase': 'applied', 'attempts': 2, 'open': False})

    def test_nothing_changes_without_a_guard_record_of_the_phase_or_a_pending_start(self):
        self.assertFalse(upgrade.withdraw_attempt())
        self.guard(phase='rolling_back')
        self.assertFalse(upgrade.withdraw_attempt())
        self.assertEqual(upgrade.load_state()['guard']['attempts'], 2)
        self.guard(attempts='x')
        self.assertFalse(upgrade.withdraw_attempt())
        state = upgrade.load_state()
        state['phase'] = 'confirmed'
        state['guard'] = {'phase': 'confirmed', 'attempts': 2, 'open': True}
        upgrade.save_state(state)
        self.assertFalse(upgrade.withdraw_attempt())
        self.assertEqual(upgrade.load_state()['guard']['attempts'], 2)

    def test_the_other_state_is_carried_through_untouched(self):
        self.guard()
        before = upgrade.load_state()
        upgrade.withdraw_attempt()
        after = upgrade.load_state()
        self.assertEqual({key: value for key, value in after.items() if key != 'guard'}, {key: value for key, value in before.items() if key != 'guard'})

    def test_the_restart_after_a_withdrawal_counts_only_its_own_attempt(self):
        import upgrade_guard
        lay = upgrade.layout()
        self.assertEqual(upgrade_guard.guard(lay), 0)
        self.assertEqual(upgrade.load_state()['guard']['attempts'], 1)
        upgrade.withdraw_attempt()
        self.assertEqual(upgrade_guard.guard(lay), 0)
        self.assertEqual(upgrade.load_state()['guard'], {'phase': 'applied', 'attempts': 1, 'open': True})


FIXTURE = ROOT / 'tests/fixtures/handover-process.ts'


class RealProcessTests(Folder):
    """The supervisor's Promotion against two real Bun processes (tests/fixtures/handover-process.ts:
    the gateway and signal wiring of lab/upstream-server.ts over a stand-in application): real /proc, real
    signals, real reuseport steering. The kernel and Bun mechanism alone is tests/handover-process.test.ts."""

    def setUp(self):
        super().setUp()
        self.children = []
        self.addCleanup(self.stop_all)
        self.ports = {name: self.free_port() for name in ('PORT', 'DATABASE_PORT', 'STUDIO_PORT')}

    def stop_all(self):
        for child in self.children:
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                child.wait(timeout=10)
            child.stdout.close()

    def free_port(self):
        """A port below the ephemeral range (32768 to 60999), where the clients' source ports are taken, reserved for
        the test by a claim folder shared with tests/fixtures/ports.ts, so two runs at once never pick the same."""
        import random
        import tempfile
        claims = Path(tempfile.gettempdir()) / 'sbarbase-handover-ports'
        claims.mkdir(exist_ok=True)
        while True:
            port = random.randrange(21000, 31000)
            folder = claims / str(port)
            # The claim appears whole or not at all: a folder with its owner inside is renamed into place, which fails
            # while the name is taken, so no other run sees it without an owner and takes it for dead.
            made = Path(tempfile.mkdtemp(prefix='claim-', dir=claims))
            (made / 'owner').write_text(str(os.getpid()))
            try:
                os.rename(made, folder)
            except OSError:
                shutil.rmtree(made, ignore_errors=True)
                try:
                    owner = int((folder / 'owner').read_text())
                except (OSError, ValueError):
                    continue
                try:
                    os.kill(owner, 0)
                    continue
                except PermissionError:
                    continue
                except ProcessLookupError:
                    pass
                away = claims / f'stale-{os.getpid()}-{port}-{time.time_ns()}'
                try:
                    os.rename(folder, away)
                except OSError:
                    continue
                shutil.rmtree(away, ignore_errors=True)
                continue
            self.addCleanup(shutil.rmtree, folder, True)
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError:
                    continue
            return port

    def launch(self, role, tag, **overrides):
        env = {**os.environ, 'ROLE': role, 'STATE': str(self.state), 'TAG': tag, **{key: str(value) for key, value in self.ports.items()}, **overrides}
        child = subprocess.Popen(['bun', str(FIXTURE)], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.children.append(child)
        self.assertIn('READY', child.stdout.readline())
        threading.Thread(target=lambda: [None for _ in child.stdout], daemon=True).start()
        return child

    def get(self, port):
        connection = __import__('http.client').client.HTTPConnection('127.0.0.1', port, timeout=60)
        try:
            connection.request('GET', '/health', headers={'connection': 'close'})
            answer = connection.getresponse()
            answer.read()
            return answer.status, answer.getheader('x-sbarbase-pid')
        finally:
            connection.close()

    def start_pair(self):
        old = self.launch('serving', 'old')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            record = handover.read_json(self.state / f'listeners-{old.pid}.json')
            if record and len(record['listeners']) == 3:
                break
            time.sleep(.05)
        handover.write_json(self.state / 'handover.json', {**{'protocol': 1, 'to': TO, 'pid': old.pid}, 'started': kept_runtime.started(old.pid)})
        candidate = self.launch('candidate', 'new')
        retiring = handover.Retiring(old.pid, kept_runtime.started(old.pid), self.state)
        supervisor = SimpleNamespace(server=candidate, retired=lambda: self.retired.append(time.monotonic()))
        return old, candidate, retiring, supervisor

    def promotion(self, supervisor, retiring, candidate, confirmed=lambda: True):
        def probe():
            url = handover.read_json(self.state / 'candidate.json')['url']
            status, headers = upgrade_health.fetch_seen(url + '/health')
            return status == 200 and headers.get('x-sbarbase-pid') == str(candidate.pid), 'private'
        # The verification's own timeout (1 s) is the design's; the clients of this test share this process's
        # interpreter, so the check is given the time a starved thread needs.
        return handover.Promotion(supervisor, retiring, confirmed, state=self.state, probe=probe, status=dev.child_status,
                                  verify=lambda port, pid: handover.verify_console(port, pid, timeout=10),
                                  log=lambda text: None, later=lambda s, f: None, promote_seconds=120, handover_seconds=600)

    @staticmethod
    def wait_for(done, seconds=120):
        deadline = time.monotonic() + seconds
        while not done() and time.monotonic() < deadline:
            time.sleep(.05)

    def drive(self, promotion, limit=240):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if promotion.poll():
                return True
            time.sleep(.05)
        self.fail('the promotion did not finish: ' + promotion.phase)

    def test_the_whole_handover_with_clients_connecting_throughout_loses_no_request(self):
        self.retired = []
        old, candidate, retiring, supervisor = self.start_pair()
        port = self.ports['PORT']
        results = []
        running = threading.Event()
        running.set()

        def client():
            while running.is_set():
                try:
                    results.append(self.get(port))
                except Exception as error:
                    results.append(('error', error.__class__.__name__))
                # Paced: back to back clients make thousands of connections a second, and loopback then drops a SYN now and
                # then (a source port reused against a TIME-WAIT), which stalls that one connection for 1, 3 or 7 s.
                time.sleep(.004)
        clients = [threading.Thread(target=client) for _ in range(6)]
        for thread in clients:
            thread.start()
        try:
            self.drive(self.promotion(supervisor, retiring, candidate))
            self.assertEqual(self.get(port), (200, str(candidate.pid)))
            self.wait_for(lambda: len(results) > 60)
        finally:
            running.clear()
            for thread in clients:
                thread.join()
        # Not one request failed. A request may be slow on a loaded host, not lost: clients wait up to a minute.
        self.assertEqual([item for item in results if item[0] != 200], [])
        self.assertGreater(len(results), 40)
        self.assertEqual({pid for _, pid in results} - {str(old.pid), str(candidate.pid)}, set())
        self.assertEqual(handover.unreaped(old.pid), 'gone')
        self.assertTrue(retiring.reaped)
        self.assertIsNone(candidate.poll())
        self.assertEqual(len(self.retired), 1)
        # The old process left no record behind and the candidate alone is in each group.
        self.assertFalse((self.state / f'listeners-{old.pid}.json').exists())
        self.assertFalse((self.state / 'handover.json').exists())
        for name, host, port_ in (('console', '127.0.0.1', self.ports['PORT']), ('database', '127.0.0.1', self.ports['DATABASE_PORT']),
                                  ('studio', '127.0.0.1', self.ports['STUDIO_PORT'])):
            self.assertEqual(len(handover.listening(host, port_)), 1, name)
        self.assertEqual(handover.read_json(self.state / 'server.json')['pid'], candidate.pid)

    def test_the_steering_is_released_after_the_old_process_is_gone(self):
        self.retired = []
        old, candidate, retiring, supervisor = self.start_pair()
        self.drive(self.promotion(supervisor, retiring, candidate))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = handover.read_json(self.state / f'listeners-{candidate.pid}.json')
            if not any(item['steered'] for item in record['listeners']):
                break
            time.sleep(.05)
        self.assertFalse(any(item['steered'] for item in record['listeners']))
        self.assertEqual([item['name'] for item in record['listeners']], ['console', 'database', 'studio-internal'])

    def test_a_candidate_that_cannot_bind_is_a_handover_failure_and_the_old_process_goes_on_serving(self):
        self.retired = []
        old = self.launch('serving', 'old')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(handover.read_json(self.state / f'listeners-{old.pid}.json')['listeners']) < 3:
            time.sleep(.05)
        handover.write_json(self.state / 'handover.json', {'protocol': 1, 'to': TO, 'pid': old.pid, 'started': kept_runtime.started(old.pid)})
        # A listener without SO_REUSEPORT on the port the candidate wants for its database.
        hog = socket.socket()
        self.addCleanup(hog.close)
        hog.bind(('127.0.0.1', 0))
        hog.listen(1)
        candidate = self.launch('candidate', 'new', DATABASE_PORT=str(hog.getsockname()[1]))
        retiring = handover.Retiring(old.pid, kept_runtime.started(old.pid), self.state)
        supervisor = SimpleNamespace(server=candidate, retired=lambda: self.retired.append(1))
        promotion = self.promotion(supervisor, retiring, candidate)
        with self.assertRaises(handover.HandoverFailed) as raised:
            self.drive(promotion)
        self.assertEqual(raised.exception.step, 'H3')
        self.assertIn('75', raised.exception.reason)
        self.assertIsNone(old.poll())
        self.assertEqual(self.get(self.ports['PORT']), (200, str(old.pid)))
        self.assertEqual(len(handover.listening('127.0.0.1', self.ports['PORT'])), 1)
        self.assertEqual(handover.read_json(self.state / 'server.json')['pid'], old.pid)
        self.assertEqual(self.retired, [])

    def test_two_handovers_in_a_row_the_promoted_process_becomes_the_old_one_of_the_next(self):
        self.retired = []
        old, first, retiring, supervisor = self.start_pair()
        results = []
        running = threading.Event()
        running.set()

        def client():
            while running.is_set():
                try:
                    results.append(self.get(self.ports['PORT']))
                except Exception as error:
                    results.append(('error', error.__class__.__name__))
                # Paced: back to back clients make thousands of connections a second, and loopback then drops a SYN now and
                # then (a source port reused against a TIME-WAIT), which stalls that one connection for 1, 3 or 7 s.
                time.sleep(.004)
        clients = [threading.Thread(target=client) for _ in range(4)]
        for thread in clients:
            thread.start()
        try:
            self.drive(self.promotion(supervisor, retiring, first))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(item['steered'] for item in handover.read_json(self.state / f'listeners-{first.pid}.json')['listeners']):
                time.sleep(.05)
            # The promoted process is the serving one now: Q6 and Q7 read it as the old process of the next handover.
            record = handover.read_json(self.state / f'listeners-{first.pid}.json')
            self.assertEqual(record['role'], 'serving')
            self.assertTrue(handover.listeners_ok(record, first.pid))
            handover.clean_group(record, first.pid)
            published = handover.read_json(self.state / 'server.json')
            self.assertEqual((published['pid'], published['protocol']), (first.pid, handover.PROTOCOL))
            handover.write_json(self.state / 'handover.json', {'protocol': 1, 'to': TO, 'pid': first.pid, 'started': kept_runtime.started(first.pid)})
            second = self.launch('candidate', 'newest')
            retiring = handover.Retiring(first.pid, kept_runtime.started(first.pid), self.state)
            supervisor = SimpleNamespace(server=second, retired=lambda: self.retired.append(time.monotonic()))
            self.drive(self.promotion(supervisor, retiring, second))
            self.assertEqual(self.get(self.ports['PORT']), (200, str(second.pid)))
            self.wait_for(lambda: len(results) > 60)
        finally:
            running.clear()
            for thread in clients:
                thread.join()
        self.assertEqual([item for item in results if item[0] != 200], [])
        self.assertEqual(handover.unreaped(first.pid), 'gone')
        self.assertIsNone(second.poll())
        self.assertEqual(len(self.retired), 2)


import combined_admission
import durable_runtime as runtime
import install_server
import resource_admission


class CreditTests(Folder):
    """SBARBASE_ADMISSION_CREDIT_MIB: each admission that runs beside the old console process counts its
    resident memory back, exactly once, and none credits anything without it."""

    def test_the_install_check_counts_the_credit_and_only_it(self):
        meminfo = 'MemAvailable:    1048576 kB\n'

        def capacity(credit):
            environ = {handover.CREDIT: str(credit)} if credit is not None else {}
            with patch.object(install_server, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: meminfo) if p == '/proc/meminfo' else Path(p)), \
                    patch.object(install_server, 'planned_placement', return_value=(1500, 1, 'placement')), \
                    patch.object(install_server, 'headroom_requirement', return_value=(1500, 'composition')), \
                    patch.object(install_server, 'kept_record', return_value=(None, 'no record')), \
                    patch.object(install_server, 'STATE', self.state), patch.dict(os.environ, environ, clear=False):
                if credit is None:
                    os.environ.pop(handover.CREDIT, None)
                return [text for _, text in install_server.capacity() if 'Host headroom insufficient' in text]
        self.assertTrue(capacity(None))
        self.assertTrue(capacity(0))
        # 1024 MiB available against 1500: 476 MiB credited lets it through, 475 does not.
        self.assertTrue(capacity(475))
        self.assertEqual(capacity(476), [])
        self.assertIn('475 MiB held by the console process a handover leaves serving counted as available', capacity(475)[0])
        self.assertNotIn('console process a handover leaves', capacity(0)[0])

    def test_the_runtime_start_counts_the_credit_and_only_it(self):
        def attempt(credit):
            target = runtime.Runtime.__new__(runtime.Runtime)
            target.values = {'environments': {}, 'admin': 'a' * 64}
            target.hba_writer = SimpleNamespace(startup=SimpleNamespace(verify=lambda: None))
            target.launch = lambda *a, **k: (_ for _ in ()).throw(StopIteration)
            meminfo = 'MemAvailable:    1048576 kB\n'
            environ = {handover.CREDIT: str(credit)}
            with patch.object(runtime, 'STATE', self.state), patch.object(runtime.effect_receipt, 'require_settled'), \
                    patch.object(runtime.lab, 'docker', return_value=SimpleNamespace(stdout='', returncode=0)), \
                    patch.object(runtime.lab, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: meminfo)), \
                    patch.object(runtime.resource_policy, 'start_placement', return_value=(1500, 1)), \
                    patch.object(runtime.resource_policy, 'START_RESERVE_MIB', 0), \
                    patch.object(runtime.kept_runtime, 'current', return_value=(None, 'no record')), \
                    patch.object(runtime, 'inspect', return_value={'Id': 'network'}), patch.dict(os.environ, environ):
                try:
                    target.start()
                except RuntimeError as error:
                    return str(error)
                except StopIteration:
                    return 'admitted'
        self.assertEqual(attempt(0), 'Insufficient runtime memory headroom')
        self.assertEqual(attempt(475), 'Insufficient runtime memory headroom')
        self.assertEqual(attempt(476), 'admitted')

    def test_the_combined_admission_counts_the_credit_and_only_it(self):
        def available(credit):
            seen = []
            admission = combined_admission.CombinedAdmission.__new__(combined_admission.CombinedAdmission)
            admission.ids = []
            admission.unrecorded_stopped = 0
            files = {'/proc/meminfo': 'MemAvailable:    1048576 kB\n', '/proc/pressure/cpu': 'some avg10=0.00 avg60=0 avg300=0 total=0\n',
                     '/proc/pressure/io': 'some avg10=0.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n',
                     '/proc/pressure/memory': 'some avg10=0.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n'}
            info = SimpleNamespace(stdout=json.dumps({'Name': socket.gethostname(), 'OSType': 'linux'}), returncode=0)
            with patch.object(combined_admission.lab, 'docker', return_value=info), patch.object(combined_admission, 'labelled_names', return_value=[]), \
                    patch.object(combined_admission, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: files[p])), \
                    patch.object(combined_admission, 'refusal', side_effect=lambda memory, cpus, free, host: seen.append(free) or None), \
                    patch.dict(os.environ, {handover.CREDIT: str(credit)}):
                admission.check_current()
            return seen[0]
        self.assertEqual(available(0), 1024 * 1024 * 1024)
        self.assertEqual(available(350), (1024 + 350) * 1024 * 1024)

    def test_the_resource_snapshot_counts_the_credit_and_only_it(self):
        def measured(credit):
            files = {'/proc/meminfo': 'MemTotal: 9 kB\nMemAvailable:    1048576 kB\n'}
            with patch.object(resource_admission.docker_profile, 'require_supported'), \
                    patch.object(resource_admission, 'Path', side_effect=lambda p: SimpleNamespace(read_text=lambda: files[p])), \
                    patch.object(resource_admission, 'disk_free', return_value=1), patch.dict(os.environ, {handover.CREDIT: str(credit)}):
                return resource_admission.snapshot().available_memory
        self.assertEqual(measured(0), 1024 * 1024 * 1024)
        self.assertEqual(measured(350), (1024 + 350) * 1024 * 1024)

    def test_without_the_variable_every_admission_is_exactly_as_before(self):
        self.assertEqual(handover.credit_mib({}), 0)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(handover.CREDIT, None)
            self.assertEqual(handover.credit_mib(), 0)

    def test_the_stages_that_run_beside_the_old_process_are_given_the_credit_and_nothing_else_is(self):
        env = dev.credit_environment(SimpleNamespace(pid=os.getpid()), {'A': 'b'})
        self.assertEqual(env['A'], 'b')
        self.assertGreater(int(env[handover.CREDIT]), 0)
        with patch.object(handover, 'vm_rss_mib', side_effect=handover.Refused('gone')):
            self.assertEqual(dev.credit_environment(SimpleNamespace(pid=1), {'A': 'b'}), {'A': 'b'})


class SwitchTests(unittest.TestCase):
    """SBARBASE_HANDOVER reaches the supervisor under systemd and in the container."""

    def test_the_container_gets_the_switch_through_compose_yaml_and_the_launcher(self):
        self.assertIn('SBARBASE_HANDOVER: ${SBARBASE_HANDOVER:-}', (ROOT / 'compose.yaml').read_text())
        launcher = (ROOT / 'deploy/compose.sh').read_text()
        self.assertIn('scalar SBARBASE_HANDOVER "${SBARBASE_HANDOVER-}"', launcher)
        # The launcher passes only declared inputs (env -i): the switch has to be one of them, and empty when unset.
        self.assertIn('SBARBASE_HANDOVER="${SBARBASE_HANDOVER-}"', launcher[launcher.index('exec env -i'):])
        self.assertNotIn('.env"', launcher[launcher.index('exec env -i'):].replace('--env-file /dev/null', ''))

    def test_the_preflight_checks_it_as_a_plain_scalar(self):
        self.assertIn('scalar SBARBASE_HANDOVER "${SBARBASE_HANDOVER-}"', (ROOT / 'deploy/host-preflight.sh').read_text())

    def test_the_docker_guide_lists_it_in_both_languages(self):
        for name in ('docker.md', 'docker.ar.md'):
            self.assertIn('sudo SBARBASE_HANDOVER=1 deploy/compose.sh up', (ROOT / 'docs/guides' / name).read_text())

    def test_the_launcher_refuses_a_switch_with_a_control_character(self):
        result = subprocess.run(['/bin/sh', str(ROOT / 'deploy/compose.sh'), 'check'], capture_output=True, text=True, timeout=60,
                                env={'PATH': os.environ['PATH'], 'SBARBASE_HANDOVER': '1\n1'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SBARBASE_HANDOVER', result.stderr + result.stdout)

    def test_the_unit_gets_it_as_a_drop_in_and_nothing_in_the_unit_sets_it_by_default(self):
        self.assertNotIn('SBARBASE_HANDOVER', (ROOT / 'deploy/sbarbase.service').read_text())

    def test_the_bun_process_turns_the_candidate_on_only_with_the_switch(self):
        text = (ROOT / 'lab/upstream-server.ts').read_text()
        self.assertIn('handoverEnabled()?{candidate:candidateRequested()', text)


class ImportTests(unittest.TestCase):
    def test_the_supervisor_has_this_versions_release_channel_loaded_before_any_move(self):
        # Q4 judges the move with the code that is running; the checkout holds the target's by then.
        result = subprocess.run([PYTHON, '-c', 'import sys, dev; print("release_channel" in sys.modules)'], capture_output=True, text=True,
                                cwd=ROOT / 'lab', env={**os.environ, 'PYTHONPATH': str(ROOT / 'lab')}, timeout=60)
        self.assertEqual(result.stdout.strip(), 'True', result.stderr)

    def test_the_declaration_is_read_when_the_module_loads_and_not_again_after_the_move(self):
        self.assertIsNotNone(handover.DECLARED)
        with patch.object(handover, 'DECLARED', None):
            with self.assertRaises(handover.Refused):
                handover.current_declared()
        with patch.object(handover, 'load_declared', side_effect=AssertionError('read again')):
            handover.current_declared()


class VerifyTests(unittest.TestCase):
    """H7: fresh connections to the console port, every answer 200 from the candidate."""

    def serve(self, pid_header='4002', status=200, delay=0, close_early=False):
        import http.server
        connections = []

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self_):
                connections.append(self_.client_address)
                if close_early:
                    self_.connection.close()
                    return
                time.sleep(delay)
                self_.send_response(status)
                if pid_header is not None:
                    self_.send_header('x-sbarbase-pid', pid_header)
                self_.send_header('content-length', '0')
                self_.send_header('connection', 'close')
                self_.end_headers()

            def log_message(self_, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port, connections

    def test_every_answer_from_the_candidate_passes_and_each_is_a_new_connection(self):
        port, connections = self.serve()
        ok, detail = handover.verify_console(port, 4002, timeout=30)
        self.assertTrue(ok, detail)
        self.assertEqual(len(connections), handover.VERIFY_CONNECTIONS)
        self.assertEqual(len(set(connections)), handover.VERIFY_CONNECTIONS)

    def test_one_answer_from_another_process_a_bad_status_or_no_header_fails(self):
        for label, options in (('the old process', dict(pid_header='4001')), ('a 503', dict(status=503)), ('no header', dict(pid_header=None))):
            with self.subTest(label):
                port, _ = self.serve(**options)
                ok, detail = handover.verify_console(port, 4002, timeout=30)
                self.assertFalse(ok)
                self.assertIn('answered by process', detail)

    def test_a_connection_that_is_refused_cut_or_slow_fails(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            closed = probe.getsockname()[1]
        ok, detail = handover.verify_console(closed, 4002)
        self.assertFalse(ok)
        self.assertIn('failed', detail)
        port, _ = self.serve(close_early=True)
        self.assertFalse(handover.verify_console(port, 4002)[0])
        port, _ = self.serve(delay=2)
        started = time.monotonic()
        ok, detail = handover.verify_console(port, 4002, timeout=.3)
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_the_defaults_are_the_designs(self):
        self.assertEqual((handover.VERIFY_CONNECTIONS, handover.VERIFY_TIMEOUT), (4, 1))
        self.assertEqual((handover.END_GRACE_SECONDS, handover.PROMOTE_SECONDS, handover.HANDOVER_SECONDS, handover.MEMORY_MARGIN_MIB), (20, 10, 90, 256))
        self.assertEqual((handover.CANDIDATE_EXIT, handover.SOL_SOCKET), (75, 1))


class RoundTests(unittest.TestCase):
    def test_rounds_run_one_at_a_time_an_interval_apart(self):
        clock = Clock(0)
        started = []
        pending = []

        def submit(function):
            future = concurrent.futures.Future()
            pending.append(future)
            started.append(clock.now)
            return future
        rounds = handover.Rounds(lambda: (True, 'ok'), clock, 2, submit)
        self.assertIsNone(rounds.poll())
        self.assertEqual(started, [0])
        for _ in range(20):
            clock.advance(.25)
            self.assertIsNone(rounds.poll())
        self.assertEqual(len(started), 1)
        pending[0].set_result((False, 'rest answered HTTP 503'))
        self.assertEqual(rounds.poll(), (False, 'rest answered HTTP 503'))
        clock.advance(1.9)
        self.assertIsNone(rounds.poll())
        self.assertEqual(len(started), 1)
        clock.advance(.2)
        self.assertIsNone(rounds.poll())
        self.assertEqual(len(started), 2)

    def test_a_round_that_raises_is_a_failed_round(self):
        clock = Clock(0)
        rounds = handover.Rounds(lambda: 1 / 0, clock, 2, immediately)
        rounds.poll()
        self.assertEqual(rounds.poll(), (False, 'health probe failed (ZeroDivisionError)'))
