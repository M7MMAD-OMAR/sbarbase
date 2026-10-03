"""Bounded configured-original-image bootstrap and native identity probe."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import time

import native_config_preservation_inspect as preservation

from native_defaults_inspect import main, wait_original_ready, admit_bootstrap_inspection


def admit_installation_setup(before, after, installed, *, newly_installed):
    """Admit one declared native installation effect before identity mutation."""
    inventory = {'roles', 'memberships', 'databases', 'libraries', 'session_libraries', 'application_oid'}
    if (set(before) != inventory or set(after) != inventory
            or installed != {'version': '0.20.4', 'owner': 'supabase_admin'}
            or not isinstance(newly_installed, bool)):
        raise RuntimeError('Native extension setup inventory or metadata differs')
    role_keys = {'oid', 'rolname', 'rolsuper', 'rolinherit', 'rolcreaterole', 'rolcreatedb',
                 'rolcanlogin', 'rolreplication', 'rolbypassrls', 'rolconnlimit'}

    def roles(snapshot):
        if not isinstance(snapshot['roles'], list):
            raise RuntimeError('Native setup roles must be a list')
        names, oids = {}, set()
        for role in snapshot['roles']:
            if (not isinstance(role, dict) or set(role) != role_keys
                    or not isinstance(role['oid'], str) or not re.fullmatch(r'[1-9][0-9]*', role['oid'])
                    or not 0 < int(role['oid']) <= 4294967295
                    or not isinstance(role['rolname'], str) or not role['rolname']
                    or role['rolname'] in names or role['oid'] in oids
                    or type(role['rolconnlimit']) is not int or role['rolconnlimit'] < -1
                    or any(type(role[key]) is not bool for key in role_keys - {'oid', 'rolname', 'rolconnlimit'})):
                raise RuntimeError('Native setup role identity or shape differs')
            names[role['rolname']] = role
            oids.add(role['oid'])
        return names

    original, observed = roles(before), roles(after)
    if any(observed.get(name) != role for name, role in original.items()):
        raise RuntimeError('Native setup changed or removed a prior role')
    for key in inventory - {'roles'}:
        if json.dumps(before[key], sort_keys=True) != json.dumps(after[key], sort_keys=True):
            raise RuntimeError('Native setup changed original identity: ' + key)
    added = [role for name, role in observed.items() if name not in original]
    if not newly_installed:
        if added or before != after:
            raise RuntimeError('Existing native extension introduced an identity delta')
        return []
    if len(added) != 1:
        raise RuntimeError('Native installation requires exactly the declared new role')
    role = added[0]
    expected = {'oid': role['oid'], 'rolname': 'supabase_functions_admin', 'rolsuper': False,
                'rolinherit': False, 'rolcreaterole': True, 'rolcreatedb': False, 'rolcanlogin': True,
                'rolreplication': False, 'rolbypassrls': False, 'rolconnlimit': -1}
    if role != expected:
        raise RuntimeError('Native installation role differs')
    return added


IDENTITY_SQL = "SELECT json_build_object(\n          'roles',(SELECT json_agg(row_to_json(r) ORDER BY rolname) FROM\n            (SELECT oid,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,\n                    rolcanlogin,rolreplication,rolbypassrls,rolconnlimit\n             FROM pg_roles) r),\n          'memberships',(SELECT coalesce(json_agg(row_to_json(m) ORDER BY roleid,member,grantor),'[]'::json)\n            FROM (SELECT roleid,member,grantor,admin_option,inherit_option,set_option FROM pg_auth_members) m),\n          'databases',(SELECT json_agg(row_to_json(d) ORDER BY datname) FROM\n            (SELECT oid,datname,datdba,datallowconn,datistemplate FROM pg_database) d),\n          'libraries',current_setting('shared_preload_libraries'),\n          'session_libraries',current_setting('session_preload_libraries'),\n          'application_oid',(SELECT oid FROM pg_database WHERE datname='postgres'));\n        "
CONFIGURATION_SQL = "SELECT json_build_object(\n          'settings',(SELECT json_agg(row_to_json(s) ORDER BY name) FROM\n            (SELECT name,setting,source,sourcefile,sourceline,pending_restart\n             FROM pg_settings WHERE name IN ('pg_net.username','pg_net.database_name',\n               'hba_file','config_file','data_directory','shared_preload_libraries',\n               'session_preload_libraries')) s),\n          'file_settings',(SELECT json_agg(row_to_json(f) ORDER BY seqno) FROM\n            (SELECT sourcefile,sourceline,seqno,name,setting,applied,error\n             FROM pg_file_settings) f),\n          'hba_rules',(SELECT json_agg(row_to_json(h) ORDER BY rule_number) FROM\n            (SELECT rule_number,file_name,line_number,type,database,user_name,\n                    address,netmask,auth_method,options,error FROM pg_hba_file_rules) h));\n        "

def identity_snapshot(sql, *, cleanup=False):
    return json.loads(sql(IDENTITY_SQL, cleanup=True) if cleanup else sql(IDENTITY_SQL))


def configuration_snapshot(sql, *, cleanup=False):
    sample = json.loads(sql(CONFIGURATION_SQL, cleanup=True) if cleanup else sql(CONFIGURATION_SQL))
    if any(r['error'] for r in sample['file_settings'] or []):
        raise RuntimeError('Native configuration file diagnostics')
    if any(r['error'] for r in sample['hba_rules'] or []):
        raise RuntimeError('Native HBA rule diagnostics')
    return sample


def original_configuration_frames(report, native, db_id, *, phase='bootstrap'):
    if phase not in ('bootstrap', 'effects-before', 'effects-after-setup', 'effects-pre-cleanup', 'effects-post-cleanup'):
        raise ValueError('Unknown fixed configuration observation phase')
    directory = 'bootstrap-artifacts' if phase == 'bootstrap' else 'worker-effects-artifacts'
    output = Path('/evidence') / directory
    admitted = report['bootstrap']['configuration_preservation']
    originals = admitted['phases']['reader']['files']
    final_script = preservation.SHELL_COMMON + preservation.frame_commands('final', False)
    final_raw = native(['docker', 'exec', '--user', '100:101', db_id, '/bin/sh', '-ec', final_script], binary=True, cleanup=phase == 'effects-post-cleanup').stdout
    final_files = preservation.decode_frames(final_raw, 'final', False)
    final_artifact = 'final-config-frames.bin' if phase == 'bootstrap' else phase + '-config-frames.bin'
    (output / final_artifact).write_bytes(final_raw)
    for base in preservation.ORIGINALS:
        expected = next(f for f in originals if f['basename'] == base and f['view'] == 'target')
        retained = Path('/evidence/configuration-preservation') / ('reader-target-' + base + '.bin')
        expected_bytes = retained.read_bytes()
        actual = final_files['frames'][(base, 'target')]
        if actual['bytes'] != expected_bytes or actual['sha256'] != expected['sha256']:
            raise RuntimeError('Final original bytes changed: ' + base)
        for key in ('native_type', 'mode', 'uid', 'gid', 'size', 'nlink', 'inode', 'device'):
            if actual['metadata'][key] != expected['metadata'][key]:
                raise RuntimeError('Final original metadata changed: ' + base)
    reader_link = admitted['phases']['reader']['layouts'][0]['link']
    if final_files['layouts'][0]['link'] != reader_link:
        raise RuntimeError('Final original link changed')
    return {'passed': True, 'artifact': directory + '/' + final_artifact,
                                            'sha256': hashlib.sha256(final_raw).hexdigest(), 'override_absent_both_views': True}


def probe(report, sql, native, db_id):
    evidence = report.setdefault('bootstrap', {})
    evidence.update({'scope': 'configured-original-image-bootstrap', 'passed': False,
                'worker_samples': [], 'auth_helpers': [], 'helper_cleanup': [],
                'expected_behavior_diagnostics': [], 'limitations': [
                    'Native configuration profile, not untouched defaults or full Compose.',
                    'Final bootstrap worker observation does not prove temporary-server attachment.',
                    'Generated initdb authentication is distinct from active HBA policy.',
                    'No key, archive replay, native job effects or full restore continuity proof.',
                    'Normal cleanup only; interruption and power loss are unrun.']})
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid bootstrap fixture identity')
    owner = 'sbarbase-fixture-' + fixture
    network = fixture + '-defaults-net'
    startup = report['original_startup']
    helpers = []
    helper_names = {}
    first_failure = None
    output = Path('/evidence/bootstrap-artifacts')
    output.mkdir()

    def observe(statement):
        return json.loads(sql(statement))

    def snapshot():
        return identity_snapshot(sql)

    def configuration(label):
        sample = configuration_snapshot(sql)
        evidence[label] = sample
        return {s['name']: s for s in sample['settings']}

    def workers():
        return observe("""SELECT coalesce(json_agg(row_to_json(w) ORDER BY pid),'[]'::json)
          FROM (SELECT pid,datid,datname,usename,backend_type FROM pg_stat_activity
                WHERE backend_type='pg_net 0.20.4 worker') w;""")

    def wait_worker(label, username, old_pid=None):
        deadline = time.monotonic() + 12
        while True:
            sample = workers()
            evidence['worker_samples'].append({'label': label, 'workers': sample,
                                               'observed_at_unix_seconds': time.time()})
            if (len(sample) == 1 and sample[0]['usename'] == username
                    and sample[0]['datname'] == 'postgres'
                    and sample[0]['datid'] == before['application_oid']
                    and (old_pid is None or sample[0]['pid'] != old_pid)):
                return sample[0]
            if time.monotonic() >= deadline:
                raise RuntimeError('Native worker identity deadline: ' + label)
            time.sleep(.2)

    try:
        net = json.loads(native(['docker', 'network', 'inspect', network]).stdout)[0]
        evidence['network'] = net
        if (net['Name'] != network or not net['Internal']
                or net.get('Labels', {}).get('io.sbarbase.owner') != owner):
            raise RuntimeError('Owned authentication network differs')
        before = snapshot()
        evidence['before'] = before
        if before['application_oid'] is None:
            raise RuntimeError('Original application database missing')
        required = {'pg_stat_statements', 'pgaudit', 'plpgsql', 'plpgsql_check', 'pg_cron',
                    'pg_net', 'pgsodium', 'auto_explain', 'pg_tle', 'plan_filter', 'supabase_vault'}
        if set(s.strip() for s in before['libraries'].split(',')) != required:
            raise RuntimeError('Original eleven preloads differ')
        if before['session_libraries'] != 'supautils':
            raise RuntimeError('Original session preload differs')
        roles = {r['rolname']: r for r in before['roles']}
        if not {'postgres', 'supabase_admin'} <= roles.keys() or not roles['supabase_admin']['rolsuper']:
            raise RuntimeError('Native bootstrap roles differ')
        initial = configuration('bootstrap_configuration')
        if (initial['pg_net.username']['setting'] != 'supabase_admin'
                or initial['pg_net.username']['sourcefile'] != '/etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf'):
            raise RuntimeError('Temporary native identity profile differs')
        evidence['bootstrap_worker'] = wait_worker('configured bootstrap', 'supabase_admin')
        evidence['hba_artifacts'] = []
        for number, path in enumerate(('/etc/postgresql/pg_hba.conf', '/var/lib/postgresql/data/pg_hba.conf')):
            raw = native(['docker', 'exec', db_id, 'cat', path], binary=True).stdout
            target = str(number) + '-pg_hba.conf'
            (output / target).write_bytes(raw)
            evidence['hba_artifacts'].append({'path': path,
                'artifact': 'bootstrap-artifacts/' + target, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest()})
        evidence['active_local_trust_rules'] = [r for r in evidence['bootstrap_configuration']['hba_rules'] or []
                                               if r['type'] == 'local' and r['auth_method'] == 'trust']
        if initial['hba_file']['setting'] != '/etc/postgresql/pg_hba.conf':
            raise RuntimeError('Active original HBA path differs')
        versions = {r['name']: r['default_version'] for r in report['available_extensions']}
        if versions.get('pg_net') != '0.20.4':
            raise RuntimeError('Native pg_net default version differs')
        extension_statement = "SELECT json_build_object('version',extversion,'owner',pg_get_userbyid(extowner)) FROM pg_extension WHERE extname='pg_net';"
        existing_output = sql(extension_statement)
        existing = json.loads(existing_output) if existing_output else None
        evidence['pg_net_before_installation'] = existing
        if existing_output and existing != {'version': '0.20.4', 'owner': 'supabase_admin'}:
            raise RuntimeError('Existing native pg_net metadata differs')
        newly_installed = existing is None
        if newly_installed:
            sql('CREATE EXTENSION pg_net;')
        installed = observe(extension_statement)
        evidence['installed_pg_net'] = installed
        if installed != {'version': '0.20.4', 'owner': 'supabase_admin'}:
            raise RuntimeError('Installed native pg_net version or owner differs')
        post_install = snapshot()
        evidence['post_install_before_transition'] = post_install
        setup_delta = admit_installation_setup(before, post_install, installed, newly_installed=newly_installed)
        evidence['installation_setup'] = {'newly_installed': newly_installed, 'added_roles': setup_delta,
                                          'passed': True}

        admitted = report['bootstrap']['configuration_preservation']
        originals = admitted['phases']['reader']['files']
        override = next(f for f in originals if f['basename'] == preservation.OVERRIDE and f['view'] == 'target')
        meta = override['metadata']
        if (meta['native_type'] != 'regular file' or meta['mode'] != '644'
                or (meta['uid'], meta['gid'], meta['size'], meta['nlink']) != (100, 101, 33, 1)
                or meta['inode'] <= 0 or meta['device'] < 0):
            raise RuntimeError('Protected temporary metadata differs')
        expected_meta = '|'.join(str(meta[key]) for key in ('native_type', 'mode', 'uid', 'gid', 'size', 'inode', 'device', 'nlink'))
        script = preservation.SHELL_COMMON + preservation.frame_commands('protected', True)
        script += "[ \"$(meta /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf)\" = '" + expected_meta + "' ]\n"
        script += "[ \"$(od -An -v -tx1 -w1 -N 34 -- /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf)\" = '" + preservation.encode_public_argument(preservation.OVERRIDE_BYTES) + "' ]\n"
        script += 'rm -- /etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf\n'
        protected_raw = native(['docker', 'exec', '--user', '100:101', db_id, '/bin/sh', '-ec', script], binary=True).stdout
        protected = preservation.decode_frames(protected_raw, 'protected', True)
        if protected['frames'][(preservation.OVERRIDE, 'target')]['metadata'] != meta:
            raise RuntimeError('Temporary object changed before unlink')
        evidence['final_config_preservation'] = original_configuration_frames(report, native, db_id)
        if sql('SELECT net.worker_restart();') != 't':
            raise RuntimeError('Native worker restart refused')
        evidence['final_worker'] = wait_worker('restored original identity', 'postgres', evidence['bootstrap_worker']['pid'])
        final = configuration('final_configuration')
        if (final['pg_net.username']['setting'] != 'postgres'
                or final['pg_net.username']['sourcefile'] != '/etc/postgresql-custom/conf.d/pg_net.conf'
                or final['pg_net.username']['pending_restart']):
            raise RuntimeError('Original native identity provenance not restored')
        evidence['after_identity_transition'] = snapshot()
        if json.dumps(evidence['after_identity_transition'], sort_keys=True) != json.dumps(post_install, sort_keys=True):
            raise RuntimeError('Roles, ownership or libraries changed during native transition')
        native(['docker', 'restart', '--time', '10', db_id])
        evidence['warm_postmaster'] = wait_original_ready(native, db_id)
        record = json.loads(native(['docker', 'inspect', db_id]).stdout)[0]
        evidence['warm_container'] = record
        if (record['Id'] != db_id or record['Image'] != startup['image_id']
                or record['Config']['Entrypoint'] != startup['entrypoint']
                or record['Config']['Cmd'] != startup['cmd']):
            raise RuntimeError('Warm original container identity differs')
        evidence['after_warm_restart'] = snapshot()
        if json.dumps(evidence['after_warm_restart'], sort_keys=True) != json.dumps(post_install, sort_keys=True):
            raise RuntimeError('Warm restart changed roles, ownership or libraries')
        evidence['warm_worker'] = wait_worker('warm original identity', 'postgres')
        warm = configuration('warm_configuration')
        if warm != final:
            raise RuntimeError('Warm restart changed effective native configuration')
        database_networks = report['container']['NetworkSettings']['Networks']
        if set(database_networks) != {network}:
            raise RuntimeError('Negative control database network differs')
        database_address = database_networks[network]['IPAddress']
        address = ipaddress.IPv4Address(database_address)
        if (str(address) != database_address or address.is_unspecified or address.is_multicast
                or address.is_loopback or database_networks[network].get('NetworkID') != net['Id']):
            raise RuntimeError('Negative control database IPv4 admission differs')
        expected_client_line = ('psql: error: connection to server at "' + fixture + '-defaults-db" ('
                                + database_address + '), port 5432 failed: FATAL:  '
                                + 'password authentication failed for user "supabase_admin"\n')
        evidence['planned_negative_client_control'] = {'database_dns': fixture + '-defaults-db',
            'database_ipv4': database_address, 'network_id': net['Id'], 'port': 5432,
            'expected_stderr_line': expected_client_line, 'runtime_observed': False}
        for index, password in enumerate(('synthetic-defaults-fixture-password', 'synthetic-wrong-fixture-password'), 1):
            name = fixture + '-bootstrap-auth-' + str(index)
            args = ['docker', 'create', '--pull=never', '--name', name,
                    '--label', 'io.sbarbase.owner=' + owner, '--network', network,
                    '--memory', '64m', '--memory-swap', '64m', '--cpus', '.25', '--pids-limit', '16',
                    '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,mode=1777,size=16m',
                    '--env', 'PGPASSWORD=' + password, '--entrypoint', 'psql', startup['reference'],
                    '-X', '-qAt', '-w', '-v', 'ON_ERROR_STOP=1', '-h', fixture + '-defaults-db',
                    '-U', 'supabase_admin', '-d', 'postgres', '-c', 'SELECT current_user,current_database();']
            helpers.append(name)
            helper_names[name] = name
            helper = native(args).stdout.strip()
            helper_names[name] = helper
            record = json.loads(native(['docker', 'inspect', helper]).stdout)[0]
            evidence['auth_helpers'].append(record)
            limits = record['HostConfig']
            if (record['Id'] != helper or record['Name'] != '/' + name or record['Image'] != startup['image_id']
                    or record['Config']['Labels'].get('io.sbarbase.owner') != owner
                    or record['Config']['User'] != '10001:10001' or not limits['ReadonlyRootfs']
                    or limits['Memory'] != 67108864 or limits['MemorySwap'] != 67108864
                    or limits['NanoCpus'] != 250000000 or limits['PidsLimit'] != 16
                    or limits.get('Privileged') or limits.get('Binds') or limits.get('PidMode') == 'host'
                    or limits.get('IpcMode') == 'host' or limits.get('NetworkMode') != network
                    or limits.get('PortBindings') or any((record['NetworkSettings'].get('Ports') or {}).values())
                    or any(m['Type'] != 'tmpfs' or m['Destination'] != '/tmp' for m in record['Mounts'])
                    or limits.get('Tmpfs') != {'/tmp': 'rw,mode=1777,size=16m'}
                    or limits.get('CapDrop') != ['ALL'] or limits.get('CapAdd')
                    or 'no-new-privileges' not in limits.get('SecurityOpt', [])
                    or set(record['NetworkSettings']['Networks']) != {network}
                    or record['Config']['Entrypoint'] != ['psql']):
                raise RuntimeError('Owned authentication helper admission differs')
            refusal = 'password authentication failed for user "supabase_admin"'
            result = native(['docker', 'start', '--attach', helper], refusal=refusal if index == 2 else None,
                            refusal_line=expected_client_line if index == 2 else None,
                            refusal_cid=helper if index == 2 else None)
            terminal = json.loads(native(['docker', 'inspect', helper]).stdout)[0]['State']
            evidence['auth_helpers'][-1]['terminal_state'] = terminal
            if terminal['Running'] or terminal['ExitCode'] != result.returncode:
                raise RuntimeError('Authentication helper terminal exit differs')
            if index == 1:
                if result.stdout.strip() != 'supabase_admin|postgres':
                    raise RuntimeError('Positive remote authentication response differs')
                evidence['positive_authentication'] = True
            else:
                evidence['negative_authentication'] = True
                evidence['planned_negative_client_control']['runtime_observed'] = True
                evidence['expected_behavior_diagnostics'] = [refusal]
        evidence['after_authentication'] = snapshot()
        if json.dumps(evidence['after_authentication'], sort_keys=True) != json.dumps(post_install, sort_keys=True):
            raise RuntimeError('Authentication changed roles, ownership or libraries')
        evidence['passed'] = True
    except Exception as error:
        first_failure = error
        evidence['first_error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        for helper in reversed(helpers):
            try:
                inspect_args = ['docker', 'container', 'inspect', helper]
                observed = native(inspect_args, cleanup=True, check=False, server_logs=True)
                if observed.returncode:
                    preservation.admit_absence({'exit_code': observed.returncode, 'stdout': observed.stdout.encode(),
                        'stderr': observed.stderr.encode(), 'refusal': None}, inspect_args)
                    evidence['helper_cleanup'].append({'identity': helper, 'passed': True, 'recognized_absence': True})
                    continue
                record = admit_bootstrap_inspection({'exit_code': observed.returncode,
                    'stdout': observed.stdout.encode(), 'stderr': observed.stderr.encode(), 'refusal': None}, inspect_args)
                if (record.get('Name') != '/' + helper or record.get('Image') != startup['image_id']
                        or record.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner
                        or not re.fullmatch(r'[a-f0-9]{64}', record.get('Id', ''))
                        or helper_names[helper] != helper and helper_names[helper] != record['Id']):
                    raise RuntimeError('Authentication cleanup ownership differs')
                result = native(['docker', 'rm', '-f', record['Id']], cleanup=True)
                evidence['helper_cleanup'].append({'identity': helper, 'passed': result.returncode == 0})
            except Exception as error:
                evidence['passed'] = False
                evidence['helper_cleanup'].append({'identity': helper, 'passed': False, 'error': str(error)})
        if first_failure is None and any(not entry['passed'] for entry in evidence['helper_cleanup']):
            raise RuntimeError('Owned authentication helper cleanup failed')


if __name__ == '__main__':
    raise SystemExit(main(probe, bootstrap=True))
