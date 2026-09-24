"""Off-site copies: request signing, chunked encryption, and push, retention and fetch against a bucket in memory."""
import datetime
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup
import offsite

E = 'e_' + 'a' * 24
PASS = 'correct horse battery staple'


class SigningTests(unittest.TestCase):
    def test_the_signature_matches_the_aws_worked_example(self):
        # "Example: GET Object" in the AWS Signature Version 4 documentation for S3.
        headers = offsite.signed_headers(
            'GET', 'https://examplebucket.s3.amazonaws.com/test.txt', 'us-east-1', 'AKIAIOSFODNN7EXAMPLE',
            'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', {'Range': 'bytes=0-9'},
            payload=hashlib.sha256(b'').hexdigest(), now=datetime.datetime(2013, 5, 24, tzinfo=datetime.UTC))
        self.assertEqual(headers['authorization'],
                         'AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, '
                         'SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, '
                         'Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41')


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = patch.object(offsite, 'CHUNK', 1024)
        patcher.start()
        self.addCleanup(patcher.stop)

    def seal(self, data, passphrase=PASS):
        source = self.root / 'plain'
        source.write_bytes(data)
        salt = os.urandom(16)
        reader = offsite.EncryptingReader(source, offsite.derive_key(passphrase, salt), salt)
        sealed = b''.join(iter(lambda: reader.read(700), b''))
        reader.close()
        self.assertEqual(len(sealed), offsite.encrypted_size(len(data)))
        return sealed

    def open(self, sealed, passphrase=PASS):
        target = self.root / 'opened'
        offsite.decrypt_stream(io.BytesIO(sealed), target, passphrase)
        return target.read_bytes()

    def test_files_of_every_size_come_back_exactly_and_never_in_the_clear(self):
        for size in (0, 1, 1023, 1024, 1025, 3000, 4096):
            data = os.urandom(size // 2) + b'PGDMP' * (size // 10) + os.urandom(size - size // 2 - 5 * (size // 10))
            sealed = self.seal(data)
            if size >= 10:
                self.assertNotIn(b'PGDMPPGDMP', sealed)
            self.assertEqual(self.open(sealed), data, size)

    def test_a_wrong_passphrase_a_changed_byte_or_a_missing_chunk_is_refused(self):
        data = os.urandom(3000)
        sealed = self.seal(data)
        with self.assertRaises(offsite.OffsiteError):
            self.open(sealed, 'another passphrase')
        changed = bytearray(sealed)
        changed[100] ^= 1
        with self.assertRaises(offsite.OffsiteError):
            self.open(bytes(changed))
        with self.assertRaises(offsite.OffsiteError):
            self.open(sealed[:28 + 1024 + 16])
        with self.assertRaises(offsite.OffsiteError):
            self.open(b'not a backup')


class MemoryBucket:
    objects = {}

    def __init__(self, config):
        self.config = config

    def request(self, method, key='', query='', body=None, length=None, stream=False):
        if method == 'DELETE':
            self.objects.pop(key, None)
            return b''
        if method == 'GET':
            if key not in self.objects:
                return b''
            return io.BytesIO(self.objects[key]) if stream else self.objects[key]
        raise AssertionError(method)

    def put_file(self, key, path, passphrase):
        salt = os.urandom(16)
        reader = offsite.EncryptingReader(path, offsite.derive_key(passphrase, salt), salt)
        self.objects[key] = b''.join(iter(lambda: reader.read(65536), b''))
        reader.close()

    def put_bytes(self, key, data):
        self.objects[key] = data

    def keys(self, prefix):
        return sorted(key for key in self.objects if key.startswith(prefix))


class PushAndFetchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        MemoryBucket.objects = {}
        self.config = offsite.validate({'endpoint': 'https://storage.example.com', 'bucket': 'my-backups', 'access_key_id': 'k',
                                        'secret_access_key': 's', 'passphrase': PASS, 'keep': 2})
        for item in [patch.object(backup, 'BACKUPS', root / 'backups'), patch.object(offsite, 'RECORD', root / 'offsite.json'),
                     patch.object(offsite, 'Bucket', MemoryBucket)]:
            item.start()
            self.addCleanup(item.stop)

    def make(self, stamp, data=b'dump'):
        path = backup.private_dir(backup.BACKUPS / E / stamp)
        (path / 'database.dump').write_bytes(data)
        (path / 'objects.tar').write_bytes(b'tar' * 100)
        manifest = {'version': 1, 'runtime': E, 'created_at': stamp,
                    'database': {'file': 'database.dump', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
                    'objects': {'file': 'objects.tar', 'bytes': 300, 'sha256': hashlib.sha256(b'tar' * 100).hexdigest(), 'files': 1},
                    'counts': {'auth.users': 1}}
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path

    def test_new_backups_are_copied_once_the_newest_are_kept_and_a_copy_comes_back_intact(self):
        self.make('20260901T030000Z')
        self.make('20260902T030000Z', b'second dump')
        self.assertEqual(offsite.push([E], self.config), [f'{E}/20260901T030000Z', f'{E}/20260902T030000Z'])
        self.assertEqual(offsite.push([E], self.config), [])
        self.assertIn(f'sbarbase/{E}/20260902T030000Z/complete', MemoryBucket.objects)
        self.assertFalse(any(b'second dump' in value for value in MemoryBucket.objects.values()))
        self.make('20260903T030000Z')
        offsite.push([E], self.config)
        self.assertEqual(offsite.remote_backups(MemoryBucket(self.config), self.config, E), ['20260902T030000Z', '20260903T030000Z'])
        import shutil
        shutil.rmtree(backup.BACKUPS / E / '20260902T030000Z')
        manifest = offsite.fetch(E, '20260902T030000Z', self.config)
        self.assertEqual(manifest['database']['bytes'], len(b'second dump'))
        self.assertEqual((backup.BACKUPS / E / '20260902T030000Z' / 'database.dump').read_bytes(), b'second dump')
        with self.assertRaises(offsite.OffsiteError):
            offsite.fetch(E, '20260902T030000Z', self.config)
        with self.assertRaises(offsite.OffsiteError):
            offsite.fetch(E, '20260901T030000Z', self.config)

    def test_an_interrupted_upload_is_never_fetched(self):
        self.make('20260901T030000Z')
        offsite.push([E], self.config)
        del MemoryBucket.objects[f'sbarbase/{E}/20260901T030000Z/complete']
        self.assertEqual(offsite.remote_backups(MemoryBucket(self.config), self.config, E), [])

    def test_settings_are_checked(self):
        good = {'endpoint': 'https://x.r2.cloudflarestorage.com', 'bucket': 'backups', 'access_key_id': 'k', 'secret_access_key': 's', 'passphrase': PASS}
        self.assertEqual(offsite.validate(good)['keep'], offsite.DEFAULT_KEEP)
        for bad in ({**good, 'endpoint': 'http://storage.example.com'}, {**good, 'endpoint': 'https://x.com/path'}, {**good, 'bucket': 'A'},
                    {**good, 'passphrase': 'short'}, {**good, 'prefix': '../x'}, {**good, 'keep': 0}, {k: v for k, v in good.items() if k != 'bucket'}):
            with self.assertRaises(offsite.OffsiteError):
                offsite.validate(bad)
        self.assertEqual(offsite.validate({**good, 'endpoint': 'http://127.0.0.1:9100'})['endpoint'], 'http://127.0.0.1:9100')


if __name__ == '__main__':
    unittest.main()
