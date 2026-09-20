"""Retained runtime safety tests; no Docker daemon mutations."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import run as lab


class RuntimeReuseTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.actual = {'Image': 'sha256:config-digest', 'Config': {
            'Labels': {'io.sbarbase.owner': 'component-lab'},
            'Env': ['JWT_SECRET=private=value', 'IMAGE_DEFAULT=ok']}}
        self.image = {'Id': 'sha256:config-digest'}

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            value = [self.image]
        elif args[0] == 'inspect':
            value = [self.actual]
        else:
            value = []
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    def launch(self):
        with patch.object(lab, 'owned', return_value=True), patch.object(lab, 'docker', self.docker):
            lab.launch('retained', 'sha256:manifest-digest', {'JWT_SECRET': 'private=value'}, '256m', .25)

    def test_resolves_manifest_digest_and_keeps_image_defaults(self):
        self.launch()
        self.assertEqual(self.calls[-1], ('start', 'retained'))
        self.assertIn(('image', 'inspect', 'sha256:manifest-digest'), self.calls)

    def test_changed_image_never_starts_or_removes_container(self):
        self.actual['Image'] = 'sha256:old'
        with self.assertRaisesRegex(RuntimeError, 'explicit migration'):
            self.launch()
        self.assertTrue(all(call[0] in ('inspect', 'image') for call in self.calls))

    def test_changed_secret_fails_without_disclosing_value(self):
        self.actual['Config']['Env'] = ['JWT_SECRET=stale-secret']
        with self.assertRaises(RuntimeError) as error:
            self.launch()
        self.assertNotIn('stale-secret', str(error.exception))
        self.assertNotIn('private=value', str(error.exception))
        self.assertNotIn(('start', 'retained'), self.calls)

    def test_missing_configuration_is_not_accepted(self):
        self.actual['Config']['Env'] = []
        with self.assertRaisesRegex(RuntimeError, 'configuration differs'):
            self.launch()

    def test_rechecks_owner_after_initial_lookup(self):
        self.actual['Config']['Labels']['io.sbarbase.owner'] = 'someone-else'
        with self.assertRaisesRegex(RuntimeError, 'ownership mismatch'):
            self.launch()
        self.assertNotIn(('start', 'retained'), self.calls)

    def test_unresolved_image_is_not_accepted(self):
        self.image = {}
        with self.assertRaises(RuntimeError):
            self.launch()


if __name__ == '__main__':
    unittest.main()
