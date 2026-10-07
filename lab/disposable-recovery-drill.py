#!/usr/bin/env python3
"""Public-clone recovery fixture using the shipped backup and fenced restore APIs.

Run ``plan`` without Docker. Run ``run --output FILE`` for disposable execution.
This is a bounded SQL and file fixture, not proof of complete Supabase recovery.
Only one test container runs at once. Nothing reads retained installation state.
"""
import argparse
import base64
import contextlib
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
from unittest.mock import patch

import backup
import image_identity
import recovery_bundle
import restore_cutover
import restore_operation
from restore_sql import quote_identifier

SOURCE = Path(__file__).resolve().parent.parent
ENVIRONMENT = 'e_' + 'd' * 24
NEIGHBOR = 'e_' + 'e' * 24
CONTENT = b'disposable recovery object\n'
VERSION = '11111111-2222-4333-8444-555555555555'
OBJECT_PATH = ENVIRONMENT + '/fixture/object.txt/' + VERSION
SUPPORTED = ('database_fixture', 'file_storage_fixture', 'storage_xattrs', 'operator_settings', 'scoped_secrets')
UNPROVEN = ('auth_api', 'rest_api', 'storage_api', 'signed_urls', 'realtime',
            'functions', 'vault', 'cron', 'queues', 'webhooks', 'logical_subscriptions',
            's3_protocol', 'external_object_store', 'tus', 'iceberg', 'management_identity')
LOCKS = ('distro-image.lock.json', 'storage-image.lock.json', 'images.lock.json',
         'realtime-image.lock.json', 'functions-image.lock.json', 'studio-image.lock.json')
CONTRACT = {
    'version': 1,
    'scope': 'SQL/file fixture only; complete application recovery remains unproven',
    'schemas': 'Entire trusted pg_dump archive, including custom schemas, data, types, functions, triggers, views, grants and RLS policies',
    'cluster': 'Scoped roles, memberships and role defaults travel in the encrypted package; image bootstrap supplies canonical roles',
    'storage': 'Versioned tenant bytes, object metadata, MIME/cache xattrs and shared tenant registration fixture',
    'configuration': 'Private operator settings and feature inventory; external addresses rebound to disposable target',
    'secrets': 'Encrypted package contains scoped source credentials; independent 32-byte key is required and never placed in evidence',
    'rotation': 'Rotate scoped database passwords, JWT secret and target platform credentials before reopening; old DB credentials must fail',
    'effects': 'All external effects and unproven enabled features refuse before resource allocation',
    'trust': 'Authenticated operator archive executes trusted administrator SQL; hostile concurrent host or catalog administration excluded',
}


class Refusal(RuntimeError):
    pass


def validate_features(features):
    if not isinstance(features, dict) or set(features) != set(SUPPORTED + UNPROVEN) \
            or any(type(value) is not bool for value in features.values()):
        raise Refusal('Complete boolean feature inventory required')
    enabled = sorted(name for name in UNPROVEN if features[name])
    if enabled:
        raise Refusal('Enabled features lack recovery proof: ' + ', '.join(enabled))
    if not all(features[name] for name in SUPPORTED):
        raise Refusal('Required fixture recovery capabilities are disabled')
    return features


def default_features():
    return {name: name in SUPPORTED for name in SUPPORTED + UNPROVEN}


def pinned_identity():
    spec = importlib.util.spec_from_file_location('disposable_reference', SOURCE / 'deploy/verify/reference_bundle.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    locks = {}
    for name in LOCKS:
        raw = (SOURCE / 'lab' / name).read_bytes()
        value = json.loads(raw)
        pins = [value] if 'id' in value else value.values()
        for pin in pins:
            image_identity.reference(pin)
        locks[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'pins': value}
    return {'repository': reference.REPOSITORY, 'tag': reference.TAG,
            'commit': reference.COMMIT, 'tag_object': reference.TAG_OBJECT,
            'candidate_locks': locks,
            'comparison': 'Pinned reference identity only; official application runtime comparison not performed'}


def plan(features=None):
    validate_features(default_features() if features is None else features)
    return {'schema': 1, 'status': 'planned-runtime-unrun', 'reference': pinned_identity(),
            'contract': CONTRACT, 'features': default_features() if features is None else features,
            'resources': {'max_running_test_containers': 1, 'database_memory_mib': 512,
                          'database_cpus': 0.5, 'helper_memory_mib': 256, 'helper_cpus': 0.5,
                          'network': 'none', 'published_ports': [], 'state': 'private temporary directory'},
            'steps': ['Inspect immutable images without pulling or mutating retained resources',
                      'Create uniquely named owned volumes, one database and stopped writer descriptors',
                      'Seed SQL schemas, RLS, grants, data, object bytes and tenant metadata fixtures',
                      'Create environment and shared metadata archives through backup.py',
                      'Inject overwrite and delete between SQL snapshot and file archive; require explicit refusal',
                      'Seal archives, settings, roles and secrets with recovery_bundle.py',
                      'Delete original database container and volume; use fresh target with rotated credentials',
                      'Restore shared metadata and environment through existing fenced restore subsystem',
                      'Interrupt first rename, recover explicitly and verify original writes survive',
                      'Fail readiness after reopen, write new data and retry recovery without replay',
                      'Verify data, policies, privileges, settings, file hashes, neighbor isolation and old credential refusal',
                      'Remove only exact owned disposable resources; publish sanitized evidence'],
            'required_unproven': list(UNPROVEN)}


def validate_package(payload):
    required = {'schema', 'environment', 'reference', 'contract', 'features', 'settings', 'secrets', 'roles', 'archives'}
    if not isinstance(payload, dict) or set(payload) != required or payload['schema'] != 1 \
            or payload['environment'] != ENVIRONMENT or payload['contract'] != CONTRACT \
            or payload['reference'] != pinned_identity():
        raise Refusal('Recovery package identity or contract differs')
    validate_features(payload['features'])
    expected = {scope + '/' + filename for scope, filenames in
                ((ENVIRONMENT, ('database.dump', 'objects.tar', 'manifest.json')),
                 ('storage', ('database.dump', 'manifest.json'))) for filename in filenames}
    if not isinstance(payload['archives'], dict) or set(payload['archives']) != expected:
        raise Refusal('Recovery package archive inventory differs')
    if set(payload['secrets']) != {'auth', 'rest', 'storage', 'jwt'}:
        raise Refusal('Scoped role and secret inventory differs')
    validate_roles(payload['roles'])
    if payload['settings'] != {'public_url': 'https://disposable.invalid', 'backup_hour': 3}:
        raise Refusal('Operator settings lack fixture recovery proof')
    if any(not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
           for value in payload['secrets'].values()):
        raise Refusal('Scoped credential format differs')
    for row in payload['archives'].values():
        if not isinstance(row, dict) or set(row) != {'data', 'sha256'}:
            raise Refusal('Archive part metadata differs')
        raw = base64.b64decode(row['data'], validate=True)
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise Refusal('Encrypted archive part checksum differs')
    return payload


def validate_roles(inventory):
    names = {ENVIRONMENT + '_' + kind for kind in ('auth', 'rest', 'storage')}
    fields = {'rolname', 'rolcanlogin', 'rolinherit', 'rolsuper', 'rolcreatedb', 'rolcreaterole',
              'rolreplication', 'rolbypassrls', 'rolconnlimit', 'rolvaliduntil', 'rolconfig'}
    if not isinstance(inventory, dict) or set(inventory) != {'records', 'memberships'} \
            or not isinstance(inventory['records'], list) or len(inventory['records']) != 3 \
            or any(not isinstance(row, dict) for row in inventory['records']) \
            or {row.get('rolname') for row in inventory['records']} != names:
        raise Refusal('Scoped role records are incomplete')
    for row in inventory['records']:
        if set(row) != fields or row['rolcanlogin'] is not True or type(row['rolinherit']) is not bool \
                or any(row[key] is not False for key in ('rolsuper', 'rolcreatedb', 'rolcreaterole', 'rolreplication', 'rolbypassrls')) \
                or type(row['rolconnlimit']) is not int or row['rolconnlimit'] < -1 or row['rolvaliduntil'] is not None \
                or row['rolconfig'] is not None and not isinstance(row['rolconfig'], list):
            raise Refusal('Scoped role properties lack recovery proof')
        for setting in row['rolconfig'] or []:
            if not isinstance(setting, str) or '=' not in setting \
                    or setting.split('=', 1)[0] not in ('statement_timeout', 'transaction_timeout', 'search_path'):
                raise Refusal('Scoped role default lacks recovery proof')
    if not isinstance(inventory['memberships'], list):
        raise Refusal('Scoped membership inventory is incomplete')
    for row in inventory['memberships']:
        if not isinstance(row, dict) or set(row) != {'parent', 'member', 'admin_option', 'inherit_option', 'set_option'} \
                or row['member'] not in names or row['parent'] not in ('anon', 'authenticated', 'service_role') \
                or row['admin_option'] is not False or any(type(row[key]) is not bool for key in ('inherit_option', 'set_option')):
            raise Refusal('Scoped membership lacks recovery proof')
    edges = [(row['parent'], row['member']) for row in inventory['memberships']]
    if len(edges) != len(set(edges)):
        raise Refusal('Duplicate scoped membership is unsupported')
    return inventory


class Disposable:
    def __init__(self, directory):
        self.root = Path(directory)
        self.prefix = 'sbarbase-disposable-' + secrets.token_hex(8)
        self.owner = self.prefix
        self.db = self.prefix + '-db'
        self.storage = self.prefix + '-storage'
        self.objects = self.prefix + '-objects'
        self.pgdata = self.prefix + '-source-pgdata'
        self.resources = []
        self.native_run = backup.run
        self.native_helper = backup.helper
        self.checks = []
        self.expected_roles = None
        self.stage = 'image-preflight'
        self.db_reference = image_identity.reference(json.loads((SOURCE / 'lab/distro-image.lock.json').read_text()))
        self.storage_reference = image_identity.reference(json.loads((SOURCE / 'lab/storage-image.lock.json').read_text()))

    def docker(self, *args, **kwargs):
        return self.native_run(['docker', *args], **kwargs)

    def check(self, label, condition):
        if not condition:
            raise Refusal(label)
        self.checks.append(label)

    def absent(self, kind, name):
        result = self.docker(kind, 'inspect', name, check=False)
        if result.returncode == 0:
            raise Refusal('Disposable resource name collision')
        # Only an exact native absence allows allocation.
        detail = result.stderr.strip()
        accepted = {'container': {'Error: No such container: ' + name, 'Error response from daemon: No such container: ' + name},
                    'volume': {'Error response from daemon: get ' + name + ': no such volume'}}
        if result.stdout.strip() != '[]' or detail not in accepted[kind]:
            raise Refusal('Disposable resource absence is not proved')

    def native_toolchain(self):
        name = self.prefix + '-toolchain'
        self.absent('container', name)
        try:
            result = self.docker('run', '--rm', '--name', name, '--pull=never', '--network', 'none',
                                 '--read-only', '--memory', '256m', '--memory-swap', '256m', '--cpus', '.5',
                                 '--pids-limit', '64', '--label', 'io.sbarbase.owner=' + self.owner,
                                 '--entrypoint', 'python3', self.storage_reference, '-c',
                                 "import decimal,os,tarfile; assert all(hasattr(os,key) for key in ('listxattr','getxattr','setxattr')); print('native-python-toolchain-present')",
                                 timeout=30)
            self.check('native pinned Storage Python and file metadata toolchain available before data allocation',
                       result.stdout.strip() == 'native-python-toolchain-present')
        finally:
            existing = self.docker('container', 'inspect', name, check=False)
            if existing.returncode == 0:
                self.resources.append(('container', name))
                self.remove('container', name)

    def volume(self, name):
        self.absent('volume', name)
        self.docker('volume', 'create', '--label', 'io.sbarbase.owner=' + self.owner, name)
        self.resources.append(('volume', name))

    def stopped_writer(self, name, image, storage=False):
        self.absent('container', name)
        argv = ['create', '--name', name, '--pull=never', '--network', 'none', '--memory', '256m',
                '--memory-swap', '256m', '--cpus', '.5', '--pids-limit', '64',
                '--label', 'io.sbarbase.owner=' + self.owner, '--entrypoint', '/bin/true']
        if storage:
            for key, value in {'STORAGE_BACKEND': 'file', 'GLOBAL_S3_BUCKET': backup.TENANT_PARENT,
                               'FILE_STORAGE_BACKEND_PATH': '/tmp/storage-data', 'MULTI_TENANT': 'true',
                               'S3_PROTOCOL_ENABLED': 'false', 'PG_QUEUE_ENABLE': 'false'}.items():
                argv.extend(['-e', key + '=' + value])
            argv.extend(['--mount', 'type=volume,source=' + self.objects + ',target=/tmp/storage-data'])
        self.docker(*argv, image)
        self.resources.append(('container', name))

    def start_database(self):
        self.absent('container', self.db)
        secret_file = self.root / 'database.env'
        backup.write_private(secret_file, 'POSTGRES_PASSWORD=' + secrets.token_hex(32) + '\nPOSTGRES_HOST=/var/run/postgresql\nPOSTGRES_DB=postgres\n')
        self.docker('run', '-d', '--name', self.db, '--pull=never', '--network', 'none',
                    '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5', '--pids-limit', '128',
                    '--label', 'io.sbarbase.owner=' + self.owner, '--env-file', str(secret_file),
                    '--mount', 'type=volume,source=' + self.pgdata + ',target=/var/lib/postgresql/data',
                    '--mount', 'type=volume,source=' + self.objects + ',target=/data',
                    self.db_reference, 'postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf',
                    '-c', 'log_statement=none')
        self.resources.append(('container', self.db))
        self.ready()

    def ready(self):
        image = self.docker('image', 'inspect', self.db_reference)
        if image.stderr.strip():
            raise Refusal('Disposable database image inspection emitted diagnostics')
        native = image_identity.resolved_id(self.db_reference, image_identity.record(image.stdout))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            inspected = self.docker('container', 'inspect', self.db)
            if inspected.stderr.strip():
                raise Refusal('Disposable database inspection emitted diagnostics')
            item = image_identity.record(inspected.stdout)
            state = item.get('State', {})
            if item.get('Name') != '/' + self.db or item.get('Image') != native \
                    or item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != self.owner:
                raise Refusal('Disposable database readiness identity differs')
            if state.get('Running') is not True or state.get('OOMKilled') is not False \
                    or state.get('Paused') is not False or state.get('Restarting') is not False:
                raise Refusal('Disposable database stopped or exceeded its memory limit during bootstrap')
            health = state.get('Health', {}).get('Status')
            if health not in ('starting', 'unhealthy', 'healthy'):
                raise Refusal('Original pinned database native health state unavailable')
            # The image briefly serves privileged SQL during initialization.
            # Its native health gate must admit the final server first.
            if health == 'healthy':
                result = self.docker('exec', self.db, 'psql', '-X', '-qAt', '-U', 'supabase_admin',
                                     '-d', 'postgres', '-c', "SELECT to_regrole('supabase_privileged_role') IS NOT NULL;", check=False)
                if not result.returncode and result.stdout.strip() == 't':
                    return
            time.sleep(.5)
        raise Refusal('Disposable pinned database bootstrap timed out')

    @contextlib.contextmanager
    def settings(self):
        (self.root / 'lab').mkdir()
        for filename in LOCKS:
            shutil.copyfile(SOURCE / 'lab' / filename, self.root / 'lab' / filename)
        state = backup.private_dir(self.root / 'state')
        values = {'ROOT': self.root, 'STATE': state, 'BACKUPS': self.root / 'backups',
                  'PREFIX': self.prefix, 'DB': self.db, 'DATABASE_OWNER': self.owner,
                  'OBJECTS_VOLUME': self.objects, 'STORAGE_CONTAINER': self.storage}
        with contextlib.ExitStack() as stack:
            for key, value in values.items():
                stack.enter_context(patch.object(backup, key, value))
            stack.enter_context(patch.object(backup, 'helper', self.helper))
            stack.enter_context(patch.object(backup, 'start_storage', lambda: 'disposable-sql-fixture'))
            stack.enter_context(patch.object(backup, 'wait_storage', lambda address: self.verify_storage()))
            stack.enter_context(patch.object(backup, 'start_services', lambda environment: None))
            stack.enter_context(patch.object(backup, 'wait_healthy', lambda environment: self.verify_data()))
            backup.write_private(state / 'endpoints.json', json.dumps({ENVIRONMENT: {}, NEIGHBOR: {}}))
            yield

    def helper(self, script, *args, **kwargs):
        # The original Node/tar helper runs unchanged, with the admitted source volume.
        # No snapshot or held stage session exists at the points the API calls this.
        self.docker('stop', self.db)
        helper_name = self.prefix + '-helper-' + secrets.token_hex(4)
        self.absent('container', helper_name)
        original_run = backup.run
        def owned_helper(argv, **options):
            if argv[:3] == ['docker', 'run', '--rm']:
                argv = [*argv[:3], '--name', helper_name, *argv[3:]]
                argv = [value.replace('io.sbarbase.owner=backup', 'io.sbarbase.owner=' + self.owner) for value in argv]
            return original_run(argv, **options)
        try:
            with patch.object(backup, 'run', owned_helper):
                return self.native_helper(script, *args, **kwargs)
        finally:
            remaining = self.docker('container', 'inspect', helper_name, check=False)
            if remaining.returncode == 0:
                self.resources.append(('container', helper_name))
                self.remove('container', helper_name)
            self.docker('start', self.db)
            self.ready()

    def remove(self, kind, name):
        result = self.docker(kind, 'inspect', name)
        item = image_identity.record(result.stdout)
        labels = item.get('Config', {}).get('Labels') if kind == 'container' else item.get('Labels')
        if not isinstance(labels, dict) or labels.get('io.sbarbase.owner') != self.owner:
            raise Refusal('Disposable cleanup ownership changed')
        self.docker(kind, 'rm', *(['-f'] if kind == 'container' else []), name)
        self.resources.remove((kind, name))

    def cleanup(self):
        failures = []
        for kind, name in list(reversed(self.resources)):
            try:
                self.remove(kind, name)
            except Exception:
                failures.append(kind + ':' + name)
        if failures:
            raise Refusal('Disposable cleanup incomplete: ' + ', '.join(failures))

    def roles(self, credentials):
        for kind in ('auth', 'rest', 'storage'):
            role = ENVIRONMENT + '_' + kind
            limit = {'auth': 5, 'rest': 7, 'storage': 6}[kind]
            backup.sql("CREATE ROLE " + role + " LOGIN NOINHERIT CONNECTION LIMIT " + str(limit) + " PASSWORD '" + credentials[kind] + "';")
            if kind == 'rest':
                backup.sql('GRANT anon, authenticated, service_role TO ' + role + ';')
        backup.sql('ALTER ROLE ' + ENVIRONMENT + '_rest SET statement_timeout TO \'8s\';')

    def role_inventory(self):
        selected = ','.join("'" + ENVIRONMENT + '_' + kind + "'" for kind in ('auth', 'rest', 'storage'))
        records = json.loads(backup.sql("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY rolname),'[]') FROM (SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_roles WHERE rolname IN (" + selected + ')) t;'))
        memberships = json.loads(backup.sql("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY parent,member),'[]') FROM (SELECT p.rolname parent,r.rolname member,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m JOIN pg_roles p ON p.oid=m.roleid JOIN pg_roles r ON r.oid=m.member WHERE r.rolname IN (" + selected + ')) t;'))
        return validate_roles({'records': records, 'memberships': memberships})

    def replay_roles(self, inventory, rotated):
        validate_roles(inventory)
        def literal(value):
            return "'" + value.replace("'", "''") + "'"
        for row in inventory['records']:
            name = quote_identifier(row['rolname'])
            kind = row['rolname'].rsplit('_', 1)[1]
            backup.sql('CREATE ROLE ' + name + ' LOGIN ' + ('INHERIT' if row['rolinherit'] else 'NOINHERIT') +
                       ' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT ' + str(row['rolconnlimit']) +
                       ' PASSWORD ' + literal(rotated[kind]) + ';')
            for setting in row['rolconfig'] or []:
                key, value = setting.split('=', 1)
                backup.sql('ALTER ROLE ' + name + ' SET ' + quote_identifier(key) + ' TO ' + literal(value) + ';')
        for row in inventory['memberships']:
            backup.sql('GRANT ' + quote_identifier(row['parent']) + ' TO ' + quote_identifier(row['member']) +
                       ' WITH INHERIT ' + ('TRUE' if row['inherit_option'] else 'FALSE') +
                       ', SET ' + ('TRUE' if row['set_option'] else 'FALSE') + ';')
        self.check('scoped role properties defaults and memberships replayed from encrypted source inventory', self.role_inventory() == inventory)

    def object_metadata(self, content=CONTENT):
        return {'eTag': '"' + hashlib.md5(content).hexdigest() + '"', 'size': len(content),
                'mimetype': 'text/plain', 'cacheControl': 'max-age=60'}

    def set_object_attributes(self):
        code = 'import os,sys; os.setxattr(sys.argv[1],"user.supabase.content-type",b"text/plain"); os.setxattr(sys.argv[1],"user.supabase.cache-control",b"max-age=60")'
        self.helper('python3 -c "$1" "$2"', code, '/data/' + backup.TENANT_PARENT + '/' + OBJECT_PATH, writable=True)

    def seed(self, credentials):
        self.roles(credentials)
        for database in (ENVIRONMENT, NEIGHBOR, backup.STORAGE_DATABASE):
            backup.sql('CREATE DATABASE ' + database + ' TEMPLATE template0 OWNER supabase_admin;')
        backup.sql('CREATE TABLE neighbor_marker(value text); INSERT INTO neighbor_marker VALUES (\'neighbor-intact\');', NEIGHBOR)
        metadata = json.dumps(self.object_metadata())
        backup.sql("""CREATE SCHEMA auth; CREATE TABLE auth.users(id text PRIMARY KEY); INSERT INTO auth.users VALUES ('user-a');
          CREATE TABLE auth.identities(id text); INSERT INTO auth.identities VALUES ('identity-a');
          CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text); INSERT INTO storage.buckets VALUES ('fixture');
          CREATE TABLE storage.objects(id text,bucket_id text,name text,version text,metadata jsonb);
          INSERT INTO storage.objects VALUES ('object-a','fixture','object.txt','""" + VERSION + """','""" + metadata + """');
          CREATE SCHEMA app_data; CREATE TYPE app_data.status AS ENUM ('open','closed');
          CREATE TABLE app_data.records(id int PRIMARY KEY,owner_id text,value text,status app_data.status);
          INSERT INTO app_data.records VALUES (1,'user-a','recover-me','open'),(2,'user-b','private','closed');
          ALTER TABLE app_data.records ENABLE ROW LEVEL SECURITY;
          CREATE POLICY own_rows ON app_data.records TO authenticated USING (owner_id=current_setting('request.jwt.claim.sub',true));
          GRANT USAGE ON SCHEMA app_data TO authenticated; GRANT SELECT ON app_data.records TO authenticated;
          CREATE FUNCTION app_data.label(value text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT upper(value) $$;
          CREATE VIEW app_data.labels AS SELECT id,app_data.label(value) AS label FROM app_data.records;
          CREATE TABLE app_data.audit(id int);
          CREATE FUNCTION app_data.record_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO app_data.audit VALUES (NEW.id); RETURN NEW; END $$;
          CREATE TRIGGER writes AFTER INSERT ON app_data.records FOR EACH ROW EXECUTE FUNCTION app_data.record_write();
          ALTER DATABASE """ + ENVIRONMENT + """ CONNECTION LIMIT 30;
          ALTER ROLE """ + ENVIRONMENT + """_rest IN DATABASE """ + ENVIRONMENT + """ SET search_path TO app_data, public;
          REVOKE CONNECT ON DATABASE """ + ENVIRONMENT + """ FROM PUBLIC;
          GRANT CONNECT ON DATABASE """ + ENVIRONMENT + """ TO """ + ENVIRONMENT + """_auth,""" + ENVIRONMENT + """_rest,""" + ENVIRONMENT + """_storage;
        """, ENVIRONMENT)
        backup.sql("CREATE TABLE public.tenants(id text PRIMARY KEY,config jsonb); INSERT INTO public.tenants VALUES ('" + ENVIRONMENT + "','{\"fixture\":true}'),('" + NEIGHBOR + "','{\"neighbor\":true}');", backup.STORAGE_DATABASE)
        self.docker('exec', self.db, 'mkdir', '-p', '/data/' + backup.TENANT_PARENT + '/' + ENVIRONMENT + '/fixture/object.txt')
        # A binary pipe writes exact bytes without putting content in shell source.
        result = subprocess.run(['docker', 'exec', '-i', self.db, 'sh', '-c', 'cat > /data/"$1"',
                                 'fixture', backup.TENANT_PARENT + '/' + OBJECT_PATH], input=CONTENT, capture_output=True)
        if result.returncode:
            raise Refusal('Disposable object seed failed')
        self.set_object_attributes()
        self.verify_data()

    def verify_storage(self):
        self.check('shared tenant metadata and neighbor registration restored', backup.storage_counts() == {'tenants': 2, 'ids': [ENVIRONMENT, NEIGHBOR]})

    def verify_data(self):
        self.check('fixture Auth and Storage metadata counts restored', backup.counts(ENVIRONMENT) == {'auth.users': 1, 'auth.identities': 1, 'storage.buckets': 1, 'storage.objects': 1})
        self.check('custom schema rows and enum restored', backup.sql("SELECT string_agg(id||':'||value||':'||status,',' ORDER BY id) FROM app_data.records WHERE id<=2;", ENVIRONMENT) == '1:recover-me:open,2:private:closed')
        self.check('custom function and view restored', backup.sql('SELECT label FROM app_data.labels WHERE id=1;', ENVIRONMENT) == 'RECOVER-ME')
        self.check('RLS enabled and policy restored', backup.sql("SELECT relrowsecurity FROM pg_class WHERE oid='app_data.records'::regclass;", ENVIRONMENT) == 't' and backup.sql("SELECT count(*) FROM pg_policies WHERE schemaname='app_data' AND policyname='own_rows';", ENVIRONMENT) == '1')
        self.check('RLS allows only authenticated subject rows', backup.sql("BEGIN; SET LOCAL ROLE authenticated; SET LOCAL request.jwt.claim.sub='user-a'; SELECT string_agg(id::text,',') FROM app_data.records WHERE id<=2; ROLLBACK;", ENVIRONMENT) == '1')
        self.check('anonymous custom table grant absent', backup.sql("SELECT has_table_privilege('anon','app_data.records','SELECT');", ENVIRONMENT) == 'f')
        self.check('database connection limit restored', backup.sql("SELECT datconnlimit FROM pg_database WHERE datname='" + ENVIRONMENT + "';") == '30')
        self.check('scoped database role search path restored', backup.sql("SELECT array_to_string(setconfig,',') FROM pg_db_role_setting WHERE setrole='" + ENVIRONMENT + "_rest'::regrole AND setdatabase=(SELECT oid FROM pg_database WHERE datname='" + ENVIRONMENT + "');") == 'search_path=app_data, public')
        self.check('neighbor data survives restore', backup.sql('SELECT value FROM neighbor_marker;', NEIGHBOR) == 'neighbor-intact')
        if self.expected_roles is not None:
            self.check('scoped role properties defaults and memberships match source', self.role_inventory() == self.expected_roles)
        metadata = json.loads(backup.sql('SELECT to_jsonb(t) FROM (SELECT bucket_id,name,version,metadata FROM storage.objects) t;', ENVIRONMENT))
        self.check('object metadata references restored version and content fingerprint', metadata == {'bucket_id': 'fixture', 'name': 'object.txt', 'version': VERSION, 'metadata': self.object_metadata()})
        result = self.docker('exec', self.db, 'sha256sum', '/data/' + backup.TENANT_PARENT + '/' + OBJECT_PATH)
        self.check('restored versioned object content hash matches', result.stdout.split()[0] == hashlib.sha256(CONTENT).hexdigest())
        code = 'import os,sys,json; print(json.dumps({key:os.getxattr(sys.argv[1],"user.supabase."+key).decode() for key in ("content-type","cache-control")}))'
        result = self.helper('python3 -c "$1" "$2"', code, '/data/' + backup.TENANT_PARENT + '/' + OBJECT_PATH)
        self.check('native MIME and cache metadata xattrs restored', json.loads(result.stdout) == {'content-type': 'text/plain', 'cache-control': 'max-age=60'})

    def races(self):
        outcomes = []
        for offset, scenario in enumerate(('overwrite', 'delete'), 1):
            fired = []
            original = backup.run
            def racing_run(argv, **kwargs):
                result = original(argv, **kwargs)
                if 'pg_dump' in argv and ENVIRONMENT in argv and not fired:
                    fired.append(True)
                    if scenario == 'overwrite':
                        changed = 'x' * len(CONTENT)
                        self.docker('exec', self.db, 'sh', '-c', 'printf %s "$2" > /data/"$1"', 'fixture', backup.TENANT_PARENT + '/' + OBJECT_PATH, changed)
                        metadata = json.dumps(self.object_metadata(changed.encode()))
                        backup.sql("UPDATE storage.objects SET metadata='" + metadata + "';", ENVIRONMENT)
                    else:
                        self.docker('exec', self.db, 'rm', '/data/' + backup.TENANT_PARENT + '/' + OBJECT_PATH)
                        backup.sql('DELETE FROM storage.objects;', ENVIRONMENT)
                return result
            stamp = datetime.datetime(2030, 1, offset + 1, tzinfo=datetime.UTC)
            try:
                with patch.object(backup, 'run', racing_run):
                    backup.create(ENVIRONMENT, now=stamp)
            except backup.BackupError:
                refused = True
            else:
                refused = False
            self.check('concurrent ' + scenario + ' refuses incoherent manifest', refused and bool(fired) and not (backup.BACKUPS / ENVIRONMENT / stamp.strftime('%Y%m%dT%H%M%SZ') / 'manifest.json').exists())
            outcomes.append({'scenario': scenario, 'status': 'refused-before-manifest'})
            result = subprocess.run(['docker', 'exec', '-i', self.db, 'sh', '-c', 'cat > /data/"$1"',
                                     'fixture', backup.TENANT_PARENT + '/' + OBJECT_PATH], input=CONTENT, capture_output=True)
            if result.returncode:
                raise Refusal('Race fixture reset failed')
            self.set_object_attributes()
            metadata = json.dumps(self.object_metadata())
            backup.sql("DELETE FROM storage.objects; INSERT INTO storage.objects VALUES ('object-a','fixture','object.txt','" + VERSION + "','" + metadata + "');", ENVIRONMENT)
        return outcomes

    def execute(self, features):
        validate_features(features)
        backup.resolve_image(backup.image_pin('distro-image.lock.json'))
        backup.resolve_storage_image()
        self.stage = 'prove-native-storage-toolchain'
        self.native_toolchain()
        self.stage = 'allocate-source'
        self.volume(self.objects)
        self.volume(self.pgdata)
        self.stopped_writer(self.storage, self.storage_reference, storage=True)
        for name in backup.service_names(ENVIRONMENT):
            self.stopped_writer(name, self.storage_reference)
        self.start_database()
        credentials = {key: secrets.token_hex(32) for key in ('auth', 'rest', 'storage', 'jwt')}
        self.seed(credentials)
        self.expected_roles = self.role_inventory()
        self.stage = 'backup-source'
        now = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
        archive, _ = backup.create(ENVIRONMENT, now=now)
        shared, _ = backup.create_storage(now=now)
        self.stage = 'concurrent-storage-races'
        race_evidence = self.races()
        self.stage = 'encrypt-package'
        parts = {}
        for scope, folder in ((ENVIRONMENT, archive), ('storage', shared)):
            for filename in ('database.dump', 'manifest.json') + (('objects.tar',) if scope == ENVIRONMENT else ()):
                raw = (folder / filename).read_bytes()
                parts[scope + '/' + filename] = {'data': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}
        payload = {'schema': 1, 'environment': ENVIRONMENT, 'reference': pinned_identity(), 'contract': CONTRACT,
                   'features': features, 'settings': {'public_url': 'https://disposable.invalid', 'backup_hour': 3},
                   'secrets': credentials, 'roles': self.expected_roles, 'archives': parts}
        validate_package(payload)
        key = secrets.token_bytes(32)
        bundle = recovery_bundle.seal(payload, key)
        backup.write_private(self.root / 'recovery.json', json.dumps(bundle))
        backup.write_private(self.root / 'recovery.key', base64.b64encode(key).decode())
        self.check('encrypted package authenticates complete fixture inventory', recovery_bundle.open_bundle(bundle, key) == payload)
        try:
            recovery_bundle.open_bundle(bundle, secrets.token_bytes(32))
        except Exception:
            self.check('unavailable or incorrect independent key refuses recovery', True)
        else:
            raise Refusal('Wrong-key recovery accepted')
        self.stage = 'validate-disk-recovery-material-before-source-deletion'
        recovered = validate_package(recovery_bundle.open_bundle(
            json.loads((self.root / 'recovery.json').read_text()),
            base64.b64decode((self.root / 'recovery.key').read_text(), validate=True)))
        self.check('disk envelope and independent key authenticate before source deletion', recovered == payload)
        self.stage = 'delete-source-and-allocate-target'
        self.remove('container', self.db)
        self.remove('volume', self.pgdata)
        for name in [self.storage, *backup.service_names(ENVIRONMENT)]:
            self.remove('container', name)
        self.remove('volume', self.objects)
        shutil.rmtree(backup.BACKUPS)
        self.pgdata = self.prefix + '-target-pgdata'
        self.volume(self.objects)
        self.volume(self.pgdata)
        self.stopped_writer(self.storage, self.storage_reference, storage=True)
        for name in backup.service_names(ENVIRONMENT):
            self.stopped_writer(name, self.storage_reference)
        self.start_database()
        # A native Storage writer would initialize this owned backend root. The
        # stopped descriptor deliberately has no startup effects in this fixture.
        self.docker('exec', self.db, 'mkdir', '-p', '/data/' + backup.TENANT_PARENT)
        rotated = {kind: secrets.token_hex(32) for kind in credentials}
        self.stage = 'replay-target-roles-and-config'
        self.replay_roles(recovered['roles'], rotated)
        for database in (ENVIRONMENT, NEIGHBOR, backup.STORAGE_DATABASE):
            backup.sql('CREATE DATABASE ' + database + ' TEMPLATE template0 OWNER supabase_admin;')
        backup.sql("CREATE TABLE neighbor_marker(value text); INSERT INTO neighbor_marker VALUES ('neighbor-intact');", NEIGHBOR)
        backup.sql('CREATE TABLE tenants(id text);', backup.STORAGE_DATABASE)
        backup.sql("CREATE TABLE recovery_original_marker(value text); INSERT INTO recovery_original_marker VALUES ('target-write-before-restore');", ENVIRONMENT)
        for name, row in recovered['archives'].items():
            scope, filename = name.split('/')
            folder = backup.private_dir(backup.BACKUPS / scope / now.strftime('%Y%m%dT%H%M%SZ'))
            target = folder / filename
            target.write_bytes(base64.b64decode(row['data'], validate=True))
            target.chmod(0o600)
        backup.write_private(self.root / 'target-settings.json', json.dumps(recovered['settings']))
        backup.write_private(self.root / 'target-secrets.json', json.dumps(rotated))
        self.check('operator settings restored from independent encrypted package', json.loads((self.root / 'target-settings.json').read_text()) == payload['settings'])
        self.check('scoped target secrets rotate deliberately', all(rotated[k] != credentials[k] for k in rotated))
        self.stage = 'restore-shared-storage-metadata'
        backup.restore_storage(now.strftime('%Y%m%dT%H%M%SZ'), now=now.replace(day=5))
        original_phase = restore_cutover.phase
        interruption_reached = []
        def interrupt(api, journal, name):
            original_phase(api, journal, name)
            if name == 'original-renamed':
                interruption_reached.append(True)
                raise Refusal('Deterministic cutover interruption')
        failed_stamp = now.replace(day=6).strftime('%Y%m%dt%H%M%Sz')
        self.stage = 'interrupt-first-rename-and-recover'
        with patch.object(restore_cutover, 'phase', interrupt):
            try:
                backup.restore(ENVIRONMENT, now.strftime('%Y%m%dT%H%M%SZ'), now=now.replace(day=6))
            except backup.BackupError:
                self.check('deterministic interruption reaches first database rename', bool(interruption_reached))
            else:
                raise Refusal('Expected cutover interruption did not occur')
        # Rollback readiness verifies only the original placeholder, not restored data.
        with patch.object(backup, 'wait_healthy', lambda environment: None):
            rolled = backup.recover_restore(ENVIRONMENT, now.strftime('%Y%m%dT%H%M%SZ'), failed_stamp)
        self.check('first rename interruption recovers explicitly to original', rolled['status'] == 'rolled-back' and backup.sql("SELECT to_regclass('app_data.records') IS NULL;", ENVIRONMENT) == 't')
        self.check('first rename rollback preserves preexisting target write', backup.sql('SELECT value FROM recovery_original_marker;',ENVIRONMENT) == 'target-write-before-restore')
        stamp = now.replace(day=7).strftime('%Y%m%dt%H%M%Sz')
        self.stage = 'fail-readiness-after-reopen-and-preserve-writes'
        with patch.object(backup, 'wait_healthy', side_effect=Refusal('Deterministic readiness failure')):
            try:
                backup.restore(ENVIRONMENT, now.strftime('%Y%m%dT%H%M%SZ'), now=now.replace(day=7))
            except backup.BackupError as error:
                if 'complete-restore' not in str(error):
                    raise Refusal('Restore failed before readiness reopening: ' + str(error)) from None
                self.check('reopened readiness failure leaves explicit completion pending', True)
            else:
                raise Refusal('Expected readiness failure did not occur')
        backup.sql("INSERT INTO app_data.records VALUES (3,'user-a','post-reopen','open');", ENVIRONMENT)
        backup.recover_restore(ENVIRONMENT, now.strftime('%Y%m%dT%H%M%SZ'), stamp)
        self.check('recovery after reopen preserves new writes', backup.sql('SELECT value FROM app_data.records WHERE id=3;', ENVIRONMENT) == 'post-reopen')
        self.check('restored trigger continues recording writes', backup.sql('SELECT id FROM app_data.audit;', ENVIRONMENT) == '3')
        # Force loopback password authentication in this isolated target only.
        self.stage = 'verify-target-credential-rotation'
        hba = 'local all supabase_admin trust\nhost all all 127.0.0.1/32 scram-sha-256\nhost all all ::1/128 scram-sha-256\n'
        location = backup.sql('SHOW hba_file;')
        process = subprocess.run(['docker', 'exec', '-i', self.db, 'sh', '-c', 'cat > "$1"', 'fixture', location], input=hba.encode(), capture_output=True)
        if process.returncode:
            raise Refusal('Disposable credential probe configuration failed')
        backup.sql('SELECT pg_reload_conf();')
        for kind in ('auth', 'rest', 'storage'):
            def connect(password):
                return subprocess.run(['docker', 'exec', '-i', self.db, 'sh', '-c',
                    'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -qAt -h 127.0.0.1 -U "$1" -d "$2" -c "SELECT 1"',
                    'fixture', ENVIRONMENT + '_' + kind, ENVIRONMENT], input=password + '\n', capture_output=True, text=True)
            fresh, old = connect(rotated[kind]), connect(credentials[kind])
            self.check('rotated ' + kind + ' database password accepted and old password refused', fresh.returncode == 0 and fresh.stdout.strip() == '1' and old.returncode != 0)
        self.verify_data()
        return {'schema': 1, 'status': 'fixture-verified-complete-application-unproven',
                'reference': pinned_identity(), 'contract': CONTRACT, 'checks': sorted(set(self.checks)),
                'races': race_evidence, 'resources': plan(features)['resources'],
                'fresh_cluster': True, 'source_database_and_volume_deleted_before_restore': True,
                'source_object_volume_deleted_before_restore': True,
                'application_services_started': [], 'required_unproven': list(UNPROVEN),
                'limitations': ['SQL fixture schemas are deliberately smaller than official Auth/Storage migrations',
                                'Stopped writer descriptors prove admission barriers, not actual Storage or Auth behavior',
                                'JWT rotation proves changed secret only; HTTP token invalidation remains unproven',
                                'Encrypted envelope is bounded to 32MiB; large backups require the existing offsite streaming path',
                                'Neighbor is an independent target fixture, not transferred source neighbor data']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--features', type=Path)
    args = parser.parse_args(argv)
    features = json.loads(args.features.read_text()) if args.features else default_features()
    result = plan(features)
    if args.mode == 'run':
        if args.output is None:
            parser.error('run requires --output for durable sanitized evidence')
        with tempfile.TemporaryDirectory(prefix='sbarbase-disposable-recovery-') as directory:
            os.chmod(directory, 0o700)
            drill = Disposable(directory)
            failure = False
            try:
                with drill.settings():
                    result = drill.execute(features)
            except Exception as error:
                failure = True
                result = {'schema': 1, 'status': 'refused', 'reference': pinned_identity(),
                          'checks_completed': sorted(set(drill.checks)),
                          'scope': CONTRACT['scope'], 'required_unproven': list(UNPROVEN),
                          'last_operation': drill.stage, 'error_class': type(error).__name__,
                          'failure_reason': str(error) if isinstance(error,(Refusal,backup.BackupError)) else 'Private native diagnostic withheld',
                          'diagnostic': 'Private command diagnostics withheld; inspect the last completed check'}
            finally:
                try:
                    drill.cleanup()
                    result['cleanup'] = 'all exact owned disposable resources removed'
                except Exception:
                    failure = True
                    result['cleanup'] = 'incomplete; exact owned names: ' + ', '.join(name for _, name in drill.resources)
    raw = json.dumps(result, sort_keys=True, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
        checks = result.get('checks', result.get('checks_completed', []))
        failed = int(args.mode == 'run' and failure)
        skipped = len(result['required_unproven']) if args.mode == 'run' else 0
        print(json.dumps({'status': result['status'], 'total': len(checks) + skipped + failed,
                          'executed': len(checks) + failed, 'failed': failed, 'skipped': skipped,
                          'required_unproven': result['required_unproven'], 'output': str(args.output)}, sort_keys=True))
    else:
        print(raw, end='')
    return 1 if args.mode == 'run' and failure else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Refusal, backup.BackupError, OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit('Disposable recovery drill refused; private data withheld. No application recovery claim.')
