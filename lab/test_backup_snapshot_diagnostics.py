"""Exported snapshot sessions must not hide diagnostics or leak streams."""
import io
import subprocess
import unittest
from unittest.mock import patch
import backup


class SnapshotDiagnostics(unittest.TestCase):
    def session(self, diagnostic=b'', status=0):
        opened = []
        class Process:
            def __init__(self, argv, **kwargs):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO('00000003-0000002A-1\n1|1|1|1\n')
                self.finished = False
                stderr = kwargs['stderr']
                if stderr != subprocess.DEVNULL:
                    stderr.write(diagnostic)
                    stderr.flush()
                opened.append(self)
            def poll(self):
                return status if self.finished else None
            def wait(self, timeout=None):
                self.finished = True
                return status
            def kill(self):
                self.finished = True
        return Process, opened

    def test_diagnostic_refuses_snapshot_even_with_zero_exit(self):
        process, opened = self.session(b'WARNING: synthetic private payload\n')
        with patch.object(backup.subprocess, 'Popen', process), patch.object(backup, 'psql', return_value=['fixture']):
            with self.assertRaisesRegex(backup.BackupError, 'snapshot') as caught:
                with backup.snapshot('fixture'):
                    pass
        self.assertNotIn('private payload', str(caught.exception))
        self.assertTrue(opened[0].stdout.closed)

    def test_nonzero_commit_refuses_snapshot(self):
        process, _ = self.session(status=3)
        with patch.object(backup.subprocess, 'Popen', process), patch.object(backup, 'psql', return_value=['fixture']):
            with self.assertRaisesRegex(backup.BackupError, 'snapshot'):
                with backup.snapshot('fixture'):
                    pass

    def test_success_closes_both_streams(self):
        process, opened = self.session()
        with patch.object(backup.subprocess, 'Popen', process), patch.object(backup, 'psql', return_value=['fixture']):
            with backup.snapshot('fixture') as (exported, counts):
                self.assertEqual(counts['auth.users'], 1)
        self.assertTrue(opened[0].stdin.closed)
        self.assertTrue(opened[0].stdout.closed)
