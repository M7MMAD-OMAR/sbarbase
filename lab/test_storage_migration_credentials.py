import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest

from storage_migration_credentials import mint

ENV = 'e_' + 'a' * 24
KEY = 'fixture-signing-material-never-a-live-key'


class MigrationCredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'credential'
        self.options = {'now': 1000, 'read_secrets': lambda: {'environments': {ENV: {'jwt': KEY}}},
                        'read_endpoints': lambda: {ENV: {'storage': {'tenantHost': ENV + '.storage.internal'}}}}

    def test_exact_scope_signature_and_private_file_without_token_in_result(self):
        result = mint(ENV, 'originals', 'b' * 64, 1900, self.output, **self.options)
        token = self.output.read_text().strip()
        header, payload, signature = token.split('.')
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        self.assertEqual(claims['role'], ENV + '_fanio_migration_originals')
        self.assertEqual(claims['aud'], 'sbarbase:storage')
        self.assertEqual(claims['exp'], 1900)
        self.assertEqual(signature, base64.urlsafe_b64encode(hmac.new(KEY.encode(), (header + '.' + payload).encode(), hashlib.sha256).digest()).rstrip(b'=').decode())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))

    def test_invalid_scope_output_and_signing_custody_refuse_without_publication(self):
        cases = [
            {'environment': 'e_invalid'},
            {'purpose': 'runtime'},
            {'manifest_sha256': 'b' * 63},
            {'expires_at': True},
            {'expires_at': 1060},
            {'output': Path('relative-credential')},
            {'read_endpoints': lambda: {}},
            {'read_secrets': lambda: {}},
            {'read_secrets': lambda: {'environments': {ENV: {'jwt': 'short'}}}},
        ]
        for changed in cases:
            with self.subTest(fields=sorted(changed)):
                arguments = {'environment': ENV, 'purpose': 'originals', 'manifest_sha256': 'b' * 64,
                             'expires_at': 1900, 'output': self.output, **self.options, **changed}
                with self.assertRaises(ValueError):
                    mint(**arguments)
                self.assertFalse(self.output.exists())

    def test_no_overwrite_symlink_unsafe_parent_wrong_tenant_or_long_lifetime(self):
        for expiry in (1001, 5000):
            with self.assertRaises(ValueError):
                mint(ENV, 'evidence', 'b' * 64, expiry, self.output, **self.options)
        self.options['read_endpoints'] = lambda: {ENV: {'storage': {'tenantHost': 'neighbor.storage.internal'}}}
        with self.assertRaises(ValueError):
            mint(ENV, 'evidence', 'b' * 64, 1900, self.output, **self.options)
        self.options['read_endpoints'] = lambda: {ENV: {'storage': {'tenantHost': ENV + '.storage.internal'}}}
        self.output.symlink_to(Path(self.directory.name) / 'absent')
        with self.assertRaises(FileExistsError):
            mint(ENV, 'evidence', 'b' * 64, 1900, self.output, **self.options)
        self.output.unlink()
        os.chmod(self.directory.name, 0o755)
        with self.assertRaises(ValueError):
            mint(ENV, 'evidence', 'b' * 64, 1900, self.output, **self.options)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
