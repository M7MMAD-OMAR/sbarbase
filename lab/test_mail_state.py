"""The non secret mail state the console reads, one entry per environment.

The file this module writes is the only mail surface that leaves the runtime:
it must never carry a value that authenticates, and its mode must stay 0600.
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import mail_config
import mail_state

MAIL = {'host': 'smtp.example.com', 'port': 587, 'user': 'operator@example.com',
        'pass': 'a-password-that-must-not-be-recorded', 'admin_email': 'from@example.com',
        'sender_name': 'Example', 'reply_to': '', 'max_frequency': '1s', 'otp_exp': 3600,
        'otp_length': 6, 'secure_email_change': True, 'autoconfirm': False,
        'rate_limit_email_sent': '30', 'rate_limit_otp': 30, 'rate_limit_verify': 30,
        'rate_limit_header': ''}


class MailStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())

    def test_the_summary_never_carries_the_password(self):
        entry = mail_state.summary('applied', MAIL)
        self.assertEqual(entry['credentials'], 'set')
        self.assertEqual(entry['pass'], 'set')
        self.assertNotIn('a-password-that-must-not-be-recorded', json.dumps(entry))

    def test_an_environment_without_mail_records_no_credentials(self):
        entry = mail_state.summary('off', None)
        self.assertEqual(entry['credentials'], 'none')
        self.assertEqual(entry['state'], 'off')

    def test_record_keeps_one_entry_per_environment(self):
        mail_state.record('e_' + 'a' * 24, 'applied', MAIL, directory=self.directory, at=1)
        mail_state.record('e_' + 'b' * 24, 'off', None, directory=self.directory, at=2)
        stored = mail_state.read(directory=self.directory)
        self.assertEqual(sorted(stored), ['e_' + 'a' * 24, 'e_' + 'b' * 24])
        self.assertEqual(stored['e_' + 'b' * 24]['state'], 'off')
        self.assertNotIn('a-password-that-must-not-be-recorded',
                         (self.directory / mail_state.FILE).read_text())

    def test_the_recorded_file_is_private(self):
        mail_state.record('e_' + 'c' * 24, 'applied', MAIL, directory=self.directory)
        mode = stat.S_IMODE(os.stat(self.directory / mail_state.FILE).st_mode)
        self.assertEqual(mode, 0o600)

    def test_an_unknown_state_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown mail state'):
            mail_state.record('e_' + 'd' * 24, 'somewhere', MAIL, directory=self.directory)

    def test_an_entry_needs_an_environment(self):
        with self.assertRaisesRegex(ValueError, 'runtime identifier'):
            mail_state.record('', 'applied', MAIL, directory=self.directory)

    def test_reading_an_unwritten_state_returns_nothing(self):
        self.assertEqual(mail_state.read(directory=self.directory), {})

    def test_the_state_vocabulary_is_the_configuration_vocabulary(self):
        self.assertEqual(mail_state.STATES, mail_config.STATES)


class RecordedStatesInTheRuntime(unittest.TestCase):
    """Every state the runtime records must be one the vocabulary defines.

    A source level check, because the failure it prevents is a state name the
    caller invents: an unknown one raises inside record() at the moment the
    operator runs the command, which no test with a fixture would have seen.
    """

    def test_every_record_call_passes_a_defined_state(self):
        import ast
        import durable_runtime as runtime
        tree = ast.parse(Path(runtime.__file__).read_text())
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == 'record' and getattr(node.func.value, 'id', None) == 'mail_state']
        self.assertTrue(calls, 'no mail_state.record call site found to check')
        for call in calls:
            self.assertGreaterEqual(len(call.args), 2, 'a record call without a state')
            states = [node.value for node in ast.walk(call.args[1]) if isinstance(node, ast.Constant)]
            self.assertTrue(states, 'a record call whose state is not a literal')
            for state in states:
                self.assertIn(state, mail_config.STATES, ast.unparse(call))


if __name__ == '__main__':
    unittest.main()