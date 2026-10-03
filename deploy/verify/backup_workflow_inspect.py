"""Native backup v1 data workflows on an owned synthetic PostgreSQL installation."""
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import backup
import image_identity
from run_checks import source_digest


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    db, network, volume = [fixture + '-workflow-' + suffix for suffix in ('db', 'net', 'objects')]
    owner = 'sbarbase-fixture-' + fixture
    environment = 'e_' + 'a' * 24
    report = {'scope': 'native-backup-v1-workflow-and-resumable-readiness', 'source_sha256': source_digest(),
              'commands': [], 'attempted_commands': [], 'service_doubles': [], 'cases': [], 'passed': False,
              'limitations': ['Synthetic schemas and bytes, not actual Supabase Auth, REST or Storage services.',
                              'Auth/REST/Storage lifecycle, endpoint publication and health use explicit doubles.',
                              'Startup/publication/health failures are constrained doubles; actual Supabase application readiness is not proved.',
                              'Existing data-phase shared Storage/direct writers and crash recovery are not fenced or certified.',
                              'Native image/owner admission, PostgreSQL snapshot/counts/dump/restore/SQL and Storage tar are real.',
                              'Failure injection replaces one native command input after verified archive admission.',
                              'No cloud import, complete installation recovery, PITR, off-host restore, classic store or release proof.',
                              'Fresh bounded tmpfs database and owned volume/internal network; no ports or private host data.']}
    original_run, original_popen = subprocess.run, subprocess.Popen
    db_id = None
    network_created = volume_created = False
    helper_sequence = 0
    fail_restore = fail_tar = False
    concurrent_write = False
    lifecycle_failure = None
    temporary = tempfile.TemporaryDirectory(prefix='sbarbase-backup-workflow-')
    workspace = Path(temporary.name)
    backup.STATE, backup.BACKUPS = workspace / 'upstream', workspace / 'backups'
    backup.PREFIX, backup.DB, backup.DATABASE_OWNER, backup.OBJECTS_VOLUME = fixture + '-workflow', db, owner, volume
    backup.STORAGE_CONTAINER = fixture + '-workflow-storage'
    backup.STATE.mkdir(mode=0o700)
    (backup.STATE / 'endpoints.json').write_text(json.dumps({environment: {'auth': 'double://auth', 'rest': 'double://rest'}}))

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
            if argv != ['docker', 'container', 'inspect', db]:
                raise RuntimeError('Unexpected container inspection')
        elif argv[:3] == ['docker', 'volume', 'inspect']:
            if argv != ['docker', 'volume', 'inspect', volume]:
                raise RuntimeError('Unexpected volume inspection')
        elif argv[:2] == ['docker', 'stop']:
            if argv[2:] not in (backup.service_names(environment), [backup.STORAGE_CONTAINER]):
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
            if helper_sequence >= 64:
                raise RuntimeError('Fixture helper maximum of 64 reached before creation')
            helper_sequence += 1
            argv = argv[:2] + ['--name', fixture + '-workflow-helper-' + str(helper_sequence), '--cap-drop', 'ALL',
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
        return sql('postgres', "SELECT datconnlimit, datallowconn, coalesce(datacl::text,'') FROM pg_database WHERE datname='" + database + "';")

    def rows(database):
        if database == backup.STORAGE_DATABASE:
            return sql(database, 'SELECT id,payload FROM tenants ORDER BY id;')
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
                        '-c shared_buffers=32MB -c max_connections=20']).stdout.strip()
        deadline = time.monotonic() + 30
        while True:
            ready = native(['docker', 'exec', db_id, 'pg_isready', '-h', '127.0.0.1', '-U', 'supabase_admin'], check=False)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Fixture PostgreSQL readiness deadline')
            time.sleep(.2)
        for database in (environment, backup.STORAGE_DATABASE):
            sql('postgres', 'CREATE DATABASE ' + database + '; REVOKE ALL ON DATABASE ' + database + ' FROM PUBLIC; '
                'ALTER DATABASE ' + database + ' CONNECTION LIMIT 7;')
        sql(environment, "CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE TABLE auth.users(id integer PRIMARY KEY,payload text); "
            "CREATE TABLE auth.identities(id integer PRIMARY KEY); CREATE TABLE storage.buckets(id text PRIMARY KEY); "
            "CREATE TABLE storage.objects(id integer PRIMARY KEY); INSERT INTO auth.users VALUES (1,'at-backup'); "
            "INSERT INTO auth.identities VALUES (1); INSERT INTO storage.buckets VALUES ('bucket'); INSERT INTO storage.objects VALUES (1);")
        sql(backup.STORAGE_DATABASE, "CREATE TABLE tenants(id text PRIMARY KEY,payload text); INSERT INTO tenants VALUES ('" + environment + "','synthetic-key-marker');")
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
        sql(backup.STORAGE_DATABASE, "UPDATE tenants SET payload='after-backup';")
        storage_record = backup.restore_storage(storage_path.name, now=clock + datetime.timedelta(minutes=2))
        if rows(backup.STORAGE_DATABASE) != environment + '|synthetic-key-marker' or props(backup.STORAGE_DATABASE) != before_storage_props:
            raise RuntimeError('Storage native restore differs')
        add_case('native-storage-metadata-create-restore', record=storage_record,
                 expected_rows=environment + '|synthetic-key-marker', actual_rows=rows(backup.STORAGE_DATABASE),
                 expected_database_properties=before_storage_props, actual_database_properties=props(backup.STORAGE_DATABASE))
        sql(environment, "UPDATE auth.users SET payload='live-before-failure'; INSERT INTO auth.identities VALUES (2); "
            "INSERT INTO storage.buckets VALUES ('live-before-failure'); INSERT INTO storage.objects VALUES (2);")
        backup.helper('printf live-before-failure > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt', writable=True)
        live_rows, live_props, live_files = rows(environment), props(environment), object_bytes()
        when = clock + datetime.timedelta(minutes=3)
        fail_restore = True
        try:
            backup.restore(environment, path.name, now=when)
        except backup.BackupError:
            if fail_restore or rows(environment) != live_rows or props(environment) != live_props or object_bytes() != live_files:
                raise RuntimeError('Native pg_restore failure did not roll back')
            assert_no_record(path, when)
        else:
            raise RuntimeError('Injected native pg_restore failure accepted')
        add_case('native-pg-restore-failure-after-rename-rolls-back', expected_tables=live_rows, actual_tables=rows(environment),
                 expected_file=live_files, actual_file=object_bytes(), expected_properties=live_props, actual_properties=props(environment))
        when = clock + datetime.timedelta(minutes=4)
        fail_tar = True
        try:
            backup.restore(environment, path.name, now=when)
        except backup.BackupError:
            if fail_tar or rows(environment) != live_rows or props(environment) != live_props or object_bytes() != live_files:
                raise RuntimeError('Native tar failure did not roll back database and files')
            assert_no_record(path, when)
        else:
            raise RuntimeError('Injected native tar failure accepted')
        add_case('native-tar-failure-after-files-move-rolls-back', expected_tables=live_rows, actual_tables=rows(environment),
                 expected_file=live_files, actual_file=object_bytes(), expected_properties=live_props, actual_properties=props(environment))
        for index, (scope, failure) in enumerate(((environment, 'environment-startup'), (environment, 'environment-health'),
                                                   (backup.STORAGE, 'storage-startup'), (backup.STORAGE, 'storage-health')), start=5):
            when = clock + datetime.timedelta(minutes=index)
            stamp = when.strftime('%Y%m%dt%H%M%Sz')
            archive = path if scope == environment else storage_path
            lifecycle_failure = failure
            try:
                if scope == environment:
                    backup.restore(environment, archive.name, now=when)
                else:
                    backup.restore_storage(archive.name, now=when)
            except backup.BackupError as error:
                if 'complete-restore' not in str(error) or 'private diagnostic' in str(error):
                    raise RuntimeError('Completion failure lacks safe sanitized instructions')
            else:
                raise RuntimeError('Injected readiness failure accepted')
            checkpoint = json.loads(backup.completion_path(scope).read_text())
            if checkpoint['status'] != 'readiness-pending' or checkpoint['container_id'] != db_id:
                raise RuntimeError('Readiness failure lacks bound pending checkpoint')
            if backup.completion_path(scope).stat().st_mode & 0o777 != 0o600:
                raise RuntimeError('Completion checkpoint is not private')
            assert_no_record(archive, when)
            database = environment if scope == environment else backup.STORAGE_DATABASE
            if checkpoint['database_oid'] != sql('postgres', "SELECT oid FROM pg_database WHERE datname='" + database + "';"):
                raise RuntimeError('Checkpoint does not bind actual restored OID')
            if scope == environment:
                sql(environment, "INSERT INTO auth.users VALUES (99,'post-cutover-" + failure + "');")
            else:
                sql(backup.STORAGE_DATABASE, "INSERT INTO tenants VALUES ('post-cutover-" + failure + "','new-write');")
            backup.helper('printf post-cutover-' + failure + ' > /data/' + backup.TENANT_PARENT + '/' + environment + '/bucket/kept.txt', writable=True)
            expected_rows, expected_files = rows(database), object_bytes()
            guard_results = []
            for operation, args in ((backup.restore, (environment, path.name)), (backup.restore_storage, (storage_path.name,)),
                                    (backup.discard_previous, (environment,)), (backup.discard_previous_storage, ()),
                                    (backup.prune, (scope, 1)), (backup.prune, ('installation', 1))):
                begin = len(report['attempted_commands'])
                try:
                    operation(*args)
                except backup.BackupError:
                    if len(report['attempted_commands']) != begin:
                        raise RuntimeError('Pending guard touched Docker')
                    guard_results.append(operation.__name__)
                else:
                    raise RuntimeError('Pending recovery identity was not protected')
            lifecycle_failure = None
            begin = len(report['commands'])
            restored_record = backup.complete_restore(scope, archive.name, stamp)
            completion_commands = report['commands'][begin:]
            if any('pg_restore' in command['args'] or 'pg_dump' in command['args'] or command['args'][:2] in
                   (['docker', 'run'], ['docker', 'stop']) for command in completion_commands):
                raise RuntimeError('Completion replays destructive data operations')
            actual_rows, actual_files = rows(database), object_bytes()
            if actual_rows != expected_rows or actual_files != expected_files:
                raise RuntimeError('Readiness completion erased post-cutover writes')
            lifecycle_count = len(report['service_doubles'])
            backup.complete_restore(scope, archive.name, stamp)
            if len(report['service_doubles']) != lifecycle_count:
                raise RuntimeError('Repeated completed retry restarted services')
            add_case('native-' + failure + '-pending-safe-completion', expected_rows=expected_rows, actual_rows=actual_rows,
                     expected_file=expected_files, actual_file=actual_files, pending_checkpoint=checkpoint,
                     completed_checkpoint=json.loads(backup.completion_path(scope).read_text()), record=restored_record,
                     completion_commands=completion_commands, pending_guards=guard_results)
        shutil.copytree(backup.STATE / 'restore-completions', '/evidence/restore-completions')
        shutil.copytree(backup.BACKUPS, '/evidence/backups')
        report['passed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
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
