"""The Authentication registry (src/control/auth-config-registry.json) against what this side writes.

The console reads the registry to show each setting under its platform key name; auth_settings.py
turns saved settings into GOTRUE_ variables. If the two disagree, the console would show a setting
Auth never receives. Every key here must reach the Auth environment under GOTRUE_ plus its own name,
with the value the console shows for it.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import auth_settings

REGISTRY = json.loads((Path(__file__).resolve().parents[1] / 'src' / 'control' / 'auth-config-registry.json').read_text())
E = 'e_' + 'c' * 24
CONFIG = {entry['key']: (True if entry['type'] == 'bool' else entry['example'] if entry['type'] == 'text' else entry['min'])
          for entry in REGISTRY['core'] if entry.get('store') == 'config'}
FULL = {'revision': 1, 'config': CONFIG, 'site_url': 'https://app.example.com', 'redirect_urls': ['https://app.example.com/**', 'myapp://cb'],
        'signup': False, 'anonymous': True, 'manual_linking': True,
        'session': {'access_token_seconds': 1800, 'timebox_seconds': 7200, 'inactivity_seconds': 600},
        'providers': {name: {'enabled': True, 'client_id': 'id-' + name, 'secret': 'secret-' + name,
                             'url': 'https://idp.example.com/' + name if meta['url'] != 'none' else '',
                             'skip_nonce_check': name in REGISTRY['nonce_providers'], 'email_optional': True}
                      for name, meta in REGISTRY['providers'].items()}}


class RegistryParityTests(unittest.TestCase):
    def setUp(self):
        self.validated = auth_settings.validate(FULL)
        with patch('saml_key.load', return_value='KEY'):
            self.config = auth_settings.configuration(E, self.validated)

    def test_the_providers_are_the_ones_this_side_accepts(self):
        self.assertEqual(sorted(REGISTRY['providers']), sorted(auth_settings.PROVIDERS))
        for name, meta in REGISTRY['providers'].items():
            self.assertEqual(meta['url'] != 'none', name in auth_settings.PROVIDER_URL, name)
            self.assertEqual(meta['url'] == 'required', auth_settings.PROVIDER_URL.get(name) is True, name)

    def test_every_core_key_reaches_auth_under_its_own_name(self):
        for entry in REGISTRY['core']:
            variable = 'GOTRUE_' + entry['key']
            self.assertIn(variable, self.config, entry['key'])
            value = self.config[variable]
            saved = self.validated
            for part in entry['from'].split('.'):
                saved = saved[part]
            if entry['type'] == 'bool':
                saved = (not saved) if entry.get('invert') and not entry.get('store') else saved
                self.assertEqual(value, 'true' if saved else 'false', entry['key'])
            elif entry['type'] == 'list':
                self.assertEqual(value, ','.join(saved), entry['key'])
            elif entry['type'] == 'int':
                self.assertEqual(value, str(saved), entry['key'])
            elif entry['type'] == 'seconds':
                # JWT_EXP is a count of seconds; the two caps are Go durations in seconds.
                self.assertEqual(value, str(saved) if entry['key'] == 'JWT_EXP' else f'{saved}s', entry['key'])
            else:
                self.assertEqual(value, saved, entry['key'])

    def test_every_provider_field_reaches_auth_under_its_own_name(self):
        callback = auth_settings.public_url() + '/' + E + '/auth/v1/callback'
        for name, meta in REGISTRY['providers'].items():
            prefix = 'GOTRUE_EXTERNAL_' + name.upper() + '_'
            for field in REGISTRY['provider_fields']:
                if field.get('only') == 'url' and meta['url'] == 'none':
                    self.assertNotIn(prefix + 'URL', self.config, name)
                    continue
                if field.get('only') == 'nonce' and name not in REGISTRY['nonce_providers']:
                    self.assertNotIn(prefix + field['suffix'], self.config, name)
                    continue
                variable = prefix + field['suffix']
                self.assertIn(variable, self.config, variable)
                if field.get('computed'):
                    self.assertEqual(self.config[variable], callback)
                elif field['type'] == 'bool':
                    self.assertEqual(self.config[variable], 'true')
                else:
                    self.assertEqual(self.config[variable], self.validated['providers'][name][field['from']])

    def test_the_registry_names_no_variable_this_side_does_not_set(self):
        # The reverse direction: a variable this module owns and writes must be explained by the registry.
        # The SAML key is not a setting: it is the environment's own file (lab/saml_key.py).
        known = {'GOTRUE_' + e['key'] for e in REGISTRY['core']} | {'API_EXTERNAL_URL', 'GOTRUE_SAML_PRIVATE_KEY'}
        known |= {'GOTRUE_EXTERNAL_' + n.upper() + '_' + f['suffix'] for n in REGISTRY['providers'] for f in REGISTRY['provider_fields']}
        for variable in self.config:
            self.assertIn(variable, known, variable)

    def test_a_disabled_provider_passes_nothing_to_auth(self):
        settings = auth_settings.validate({**FULL, 'providers': {'google': {'enabled': False, 'client_id': 'g', 'secret': 'x', 'url': ''}}})
        with patch('saml_key.load', return_value='KEY'):
            config = auth_settings.configuration(E, settings)
        self.assertEqual([k for k in config if k.startswith('GOTRUE_EXTERNAL_GOOGLE_')], [])

    def test_the_defaults_the_registry_claims_are_the_ones_the_base_configuration_sets(self):
        import run as lab
        base = lab.auth_configuration(E, {'auth': 'a', 'jwt': 'j'}, 'db')
        for entry in REGISTRY['core']:
            variable = 'GOTRUE_' + entry['key']
            if entry['key'] == 'SITE_URL':
                self.assertEqual(base[variable], entry['default'])
            elif entry['default'] is None:
                # No default claimed: this repository sets nothing, so the pinned Auth's own applies.
                self.assertNotIn(variable, base, entry['key'])
            elif entry['type'] == 'bool':
                self.assertNotIn(variable, base, entry['key'])


if __name__ == '__main__':
    unittest.main()


class SamlKeyTests(unittest.TestCase):
    def test_saml_on_without_a_key_is_refused_and_with_one_reaches_auth(self):
        settings = auth_settings.validate({**FULL, 'config': {'SAML_ENABLED': True}})
        with patch('saml_key.load', return_value=None):
            with self.assertRaises(auth_settings.SettingsError):
                auth_settings.configuration(E, settings)
        import saml_key
        with patch('saml_key.load', side_effect=saml_key.SamlKeyError('file')):
            with self.assertRaises(auth_settings.SettingsError):
                auth_settings.configuration(E, settings)
        with patch('saml_key.load', return_value='THE-KEY'):
            config = auth_settings.configuration(E, settings)
        self.assertEqual((config['GOTRUE_SAML_ENABLED'], config['GOTRUE_SAML_PRIVATE_KEY']), ('true', 'THE-KEY'))
        # Off adds neither variable, and asks for no key.
        off = auth_settings.validate({**FULL, 'config': {'SAML_ENABLED': False}})
        with patch('saml_key.load', side_effect=AssertionError('no key is read when SAML is off')):
            self.assertNotIn('GOTRUE_SAML_PRIVATE_KEY', auth_settings.configuration(E, off))


class TextSettingTests(unittest.TestCase):
    def test_a_hook_address_must_name_a_postgres_function_and_empty_clears_it(self):
        base = {**FULL, 'config': {}}
        good = 'pg-functions://postgres/public/my_hook'
        self.assertEqual(auth_settings.validate({**base, 'config': {'HOOK_SEND_EMAIL_URI': good}})['config'], {'HOOK_SEND_EMAIL_URI': good})
        self.assertEqual(auth_settings.validate({**base, 'config': {'HOOK_SEND_EMAIL_URI': ''}})['config'], {})
        for bad in ('https://example.com/hook', 'pg-functions://postgres/public/x;drop', 'pg-functions://postgres/1bad/x', 'pg-functions://other/public/x',
                    'pg-functions://postgres/public/x\n', 'pg-functions://postgres/public/' + 'a' * 64, 1, True):
            with self.assertRaises(auth_settings.SettingsError, msg=str(bad)[:40]):
                auth_settings.validate({**base, 'config': {'HOOK_SEND_EMAIL_URI': bad}})
