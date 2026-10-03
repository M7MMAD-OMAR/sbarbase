"""Native archive metadata and retained PostgreSQL session prerequisite proof."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import image_identity
from restore_session import HeldSession
from restore_sql import plan_archive
from run_checks import source_digest


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    db, network = fixture + '-session-db', fixture + '-session-net'
    owner = 'sbarbase-fixture-' + fixture
    report = {'scope': 'native-retained-session-archive-prerequisite',
              'source_sha256': source_digest(), 'commands': [], 'cases': [], 'passed': False,
              'limitations': ['Synthetic archives and core-only owned PostgreSQL server.',
                              'No full restore, object barrier, cutover, crash recovery or worker admission proof.',
                              'No Supabase API, G2, independent host, HA/PITR or release acceptance.']}
    db_id = None
    network_created = False
    current_step = 'native admission'
    held = None

    def native(args, *, data=None, binary=False, check=True):
        result = subprocess.run(args, input=data, capture_output=True, text=not binary, timeout=60)
        stderr = result.stderr.decode(errors='replace') if binary else result.stderr
        entry = {'args': args, 'exit_code': result.returncode, 'stderr': stderr}
        if binary:
            entry.update(stdout_bytes=len(result.stdout), stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        else:
            entry['stdout'] = result.stdout
        report['commands'].append(entry)
        if 'warning' in stderr.lower() or check and result.returncode:
            raise RuntimeError('Native prerequisite command refused: ' + stderr.strip())
        return result

    def argv(database, user='supabase_admin'):
        return ['docker', 'exec', '-i', db_id, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                '-U', user, '-d', database]

    def sql(statement, database='postgres'):
        return native(argv(database), data=statement).stdout.strip()

    def properties(database):
        value = sql("SELECT (to_jsonb(d) - ARRAY['oid','datname','datallowconn','datfrozenxid','datminmxid']) "
                    "|| jsonb_build_object('owner',pg_get_userbyid(datdba),'comment',shobj_description(oid,'pg_database'),"
                    "'settings',coalesce((SELECT jsonb_agg(jsonb_build_object('role',pg_get_userbyid(setrole),"
                    "'config',setconfig) ORDER BY setrole) FROM pg_db_role_setting s WHERE s.setdatabase=d.oid),"
                    "'[]'::jsonb)) FROM pg_database d WHERE datname='" + database + "';")
        value = json.loads(value)
        if isinstance(value['datacl'], list):
            value['datacl'].sort()
        return value

    contents = "SELECT jsonb_build_object('rows',(SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM public.payload t)," \
               "'function',public.echo(),'owner',(SELECT pg_get_userbyid(relowner) FROM pg_class " \
               "WHERE oid='public.payload'::regclass),'acl',(SELECT relacl FROM pg_class WHERE oid='public.payload'::regclass));"
    try:
        pin = json.loads((ROOT / 'lab/distro-image.lock.json').read_text())
        reference = image_identity.reference(pin)
        expected_id = image_identity.resolved_id(reference, image_identity.record(native(['docker', 'image', 'inspect', reference]).stdout))
        native(['docker', 'network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network])
        network_created = True
        db_id = native(['docker', 'run', '-d', '--pull=never', '--name', db, '--label', 'io.sbarbase.owner=' + owner,
                        '--network', network, '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5',
                        '--pids-limit', '128', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                        '--user', 'postgres', '--env', 'PGHOST=127.0.0.1', '--tmpfs', '/tmp:rw,mode=1777,size=256m',
                        '--entrypoint', 'sh', reference, '-c',
                        'initdb -D /tmp/fixture-pg -U supabase_admin --auth=trust --no-locale --encoding=UTF8 >/tmp/initdb.log && '
                        "exec postgres -D /tmp/fixture-pg -c listen_addresses='*' -c unix_socket_directories=/tmp "
                        '-c shared_buffers=32MB -c max_connections=20']).stdout.strip()
        record = image_identity.record(native(['docker', 'inspect', db_id]).stdout)
        if record['Image'] != expected_id or record['Name'] != '/' + db or record['Config']['Labels']['io.sbarbase.owner'] != owner:
            raise RuntimeError('Owned PostgreSQL native identity differs')
        deadline = time.monotonic() + 30
        while native(['docker', 'exec', db_id, 'pg_isready', '-U', 'supabase_admin'], check=False).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError('PostgreSQL readiness deadline')
            time.sleep(.2)
        sql('CREATE ROLE "archive owner" LOGIN; CREATE ROLE writer LOGIN; CREATE ROLE "quoted ""role" LOGIN;')
        variants = (
            ('libc-unlimited-granted-acl', -1, '', 'granted', 'c'),
            ('libc-zero-null-acl', 0, '', 'null', 'c'),
            ('libc-empty-acl', 2, '', 'empty', 'c'),
            ('icu-custom-rules', 3, " LOCALE_PROVIDER=icu ICU_LOCALE='und' ICU_RULES='&a < b' ENCODING='UTF8'", 'granted', 'i'),
            ('builtin-utf8', 4, " LOCALE_PROVIDER=builtin BUILTIN_LOCALE='C.UTF-8' ENCODING='UTF8'", 'null', 'b'),
        )
        for number, (variant, limit, locale_options, acl, provider) in enumerate(variants):
            original = 'e_' + 'abcde'[number] * 24
            stage = 'fixture_stage_' + str(number)
            sql('CREATE DATABASE ' + original + ' TEMPLATE template0 OWNER "archive owner" CONNECTION LIMIT '
                + str(limit) + locale_options + ';')
            sql("COMMENT ON DATABASE " + original + " IS 'quote '' and semi; CONNECTION LIMIT'; "
                "ALTER DATABASE " + original + " SET application_name TO 'CONNECTION LIMIT'; "
                "ALTER ROLE \"quoted \"\"role\" IN DATABASE " + original + " SET search_path TO public, pg_catalog;")
            if acl == 'granted':
                sql('REVOKE ALL ON DATABASE ' + original + ' FROM PUBLIC; GRANT CONNECT ON DATABASE ' + original + ' TO writer WITH GRANT OPTION;')
            elif acl == 'empty':
                sql('REVOKE ALL ON DATABASE ' + original + ' FROM PUBLIC; REVOKE ALL ON DATABASE '
                    + original + ' FROM "archive owner";')
            sql("CREATE TABLE public.payload(id int PRIMARY KEY,body text); "
                "INSERT INTO public.payload VALUES (1,'ALTER DATABASE " + original + " CONNECTION LIMIT 99;'),"
                "(2,E'\\\\connect wrong\\nCOPY terminator'); "
                "CREATE FUNCTION public.echo() RETURNS text LANGUAGE SQL AS $$SELECT 'ALTER DATABASE " + original + " CONNECTION LIMIT 42;'$$; "
                'ALTER TABLE public.payload OWNER TO "archive owner"; GRANT SELECT ON public.payload TO writer WITH GRANT OPTION;', original)
            expected_properties = properties(original)
            if expected_properties['comment'] != "quote ' and semi; CONNECTION LIMIT":
                raise RuntimeError('Fixture database comment is missing')
            if expected_properties['datlocprovider'] != provider \
                    or acl == 'empty' and expected_properties['datacl'] != [] \
                    or acl == 'null' and expected_properties['datacl'] is not None \
                    or provider == 'i' and expected_properties['daticurules'] != '&a < b':
                raise RuntimeError('Fixture metadata variant was not established')
            expected_contents = json.loads(sql(contents, original))
            dump = native(['docker', 'exec', db_id, 'pg_dump', '-U', 'supabase_admin', '-Fc', '-d', original], binary=True).stdout
            generated = native(['docker', 'exec', '-i', db_id, 'pg_restore', '--create', '--file=-'], data=dump, binary=True).stdout
            Path('/evidence/archive-' + str(number) + '.dump').write_bytes(dump)
            Path('/evidence/generated-' + str(number) + '.sql').write_bytes(generated)
            body = io.StringIO()
            plan = plan_archive(io.StringIO(generated.decode('utf-8')), body, original, stage)
            native_body = body.getvalue().encode('utf-8')
            Path('/evidence/replay-' + str(number) + '.sql').write_bytes(native_body)
            allocated = sql("SELECT pg_nextoid('pg_catalog.pg_database'::regclass, 'oid'::name, "
                            "'pg_catalog.pg_database_oid_index'::regclass);")
            if not allocated.isdigit() or not 16384 <= int(allocated) <= 4294967295:
                raise RuntimeError('Native database OID allocator refused')
            expected_identity = Path('/evidence/stage-identity-' + str(number) + '.json')
            with expected_identity.open('x') as stream:
                os.chmod(expected_identity, 0o600)
                json.dump({'stage': stage, 'oid': int(allocated)}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open('/evidence', os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if not plan.create.rstrip().endswith(';'):
                raise RuntimeError('Native staged CREATE is malformed')
            sql(plan.create.rstrip()[:-1] + ' OID = ' + allocated + ';')
            stage_oid = sql("SELECT oid FROM pg_database WHERE datname='" + stage + "';")
            if stage_oid != allocated:
                raise RuntimeError('Created database OID differs from prebound identity')
            collision_name = 'fixture_collision_' + str(number)
            collision = native(argv('postgres'), data='CREATE DATABASE ' + collision_name
                               + ' TEMPLATE template0 OID = ' + allocated + ';', check=False)
            expected_collision = 'ERROR:  database OID ' + allocated + ' is already in use by database "' + stage + '"\n'
            if collision.returncode == 0 or collision.stderr != expected_collision \
                    or sql("SELECT count(*) FROM pg_database WHERE datname='" + collision_name + "';") != '0' \
                    or sql("SELECT oid FROM pg_database WHERE datname='" + stage + "';") != allocated \
                    or properties(original) != expected_properties:
                raise RuntimeError('Explicit OID collision did not preserve existing databases')
            with HeldSession(argv(stage), timeout=30) as held:
                current_step = 'retained backend identity'
                pid = held.execute('SELECT pg_backend_pid();')
                if held.execute('SELECT oid FROM pg_database WHERE datname=current_database();') != stage_oid:
                    raise RuntimeError('Held stage database identity changed')
                current_step = 'stage connection fence'
                sql('ALTER DATABASE ' + stage + ' ALLOW_CONNECTIONS false;')
                if sql("SELECT datallowconn FROM pg_database WHERE datname='" + stage + "';") != 'f':
                    raise RuntimeError('Stage did not retain connection fence')
                if sql("SELECT oid FROM pg_database WHERE datname='" + stage + "';") != stage_oid:
                    raise RuntimeError('Fenced stage database identity changed')
                refused = []
                for user in ('writer', 'supabase_admin'):
                    attempt = native(argv(stage, user), data='SELECT 1;', check=False)
                    if attempt.returncode == 0 or 'not currently accepting connections' not in attempt.stderr:
                        raise RuntimeError('New stage connection was not fenced')
                    refused.append({'user': user, 'exit_code': attempt.returncode})
                current_step = 'archive SQL replay'
                held.replay(io.BytesIO(native_body))
                current_step = 'archive database metadata'
                held.execute('\n'.join(plan.metadata))
                current_step = 'archive content validation'
                actual_contents = json.loads(held.execute(contents))
                actual_properties = properties(stage)
                if actual_contents != expected_contents or actual_properties != expected_properties:
                    raise RuntimeError('Archived content or database metadata changed')
                if held.execute('SELECT pg_backend_pid();') != pid:
                    raise RuntimeError('Restore reconnected instead of retaining session')
            if sql("SELECT count(*) FROM pg_stat_activity WHERE datname='" + stage + "';") != '0':
                raise RuntimeError('Closed stage session did not drain')
            if sql("SELECT datallowconn FROM pg_database WHERE datname='" + stage + "';") != 'f':
                raise RuntimeError('Stage reopened on session close')
            if sql("SELECT count(*) FROM pg_prepared_xacts WHERE database='" + stage + "';") != '0':
                raise RuntimeError('Stage has prepared transactions')
            report['cases'].append({'case': 'native-held-stage-' + variant, 'passed': True,
                                    'expected_properties': expected_properties, 'actual_properties': actual_properties,
                                    'expected_contents': expected_contents, 'actual_contents': actual_contents,
                                    'retained_backend_pid': pid, 'refused_connections': refused,
                                    'prebound_stage_oid': int(allocated), 'oid_collision_exit': collision.returncode,
                                    'archive_sha256': hashlib.sha256(dump).hexdigest(),
                                    'sql_sha256': hashlib.sha256(generated).hexdigest(),
                                    'replay_sha256': hashlib.sha256(native_body).hexdigest()})
        report['passed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        report['failed_step'] = current_step
        diagnostic = getattr(held, 'private_diagnostic', b'')
        if diagnostic:
            report['synthetic_fixture_diagnostic'] = diagnostic.decode('utf-8', errors='replace')
    finally:
        if db_id:
            try:
                record = image_identity.record(native(['docker', 'inspect', db_id]).stdout)
                if record['Name'] != '/' + db or record['Config']['Labels']['io.sbarbase.owner'] != owner:
                    raise RuntimeError('Cleanup ownership mismatch')
                native(['docker', 'rm', '-f', db_id])
            except Exception as error:
                report['passed'] = False
                report['cleanup_error'] = str(error)
        if network_created:
            try:
                native(['docker', 'network', 'rm', network])
            except Exception as error:
                report['passed'] = False
                report['cleanup_error'] = str(error)
        Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('scope', 'source_sha256', 'passed', 'error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
