"""Per environment mail configuration: schema, private storage, and the builder's disabled posture.

The load bearing assertions, in the order the design lists them:

- The builder without a mail argument and the builder with None produce equal
  dicts, compared as rendered text, not by eye.
- The management realm's three argument call is unchanged.
- No key of an unconfigured dict starts with GOTRUE_SMTP_.
- GOTRUE_MAILER_AUTOCONFIRM is still 'true' in the unconfigured dict.
- An invalid mail file raises instead of returning None.
- An absent mail file returns None.
- The password never appears in argv, in stdout, in stderr or in an error message.

The construction tests also pin the file mode, the git ignore guard, the
autoconfirm override, and the four states the console renders.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lab'))

import mail_config
import run as lab

TOOL = ROOT / 'lab' / 'mail_config.py'
RUNTIME_ID = 'e_1f0624c545789214eef426c9'
NEIGHBOUR_ID = 'e_60332245e3a0426dd242492f'
PASSWORD = 'probe-password-4f6a1c8e2b9d'
VALUES = {'auth': 'a' * 64, 'rest': 'b' * 64, 'jwt': 'c' * 64}
CONFIG = {
    'host': 'mailpit-a', 'port': 1025, 'user': 'mailprobe', 'pass': PASSWORD,
    'admin_email': 'noreply@a.example.test', 'sender_name': 'Sbarbase A probe',
    'reply_to': 'support@a.example.test', 'max_frequency': '1s', 'otp_exp': 300,
    'otp_length': 6, 'secure_email_change': True, 'autoconfirm': False,
    'rate_limit_email_sent': '30', 'rate_limit_otp': 30, 'rate_limit_verify': 30,
    'rate_limit_header': '',
}
MAIL_KEYS = (
    'GOTRUE_SMTP_HOST', 'GOTRUE_SMTP_PORT', 'GOTRUE_SMTP_USER', 'GOTRUE_SMTP_PASS',
    'GOTRUE_SMTP_ADMIN_EMAIL', 'GOTRUE_SMTP_SENDER_NAME', 'GOTRUE_SMTP_MAX_FREQUENCY',
    'GOTRUE_SMTP_LOGGING_ENABLED', 'GOTRUE_MAILER_AUTOCONFIRM', 'GOTRUE_MAILER_OTP_EXP',
    'GOTRUE_MAILER_OTP_LENGTH', 'GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED',
    'GOTRUE_RATE_LIMIT_EMAIL_SENT', 'GOTRUE_RATE_LIMIT_OTP', 'GOTRUE_RATE_LIMIT_VERIFY',
    'GOTRUE_RATE_LIMIT_HEADER', 'GOTRUE_SMTP_HEADERS')


def rendered(configuration):
    """One deterministic rendering of a builder result, for byte comparison."""
    return json.dumps(configuration, sort_keys=True, separators=(';', '='))


class BuilderTests(unittest.TestCase):
    def test_the_builder_without_mail_is_byte_identical_to_the_builder_with_none(self):
        without = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db')
        explicitly_none = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', None)
        self.assertEqual(rendered(without), rendered(explicitly_none))
        self.assertEqual(without, explicitly_none)

    def test_the_management_realm_three_argument_call_is_unchanged(self):
        management = {'auth': 'd' * 64, 'jwt': 'e' * 64}
        self.assertEqual(lab.auth_configuration('management', management, 'sbarbase-db'),
                         lab.auth_configuration('management', management, 'sbarbase-db', None))

    def test_no_key_of_an_unconfigured_environment_starts_with_smtp(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', None)
        self.assertEqual([key for key in configuration if key.startswith('GOTRUE_SMTP_')], [])

    def test_autoconfirm_is_still_true_in_the_unconfigured_dict(self):
        self.assertEqual(lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db')['GOTRUE_MAILER_AUTOCONFIRM'], 'true')

    def test_the_mail_block_adds_exactly_the_designed_keys(self):
        without = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db')
        with_mail = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', dict(CONFIG))
        self.assertEqual(set(with_mail) - set(without), set(MAIL_KEYS) - {'GOTRUE_MAILER_AUTOCONFIRM'})
        self.assertEqual(set(without) - set(with_mail), set())
        for key, value in without.items():
            if key not in ('GOTRUE_MAILER_AUTOCONFIRM',):
                self.assertEqual(with_mail[key], value)

    def test_the_mail_block_carries_the_configured_values(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', dict(CONFIG))
        self.assertEqual(configuration['GOTRUE_SMTP_HOST'], 'mailpit-a')
        self.assertEqual(configuration['GOTRUE_SMTP_PORT'], '1025')
        self.assertEqual(configuration['GOTRUE_SMTP_USER'], 'mailprobe')
        self.assertEqual(configuration['GOTRUE_SMTP_PASS'], PASSWORD)
        self.assertEqual(configuration['GOTRUE_SMTP_ADMIN_EMAIL'], 'noreply@a.example.test')
        self.assertEqual(configuration['GOTRUE_SMTP_SENDER_NAME'], 'Sbarbase A probe')
        self.assertEqual(configuration['GOTRUE_SMTP_MAX_FREQUENCY'], '1s')
        self.assertEqual(configuration['GOTRUE_MAILER_AUTOCONFIRM'], 'false')
        self.assertEqual(configuration['GOTRUE_MAILER_OTP_EXP'], '300')
        self.assertEqual(configuration['GOTRUE_MAILER_OTP_LENGTH'], '6')
        self.assertEqual(configuration['GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED'], 'true')
        self.assertEqual(configuration['GOTRUE_RATE_LIMIT_EMAIL_SENT'], '30')
        self.assertEqual(configuration['GOTRUE_RATE_LIMIT_OTP'], '30')
        self.assertEqual(configuration['GOTRUE_RATE_LIMIT_VERIFY'], '30')
        self.assertEqual(configuration['GOTRUE_RATE_LIMIT_HEADER'], '')

    def test_the_recipient_address_logging_switch_is_fixed_false(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', dict(CONFIG))
        self.assertEqual(configuration['GOTRUE_SMTP_LOGGING_ENABLED'], 'false')

    def test_reply_to_travels_as_a_json_header(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', dict(CONFIG))
        self.assertEqual(json.loads(configuration['GOTRUE_SMTP_HEADERS']), {'Reply-To': ['support@a.example.test']})

    def test_the_reply_to_header_is_omitted_when_empty(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', dict(CONFIG, reply_to=''))
        self.assertNotIn('GOTRUE_SMTP_HEADERS', configuration)

    def test_autoconfirm_true_and_secure_email_change_false_reach_the_container_as_lowercase(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db',
                                              dict(CONFIG, autoconfirm=True, secure_email_change=False))
        self.assertEqual(configuration['GOTRUE_MAILER_AUTOCONFIRM'], 'true')
        self.assertEqual(configuration['GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED'], 'false')

    def test_an_integer_rate_limit_reaches_the_container_as_text(self):
        configuration = lab.auth_configuration(RUNTIME_ID, VALUES, 'sbarbase-db', dict(CONFIG, rate_limit_email_sent=30))
        self.assertEqual(configuration['GOTRUE_RATE_LIMIT_EMAIL_SENT'], '30')


class SchemaTests(unittest.TestCase):
    def payload(self, **overrides):
        return dict(CONFIG, **overrides)

    def test_the_schema_holds_exactly_the_designed_keys(self):
        self.assertEqual(mail_config.KEY_ORDER, (
            'host', 'port', 'user', 'pass', 'admin_email', 'sender_name', 'reply_to', 'max_frequency',
            'otp_exp', 'otp_length', 'secure_email_change', 'autoconfirm', 'rate_limit_email_sent',
            'rate_limit_otp', 'rate_limit_verify', 'rate_limit_header'))

    def test_a_valid_object_is_normalized(self):
        config = mail_config.validate(self.payload(rate_limit_email_sent=30))
        self.assertEqual(config['rate_limit_email_sent'], '30')
        self.assertEqual(set(config), set(mail_config.KEY_ORDER))

    def test_the_four_states_are_the_designed_four(self):
        self.assertEqual(mail_config.STATES, ('unconfigured', 'applied', 'failed', 'off'))
        self.assertEqual(set(mail_config.STATE_TEXT), set(mail_config.STATES))
        self.assertEqual(mail_config.FAILURE_REASONS,
                         ('invalid_configuration', 'smtp_unreachable', 'smtp_rejected', 'smtp_tls'))

    def test_a_missing_key_is_refused(self):
        payload = self.payload()
        payload.pop('otp_exp')
        with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
            mail_config.validate(payload)
        self.assertIn('otp_exp', str(caught.exception))

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
            mail_config.validate(self.payload(passw=PASSWORD))
        self.assertIn('passw', str(caught.exception))

    def test_a_bad_type_is_refused(self):
        with self.assertRaises(mail_config.InvalidMailConfiguration):
            mail_config.validate(self.payload(autoconfirm='false'))
        with self.assertRaises(mail_config.InvalidMailConfiguration):
            mail_config.validate(self.payload(otp_exp=True))

    def test_a_port_outside_the_range_is_refused(self):
        for port in (0, 65536, -1, '587'):
            with self.assertRaises(mail_config.InvalidMailConfiguration):
                mail_config.validate(self.payload(port=port))

    def test_a_host_with_whitespace_or_a_scheme_is_refused(self):
        for host in ('mail pit', 'smtp://mailpit', 'mailpit:1025', ''):
            with self.assertRaises(mail_config.InvalidMailConfiguration):
                mail_config.validate(self.payload(host=host))

    def test_a_bad_address_shape_is_refused(self):
        for field in ('admin_email', 'reply_to'):
            for value in ('noreply', 'a b@example.test', '<a@example.test>', 'a@b@c.test'):
                with self.assertRaises(mail_config.InvalidMailConfiguration):
                    mail_config.validate(self.payload(**{field: value}))

    def test_an_empty_reply_to_is_accepted(self):
        self.assertEqual(mail_config.validate(self.payload(reply_to=''))['reply_to'], '')

    def test_an_otp_length_outside_six_to_ten_is_refused(self):
        for length in (5, 11, 0):
            with self.assertRaises(mail_config.InvalidMailConfiguration):
                mail_config.validate(self.payload(otp_length=length))

    def test_a_bad_or_zero_duration_is_refused(self):
        for frequency in ('60 seconds', '0s', '0m', '', 'every minute'):
            with self.assertRaises(mail_config.InvalidMailConfiguration):
                mail_config.validate(self.payload(max_frequency=frequency))

    def test_a_duration_with_two_units_is_accepted(self):
        self.assertEqual(mail_config.validate(self.payload(max_frequency='1h30m'))['max_frequency'], '1h30m')

    def test_the_rate_limit_accepts_a_number_or_an_over_time_suffix(self):
        self.assertEqual(mail_config.validate(self.payload(rate_limit_email_sent=30))['rate_limit_email_sent'], '30')
        self.assertEqual(mail_config.validate(self.payload(rate_limit_email_sent='100/1h'))['rate_limit_email_sent'], '100/1h')
        for value in ('30 per hour', 'per hour', '', True):
            with self.assertRaises(mail_config.InvalidMailConfiguration):
                mail_config.validate(self.payload(rate_limit_email_sent=value))

    def test_zero_sent_mails_is_accepted_and_warned_about(self):
        config = mail_config.validate(self.payload(rate_limit_email_sent=0))
        self.assertEqual(config['rate_limit_email_sent'], '0')
        self.assertTrue(any('429' in said for said in mail_config.warnings(config)))

    def test_an_empty_password_is_refused_and_an_empty_user_is_only_warned_about(self):
        with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
            mail_config.validate(self.payload(**{'pass': ''}))
        self.assertIn('pass', str(caught.exception))
        config = mail_config.validate(self.payload(user=''))
        self.assertTrue(any('no SMTP AUTH' in said for said in mail_config.warnings(config)))

    def test_autoconfirm_true_is_warned_about_with_the_section(self):
        config = mail_config.validate(self.payload(autoconfirm=True))
        said = [text for text in mail_config.warnings(config) if '3.4' in text]
        self.assertEqual(len(said), 1)

    def test_a_header_carrying_a_space_is_refused(self):
        for header in ('x client ip', 'x-client-ip;'):
            with self.assertRaises(mail_config.InvalidMailConfiguration):
                mail_config.validate(self.payload(rate_limit_header=header))
        self.assertEqual(mail_config.validate(self.payload(rate_limit_header=''))['rate_limit_header'], '')
        self.assertEqual(mail_config.validate(self.payload(rate_limit_header='x-forwarded-for'))['rate_limit_header'],
                         'x-forwarded-for')

    def test_an_error_message_never_carries_a_value(self):
        secrets_used = [PASSWORD, 'admin@example.test', 'a b']
        payloads = (self.payload(**{'port': 99999, 'pass': PASSWORD}),
                    self.payload(**{'admin_email': 'admin@example', 'pass': PASSWORD}),
                    self.payload(**{'host': 'a b', 'user': PASSWORD}))
        for payload in payloads:
            with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
                mail_config.validate(payload)
            for value in secrets_used:
                self.assertNotIn(value, str(caught.exception))
        with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
            mail_config.parse(b'{"pass": "' + PASSWORD.encode() + b'"')
        self.assertNotIn(PASSWORD, str(caught.exception))

    def test_summarize_hides_the_password_and_the_user_value(self):
        summary = mail_config.summarize(mail_config.validate(dict(CONFIG)))
        self.assertEqual(summary['pass'], 'set')
        self.assertEqual(summary['user'], 'set')
        self.assertNotIn(PASSWORD, rendered(summary))
        self.assertNotIn('mailprobe', rendered(summary))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / (RUNTIME_ID + '-mail.json')

    def test_an_absent_mail_file_returns_none(self):
        self.assertIsNone(mail_config.load(RUNTIME_ID, self.directory.name))
        self.assertIsNone(mail_config.load(NEIGHBOUR_ID, self.directory.name))

    def test_a_valid_mail_file_loads_its_configuration(self):
        mail_config.write_config(self.path, mail_config.validate(dict(CONFIG)))
        loaded = mail_config.load(RUNTIME_ID, self.directory.name)
        self.assertEqual(loaded, mail_config.validate(dict(CONFIG)))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_an_invalid_mail_file_raises_instead_of_returning_none(self):
        mail_config.write_config(self.path, dict(CONFIG, otp_length=42))
        with self.assertRaises(mail_config.InvalidMailConfiguration):
            mail_config.load(RUNTIME_ID, self.directory.name)

    def test_a_mail_file_that_is_not_json_raises(self):
        mail_config.write_config(self.path, CONFIG)
        self.path.write_text('not json at all')
        with self.assertRaises(mail_config.InvalidMailConfiguration):
            mail_config.load(RUNTIME_ID, self.directory.name)

    def test_a_mail_file_readable_by_others_raises(self):
        mail_config.write_config(self.path, mail_config.validate(dict(CONFIG)))
        os.chmod(self.path, 0o644)
        with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
            mail_config.load(RUNTIME_ID, self.directory.name)
        self.assertIn('0o644', str(caught.exception))

    def test_a_mail_file_that_is_a_symlink_raises(self):
        target = Path(self.directory.name) / 'elsewhere.json'
        target.write_text(json.dumps(CONFIG))
        self.path.symlink_to(target)
        with self.assertRaises(mail_config.InvalidMailConfiguration):
            mail_config.load(RUNTIME_ID, self.directory.name)

    def test_an_invalid_environment_identifier_is_refused(self):
        for identifier in ('management', 'e_1F0624C545789214EEF426C9', 'e_short', ''):
            with self.assertRaises(mail_config.MailConfigurationError):
                mail_config.path_for(identifier, self.directory.name)

    def test_the_writer_creates_mode_0600_and_refuses_an_existing_file(self):
        mode = mail_config.write_config(self.path, mail_config.validate(dict(CONFIG)))
        self.assertEqual(mode, 0o600)
        with self.assertRaises(FileExistsError):
            mail_config.write_config(self.path, mail_config.validate(dict(CONFIG)))

    def test_the_writer_replaces_a_file_only_when_forced(self):
        mail_config.write_config(self.path, mail_config.validate(dict(CONFIG)))
        mail_config.write_config(self.path, mail_config.validate(dict(CONFIG, host='mailpit-b')), force=True)
        self.assertEqual(mail_config.load(RUNTIME_ID, self.directory.name)['host'], 'mailpit-b')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)


class CommandTests(unittest.TestCase):
    """The command surface, exercised the way an operator would run it."""

    def setUp(self):
        # A directory git already ignores, so the ignore guard is really exercised.
        self.directory = ROOT / 'lab' / '__pycache__' / 'mail-config-tests'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.remove_directory)
        self.path = self.directory / (RUNTIME_ID + '-mail.json')

    def remove_directory(self):
        for entry in sorted(self.directory.glob('*')):
            entry.unlink()
        self.directory.rmdir()

    def run_tool(self, *args, **kwargs):
        return subprocess.run([sys.executable, str(TOOL), *args], input=kwargs.pop('data', b''),
                              capture_output=True, **kwargs)

    def test_write_then_show_prints_pass_set_and_never_the_password(self):
        written = self.run_tool('write', str(self.path), '--stdin', data=json.dumps(CONFIG).encode())
        self.assertEqual(written.returncode, 0, written.stderr)
        shown = self.run_tool('show', str(self.path))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        text = shown.stdout.decode()
        self.assertIn('pass set', text)
        self.assertIn('user set', text)
        self.assertIn('mailpit-a', text)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn('mailprobe', text)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_the_password_never_appears_in_stdout_stderr_or_argv(self):
        result = self.run_tool('write', str(self.path), '--stdin', data=json.dumps(CONFIG).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(PASSWORD, result.stdout.decode())
        self.assertNotIn(PASSWORD, result.stderr.decode())
        for argument in result.args:
            self.assertNotIn(PASSWORD, argument)
        with self.assertRaises(mail_config.InvalidMailConfiguration) as caught:
            mail_config.parse(json.dumps(dict(CONFIG, **{'pass': ''})).encode())
        self.assertNotIn(PASSWORD, str(caught.exception))

    def test_an_invalid_object_is_refused_without_echoing_it(self):
        result = self.run_tool('write', str(self.path), '--stdin', data=json.dumps(dict(CONFIG, port=70000)).encode())
        self.assertEqual(result.returncode, 1)
        self.assertIn('port', result.stderr.decode())
        self.assertFalse(self.path.exists())

    def test_autoconfirm_true_is_refused_without_the_flag_and_warned_about_with_it(self):
        payload = json.dumps(dict(CONFIG, autoconfirm=True)).encode()
        refused = self.run_tool('write', str(self.path), '--stdin', data=payload)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('3.4', refused.stderr.decode())
        self.assertFalse(self.path.exists())
        allowed = self.run_tool('write', str(self.path), '--stdin', '--allow-autoconfirm', data=payload)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn('warning', allowed.stderr.decode())
        self.assertEqual(mail_config.load(RUNTIME_ID, str(self.directory))['autoconfirm'], True)

    def test_a_path_git_does_not_ignore_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / (RUNTIME_ID + '-mail.json')
            result = self.run_tool('write', str(path), '--stdin', data=json.dumps(CONFIG).encode())
            self.assertEqual(result.returncode, 1)
            self.assertIn('ignored', result.stderr.decode())
            self.assertFalse(path.exists())

    def test_a_relative_path_is_refused(self):
        result = self.run_tool('write', RUNTIME_ID + '-mail.json', '--stdin', data=json.dumps(CONFIG).encode())
        self.assertEqual(result.returncode, 1)
        self.assertIn('absolute', result.stderr.decode())

    def test_a_file_name_that_is_not_a_mail_file_is_refused(self):
        path = self.directory / 'something-else.json'
        result = self.run_tool('write', str(path), '--stdin', data=json.dumps(CONFIG).encode())
        self.assertEqual(result.returncode, 1)
        self.assertIn('-mail.json', result.stderr.decode())

    def test_an_existing_file_needs_the_force_flag(self):
        self.assertEqual(self.run_tool('write', str(self.path), '--stdin', data=json.dumps(CONFIG).encode()).returncode, 0)
        again = self.run_tool('write', str(self.path), '--stdin', data=json.dumps(CONFIG).encode())
        self.assertEqual(again.returncode, 1)
        self.assertIn('--force', again.stderr.decode())
        forced = self.run_tool('write', str(self.path), '--stdin', '--force', data=json.dumps(CONFIG).encode())
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_remove_deletes_the_file_and_a_missing_file_is_refused(self):
        self.assertEqual(self.run_tool('write', str(self.path), '--stdin', data=json.dumps(CONFIG).encode()).returncode, 0)
        removed = self.run_tool('remove', str(self.path))
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.path.exists())
        self.assertIsNone(mail_config.load(RUNTIME_ID, str(self.directory)))
        self.assertEqual(self.run_tool('remove', str(self.path)).returncode, 1)

    def test_show_refuses_a_file_that_is_not_there(self):
        result = self.run_tool('show', str(self.path))
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not exist', result.stderr.decode())


if __name__ == '__main__':
    unittest.main()