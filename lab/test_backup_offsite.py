"""Installation manifest and encrypted off-host copies, against a loopback S3 fake. No containers."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

import backup
import backup_offsite as offsite
import notification_producers

ROOT = Path(__file__).resolve().parents[1]
E1 = 'e_' + 'a' * 24
E2 = 'e_' + 'b' * 24
SENTINEL = 'SENTINEL' + 'x' * 24
ACCESS, SECRET = 'AKIDTESTONLY', 'secret-for-the-loopback-fake-only'


class FakeS3(ThreadingHTTPServer):
    """A bucket in memory that checks each request's Signature Version 4 and payload digest."""

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.objects = {}
        self.fail = set()
        self.requests = []
        self.bad_signatures = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def check(self, body):
        parts = urlsplit(self.path)
        headers = {'host': self.headers['host']}
        if self.command == 'PUT':
            headers['content-length'] = self.headers['content-length']
        payload = self.headers['x-amz-content-sha256']
        expected = offsite.sign(self.command, parts.path, parse_qsl(parts.query, keep_blank_values=True), headers,
                                payload, ACCESS, SECRET, 'test-1', self.headers['x-amz-date'])
        good = expected['authorization'] == self.headers['authorization'] and \
            payload == hashlib.sha256(body).hexdigest()
        if not good:
            self.server.bad_signatures += 1
        return good, parts

    def reply(self, status, body=b''):
        self.send_response(status)
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_one(self):
        length = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(length) if length else b''
        good, parts = self.check(body)
        self.server.requests.append((self.command, parts.path))
        if not good:
            return self.reply(403)
        if self.command in self.server.fail:
            return self.reply(500)
        bucket, _, key = parts.path.lstrip('/').partition('/')
        if bucket != 'bucket':
            return self.reply(404)
        if self.command == 'PUT':
            self.server.objects[key] = body
            return self.reply(200)
        if self.command == 'DELETE':
            self.server.objects.pop(key, None)
            return self.reply(204)
        if key:
            return self.reply(200, self.server.objects[key]) if key in self.server.objects else self.reply(404)
        prefix = dict(parse_qsl(parts.query)).get('prefix', '')
        keys = ''.join(f'<Contents><Key>{name}</Key></Contents>' for name in sorted(self.server.objects)
                       if name.startswith(prefix))
        xml = ('<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
               f'{keys}<IsTruncated>false</IsTruncated></ListBucketResult>')
        return self.reply(200, xml.encode())

    do_GET = do_PUT = do_DELETE = handle_one


def private(path, value):
    path.write_text(json.dumps(value))
    os.chmod(path, 0o600)
    return path


def tree(path):
    return {str(item.relative_to(path)): item.read_bytes() for item in sorted(path.rglob('*')) if item.is_file()}


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.backups, self.state, self.secrets = (self.root / name for name in ('backups', 'state', 'secrets'))
        self.state.mkdir()
        (self.secrets / 'upstream').mkdir(parents=True)
        (self.state / 'endpoints.json').write_text(json.dumps({
            E1: {'auth': 'http://10.0.0.2:9999', 'rest': 'http://10.0.0.3:3000',
                 'storage': {'url': 'http://10.0.0.4:5000', 'tenantHost': E1}, 'serviceConcurrency': {'rest': 4}},
            E2: {'auth': 'http://10.0.0.5:9999', 'rest': 'http://10.0.0.6:3000'}}))
        for module, name, value in ((backup, 'BACKUPS', self.backups), (backup, 'STATE', self.state),
                                    (offsite, 'SECRETS', self.secrets)):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.key_file = private(self.secrets / 'upstream' / 'offsite-key.json', {'schema': 1, 'key': '11' * 32})
        self.key = bytes.fromhex('11' * 32)

    def complete(self, e, stamp):
        path = backup.private_dir(self.backups / e / stamp)
        (path / 'database.dump').write_bytes(b'dump ' + e.encode() + stamp.encode() * 1000)
        (path / 'objects.tar').write_bytes(b'tar ' + e.encode())
        manifest = {'version': 1, 'runtime': e, 'created_at': stamp,
                    'database': {'file': 'database.dump', 'bytes': (path / 'database.dump').stat().st_size,
                                 'sha256': backup.digest(path / 'database.dump')},
                    'objects': {'file': 'objects.tar', 'bytes': (path / 'objects.tar').stat().st_size,
                                'sha256': backup.digest(path / 'objects.tar'), 'files': 0},
                    'counts': {'auth.users': 1}}
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path

    def storage(self, stamp):
        path = backup.private_dir(self.backups / 'storage' / stamp)
        (path / 'database.dump').write_bytes(b'storage dump ' + stamp.encode())
        manifest = {'version': 1, 'kind': 'storage_metadata', 'created_at': stamp,
                    'database': {'file': 'database.dump', 'name': 'storage_metadata',
                                 'bytes': (path / 'database.dump').stat().st_size,
                                 'sha256': backup.digest(path / 'database.dump')},
                    'counts': {'tenants': 2}, 'tenants': [E1, E2]}
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path

    def run_set(self, stamp):
        for e in (E1, E2):
            self.complete(e, stamp)
        self.storage(stamp)
        offsite.write_installation(stamp, [E1, E2], environ={})

    def encrypted(self, stamp, name=None):
        path = self.root / f'{stamp}.sbb'
        with path.open('wb') as handle:
            offsite.pack(stamp, [E1, E2], self.key, handle)
        return path

    def serve(self):
        server = FakeS3()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        credentials = private(self.secrets / 'upstream' / 'offsite-s3.json',
                              {'schema': 1, 'accessKeyId': ACCESS, 'secretAccessKey': SECRET})
        private(self.state / 'backup-offsite.json', {
            'schema': 1, 'keyFile': str(self.key_file),
            's3': {'endpoint': f'http://127.0.0.1:{server.server_address[1]}', 'region': 'test-1',
                   'bucket': 'bucket', 'prefix': 'sbarbase/', 'credentialsFile': str(credentials)}})
        return server


class SignatureTests(unittest.TestCase):
    def test_the_published_s3_get_object_example_signs_to_its_documented_signature(self):
        # Example "GET Object" of the Amazon S3 Signature Version 4 documentation.
        headers = offsite.sign('GET', '/test.txt', [], {'Host': 'examplebucket.s3.amazonaws.com', 'Range': 'bytes=0-9'},
                               offsite.EMPTY_SHA256, 'AKIAIOSFODNN7EXAMPLE',
                               'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'us-east-1', '20130524T000000Z')
        self.assertIn('SignedHeaders=host;range;x-amz-content-sha256;x-amz-date', headers['authorization'])
        self.assertTrue(headers['authorization'].endswith(
            'Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'))


class ManifestTests(Fixture):
    def test_the_manifest_holds_pins_routing_catalog_and_secret_names_but_no_secret_value(self):
        (self.secrets / 'upstream' / 'runtime.json').write_text(json.dumps({'jwt': SENTINEL}))
        with contextlib.closing(sqlite3.connect(self.state / 'control.sqlite')) as database, database:
            database.execute('CREATE TABLE organizations(id TEXT, name TEXT)')
            database.execute("INSERT INTO organizations VALUES ('o1', 'Client')")
            database.execute('CREATE TABLE provision_jobs(environment TEXT, runtime TEXT, actor TEXT, organization TEXT,'
                             ' state TEXT, attempt INTEGER, claim TEXT)')
            database.execute("INSERT INTO provision_jobs VALUES ('env1', ?, 'actor', 'o1', 'succeeded', 1, ?)",
                             (E1, SENTINEL))
            database.execute('CREATE TABLE provision_recovery_decisions(receipt_token TEXT)')
            database.execute('INSERT INTO provision_recovery_decisions VALUES (?)', (SENTINEL,))
        (self.state / 'notifications.json').write_text(json.dumps({
            'schema': 1, 'webhook': {'enabled': True, 'url': f'https://hooks.example.invalid/{SENTINEL}',
                                     'secretFile': '.secrets/upstream/notifier.json', 'extra': SENTINEL},
            'email': {'enabled': True, 'host': 'smtp.example.invalid', 'password': SENTINEL}}))
        environ = {'SBARBASE_BACKUP_KEEP': '7', 'SBARBASE_EFFECT_TOKEN': SENTINEL}
        path = offsite.write_installation('20260924T030000Z', [E1], environ=environ)
        text = (path / 'installation.json').read_text()
        self.assertNotIn(SENTINEL, text)
        manifest = json.loads(text)
        self.assertEqual(manifest['secrets']['files'], ['upstream/offsite-key.json', 'upstream/runtime.json'])
        self.assertEqual(set(manifest['pins']), {'distro-image.lock.json', 'images.lock.json',
                                                 'storage-image.lock.json', 'studio-image.lock.json'})
        self.assertEqual(manifest['catalog']['provision_jobs'][0]['runtime'], E1)
        self.assertNotIn('claim', manifest['catalog']['provision_jobs'][0])
        self.assertEqual(manifest['routing'][E1]['storage'], {'url': 'http://10.0.0.4:5000', 'tenantHost': E1})
        self.assertEqual(manifest['settings'], {'SBARBASE_BACKUP_KEEP': '7'})
        self.assertEqual(manifest['notifications']['webhook']['origin'], 'https://hooks.example.invalid')
        self.assertEqual(oct((path / 'installation.json').stat().st_mode & 0o777), '0o600')
        self.assertEqual(offsite.verify_installation(path)['kind'], 'installation')
        (path / 'installation.json').write_text(text.replace('"version": 1', '"version": 2'))
        with self.assertRaisesRegex(backup.BackupError, 'digest'):
            offsite.verify_installation(path)


class EncryptionTests(Fixture):
    STAMP = '20260924T030000Z'

    def test_a_set_round_trips_and_no_plaintext_reaches_the_ciphertext(self):
        self.run_set(self.STAMP)
        before = tree(self.backups)
        path = self.encrypted(self.STAMP)
        self.assertNotIn(b'dump ' + E1.encode(), path.read_bytes())
        staging = self.root / 'staging'
        staging.mkdir()
        offsite.unpack(path, self.key, self.STAMP, staging)
        self.assertEqual(tree(staging), before)

    def test_a_changed_byte_a_renamed_set_and_a_wrong_key_are_refused_before_anything_is_extracted(self):
        self.run_set(self.STAMP)
        path = self.encrypted(self.STAMP)
        staging = self.root / 'staging'
        staging.mkdir()
        with self.assertRaisesRegex(backup.BackupError, 'authentication'):
            offsite.unpack(path, self.key, '20260925T030000Z', staging)
        with self.assertRaisesRegex(backup.BackupError, 'authentication'):
            offsite.unpack(path, bytes(32), self.STAMP, staging)
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(bytes(data))
        with self.assertRaisesRegex(backup.BackupError, 'authentication'):
            offsite.unpack(path, self.key, self.STAMP, staging)
        self.assertEqual(list(staging.iterdir()), [])

    def test_an_entry_outside_the_allow_list_is_refused(self):
        path = self.root / 'evil.sbb'
        with path.open('wb') as handle:
            writer = offsite.Encrypting(handle, self.key, self.STAMP)
            with tarfile.open(fileobj=writer, mode='w|') as archive:
                info = tarfile.TarInfo('../escaped')
                info.size = 3
                archive.addfile(info, io.BytesIO(b'bad'))
            writer.finish()
        staging = self.root / 'staging'
        staging.mkdir()
        with self.assertRaisesRegex(backup.BackupError, 'unexpected entry'):
            offsite.unpack(path, self.key, self.STAMP, staging)
        self.assertFalse((self.root / 'escaped').exists())

    def test_the_key_file_must_be_private_valid_and_is_never_overwritten(self):
        os.chmod(self.key_file, 0o644)
        with self.assertRaisesRegex(backup.BackupError, 'not private'):
            offsite.load_key(self.key_file)
        with self.assertRaisesRegex(backup.BackupError, 'never overwritten'):
            offsite.new_key(self.key_file)
        created = offsite.new_key(self.root / 'new-key.json')
        self.assertEqual(oct(created.stat().st_mode & 0o777), '0o600')
        self.assertEqual(len(offsite.load_key(created)), 32)

    def test_a_plain_http_endpoint_is_refused_unless_it_is_loopback(self):
        config = self.root / 'offsite.json'
        base = {'schema': 1, 'keyFile': 'k', 's3': {'region': 'r', 'bucket': 'bucket', 'credentialsFile': 'c'}}
        config.write_text(json.dumps({**base, 's3': {**base['s3'], 'endpoint': 'http://s3.example.invalid'}}))
        with self.assertRaisesRegex(backup.BackupError, 'https'):
            offsite.load_config(config)
        config.write_text(json.dumps({**base, 's3': {**base['s3'], 'endpoint': 'http://127.0.0.1:9000'}}))
        self.assertEqual(offsite.load_config(config)['host'], '127.0.0.1')
        self.assertIsNone(offsite.load_config(self.root / 'absent.json'))


class TargetTests(Fixture):
    def test_uploads_keep_the_newest_sets_and_leave_other_objects_alone(self):
        server = self.serve()
        server.objects['sbarbase/notes.txt'] = b'operator note'
        server.objects['other/20200101T000000Z.sbb'] = b'another installation'
        for stamp in ('20260922T030000Z', '20260923T030000Z', '20260924T030000Z'):
            self.run_set(stamp)
            self.assertTrue(offsite.after_run(stamp, [E1, E2], 2))
        self.assertEqual(sorted(server.objects), ['other/20200101T000000Z.sbb', 'sbarbase/20260923T030000Z.sbb',
                                                  'sbarbase/20260924T030000Z.sbb', 'sbarbase/notes.txt'])
        self.assertEqual(offsite.list_remote(), ['20260923T030000Z', '20260924T030000Z'])
        self.assertEqual(server.bad_signatures, 0)
        self.assertEqual([path.name for path in self.backups.iterdir() if path.name.startswith('.')], [])

    def test_a_set_is_fetched_back_verified_and_an_existing_local_backup_is_left_alone(self):
        self.serve()
        stamp = '20260924T030000Z'
        self.run_set(stamp)
        self.assertTrue(offsite.after_run(stamp, [E1, E2], 7))
        expected = tree(self.backups)
        shutil.rmtree(self.backups / E1 / stamp)
        shutil.rmtree(self.backups / 'installation' / stamp)
        shutil.rmtree(self.backups / 'storage' / stamp)
        (self.backups / E2 / stamp / 'marker').write_text('local')
        # Storage's shared metadata travels in the run's set and comes back verified.
        self.assertEqual(offsite.fetch(stamp), [E1, 'installation', 'storage'])
        self.assertEqual(backup.verify_storage(self.backups / 'storage' / stamp)['tenants'], [E1, E2])
        self.assertEqual(backup.verify(E1, self.backups / E1 / stamp)['runtime'], E1)
        restored = tree(self.backups)
        self.assertEqual(restored.pop(f'{E2}/{stamp}/marker'), b'local')
        self.assertEqual(restored, expected)
        self.assertEqual(offsite.fetch(stamp), [])

    def test_a_failed_upload_leaves_local_backups_untouched_prunes_nothing_and_notifies(self):
        catalog = self.root / 'control.sqlite'
        result = subprocess.run(['bun', '-e', "import {Catalog} from '" + str(ROOT / 'src/control/catalog') +
                                 "'; new Catalog(" + json.dumps(str(catalog)) + ').close();'],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        server = self.serve()
        server.objects['sbarbase/20260101T000000Z.sbb'] = b'old set'
        server.fail.add('PUT')
        stamp = '20260924T030000Z'
        self.run_set(stamp)
        before = tree(self.backups)
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertFalse(offsite.after_run(stamp, [E1, E2], 1, catalog=catalog))
        self.assertIn('HTTP 500', error.getvalue())
        self.assertEqual(tree(self.backups), before)
        self.assertNotIn('DELETE', [method for method, _ in server.requests])
        self.assertIn('sbarbase/20260101T000000Z.sbb', server.objects)
        with contextlib.closing(sqlite3.connect(catalog)) as database:
            rows = database.execute('SELECT kind, severity, dedupe_key FROM notification_outbox').fetchall()
        self.assertEqual(rows, [('backup.failed', 'critical', 'backup.offsite_failed|installation')])

    def test_the_daily_run_shares_one_time_writes_the_manifest_and_exits_3_when_only_the_copy_failed(self):
        server = self.serve()
        server.fail.add('PUT')

        def create(e, keep, now=None, reason=None, protected=None):
            path = self.complete(e, now.strftime('%Y%m%dT%H%M%SZ'))
            return path, json.loads((path / 'manifest.json').read_text())

        def create_storage(keep, now=None, reason=None, protected=None):
            path = self.storage(now.strftime('%Y%m%dT%H%M%SZ'))
            return path, json.loads((path / 'manifest.json').read_text())

        with patch.object(backup, 'create', create), patch.object(backup, 'create_storage', create_storage), \
                patch.object(notification_producers, 'emit') as emit, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            # Local backups are fine; 3 tells the supervisor the failed copy was already reported.
            self.assertEqual(backup.main(['create', 'all']), backup.OFFSITE_FAILED)
        self.assertIn('off-host copy', error.getvalue())
        self.assertEqual(emit.call_args.args[0], 'backup.failed')
        stamps = {path.name for e in (E1, E2, 'storage', 'installation') for path in (self.backups / e).iterdir()}
        self.assertEqual(len(stamps), 1)
        self.assertTrue((self.backups / 'installation' / stamps.pop() / 'manifest.json').is_file())

    def test_an_installation_manifest_taken_for_an_upgrade_is_marked_and_kept(self):
        marked = offsite.write_installation('20260901T030000Z', [E1], environ={}, reason='upgrade')
        self.assertEqual(json.loads((marked / 'manifest.json').read_text())['reason'], 'upgrade')
        for day in range(2, 6):
            offsite.write_installation(f'202609{day:02d}T030000Z', [E1], keep=2, environ={})
        kept = sorted(path.name for path in (self.backups / 'installation').iterdir())
        self.assertEqual(kept, ['20260901T030000Z', '20260904T030000Z', '20260905T030000Z'])
        self.assertNotIn('reason', json.loads((self.backups / 'installation' / '20260905T030000Z' / 'manifest.json').read_text()))
        with self.assertRaises(backup.BackupError):
            offsite.write_installation('20260906T030000Z', [E1], environ={}, reason='whim')

    def test_a_broken_settings_file_is_recorded_as_invalid_and_does_not_fail_the_manifest(self):
        (self.state / 'notifications.json').write_text('{broken')
        (self.state / 'backup-offsite.json').write_text(json.dumps(
            {'schema': 1, 's3': {'endpoint': f'https://user:{SENTINEL}@s3.example.invalid/x'}}))
        path = offsite.write_installation('20260924T030000Z', [E1], environ={})
        text = (path / 'installation.json').read_text()
        self.assertNotIn(SENTINEL, text)
        manifest = json.loads(text)
        self.assertEqual(manifest['notifications'], 'invalid')
        self.assertEqual(manifest['offsite']['s3']['endpoint'], 'https://s3.example.invalid')

    def test_the_command_line_refuses_without_a_traceback(self):
        existing = self.root / 'existing.json'
        existing.write_text('{}')
        result = subprocess.run(['/usr/bin/python3', str(ROOT / 'lab' / 'backup.py'), 'offsite-key', str(existing)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith('refused:'), result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(existing.read_text(), '{}')

    def test_without_configuration_nothing_is_attempted(self):
        self.run_set('20260924T030000Z')
        self.assertIsNone(offsite.after_run('20260924T030000Z', [E1, E2], 7))


if __name__ == '__main__':
    unittest.main()
