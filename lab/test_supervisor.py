"""Runner lifecycle tests with owned local children; no Docker mutations."""
import collections
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import dev

PYTHON = '/usr/bin/python3'


class SupervisorTests(unittest.TestCase):
    def test_stage_exit_status(self):
        self.assertEqual(dev.run_stage([PYTHON, '-c', 'raise SystemExit(7)'], threading.Event()), 7)

    def test_cancelled_stage_reaped(self):
        children = []
        popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child
        stop = threading.Event()
        stop.set()
        with patch.object(dev.subprocess, 'Popen', spawn):
            with self.assertRaises(InterruptedError):
                dev.run_stage([PYTHON, '-c', 'import time; time.sleep(60)'], stop)
        self.assertIsNotNone(children[0].poll())

    def test_stage_timeout(self):
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            dev.run_stage([PYTHON, '-c', 'import time; time.sleep(60)'], threading.Event(), timeout=.1)
        self.assertLess(time.monotonic()-start, 5)

    def test_parent_retains_lock_after_worker_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'worker.lock'
            with path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                child = subprocess.Popen([PYTHON, '-c', 'pass'], pass_fds=(lock.fileno(),), start_new_session=True)
                child.wait(timeout=5)
                result = subprocess.run([PYTHON, '-c',
                    'import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)',
                    str(path)], capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)

    def test_restart_limit_preserves_live_server(self):
        supervisor = dev.Supervisor()
        supervisor.server = subprocess.Popen([PYTHON, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        supervisor.worker = subprocess.Popen([PYTHON, '-c', 'pass'], start_new_session=True)
        supervisor.worker.wait(timeout=5)
        supervisor.restarts = collections.deque([time.monotonic()]*3)
        try:
            with self.assertRaisesRegex(RuntimeError, 'restart limit'):
                supervisor.check()
            self.assertIsNone(supervisor.server.poll())
        finally:
            dev.terminate_group(supervisor.server, grace=0)


if __name__ == '__main__':
    unittest.main()
