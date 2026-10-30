"""Email templates: the file the console writes, the route address, and the variables Auth receives."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import mail_templates
import run as lab

E = 'e_' + 'a' * 24
OTHER = 'e_' + 'b' * 24
TOKEN = 'ab' * 32
MAIL = {'host': 'smtp.example.com', 'port': 587, 'user': 'u', 'pass': 'p', 'admin_email': 'a@example.com', 'sender_name': 'S', 'max_frequency': '60s',
        'autoconfirm': False, 'otp_exp': 3600, 'otp_length': 6, 'secure_email_change': True, 'rate_limit_email_sent': 30, 'rate_limit_otp': 30,
        'rate_limit_verify': 30, 'rate_limit_header': '', 'reply_to': '', 'magic_link_template': ''}


class TemplatesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = Path(tempfile.mkdtemp())
        (self.state / 'studio.json').write_text(json.dumps({'sessions': {}, 'upstream': {'host': '172.30.0.1', 'port': 25432}}))

    def write(self, e=E, templates=None, mode=0o600, token=TOKEN):
        file = self.root / f'{e}-templates.json'
        file.write_text(json.dumps({'version': 1, 'token': token, 'templates': templates if templates is not None else {}}))
        file.chmod(mode)
        return file

    def test_no_file_adds_nothing_and_a_file_without_templates_adds_nothing(self):
        self.assertEqual(mail_templates.configuration(E, self.root, self.state), {})
        self.write()
        self.assertEqual(mail_templates.configuration(E, self.root, self.state), {})

    def test_a_body_becomes_an_address_on_the_internal_route_and_a_subject_stays_text(self):
        self.write(templates={'confirmation': {'subject': 'Welcome', 'body': '<p>x</p>'}, 'recovery': {'subject': 'Reset', 'body': ''},
                              'magic_link': {'subject': '', 'body': '<p>m</p>'}})
        config = mail_templates.configuration(E, self.root, self.state)
        self.assertEqual(config, {
            'GOTRUE_MAILER_TEMPLATES_CONFIRMATION': f'http://172.30.0.1:25432/{E}/mail-templates/{TOKEN}/confirmation',
            'GOTRUE_MAILER_SUBJECTS_CONFIRMATION': 'Welcome',
            'GOTRUE_MAILER_SUBJECTS_RECOVERY': 'Reset',
            'GOTRUE_MAILER_TEMPLATES_MAGIC_LINK': f'http://172.30.0.1:25432/{E}/mail-templates/{TOKEN}/magic_link'})

    def test_each_environment_reads_only_its_own_file(self):
        self.write(templates={'confirmation': {'subject': 'A only', 'body': ''}})
        self.assertEqual(mail_templates.configuration(OTHER, self.root, self.state), {})

    def test_a_bad_file_raises_and_names_the_field(self):
        for mode in (0o644, 0o640):
            self.write(templates={'confirmation': {'subject': 'x', 'body': ''}}, mode=mode)
            with self.assertRaises(mail_templates.TemplatesError):
                mail_templates.load(E, self.root)
        for bad in ({'nope': {'subject': 'x'}}, {'confirmation': {'title': 'x'}}, {'confirmation': {'subject': 1}}, {'confirmation': {'subject': 'a\nb'}},
                    {'confirmation': {'body': 'x' * 32769}}, {'confirmation': 'x'}):
            self.write(templates=bad)
            with self.assertRaises(mail_templates.TemplatesError, msg=str(bad)[:40]):
                mail_templates.load(E, self.root)
        self.write(token='short')
        with self.assertRaises(mail_templates.TemplatesError):
            mail_templates.load(E, self.root)
        self.write()
        (self.root / f'{E}-templates.json').write_text('{"version": 2}')
        (self.root / f'{E}-templates.json').chmod(0o600)
        with self.assertRaises(mail_templates.TemplatesError):
            mail_templates.load(E, self.root)

    def test_a_link_is_refused_even_when_dangling(self):
        os.symlink(self.root / 'nowhere', self.root / f'{E}-templates.json')
        with self.assertRaises(mail_templates.TemplatesError):
            mail_templates.load(E, self.root)

    def test_templates_need_a_published_route_and_the_route_must_be_an_address(self):
        self.write(templates={'confirmation': {'subject': '', 'body': '<p>x</p>'}})
        (self.state / 'studio.json').write_text(json.dumps({'sessions': {}}))
        with self.assertRaises(mail_templates.TemplatesError):
            mail_templates.configuration(E, self.root, self.state)
        for upstream in ({'host': 'evil.example.com', 'port': 25432}, {'host': '172.30.0.1', 'port': 0}, {'host': '172.30.0.1', 'port': True}, {'host': '172.30.0.1/x', 'port': 1}):
            (self.state / 'studio.json').write_text(json.dumps({'sessions': {}, 'upstream': upstream}))
            self.assertIsNone(mail_templates.route(self.state), upstream)

    def test_the_port_is_the_one_studio_uses(self):
        import studio
        self.assertEqual(mail_templates.ROUTE_PORT, studio.UPSTREAM_PORT)

    def test_the_auth_configuration_carries_templates_only_with_mail_and_they_win_over_the_old_pointer(self):
        self.write(templates={'magic_link': {'subject': '', 'body': '<p>m</p>'}})
        values = {'auth': 'a', 'jwt': 'j'}
        original = mail_templates.DIRECTORY, mail_templates.STATE
        mail_templates.DIRECTORY, mail_templates.STATE = self.root, self.state
        try:
            without = lab.auth_configuration(E, values, 'db')
            self.assertFalse([key for key in without if 'MAILER_TEMPLATES' in key])
            with_mail = lab.auth_configuration(E, values, 'db', {**MAIL, 'magic_link_template': 'https://old.example.com/m'})
            self.assertEqual(with_mail['GOTRUE_MAILER_TEMPLATES_MAGIC_LINK'], f'http://172.30.0.1:25432/{E}/mail-templates/{TOKEN}/magic_link')
        finally:
            mail_templates.DIRECTORY, mail_templates.STATE = original



class PublishTests(unittest.TestCase):
    def test_the_gateway_address_is_recorded_once_and_nothing_else_in_the_studio_state_changes(self):
        import durable_runtime
        from unittest.mock import patch, MagicMock
        state = Path(tempfile.mkdtemp())
        (state / 'studio.json').write_text(json.dumps({'sessions': {E: {'url': 'http://x'}}}))
        with patch.object(durable_runtime, 'STATE', state), patch.object(durable_runtime.lab, 'docker', return_value=MagicMock(stdout='172.31.0.1\n')):
            durable_runtime.publish_internal_route()
            first = (state / 'studio.json').read_text()
            durable_runtime.publish_internal_route()
            self.assertEqual((state / 'studio.json').read_text(), first)
        document = json.loads(first)
        self.assertEqual(document['upstream'], {'host': '172.31.0.1', 'port': mail_templates.ROUTE_PORT})
        self.assertEqual(document['sessions'], {E: {'url': 'http://x'}})
        # A network with no IPv4 gateway records nothing.
        other = Path(tempfile.mkdtemp())
        with patch.object(durable_runtime, 'STATE', other), patch.object(durable_runtime.lab, 'docker', return_value=MagicMock(stdout='fe80::1\n')):
            durable_runtime.publish_internal_route()
        self.assertFalse((other / 'studio.json').exists())


class AppliedTests(unittest.TestCase):
    def test_a_restart_reads_the_applied_copy_and_never_a_save_waiting_for_its_apply(self):
        root, state = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (state / 'studio.json').write_text(json.dumps({'sessions': {}, 'upstream': {'host': '172.30.0.1', 'port': 25432}}))
        file = root / f'{E}-templates.json'
        file.write_text(json.dumps({'version': 1, 'token': TOKEN, 'templates': {'recovery': {'subject': 'Old', 'body': ''}}}))
        file.chmod(0o600)
        self.assertEqual(mail_templates.configuration(E, root, state, applied=True), {})
        mail_templates.write_applied(E, mail_templates.load(E, root), root)
        file.write_text(json.dumps({'version': 1, 'token': TOKEN, 'templates': {'recovery': {'subject': 'New', 'body': ''}}}))
        self.assertEqual(mail_templates.configuration(E, root, state, applied=True), {'GOTRUE_MAILER_SUBJECTS_RECOVERY': 'Old'})
        self.assertEqual(mail_templates.configuration(E, root, state), {'GOTRUE_MAILER_SUBJECTS_RECOVERY': 'New'})
        mail_templates.write_applied(E, None, root)
        self.assertEqual(mail_templates.configuration(E, root, state, applied=True), {})


class DriftTests(unittest.TestCase):
    def test_template_and_retired_keys_in_a_retained_auth_are_settings_not_drift(self):
        import durable_runtime
        base = {'GOTRUE_SITE_URL': 'http://localhost'}
        for key in ('GOTRUE_MAILER_TEMPLATES_CONFIRMATION', 'GOTRUE_MAILER_SUBJECTS_RECOVERY', 'GOTRUE_RATE_LIMIT_WEB3', 'GOTRUE_RATE_LIMIT_TOKEN_REFRESH',
                    'GOTRUE_RATE_LIMIT_ANONYMOUS_USERS', 'GOTRUE_SECURITY_REFRESH_TOKEN_ROTATION_ENABLED'):
            # A key the container has and the desired configuration lacks, and the other way round.
            self.assertTrue(durable_runtime.settings_only('auth', {**base, key: 'x'}, base), key)
            self.assertTrue(durable_runtime.settings_only('auth', base, {**base, key: 'x'}), key)
        self.assertFalse(durable_runtime.settings_only('auth', {**base, 'GOTRUE_JWT_SECRET': 'a'}, {**base, 'GOTRUE_JWT_SECRET': 'b'}))


if __name__ == '__main__':
    unittest.main()
