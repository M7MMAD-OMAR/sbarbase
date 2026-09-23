"""The operation lock guard every recovery and fence script runs its main under."""
import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import durable_runtime as runtime


class RunLockedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name)
        patcher = patch.object(runtime, 'STATE', self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.directory.cleanup)

    def test_main_runs_while_the_operation_lock_is_held(self):
        seen = []

        def main():
            with (self.state/'operation.lock').open('a') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append('ran')
        runtime.run_locked(main, 'failed')
        self.assertEqual(seen, ['ran'])

    def test_a_failure_exits_with_only_the_given_message(self):
        def main():
            raise RuntimeError('secret detail')
        with self.assertRaises(SystemExit) as raised:
            runtime.run_locked(main, 'Probe failed; sensitive output withheld')
        self.assertEqual(str(raised.exception), 'Probe failed; sensitive output withheld')
        self.assertIsNone(raised.exception.__cause__)

    def test_a_held_lock_refuses_before_main_runs(self):
        seen = []
        with (self.state/'operation.lock').open('a') as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SystemExit):
                runtime.run_locked(lambda: seen.append('ran'), 'busy')
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
