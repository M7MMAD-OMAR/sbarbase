"""Observe actual bounded native cron writes and internal pg_net deliveries."""

import json
import math
from pathlib import Path
import os
import re
import time

from native_defaults_inspect import main


def match_effect_sample(sample, fixture):
    expected = {(r['marker'], r['sequence'], r['kind']) for r in sample['rows']}
    actual = [(r['marker'], r['sequence'], r['kind']) for r in sample['receipts']]
    if any(r['marker'] != fixture or r['kind'] not in ('cron', 'explicit')
           or type(r['sequence']) is not int or not 1 <= r['sequence'] <= 128
           for r in sample['receipts']):
        raise RuntimeError('Unexpected owned HTTP receipt identity')
    if len(set(actual)) != len(actual):
        raise RuntimeError('Duplicate owned HTTP receipt')
    ids = {r['request_id'] for r in sample['rows']}
    if len(ids) != len(sample['rows']):
        raise RuntimeError('Tracked request identity is not unique')
    response_ids = [r['id'] for r in sample['responses']]
    if len(set(response_ids)) != len(response_ids):
        raise RuntimeError('Duplicate tracked native response')
    for response in sample['responses']:
        payload = json.loads(response['content'])
        row = next(r for r in sample['rows'] if r['request_id'] == response['id'])
        if payload != {key: row[key] for key in ('marker', 'sequence', 'kind')}:
            raise RuntimeError('Native response body does not match tracked row')
    receipts_match = expected.issubset(set(actual)) if sample['job']['active'] else set(actual) == expected
    return receipts_match and set(response_ids) == ids and sample['queued'] == 0


def legacy_probe(report, sql, native, container):
    evidence = {'scope': 'owned-native-cron-writes-and-pg-net-delivery', 'passed': False,
                'samples': [], 'limitations': [
                    'Only the owned cron job and tracked requests are observed.',
                    'Bounded absence is not lasting suspension or exactly-once delivery.',
                    'No archive replay, key, role, object or full restore continuity proof.',
                    'Original startup diagnostics remain independently unaccepted.']}
    report['worker_effects'] = evidence
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    image = os.environ['SBARBASE_VERIFY_IMAGE_ID']
    if (not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', image)):
        raise ValueError('Invalid owned helper identity')
    owner = 'sbarbase-fixture-' + fixture
    name, network = fixture + '-worker-http', fixture + '-defaults-net'
    helper = None
    fenced = False
    jobid = None
    maintenance = 'fixture_maintenance'
    kinds = {'pg_cron launcher', 'pg_net 0.20.4 worker'}
    table = 'public.fixture_worker_effects'
    app_oid = None

    def observe(query, database='postgres'):
        return json.loads(sql(query, database))

    def events():
        value = json.loads(native(['docker', 'exec', helper, '/usr/bin/python3', '-c',
                                  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/events', timeout=2).read().decode())"]).stdout)
        if value['marker'] != fixture or len(value['events']) > 128:
            raise RuntimeError('Helper receipts identity or bounds differ')
        return value['events']

    def workers():
        return observe("SELECT coalesce(json_agg(row_to_json(a) ORDER BY backend_type,pid),'[]'::json) "
                       "FROM (SELECT pid,datid,backend_type FROM pg_stat_activity WHERE datid="
                       + str(app_oid) + " AND backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')) a;", maintenance)

    def definition():
        return observe('SELECT row_to_json(j) FROM cron.job j WHERE jobid=' + str(jobid) + ';')

    def snapshot(label):
        sample = observe("SELECT json_build_object("
                         "'rows',(SELECT coalesce(json_agg(row_to_json(t) ORDER BY sequence),'[]'::json) FROM " + table + " t),"
                         "'responses',(SELECT coalesce(json_agg(row_to_json(r) ORDER BY id),'[]'::json) FROM net._http_response r "
                         "WHERE id IN (SELECT request_id FROM " + table + ")),"
                         "'queued',(SELECT count(*) FROM net.http_request_queue WHERE id IN (SELECT request_id FROM " + table + ")),"
                         "'runs',(SELECT coalesce(json_agg(row_to_json(r) ORDER BY runid),'[]'::json) FROM cron.job_run_details r "
                         "WHERE jobid=" + str(jobid) + "),'job',(SELECT row_to_json(j) FROM cron.job j WHERE jobid="
                         + str(jobid) + "),'sql_epoch',extract(epoch FROM clock_timestamp()));")
        sample.update(label=label, epoch=time.time(), receipts=events())
        responses, runs = sample['responses'], sample['runs']
        evidence['samples'].append(sample)
        if any(run['status'] == 'failed' for run in runs):
            raise RuntimeError('Owned cron job failed')
        if any(r['status_code'] != 200 or r['timed_out'] or r['error_msg'] for r in responses):
            raise RuntimeError('Tracked native HTTP response failed')
        return sample

    def matched(sample):
        return match_effect_sample(sample, fixture)

    def wait_effects(label, minimum, timeout):
        deadline = time.monotonic() + timeout
        while True:
            sample = snapshot(label)
            if len(sample['rows']) >= minimum and matched(sample):
                return sample
            if time.monotonic() >= deadline:
                raise RuntimeError(label + ' effects deadline')
            time.sleep(.25)

    def disable_drain(label):
        sql('SELECT cron.alter_job(' + str(jobid) + ', active := false);')
        deadline = time.monotonic() + 10
        stable = None
        while True:
            sample = snapshot(label)
            complete = all(r['status'] == 'succeeded' and r['end_time'] is not None for r in sample['runs'])
            signature = (len(sample['rows']), len(sample['runs']), len(sample['receipts']))
            if complete and matched(sample) and stable == signature:
                return sample
            stable = signature if complete and matched(sample) else None
            if time.monotonic() >= deadline:
                raise RuntimeError(label + ' drain deadline')
            time.sleep(.5)

    try:
        versions = {r['name']: r['default_version'] for r in report['available_extensions']}
        if versions.get('pg_cron') != '1.6.4' or versions.get('pg_net') != '0.20.4':
            raise RuntimeError('Native effects default extension versions differ')
        net = json.loads(native(['docker', 'network', 'inspect', network]).stdout)[0]
        evidence['network'] = net
        if net['Name'] != network or not net['Internal'] or net.get('Labels', {}).get('io.sbarbase.owner') != owner:
            raise RuntimeError('Owned internal network differs')
        helper = native(['docker', 'run', '-d', '--pull=never', '--name', name,
                         '--label', 'io.sbarbase.owner=' + owner, '--network', network,
                         '--memory', '64m', '--memory-swap', '64m', '--cpus', '.25', '--pids-limit', '16',
                         '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL',
                         '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,mode=1777,size=16m',
                         '--env', 'SBARBASE_FIXTURE_ID=' + fixture, '--entrypoint', '/usr/bin/python3',
                         image, '/opt/sbarbase/deploy/verify/native_http_fixture.py']).stdout.strip()
        record = json.loads(native(['docker', 'container', 'inspect', helper]).stdout)[0]
        evidence['helper'] = record
        limits = record['HostConfig']
        if (record['Id'] != helper or record['Name'] != '/' + name or record['Image'] != image
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
                or set(record['NetworkSettings']['Networks']) != {network}):
            raise RuntimeError('Owned HTTP helper admission differs')
        deadline = time.monotonic() + 10
        while True:
            result = native(['docker', 'exec', helper, '/usr/bin/python3', '-c',
                             "import socket; s=socket.socket(); s.settimeout(1); r=s.connect_ex(('127.0.0.1',8080)); s.close(); raise SystemExit(r != 0)"], check=False)
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Owned helper readiness deadline')
            time.sleep(.2)
        sql('CREATE EXTENSION pg_cron; CREATE EXTENSION pg_net;')
        installed = observe("SELECT json_object_agg(extname,extversion) FROM pg_extension WHERE extname IN ('pg_cron','pg_net');")
        evidence['installed_versions'] = installed
        if installed != {'pg_cron': '1.6.4', 'pg_net': '0.20.4'}:
            raise RuntimeError('Installed native effects versions differ')
        app_oid = int(sql("SELECT oid FROM pg_database WHERE datname='postgres';"))
        evidence['application_oid'] = app_oid
        sql('CREATE DATABASE fixture_maintenance TEMPLATE template0;')
        maintenance_oid = int(sql("SELECT oid FROM pg_database WHERE datname='fixture_maintenance';", maintenance))
        if maintenance_oid == app_oid:
            raise RuntimeError('Maintenance database identity equals application')
        evidence['maintenance_oid'] = maintenance_oid
        sql('CREATE TABLE ' + table + '(sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, '
            'marker text NOT NULL, kind text NOT NULL, request_id bigint NOT NULL UNIQUE);')
        url = 'http://' + name + ':8080/fixture'
        command = ("INSERT INTO " + table + "(sequence,marker,kind,request_id) OVERRIDING SYSTEM VALUE SELECT s.sequence,'"
                   + fixture + "','cron',net.http_post(url := '" + url + "',body := jsonb_build_object('marker','"
                   + fixture + "','sequence',s.sequence,'kind','cron'),timeout_milliseconds := 2000) "
                   "FROM (SELECT nextval(pg_get_serial_sequence('" + table + "','sequence')) AS sequence) s;")
        jobid = int(sql("SELECT cron.schedule('" + fixture + "-effects','1 second',$job$" + command + '$job$);'))
        original = definition()
        evidence['original_job'] = original
        wait_effects('before-fence', 2, 10)
        drained = disable_drain('disabled-drained')
        disabled = dict(original, active=False)
        if drained['job'] != disabled:
            raise RuntimeError('Disabled full native job definition differs')
        before = workers()
        evidence['workers_before'] = before
        if len(before) != 2 or {w['backend_type'] for w in before} != kinds:
            raise RuntimeError('Original native worker pair differs')
        if int(sql("SELECT oid FROM pg_database WHERE datname='postgres';", maintenance)) != app_oid:
            raise RuntimeError('Application identity changed before fence')
        sql('ALTER DATABASE postgres ALLOW_CONNECTIONS false;', maintenance)
        fenced = True
        attached = workers()
        evidence['workers_after_fence'] = attached
        if attached != before:
            raise RuntimeError('Inventoried worker pair changed before drain')
        for worker in attached:
            value = sql('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid=' + str(worker['pid'])
                        + ' AND datid=' + str(app_oid) + " AND backend_type='" + worker['backend_type'] + "';", maintenance)
            if value != 't':
                raise RuntimeError('Exact inventoried worker termination failed')
        fence_samples = []
        start = time.monotonic()
        absent = False
        while time.monotonic() - start < 4:
            attached = workers()
            receipt = events()
            fence_samples.append({'epoch': time.time(), 'elapsed': time.monotonic() - start,
                                  'workers': attached, 'receipts': receipt})
            if not attached:
                absent = True
            elif absent:
                raise RuntimeError('Worker reattached during bounded application fence')
            if receipt != drained['receipts']:
                raise RuntimeError('HTTP receipts changed during bounded application fence')
            time.sleep(.25)
        evidence['fence_samples'] = fence_samples
        if not absent or fence_samples[-1]['workers'] or fence_samples[-1]['elapsed'] < 3.5:
            raise RuntimeError('Bounded worker absence was not observed')
        if int(sql("SELECT oid FROM pg_database WHERE datname='postgres';", maintenance)) != app_oid:
            raise RuntimeError('Application identity changed before reopen')
        sql('ALTER DATABASE postgres ALLOW_CONNECTIONS true;', maintenance)
        fenced = False
        deadline = time.monotonic() + 8
        while True:
            after = workers()
            if len(after) == 2 and {w['backend_type'] for w in after} == kinds:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Native worker reopen deadline')
            time.sleep(.25)
        if {w['pid'] for w in after} & {w['pid'] for w in before}:
            raise RuntimeError('Reopened workers retain prior process identities')
        evidence['workers_after_reopen'] = after
        reopened = snapshot('reopened-disabled')
        if (reopened['rows'] != drained['rows'] or reopened['receipts'] != drained['receipts']
                or reopened['job'] != disabled or not matched(reopened)):
            raise RuntimeError('Disabled contents or full job definition changed across fence')
        sql('SELECT cron.alter_job(' + str(jobid) + ', active := true);')
        if definition() != original:
            raise RuntimeError('Restored full native job definition differs')
        wait_effects('resumed', len(drained['rows']) + 2, 8)
        final = disable_drain('final-disabled-drained')
        explicit = ("INSERT INTO " + table + "(sequence,marker,kind,request_id) OVERRIDING SYSTEM VALUE SELECT s.sequence,'"
                    + fixture + "','explicit',net.http_post(url := '" + url + "',body := jsonb_build_object('marker','"
                    + fixture + "','sequence',s.sequence,'kind','explicit'),timeout_milliseconds := 2000) "
                    "FROM (SELECT nextval(pg_get_serial_sequence('" + table + "','sequence')) AS sequence) s;")
        sql(explicit)
        delivered = wait_effects('explicit-after-reopen', len(final['rows']) + 1, 10)
        if delivered['job'] != disabled or delivered['rows'][-1]['kind'] != 'explicit':
            raise RuntimeError('Final disabled job or explicit delivery differs')
        evidence['passed'] = True
    except Exception as error:
        evidence['passed'] = False
        evidence['error'] = type(error).__name__ + ': ' + str(error)
        if jobid is not None and not fenced:
            try:
                snapshot('failure-diagnostics')
            except Exception as diagnostic:
                evidence['failure_diagnostic_error'] = str(diagnostic)
            try:
                sql('SELECT cron.alter_job(' + str(jobid) + ', active := false);')
            except Exception as diagnostic:
                evidence['failure_disable_error'] = str(diagnostic)
        raise
    finally:
        if fenced:
            try:
                sql('ALTER DATABASE postgres ALLOW_CONNECTIONS true;', maintenance)
                evidence['failure_reopen'] = True
            except Exception as error:
                evidence['failure_reopen_error'] = str(error)
        if helper:
            try:
                logs = native(['docker', 'logs', helper], server_logs=True)
                evidence['helper_output'] = logs.stdout + logs.stderr
                if evidence['helper_output'].strip():
                    evidence['passed'] = False
                    raise RuntimeError('Unexpected helper diagnostic output')
            finally:
                try:
                    native(['docker', 'rm', '-f', helper])
                    absent = native(['docker', 'container', 'ls', '-aq', '--no-trunc', '--filter', 'id=' + helper]).stdout.strip()
                    evidence['helper_cleanup'] = {'id': helper, 'passed': not absent}
                    if absent:
                        raise RuntimeError('Owned helper remains after cleanup')
                except Exception as error:
                    evidence['passed'] = False
                    evidence['helper_cleanup'] = {'id': helper, 'passed': False, 'error': str(error)}
                    raise



PROJECTION_LIMITS = {'namespaces': 128, 'relations': 1024, 'routines': 4096, 'extensions': 128, 'default_acls': 512, 'event_triggers': 64}
TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')
ROUTINES = {
    'schedule(text,text)': (True, 'bigint', 'cron_schedule', 0, None),
    'schedule(text,text,text)': (False, 'bigint', 'cron_schedule_named', 0, None),
    'unschedule(bigint)': (True, 'boolean', 'cron_unschedule', 0, None),
    'unschedule(text)': (True, 'boolean', 'cron_unschedule_named', 0, None),
    'job_cache_invalidate()': (False, 'trigger', 'cron_job_cache_invalidate', 0, None),
    'alter_job(bigint,text,text,text,text,boolean)': (False, 'void', 'cron_alter_job', 5, 'NULL::text, NULL::text, NULL::text, NULL::text, NULL::boolean'),
    'schedule_in_database(text,text,text,text,text,boolean)': (False, 'bigint', 'cron_schedule_named', 2, 'NULL::text, true'),
}
ROUTINE_ARGUMENT_IDENTITIES = {
    'schedule(text,text)': 'schedule text, command text',
    'schedule(text,text,text)': 'job_name text, schedule text, command text',
    'unschedule(bigint)': 'job_id bigint', 'unschedule(text)': 'job_name text',
    'job_cache_invalidate()': '',
    'alter_job(bigint,text,text,text,text,boolean)': 'job_id bigint, schedule text, command text, database text, username text, active boolean',
    'schedule_in_database(text,text,text,text,text,boolean)': 'job_name text, schedule text, command text, database text, username text, active boolean',
}
CRON_RELATIONS = {'job': 'r', 'job_run_details': 'r', 'jobid_seq': 'S', 'runid_seq': 'S',
                  'job_pkey': 'i', 'job_run_details_pkey': 'i', 'jobname_username_uniq': 'i'}

# One statement, one MVCC snapshot, no LIMIT or opaque routine bodies.
PROJECTION_SQL = r"""WITH owner AS (SELECT oid FROM pg_roles WHERE rolname='supabase_admin'),
ns AS (SELECT n.oid::text AS oid,n.nspname AS name,n.nspowner::text AS owner_oid,pg_get_userbyid(n.nspowner) AS owner,
 n.nspacl AS acl_raw,CASE WHEN cardinality(n.nspacl)=0 THEN '[]'::json ELSE (SELECT coalesce(json_agg(row_to_json(a) ORDER BY grantor,grantee,privilege_type,is_grantable),'[]'::json)
 FROM (SELECT grantor::text,grantee::text,privilege_type,is_grantable FROM aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner)))) a) END AS acl,
 (SELECT coalesce(json_agg(e.extname ORDER BY e.extname),'[]'::json) FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
 WHERE d.classid='pg_namespace'::regclass AND d.objid=n.oid AND d.deptype='e') AS extensions FROM pg_namespace n),
rels AS (SELECT c.oid::text AS oid,c.relnamespace::text AS namespace_oid,n.nspname AS namespace,c.relname AS name,
 c.relkind::text AS kind,c.relpersistence::text AS persistence,c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,c.relowner::text AS owner_oid,pg_get_userbyid(c.relowner) AS owner,c.relacl AS acl_raw,
 CASE WHEN c.relkind IN ('i','I') OR cardinality(c.relacl)=0 THEN '[]'::json ELSE
 (SELECT coalesce(json_agg(row_to_json(a) ORDER BY grantor,grantee,privilege_type,is_grantable),'[]'::json)
 FROM (SELECT grantor::text,grantee::text,privilege_type,is_grantable FROM aclexplode(
 coalesce(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char" END,c.relowner)))) a) END AS acl,
 i.indrelid::text AS parent_oid,CASE WHEN i.indexrelid IS NOT NULL THEN pg_get_indexdef(c.oid) END AS index_definition,
 (SELECT coalesce(json_agg(e.extname ORDER BY e.extname),'[]'::json) FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
 WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e') AS extensions
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_index i ON i.indexrelid=c.oid
 WHERE n.nspname !~ '^pg_(catalog|toast|temp_)'),
funcs AS (SELECT p.oid::text AS oid,p.pronamespace::text AS namespace_oid,n.nspname AS namespace,p.proname AS name,
 pg_get_function_identity_arguments(p.oid) AS identity_arguments,oidvectortypes(p.proargtypes) AS argument_types,
 format_type(p.prorettype,NULL) AS return_type,l.lanname AS language,p.prokind::text AS kind,p.proisstrict AS strict,p.provolatile::text AS volatility,
 p.prosecdef AS security_definer,p.proparallel::text AS parallel,p.proconfig AS configuration,p.provariadic::text AS variadic,
 p.pronargdefaults AS default_count,pg_get_expr(p.proargdefaults,0) AS defaults,p.proretset AS returns_set,
 p.proowner::text AS owner_oid,pg_get_userbyid(p.proowner) AS owner,p.proacl AS acl_raw,
 CASE WHEN l.lanname='c' THEN p.probin END AS library,CASE WHEN l.lanname='c' THEN p.prosrc END AS c_symbol,
 CASE WHEN cardinality(p.proacl)=0 THEN '[]'::json ELSE (SELECT coalesce(json_agg(row_to_json(a) ORDER BY grantor,grantee,privilege_type,is_grantable),'[]'::json)
 FROM (SELECT grantor::text,grantee::text,privilege_type,is_grantable FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner)))) a) END AS acl,
 (SELECT coalesce(json_agg(e.extname ORDER BY e.extname),'[]'::json) FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
 WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') AS extensions
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
 WHERE n.nspname !~ '^pg_(catalog|toast|temp_)'),
exts AS (SELECT e.oid::text AS oid,e.extname AS name,e.extowner::text AS owner_oid,pg_get_userbyid(e.extowner) AS owner,
 e.extnamespace::text AS namespace_oid,n.nspname AS namespace,e.extversion AS version,e.extrelocatable AS relocatable,
 CASE WHEN e.extconfig IS NULL THEN NULL ELSE ARRAY(SELECT x::text FROM unnest(e.extconfig) x) END AS configuration,e.extcondition AS conditions FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace),
defs AS (SELECT d.oid::text AS oid,d.defaclrole::text AS role_oid,d.defaclnamespace::text AS namespace_oid,d.defaclobjtype::text AS kind,d.defaclacl AS acl_raw,
 CASE WHEN cardinality(d.defaclacl)=0 THEN '[]'::json ELSE (SELECT coalesce(json_agg(row_to_json(a) ORDER BY grantor,grantee,privilege_type,is_grantable),'[]'::json)
 FROM (SELECT grantor::text,grantee::text,privilege_type,is_grantable FROM aclexplode(d.defaclacl)) a) END AS acl FROM pg_default_acl d),
creation AS (SELECT k.kind,d.defaclacl AS global_acl_raw,
 CASE WHEN cardinality(d.defaclacl)=0 THEN '[]'::json ELSE (SELECT coalesce(json_agg(row_to_json(a) ORDER BY grantor,grantee,privilege_type,is_grantable),'[]'::json)
 FROM (SELECT grantor::text,grantee::text,privilege_type,is_grantable FROM aclexplode(coalesce(d.defaclacl,acldefault(CASE WHEN k.kind='S' THEN 's'::"char" ELSE k.kind END,owner.oid)))) a) END AS acl
 FROM owner CROSS JOIN (VALUES ('n'::"char"),('r'::"char"),('S'::"char"),('f'::"char")) k(kind)
 LEFT JOIN pg_default_acl d ON d.defaclrole=owner.oid AND d.defaclnamespace=0 AND d.defaclobjtype=k.kind)
SELECT json_build_object(
 'namespaces',(SELECT coalesce(json_agg(row_to_json(n) ORDER BY name),'[]'::json) FROM ns n),
 'relations',(SELECT coalesce(json_agg(row_to_json(r) ORDER BY namespace,name),'[]'::json) FROM rels r),
 'routines',(SELECT coalesce(json_agg(row_to_json(f) ORDER BY namespace,name,argument_types),'[]'::json) FROM funcs f),
 'extensions',(SELECT coalesce(json_agg(row_to_json(e) ORDER BY name),'[]'::json) FROM exts e),
 'default_acls',(SELECT coalesce(json_agg(row_to_json(d) ORDER BY role_oid,namespace_oid,kind),'[]'::json) FROM defs d),
 'event_triggers',(SELECT coalesce(json_agg(row_to_json(t) ORDER BY name),'[]'::json) FROM (SELECT oid::text AS oid,evtname AS name,evtevent AS event,evtowner::text AS owner_oid,evtenabled::text AS enabled,evtfoid::text AS function_oid,evttags AS tags FROM pg_event_trigger) t),
 'creation_defaults',(SELECT json_agg(row_to_json(c) ORDER BY kind) FROM creation c));"""


def exact(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def positive_oid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*', value) or int(value) > 4294967295:
        raise RuntimeError('Projected PostgreSQL OID differs')
    return value


def projection_rows(projection):
    if set(projection) != set(PROJECTION_LIMITS) | {'creation_defaults'}:
        raise RuntimeError('Metadata projection fields differ')
    if len(json.dumps(projection, ensure_ascii=True).encode()) > 1024 * 1024:
        raise RuntimeError('Metadata projection byte bound exceeded')
    result = {}
    for kind, limit in PROJECTION_LIMITS.items():
        rows = projection[kind]
        if not isinstance(rows, list) or len(rows) > limit:
            raise RuntimeError('Metadata projection row bound exceeded: ' + kind)
        indexed = {}
        for row in rows:
            oid = positive_oid(row['oid'])
            if oid in indexed:
                raise RuntimeError('Duplicate projected catalog OID')
            indexed[oid] = row
        result[kind] = indexed
    return result


def acl_set(rows):
    values = []
    for grant in rows:
        if (set(grant) != {'grantor', 'grantee', 'privilege_type', 'is_grantable'}
                or not isinstance(grant['grantor'], str) or not isinstance(grant['grantee'], str)
                or type(grant['is_grantable']) is not bool):
            raise RuntimeError('Projected canonical ACL differs')
        values.append((grant['grantor'], grant['grantee'], grant['privilege_type'], grant['is_grantable']))
    if len(set(values)) != len(values):
        raise RuntimeError('Duplicate canonical ACL grant')
    return set(values)


ACL_CODES = {'n': {'U': 'USAGE', 'C': 'CREATE'},
             'r': {'a': 'INSERT', 'r': 'SELECT', 'w': 'UPDATE', 'd': 'DELETE', 'D': 'TRUNCATE', 'x': 'REFERENCES', 't': 'TRIGGER', 'm': 'MAINTAIN'},
             'S': {'r': 'SELECT', 'w': 'UPDATE', 'U': 'USAGE'}, 'f': {'X': 'EXECUTE'}}


def native_acl_raw(raw, kind, role_oids):
    if not isinstance(raw, list):
        raise RuntimeError('Native non-null ACL array differs')
    grants = set()
    for entry in raw:
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*|)=([A-Za-z*]*)/([A-Za-z_][A-Za-z0-9_]*)', entry)
        if not match:
            raise RuntimeError('Unrecognized native ACL identifier framing')
        grantee_name, privileges, grantor_name = match.groups()
        grantee = role_oids.get(grantee_name) if grantee_name else '0'
        grantor = role_oids.get(grantor_name)
        if grantee is None or grantor is None:
            raise RuntimeError('Native ACL role identity unavailable')
        offset = 0
        while offset < len(privileges):
            code = privileges[offset]
            offset += 1
            if code not in ACL_CODES[kind]:
                raise RuntimeError('Native ACL privilege code differs')
            grantable = offset < len(privileges) and privileges[offset] == '*'
            offset += int(grantable)
            grant = (grantor, grantee, ACL_CODES[kind][code], grantable)
            if grant in grants:
                raise RuntimeError('Native ACL duplicate privilege differs')
            grants.add(grant)
    return grants


def admit_cron_setup(before, after, identity, *, newly_installed):
    old, new = projection_rows(before), projection_rows(after)
    if not exact(before['creation_defaults'], after['creation_defaults']):
        raise RuntimeError('Original global creation defaults changed')
    additions = {}
    for kind in old:
        if any(oid not in new[kind] or not exact(row, new[kind][oid]) for oid, row in old[kind].items()):
            raise RuntimeError('Original projected metadata changed: ' + kind)
        additions[kind] = [row for oid, row in new[kind].items() if oid not in old[kind]]
    if not newly_installed:
        if any(additions.values()) or not exact(before, after):
            raise RuntimeError('Existing pg_cron introduced a setup delta')
        # Validate its full same closed surface by temporarily removing only cron rows.
        base = dict(after)
        for kind in PROJECTION_LIMITS:
            base[kind] = [r for r in after[kind] if not (r.get('namespace') == 'cron' or kind == 'namespaces' and r['name'] == 'cron'
                         or kind == 'extensions' and r['name'] == 'pg_cron')]
        cron_ns = next((r['oid'] for r in after['namespaces'] if r['name'] == 'cron'), None)
        base['default_acls'] = [r for r in after['default_acls'] if r['namespace_oid'] != cron_ns]
        return admit_cron_setup(base, after, identity, newly_installed=True)
    role_oids = {r['rolname']: positive_oid(str(r['oid'])) for r in identity['roles']}
    if len(role_oids) != len(identity['roles']) or len(set(role_oids.values())) != len(role_oids):
        raise RuntimeError('Accepted role identities are not unique')
    admin, postgres = role_oids['supabase_admin'], role_oids['postgres']
    if additions['event_triggers']:
        raise RuntimeError('Cron setup added an event trigger')
    namespaces = additions['namespaces']
    if len(namespaces) != 1 or namespaces[0]['name'] != 'cron':
        raise RuntimeError('Closed cron namespace differs')
    namespace = namespaces[0]
    cron_oid = namespace['oid']
    relations = additions['relations']
    by_name = {r['name']: r for r in relations}
    if len(relations) != 7 or set(by_name) != set(CRON_RELATIONS):
        raise RuntimeError('Closed cron relation surface differs')
    defaults = {d['kind']: d for d in before['creation_defaults']}
    if set(defaults) != {'n', 'r', 'S', 'f'} or len(before['creation_defaults']) != 4:
        raise RuntimeError('Creation-default baseline differs')

    for kind, value in defaults.items():
        hardwired = {(admin, admin, p, False) for p in ACL_CODES[kind].values()}
        if kind == 'f':
            hardwired.add((admin, '0', 'EXECUTE', False))
        raw = value['global_acl_raw']
        expected_creation = hardwired if raw is None else native_acl_raw(raw, kind, role_oids)
        if acl_set(value['acl']) != expected_creation:
            raise RuntimeError('Original creation ACL representation differs')

    def owner(row):
        if row['owner'] != 'supabase_admin' or row['owner_oid'] != admin:
            raise RuntimeError('Projected cron owner differs')

    def grants(row, kind, additions=(), remove_postgres=False, remove_public=False, allow_null=False):
        expected = acl_set(defaults[kind]['acl'])
        if remove_postgres:
            expected = {g for g in expected if g[1] != postgres}
        if remove_public:
            expected = {g for g in expected if g[1] != '0'}
        for grantee, privilege, grantable in additions:
            expected.discard((admin, grantee, privilege, False))
            expected.discard((admin, grantee, privilege, True))
            expected.add((admin, grantee, privilege, grantable))
        if (acl_set(row['acl']) != expected or (row['acl_raw'] is None and not allow_null)
                or row['acl_raw'] is not None and native_acl_raw(row['acl_raw'], kind, role_oids) != expected):
            raise RuntimeError('Closed source-declared cron ACL differs')

    owner(namespace)
    if namespace['extensions'] != ['pg_cron']:
        raise RuntimeError('Cron namespace direct membership differs')
    grants(namespace, 'n', [(postgres, 'USAGE', True)])
    for name, row in by_name.items():
        owner(row)
        if (row['namespace'] != 'cron' or row['namespace_oid'] != cron_oid or row['kind'] != CRON_RELATIONS[name] or row['persistence'] != 'p'
                or row['rls'] is not (row['kind'] == 'r') or row['force_rls'] is not False):
            raise RuntimeError('Closed cron relation identity differs')
        if row['kind'] == 'i':
            parent, columns = {'job_pkey': ('job', 'jobid'), 'job_run_details_pkey': ('job_run_details', 'runid'),
                               'jobname_username_uniq': ('job', 'jobname, username')}[name]
            definition = 'CREATE UNIQUE INDEX ' + name + ' ON cron.' + parent + ' USING btree (' + columns + ')'
            if (row['parent_oid'] != by_name[parent]['oid'] or row['index_definition'] != definition
                    or row['extensions'] or row['acl_raw'] is not None or row['acl']):
                raise RuntimeError('Cron index parent/definition differs')
        else:
            if row['parent_oid'] is not None or row['index_definition'] is not None or row['extensions'] != ['pg_cron']:
                raise RuntimeError('Cron relation direct membership differs')
            if row['kind'] == 'S':
                grants(row, 'S', [('0', 'SELECT', False)])
            elif name == 'job':
                grants(row, 'r', [('0', 'SELECT', False), (postgres, 'SELECT', True)], remove_postgres=True)
            else:
                grants(row, 'r', [('0', 'SELECT', False), ('0', 'DELETE', False)] + [(postgres, p, True) for p in TABLE_PRIVILEGES if p != 'TRIGGER'], remove_postgres=True)
    routines = additions['routines']
    signatures = {r['name'] + '(' + r['argument_types'].replace(' ', '') + ')': r for r in routines}
    if len(routines) != 7 or set(signatures) != set(ROUTINES):
        raise RuntimeError('Closed cron routine surface differs')
    for signature, row in signatures.items():
        owner(row)
        strict, returns, symbol, default_count, default_text = ROUTINES[signature]
        if (row['namespace'] != 'cron' or row['namespace_oid'] != cron_oid or row['language'] != 'c'
                or row['kind'] != 'f' or row['identity_arguments'] != ROUTINE_ARGUMENT_IDENTITIES[signature]
                or row['strict'] is not strict or row['return_type'] != returns or row['volatility'] != 'v'
                or row['security_definer'] is not False or row['parallel'] != 'u' or row['configuration'] is not None
                or row['variadic'] != '0' or row['returns_set'] is not False or row['default_count'] != default_count
                or row['defaults'] != default_text or row['library'] != '$libdir/pg_cron' or row['c_symbol'] != symbol
                or row['extensions'] != ['pg_cron']):
            raise RuntimeError('Closed cron routine metadata differs')
        restricted = row['name'] in ('alter_job', 'schedule_in_database')
        grants(row, 'f', remove_public=restricted, allow_null=not restricted and defaults['f']['global_acl_raw'] is None)
    extensions = additions['extensions']
    if len(extensions) != 1:
        raise RuntimeError('Closed cron extension addition differs')
    extension = extensions[0]
    owner(extension)
    pg_catalog_oid = next(n['oid'] for n in before['namespaces'] if n['name'] == 'pg_catalog')
    expected_config = [by_name[n]['oid'] for n in ('job', 'jobid_seq', 'job_run_details', 'runid_seq')]
    if (extension['name'] != 'pg_cron' or extension['version'] != '1.6.4' or extension['namespace'] != 'pg_catalog'
            or extension['namespace_oid'] != pg_catalog_oid or extension['relocatable'] is not False
            or extension['configuration'] != expected_config or extension['conditions'] != ['', '', '', '']):
        raise RuntimeError('Pinned native pg_cron extension metadata differs')
    default_acls = additions['default_acls']
    if len(default_acls) != 3 or {d['kind'] for d in default_acls} != {'r', 'f', 'S'}:
        raise RuntimeError('Closed per-cron default ACL keys differ')
    for row in default_acls:
        privileges = {'r': TABLE_PRIVILEGES, 'f': ('EXECUTE',), 'S': ('SELECT', 'UPDATE', 'USAGE')}[row['kind']]
        expected = {(admin, postgres, p, True) for p in privileges}
        if (row['role_oid'] != admin or row['namespace_oid'] != cron_oid or row['acl_raw'] is None or acl_set(row['acl']) != expected
                or native_acl_raw(row['acl_raw'], row['kind'], role_oids) != expected):
            raise RuntimeError('Closed per-cron default ACL grants differ')
    hook = [r for r in before['routines'] if r['name'] == 'grant_pg_cron_access' and r['namespace'] == 'extensions']
    if len(hook) != 1 or hook[0]['security_definer'] is not False or hook[0]['configuration'] is not None:
        raise RuntimeError('Original native cron hook execution context differs')
    return {'cron_namespace_oid': cron_oid, 'extension_oid': extension['oid'],
            'relation_oids': {name: row['oid'] for name, row in by_name.items()},
            'routine_oids': {sig: row['oid'] for sig, row in signatures.items()},
            'default_acl_oids': {row['kind']: row['oid'] for row in default_acls}}


def strict_effect_sample(sample, fixture):
    for key in ('rows', 'responses', 'receipts', 'runs'):
        if not isinstance(sample[key], list) or len(sample[key]) > 128:
            raise RuntimeError('Owned effect sample bound differs')
    if type(sample['queued']) is not int or sample['queued'] < 0 or type(sample['job']['active']) is not bool:
        raise RuntimeError('Owned queue/job state differs')
    sequences, requests = set(), set()
    for row in sample['rows']:
        if (set(row) != {'sequence', 'marker', 'kind', 'request_id'} or row['marker'] != fixture
                or row['kind'] not in ('cron', 'explicit') or type(row['sequence']) is not int
                or not 1 <= row['sequence'] <= 128 or type(row['request_id']) is not int or row['request_id'] <= 0
                or row['sequence'] in sequences or row['request_id'] in requests):
            raise RuntimeError('Owned row identity differs')
        sequences.add(row['sequence'])
        requests.add(row['request_id'])
    for receipt in sample['receipts']:
        if (set(receipt) != {'marker', 'sequence', 'kind', 'received_epoch'}
                or type(receipt['received_epoch']) not in (int, float) or not math.isfinite(receipt['received_epoch'])
                or receipt['received_epoch'] <= 0):
            raise RuntimeError('Owned HTTP receipt shape differs')
    runids = set()
    for run in sample['runs']:
        if (type(run['runid']) is not int or run['runid'] <= 0 or run['runid'] in runids
                or run['jobid'] != sample['job']['jobid'] or run['status'] not in ('starting', 'running', 'succeeded')):
            raise RuntimeError('Owned cron run identity/status differs')
        runids.add(run['runid'])
    for response in sample['responses']:
        payload = json.loads(response['content'])
        if (not isinstance(payload, dict) or set(payload) != {'marker', 'sequence', 'kind'}
                or type(payload['sequence']) is not int or type(response['id']) is not int):
            raise RuntimeError('Tracked native response payload shape differs')
        if (response['id'] not in requests or response['status_code'] != 200 or response['timed_out']
                or response['error_msg']):
            raise RuntimeError('Tracked native response identity/status differs')
    matched = match_effect_sample(sample, fixture)
    return matched and all(r['status'] == 'succeeded' and r['end_time'] is not None for r in sample['runs'])


def stable_effect_signature(sample):
    return json.dumps({key: sample[key] for key in ('rows', 'responses', 'receipts', 'runs', 'job', 'queued')}, sort_keys=True)


def admit_http_helper(record, cid, name, owner, image, network, *, running):
    config, limits = record['Config'], record['HostConfig']
    if (not re.fullmatch(r'[a-f0-9]{64}', cid) or record['Id'] != cid or record['Name'] != '/' + name
            or record['Image'] != image or config.get('Labels', {}).get('io.sbarbase.owner') != owner
            or config.get('User') != '10001:10001' or config.get('Entrypoint') != ['/usr/bin/python3']
            or config.get('Cmd') != ['/opt/sbarbase/deploy/verify/native_http_fixture.py'] or config.get('Volumes')
            or not limits.get('ReadonlyRootfs') or limits.get('Memory') != 67108864 or limits.get('MemorySwap') != 67108864
            or limits.get('NanoCpus') != 250000000 or limits.get('PidsLimit') != 16
            or limits.get('Privileged') or limits.get('Binds') or limits.get('PidMode') or limits.get('UTSMode')
            or limits.get('IpcMode') != 'private' or limits.get('NetworkMode') != network
            or limits.get('PortBindings') or limits.get('PublishAllPorts') or limits.get('Devices') or limits.get('DeviceRequests')
            or limits.get('VolumesFrom') or limits.get('CapDrop') != ['ALL'] or limits.get('CapAdd')
            or limits.get('SecurityOpt') != ['no-new-privileges']
            or limits.get('Tmpfs') != {'/tmp': 'rw,mode=1777,size=16m'}
            or limits.get('RestartPolicy') != {'Name': 'no', 'MaximumRetryCount': 0} or limits.get('AutoRemove')
            or record['Mounts'] or set(record['NetworkSettings']['Networks']) != {network}
            or any((record['NetworkSettings'].get('Ports') or {}).values())
            or record['State'].get('OOMKilled') or record['State'].get('Error')
            or (running is not None and record['State'].get('Running') is not running)
            or (running is False and record['State'].get('Status') != 'created')):
        raise RuntimeError('Configured owned HTTP helper identity/isolation differs')


OWNED_TABLE_SQL = r"""SELECT json_build_object(
 'table',(SELECT json_build_object('oid',c.oid::text,'owner',pg_get_userbyid(c.relowner),'owner_oid',c.relowner::text,'namespace',n.nspname,'name',c.relname,'kind',c.relkind,'persistence',c.relpersistence,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity)
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass('public.fixture_worker_effects')),
 'columns',(SELECT json_agg(json_build_object('name',a.attname,'type',format_type(a.atttypid,a.atttypmod),'not_null',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated) ORDER BY a.attnum)
 FROM pg_attribute a WHERE a.attrelid=to_regclass('public.fixture_worker_effects') AND a.attnum>0 AND NOT a.attisdropped),
 'sequence',(SELECT json_build_object('oid',c.oid::text,'name',c.relname,'owner',pg_get_userbyid(c.relowner),'owner_oid',c.relowner::text,'namespace',n.nspname,'kind',c.relkind,'persistence',c.relpersistence,
 'parent_oid',d.refobjid::text,'parent_column',d.refobjsubid,'dependency',d.deptype,'type',format_type(s.seqtypid,NULL),'increment',s.seqincrement,'min',s.seqmin,'max',s.seqmax,'start',s.seqstart,'cache',s.seqcache,'cycle',s.seqcycle)
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_sequence s ON s.seqrelid=c.oid
 JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=c.oid AND d.refclassid='pg_class'::regclass AND d.deptype='i'
 WHERE c.oid=to_regclass(pg_get_serial_sequence('public.fixture_worker_effects','sequence'))));"""


def admit_owned_table(value):
    table, sequence = value['table'], value['sequence']
    if (not table or table != {'oid': positive_oid(table['oid']), 'owner': 'supabase_admin', 'owner_oid': positive_oid(table['owner_oid']), 'namespace': 'public',
                             'name': 'fixture_worker_effects', 'kind': 'r', 'persistence': 'p', 'rls': True, 'force_rls': False}):
        raise RuntimeError('Owned effects table identity differs')
    columns = [{'name': n, 'type': t, 'not_null': True, 'identity': 'a' if n == 'sequence' else '', 'generated': ''}
               for n, t in (('sequence', 'bigint'), ('marker', 'text'), ('kind', 'text'), ('request_id', 'bigint'))]
    expected = {'oid': positive_oid(sequence['oid']), 'name': 'fixture_worker_effects_sequence_seq', 'owner': 'supabase_admin', 'owner_oid': table['owner_oid'],
                'namespace': 'public', 'kind': 'S', 'persistence': 'p', 'parent_oid': table['oid'], 'parent_column': 1,
                'dependency': 'i', 'type': 'bigint', 'increment': 1, 'min': 1, 'max': 9223372036854775807,
                'start': 1, 'cache': 1, 'cycle': False}
    if not exact(value['columns'], columns) or not exact(sequence, expected) or sequence['oid'] == table['oid']:
        raise RuntimeError('Owned table/identity-sequence definition differs')
    return value


def admit_owned_projection(baseline, current, binding, owned_baseline=None, *, absent=False):
    old, now = projection_rows(baseline), projection_rows(current)
    if not exact(baseline['creation_defaults'], current['creation_defaults']):
        raise RuntimeError('Original global defaults changed during effects')
    extra = {}
    for kind in old:
        if any(oid not in now[kind] or not exact(row, now[kind][oid]) for oid, row in old[kind].items()):
            raise RuntimeError('Original projected row changed during effects: ' + kind)
        extra[kind] = [r for oid, r in now[kind].items() if oid not in old[kind]]
    if absent:
        if any(extra.values()) or not exact(baseline, current):
            raise RuntimeError('Owned effects left projected metadata residue')
        return None
    if any(extra[k] for k in extra if k != 'relations'):
        raise RuntimeError('Unexpected owned phase projected addition')
    rows = extra['relations']
    by_name = {r['name']: r for r in rows}
    expected = {'fixture_worker_effects': 'r', 'fixture_worker_effects_sequence_seq': 'S',
                'fixture_worker_effects_pkey': 'i', 'fixture_worker_effects_request_id_key': 'i'}
    if len(rows) != 4 or set(by_name) != set(expected):
        raise RuntimeError('Owned table/sequence/two-index projection differs')
    table_oid = binding['table']['oid']
    public_oid = next(r['oid'] for r in baseline['namespaces'] if r['name'] == 'public')
    for name, row in by_name.items():
        if (row['namespace'] != 'public' or row['namespace_oid'] != public_oid
                or row['owner'] != 'supabase_admin' or row['owner_oid'] != binding['table']['owner_oid'] or row['kind'] != expected[name]
                or row['persistence'] != 'p' or row['extensions']
                or row['rls'] is not (row['kind'] == 'r') or row['force_rls'] is not False):
            raise RuntimeError('Owned projected relation identity differs')
        if row['kind'] == 'i':
            column = 'sequence' if name.endswith('_pkey') else 'request_id'
            if row['parent_oid'] != table_oid or row['index_definition'] != 'CREATE UNIQUE INDEX ' + name + ' ON public.fixture_worker_effects USING btree (' + column + ')':
                raise RuntimeError('Owned index identity/definition differs')
        elif row['oid'] != binding['table' if row['kind'] == 'r' else 'sequence']['oid'] or row['parent_oid'] is not None:
            raise RuntimeError('Owned table/sequence OID differs')
    if owned_baseline is not None and not exact(rows, owned_baseline):
        raise RuntimeError('Owned effects metadata identity changed')
    return rows


def configured_probe(report, sql, native, container):
    from native_bootstrap_inspect import identity_snapshot, configuration_snapshot, original_configuration_frames
    fixture, image = os.environ['SBARBASE_FIXTURE_ID'], os.environ['SBARBASE_VERIFY_IMAGE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture) or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('Configured effect fixture/verifier identity differs')
    owner, name, network = 'sbarbase-fixture-' + fixture, fixture + '-worker-http', fixture + '-defaults-net'
    table, jobname = 'public.fixture_worker_effects', fixture + '-effects'
    evidence = {'scope': 'configured-owned-cron-writes-and-native-http', 'passed': False, 'samples': [], 'cleanup': [],
        'metadata_projection': {'catalog_row_limits': PROJECTION_LIMITS, 'byte_limit': 1024 * 1024,
            'noninternal_excludes': ['pg_catalog', 'pg_toast', 'pg_temp and internal namespace variants'],
            'omissions': ['Full types, constraints, policies, triggers, TOAST, dependency vectors, pg_init_privs.',
                          'Object contents, unrelated application rows, routine bodies and key/password material.']},
        'packaging_caveat': 'Upstream v1.6.4 control default_version is 1.6; admitted native image reports 1.6.4. Closed projection is not installed SQL-body attestation.',
        'limitations': ['Bounded owned effects after accepted original bootstrap/warm/auth; no second restart or owned-job restart proof.',
                       'No fence, archive/keys/full native restore, full stack, interruption, power loss or release acceptance.']}
    report['worker_effects'] = evidence
    effects_deadline = time.monotonic() + 90
    evidence['work_started_at_unix_seconds'] = time.time()
    output = Path('/evidence/worker-effects-artifacts')
    output.mkdir()
    helper = None
    helper_attempted = False
    table_attempted = False
    job_attempted = False
    binding = None
    owned_rows = None
    jobid = None
    original = None
    command = None
    baseline = None
    original_jobs = None
    first_failure = None
    phase = 'bootstrap prerequisite'

    def remaining_deadline(phase_deadline=None):
        deadline = effects_deadline if phase_deadline is None else min(effects_deadline, phase_deadline)
        if time.monotonic() >= deadline:
            raise RuntimeError('Configured effects work or phase budget exhausted')
        return deadline

    def call(args, *, phase_deadline=None, **kwargs):
        deadline = remaining_deadline(phase_deadline)
        result = native(args, operation_deadline=deadline, **kwargs)
        remaining_deadline(deadline)
        return result

    def query(statement, *, cleanup=False, phase_deadline=None):
        if cleanup:
            if phase_deadline is not None:
                raise ValueError('Cleanup retains its separate whole deadline')
            return sql(statement, cleanup=True)
        deadline = remaining_deadline(phase_deadline)
        result = sql(statement, operation_deadline=deadline)
        remaining_deadline(deadline)
        return result

    def observe(statement, *, cleanup=False, phase_deadline=None):
        result = json.loads(query(statement, cleanup=cleanup, phase_deadline=phase_deadline))
        if not cleanup:
            remaining_deadline(phase_deadline)
        return result

    def jobs(*, cleanup=False):
        return observe("SELECT coalesce(json_agg(row_to_json(j) ORDER BY jobid),'[]'::json) FROM cron.job j;", cleanup=cleanup)

    def unchanged_jobs(current):
        unrelated = [j for j in current if jobid is None or j['jobid'] != jobid]
        if not exact(unrelated, original_jobs):
            raise RuntimeError('Original cron job definitions changed')

    def preserve(label, *, cleanup=False):
        identity = identity_snapshot(query, cleanup=cleanup)
        configuration = configuration_snapshot(query, cleanup=cleanup)
        projected = observe(PROJECTION_SQL, cleanup=cleanup)
        frames = original_configuration_frames(report, native if cleanup else call, container, phase=label)
        result = {'identity': identity, 'configuration': configuration, 'projection': projected, 'configuration_frames': frames}
        evidence.setdefault('preservation_snapshots', {})[label] = result
        if not exact(identity, report['bootstrap']['after_authentication']) or not exact(configuration, report['bootstrap']['warm_configuration']):
            raise RuntimeError('Accepted bootstrap identity/configuration changed during effects')
        projection_rows(projected)
        return projected

    def events(*, phase_deadline=None):
        value = json.loads(call(['docker', 'exec', helper, '/usr/bin/python3', '-c',
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/events', timeout=2).read().decode())"], phase_deadline=phase_deadline).stdout)
        if set(value) != {'marker', 'events'} or value['marker'] != fixture or len(value['events']) > 128:
            raise RuntimeError('Owned HTTP receipts marker/bounds differ')
        remaining_deadline(phase_deadline)
        return value['events']

    def definition(*, phase_deadline=None):
        return observe('SELECT row_to_json(j) FROM cron.job j WHERE jobid=' + str(jobid) + ';', phase_deadline=phase_deadline)

    def snapshot(label, *, phase_deadline=None):
        sample = observe("SELECT json_build_object("
            "'rows',(SELECT coalesce(json_agg(row_to_json(t) ORDER BY sequence),'[]'::json) FROM " + table + " t),"
            "'responses',(SELECT coalesce(json_agg(row_to_json(r) ORDER BY id),'[]'::json) FROM net._http_response r WHERE id IN (SELECT request_id FROM " + table + ")),"
            "'queued',(SELECT count(*) FROM net.http_request_queue WHERE id IN (SELECT request_id FROM " + table + ")),"
            "'runs',(SELECT coalesce(json_agg(row_to_json(r) ORDER BY runid),'[]'::json) FROM cron.job_run_details r WHERE jobid=" + str(jobid) + "),"
            "'job',(SELECT row_to_json(j) FROM cron.job j WHERE jobid=" + str(jobid) + "));", phase_deadline=phase_deadline)
        sample.update(label=label, epoch=time.time(), receipts=events(phase_deadline=phase_deadline))
        evidence['samples'].append(sample)
        if not exact(sample['job'], dict(original, active=sample['job']['active'])):
            raise RuntimeError('Full owned job definition changed')
        strict_effect_sample(sample, fixture)
        remaining_deadline(phase_deadline)
        return sample

    def wait_effects(label, minimum, timeout, *, phase_deadline=None):
        deadline = (min(effects_deadline, time.monotonic() + timeout)
                    if phase_deadline is None else min(effects_deadline, phase_deadline))
        while True:
            remaining_deadline(deadline)
            sample = snapshot(label, phase_deadline=deadline)
            complete = len(sample['rows']) >= minimum and strict_effect_sample(sample, fixture)
            remaining_deadline(deadline)
            if complete:
                return sample
            time.sleep(min(.25, deadline - time.monotonic()))

    def disable_drain(label):
        deadline = min(effects_deadline, time.monotonic() + 10)
        if not exact(definition(phase_deadline=deadline), original):
            raise RuntimeError('Job mutation guard differs before disable')
        query('SELECT cron.alter_job(' + str(jobid) + ', active := false);', phase_deadline=deadline)
        stable = None
        while True:
            remaining_deadline(deadline)
            sample = snapshot(label, phase_deadline=deadline)
            complete = strict_effect_sample(sample, fixture)
            signature = stable_effect_signature(sample)
            if sample['job'] != dict(original, active=False):
                raise RuntimeError('Inactive full job definition differs')
            remaining_deadline(deadline)
            if complete and stable == signature:
                return sample
            stable = signature if complete else None
            time.sleep(min(.5, deadline - time.monotonic()))

    try:
        if (not report['bootstrap'].get('passed') or any(not e['passed'] for e in report['bootstrap']['helper_cleanup'])
                or not report['bootstrap'].get('positive_authentication') or not report['bootstrap'].get('negative_authentication')):
            raise RuntimeError('Complete bootstrap prerequisite not admitted')
        phase = 'fresh HTTP namespace'
        call(['docker', 'container', 'inspect', name], allow_absence=True)
        phase = 'original configured metadata before cron setup'
        evidence['workers_before_setup'] = observe("SELECT coalesce(json_agg(row_to_json(w) ORDER BY backend_type,pid),'[]'::json) FROM (SELECT pid,datid,datname,usename,backend_type FROM pg_stat_activity WHERE backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')) w;")
        net_workers = [w for w in evidence['workers_before_setup'] if w['backend_type'] == 'pg_net 0.20.4 worker']
        if len(net_workers) != 1 or not exact(net_workers[0], report['bootstrap']['warm_worker']):
            raise RuntimeError('Accepted original postgres worker changed before effects')
        before = preserve('effects-before')
        existing = [e for e in before['extensions'] if e['name'] == 'pg_cron']
        pg_net = [e for e in before['extensions'] if e['name'] == 'pg_net']
        if len(pg_net) != 1 or pg_net[0]['version'] != '0.20.4' or pg_net[0]['owner'] != 'supabase_admin':
            raise RuntimeError('Existing original pg_net pinned metadata differs')
        settings = observe("SELECT json_build_object('database',current_setting('cron.database_name'),'host',current_setting('cron.host'),'background_workers',current_setting('cron.use_background_workers'));")
        evidence['cron_settings'] = settings
        if settings != {'database': 'postgres', 'host': 'localhost', 'background_workers': 'off'}:
            raise RuntimeError('Original native cron connection settings differ')
        cron_namespaces = [n for n in before['namespaces'] if n['name'] == 'cron']
        if not existing and cron_namespaces:
            raise RuntimeError('Preexisting cron namespace collision')
        if len(existing) > 1:
            raise RuntimeError('Duplicate pg_cron extension metadata')
        original_jobs = jobs() if existing else []
        evidence['original_jobs'] = original_jobs
        phase = 'closed native pg_cron setup'
        if not existing:
            query('CREATE EXTENSION pg_cron;')
        candidate = preserve('effects-after-setup')
        evidence['cron_setup'] = admit_cron_setup(before, candidate, report['bootstrap']['after_authentication'], newly_installed=not existing)
        baseline = candidate
        evidence['cron_setup']['newly_installed'] = not existing
        if not exact(jobs(), original_jobs):
            raise RuntimeError('Cron setup changed original jobs')
        phase = 'owned table/job absence'
        collision = observe("SELECT json_build_object('table',to_regclass('public.fixture_worker_effects')::text,'job_count',(SELECT count(*) FROM cron.job WHERE jobname='" + jobname + "'));")
        if collision != {'table': None, 'job_count': 0}:
            raise RuntimeError('Owned effects table/job collision')
        for row in baseline['relations']:
            if row['namespace'] == 'public' and row['name'] in ('fixture_worker_effects_sequence_seq', 'fixture_worker_effects_pkey', 'fixture_worker_effects_request_id_key'):
                raise RuntimeError('Owned sequence/index namespace collision')
        phase = 'admitted HTTP recorder creation'
        verifier = json.loads(call(['docker', 'image', 'inspect', image]).stdout)[0]
        if verifier['Id'] != image or verifier['Config'].get('Volumes'):
            raise RuntimeError('Verifier image cannot create anonymous helper volumes')
        helper_attempted = True
        helper = call(['docker', 'create', '--pull=never', '--name', name, '--label', 'io.sbarbase.owner=' + owner,
            '--network', network, '--memory', '64m', '--memory-swap', '64m', '--cpus', '.25', '--pids-limit', '16',
            '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--tmpfs', '/tmp:rw,mode=1777,size=16m', '--env', 'SBARBASE_FIXTURE_ID=' + fixture,
            '--entrypoint', '/usr/bin/python3', image, '/opt/sbarbase/deploy/verify/native_http_fixture.py']).stdout.strip()
        record = json.loads(call(['docker', 'container', 'inspect', helper]).stdout)[0]
        admit_http_helper(record, helper, name, owner, image, network, running=False)
        evidence['helper'] = record
        call(['docker', 'start', helper])
        record = json.loads(call(['docker', 'container', 'inspect', helper]).stdout)[0]
        admit_http_helper(record, helper, name, owner, image, network, running=True)
        evidence['helper_running'] = record
        call(['docker', 'exec', helper, '/usr/bin/python3', '-c',
            "import socket,time; end=time.monotonic()+8\nwhile True:\n s=socket.socket(); s.settimeout(1); r=s.connect_ex(('127.0.0.1',8080)); s.close()\n if r==0: break\n if time.monotonic()>=end: raise SystemExit(1)\n time.sleep(.1)"])
        phase = 'owned native table and cron job'
        table_attempted = True
        query('CREATE TABLE ' + table + '(sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, marker text NOT NULL, kind text NOT NULL, request_id bigint NOT NULL UNIQUE); ALTER TABLE public.fixture_worker_effects ENABLE ROW LEVEL SECURITY;')
        binding = admit_owned_table(observe(OWNED_TABLE_SQL))
        admin_oid = positive_oid(str(next(r['oid'] for r in report['bootstrap']['after_authentication']['roles'] if r['rolname'] == 'supabase_admin')))
        if binding['table']['owner_oid'] != admin_oid:
            raise RuntimeError('Owned table owner OID differs from accepted native role')
        evidence['owned_table'] = binding
        owned_rows = admit_owned_projection(baseline, observe(PROJECTION_SQL), binding)
        evidence['owned_projected_relations'] = owned_rows
        url = 'http://' + name + ':8080/fixture'
        def insert(kind):
            return ("INSERT INTO " + table + "(sequence,marker,kind,request_id) OVERRIDING SYSTEM VALUE SELECT s.sequence,'" + fixture + "','" + kind
                + "',net.http_post(url := '" + url + "',body := jsonb_build_object('marker','" + fixture + "','sequence',s.sequence,'kind','" + kind
                + "'),timeout_milliseconds := 2000) FROM (SELECT nextval(pg_get_serial_sequence('" + table + "','sequence')) AS sequence) s;")
        command = insert('cron')
        job_attempted = True
        jobid = int(query("SELECT cron.schedule('" + jobname + "','1 second',$job$" + command + '$job$);'))
        original = definition()
        expected = {'jobid': jobid, 'schedule': '1 second', 'command': command, 'nodename': 'localhost', 'nodeport': 5432,
                    'database': 'postgres', 'username': 'supabase_admin', 'active': True, 'jobname': jobname}
        if jobid <= 0 or not exact(original, expected):
            raise RuntimeError('Owned full native job definition differs')
        evidence['original_job'] = original
        evidence['workers_before'] = observe("SELECT coalesce(json_agg(row_to_json(w) ORDER BY backend_type,pid),'[]'::json) FROM (SELECT pid,datid,datname,usename,backend_type FROM pg_stat_activity WHERE backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')) w;")
        workers = evidence['workers_before']
        if not exact(workers, evidence['workers_before_setup']):
            raise RuntimeError('Original worker identities changed during cron setup')
        if (len(workers) != 2 or {w['backend_type'] for w in workers} != {'pg_cron launcher', 'pg_net 0.20.4 worker'}
                or any(str(w['datid']) != str(report['bootstrap']['after_authentication']['application_oid']) or w['datname'] != 'postgres' for w in workers)
                or next(w for w in workers if w['backend_type'] == 'pg_net 0.20.4 worker')['usename'] != 'postgres'):
            raise RuntimeError('Configured original application workers differ')
        phase = 'enabled writes and delivery'
        wait_effects('enabled', 2, 10)
        drained = disable_drain('disabled-drained')
        phase = 'bounded inactive stability'
        start = time.monotonic()
        stability_deadline = min(effects_deadline, start + 4)
        while time.monotonic() - start < 4:
            stable = snapshot('inactive-stable', phase_deadline=stability_deadline)
            if stable_effect_signature(stable) != stable_effect_signature(drained) or not strict_effect_sample(stable, fixture):
                raise RuntimeError('Disabled tracked rows/responses/receipts/runs changed')
            remaining_deadline(stability_deadline)
            time.sleep(min(.25, stability_deadline - time.monotonic()))
        remaining_deadline()
        phase = 'resume writes and final inactive delivery'
        resume_deadline = min(effects_deadline, time.monotonic() + 8)
        if not exact(definition(phase_deadline=resume_deadline), dict(original, active=False)):
            raise RuntimeError('Inactive job mutation guard differs before resume')
        query('SELECT cron.alter_job(' + str(jobid) + ', active := true);', phase_deadline=resume_deadline)
        if not exact(definition(phase_deadline=resume_deadline), original):
            raise RuntimeError('Restored full job definition differs')
        wait_effects('resumed', len(drained['rows']) + 2, 8, phase_deadline=resume_deadline)
        final = disable_drain('final-disabled-drained')
        explicit_deadline = min(effects_deadline, time.monotonic() + 10)
        query(insert('explicit'), phase_deadline=explicit_deadline)
        delivered = wait_effects('explicit-while-inactive', len(final['rows']) + 1, 10, phase_deadline=explicit_deadline)
        if (delivered['job'] != dict(original, active=False) or sum(r['kind'] == 'cron' for r in delivered['rows']) < 4
                or sum(r['kind'] == 'explicit' for r in delivered['rows']) != 1):
            raise RuntimeError('Final cron/explicit owned effects differ')
        evidence['final_effects'] = delivered
        phase = 'full original preservation before owned cleanup'
        current = preserve('effects-pre-cleanup')
        admit_owned_projection(baseline, current, binding, owned_rows)
        if not exact(observe(OWNED_TABLE_SQL), binding):
            raise RuntimeError('Owned table/sequence definition changed before cleanup')
        unchanged_jobs(jobs())
        evidence['workers_before_cleanup'] = observe("SELECT coalesce(json_agg(row_to_json(w) ORDER BY backend_type,pid),'[]'::json) FROM (SELECT pid,datid,datname,usename,backend_type FROM pg_stat_activity WHERE backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')) w;")
        if not exact(evidence['workers_before_cleanup'], evidence['workers_before_setup']):
            raise RuntimeError('Original worker identities changed during owned effects')
        evidence['effects_complete'] = True
    except Exception as error:
        first_failure = error
        evidence['error'] = type(error).__name__ + ': ' + str(error)
        evidence['failed_phase'] = phase
        raise
    finally:
        evidence['work_finished_at_unix_seconds'] = time.time()
        # Eight once-only commands maximum: disable/drain, guarded unschedule,
        # guarded drop, then identity/config/projection/jobs and fixed frames.
        sql_clear = not job_attempted
        try:
            if job_attempted:
                job_guard = "SELECT coalesce(json_agg(row_to_json(j) ORDER BY jobid),'[]'::json) FROM cron.job j WHERE jobname='" + jobname + "'"
                expected_job = dict(original) if original is not None else {'schedule': '1 second', 'command': command, 'nodename': 'localhost', 'nodeport': 5432,
                    'database': 'postgres', 'username': 'supabase_admin', 'active': True, 'jobname': jobname}
                guard_json = json.dumps(expected_job, sort_keys=True).replace("'", "''")
                without_runtime = "admitted_job::jsonb-'active'" if original is not None else "admitted_job::jsonb-'jobid'-'active'"
                disable = ("DO $cleanup$ DECLARE admitted_job json; matches json; deadline timestamptz := clock_timestamp()+interval '6 seconds'; BEGIN "
                    + job_guard.replace(' FROM cron.job j', ' INTO matches FROM cron.job j') + "; IF json_array_length(matches)<>1 THEN RAISE EXCEPTION 'owned job cleanup identity unavailable'; END IF; "
                    "admitted_job:=matches->0; IF json_typeof(admitted_job->'active')<>'boolean' OR (admitted_job->>'jobid')::bigint<=0 OR " + without_runtime + "<>(('" + guard_json + "'::jsonb)-'active') THEN "
                    "RAISE EXCEPTION 'owned job definition changed'; END IF; "
                    "PERFORM cron.alter_job((admitted_job->>'jobid')::bigint,active:=false); LOOP "
                    "EXIT WHEN NOT EXISTS(SELECT 1 FROM cron.job_run_details WHERE jobid=(admitted_job->>'jobid')::bigint AND (status<>'succeeded' OR end_time IS NULL)) "
                    "AND NOT EXISTS(SELECT 1 FROM net.http_request_queue WHERE id IN (SELECT request_id FROM public.fixture_worker_effects)); "
                    "IF clock_timestamp()>=deadline THEN RAISE EXCEPTION 'owned cleanup drain deadline'; END IF; PERFORM pg_sleep(.1); END LOOP; END $cleanup$;")
                query(disable, cleanup=True)
                inactive = dict(expected_job, active=False)
                guard = json.dumps(inactive, sort_keys=True).replace("'", "''")
                same = "admitted_job::jsonb='" + guard + "'::jsonb" if original is not None else "(admitted_job::jsonb-'jobid')='" + guard + "'::jsonb"
                unschedule = ("DO $cleanup$ DECLARE admitted_job json; matches json; BEGIN " + job_guard.replace(' FROM cron.job j', ' INTO matches FROM cron.job j') + "; "
                    "IF json_array_length(matches)<>1 THEN RAISE EXCEPTION 'owned inactive job unavailable'; END IF; admitted_job:=matches->0; "
                    "IF NOT(" + same + ") THEN RAISE EXCEPTION 'owned inactive job changed'; END IF; "
                    "IF NOT cron.unschedule((admitted_job->>'jobid')::bigint) THEN RAISE EXCEPTION 'owned unschedule refused'; END IF; "
                    "IF EXISTS(SELECT 1 FROM cron.job WHERE jobname='" + jobname + "') THEN RAISE EXCEPTION 'owned job remains'; END IF; END $cleanup$;")
                query(unschedule, cleanup=True)
                evidence['cleanup'].append({'kind': 'job', 'passed': True})
                sql_clear = True
            if table_attempted and sql_clear:
                # Admit the exact shape even when creation returned an unparseable result.
                metadata = binding or admit_owned_table(observe(OWNED_TABLE_SQL, cleanup=True))
                encoded = json.dumps(metadata, sort_keys=True).replace("'", "''")
                guarded_drop = ("DO $cleanup$ DECLARE actual json; BEGIN " + OWNED_TABLE_SQL.removesuffix(';') + " INTO actual; "
                    "IF actual::jsonb<>'" + encoded + "'::jsonb THEN RAISE EXCEPTION 'owned table identity changed'; END IF; "
                    "DROP TABLE public.fixture_worker_effects; END $cleanup$;")
                query(guarded_drop, cleanup=True)
                evidence['cleanup'].append({'kind': 'table-sequence-two-indexes', 'passed': True})
            if baseline is not None and sql_clear:
                after = preserve('effects-post-cleanup', cleanup=True)
                admit_owned_projection(baseline, after, binding, absent=True)
                terminal = observe("SELECT json_build_object('jobs',(SELECT coalesce(json_agg(row_to_json(j) ORDER BY jobid),'[]'::json) FROM cron.job j),'workers',(SELECT coalesce(json_agg(row_to_json(w) ORDER BY backend_type,pid),'[]'::json) FROM (SELECT pid,datid,datname,usename,backend_type FROM pg_stat_activity WHERE backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')) w));", cleanup=True)
                unchanged_jobs(terminal['jobs'])
                evidence['workers_after_cleanup'] = terminal['workers']
                if not exact(terminal['workers'], evidence['workers_before_setup']):
                    raise RuntimeError('Original worker identities changed after owned cleanup')
                evidence['cleanup'].append({'kind': 'original-projection-and-jobs', 'passed': True})
        except Exception as error:
            evidence['cleanup'].append({'kind': 'owned-sql', 'passed': False, 'error': str(error)})
        if helper_attempted:
            try:
                lookup = helper if isinstance(helper, str) and re.fullmatch(r'[a-f0-9]{64}', helper) else name
                inspection = native(['docker', 'container', 'inspect', lookup], cleanup=True, check=False, server_logs=True)
                if inspection.returncode:
                    if lookup != name:
                        raise RuntimeError('Created HTTP helper CID is unexpectedly absent')
                    evidence['helper_cleanup'] = {'name': name, 'passed': True, 'recognized_absence': True}
                else:
                    item = json.loads(inspection.stdout)[0]
                    cid = item['Id']
                    admit_http_helper(item, cid, name, owner, image, network, running=None)
                    if helper is not None and re.fullmatch(r'[a-f0-9]{64}', helper) and helper != cid:
                        raise RuntimeError('Cleanup HTTP CID differs')
                    evidence.setdefault('helper', item)
                    logs = native(['docker', 'logs', cid], cleanup=True, server_logs=True)
                    evidence['helper_output'] = logs.stdout + logs.stderr
                    if logs.stdout or logs.stderr:
                        raise RuntimeError('Owned HTTP recorder diagnosed')
                    native(['docker', 'rm', '-f', cid], cleanup=True)
                    native(['docker', 'container', 'inspect', cid], cleanup=True, allow_absence=True)
                    native(['docker', 'container', 'inspect', name], cleanup=True, allow_absence=True)
                    evidence['helper_cleanup'] = {'id': cid, 'name': name, 'passed': True}
            except Exception as error:
                evidence['helper_cleanup'] = {'id': helper, 'name': name, 'passed': False, 'error': str(error)}
        cleanup_ok = all(e['passed'] for e in evidence['cleanup']) and (not helper_attempted or evidence.get('helper_cleanup', {}).get('passed', False))
        evidence['passed'] = bool(evidence.get('effects_complete') and cleanup_ok and first_failure is None)
        evidence['cleanup_finished_at_unix_seconds'] = time.time()
        if not cleanup_ok and first_failure is None:
            raise RuntimeError('Configured effects owned cleanup failed')


def probe(report, sql, native, container, *, configured=False):
    if configured:
        return configured_probe(report, sql, native, container)
    return legacy_probe(report, sql, native, container)


if __name__ == '__main__':
    raise SystemExit(main(probe))
