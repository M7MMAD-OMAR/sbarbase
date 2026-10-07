#!/usr/bin/env python3
"""Original Storage HTTP upload, snapshot races and shipped backup restore.

The separate DB/file drill proves fresh-cluster fixture recovery. This drill
observes ordinary native upload metadata and verifies usable Storage downloads.
It requires a local Linux Docker daemon and never reads retained installations.
"""
import argparse
import contextlib
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import backup
import backup_consistency
import durable_runtime as runtime
import image_identity
import run as lab

SPEC = importlib.util.spec_from_file_location('native_storage_disposable_base', Path(__file__).with_name('disposable-recovery-drill.py'))
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
ENVIRONMENT = base.ENVIRONMENT
BUCKET = 'fixture'
OBJECT = 'object.txt'
ORIGINAL = b'ordinary native storage object\n'
CHANGED = b'x' * len(ORIGINAL)
UNPROVEN = ('enduser_auth', 'cross_tenant_isolation', 'signed_urls', 'fresh_host_full_application',
            'platform_secret_rotation', 'external_object_store', 'native_feature_recovery')


def plan():
    return {'schema': 1, 'status': 'planned-runtime-unrun', 'reference': base.pinned_identity(),
            'scope': 'Original pinned Storage service-role HTTP behavior and shipped environment backup restore',
            'resources': {'aggregate_running_memory_mib': 512, 'aggregate_running_cpus': .5,
                          'database_memory_mib': 256, 'database_cpus': .25,
                          'auth_bootstrap_memory_mib': 256, 'auth_bootstrap_cpus': .25,
                          'storage_memory_mib': 256, 'storage_cpus': .25,
                          'helper_memory_mib': 256, 'helper_cpus': .5,
                          'max_running_containers': 2, 'network': 'owned internal bridge', 'published_ports': []},
            'steps': ['Prove immutable native images and Storage helper tools before data allocation',
                      'Create private owned network and volumes; start bounded original PostgreSQL',
                      'Use existing SQL provisioner and original GoTrue once to bootstrap canonical Auth migrations',
                      'Remove GoTrue before starting original Storage; register one native tenant',
                      'Upload through service-role HTTP; read actual native metadata, version paths and xattrs',
                      'Create an ordinary usable backup without editing native metadata or forcing ETag configuration',
                      'Overwrite and delete through native HTTP and verify their effects',
                      'Inject HTTP overwrite and delete after pg_dump while the exported snapshot remains held',
                      'Require either admitted coherent checkpoint bytes or explicit refusal before manifest',
                      'Restore the ordinary backup through shipped fencing and verify original native HTTP download',
                      'Stop both services before each original isolated helper; restart only previous running services',
                      'Remove every exact owned disposable resource and emit sanitized evidence'],
            'required_unproven': list(UNPROVEN)}


class NativeStorage(base.Disposable):
    def __init__(self, directory):
        super().__init__(directory)
        self.network = self.prefix + '-net'
        self.credentials = {kind: secrets.token_hex(32) for kind in ('auth', 'rest', 'storage', 'jwt')}
        self.control = secrets.token_hex(32)
        self.admin_key = secrets.token_hex(32)
        self.encryption = secrets.token_hex(32)
        self.observations = []
        self.expected_content = ORIGINAL
        self.auth_pin = json.loads((base.SOURCE / 'lab/images.lock.json').read_text())['auth']

    def absent(self, kind, name):
        if kind != 'network':
            return super().absent(kind, name)
        result = self.docker('network', 'inspect', name, check=False)
        expected = {'Error response from daemon: network ' + name + ' not found', 'Error: No such network: ' + name}
        if result.returncode == 0 or result.stdout.strip() != '[]' or result.stderr.strip() not in expected:
            raise base.Refusal('Disposable network absence is not proved')

    def running(self, name):
        item = image_identity.record(self.docker('container', 'inspect', name).stdout)
        if item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != self.owner:
            raise base.Refusal('Disposable service owner differs')
        value = item.get('State', {}).get('Running')
        if type(value) is not bool:
            raise base.Refusal('Disposable service running state is unproved')
        return value

    def endpoint(self, name, port):
        item = image_identity.record(self.docker('container', 'inspect', name).stdout)
        if item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != self.owner:
            raise base.Refusal('HTTP endpoint owner differs')
        address = item.get('NetworkSettings', {}).get('Networks', {}).get(self.network, {}).get('IPAddress')
        if not address:
            raise base.Refusal('Disposable internal HTTP address unavailable')
        return 'http://' + address + ':' + str(port)

    def launch(self, name, reference, configuration, mounts=(), command=()):
        self.absent('container', name)
        filename = self.root / (name + '.env')
        backup.write_private(filename, ''.join(key + '=' + str(value) + '\n' for key, value in configuration.items()))
        argv = ['run', '-d', '--name', name, '--pull=never', '--network', self.network,
                '--memory', '256m', '--memory-swap', '256m', '--cpus', '.25', '--pids-limit', '128',
                '--label', 'io.sbarbase.owner=' + self.owner, '--env-file', str(filename),
                '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2']
        for volume, target in mounts:
            argv.extend(['--mount', 'type=volume,source=' + volume + ',target=' + target])
        self.docker(*argv, reference, *command)
        self.resources.append(('container', name))

    def start_database(self):
        self.launch(self.db, self.db_reference,
                    {'POSTGRES_PASSWORD': secrets.token_hex(32), 'POSTGRES_HOST': '/var/run/postgresql', 'POSTGRES_DB': 'postgres'},
                    ((self.pgdata, '/var/lib/postgresql/data'), (self.objects, '/data')),
                    ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none'))
        self.ready()

    def provision(self):
        def execute(query, database='postgres'):
            return SimpleNamespace(stdout=backup.sql(query, database))
        lab.provision_environment(ENVIRONMENT, self.credentials, executor=execute)
        backup.sql('CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions; CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions; GRANT USAGE ON SCHEMA extensions TO anon,authenticated,service_role;', ENVIRONMENT)
        backup.sql('CREATE ROLE ' + ENVIRONMENT + "_storage LOGIN NOINHERIT PASSWORD '" + self.credentials['storage'] + "';")
        backup.sql('GRANT anon,authenticated,service_role TO ' + ENVIRONMENT + '_storage; GRANT CONNECT ON DATABASE ' + ENVIRONMENT + ' TO ' + ENVIRONMENT + '_storage;')
        backup.sql('CREATE SCHEMA storage AUTHORIZATION ' + ENVIRONMENT + '_storage; GRANT USAGE ON SCHEMA storage TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES FOR ROLE ' + ENVIRONMENT + '_storage IN SCHEMA storage GRANT ALL ON TABLES TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES FOR ROLE ' + ENVIRONMENT + '_storage IN SCHEMA storage GRANT ALL ON SEQUENCES TO anon,authenticated,service_role;', ENVIRONMENT)
        backup.sql("CREATE ROLE storage_control LOGIN NOINHERIT PASSWORD '" + self.control + "'; CREATE DATABASE storage_metadata OWNER storage_control;")
        hba = ['local all supabase_admin trust', 'host storage_metadata storage_control 0.0.0.0/0 scram-sha-256']
        hba.extend('host ' + ENVIRONMENT + ' ' + ENVIRONMENT + '_' + kind + ' 0.0.0.0/0 scram-sha-256' for kind in ('auth', 'rest', 'storage'))
        hba.extend(('host all all 0.0.0.0/0 reject', 'host all all ::/0 reject'))
        location = backup.sql('SHOW hba_file;')
        with tempfile.TemporaryFile() as source:
            source.write(('\n'.join(hba) + '\n').encode())
            source.seek(0)
            self.docker('exec', '-i', self.db, 'sh', '-c', 'cat > "$1"', 'fixture', location, stdin=source, text=False)
        backup.sql('SELECT pg_reload_conf();')

    def bootstrap_auth(self):
        name = self.prefix + '-auth-bootstrap'
        self.launch(name, image_identity.reference(self.auth_pin), lab.auth_configuration(ENVIRONMENT, self.credentials, self.db))
        try:
            self.wait_http(name, 9999, '/health')
            self.check('original GoTrue bootstrap creates native Auth tables and uid function',
                       backup.sql("SELECT to_regclass('auth.users') IS NOT NULL AND to_regclass('auth.identities') IS NOT NULL AND to_regprocedure('auth.uid()') IS NOT NULL;", ENVIRONMENT) == 't')
        finally:
            self.remove('container', name)

    def storage_configuration(self):
        return {'MULTI_TENANT': 'true', 'MULTITENANT_DATABASE_URL': 'postgres://storage_control:' + self.control + '@' + self.db + ':5432/storage_metadata',
                'ENCRYPTION_KEY': self.encryption, 'ADMIN_API_KEYS': self.admin_key, 'DB_INSTALL_ROLES': 'false',
                'STORAGE_BACKEND': 'file', 'GLOBAL_S3_BUCKET': backup.TENANT_PARENT, 'FILE_STORAGE_BACKEND_PATH': '/tmp/storage-data',
                'REGION': 'local', 'FILE_SIZE_LIMIT': '1048576', 'DATABASE_MAX_CONNECTIONS': '3',
                'MULTITENANT_DATABASE_MAX_CONNECTIONS': '3', 'PG_QUEUE_ENABLE': 'false', 'ENABLE_IMAGE_TRANSFORMATION': 'false',
                'S3_PROTOCOL_ENABLED': 'false', 'X_FORWARDED_HOST_REGEXP': r'^(e_[a-f0-9]{24})\.storage\.internal$', 'LOG_LEVEL': 'error'}

    def wait_http(self, name, port, suffix, headers=None):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if not self.running(name):
                raise base.Refusal('Original native service exited during readiness')
            try:
                if runtime.http(self.endpoint(name, port) + suffix, headers=headers)[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(.5)
        raise base.Refusal('Original native HTTP readiness timed out')

    def storage_ready(self):
        self.wait_http(self.storage, 5001, '/tenants', {'apikey': self.admin_key})

    def start_storage(self):
        self.docker('start', self.storage)
        return self.endpoint(self.storage, 5000)

    def register_tenant(self):
        payload = {'anonKey': runtime.token(self.credentials['jwt'], 'anon'),
                   'serviceKey': runtime.token(self.credentials['jwt'], 'service_role'), 'jwtSecret': self.credentials['jwt'],
                   'databaseUrl': 'postgres://' + ENVIRONMENT + '_storage:' + self.credentials['storage'] + '@' + self.db + ':5432/' + ENVIRONMENT,
                   'maxConnections': 3, 'features': {'s3Protocol': {'enabled': False}, 'imageTransformation': {'enabled': False}}}
        status, _ = runtime.http(self.endpoint(self.storage, 5001) + '/tenants/' + ENVIRONMENT, 'POST',
                                 json.dumps(payload).encode(), {'apikey': self.admin_key, 'content-type': 'application/json'})
        self.check('original Storage admin registers scoped native tenant', status == 201)
        self.check('original Storage migrations create native object metadata', backup.sql("SELECT to_regclass('storage.objects') IS NOT NULL;", ENVIRONMENT) == 't')

    def api(self, path, method='GET', body=None, headers=None):
        common = {'authorization': 'Bearer ' + runtime.token(self.credentials['jwt'], 'service_role'),
                  'x-forwarded-host': ENVIRONMENT + '.storage.internal', 'content-type': 'application/json'}
        common.update(headers or {})
        return runtime.http(self.endpoint(self.storage, 5000) + path, method, body, common)

    def upload(self, content, upsert=False):
        status, _ = self.api('/object/' + BUCKET + '/' + OBJECT, 'POST', content,
                             {'content-type': 'text/plain', 'cache-control': 'max-age=60', 'x-upsert': 'true' if upsert else 'false'})
        self.check('original Storage HTTP ' + ('overwrite' if upsert else 'upload') + ' succeeds', status in (200, 201))

    def delete(self):
        status, _ = self.api('/object/' + BUCKET, 'DELETE', json.dumps({'prefixes': [OBJECT]}).encode())
        self.check('original Storage HTTP delete succeeds', status == 200)

    def verify_download(self, content):
        status, data = self.api('/object/' + BUCKET + '/' + OBJECT)
        self.check('original Storage HTTP returns expected checkpoint bytes', status == 200 and data == content)

    def rows(self):
        return json.loads(backup.sql("SELECT coalesce(jsonb_agg(jsonb_build_object('bucket_id',bucket_id,'name',name,'version',version,'metadata',metadata) ORDER BY bucket_id,name),'[]') FROM storage.objects;", ENVIRONMENT))

    def observe(self, label):
        rows = self.rows()
        code = r'''import base64,hashlib,json,os,pathlib,sys
root=pathlib.Path('/data/sbarbase-lab')/sys.argv[1]
files=[]
if root.exists():
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            files.append({'path':str(path.relative_to(root)), 'bytes':path.stat().st_size,
                          'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                          'xattrs':{key:base64.b64encode(os.getxattr(path,key)).decode() for key in sorted(os.listxattr(path)) if key.startswith('user.supabase.')}})
print(json.dumps(files))'''
        files = json.loads(self.helper('python3 -c "$1" "$2"', code, ENVIRONMENT).stdout)
        observation = {'phase': label, 'metadata_rows': rows, 'native_files': files}
        self.observations.append(observation)
        return observation

    @contextlib.contextmanager
    def settings(self):
        with super().settings(), contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(backup, 'start_storage', self.start_storage))
            stack.enter_context(patch.object(backup, 'wait_storage', lambda address: self.storage_ready()))
            stack.enter_context(patch.object(backup, 'wait_healthy', lambda environment: self.verify_download(self.expected_content)))
            backup.write_private(backup.STATE / 'endpoints.json', json.dumps({ENVIRONMENT: {'storage': {'tenantHost': ENVIRONMENT + '.storage.internal'}}}))
            yield

    def helper(self, script, *args, **kwargs):
        was_running = self.running(self.storage)
        if was_running:
            self.docker('stop', self.storage)
        try:
            return super().helper(script, *args, **kwargs)
        finally:
            if was_running:
                self.start_storage()
                self.storage_ready()

    def race(self, scenario, now):
        before = self.rows()
        self.verify_download(ORIGINAL)
        original_run = backup.run
        injected = []
        def run_with_race(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if 'pg_dump' in argv and ENVIRONMENT in argv and not injected:
                injected.append(True)
                if scenario == 'overwrite':
                    self.upload(CHANGED, upsert=True)
                else:
                    self.delete()
            return result
        stamp = now.strftime('%Y%m%dT%H%M%SZ')
        try:
            with patch.object(backup, 'run', run_with_race):
                archive, manifest = backup.create(ENVIRONMENT, now=now)
        except backup.BackupError:
            self.check('native HTTP ' + scenario + ' race refuses before complete manifest',
                       bool(injected) and not (backup.BACKUPS / ENVIRONMENT / stamp / 'manifest.json').exists())
            outcome = 'explicit-refusal-before-manifest'
        else:
            self.check('native HTTP ' + scenario + ' race actually occurs during held snapshot', bool(injected))
            backup_consistency.validate(archive / 'objects.tar', ENVIRONMENT, before, manifest['counts']['storage.objects'])
            self.check('native HTTP ' + scenario + ' race backup retains coherent snapshot references', True)
            outcome = 'coherent-checkpoint-admitted'
        observed = self.observe('after-' + scenario + '-race')
        self.upload(ORIGINAL, upsert=True)
        self.verify_download(ORIGINAL)
        return {'scenario': scenario, 'outcome': outcome, 'snapshot_metadata': before,
                'live_metadata_after_race': observed['metadata_rows']}

    def execute(self):
        self.stage = 'native-image-and-toolchain-preflight'
        backup.resolve_image(backup.image_pin('distro-image.lock.json'))
        backup.resolve_storage_image()
        backup.resolve_image(self.auth_pin)
        info = json.loads(self.docker('info', '--format', '{{json .}}').stdout)
        self.check('local Linux daemon required for unpublished bridge HTTP', info.get('OSType') == 'linux' and info.get('Name') == socket.gethostname())
        self.native_toolchain()
        self.stage = 'allocate-owned-runtime'
        self.absent('network', self.network)
        self.docker('network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + self.owner, self.network)
        self.resources.append(('network', self.network))
        self.volume(self.objects)
        self.volume(self.pgdata)
        self.start_database()
        self.provision()
        self.stage = 'original-auth-migration-bootstrap'
        self.bootstrap_auth()
        self.stage = 'original-storage-startup-and-registration'
        self.launch(self.storage, self.storage_reference, self.storage_configuration(), ((self.objects, '/tmp/storage-data'),))
        self.storage_ready()
        self.register_tenant()
        for name in backup.service_names(ENVIRONMENT):
            self.stopped_writer(name, self.storage_reference)
        status, _ = self.api('/bucket', 'POST', json.dumps({'id': BUCKET, 'name': BUCKET, 'public': False}).encode())
        self.check('original Storage service role creates private bucket', status in (200, 201))
        self.stage = 'ordinary-native-upload-and-backup'
        self.upload(ORIGINAL)
        self.verify_download(ORIGINAL)
        original_observation = self.observe('ordinary-native-upload')
        self.check('ordinary upload produces one native metadata reference', len(original_observation['metadata_rows']) == 1)
        now = datetime.datetime(2031, 1, 1, tzinfo=datetime.UTC)
        archive, manifest = backup.create(ENVIRONMENT, now=now)
        self.check('ordinary untouched native upload produces complete shipped backup', backup.verify(ENVIRONMENT, archive) == manifest)
        self.stage = 'ordinary-native-overwrite-and-delete'
        self.upload(CHANGED, upsert=True)
        self.verify_download(CHANGED)
        self.observe('ordinary-native-overwrite')
        self.delete()
        self.check('original Storage HTTP deletion removes native metadata', self.rows() == [])
        self.check('original Storage HTTP deletion removes download access', self.api('/object/' + BUCKET + '/' + OBJECT)[0] != 200)
        self.observe('ordinary-native-delete')
        self.upload(ORIGINAL, upsert=True)
        self.stage = 'native-http-snapshot-races'
        races = [self.race('overwrite', now.replace(day=2)), self.race('delete', now.replace(day=3))]
        self.stage = 'shipped-restore-and-native-http-download'
        self.upload(CHANGED, upsert=True)
        self.expected_content = ORIGINAL
        backup.restore(ENVIRONMENT, archive.name, now=now.replace(month=2))
        self.verify_download(ORIGINAL)
        self.check('shipped restore returns original native metadata and version', self.rows() == original_observation['metadata_rows'])
        restored = self.observe('after-shipped-restore')
        expected_files = original_observation['native_files']
        by_path = {item['path']: item for item in restored['native_files']}
        self.check('shipped restore preserves original native bytes and xattrs', all(by_path.get(item['path']) == item for item in expected_files))
        return {'schema': 1, 'status': 'native-storage-fixture-verified-full-recovery-unproven',
                'reference': base.pinned_identity(), 'scope': plan()['scope'], 'checks': sorted(set(self.checks)),
                'observations': self.observations, 'races': races, 'resources': plan()['resources'],
                'etag_configuration_override': False, 'native_metadata_edited_by_fixture': False,
                'auth_bootstrap': 'Original pinned GoTrue migrations only; process removed before Storage starts',
                'restore': 'Actual existing fenced environment restore followed by original Storage HTTP download',
                'required_unproven': list(UNPROVEN)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = plan()
    failed = 0
    if args.mode == 'run':
        if args.output is None:
            parser.error('run requires --output for sanitized durable evidence')
        with tempfile.TemporaryDirectory(prefix='sbarbase-native-storage-backup-') as directory:
            os.chmod(directory, 0o700)
            drill = NativeStorage(directory)
            try:
                with drill.settings():
                    result = drill.execute()
            except Exception as error:
                failed = 1
                result = {'schema': 1, 'status': 'refused', 'reference': base.pinned_identity(),
                          'scope': plan()['scope'], 'last_operation': drill.stage, 'error_class': type(error).__name__,
                          'checks_completed': sorted(set(drill.checks)), 'observations': drill.observations,
                          'required_unproven': list(UNPROVEN)}
                if type(error) is base.Refusal:
                    result['diagnostic'] = str(error)
            finally:
                try:
                    drill.cleanup()
                    result['cleanup'] = 'all exact owned disposable resources removed'
                except Exception:
                    failed = 1
                    result['cleanup'] = 'incomplete; exact owned names: ' + ', '.join(name for _, name in drill.resources)
    raw = json.dumps(result, sort_keys=True, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
        checks = result.get('checks', result.get('checks_completed', []))
        skipped = len(result['required_unproven']) if args.mode == 'run' else 0
        print(json.dumps({'status': result['status'], 'total': len(checks) + failed + skipped,
                          'executed': len(checks) + failed, 'failed': failed, 'skipped': skipped,
                          'required_unproven': result['required_unproven'], 'output': str(args.output)}, sort_keys=True))
    else:
        print(raw, end='')
    return failed


if __name__ == '__main__':
    raise SystemExit(main())
