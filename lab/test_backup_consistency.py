"""Actual file races at the dump/archive boundary must not publish a backup."""
import contextlib
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import backup

E = 'e_' + 'a' * 24
VERSION = '11111111-2222-3333-4444-555555555555'


class BackupFileRace(unittest.TestCase):
    def run_backup(self, mutation):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / 'files' / E
            obj = tree / 'bucket' / 'object.txt' / VERSION
            obj.parent.mkdir(parents=True)
            obj.write_bytes(b'original bytes')
            before = {'auth.users': 0, 'auth.identities': 0, 'storage.buckets': 1, 'storage.objects': 1}
            inventory = [{'bucket_id': 'bucket', 'name': 'object.txt', 'version': VERSION,
                          'metadata': {'size': 14, 'eTag': '"' + hashlib.md5(b'original bytes').hexdigest() + '"'}}]
            @contextlib.contextmanager
            def held(*args):
                yield '00000003-0000002A-1', before
            def run(argv, **kwargs):
                kwargs['stdout'].write(b'actual archive fixture stand-in')
                mutation(obj)
            def helper(script, *args, **kwargs):
                with tarfile.open(fileobj=kwargs['stdout'], mode='w') as archive:
                    archive.add(tree, arcname=E)
            with contextlib.ExitStack() as stack:
                for name, value in [('BACKUPS', root / 'backups'), ('published', lambda: {E: {}}),
                                    ('no_pending_completion', lambda: None), ('preflight', lambda: {'db':'fixture'}),
                                    ('snapshot', held), ('run', run), ('helper', helper),
                                    ('ownership', lambda e: None), ('storage_image', lambda: 'fixture'),
                                    ('sql', lambda *args: json.dumps(inventory))]:
                    stack.enter_context(patch.object(backup, name, value))
                try:
                    result = backup.create(E)
                except backup.BackupError:
                    self.assertEqual(list((root / 'backups').rglob('manifest.json')), [])
                    return False
                self.assertTrue((result[0] / 'manifest.json').exists())
                return True

    def test_delete_after_dump_is_refused_before_manifest(self):
        self.assertFalse(self.run_backup(lambda obj: obj.unlink()))

    def test_same_length_overwrite_after_dump_is_refused_before_manifest(self):
        self.assertFalse(self.run_backup(lambda obj: obj.write_bytes(b'replaced bytes')))

    def test_unchanged_object_is_accepted(self):
        self.assertTrue(self.run_backup(lambda obj: None))

class ArchiveContract(unittest.TestCase):
    def test_real_archive_preserves_native_attributes_and_restores_them(self):
        import os
        import backup_consistency as consistency
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source' / E / 'bucket' / 'object.txt' / VERSION
            source.parent.mkdir(parents=True)
            source.write_bytes(b'original bytes')
            os.utime(source, ns=(1600000000123456789,1600000000123456789))
            os.chmod(source,0o640)
            attributes = {'user.supabase.content-type': b'text/plain',
                          'user.supabase.cache-control': b'max-age=60',
                          'user.supabase.etag': b'original'}
            for key, value in attributes.items():
                os.setxattr(source, key, value)
            archive = root / 'objects.tar'
            with archive.open('wb') as handle:
                subprocess.run(['python3', '-c', consistency.ARCHIVE_SCRIPT, E, str(root / 'source')],
                               stdout=handle, check=True, timeout=10)
            rows = [{'bucket_id': 'bucket', 'name': 'object.txt', 'version': VERSION,
                     'metadata': {'size': 14, 'eTag': '"' + hashlib.md5(b'original bytes').hexdigest() + '"',
                                  'mimetype': 'text/plain', 'cacheControl': 'max-age=60'}}]
            self.assertEqual(consistency.validate(archive, E, rows, 1)['referenced_files'], 1)
            target = root / 'target'
            target.mkdir()
            with archive.open('rb') as handle:
                subprocess.run(['python3', '-c', consistency.EXTRACT_SCRIPT, str(target)],
                               stdin=handle, check=True, timeout=10)
            restored = target / E / 'bucket' / 'object.txt' / VERSION
            self.assertEqual(restored.read_bytes(), source.read_bytes())
            self.assertEqual(restored.stat().st_mtime_ns,source.stat().st_mtime_ns)
            self.assertEqual(restored.stat().st_mode,source.stat().st_mode)
            self.assertEqual((restored.stat().st_uid,restored.stat().st_gid),(source.stat().st_uid,source.stat().st_gid))
            for key, value in attributes.items():
                self.assertEqual(os.getxattr(restored, key), value)
            for etag in ['"199abc-14"', None, 'unquoted', '"' + '0' * 32 + '"']:
                rows[0]['metadata']['eTag'] = etag
                with self.subTest(etag=etag), self.assertRaises(consistency.ConsistencyError):
                    consistency.validate(archive, E, rows, 1)
            rows[0]['metadata']['eTag'] = '"' + hashlib.md5(b'original bytes').hexdigest() + '"'
            rows[0]['version'] = None
            with self.assertRaises(consistency.ConsistencyError):
                consistency.validate(archive,E,rows,1)
            rows[0]['version'] = VERSION
            rows[0]['metadata']['mimetype'] = 'application/json'
            with self.assertRaises(consistency.ConsistencyError):
                consistency.validate(archive, E, rows, 1)

    def test_inventory_imports_exact_exported_snapshot(self):
        import backup_consistency as consistency
        from types import SimpleNamespace
        recorded = []
        api = SimpleNamespace(sql=lambda query, database: recorded.append((query, database)) or '[]')
        self.assertEqual(consistency.inventory(api, E, '00000003-0000002A-1'), [])
        self.assertIn("SET TRANSACTION SNAPSHOT '00000003-0000002A-1'", recorded[0][0])
        self.assertEqual(recorded[0][1], E)
        with self.assertRaises(consistency.ConsistencyError):
            consistency.inventory(api, E, "'; SELECT private_data; --")


if __name__ == '__main__':
    unittest.main()
