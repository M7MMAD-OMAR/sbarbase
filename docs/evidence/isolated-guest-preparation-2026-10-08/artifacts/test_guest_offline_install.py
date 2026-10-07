"""Actual subprocess, bounded log and original clock regression fixtures."""
import hashlib
import importlib.util
import io
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
