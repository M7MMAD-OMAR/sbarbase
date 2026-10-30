"""Phone sign-in settings: the file the console writes and the variables Auth receives."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import durable_runtime
import run as lab
import sms_config

E = 'e_' + 'a' * 24
OTHER = 'e_' + 'b' * 24


def document(provider='twilio', **change):
    base = {'version': 1, 'enabled': True, 'provider': provider, 'autoconfirm': False, 'rate_limit_sms_sent': 30,
            'twilio': {'account_sid': 'AC1', 'auth_token': 'TOKEN-1', 'message_service_sid': 'MG1'}}
    base.update(change)
    return base


class SmsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def write(self, value, e=E, mode=0o600):
        file = self.root / f'{e}-sms.json'
        file.write_text(json.dumps(value))
        file.chmod(mode)

    def test_no_file_adds_nothing_and_off_adds_nothing(self):
        self.assertEqual(sms_config.configuration(E, self.root), {})
        self.write(document(enabled=False))
        self.assertEqual(sms_config.configuration(E, self.root), {})
        self.assertFalse(sms_config.wanted(E, self.root))

    def test_a_provider_becomes_its_variables_and_needs_a_way_out(self):
        self.write(document(autoconfirm=True, rate_limit_sms_sent=5))
        self.assertEqual(sms_config.configuration(E, self.root), {
            'GOTRUE_EXTERNAL_PHONE_ENABLED': 'true', 'GOTRUE_SMS_PROVIDER': 'twilio', 'GOTRUE_SMS_AUTOCONFIRM': 'true',
            'GOTRUE_RATE_LIMIT_SMS_SENT': '5', 'GOTRUE_SMS_TWILIO_ACCOUNT_SID': 'AC1', 'GOTRUE_SMS_TWILIO_AUTH_TOKEN': 'TOKEN-1', 'GOTRUE_SMS_TWILIO_MESSAGE_SERVICE_SID': 'MG1'})
        # The way out follows what the running Auth was started with, not the saved file.
        self.assertFalse(sms_config.wanted(E, self.root))
        sms_config.write_applied(E, sms_config.load(E, self.root), self.root)
        self.assertTrue(sms_config.wanted(E, self.root))
        self.assertTrue(sms_config.wanted(E, document=sms_config.load(E, self.root)))

    def test_every_provider_maps_its_fields_to_the_names_the_live_check_tried(self):
        names = {'twilio': {'GOTRUE_SMS_TWILIO_ACCOUNT_SID', 'GOTRUE_SMS_TWILIO_AUTH_TOKEN', 'GOTRUE_SMS_TWILIO_MESSAGE_SERVICE_SID'},
                 'twilio_verify': {'GOTRUE_SMS_TWILIO_VERIFY_ACCOUNT_SID', 'GOTRUE_SMS_TWILIO_VERIFY_AUTH_TOKEN', 'GOTRUE_SMS_TWILIO_VERIFY_MESSAGE_SERVICE_SID'},
                 'messagebird': {'GOTRUE_SMS_MESSAGEBIRD_ACCESS_KEY', 'GOTRUE_SMS_MESSAGEBIRD_ORIGINATOR'},
                 'textlocal': {'GOTRUE_SMS_TEXTLOCAL_API_KEY', 'GOTRUE_SMS_TEXTLOCAL_SENDER'},
                 'vonage': {'GOTRUE_SMS_VONAGE_API_KEY', 'GOTRUE_SMS_VONAGE_API_SECRET', 'GOTRUE_SMS_VONAGE_FROM'}}
        self.assertEqual(set(names), set(sms_config.PROVIDERS))
        for provider, expected in names.items():
            block = {field: 'v-' + field for field, _, _ in sms_config.PROVIDERS[provider]}
            self.write({**document(provider), provider: block})
            config = sms_config.configuration(E, self.root)
            self.assertEqual({key for key in config if key.startswith('GOTRUE_SMS_') and key not in ('GOTRUE_SMS_PROVIDER', 'GOTRUE_SMS_AUTOCONFIRM')}, expected, provider)

    def test_only_the_chosen_provider_is_carried_and_each_environment_reads_its_own_file(self):
        self.write(document(vonage={'api_key': 'k', 'api_secret': 's', 'from': 'f'}))
        self.assertNotIn('vonage', sms_config.load(E, self.root))
        self.assertEqual(sms_config.configuration(OTHER, self.root), {})

    def test_a_bad_file_raises_and_names_the_field(self):
        self.write(document(), mode=0o644)
        with self.assertRaises(sms_config.SmsError):
            sms_config.load(E, self.root)
        for bad in (document(provider='nope'), document(autoconfirm='no'), document(max_frequency='60s'), document(rate_limit_sms_sent=0),
                    document(rate_limit_sms_sent=True), document(twilio={'account_sid': 'AC1'}), document(twilio={'account_sid': '', 'auth_token': 't', 'message_service_sid': 'm'}),
                    document(twilio={'account_sid': 'a\nb', 'auth_token': 't', 'message_service_sid': 'm'}), document(extra=1), {**document(), 'version': 2}):
            self.write(bad)
            with self.assertRaises(sms_config.SmsError, msg=str(bad)[:60]):
                sms_config.load(E, self.root)
        os.remove(self.root / f'{E}-sms.json')
        os.symlink(self.root / 'nowhere', self.root / f'{E}-sms.json')
        with self.assertRaises(sms_config.SmsError):
            sms_config.load(E, self.root)

    def test_the_auth_configuration_carries_it_with_or_without_mail_and_the_management_realm_never_does(self):
        self.write(document())
        sms_config.write_applied(E, sms_config.load(E, self.root), self.root)
        original = sms_config.DIRECTORY
        sms_config.DIRECTORY = self.root
        try:
            config = lab.auth_configuration(E, {'auth': 'a', 'jwt': 'j'}, 'db')
            self.assertEqual(config['GOTRUE_SMS_PROVIDER'], 'twilio')
            self.assertEqual(lab.auth_configuration('management', {'auth': 'a', 'jwt': 'j'}, 'db').get('GOTRUE_SMS_PROVIDER'), None)
            self.assertTrue(durable_runtime.egress_wanted(None, None, E))
            self.assertFalse(durable_runtime.egress_wanted(None, None, OTHER))
            self.assertFalse(durable_runtime.egress_wanted(None, None))
        finally:
            sms_config.DIRECTORY = original

    def test_its_variables_belong_to_the_sign_in_settings_so_a_change_is_not_drift(self):
        import auth_settings
        for key in ('GOTRUE_SMS_PROVIDER', 'GOTRUE_SMS_TWILIO_AUTH_TOKEN', 'GOTRUE_RATE_LIMIT_SMS_SENT', 'GOTRUE_EXTERNAL_PHONE_ENABLED', 'GOTRUE_HOOK_CUSTOM_ACCESS_TOKEN_URI',
                    'GOTRUE_SAML_ENABLED', 'GOTRUE_SAML_PRIVATE_KEY', 'GOTRUE_AUDIT_LOG_DISABLE_POSTGRES'):
            self.assertTrue(auth_settings.owned(key), key)
            self.assertFalse(durable_runtime.is_mail_key(key), key)


    def test_a_restart_reads_the_applied_copy_and_never_a_save_waiting_for_its_apply(self):
        self.write(document())
        self.assertEqual(sms_config.configuration(E, self.root, applied=True), {})
        sms_config.write_applied(E, sms_config.load(E, self.root), self.root)
        self.write(document(rate_limit_sms_sent=7))
        self.assertEqual(sms_config.configuration(E, self.root, applied=True)['GOTRUE_RATE_LIMIT_SMS_SENT'], '30')
        self.assertEqual(sms_config.configuration(E, self.root)['GOTRUE_RATE_LIMIT_SMS_SENT'], '7')
        self.assertEqual((self.root / f'{E}-sms.applied.json').stat().st_mode & 0o777, 0o600)
        sms_config.write_applied(E, None, self.root)
        self.assertEqual(sms_config.configuration(E, self.root, applied=True), {})


if __name__ == '__main__':
    unittest.main()
