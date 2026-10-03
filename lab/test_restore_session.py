import io
import subprocess
import sys
import unittest

from restore_session import HeldSession, SessionError


# The child is a pipe transport fixture, not PostgreSQL behavior evidence.
CHILD = '''import sys
for line in sys.stdin:
    if line.startswith("SELECT '__sbarbase_restore_"):
        print(line.split("'")[1], flush=True)
    elif line.strip() == 'VALUE;':
        print('value', flush=True)
    elif line.strip() == 'WARN;':
        print('private diagnostic', file=sys.stderr, flush=True)
    elif line.strip() == 'FAIL;':
        raise SystemExit(3)
    elif line.strip() == 'MANY;':
        print('x' * 2000, flush=True)
    elif line.strip() == 'FLOOD;':
        print('private-' * 1000000, file=sys.stderr, flush=True)
'''


class HeldRestoreSessionTests(unittest.TestCase):
    def test_same_process_multiple_exchanges_large_input_and_clean_pipe_closure(self):
        session = HeldSession([sys.executable, '-c', CHILD], timeout=5)
        with session:
            pid = session.process.pid
            self.assertEqual(session.execute('VALUE;\n'), 'value')
            self.assertEqual(session.replay(io.BytesIO(b'-- ignored\n' * 100000)), '')
            self.assertEqual(session.execute('VALUE;\n'), 'value')
            self.assertEqual(session.process.pid, pid)
        self.assertEqual(session.process.returncode, 0)
        self.assertTrue(session.process.stdin.closed)
        self.assertTrue(session.process.stdout.closed)
        self.assertTrue(session.diagnostics.closed)
        with self.assertRaises(SessionError):
            session.execute('VALUE;')

    def test_diagnostics_refuse_without_exposing_payload(self):
        session = HeldSession([sys.executable, '-c', CHILD], timeout=5)
        with self.assertRaises(SessionError) as failure, session:
            session.execute('WARN;\n')
        self.assertNotIn('private diagnostic', str(failure.exception))
        self.assertIn(b'private diagnostic', session.private_diagnostic)
        self.assertLessEqual(len(session.private_diagnostic), 4096)
        self.assertTrue(session.process.stdout.closed)

    def test_diagnostic_flood_is_bounded_and_kills_owned_child_without_logging(self):
        session = HeldSession([sys.executable, '-c', CHILD], timeout=5)
        with self.assertRaises(SessionError) as failure, session:
            session.execute('FLOOD;\n')
        self.assertGreater(len(session.private_diagnostic), 0)
        self.assertLessEqual(len(session.private_diagnostic), 4096)
        self.assertNotIn('private-', str(failure.exception))
        self.assertIsNotNone(session.process.returncode)

    def test_nonzero_exit_refuses_and_closes_pipes(self):
        session = HeldSession([sys.executable, '-c', CHILD], timeout=5)
        with self.assertRaises(SessionError), session:
            session.execute('FAIL;\n')
        self.assertEqual(session.process.returncode, 3)
        self.assertTrue(session.process.stdout.closed)

    def test_excess_output_refuses(self):
        with self.assertRaises(SessionError), HeldSession([sys.executable, '-c', CHILD], timeout=5, output_limit=100) as session:
            session.execute('MANY;\n')

    def test_missing_confirmation_times_out_and_reaps_owned_child(self):
        session = HeldSession([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=.01)
        with self.assertRaises(SessionError), session:
            session.execute('VALUE;\n')
        self.assertIsNotNone(session.process.returncode)
        self.assertTrue(session.process.stdout.closed)


if __name__ == '__main__':
    unittest.main()
