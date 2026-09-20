"""The operator bootstrap file is created, never leaked.

An install reads its operator identity from a 0600 JSON object piped on stdin.
If an operator types that file by hand the password lands in the shell's
history, and if it is passed as an argument it lands in the process list. These
tests pin the writer's refusals: the wrong fields, a short password, a path that
is not absolute, a symlink, an existing file, and stdin larger than the bound.
"""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lab'))

import operator_file
TOOL = ROOT / 'lab' / 'operator_file.py'
PASSWORD = 'correct-horse-battery-staple'
PAYLOAD = {'email': 'owner@example.test', 'organization': 'Acme', 'password': PASSWORD}


def run(*args, stdin=b'', input_text=None):
    """Run the tool. Secrets are passed on stdin, never as arguments."""
    return subprocess.run([sys.executable, str(TOOL), *args], input=input_text if input_text is not None else stdin,
                          capture_output=True)


class OperatorFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'operator.json'

    def payload(self, **overrides):
        return json.dumps(dict(PAYLOAD, **overrides)).encode()

    def write(self, *args, strict=True, **kwargs):
        result = run(str(self.path), '--stdin', *args, stdin=self.payload(**kwargs.pop('payload', {})))
        if strict:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_it_writes_exactly_the_three_fields_with_mode_0600(self):
        result = self.write()
        self.assertIn('mode 0600', result.stdout.decode())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_text()), PAYLOAD)

    def test_the_password_never_appears_in_stdout_or_stderr(self):
        result = self.write()
        self.assertNotIn(PASSWORD, result.stdout.decode())
        self.assertNotIn(PASSWORD, result.stderr.decode())
        self.assertNotIn(PASSWORD, self.path.name)

    def test_it_refuses_an_existing_file_unless_forced(self):
        self.write()
        refused = self.write(strict=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('exists', refused.stderr.decode())
        self.assertEqual(json.loads(self.path.read_text()), PAYLOAD)
        replaced = run(str(self.path), '--stdin', '--force', stdin=self.payload(email='second@example.test'))
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertEqual(json.loads(self.path.read_text())['email'], 'second@example.test')

    def test_it_refuses_fields_the_install_would_reject(self):
        for payload, expected in (({'email': 'owner@example.test'}, 'exactly'),
                                  ({'email': 'owner@example.test', 'organization': 'Acme', 'password': PASSWORD, 'extra': 'x'}, 'exactly'),
                                  ({}, 'exactly')):
            result = run(str(self.path), '--stdin', stdin=json.dumps(payload).encode())
            self.assertEqual(result.returncode, 1)
            self.assertIn(expected, result.stderr.decode())
            self.assertFalse(self.path.exists())

    def test_it_refuses_a_short_password_and_a_malformed_email(self):
        short = run(str(self.path), '--stdin', stdin=json.dumps(dict(PAYLOAD, password='elevenchars')).encode())
        self.assertEqual(short.returncode, 1)
        self.assertIn('at least 12', short.stderr.decode())
        for email in ('owner', 'two@at@once', 'owner@example.test '):
            malformed = run(str(self.path), '--stdin', stdin=json.dumps(dict(PAYLOAD, email=email)).encode())
            self.assertEqual(malformed.returncode, 1)
            self.assertIn('email', malformed.stderr.decode())
        self.assertFalse(self.path.exists())

    def test_it_refuses_a_relative_path_a_missing_parent_and_a_symlink(self):
        relative = run('operator.json', '--stdin', stdin=self.payload())
        self.assertEqual(relative.returncode, 1)
        self.assertIn('absolute', relative.stderr.decode())
        missing = run(str(Path(self.directory.name) / 'nowhere' / 'operator.json'), '--stdin', stdin=self.payload())
        self.assertEqual(missing.returncode, 1)
        self.assertIn('parent directory', missing.stderr.decode())
        link = Path(self.directory.name) / 'link.json'
        link.symlink_to(self.path)
        through_link = run(str(link), '--stdin', '--force', stdin=self.payload())
        self.assertEqual(through_link.returncode, 1)
        self.assertIn('symlink', through_link.stderr.decode())
        self.assertFalse(self.path.exists(), 'the symlink target must not be written')

    def test_it_refuses_stdin_past_the_bound_without_writing(self):
        oversized = b' ' * 8193 + self.payload()
        result = run(str(self.path), '--stdin', stdin=oversized)
        self.assertEqual(result.returncode, 1)
        self.assertIn('8192', result.stderr.decode())
        self.assertFalse(self.path.exists())

    def test_it_refuses_a_prompt_without_a_terminal(self):
        result = run(str(self.path), input_text=b'')
        self.assertEqual(result.returncode, 1)
        self.assertIn('--stdin', result.stderr.decode())
        self.assertFalse(self.path.exists())

    def test_a_failed_write_leaves_nothing_behind(self):
        result = subprocess.run([sys.executable, str(TOOL), str(self.path), '--stdin'],
                                input=self.payload(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with mock.patch('os.fdopen', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                operator_file.write_payload(self.path, PAYLOAD, force=True)
        self.assertFalse(self.path.exists(), 'a failed write must not leave a partial file')


if __name__ == '__main__':
    unittest.main()
