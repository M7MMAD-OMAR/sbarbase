"""The Authentication registry (src/control/auth-config-registry.json) against what this side writes.

The console reads the registry to show each setting under its platform key name; auth_settings.py
turns saved settings into GOTRUE_ variables. If the two disagree, the console would show a setting
Auth never receives. Every key here must reach the Auth environment under GOTRUE_ plus its own name,
with the value the console shows for it.
"""
import json
import unittest
from pathlib import Path

import auth_settings

REGISTRY = json.loads((Path(__file__).resolve().parents[1] / 'src' / 'control' / 'auth-config-registry.json').read_text())
E = 'e_' + 'c' * 24
CONFIG = {entry['key']: (True if entry['type'] == 'bool' else entry['min'])
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
                saved = (not saved) if entry.get('invert') else saved
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
        known = {'GOTRUE_' + e['key'] for e in REGISTRY['core']} | {'API_EXTERNAL_URL'}
        known |= {'GOTRUE_EXTERNAL_' + n.upper() + '_' + f['suffix'] for n in REGISTRY['providers'] for f in REGISTRY['provider_fields']}
        for variable in self.config:
            self.assertIn(variable, known, variable)

    def test_a_disabled_provider_passes_nothing_to_auth(self):
        settings = auth_settings.validate({**FULL, 'providers': {'google': {'enabled': False, 'client_id': 'g', 'secret': 'x', 'url': ''}}})
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
