"""Observe original database image startup, without substituting initialization."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import native_config_preservation_inspect as preservation
import image_identity
from run_checks import source_digest

ORIGINAL_ARTIFACT_PATHS = (
    '/usr/local/bin/docker-entrypoint.sh', '/docker-entrypoint-initdb.d/migrate.sh',
    '/etc/postgresql/postgresql.conf', '/etc/postgresql-custom/conf.d/pg_net.conf',
    '/etc/postgresql-custom/conf.d/pg_cron.conf', '/etc/postgresql-custom/supautils.conf',
)


def admit_bootstrap_inspection(captured, args):
    """A successful ownership inspection cannot use the server-log waiver."""
    if captured['refusal']:
        raise RuntimeError('Ownership inspection capture refused')
    if captured['exit_code']:
        preservation.admit_absence(captured, args)
        return None
    if captured['stderr'] or not captured['stdout'].endswith(b']\n'):
        raise RuntimeError('Ownership inspection diagnostics or framing differ')

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate inspection JSON key')
            value[key] = item
        return value

    values = json.loads(captured['stdout'].decode('utf-8', errors='strict'), object_pairs_hook=unique_object)
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise RuntimeError('Ownership inspection must contain exactly one object')
    return values[0]


def admit_negative_authentication(result, args, refusal, expected_line, expected_cid, *, fixture, database, network, helper):
    network_name = fixture + '-defaults-net'
    owner = 'sbarbase-fixture-' + fixture
    connections = database['NetworkSettings']['Networks']
    if (set(connections) != {network_name} or database.get('Name') != '/' + fixture + '-defaults-db'
            or network.get('Name') != network_name or not network.get('Internal')
            or network.get('Labels') != {'io.sbarbase.owner': owner}
            or helper.get('Name') != '/' + fixture + '-bootstrap-auth-2'
            or helper.get('Image') != database.get('Image')
            or helper.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner
            or expected_cid != helper.get('Id')):
        raise RuntimeError('Negative authentication identity binding differs')
    address_text = connections[network_name]['IPAddress']
    address = ipaddress.IPv4Address(address_text)
    if (str(address) != address_text or address.is_unspecified or address.is_multicast
            or address.is_loopback or connections[network_name].get('NetworkID') != network.get('Id')):
        raise RuntimeError('Negative authentication IPv4 binding differs')
    line = ('psql: error: connection to server at "' + fixture + '-defaults-db" (' + address_text
            + '), port 5432 failed: FATAL:  password authentication failed for user "supabase_admin"\n')
    if (refusal != 'password authentication failed for user "supabase_admin"' or expected_line != line
            or args != ['docker', 'start', '--attach', expected_cid]
            or not isinstance(expected_cid, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_cid)
            or result.returncode != 2 or result.stdout != '' or result.stderr != line):
        raise RuntimeError('Exact negative authentication client control differs')
    return line


def wait_original_ready(native, db_id):
    """Observe the final owned postmaster before attempting a connection."""
    deadline = time.monotonic() + 45
    while True:
        result = native(['docker', 'exec', db_id, 'cat',
                         '/var/lib/postgresql/data/postmaster.pid'],
                        check=False, server_logs=True)
        if result.returncode:
            missing = {
                'cat: /var/lib/postgresql/data/postmaster.pid: No such file or directory',
                "cat: can't open '/var/lib/postgresql/data/postmaster.pid': No such file or directory"}
            if result.returncode != 1 or result.stdout or result.stderr.strip() not in missing:
                raise RuntimeError('Unexpected postmaster identity observation: ' + result.stderr)
        elif result.stderr:
            raise RuntimeError('Unexpected postmaster identity diagnostic: ' + result.stderr)
        else:
            lines = result.stdout.splitlines()
            if len(lines) >= 8 and lines[0] == '1' and lines[7].strip() == 'ready':
                process = native(['docker', 'exec', db_id, 'cat', '/proc/1/comm']).stdout.strip()
                if process not in ('postgres', '.postgres-wrapp'):
                    raise RuntimeError('Final postmaster process differs')
                return {'pid': 1, 'comm': process, 'pid_file': result.stdout}
        if time.monotonic() >= deadline:
            raise RuntimeError('Original image readiness deadline')
        time.sleep(.5)


def main(behavior=None, *, bootstrap=False, configured_effects=False):
    if configured_effects and not bootstrap:
        raise ValueError('Configured effects requires the original bootstrap profile')
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    output = Path('/evidence')
    output.mkdir(exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('Owned evidence directory must be empty')
    names = {kind: fixture + '-defaults-' + kind for kind in ('db', 'net', 'data', 'config')}
    owner = 'sbarbase-fixture-' + fixture
    report = {'scope': 'original-database-image-defaults-inventory',
              'source_sha256': source_digest(), 'commands': [], 'passed': False,
              'limitations': ['Database image original entrypoint/Cmd only, no upstream Compose init/config mounts.',
                              'Not .136 equivalence, full Supabase initialization or API readiness.',
                              'No worker suspension, maintenance fence, recovery or release acceptance.']}
    if bootstrap:
        report['scope'] = 'original-database-image-configured-bootstrap'
        report['limitations'] = [
            'Configured image bootstrap only, no full upstream Compose services.',
            'Native active HBA rules remain unchanged; local trust is not production hardening.',
            'No key/archive continuity, native restore, interruption or release acceptance.']
        report['bootstrap'] = {}
        names['confdir'] = fixture + '-bootstrap-confdir'
    if configured_effects:
        report['scope'] = 'configured-original-engine-owned-cron-and-http-effects'
        report['configured_effects_profile'] = {'whole_seconds': 780, 'work_seconds': 540,
                                               'cleanup_seconds': 240, 'cleanup_command_limit': 28}
    whole_deadline = time.monotonic() + (780 if configured_effects else 600)
    work_deadline = whole_deadline - (240 if configured_effects else 120)
    cleanup_commands = 0
    cleanup_mode = False
    created = []
    db_id = None
    step = 'image admission'

    def native(args, *, data=None, check=True, server_logs=False, binary=False, refusal=None, cleanup=False, allow_absence=False, refusal_line=None, refusal_cid=None, operation_deadline=None):
        nonlocal cleanup_commands
        if configured_effects and (cleanup or cleanup_mode):
            if cleanup_commands >= 28:
                raise RuntimeError('Declared cleanup operation bound exhausted')
            cleanup_commands += 1
        started_at = time.time()
        started = time.monotonic()
        if bootstrap:
            budget = min(8, whole_deadline - started) if cleanup or cleanup_mode else min(60, work_deadline - started)
            if operation_deadline is not None:
                if not configured_effects or cleanup or cleanup_mode:
                    raise ValueError('Operation deadline belongs only to configured effects work')
                budget = min(budget, operation_deadline - started)
            if budget <= 0:
                raise RuntimeError('Bootstrap overall deadline exceeded')
            payload = data.encode('utf-8') if isinstance(data, str) else data
            captured = preservation.bounded_capture(args, budget, 1024 * 1024, 256 * 1024, data=payload)
            ordinal = len(report['commands']) + 1
            raw_entry = {'args': args, 'started_at_unix_seconds': started_at,
                         'duration_seconds': captured['duration_seconds'],
                         'exit_code': captured['exit_code'], 'stream_refusal': captured['refusal']}
            if configured_effects:
                raw_entry['cleanup_operation'] = bool(cleanup or cleanup_mode)
            for key in ('stdout', 'stderr'):
                artifact = f'command-{ordinal:03d}-{key}.bin'
                (output / artifact).write_bytes(captured[key])
                raw_entry.update({key + '_artifact': artifact, key + '_bytes': len(captured[key]),
                                  key + '_sha256': hashlib.sha256(captured[key]).hexdigest()})
            if payload is not None:
                artifact = f'command-{ordinal:03d}-stdin.bin'
                (output / artifact).write_bytes(payload)
                raw_entry['stdin_artifact'] = artifact
            report['commands'].append(raw_entry)
            if captured['refusal']:
                raise RuntimeError('Bootstrap streaming refusal: ' + captured['refusal'])
            if allow_absence:
                preservation.admit_absence(captured, args)
                raw_entry['recognized_absence'] = True
                return None
            if args[1:3] in (['container', 'inspect'], ['network', 'inspect'], ['volume', 'inspect']):
                admit_bootstrap_inspection(captured, args)
            elif args[1:2] == ['inspect']:
                admit_bootstrap_inspection(captured, ['docker', 'container', 'inspect', *args[2:]])
            result = subprocess.CompletedProcess(args, captured['exit_code'],
                captured['stdout'] if binary else captured['stdout'].decode('utf-8', errors='strict'),
                captured['stderr'] if binary else captured['stderr'].decode('utf-8', errors='strict'))
        else:
            result = subprocess.run(args, input=data, capture_output=True, text=not binary, timeout=60)
        stderr = result.stderr.decode(errors='replace') if binary else result.stderr
        entry = {'args': args, 'exit_code': result.returncode, 'stderr': stderr,
                 'started_at_unix_seconds': started_at,
                 'duration_seconds': time.monotonic() - started}
        if isinstance(data, str):
            entry['stdin'] = data
        if binary:
            entry.update(stdout_bytes=len(result.stdout), stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        else:
            entry['stdout'] = result.stdout
        if bootstrap:
            raw_entry.update(entry)
            entry = raw_entry
        else:
            report['commands'].append(entry)
        if refusal is not None and bootstrap:
            controls = report['bootstrap']
            admit_negative_authentication(result, args, refusal, refusal_line, refusal_cid,
                fixture=fixture, database=report['container'], network=controls['network'],
                helper=controls['auth_helpers'][1])
            entry['expected_refusal'] = refusal
            entry['expected_refusal_line'] = refusal_line
            return result
        if refusal is not None:
            errors = [line for line in stderr.splitlines() if re.search(r'\b(?:ERROR|FATAL):', line)]
            if (result.returncode == 0 or len(errors) != 1 or refusal not in errors[0]
                    or re.search(r'(?i)\b(?:WARNING|PANIC):', stderr)):
                raise RuntimeError('Expected native refusal differs: ' + stderr.strip())
            entry['expected_refusal'] = refusal
            return result
        if bootstrap and args[:2] != ['docker', 'logs']:
            typed_inspection = args[1:3] in (['container', 'inspect'], ['network', 'inspect'], ['volume', 'inspect'])
            missing_postmaster = (args == ['docker', 'exec', db_id, 'cat', '/var/lib/postgresql/data/postmaster.pid']
                and not check and result.returncode == 1 and result.stdout == '' and result.stderr in (
                    'cat: /var/lib/postgresql/data/postmaster.pid: No such file or directory\n',
                    "cat: can't open '/var/lib/postgresql/data/postmaster.pid': No such file or directory\n"))
            if result.returncode:
                if not typed_inspection and not missing_postmaster:
                    raise RuntimeError('Non-log native command failed')
            else:
                container = report.get('container', {})
                opaque_original_artifact = (len(args) == 5
                    and args[:4] == ['docker', 'exec', db_id, 'cat']
                    and args[4] in ORIGINAL_ARTIFACT_PATHS
                    and isinstance(db_id, str) and re.fullmatch(r'[a-f0-9]{64}', db_id)
                    and container.get('Id') == db_id
                    and container.get('Name') == '/' + fixture + '-defaults-db'
                    and container.get('Image') == report.get('original_startup', {}).get('image_id')
                    and container.get('Image') is not None
                    and container.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') == 'sbarbase-fixture-' + fixture)
                if stderr or (not opaque_original_artifact and preservation.DIAGNOSTICS.search(
                        result.stdout if binary else result.stdout.encode('utf-8'))):
                    raise RuntimeError('Non-log native command diagnosed')
                if opaque_original_artifact:
                    entry['stdout_kind'] = 'closed-original-artifact-bytes'
                    entry['original_artifact_path'] = args[4]
        if (stderr.strip() and not server_logs) or check and result.returncode:
            raise RuntimeError('Native inventory command refused: ' + stderr.strip())
        return result

    def sql(statement, database='postgres', *, cleanup=False, operation_deadline=None):
        return native(['docker', 'exec', '-i', db_id, 'psql', '-X', '-qAt', '-v',
                       'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database],
                      data=statement, cleanup=cleanup, operation_deadline=operation_deadline).stdout.strip()

    try:
        if bootstrap:
            step = 'exact namespace refusal'
            for suffix in ('-defaults-db', '-bootstrap-auth-1', '-bootstrap-auth-2'):
                native(['docker', 'container', 'inspect', fixture + suffix], allow_absence=True)
            native(['docker', 'network', 'inspect', names['net']], allow_absence=True)
            native(['docker', 'volume', 'inspect', names['data']], allow_absence=True)
        pin = json.loads((ROOT / 'lab/distro-image.lock.json').read_text())
        reference = image_identity.reference(pin)
        image = image_identity.record(native(['docker', 'image', 'inspect', reference]).stdout)
        expected_id = image_identity.resolved_id(reference, image)
        config = image['Config']
        if config.get('Entrypoint') != ['docker-entrypoint.sh'] or config.get('Cmd') != ['postgres', '-D', '/etc/postgresql']:
            raise RuntimeError('Original image startup contract differs')
        destinations = {'/var/lib/postgresql/data', '/etc/postgresql-custom'}
        if set(config.get('Volumes') or {}) - destinations:
            raise RuntimeError('Unaccounted original image volume destination')
        if bootstrap and config.get('Volumes'):
            raise RuntimeError('Bootstrap client image must not create anonymous volumes')
        environment = dict(value.split('=', 1) for value in config.get('Env', []) if '=' in value)
        if environment.get('POSTGRES_USER') != 'supabase_admin' or environment.get('POSTGRES_DB') != 'postgres':
            raise RuntimeError('Original application database/user defaults differ')
        report['original_startup'] = {'reference': reference, 'image_id': expected_id,
                                      'entrypoint': config['Entrypoint'], 'cmd': config['Cmd'],
                                      'declared_volumes': config.get('Volumes'),
                                      'user': environment['POSTGRES_USER'], 'database': environment['POSTGRES_DB']}
        if bootstrap:
            step = 'configuration preservation preparation'
            original_initdb = environment.get('POSTGRES_INITDB_ARGS')
            if not original_initdb or '--auth-' in original_initdb:
                raise RuntimeError('Original initdb arguments need an explicit compatibility decision')
            initdb_args = original_initdb + ' --auth-local=scram-sha-256 --auth-host=scram-sha-256'
            report['bootstrap']['original_initdb_args'] = original_initdb
            report['bootstrap']['configured_initdb_args'] = initdb_args
            created.extend(('volume', names[kind]) for kind in ('config', 'confdir'))
            receipt = preservation.prepare_bootstrap_config(fixture, report['source_sha256'], work_deadline, whole_deadline)
            if (not isinstance(receipt, preservation.BootstrapConfigReceipt)
                    or receipt.fixture != fixture or receipt.owner != owner
                    or receipt.reference != reference or receipt.image_id != expected_id
                    or receipt.source_sha256 != report['source_sha256']
                    or receipt.volumes != (names['config'], names['confdir'])
                    or receipt.evidence_directory != '/evidence/configuration-preservation'):
                raise RuntimeError('Configuration handoff binding differs')
            report['bootstrap']['configuration_preservation'] = {
                'fixture': receipt.fixture, 'owner': receipt.owner, 'reference': receipt.reference,
                'image_id': receipt.image_id, 'source_sha256': receipt.source_sha256,
                'volumes': list(receipt.volumes), 'evidence_directory': receipt.evidence_directory,
                'phases': json.loads(receipt.phases_json)}
            for name in receipt.volumes:
                item = image_identity.record(native(['docker', 'volume', 'inspect', name]).stdout)
                preservation.admit_volume(item, name, owner)
        step = 'original startup'
        if bootstrap:
            created.append(('network', names['net']))
        native(['docker', 'network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, names['net']])
        if not bootstrap:
            created.append(('network', names['net']))
        for kind in (('data',) if bootstrap else ('data', 'config')):
            if bootstrap:
                created.append(('volume', names[kind]))
            native(['docker', 'volume', 'create', '--label', 'io.sbarbase.owner=' + owner, names[kind]])
            if not bootstrap:
                created.append(('volume', names[kind]))
        step = 'original startup'
        args = ['docker', 'run', '-d', '--pull=never', '--name', names['db'],
                '--label', 'io.sbarbase.owner=' + owner, '--network', names['net'],
                '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5', '--pids-limit', '128',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--tmpfs', '/tmp:rw,mode=1777,size=64m',
                '--env', 'POSTGRES_PASSWORD=synthetic-defaults-fixture-password',
                '--mount', 'type=volume,source=' + names['data'] + ',target=/var/lib/postgresql/data',
                '--mount', 'type=volume,source=' + names['config'] + ',target=/etc/postgresql-custom']
        for capability in ('CHOWN', 'SETUID', 'SETGID', 'DAC_OVERRIDE', 'FOWNER'):
            args += ['--cap-add', capability]
        if bootstrap:
            args += ['--env', 'POSTGRES_INITDB_ARGS=' + initdb_args]
        if bootstrap:
            args += ['--mount', 'type=volume,source=' + names['confdir'] + ',target=' + preservation.TARGET]
            created.append(('container', names['db']))
        db_id = native(args + [reference]).stdout.strip()
        if not bootstrap:
            created.append(('container', db_id))
        record = image_identity.record(native(['docker', 'inspect', db_id]).stdout)
        if record['Image'] != expected_id or record['Name'] != '/' + names['db'] or record['Config']['Labels']['io.sbarbase.owner'] != owner:
            raise RuntimeError('Original startup container identity differs')
        report['container'] = record
        limits = record['HostConfig']
        if (limits['Memory'] != 536870912 or limits['MemorySwap'] != 536870912
                or limits['NanoCpus'] != 500000000 or limits['PidsLimit'] != 128
                or limits.get('PortBindings')):
            raise RuntimeError('Original startup resource bounds differ')
        if record['Config']['Entrypoint'] != config['Entrypoint'] or record['Config']['Cmd'] != config['Cmd']:
            raise RuntimeError('Original startup was overridden')
        expected_mounts = {('volume', names['data'], '/var/lib/postgresql/data'),
                           ('volume', names['config'], '/etc/postgresql-custom')}
        if bootstrap:
            expected_mounts.add(('volume', names['confdir'], preservation.TARGET))
            actual_mounts = record['Mounts']
            if (len(actual_mounts) != 3
                    or limits.get('Tmpfs') != {'/tmp': 'rw,mode=1777,size=64m'}
                    or any(m.get('Type') != 'volume' or m.get('Driver') != 'local'
                           or m.get('RW') is not True for m in actual_mounts)):
                raise RuntimeError('Bootstrap complete mounts or temporary policy differ')
            mounted = {(m['Type'], m.get('Name'), m['Destination']) for m in actual_mounts}
            if len(mounted) != 3:
                raise RuntimeError('Bootstrap mount identity duplicated')
        else:
            mounted = {(m['Type'], m.get('Name'), m['Destination']) for m in record['Mounts'] if m['Type'] != 'tmpfs'}
        if mounted != expected_mounts:
            raise RuntimeError('Unexpected original startup mount')
        deadline = time.monotonic() + 45
        while not bootstrap:
            process = native(['docker', 'exec', db_id, 'cat', '/proc/1/comm']).stdout.strip()
            ready = native(['docker', 'exec', db_id, 'pg_isready', '-U', 'supabase_admin', '-d', 'postgres'], check=False)
            if process in ('postgres', '.postgres-wrapp') and ready.returncode == 0:
                pid = sql("SELECT split_part(pg_read_file('postmaster.pid'),E'\\n',1);")
                if pid == '1':
                    report['postmaster'] = {'pid': 1, 'comm': process}
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError('Original image readiness deadline')
            time.sleep(.5)
        if bootstrap:
            report['postmaster'] = wait_original_ready(native, db_id)
        step = 'original artifact reads'
        original = output / 'original-artifacts'
        original.mkdir()
        report['original_artifacts'] = []
        for number, path in enumerate(ORIGINAL_ARTIFACT_PATHS):
            step = 'original artifact read: ' + path
            result = native(['docker', 'exec', db_id, 'cat', path], binary=True)
            target = str(number) + '-' + Path(path).name
            (original / target).write_bytes(result.stdout)
            report['original_artifacts'].append({'path': path, 'artifact': 'original-artifacts/' + target,
                                                  'bytes': len(result.stdout),
                                                  'sha256': hashlib.sha256(result.stdout).hexdigest()})
        step = 'native defaults SQL'
        report['server'] = json.loads(sql("SELECT json_build_object('version',version(),'version_num',current_setting('server_version_num'),'database',current_database(),'user',current_user);"))
        report['available_extensions'] = json.loads(sql("SELECT coalesce(json_agg(row_to_json(e) ORDER BY name),'[]'::json) FROM (SELECT name,default_version,installed_version FROM pg_available_extensions) e;"))
        report['available_extension_versions'] = json.loads(sql("SELECT coalesce(json_agg(row_to_json(e) ORDER BY name,version),'[]'::json) FROM (SELECT name,version,installed,superuser,trusted,relocatable,schema,requires FROM pg_available_extension_versions) e;"))
        report['settings'] = json.loads(sql("SELECT json_agg(row_to_json(s) ORDER BY name) FROM (SELECT name,setting,unit,context,source,sourcefile,sourceline,pending_restart FROM pg_settings WHERE name IN ('shared_preload_libraries','session_preload_libraries','local_preload_libraries','config_file','data_directory','max_connections','shared_buffers') OR name LIKE 'cron.%' OR name LIKE 'pg_net.%' OR name LIKE 'pgtle.%' OR name LIKE 'supautils.%' OR name='pgsodium.getkey_script') s;"))
        report['databases'] = json.loads(sql("SELECT json_agg(row_to_json(d) ORDER BY datname) FROM (SELECT oid,datname,datallowconn,datistemplate,pg_get_userbyid(datdba) AS owner FROM pg_database) d;"))
        report['extensions'] = {}
        for database in report['databases']:
            if database['datallowconn'] and not database['datistemplate']:
                report['extensions'][database['datname']] = json.loads(sql("SELECT coalesce(json_agg(row_to_json(e) ORDER BY extname),'[]'::json) FROM (SELECT extname,extversion,extnamespace::regnamespace::text AS schema FROM pg_extension) e;", database['datname']))
        report['activity'] = json.loads(sql("SELECT coalesce(json_agg(row_to_json(a) ORDER BY backend_type,pid),'[]'::json) FROM (SELECT pid,datid,datname,usename,backend_type,application_name,state FROM pg_stat_activity) a;"))
        report['observations_complete'] = True
        baseline = None
        if bootstrap:
            step = 'configured bootstrap initial diagnostics'
            baseline = native(['docker', 'logs', db_id], server_logs=True)
            baseline_started_at = report['commands'][-1]['started_at_unix_seconds'] + report['commands'][-1]['duration_seconds']
            report['bootstrap']['baseline_logs'] = {'stdout': baseline.stdout, 'stderr': baseline.stderr}
            (output / 'bootstrap-baseline.log').write_text(baseline.stdout + baseline.stderr)
            if re.search(r'(?im)\b(?:WARNING|ERROR|FATAL|PANIC):', baseline.stdout + baseline.stderr):
                raise RuntimeError('Configured bootstrap contains an unexplained initial diagnostic')
            report['bootstrap']['initial_diagnostics_passed'] = True
        if behavior is not None:
            step = 'declared native behavior'
            behavior(report, sql, native, db_id)
        step = 'original startup diagnostics'
        logs = native(['docker', 'logs', db_id], server_logs=True)
        report['startup_logs'] = logs.stdout + logs.stderr
        (output / 'startup.log').write_text(report['startup_logs'])
        if bootstrap:
            if not logs.stdout.startswith(baseline.stdout) or not logs.stderr.startswith(baseline.stderr):
                raise RuntimeError('Native log streams lost the captured baseline prefix')
            delta = logs.stdout[len(baseline.stdout):] + logs.stderr[len(baseline.stderr):]
            report['bootstrap']['behavior_logs_delta'] = delta
            diagnostics = [line for line in delta.splitlines()
                           if re.search(r'(?i)\b(?:WARNING|ERROR|FATAL|PANIC):', line)]
            expected = 'password authentication failed for user "supabase_admin"'
            auth_helpers = report['bootstrap'].get('auth_helpers', [])
            if len(auth_helpers) != 2 or auth_helpers[1]['Name'] != '/' + fixture + '-bootstrap-auth-2':
                raise RuntimeError('Declared negative authentication helper identity missing')
            paired = [command for command in report['commands']
                      if command.get('expected_refusal') == expected
                      and command['exit_code'] != 0
                      and command['args'][:3] == ['docker', 'start', '--attach']
                      and command['args'][3:] == [auth_helpers[1]['Id']]
                      and command['started_at_unix_seconds'] > baseline_started_at]
            if (report['bootstrap'].get('expected_behavior_diagnostics') != [expected]
                    or len(paired) != 1 or len(diagnostics) != 1
                    or not re.search(r'\bFATAL:\s+' + re.escape(expected) + r'\s*$', diagnostics[0])):
                raise RuntimeError('Configured bootstrap diagnostic does not match the declared negative control')
            report['bootstrap']['negative_control_diagnostic_paired'] = True
        elif re.search(r'(?im)\b(?:WARNING|ERROR|FATAL|PANIC):', report['startup_logs']):
            raise RuntimeError('Original startup contains an unexplained diagnostic')
        report['passed'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        report['failed_step'] = step
        if db_id:
            try:
                if bootstrap and time.monotonic() >= work_deadline:
                    raise RuntimeError('Failed log capture work budget exhausted; bounded command artifacts retained')
                result = native(['docker', 'logs', db_id], check=False, server_logs=True) if bootstrap else subprocess.run(['docker', 'logs', db_id], capture_output=True, text=True, timeout=20)
                (output / 'startup.log').write_text(result.stdout + result.stderr)
                report['failed_startup_logs'] = result.stdout + result.stderr
            except Exception as diagnostic:
                report['log_capture_error'] = type(diagnostic).__name__ + ': ' + str(diagnostic)
    finally:
        report['cleanup'] = []
        cleanup_mode = True
        consumers_clear = not any(not e['passed'] for e in report.get('bootstrap', {}).get('helper_cleanup', []))
        if configured_effects:
            effects_cleanup = report.get('worker_effects', {}).get('helper_cleanup')
            if effects_cleanup is not None and not effects_cleanup.get('passed'):
                consumers_clear = False
        if bootstrap and 'configuration_preservation' not in report.get('bootstrap', {}):
            prep_path = output / 'configuration-preservation/report.json'
            if not prep_path.exists():
                created = []
            if prep_path.exists():
                prep = json.loads(prep_path.read_text())
                created = [(kind, name) for kind, name in created if kind != 'volume' or name in prep.get('attempted_volumes', [])]
                failed_helpers = [e for e in prep['cleanup'] if e.get('kind') == 'container' and not e['passed']]
                consumers_clear = not failed_helpers
                if len(failed_helpers) == 1:
                    try:
                        name = failed_helpers[0]['name']
                        if name not in {fixture + '-configpreserve-' + role for role in preservation.SCRIPTS}:
                            raise RuntimeError('Preparation fallback name differs')
                        inspection = native(['docker', 'container', 'inspect', name], check=False, server_logs=True)
                        if inspection.returncode:
                            preservation.admit_absence({'exit_code': inspection.returncode, 'stdout': inspection.stdout.encode(),
                                'stderr': inspection.stderr.encode(), 'refusal': None}, ['docker', 'container', 'inspect', name])
                        else:
                            item = image_identity.record(inspection.stdout)
                            if (item.get('Name') != '/' + name or item.get('Image') != expected_id
                                    or item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner
                                    or not re.fullmatch(r'[a-f0-9]{64}', item.get('Id', ''))):
                                raise RuntimeError('Preparation fallback ownership differs')
                            native(['docker', 'rm', '-f', item['Id']])
                        consumers_clear = True
                        report['cleanup'].append({'kind': 'preparation-container', 'identity': name, 'passed': True})
                    except Exception as error:
                        report['cleanup'].append({'kind': 'preparation-container', 'passed': False, 'error': str(error)})
        for kind, identity in reversed(created):
            args = ['docker', 'rm', '-f', identity] if kind == 'container' else ['docker', kind, 'rm', identity]
            try:
                if bootstrap:
                    if kind != 'container' and not consumers_clear:
                        raise RuntimeError('Consumer cleanup unknown')
                    inspect_args = ['docker', 'container' if kind == 'container' else kind, 'inspect', identity]
                    observed = native(inspect_args, check=False, server_logs=True)
                    if observed.returncode:
                        captured = {'exit_code': observed.returncode, 'stdout': observed.stdout.encode(), 'stderr': observed.stderr.encode(), 'refusal': None}
                        preservation.admit_absence(captured, inspect_args)
                        report['cleanup'].append({'kind': kind, 'identity': identity, 'passed': True, 'recognized_absence': True})
                        continue
                    item = image_identity.record(observed.stdout)
                    if kind == 'container':
                        if (item.get('Name') != '/' + names['db'] or item.get('Image') != expected_id
                                or item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner
                                or not re.fullmatch(r'[a-f0-9]{64}', item.get('Id', ''))
                                or db_id is not None and item['Id'] != db_id):
                            raise RuntimeError('Cleanup database ownership differs')
                        args = ['docker', 'rm', '-f', item['Id']]
                    elif kind == 'volume':
                        preservation.admit_volume(item, identity, owner)
                    elif (item.get('Name') != identity or item.get('Driver') != 'bridge'
                          or item.get('Scope') != 'local' or not item.get('Internal')
                          or item.get('Labels') != {'io.sbarbase.owner': owner} or item.get('Options')):
                        raise RuntimeError('Cleanup network ownership differs')
                result = native(args)
                report['cleanup'].append({'kind': kind, 'identity': identity, 'passed': result.returncode == 0})
            except Exception as error:
                report['passed'] = False
                if kind == 'container':
                    consumers_clear = False
                report['cleanup'].append({'kind': kind, 'identity': identity, 'passed': False, 'error': str(error)})
        if configured_effects:
            report['cleanup_operation_count'] = cleanup_commands
        if bootstrap and time.monotonic() > whole_deadline:
            report['passed'] = False
            report['deadline_error'] = 'Bootstrap whole deadline exceeded'
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'scope': report['scope'], 'source_sha256': report['source_sha256'],
                      'passed': report['passed'], 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
