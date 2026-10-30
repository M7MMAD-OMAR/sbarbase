"""The console's SMTP page (src/control/smtp.ts) writes the mail file this module's validator reads."""
import json
import unittest
from pathlib import Path

import mail_config

DEFAULTS = {key: value for key, value in json.loads(
    (Path(__file__).resolve().parents[1] / 'src' / 'control' / 'mail-defaults.json').read_text()).items() if key != 'about'}
CLOUD = {'host': 'smtp.example.com', 'port': 587, 'user': 'apikey', 'pass': 'a-secret', 'admin_email': 'noreply@example.com', 'sender_name': 'Example'}


class ConsoleMailFileTests(unittest.TestCase):
    def test_the_defaults_plus_the_seven_fields_are_a_valid_mail_file(self):
        config = mail_config.validate({**DEFAULTS, **CLOUD})
        self.assertEqual(config['host'], 'smtp.example.com')
        self.assertFalse(config['autoconfirm'])

    def test_the_defaults_name_exactly_the_keys_the_console_does_not_ask_for(self):
        self.assertEqual(set(DEFAULTS) | set(CLOUD), set(mail_config.KEY_ORDER))

    def test_the_inputs_the_console_refuses_are_refused_here_too(self):
        for change in ({'host': 'smtp://x'}, {'host': 'a b'}, {'host': ''}, {'port': 0}, {'port': 70000}, {'port': '587'},
                       {'admin_email': 'no-at-sign'}, {'admin_email': 'a b@x.co'}, {'admin_email': '<a@b.co>'},
                       {'sender_name': 'a<b'}, {'sender_name': 'a\nb'}, {'user': 'a\nb'}, {'pass': ''}, {'pass': 'a\nb'}):
            with self.assertRaises(mail_config.InvalidMailConfiguration, msg=str(change)):
                mail_config.validate({**DEFAULTS, **CLOUD, **change})

    def test_the_confirmation_and_limit_values_the_console_writes_are_valid_here(self):
        config = mail_config.validate({**DEFAULTS, **CLOUD, 'autoconfirm': True, 'rate_limit_email_sent': 100, 'rate_limit_otp': 20,
                                       'rate_limit_verify': 40, 'max_frequency': '30s'})
        self.assertTrue(config['autoconfirm'])
        self.assertEqual(config['max_frequency'], '30s')
        # The console is stricter than the file format for the email limit (it refuses 0).
        for change in ({'rate_limit_otp': 0}, {'max_frequency': '0s'}, {'max_frequency': '30'}):
            with self.assertRaises(mail_config.InvalidMailConfiguration, msg=str(change)):
                mail_config.validate({**DEFAULTS, **CLOUD, **change})

    def test_the_console_values_reach_auth_under_the_right_variables(self):
        import run as lab
        mail = mail_config.validate({**DEFAULTS, **CLOUD, 'autoconfirm': True, 'rate_limit_email_sent': 100, 'max_frequency': '30s'})
        config = lab.auth_configuration('e_' + 'a' * 24, {'auth': 'a', 'jwt': 'j'}, 'db', mail)
        self.assertEqual((config['GOTRUE_RATE_LIMIT_EMAIL_SENT'], config['GOTRUE_SMTP_MAX_FREQUENCY'], config['GOTRUE_MAILER_AUTOCONFIRM']),
                         ('100', '30s', 'true'))
        self.assertEqual(config['GOTRUE_SMTP_HOST'], 'smtp.example.com')

    def test_a_file_the_console_wrote_loads_with_the_loader_rules(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as directory:
            runtime = 'e_' + 'a' * 24
            path = Path(directory) / f'{runtime}-mail.json'
            path.write_text(json.dumps({**DEFAULTS, **CLOUD}))
            path.chmod(0o600)
            self.assertEqual(mail_config.load(runtime, directory)['pass'], 'a-secret')
            path.chmod(0o644)
            with self.assertRaises(mail_config.MailConfigurationError):
                mail_config.load(runtime, directory)


if __name__ == '__main__':
    unittest.main()
