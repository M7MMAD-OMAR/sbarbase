"""Native backup v1 data workflows on an owned synthetic PostgreSQL installation."""
import contextlib
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import signal
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import backup
import restore_cutover
import restore_operation
from restore_session import SessionError
import image_identity
from run_checks import source_digest


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    if 'SBARBASE_FENCED_BOUNDARIES' in os.environ:
        raise ValueError('Subset interruption selection cannot produce a full fenced matrix result')
    db, network, volume = [fixture + '-fenced-' + suffix for suffix in ('db', 'net', 'objects')]
    owner = 'sbarbase-fixture-' + fixture
    environment = 'e_' + 'a' * 24
    report = {'scope': 'native-fenced-production-cutover-and-recovery', 'source_sha256': source_digest(),
              'commands': [], 'attempted_commands': [], 'service_doubles': [], 'cases': [], 'passed': False,
              'limitations': ['Synthetic schemas and bytes, not actual Supabase Auth, REST or Storage services.',
                              'Auth/REST lifecycle, endpoint publication and HTTP/TCP health use explicit doubles.',
                              'Owned sleeping Storage container stop/start and native stopped identity checks are real; no Storage API runs.',
                              'Startup/publication/health failures are constrained doubles; actual Supabase application readiness is not proved.',
                              'Only classified core workers enabled; full Supabase default worker support remains unproven.',
                              'Native image/owner admission, PostgreSQL snapshot/counts/dump/restore/SQL and Storage tar are real.',
                              'Interruption controls SIGKILL the owned verifier actor at actual native cutover boundaries.',
                              'No cloud import, complete installation recovery, PITR, off-host restore, classic store or release proof.',
                              'Fresh bounded tmpfs database and owned volume/internal network; no ports or private host data.']}
    original_run, original_popen = subprocess.run, subprocess.Popen
    db_id = None
    storage_cid = None
    network_created = volume_created = False
    helper_sequence = 0
    fail_restore = fail_tar = False
    concurrent_write = False
    lifecycle_failure = None
    active_holders = []
    temporary = tempfile.TemporaryDirectory(prefix='sbarbase-backup-workflow-')
    workspace = Path(temporary.name)
    backup.STATE, backup.BACKUPS = workspace / 'upstream', workspace / 'backups'
    backup.PREFIX, backup.DB, backup.DATABASE_OWNER, backup.OBJECTS_VOLUME = fixture + '-fenced', db, owner, volume
    backup.STORAGE_CONTAINER = fixture + '-fenced-storage'
    backup.STATE.mkdir(mode=0o700)
    (backup.STATE / 'endpoints.json').write_text(json.dumps({environment: {'auth': 'double://auth', 'rest': 'double://rest'}}))
    original_session = restore_cutover.HeldSession

    class FixtureSession(original_session):
        def __exit__(self, kind, value, traceback):
            try:
                return super().__exit__(kind, value, traceback)
            finally:
                if self.private_diagnostic:
                    report.setdefault('owned_session_diagnostics', []).append(
                        self.private_diagnostic[:4096].decode('utf-8', errors='replace'))

    restore_cutover.HeldSession = FixtureSession

    def native(argv, *, stdin=None, input=None, stdout=subprocess.PIPE, text=True, timeout=60, check=True):
        result = original_run(argv, stdin=stdin, input=input, stdout=stdout, stderr=subprocess.PIPE,
                              text=text, timeout=timeout)
        error = result.stderr if text else result.stderr.decode(errors='replace')
        entry = {'args': argv, 'exit_code': result.returncode, 'stderr': error}
        if isinstance(result.stdout, str):
            entry['stdout'] = result.stdout
        elif isinstance(result.stdout, bytes):
            entry.update(stdout_bytes=len(result.stdout), stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        report['commands'].append(entry)
        if 'warning' in error.lower():
            raise RuntimeError('Native workflow warning')
        if check and result.returncode:
            raise RuntimeError('Native workflow command failed: ' + error.strip())
        return result

    def sql(database, statement):
        return native(['docker', 'exec', '-i', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                       '-U', 'supabase_admin', '-d', database], input=statement).stdout.strip()

    def guard_database(argv):
        offset = 3 if argv[2] == '-i' else 2
        if argv[offset] != db_id or argv[offset + 1] not in ('psql', 'pg_dump', 'pg_restore'):
            raise RuntimeError('Unowned database command')

    def backup_run(argv, *, stdin=None, stdout=subprocess.PIPE, check=True, text=True, timeout=3600):
        nonlocal helper_sequence, concurrent_write, fail_restore, fail_tar
        report['attempted_commands'].append(argv)
        if argv[:3] == ['docker', 'image', 'inspect']:
            if len(argv) != 4 or argv[3] not in references:
                raise RuntimeError('Unexpected image inspection')
        elif argv[:3] == ['docker', 'container', 'inspect']:
            if argv not in (['docker', 'container', 'inspect', db], ['docker', 'container', 'inspect', backup.STORAGE_CONTAINER]):
                raise RuntimeError('Unexpected container inspection')
        elif argv[:3] == ['docker', 'volume', 'inspect']:
            if argv != ['docker', 'volume', 'inspect', volume]:
                raise RuntimeError('Unexpected volume inspection')
        elif argv[:2] == ['docker', 'stop']:
            if argv[2:] == [storage_cid]:
                return native(argv, check=check)
            if argv[2:] not in (backup.service_names(environment),):
                raise RuntimeError('Unexpected service lifecycle')
            report['service_doubles'].append({'operation': 'stop', 'services': argv[2:]})
            return subprocess.CompletedProcess(argv, 0, '', '')
        elif argv[:2] == ['docker', 'exec']:
            guard_database(argv)
            if 'pg_dump' in argv and concurrent_write and argv[-1] == environment:
                if not any(arg.startswith('--snapshot=') for arg in argv):
                    raise RuntimeError('Backup dump did not use exported snapshot')
                sql(environment, "INSERT INTO auth.users VALUES (2,'after-export');")
                concurrent_write = False
                report['concurrent_writer'] = 'Committed after exported snapshot, before native pg_dump'
            if 'pg_restore' in argv and fail_restore:
                expected_previous = environment + '_pre_' + when.strftime('%Y%m%dt%H%M%Sz')
                if sql('postgres', "SELECT count(*) FROM pg_database WHERE datname='" + expected_previous + "';") != '1' \
                        or sql('postgres', "SELECT count(*) FROM pg_database WHERE datname='" + environment + "';") != '0':
                    raise RuntimeError('Native failure requires exact renamed previous database and absent live database')
                fail_restore = False
                result = native(argv, input=b'not-a-postgresql-archive', stdout=stdout, text=False, check=False)
                report['failure_injection_pg_restore'] = {'exit_code': result.returncode, 'after_database_rename': True}
                if result.returncode == 0:
                    raise RuntimeError('Invalid native archive unexpectedly restored')
                raise backup.BackupError('Native injected pg_restore failed with exit ' + str(result.returncode))
        elif argv[:2] == ['docker', 'run']:
            if '--pull=never' not in argv or storage_reference not in argv or '--network' not in argv or argv[argv.index('--network') + 1] != 'none':
                raise RuntimeError('Unexpected helper image or network')
            if f'{volume}:/data' not in argv and f'{volume}:/data:ro' not in argv:
                raise RuntimeError('Unexpected helper volume')
            if helper_sequence >= 128:
                raise RuntimeError('Fixture helper maximum of 128 reached before creation')
            helper_sequence += 1
            argv = argv[:2] + ['--name', fixture + '-fenced-helper-' + str(helper_sequence), '--cap-drop', 'ALL',
                              '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--memory-swap', '256m'] + argv[2:]
            if fail_tar and 'tar -xf -' in argv[argv.index('-c') + 1]:
                fail_tar = False
                aside = '.pre-restore-' + environment + '-' + when.strftime('%Y%m%dt%H%M%Sz')
                backup.helper('test -f /data/' + backup.TENANT_PARENT + '/' + aside + '/bucket/kept.txt')
                result = native(argv, input=b'not-a-tar-archive', stdout=stdout, text=False, check=False)
                report['failure_injection_tar'] = {'exit_code': result.returncode, 'after_files_move': True}
                if result.returncode == 0:
                    raise RuntimeError('Invalid native tar unexpectedly extracted')
                raise backup.BackupError('Native injected tar failed with exit ' + str(result.returncode))
        else:
            raise RuntimeError('Unexpected backup Docker operation')
        result = native(argv, stdin=stdin, stdout=stdout, text=text, timeout=min(timeout, 60), check=False)
        if 'pg_restore' in argv and '--file=-' in argv and hasattr(stdout, 'seek'):
            position = stdout.tell()
            stdout.seek(0)
            generated = stdout.read(1024 * 1024 + 1)
            stdout.seek(position)
            if len(generated) > 1024 * 1024:
                raise RuntimeError('Synthetic archive SQL diagnostic exceeds fixture bound')
            diagnostic_name = 'generated-archive-' + str(len(report.setdefault('owned_archive_sql', []))) + '.sql'
            Path('/evidence', diagnostic_name).write_bytes(generated)
            report['owned_archive_sql'].append(diagnostic_name)
        if check and result.returncode:
            raise backup.BackupError('Native backup command failed with exit ' + str(result.returncode))
        return result

    def sql_transport(argv, **kwargs):
        report['attempted_commands'].append(argv)
        if argv[:3] != ['docker', 'exec', '-i']:
            raise RuntimeError('Unexpected SQL transport')
        guard_database(argv)
        return native(argv, input=kwargs['input'], text=True, check=False, timeout=60)

    def snapshot_transport(argv, **kwargs):
        report['attempted_commands'].append(argv)
        guard_database(argv)
        report['commands'].append({'args': argv, 'operation': 'held-native-psql-snapshot'})
        return original_popen(argv, **kwargs)

    def start_services(e):
        if e != environment:
            raise RuntimeError('Unowned service restart')
        report['service_doubles'].append({'operation': 'start-and-publish', 'services': backup.service_names(e)})
        if lifecycle_failure == 'environment-startup':
            raise RuntimeError('synthetic startup private diagnostic')

    def wait_healthy(e, timeout=180):
        if e != environment:
            raise RuntimeError('Unowned health query')
        report['service_doubles'].append({'operation': 'health', 'environment': e})
        if lifecycle_failure == 'environment-health':
            raise RuntimeError('synthetic health private diagnostic')
        return True

    def start_storage():
        native(['docker', 'start', storage_cid])
        report['service_doubles'].append({'operation': 'start-and-publish-storage'})
        if lifecycle_failure == 'storage-startup':
            raise RuntimeError('synthetic startup private diagnostic')
        return 'double://storage'

    def wait_storage(address, timeout=180):
        if address != 'double://storage':
            raise RuntimeError('Unexpected Storage health address')
        report['service_doubles'].append({'operation': 'storage-health'})
        if lifecycle_failure == 'storage-health':
            raise RuntimeError('synthetic health private diagnostic')
        return True

    def add_case(name, **details):
        report['cases'].append({'case': name, 'passed': True, **details})

    def props(database):
        return sql('postgres', "SELECT json_build_object('limit',datconnlimit,'allow',datallowconn,'acl',datacl::text,"
            "'owner',pg_get_userbyid(datdba),'encoding',pg_encoding_to_char(encoding),'collate',datcollate,'ctype',datctype,"
            "'provider',datlocprovider,'locale',to_jsonb(d)->'datlocale','rules',to_jsonb(d)->'daticurules',"
            "'version',datcollversion,'comment',shobj_description(d.oid,'pg_database'),'settings',"
            "(SELECT coalesce(json_agg(json_build_object('role',setrole::text,'config',setconfig) ORDER BY setrole),'[]'::json) "
            "FROM pg_db_role_setting WHERE setdatabase=d.oid)) FROM pg_database d WHERE datname='" + database + "';")

    def rows(database):
        if database == backup.STORAGE_DATABASE:
            return sql(database, 'SELECT id,payload FROM public.tenants ORDER BY id;')
        return {table: sql(database, "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text, '[]') FROM " + table + ' t;')
                for table in ('auth.users', 'auth.identities', 'storage.buckets', 'storage.objects')}

    def object_bytes():
        return backup.helper('cat /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt').stdout

    def assert_no_record(path, when):
        stamp = when.strftime('%Y%m%dt%H%M%Sz')
        if (path / ('restore-' + stamp + '.json')).exists():
            raise RuntimeError('Failed restore wrote a success record')

    references = set()
    try:
        db_reference = image_identity.reference(json.loads((ROOT / 'lab/distro-image.lock.json').read_text()))
        storage_reference = image_identity.reference(json.loads((ROOT / 'lab/storage-image.lock.json').read_text()))
        references.update((db_reference, storage_reference))
        for reference in references:
            image_identity.resolved_id(reference, image_identity.record(native(['docker', 'image', 'inspect', reference]).stdout))
        native(['docker', 'network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network])
        network_created = True
        native(['docker', 'volume', 'create', '--label', 'io.sbarbase.owner=' + owner, volume])
        volume_created = True
        db_id = native(['docker', 'run', '-d', '--pull=never', '--name', db, '--label', 'io.sbarbase.owner=' + owner,
                        '--network', network, '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5',
                        '--pids-limit', '128', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                        '--env', 'PGHOST=127.0.0.1', '--user', 'postgres', '--tmpfs', '/tmp:rw,mode=1777,size=256m',
                        '--entrypoint', 'sh', db_reference, '-c',
                        'initdb -D /tmp/fixture-pg -U supabase_admin --auth=trust --no-locale >/tmp/initdb.log && '
                        "exec postgres -D /tmp/fixture-pg -c listen_addresses='*' -c unix_socket_directories=/tmp "
                        '-c shared_buffers=32MB -c max_connections=20 -c max_prepared_transactions=4']).stdout.strip()
        deadline = time.monotonic() + 30
        while True:
            ready = native(['docker', 'exec', db_id, 'pg_isready', '-h', '127.0.0.1', '-U', 'supabase_admin'], check=False)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Fixture PostgreSQL readiness deadline')
            time.sleep(.2)
        storage_cid = native(['docker', 'run', '-d', '--pull=never', '--name', backup.STORAGE_CONTAINER,
            '--label', 'io.sbarbase.owner=' + owner, '--network', network, '--memory', '256m', '--memory-swap', '256m',
            '--cpus', '.25', '--pids-limit', '64', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '-v', volume + ':/tmp/storage-data', '--env', 'STORAGE_BACKEND=file', '--env', 'GLOBAL_S3_BUCKET=sbarbase-lab',
            '--env', 'FILE_STORAGE_BACKEND_PATH=/tmp/storage-data', '--env', 'MULTI_TENANT=true',
            '--env', 'S3_PROTOCOL_ENABLED=false', '--env', 'PG_QUEUE_ENABLE=false', '--entrypoint', 'sh', storage_reference,
            '-c', 'exec sleep 1800']).stdout.strip()
        for database in (environment, backup.STORAGE_DATABASE):
            sql('postgres', 'CREATE DATABASE ' + database + '; REVOKE ALL ON DATABASE ' + database + ' FROM PUBLIC; '
                'ALTER DATABASE ' + database + ' CONNECTION LIMIT 7;')
        sql(environment, "CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE TABLE auth.users(id integer PRIMARY KEY,payload text); "
            "CREATE TABLE auth.identities(id integer PRIMARY KEY); CREATE TABLE storage.buckets(id text PRIMARY KEY); "
            "CREATE TABLE storage.objects(id integer PRIMARY KEY); INSERT INTO auth.users VALUES (1,'at-backup'); "
            "INSERT INTO auth.identities VALUES (1); INSERT INTO storage.buckets VALUES ('bucket'); INSERT INTO storage.objects VALUES (1);")
        sql(backup.STORAGE_DATABASE, "CREATE TABLE tenants(id text PRIMARY KEY,payload text); INSERT INTO tenants VALUES ('" + environment + "','synthetic-key-marker');")
        writer_roles = [environment + '_' + name for name in ('auth', 'rest', 'storage', 'developer', 'studio', 'realtime')]
        super_role = 'fixture_normal_super'
        for role in writer_roles:
            sql('postgres', 'CREATE ROLE ' + role + ' LOGIN; GRANT CONNECT ON DATABASE ' + environment + ' TO ' + role + ';')
            sql(environment, 'GRANT USAGE ON SCHEMA auth TO ' + role + '; GRANT SELECT,UPDATE ON auth.users TO ' + role + ';')
            sql('postgres', 'GRANT CONNECT ON DATABASE ' + backup.STORAGE_DATABASE + ' TO ' + role + ';')
            sql(backup.STORAGE_DATABASE, 'GRANT USAGE ON SCHEMA public TO ' + role + '; GRANT SELECT,UPDATE ON public.tenants TO ' + role + ';')
            native(['docker', 'exec', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', role,
                    '-d', environment, '-c', 'UPDATE auth.users SET payload=payload WHERE id=1;'])
        sql('postgres', 'CREATE ROLE ' + super_role + ' LOGIN SUPERUSER; CREATE DATABASE fixture_neighbor;')
        native(['docker', 'exec', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', super_role,
                '-d', environment, '-c', 'UPDATE auth.users SET payload=payload WHERE id=1;'])
        sql('fixture_neighbor', "CREATE TABLE sentinel(value text); INSERT INTO sentinel VALUES ('neighbor-intact');")
        sql('postgres', "COMMENT ON DATABASE " + environment + " IS 'Archive CONNECTION LIMIT literal'; "
            "ALTER DATABASE " + environment + " SET application_name TO 'archive-setting'; "
            "ALTER ROLE " + writer_roles[0] + " IN DATABASE " + environment + " SET statement_timeout TO '3s'; "
            "ALTER DATABASE " + backup.STORAGE_DATABASE + " SET search_path TO '';")
        report['native_worker_inventory'] = json.loads(sql('postgres', restore_operation.WORKER_QUERY))
        backup.run = backup_run
        backup.subprocess = SimpleNamespace(run=sql_transport, Popen=snapshot_transport, PIPE=subprocess.PIPE,
                                            DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
        backup.start_services, backup.wait_healthy = start_services, wait_healthy
        backup.start_storage, backup.wait_storage = start_storage, wait_storage
        backup.helper('mkdir -p /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket && '
                      'printf fixture-at-backup > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt', writable=True)
        before_rows = rows(environment)
        before_props = props(environment)
        before_storage_props = props(backup.STORAGE_DATABASE)
        clock = datetime.datetime(2026, 10, 3, 6, tzinfo=datetime.UTC)
        concurrent_write = True
        path, manifest = backup.create(environment, now=clock)
        if manifest['version'] != 1 or manifest['counts'] != {'auth.users': 1, 'auth.identities': 1, 'storage.buckets': 1, 'storage.objects': 1}:
            raise RuntimeError('Snapshot manifest counts differ')
        if sql(environment, 'SELECT id,payload FROM auth.users ORDER BY id;') != '1|at-backup\n2|after-export':
            raise RuntimeError('Concurrent writer did not commit')
        if any(rows(environment)[table] != before_rows[table] for table in ('auth.identities', 'storage.buckets', 'storage.objects')):
            raise RuntimeError('Concurrent writer changed unrelated synthetic tables')
        if manifest['images'] != {'db': backup.image_pin('distro-image.lock.json')['id'], 'storage': backup.storage_image()}:
            raise RuntimeError('Logical image pins changed')
        if {item.name for item in path.iterdir()} != {'database.dump', 'objects.tar', 'manifest.json'}:
            raise RuntimeError('Unexpected archive members')
        if path.stat().st_mode & 0o777 != 0o700 or any(item.stat().st_mode & 0o777 != 0o600 for item in path.iterdir()):
            raise RuntimeError('Backup permissions are not private')
        with tarfile.open(path / 'objects.tar') as archive:
            expected_file = environment + '/bucket/kept.txt'
            if {member.name for member in archive.getmembers()} != {environment, environment + '/bucket', expected_file}:
                raise RuntimeError('Tar includes unexpected tenant members')
            with archive.extractfile(expected_file) as archived_file:
                if archived_file.read() != b'fixture-at-backup':
                    raise RuntimeError('Tar fixture object differs')
        backup.verify(environment, path)
        add_case('native-create-snapshot-counts-private-v1-archives', snapshot_users=1, live_users=2,
                 database_sha256=manifest['database']['sha256'], objects_sha256=manifest['objects']['sha256'],
                 expected_snapshot_tables=before_rows, live_after_export_tables=rows(environment))
        backup.helper('printf after-backup > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt', writable=True)
        restored = backup.restore(environment, path.name, now=clock + datetime.timedelta(minutes=1))
        if rows(environment) != before_rows or object_bytes() != 'fixture-at-backup' or props(environment) != before_props:
            raise RuntimeError('Native successful restore rows/files/ACL/limit differ')
        if not (path / ('restore-' + restored['restored_at'] + '.json')).is_file():
            raise RuntimeError('Successful restore record missing')
        add_case('native-successful-environment-restore', record=restored, database_properties=props(environment), expected_tables=before_rows, actual_tables=rows(environment))
        storage_path, storage_manifest = backup.create_storage(now=clock)
        backup.verify_storage(storage_path)
        if storage_manifest['version'] != 1 or storage_manifest['tenants'] != [environment] or storage_manifest['counts'] != {'tenants': 1}:
            raise RuntimeError('Storage metadata manifest differs')
        if {item.name for item in storage_path.iterdir()} != {'database.dump', 'manifest.json'} or any(item.stat().st_mode & 0o777 != 0o600 for item in storage_path.iterdir()):
            raise RuntimeError('Storage backup contents/permissions differ')
        sql(backup.STORAGE_DATABASE, "UPDATE public.tenants SET payload='after-backup';")
        storage_record = backup.restore_storage(storage_path.name, now=clock + datetime.timedelta(minutes=2))
        if rows(backup.STORAGE_DATABASE) != environment + '|synthetic-key-marker' or props(backup.STORAGE_DATABASE) != before_storage_props:
            raise RuntimeError('Storage native restore differs')
        add_case('native-storage-metadata-create-restore', record=storage_record,
                 expected_rows=environment + '|synthetic-key-marker', actual_rows=rows(backup.STORAGE_DATABASE),
                 expected_database_properties=before_storage_props, actual_database_properties=props(backup.STORAGE_DATABASE))
        target_oid = backup.database_oid(environment)
        sql(environment, "BEGIN; UPDATE auth.users SET payload=payload WHERE id=1; PREPARE TRANSACTION 'fenced_fixture_prepared';")
        attempt_start = len(report['attempted_commands'])
        try:
            backup.restore(environment, path.name, now=clock + datetime.timedelta(minutes=3))
        except backup.BackupError as error:
            if 'Prepared transactions' not in str(error):
                raise
        else:
            raise RuntimeError('Actual prepared transaction did not refuse restore')
        if backup.database_oid(environment) != target_oid or any(
                command[:2] in (['docker', 'stop'], ['docker', 'run']) for command in report['attempted_commands'][attempt_start:]):
            raise RuntimeError('Prepared refusal mutated database or writer lifecycle')
        sql(environment, "ROLLBACK PREPARED 'fenced_fixture_prepared';")
        add_case('native-prepared-transaction-refuses-before-mutation')
        storage_oid = backup.database_oid(backup.STORAGE_DATABASE)
        sql(backup.STORAGE_DATABASE, "BEGIN; UPDATE public.tenants SET payload=payload; PREPARE TRANSACTION 'fenced_shared_prepared';")
        attempt_start = len(report['attempted_commands'])
        try:
            backup.restore_storage(storage_path.name, now=clock + datetime.timedelta(minutes=4))
        except backup.BackupError as error:
            if 'Prepared transactions' not in str(error):
                raise
        else:
            raise RuntimeError('Actual shared prepared transaction did not refuse restore')
        if backup.database_oid(backup.STORAGE_DATABASE) != storage_oid or any(
                command[:2] in (['docker', 'stop'], ['docker', 'run']) for command in report['attempted_commands'][attempt_start:]):
            raise RuntimeError('Shared prepared refusal mutated database or writer lifecycle')
        sql(backup.STORAGE_DATABASE, "ROLLBACK PREPARED 'fenced_shared_prepared';")
        add_case('native-shared-prepared-transaction-refuses-before-mutation')

        environment_boundaries = ('stage-created', 'original-renamed', 'stage-renamed', 'files-aside', 'files-ready', 'data-ready', 'opened')
        storage_boundaries = ('stage-created', 'original-renamed', 'stage-renamed', 'data-ready', 'opened')
        scenarios = [(environment, boundary) for boundary in environment_boundaries] \
            + [(backup.STORAGE, boundary) for boundary in storage_boundaries]
        report['required_matrix'] = {'environment': list(environment_boundaries), 'storage': list(storage_boundaries)}
        report['required_recovery_matrix'] = {'environment': ['original-opened'], 'storage': ['original-opened']}
        original_phase = restore_cutover.phase
        for index, (scope, boundary) in enumerate(scenarios):
            shared_scope = scope == backup.STORAGE
            target_database = backup.STORAGE_DATABASE if shared_scope else environment
            target_archive = storage_path if shared_scope else path
            relation = 'public.tenants' if shared_scope else 'auth.users'
            predicate = "id='" + environment + "'" if shared_scope else 'id=1'
            write_query = 'UPDATE ' + relation + ' SET payload=payload WHERE ' + predicate + ';'
            counterpart = environment if shared_scope else backup.STORAGE_DATABASE
            counterpart_rows = rows(counterpart)
            when = clock + datetime.timedelta(minutes=10 + index)
            sql(target_database, 'UPDATE ' + relation + " SET payload='before-" + boundary + "' WHERE " + predicate + ';')
            original_objects = None
            if not shared_scope:
                backup.helper('printf "$1" > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt',
                              'before-' + boundary, writable=True)
                original_objects = object_bytes()
            original_rows = rows(target_database)
            original_oid = backup.database_oid(target_database)
            holders = []
            for role in (*writer_roles, super_role):
                report['current_step'] = {'scope': scope, 'boundary': boundary, 'role': role, 'step': 'held-writer-connect'}
                held = original_session(['docker', 'exec', '-i', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                                         '-U', role, '-d', target_database])
                active_holders.append(held)
                report['current_step']['step'] = 'held-writer-pid'
                pid = held.execute('SELECT pg_backend_pid();')
                report['current_step']['step'] = 'held-writer-write'
                held.execute(write_query)
                holders.append((role, pid, held))
            actor_file = workspace / ('actor-' + scope + '-' + boundary + '.json')
            commands_before, attempts_before, doubles_before = (len(report[key]) for key in ('commands', 'attempted_commands', 'service_doubles'))
            actor = os.fork()
            if actor == 0:
                def interrupted_phase(api, journal, name):
                    if name == boundary:
                        report['interrupted_journal'] = journal
                        report['owned_cutover_state'] = {item.name: json.loads(item.read_text()) for item in
                            [*backup.STATE.glob('restore-operation-*.json'), *backup.STATE.glob('restore-completions/*.json')]}
                        report['helper_count'] = helper_sequence
                        with actor_file.open('w') as handle:
                            json.dump(report, handle)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.kill(os.getpid(), signal.SIGSTOP)
                    return original_phase(api, journal, name)
                restore_cutover.phase = interrupted_phase
                try:
                    if shared_scope:
                        backup.restore_storage(target_archive.name, now=when)
                    else:
                        backup.restore(scope, target_archive.name, now=when)
                    failure = 'Actor missed requested actual interruption boundary'
                except BaseException as error:
                    failure = type(error).__name__ + ': ' + str(error)
                actor_file.write_text(json.dumps({'actor_error': failure}))
                os._exit(73)
            stopped = False
            observed = 0
            deadline = time.monotonic() + 180
            try:
                while time.monotonic() < deadline:
                    observed, status = os.waitpid(actor, os.WNOHANG | os.WUNTRACED)
                    if observed:
                        if not os.WIFSTOPPED(status):
                            raise RuntimeError('Interruption actor exited: ' + actor_file.read_text()[:4096])
                        if os.WSTOPSIG(status) != signal.SIGSTOP:
                            raise RuntimeError('Interruption actor stopped by an unexpected signal')
                        stopped = True
                        break
                    time.sleep(.1)
                if not stopped:
                    raise RuntimeError('Interruption actor did not reach boundary before deadline')
            finally:
                if stopped or observed == 0:
                    os.kill(actor, signal.SIGKILL)
                    _, killed = os.waitpid(actor, 0)
                    if not os.WIFSIGNALED(killed) or os.WTERMSIG(killed) != signal.SIGKILL:
                        raise RuntimeError('Owned actor was not actually killed by SIGKILL')
            actor_report = json.loads(actor_file.read_text())
            for key, offset in (('commands', commands_before), ('attempted_commands', attempts_before), ('service_doubles', doubles_before)):
                report[key].extend(actor_report[key][offset:])
            report['owned_archive_sql'] = actor_report.get('owned_archive_sql', report.get('owned_archive_sql', []))
            helper_sequence = actor_report['helper_count']
            journal = restore_operation.read_journal(backup.STATE, scope)
            state_files = [*backup.STATE.glob('restore-operation-*.json'), *backup.STATE.glob('restore-completions/*.json')]
            status_before = {str(item.relative_to(backup.STATE)): item.read_bytes() for item in state_files}
            status_rows = backup.restore_status()
            status_output = io.StringIO()
            with contextlib.redirect_stdout(status_output):
                status_exit = backup.main(['restore-status'])
            if status_before != {str(item.relative_to(backup.STATE)): item.read_bytes() for item in state_files}:
                raise RuntimeError('Read-only status changed actual durable interrupted control state')
            expected_command = 'python3 lab/backup.py recover-restore ' + scope + ' ' + target_archive.name + ' ' + journal['stamp']
            if not any(item['scope'] == scope and item['command'] == expected_command for item in status_rows):
                raise RuntimeError('Actual message-free SIGKILL status did not yield exact guarded recovery command')
            if status_exit != 0 or expected_command not in status_output.getvalue():
                raise RuntimeError('Actual restore-status CLI omitted guarded recovery command')
            current_state = restore_cutover.inventory(backup, journal)
            storage_state = image_identity.record(native(['docker', 'container', 'inspect', backup.STORAGE_CONTAINER]).stdout)
            if boundary != 'stage-created':
                restore_cutover.storage(backup, storage_cid, stopped=True)
            writer_proofs = []
            for fenced_database, state in current_state.items():
                if state is None or state['allow_connections']:
                    continue
                for role in (*writer_roles, super_role):
                    result = native(['docker', 'exec', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                        '-U', role, '-d', fenced_database, '-c', write_query], check=False)
                    if result.returncode == 0 or 'not currently accepting connections' not in result.stderr:
                        raise RuntimeError('Fenced database admitted writer or produced an unknown refusal')
                    writer_proofs.append({'database': fenced_database, 'role': role, 'exit_code': result.returncode, 'write_query': write_query})
            if sql('fixture_neighbor', 'SELECT value FROM sentinel;') != 'neighbor-intact':
                raise RuntimeError('Neighbor database changed at interruption')
            sql('fixture_neighbor', 'UPDATE sentinel SET value=value;')
            if boundary == 'opened':
                sql(target_database, 'UPDATE ' + relation + " SET payload='post-open-write' WHERE " + predicate + ';')
                if not shared_scope:
                    backup.helper('printf post-open-write > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt', writable=True)
                preserved_rows = rows(target_database)
            held_pids = ','.join(pid for _, pid, _ in holders)
            if boundary != 'stage-created' and sql('postgres', 'SELECT count(*) FROM pg_stat_activity WHERE pid IN (' + held_pids + ');') != '0':
                raise RuntimeError('Old ordinary/superuser held writer backend survived cutover draining')
            if boundary == 'stage-created':
                recovery_file = workspace / ('recovery-actor-' + scope + '.json')
                recovery_offsets = {key: len(report[key]) for key in ('commands', 'attempted_commands', 'service_doubles')}
                recovery_actor = os.fork()
                if recovery_actor == 0:
                    def interrupted_original_open(api, saved, name):
                        if name == 'original-opened':
                            report['owned_original_open_state'] = {item.name: json.loads(item.read_text()) for item in
                                [*backup.STATE.glob('restore-operation-*.json'), *backup.STATE.glob('restore-completions/*.json')]}
                            report['helper_count'] = helper_sequence
                            with recovery_file.open('w') as handle:
                                json.dump(report, handle)
                                handle.flush()
                                os.fsync(handle.fileno())
                            os.kill(os.getpid(), signal.SIGSTOP)
                        return original_phase(api, saved, name)
                    restore_cutover.phase = interrupted_original_open
                    try:
                        backup.recover_restore(scope, target_archive.name, journal['stamp'])
                        failure = 'Recovery actor missed actual original reopen boundary'
                    except BaseException as error:
                        failure = type(error).__name__ + ': ' + str(error)
                    recovery_file.write_text(json.dumps({'actor_error': failure}))
                    os._exit(73)
                recovery_observed, recovery_stopped = 0, False
                deadline = time.monotonic() + 180
                try:
                    while time.monotonic() < deadline:
                        recovery_observed, recovery_status = os.waitpid(recovery_actor, os.WNOHANG | os.WUNTRACED)
                        if recovery_observed:
                            if not os.WIFSTOPPED(recovery_status) or os.WSTOPSIG(recovery_status) != signal.SIGSTOP:
                                raise RuntimeError('Recovery actor did not stop at original reopening: ' + recovery_file.read_text()[:4096])
                            recovery_stopped = True
                            break
                        time.sleep(.1)
                    if not recovery_stopped:
                        raise RuntimeError('Recovery actor did not reach original reopening before deadline')
                finally:
                    if recovery_stopped or recovery_observed == 0:
                        os.kill(recovery_actor, signal.SIGKILL)
                        _, recovery_killed = os.waitpid(recovery_actor, 0)
                        if not os.WIFSIGNALED(recovery_killed) or os.WTERMSIG(recovery_killed) != signal.SIGKILL:
                            raise RuntimeError('Recovery actor was not actually killed by SIGKILL')
                recovery_report = json.loads(recovery_file.read_text())
                for key, offset in recovery_offsets.items():
                    report[key].extend(recovery_report[key][offset:])
                helper_sequence = recovery_report['helper_count']
                reopened_journal = restore_operation.read_journal(backup.STATE, scope)
                if reopened_journal['phase'] != 'rollback-open-intent' or backup.database_oid(target_database) != original_oid:
                    raise RuntimeError('Original reopen crash did not retain exact durable intent and old OID')
                sql(target_database, 'UPDATE ' + relation + " SET payload='post-original-open-write' WHERE " + predicate + ';')
                original_rows = rows(target_database)
                if not shared_scope:
                    backup.helper('printf post-original-open-write > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt', writable=True)
                    original_objects = object_bytes()
                saved_files = [*backup.STATE.glob('restore-operation-*.json'), *backup.STATE.glob('restore-completions/*.json')]
                saved_bytes = {str(item.relative_to(backup.STATE)): item.read_bytes() for item in saved_files}
                discovered = backup.restore_status()
                if not any(item['command'] == expected_command and item['phase'] == 'rollback-open-intent' for item in discovered) \
                        or saved_bytes != {str(item.relative_to(backup.STATE)): item.read_bytes() for item in saved_files}:
                    raise RuntimeError('Original reopen interruption status mutated state or omitted guarded command')
            recovery_output = io.StringIO()
            with contextlib.redirect_stdout(recovery_output):
                recovery_exit = backup.main(['recover-restore', scope, target_archive.name, journal['stamp']])
            expected_prefix = 'restore recovery completed for ' + scope + ': '
            if recovery_exit != 0 or not recovery_output.getvalue().startswith(expected_prefix):
                raise RuntimeError('Guarded recover-restore CLI refused or omitted completion output')
            recovered = {'status': recovery_output.getvalue()[len(expected_prefix):].strip()}
            if boundary == 'stage-created':
                attempts_before_retry = len(report['attempted_commands'])
                retry_helper_count = helper_sequence
                retry_output = io.StringIO()
                with contextlib.redirect_stdout(retry_output):
                    retry_exit = backup.main(['recover-restore', scope, target_archive.name, journal['stamp']])
                if retry_exit != 0 or retry_output.getvalue() != expected_prefix + 'rolled-back\n':
                    raise RuntimeError('Guarded repeated recover-restore CLI refused or omitted rollback result')
                if helper_sequence != retry_helper_count or any(command[:2] == ['docker', 'stop'] for command in report['attempted_commands'][attempts_before_retry:]):
                    raise RuntimeError('Repeat original recovery performed destructive replay or writer stop')
                if rows(target_database) != original_rows or backup.database_oid(target_database) != original_oid \
                        or not shared_scope and object_bytes() != original_objects:
                    raise RuntimeError('Repeat original recovery destroyed post-original-open writes')
                add_case('native-' + ('storage' if shared_scope else 'environment') + '-recovery-sigkill-original-opened',
                         actor_pid=recovery_actor, stop_wait_status=recovery_status, stop_signal=os.WSTOPSIG(recovery_status),
                         kill_wait_status=recovery_killed, kill_signal=os.WTERMSIG(recovery_killed),
                         immutable_owned_original_open_state=recovery_report['owned_original_open_state'],
                         exact_status_command=expected_command, original_oid=original_oid,
                         preserved_rows=original_rows, preserved_files=original_objects,
                         repeated_recovery_cli_exit=retry_exit, repeated_recovery_cli_output=retry_output.getvalue(),
                         repeated_recovery='preserved without writer stop or file helper')
            if sql('postgres', 'SELECT count(*) FROM pg_stat_activity WHERE pid IN (' + held_pids + ');') != '0':
                raise RuntimeError('Held writer backend survived recovery draining')
            held_proofs = []
            for role, pid, held in holders:
                try:
                    held.execute(write_query)
                except SessionError:
                    held.close(check=False)
                    diagnostic = held.private_diagnostic.decode('utf-8', errors='replace')
                    if 'warning' in diagnostic.lower() or 'terminating connection due to administrator command' not in diagnostic:
                        raise RuntimeError('Held writer refusal did not retain the native termination diagnostic')
                    held_proofs.append({'role': role, 'backend_pid': pid, 'diagnostic': diagnostic, 'native_backend_count': 0})
                else:
                    raise RuntimeError('Drained held writer executed a new write')
            if boundary == 'opened':
                if rows(target_database) != preserved_rows or (not shared_scope and object_bytes() != 'post-open-write') \
                        or backup.database_oid(target_database) != journal['stage_oid']:
                    raise RuntimeError('Recovery destroyed observed post-open writes')
            elif recovered.get('status') != 'rolled-back' or rows(target_database) != original_rows \
                    or (not shared_scope and object_bytes() != original_objects) or backup.database_oid(target_database) != original_oid:
                raise RuntimeError('SIGKILL recovery did not restore exact original database and objects')
            if sql('fixture_neighbor', 'SELECT value FROM sentinel;') != 'neighbor-intact':
                raise RuntimeError('Neighbor database changed after recovery')
            if rows(counterpart) != counterpart_rows:
                raise RuntimeError('Published neighboring database changed during restore')
            for role in (*writer_roles, super_role):
                native(['docker', 'exec', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                        '-U', role, '-d', target_database, '-c', write_query])
            add_case('native-' + ('storage' if shared_scope else 'environment') + '-sigkill-' + boundary, actor_pid=actor, signal='SIGKILL',
                     stop_wait_status=status, stop_signal=os.WSTOPSIG(status), kill_wait_status=killed, kill_signal=os.WTERMSIG(killed),
                     immutable_owned_cutover_state=actor_report['owned_cutover_state'], held_writer_refusals=held_proofs,
                     exact_status_command=expected_command, native_storage_state=storage_state,
                     native_status_cli_exit=status_exit, native_status_cli_output=status_output.getvalue(),
                     native_recovery_cli_exit=recovery_exit, native_recovery_cli_output=recovery_output.getvalue(),
                     status_immutable_state_sha256={key: hashlib.sha256(value).hexdigest() for key, value in status_before.items()},
                     saved_phase=journal['phase'], native_database_state=current_state, writer_refusals=writer_proofs,
                     recovery=recovered, neighbor='unchanged and writable')
        expected_cases = {'native-environment-sigkill-' + boundary for boundary in environment_boundaries} \
            | {'native-storage-sigkill-' + boundary for boundary in storage_boundaries}
        observed_cases = {case['case'] for case in report['cases']
                          if '-sigkill-' in case['case'] and '-recovery-sigkill-' not in case['case'] and case['passed']}
        if observed_cases != expected_cases:
            raise RuntimeError('Full environment/shared interruption matrix is incomplete')
        if {case['case'] for case in report['cases'] if '-recovery-sigkill-' in case['case'] and case['passed']} != {
                'native-environment-recovery-sigkill-original-opened', 'native-storage-recovery-sigkill-original-opened'}:
            raise RuntimeError('Full original reopening recovery matrix is incomplete')
        report['full_matrix_complete'] = True
        report['passed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        report['owned_writer_diagnostics'] = []
        for held in active_holders:
            held.close(check=False)
            if held.private_diagnostic:
                report['owned_writer_diagnostics'].append({'args': held.process.args,
                    'diagnostic': held.private_diagnostic[:4096].decode('utf-8', errors='replace')})
        # This fixture owns every value in its temporary workspace. Preserve
        # bounded synthetic diagnostics before cleanup, never operator state.
        report['exception_chain'] = []
        diagnostic = error
        while diagnostic is not None and len(report['exception_chain']) < 8:
            report['exception_chain'].append(type(diagnostic).__name__ + ': ' + str(diagnostic)[:4096])
            diagnostic = diagnostic.__context__
        report['owned_operation_journals'] = {}
        for journal in backup.STATE.glob('restore-operation-*.json'):
            if journal.is_file() and not journal.is_symlink() and journal.stat().st_size <= 65536:
                report['owned_operation_journals'][journal.name] = json.loads(journal.read_text())
        report['owned_completion_checkpoints'] = {}
        for checkpoint in backup.STATE.glob('restore-completions/*.json'):
            if checkpoint.is_file() and not checkpoint.is_symlink() and checkpoint.stat().st_size <= 65536:
                report['owned_completion_checkpoints'][checkpoint.name] = json.loads(checkpoint.read_text())
    finally:
        restore_cutover.HeldSession = original_session
        for held in active_holders:
            if not held.closed:
                held.close(check=False)
        if storage_cid:
            try:
                native(['docker', 'rm', '-f', storage_cid])
            except Exception as error:
                report['passed'] = False
                report['cleanup_error'] = str(error)
        if db_id:
            try:
                record = image_identity.record(native(['docker', 'inspect', db_id]).stdout)
                if record.get('Name') != '/' + db or record.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner:
                    raise RuntimeError('Cleanup ownership mismatch')
                native(['docker', 'rm', '-f', db_id])
            except Exception as error:
                report['passed'] = False
                report['cleanup_error'] = str(error)
        for kind, name, created in (('network', network, network_created), ('volume', volume, volume_created)):
            if created:
                try:
                    native(['docker', kind, 'rm', name])
                except Exception as error:
                    report['passed'] = False
                    report['cleanup_error'] = str(error)
        temporary.cleanup()
        report['helper_count'] = helper_sequence
        Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('scope', 'source_sha256', 'cases', 'passed')}, indent=2))
    if 'error' in report:
        print(report['error'], file=sys.stderr)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
