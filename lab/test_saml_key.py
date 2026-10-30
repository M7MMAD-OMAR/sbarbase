import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

import saml_key

E = 'e_' + 'a' * 24


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def write(self, document, mode=0o600):
        file = self.root / f'{E}-saml.json'
        file.write_text(json.dumps(document))
        file.chmod(mode)

    def test_no_file_is_none_and_a_good_file_is_the_key(self):
        self.assertIsNone(saml_key.load(E, self.root))
        key = base64.b64encode(b'k' * 1190).decode()
        self.write({'version': 1, 'private_key': key})
        self.assertEqual(saml_key.load(E, self.root), key)

    def test_a_bad_file_raises_and_another_environment_has_none(self):
        key = base64.b64encode(b'k' * 1190).decode()
        self.write({'version': 1, 'private_key': key}, mode=0o644)
        with self.assertRaises(saml_key.SamlKeyError):
            saml_key.load(E, self.root)
        for bad in ({'version': 2, 'private_key': key}, {'version': 1}, {'version': 1, 'private_key': 'not base64!'}, {'version': 1, 'private_key': base64.b64encode(b'short').decode()}, []):
            self.write(bad)
            with self.assertRaises(saml_key.SamlKeyError, msg=str(bad)[:40]):
                saml_key.load(E, self.root)
        self.assertIsNone(saml_key.load('e_' + 'b' * 24, self.root))
        os.remove(self.root / f'{E}-saml.json')
        os.symlink(self.root / 'nowhere', self.root / f'{E}-saml.json')
        with self.assertRaises(saml_key.SamlKeyError):
            saml_key.load(E, self.root)


if __name__ == '__main__':
    unittest.main()
