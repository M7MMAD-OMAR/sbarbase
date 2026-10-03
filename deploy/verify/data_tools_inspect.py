"""Owned native PostgreSQL and Storage tool roundtrip using synthetic data."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import backup
import import_project
import image_identity
from run_checks import source_digest


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):raise ValueError('Invalid fixture identity')
    db, tool, network, volume = [fixture + '-data-' + suffix for suffix in ('db', 'source', 'net', 'objects')]
    owner = 'sbarbase-fixture-' + fixture
    report = {'scope': 'owned-native-data-tools-roundtrip', 'source_sha256': source_digest(),
              'commands': [], 'attempted_tool_commands': [], 'cases': [], 'passed': False,
              'limitations': ['Synthetic PostgreSQL tables and one Storage object only.',
                              'No actual project/cloud import, complete backup restore, services, PITR or release acceptance.',
                              'Fixture database/tmpfs/volume/network only; no host ports or private state.',
                              'Containerd host only; no independent host, classic store or IO enforcement proof.']}
    original_run = subprocess.run
    containers = set()
    created_network = created_volume = False
    source = None
    helper_sequence = 0

    def native(argv, *, stdin=None, input=None, stdout=subprocess.PIPE, text=True, timeout=60, check=True):
        result = original_run(argv, stdin=stdin, input=input, stdout=stdout, stderr=subprocess.PIPE,
                              text=text, timeout=timeout)
        error = result.stderr if text else result.stderr.decode(errors='replace')
        output = result.stdout if isinstance(result.stdout, str) else None
        entry = {'args': argv, 'exit_code': result.returncode, 'stdout': output, 'stderr': error}
        if isinstance(result.stdout, bytes):
            entry.update(stdout_bytes=len(result.stdout), stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        report['commands'].append(entry)
        if 'warning' in error.lower():raise RuntimeError('Native warning: ' + error.strip())
        if check and result.returncode:raise RuntimeError('Native command failed: ' + error.strip())
        return result

    def transport(*args, **kwargs):
        report['attempted_tool_commands'].append(list(args))
        if args[:2] == ('image', 'inspect'):
            if len(args) != 3 or args[2] not in references:raise RuntimeError('Unexpected image inspection')
        elif args[:2] == ('network', 'inspect'):
            if args[2] != network:raise RuntimeError('Unexpected network inspection')
        elif args[0] == 'inspect':
            if args[-1] not in containers | {db, tool}:raise RuntimeError('Unexpected container inspection')
        elif args[0] == 'run':
            if args[args.index('--name') + 1] != tool or '--pull=never' not in args:
                raise RuntimeError('Unexpected import tool creation')
        elif args[:2] == ('rm', '-f'):
            if args[2] not in containers:raise RuntimeError('Unexpected import tool removal')
        else:raise RuntimeError('Unexpected import Docker operation')
        result = native(['docker', *args], check=kwargs.get('check', True))
        if args[0] == 'run' and not result.returncode:containers.add(result.stdout.strip())
        return result

    def backup_run(argv, *, stdin=None, stdout=subprocess.PIPE, check=True, text=True, timeout=3600):
        nonlocal helper_sequence
        report['attempted_tool_commands'].append(argv)
        if argv[:3] == ['docker', 'image', 'inspect']:
            if argv[3] not in references:raise RuntimeError('Unexpected backup image inspection')
        elif argv[:3] == ['docker', 'volume', 'inspect']:
            if argv[3] not in (volume, volume + '-missing'):raise RuntimeError('Unexpected backup volume inspection')
        elif argv[:2] == ['docker', 'inspect']:
            if argv[2] != db:raise RuntimeError('Unexpected backup DB inspection')
        elif argv[:3] == ['docker', 'container', 'inspect']:
            if argv[3] != db:raise RuntimeError('Unexpected backup DB inspection')
        elif argv[:2] == ['docker', 'exec']:
            if not any(item in containers for item in argv[2:5]):raise RuntimeError('Unexpected database exec identity')
        elif argv[:2] == ['docker', 'run']:
            if '--pull=never' not in argv or storage_reference not in argv:
                raise RuntimeError('Unexpected Storage helper image')
            if f'{volume}:/data' not in argv and f'{volume}:/data:ro' not in argv:
                raise RuntimeError('Unexpected Storage helper volume')
            helper_sequence += 1
            argv = argv[:2] + ['--name', fixture + '-data-helper-' + str(helper_sequence),
                              '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128'] + argv[2:]
        else:raise RuntimeError('Unexpected backup operation')
        return native(argv, stdin=stdin, stdout=stdout, text=text, timeout=min(timeout, 60), check=check)

    def source_run(argv, **kwargs):
        report['attempted_tool_commands'].append(argv)
        if argv[:3] != ['docker', 'exec', '-i'] or source.container_id not in argv:
            raise RuntimeError('Unexpected source exec identity')
        return native(argv, timeout=min(kwargs.get('timeout', 60), 60), check=False)

    def sql(database, statement):
        return native(['docker', 'exec', db_id, 'psql', '-X', '-A', '-t', '-q', '-v', 'ON_ERROR_STOP=1',
                       '-U', 'supabase_admin', '-d', database, '-c', statement]).stdout.strip()

    references = set()
    try:
        db_reference = image_identity.reference(json.loads((ROOT / 'lab/distro-image.lock.json').read_text()))
        storage_reference = image_identity.reference(json.loads((ROOT / 'lab/storage-image.lock.json').read_text()))
        references.update((db_reference, storage_reference))
        for reference in references:
            image_identity.resolved_id(reference, image_identity.record(native(['docker', 'image', 'inspect', reference]).stdout))
        native(['docker', 'network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network])
        created_network = True
        native(['docker', 'volume', 'create', '--label', 'io.sbarbase.owner=' + owner, volume])
        created_volume = True
        db_id = native(['docker', 'run', '-d', '--pull=never', '--name', db, '--label', 'io.sbarbase.owner=' + owner,
                        '--network', network, '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5',
                        '--pids-limit', '128', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                        '--env', 'PGHOST=127.0.0.1',
                        '--user', 'postgres', '--tmpfs', '/tmp:rw,mode=1777,size=256m', '--entrypoint', 'sh', db_reference,
                        '-c', 'initdb -D /tmp/fixture-pg -U supabase_admin --auth=trust --no-locale >/tmp/initdb.log && '
                        'exec postgres -D /tmp/fixture-pg -c listen_addresses=\'*\' -c unix_socket_directories=/tmp '
                        '-c shared_buffers=32MB -c max_connections=20']).stdout.strip()
        containers.add(db_id)
        deadline = time.monotonic() + 30
        while True:
            ready = native(['docker', 'exec', db_id, 'pg_isready', '-h', '127.0.0.1', '-U', 'supabase_admin'], check=False)
            if ready.returncode == 0:break
            if time.monotonic() >= deadline:raise RuntimeError('Fixture PostgreSQL readiness deadline')
            time.sleep(.2)
        for database in ('fixture', 'import_restored'):
            sql('postgres', 'CREATE DATABASE ' + database)
        sql('fixture', "CREATE TABLE public.fixture_items(id integer PRIMARY KEY, payload text NOT NULL); "
                      "INSERT INTO public.fixture_items VALUES (1,'first'),(2,'second');")
        backup.DB, backup.DATABASE_OWNER, backup.OBJECTS_VOLUME = db, owner, volume
        backup.run = backup_run
        import_project.TOOL = tool
        import_project.runtime.OWNER, import_project.runtime.EGRESS = owner, network
        import_project.lab.docker = transport
        import_project.subprocess = SimpleNamespace(run=source_run)
        source = import_project.Source('postgresql://supabase_admin@' + db + ':5432/fixture')
        source_item = json.loads(native(['docker', 'inspect', source.container_id]).stdout)[0]
        source_address = str(ipaddress.IPv4Address(source_item['NetworkSettings']['Networks'][network]['IPAddress']))
        native(['docker', 'exec', db_id, 'sh', '-c',
                'printf "\\nhost fixture supabase_admin %s/32 trust\\n" "$1" >> /tmp/fixture-pg/pg_hba.conf',
                'sh', source_address])
        sql('postgres', 'SELECT pg_reload_conf()')
        rows = source.query('SELECT id,payload FROM public.fixture_items ORDER BY id')
        if rows != [['1', 'first'], ['2', 'second']]:raise RuntimeError('Actual Source query differs')
        try:source.query("INSERT INTO public.fixture_items VALUES (3,'forbidden')")
        except import_project.ImportError_ as error:
            if 'read-only' not in str(error):raise RuntimeError('Unexpected source write refusal')
        else:raise RuntimeError('Source writes were not refused')
        dump = import_project.schema_script(source.dump('--schema-only', '--schema=public'))
        dump += source.dump('--data-only', '--schema=public')
        Path('/evidence/fixture-source.sql').write_text(dump)
        native(['docker', 'exec', '-i', db_id, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '--single-transaction',
                '-U', 'supabase_admin', '-d', 'import_restored'], input=dump)
        if sql('import_restored', 'SELECT id,payload FROM public.fixture_items ORDER BY id') != '1|first\n2|second':
            raise RuntimeError('Actual imported rows differ')
        report['cases'].append({'case': 'source-plain-dump-restore-and-readonly', 'passed': True,
                                'source_container': source.container_id, 'image_reference': source.image_ref,
                                'dump_sha256': hashlib.sha256(dump.encode()).hexdigest(), 'rows': 2})
        admitted = backup.preflight()
        if admitted['db'] != db_id:raise RuntimeError('Backup did not bind actual DB CID')
        with tempfile.TemporaryFile() as archive:
            backup.run(['docker', 'exec', admitted['db'], 'pg_dump', '-U', 'supabase_admin', '-Fc', '-d', 'fixture'],
                       stdout=archive, text=False)
            archive.seek(0)
            archive_bytes = archive.read()
            Path('/evidence/fixture-database.dump').write_bytes(archive_bytes)
            archive.seek(0)
            sql('postgres', 'ALTER DATABASE fixture RENAME TO fixture_previous')
            native(['docker', 'exec', '-i', db_id, 'pg_restore', '-U', 'supabase_admin', '--create',
                    '--exit-on-error', '-d', 'postgres'], stdin=archive, text=False)
        if sql('fixture', 'SELECT id,payload FROM public.fixture_items ORDER BY id') != '1|first\n2|second':
            raise RuntimeError('Actual restored backup rows differ')
        report['cases'].append({'case': 'native-custom-archive-restoration', 'passed': True, 'database_container': db_id,
                                'rows': 2, 'archive_sha256': hashlib.sha256(archive_bytes).hexdigest()})
        backup.helper("mkdir -p /data/fixture && printf fixture-object > /data/fixture/object.txt", writable=True)
        with tempfile.TemporaryFile() as archive:
            backup.helper('cd /data && tar -cf - fixture', stdout=archive, text=False)
            archive.seek(0)
            object_archive = archive.read()
            Path('/evidence/fixture-objects.tar').write_bytes(object_archive)
            archive.seek(0)
            backup.helper('rm -rf /data/fixture && tar -xf - -C /data', stdin=archive, writable=True, text=False)
        if backup.helper('cat /data/fixture/object.txt').stdout != 'fixture-object':
            raise RuntimeError('Actual Storage object restore differs')
        report['cases'].append({'case': 'native-storage-tar-roundtrip', 'passed': True, 'image_reference': storage_reference,
                                'archive_sha256': hashlib.sha256(object_archive).hexdigest()})
        backup.OBJECTS_VOLUME = volume + '-missing'
        before = len(report['attempted_tool_commands'])
        try:backup.helper('printf forbidden', writable=True)
        except backup.BackupError as error:
            if any(c[:2] == ['docker', 'run'] for c in report['attempted_tool_commands'][before:]):
                raise RuntimeError('Missing objects volume attempted helper creation')
            report['cases'].append({'case': 'native-missing-volume-refuses-helper', 'passed': True, 'detail': str(error)})
        else:raise RuntimeError('Missing objects volume accepted')
        backup.OBJECTS_VOLUME = volume
        source.close()
        source = None
        report['passed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        if containers:
            try:report['fixture_database_logs'] = native(['docker', 'logs', db_id], check=False).stdout
            except Exception:pass
    finally:
        for cid in containers:
            try:
                actual = native(['docker', 'inspect', cid], check=False)
                if actual.returncode:
                    if actual.stdout.strip() != '[]' or 'no such' not in actual.stderr.lower():
                        raise RuntimeError('Cleanup inspection unavailable')
                    continue
                record = json.loads(actual.stdout)[0]
                if record.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner:
                    raise RuntimeError('Cleanup ownership mismatch')
                native(['docker', 'rm', '-f', cid])
            except Exception as error:report['passed'] = False;report['cleanup_error'] = str(error)
        for kind, name, created in (('network', network, created_network), ('volume', volume, created_volume)):
            if created:
                try:native(['docker', kind, 'rm', name])
                except Exception as error:report['passed'] = False;report['cleanup_error'] = str(error)
        Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('scope', 'source_sha256', 'cases', 'passed')}, indent=2))
    if 'error' in report:print(report['error'], file=sys.stderr)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
