"""Actual subprocess, bounded log and original clock regression fixtures."""
import hashlib
import importlib.util
import io
import json
import tarfile
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('guest_fixture', Path(__file__).with_name('guest_offline_install.py'))
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class GuestPreparationTests(unittest.TestCase):
    def test_successful_leader_retires_remaining_original_group(self):
        with tempfile.TemporaryDirectory() as directory:
            area = Path(directory)
            script = "import os,time; p=os.fork(); (time.sleep(20) if p==0 else open('child','w').write(str(p)))"
            now = time.monotonic()
            guest.command([sys.executable, '-I', '-B', '-c', script], area,
                          {'PATH': '/usr/bin:/bin'}, now + 3, 'group', area,
                          cleanup_deadline=now + 6)
            pid = int((area / 'child').read_text())
            end = time.monotonic() + 1
            while time.monotonic() < end:
                try:
                    state = Path('/proc', str(pid), 'stat').read_text().split(') ', 1)[1][0]
                except FileNotFoundError:
                    break
                if state == 'Z':
                    break
                time.sleep(0.01)
            else:
                self.fail('Original remaining command group member stayed live')
            self.assertEqual(guest.RETAINED_COMMANDS, [])

    def test_large_regular_command_output_is_not_limited_by_diagnostic_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            area = Path(directory)
            now = time.monotonic()
            guest.command([sys.executable, '-I', '-B', '-c',
                           'import pathlib; pathlib.Path("large").write_bytes(b"x"*(2<<20)); print("ok")'],
                          area, {'PATH': '/usr/bin:/bin'}, now + 3, 'regular', area,
                          cleanup_deadline=now + 6)
            self.assertEqual((area / 'large').stat().st_size, 2 << 20)
            self.assertEqual((area / 'regular.stdout').read_bytes(), b'ok\n')
            self.assertEqual(guest.RETAINED_COMMANDS, [])

    def test_both_full_diagnostic_streams_drain_without_mutual_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            area = Path(directory)
            now = time.monotonic()
            guest.command([sys.executable, '-I', '-B', '-c',
                           'import os; os.write(1, b"x"*(1<<20)); os.write(2, b"y"*(1<<20))'],
                          area, {'PATH': '/usr/bin:/bin'}, now + 3, 'dual', area,
                          cleanup_deadline=now + 6)
            self.assertEqual((area / 'dual.stdout').stat().st_size, 1 << 20)
            self.assertEqual((area / 'dual.stderr').stat().st_size, 1 << 20)
            self.assertEqual(guest.RETAINED_COMMANDS, [])

    def test_raw_log_byte_limit_is_enforced_and_child_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            area = Path(directory)
            now = time.monotonic()
            with self.assertRaises(BaseExceptionGroup):
                guest.command([sys.executable, '-I', '-B', '-c',
                               'import os; os.write(1, b"x"*(2<<20)); os.write(1, b"overflow")'], area,
                              {'PATH': '/usr/bin:/bin'}, now + 3, 'limit', area,
                              cleanup_deadline=now + 6)
            self.assertLessEqual((area / 'limit.stdout').stat().st_size, guest.MAX_LOG_BYTES)
            self.assertEqual(guest.RETAINED_COMMANDS, [])

    def test_actual_write_cannot_return_success_after_original_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'output'
            original_open = Path.open

            class SlowOutput:
                def __enter__(self):
                    self.output = original_open(target, 'xb')
                    return self
                def write(self, data):
                    count = self.output.write(data)
                    self.output.flush()
                    time.sleep(0.05)
                    return count
                def __exit__(self, *args):
                    self.output.close()

            class Destination:
                parent = target.parent
                def open(self, mode):
                    return SlowOutput()

            with self.assertRaisesRegex(RuntimeError, 'deadline exhausted'):
                guest.write_member(io.BytesIO(b'actual'), Destination(), 6,
                                   hashlib.sha256(b'actual').hexdigest(), time.monotonic() + 0.03)
            self.assertEqual(target.read_bytes(), b'actual')

    def test_expired_zero_byte_input_does_not_create_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'absent' / 'output'
            with self.assertRaisesRegex(RuntimeError, 'deadline exhausted'):
                guest.write_member(io.BytesIO(), target, 0, hashlib.sha256(b'').hexdigest(),
                                   time.monotonic() - 1)
            self.assertFalse(target.parent.exists())

    def test_independent_same_inode_log_reopen_is_refused_without_foreign_close(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'log'
            log = guest.OriginalLog()
            deadline = time.monotonic() + 3
            log.acquire(target, deadline)
            replacement = os.open(target, os.O_WRONLY)
            try:
                os.dup2(replacement, log.fd)
                with self.assertRaisesRegex(RuntimeError, 'open file description changed'):
                    log.close(deadline)
                os.fstat(log.fd)
                self.assertEqual(log.state, 'owned')
                os.dup2(log.anchor, log.fd)
                log.close(deadline)
                self.assertTrue(log.closed)
            finally:
                os.close(replacement)

    def test_new_bound_source_snapshot_is_not_restricted_to_historical_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory) / 'public'
            public.mkdir()
            output = Path(directory) / 'application'
            output.mkdir()
            commit = 'a' * 40
            value = b'fresh published source fixture'
            inventory = {'commit': commit, 'members': {'lab/fixture.py': {
                'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest(), 'mode': '100644'}}}
            (public / 'source-manifest.json').write_text(json.dumps(inventory))
            with tarfile.open(public / 'source.tar.gz', 'w:gz') as archive:
                info = tarfile.TarInfo('sbarbase-' + commit + '/lab/fixture.py')
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
            observed = guest.unpack_source(public, output, time.monotonic() + 3)
            self.assertEqual(observed['commit'], commit)
            self.assertEqual((output / 'lab/fixture.py').read_bytes(), value)

    def test_noncanonical_source_commit_is_refused_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            (public / 'source-manifest.json').write_text(json.dumps({'commit': '../escape', 'members': {}}))
            output = public / 'absent'
            with self.assertRaisesRegex(RuntimeError, 'Canonical published source commit'):
                guest.unpack_source(public, output, time.monotonic() + 3)
            self.assertFalse(output.exists())

    def test_actual_stalled_pipe_obeys_original_input_deadline(self):
        reader, writer = os.pipe()
        try:
            with self.assertRaisesRegex(RuntimeError, 'deadline exhausted'):
                guest.DeadlineInput(reader, time.monotonic() + 0.04).read(1)
        finally:
            os.close(reader)
            os.close(writer)


if __name__ == '__main__':
    unittest.main()
