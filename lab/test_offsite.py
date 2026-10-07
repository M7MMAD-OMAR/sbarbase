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

    def test_actual_four_mib_chunk_boundaries(self):
        chunk = 4 * 1024 * 1024
        with patch.object(offsite, 'CHUNK', chunk):
            for size in (0, chunk - 1, chunk, chunk + 1, 2 * chunk + 17):
                data = b'a' * size
                source = self.root / 'actual-source'
                source.write_bytes(data)
                salt = os.urandom(16)
                with offsite.EncryptingReader(source, offsite.derive_key(PASS, salt), salt) as reader:
                    sealed = b''.join(iter(lambda: reader.read(chunk + offsite.TAG), b''))
                self.assertEqual(len(sealed), offsite.encrypted_size(size))
                target = self.root / 'actual-output'
                receipt = offsite.decrypt_stream(io.BytesIO(sealed), target, PASS,
                                                expected_bytes=size, max_bytes=size,
                                                expected_sha256=hashlib.sha256(data).hexdigest())
                self.assertEqual(receipt['bytes'], size)
                self.assertEqual(target.read_bytes(), data)
                self.assertFalse(list(self.root.glob('.*.decrypt-*')))

    def test_fragmented_transport_and_plaintext_receipt(self):
        data = b'fragmented transport' * 150
        sealed = self.seal(data)

        class Fragmented(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 7))

        target = self.root / 'fragmented'
        receipt = offsite.decrypt_stream(Fragmented(sealed), target, PASS,
                                        expected_bytes=len(data), expected_sha256=hashlib.sha256(data).hexdigest(),
                                        max_bytes=len(data))
        self.assertEqual(target.read_bytes(), data)
        self.assertEqual(receipt, {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob('.*.decrypt-*')))

    def test_late_authentication_failure_preserves_destination_and_hides_prefix(self):
        sealed = bytearray(self.seal(b'a' * 3000))
        sealed[-1] ^= 1
        for exists in (False, True):
            target = self.root / 'late'
            if exists:
                target.write_bytes(b'prior bytes')
            with self.assertRaises(offsite.OffsiteError):
                offsite.decrypt_stream(io.BytesIO(sealed), target, PASS)
            if exists:
                self.assertEqual(target.read_bytes(), b'prior bytes')
            else:
                self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob('.*.decrypt-*')))

    def test_plaintext_limits_and_digest_fail_before_publication(self):
        data = b'bounded' * 300
        sealed = self.seal(data)
        target = self.root / 'limited'
        target.write_bytes(b'previous')
        for options in ({'max_bytes': len(data) - 1}, {'expected_bytes': len(data) - 1},
                        {'expected_bytes': len(data) + 1}, {'expected_sha256': '0' * 64}):
            with self.assertRaises(offsite.OffsiteError):
                offsite.decrypt_stream(io.BytesIO(sealed), target, PASS, **options)
            self.assertEqual(target.read_bytes(), b'previous')
            self.assertFalse(list(self.root.glob('.*.decrypt-*')))

    def test_invalid_typed_admissions_fail_before_reading(self):
        class Unreadable:
            def read(self, size):
                raise AssertionError('Invalid admission must not read the source')

        for field in ('expected_bytes', 'max_bytes'):
            for value in (True, -1, 1.5, '12', offsite.CHUNK * 2**32 + 1):
                with self.assertRaises(offsite.OffsiteError):
                    offsite.decrypt_stream(Unreadable(), self.root / 'bad', PASS, **{field: value})
        for value in (b'0' * 64, 'X' * 64, '0' * 63, 42):
            with self.assertRaises(offsite.OffsiteError):
                offsite.decrypt_stream(Unreadable(), self.root / 'bad', PASS, expected_sha256=value)
        with self.assertRaises(offsite.OffsiteError):
            offsite.decrypt_stream(Unreadable(), self.root / 'bad', PASS, expected_bytes=2, max_bytes=1)
        for value in (True, -1, 1.5, '12', offsite.CHUNK * 2**32 + 1):
            with self.assertRaises(offsite.OffsiteError):
                offsite.encrypted_size(value)
        self.assertEqual(offsite.encrypted_size(offsite.CHUNK * 2**32),
                         28 + offsite.CHUNK * 2**32 + 16 * 2**32)

    def test_unsafe_destination_is_refused_without_following_it(self):
        sealed = self.seal(b'private')
        destination = self.root / 'unsafe'
        prior = self.root / 'prior'
        prior.write_bytes(b'keep')
        destination.symlink_to(prior)
        with self.assertRaises(offsite.OffsiteError):
            offsite.decrypt_stream(io.BytesIO(sealed), destination, PASS)
        self.assertEqual(prior.read_bytes(), b'keep')
        destination.unlink()
        destination.mkdir()
        with self.assertRaises(offsite.OffsiteError):
            offsite.decrypt_stream(io.BytesIO(sealed), destination, PASS)

    def test_reader_rejects_shrink_growth_and_same_length_mutation(self):
        source = self.root / 'mutable'
        for mutation in ('shrink', 'growth', 'rewrite'):
            source.write_bytes(b'a' * 3000)
            salt = os.urandom(16)
            with offsite.EncryptingReader(source, b'k' * 32, salt) as reader:
                self.assertEqual(reader.read(28)[:4], offsite.MAGIC)
                self.assertTrue(reader.read(1040))
                if mutation == 'shrink':
                    source.write_bytes(b'a')
                elif mutation == 'growth':
                    with source.open('ab') as handle:
                        handle.write(b'grown')
                else:
                    source.write_bytes(b'b' * 3000)
                with self.assertRaises(offsite.OffsiteError):
                    while reader.read(1040):
                        pass
                with self.assertRaises(offsite.OffsiteError):
                    reader.read(1040)

    def test_huge_readinto_keeps_only_one_chunk_and_zero_request_does_not_advance(self):
        source = self.root / 'large'
        source.write_bytes(b'z' * (offsite.CHUNK * 12))
        with offsite.EncryptingReader(source, b'k' * 32, b's' * 16) as reader:
            self.assertEqual(reader.readinto(bytearray()), 0)
            self.assertEqual(reader.handle.tell(), 0)
            buffer = bytearray(1024 * 1024)
            count = reader.readinto(buffer)
            self.assertEqual(count, 28)
            self.assertEqual(reader.handle.tell(), 0)
            ciphertext = bytes(buffer[:count])
            while count := reader.readinto(buffer):
                self.assertLessEqual(count, offsite.CHUNK + offsite.TAG)
                self.assertLessEqual(len(reader.pending), offsite.CHUNK + offsite.TAG)
                ciphertext += bytes(buffer[:count])
        self.assertEqual(len(ciphertext), offsite.encrypted_size(source.stat().st_size))

    def test_reader_admits_only_exact_key_and_salt_lengths_and_refuses_index_exhaustion(self):
        source = self.root / 'keys'
        source.write_bytes(b'plain')
        for key, salt in ((b'k' * 16, b's' * 16), (b'k' * 32, b's'), ('k' * 32, b's' * 16)):
            with self.assertRaises(offsite.OffsiteError):
                offsite.EncryptingReader(source, key, salt)
        for salt in (b's', 's' * 16):
            with self.assertRaises(offsite.OffsiteError):
                offsite.derive_key(PASS, salt)
        with offsite.EncryptingReader(source, b'k' * 32, b's' * 16) as reader:
            reader.read(28)
            reader.index = 2**32
            with self.assertRaises(offsite.OffsiteError):
                reader.read(1040)

    def test_failed_source_read_does_not_expose_authenticated_prefix(self):
        sealed = self.seal(b'a' * 3000)

        class FailedTransport(io.BytesIO):
            def read(self, size=-1):
                if self.tell() >= 28 + 2 * (offsite.CHUNK + offsite.TAG):
                    raise OSError('transport interrupted')
                return super().read(size)

        target = self.root / 'interrupted'
        target.write_bytes(b'previous')
        with self.assertRaisesRegex(OSError, 'transport interrupted'):
            offsite.decrypt_stream(FailedTransport(sealed), target, PASS)
        self.assertEqual(target.read_bytes(), b'previous')
        self.assertFalse(list(self.root.glob('.*.decrypt-*')))

    def test_decrypt_staging_is_private_before_atomic_replacement(self):
        sealed = self.seal(b'new plaintext')
        target = self.root / 'permissions'
        target.write_bytes(b'old plaintext')
        original = offsite.os.replace
        observed = []

        def replace(source, destination):
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(source.parent.parent, target.parent)
            self.assertEqual(target.read_bytes(), b'old plaintext')
            observed.append(source)
            return original(source, destination)

        with patch.object(offsite.os, 'replace', side_effect=replace):
            offsite.decrypt_stream(io.BytesIO(sealed), target, PASS)
        self.assertEqual(len(observed), 1)
        self.assertEqual(target.read_bytes(), b'new plaintext')

    def test_partial_header_tag_and_appended_bytes_never_publish(self):
        sealed = self.seal(b'a' * 2048)
        for malformed in (sealed[:27], sealed[:30], sealed[:-1], sealed + b'extra'):
            target = self.root / 'malformed'
            with self.assertRaises(offsite.OffsiteError):
                offsite.decrypt_stream(io.BytesIO(malformed), target, PASS)
            self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob('.*.decrypt-*')))


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

    def prepare_fetch(self):
        stamp = '20260901T030000Z'
        path = self.make(stamp)
        offsite.push([E], self.config)
        import shutil
        shutil.rmtree(path)
        return stamp, path

    def test_fetch_verifies_private_staging_before_publication(self):
        stamp, target = self.prepare_fetch()
        original = backup.verify
        observed = []

        def verify(e, path, *, staged_stamp=None):
            self.assertFalse(target.exists())
            self.assertEqual(staged_stamp, stamp)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertTrue(path.name.startswith(f'.{stamp}.fetching-'))
            observed.append(path)
            return original(e, path, staged_stamp=staged_stamp)

        with patch.object(backup, 'verify', side_effect=verify):
            offsite.fetch(E, stamp, self.config)
        self.assertEqual(len(observed), 1)
        self.assertTrue(target.exists())
        self.assertFalse(observed[0].exists())

    def test_fetch_bad_manifest_leaves_no_published_backup_or_staging(self):
        stamp, target = self.prepare_fetch()
        # A valid authenticated file paired with the wrong manifest must fail verification.
        wrong = target.parent / 'wrong'
        wrong.write_bytes(b'incorrect database')
        MemoryBucket(self.config).put_file(f'sbarbase/{E}/{stamp}/database.dump.sbb', wrong, PASS)
        wrong.unlink()
        with self.assertRaises(backup.BackupError):
            offsite.fetch(E, stamp, self.config)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob(f'.{stamp}.fetching-*')))

    def test_fetch_rejects_an_authenticated_copy_renamed_to_another_backup_time(self):
        original_stamp, original_target = self.prepare_fetch()
        requested_stamp = '20260902T030000Z'
        original_base = f'sbarbase/{E}/{original_stamp}/'
        requested_base = f'sbarbase/{E}/{requested_stamp}/'
        for key, ciphertext in list(MemoryBucket.objects.items()):
            if key.startswith(original_base):
                MemoryBucket.objects[requested_base + key[len(original_base):]] = ciphertext
        requested_target = original_target.with_name(requested_stamp)
        with self.assertRaisesRegex(offsite.OffsiteError, 'creation time'):
            offsite.fetch(E, requested_stamp, self.config)
        self.assertFalse(requested_target.exists())
        self.assertFalse(list(requested_target.parent.glob(f'.{requested_stamp}.fetching-*')))
        manifest = offsite.fetch(E, original_stamp, self.config)
        self.assertEqual(manifest['created_at'], original_stamp)
        self.assertEqual(backup.verify(E, original_target), manifest)
        self.assertEqual((original_target / 'database.dump').read_bytes(), b'dump')

    def test_fetch_rejects_missing_or_malformed_authenticated_creation_time(self):
        stamp, target = self.prepare_fetch()
        key = f'sbarbase/{E}/{stamp}/manifest.json.sbb'
        source = target.parent / 'manifest-source.json'
        offsite.decrypt_stream(io.BytesIO(MemoryBucket.objects[key]), source, PASS)
        manifest = json.loads(source.read_text())
        for created_at in (None, False, 20260901, '', '2026-09-01T03:00:00Z'):
            with self.subTest(created_at=created_at):
                malformed = dict(manifest)
                if created_at is None:
                    malformed.pop('created_at')
                else:
                    malformed['created_at'] = created_at
                source.write_text(json.dumps(malformed))
                MemoryBucket(self.config).put_file(key, source, PASS)
                with self.assertRaisesRegex(offsite.OffsiteError, 'creation time'):
                    offsite.fetch(E, stamp, self.config)
                self.assertFalse(target.exists())
                self.assertFalse(list(target.parent.glob(f'.{stamp}.fetching-*')))

    def test_fetch_concurrent_destination_is_not_replaced(self):
        stamp, target = self.prepare_fetch()
        original = offsite._promote_directory

        def collide(source, destination):
            target.mkdir()
            return original(source, destination)

        with patch.object(offsite, '_promote_directory', side_effect=collide):
            with self.assertRaises(offsite.OffsiteError):
                offsite.fetch(E, stamp, self.config)
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(list(target.parent.glob(f'.{stamp}.fetching-*')))

    def test_fetch_refuses_dangling_destination_symlink(self):
        stamp, target = self.prepare_fetch()
        target.symlink_to(target.parent / 'missing')
        with self.assertRaises(offsite.OffsiteError):
            offsite.fetch(E, stamp, self.config)
        self.assertTrue(target.is_symlink())

    def test_owned_staging_verifier_rejects_identity_and_path_mismatches(self):
        stamp = '20260901T030000Z'
        path = self.make(stamp)
        staged = path.with_name(f'.{stamp}.fetching-' + 'a' * 32)
        path.rename(staged)
        self.assertEqual(backup.verify(E, staged, staged_stamp=stamp)['runtime'], E)
        for supplied in (None, True, '20260902T030000Z', '../bad'):
            with self.assertRaises(backup.BackupError):
                backup.verify(E, staged, staged_stamp=supplied)
        link = staged.with_name(f'.{stamp}.fetching-' + 'b' * 32)
        link.symlink_to(staged, target_is_directory=True)
        with self.assertRaises(backup.BackupError):
            backup.verify(E, link, staged_stamp=stamp)
        other = backup.private_dir(staged.parent / 'other') / staged.name
        with self.assertRaises(backup.BackupError):
            backup.verify(E, other, staged_stamp=stamp)

    def test_owned_staging_name_collision_does_not_remove_another_directory(self):
        stamp, target = self.prepare_fetch()
        partial = target.with_name(f'.{stamp}.fetching-' + 'a' * 32)
        partial.mkdir(mode=0o700)
        marker = partial / 'another-owner'
        marker.write_bytes(b'preserve')
        with patch.object(offsite.secrets, 'token_hex', return_value='a' * 32):
            with self.assertRaises(FileExistsError):
                offsite.fetch(E, stamp, self.config)
        self.assertEqual(marker.read_bytes(), b'preserve')
        self.assertFalse(target.exists())

    def test_cleanup_failure_is_explicit_and_preserves_original_failure(self):
        stamp, target = self.prepare_fetch()
        original_cleanup = offsite.shutil.rmtree

        def deny_fetch_cleanup(path):
            if Path(path).name.startswith(f'.{stamp}.fetching-'):
                raise OSError('cleanup denied')
            return original_cleanup(path)

        with patch.object(backup, 'verify', side_effect=backup.BackupError('manifest refusal')) as verifier:
            with patch.object(offsite.shutil, 'rmtree', side_effect=deny_fetch_cleanup):
                with self.assertRaisesRegex(offsite.OffsiteError, 'cleanup failed') as caught:
                    offsite.fetch(E, stamp, self.config)
        verifier.assert_called_once()
        self.assertIsInstance(caught.exception.__context__, OSError)
        self.assertIsInstance(caught.exception.__context__.__context__, backup.BackupError)
        self.assertFalse(target.exists())

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
