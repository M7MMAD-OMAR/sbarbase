"""Read-only provenance for the pinned original pg_cron installation hook."""

import hashlib
import json
import re

ORIGINAL_IMAGE = 'sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86'
UPSTREAM_COMMIT = 'af61232a627931d1da9d392993780cecb6a472de'
SCRIPT_PATH = '/etc/postgresql-custom/extension-custom-scripts/pg_cron/after-create.sql'
SCRIPT_BYTES = 737
SCRIPT_SHA256 = '12169cf2fa48df164030dc0519594757b864e4ed1cb1417daeb3c29ec8fb1a8b'
BODY_BYTES = 1194
BODY_SHA256 = '00db212b76cecd4b0794b2f20c414ab4988d675959747cd12d3eb4c6d6736f38'
SQL_BYTE_LIMIT = 16384
METADATA_BYTE_LIMIT = 512
METADATA_TEMPLATE = '[{"Id":{{json .Id}},"Image":{{json .Image}}}]'
SETTINGS = ('supautils.extension_custom_scripts_path', 'supautils.privileged_extensions',
            'supautils.privileged_extensions_superuser')
FILE_PAYLOAD = ('set -eu; p=' + SCRIPT_PATH + '; test -f "$p"; test ! -L "$p"; '
                'test "$(wc -c < "$p")" -eq 737; cat "$p"')
SQL = r"""WITH hooks AS (
 SELECT p.oid::text AS oid,p.pronamespace::text AS namespace_oid,n.nspname AS namespace,p.proname AS name,
 p.proowner::text AS owner_oid,pg_get_userbyid(p.proowner) AS owner,l.lanname AS language,
 p.prosecdef AS security_definer,p.proconfig AS configuration,p.prokind::text AS kind,
 oidvectortypes(p.proargtypes) AS argument_types,format_type(p.prorettype,NULL) AS return_type,
 CASE WHEN octet_length(p.prosrc)<=4096 THEN p.prosrc END AS body,octet_length(p.prosrc) AS body_bytes
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
 WHERE n.nspname='extensions' AND p.proname='grant_pg_cron_access'),
 triggers AS (SELECT oid::text AS oid,evtname AS name,evtevent AS event,evtowner::text AS owner_oid,
 evtenabled::text AS enabled,evtfoid::text AS function_oid,evttags AS tags
 FROM pg_event_trigger WHERE evtname='issue_pg_cron_access')
 SELECT json_build_object('hook_count',(SELECT count(*) FROM hooks),
 'hooks',(SELECT coalesce(json_agg(row_to_json(h)),'[]'::json) FROM (SELECT * FROM hooks ORDER BY oid LIMIT 2) h),
 'trigger_count',(SELECT count(*) FROM triggers),
 'triggers',(SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT * FROM triggers ORDER BY oid LIMIT 2) t),
 'current_user',current_user,'session_user',session_user,
 'settings',json_build_object(
 'supautils.extension_custom_scripts_path',CASE WHEN octet_length(current_setting('supautils.extension_custom_scripts_path',true))<=4096 THEN current_setting('supautils.extension_custom_scripts_path',true) END,
 'supautils.privileged_extensions',CASE WHEN octet_length(current_setting('supautils.privileged_extensions',true))<=4096 THEN current_setting('supautils.privileged_extensions',true) END,
 'supautils.privileged_extensions_superuser',CASE WHEN octet_length(current_setting('supautils.privileged_extensions_superuser',true))<=4096 THEN current_setting('supautils.privileged_extensions_superuser',true) END));
"""


def oid(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) is not None and int(value) <= 4294967295


def admit_file(raw):
    if not isinstance(raw, bytes) or len(raw) != SCRIPT_BYTES or hashlib.sha256(raw).hexdigest() != SCRIPT_SHA256:
        raise RuntimeError('Original Cron custom script byte identity differs')


def admit_sql(value, identity, projection):
    if (not isinstance(value, dict) or set(value) != {'hook_count', 'hooks', 'trigger_count', 'triggers', 'current_user', 'session_user', 'settings'}
            or type(value['hook_count']) is not int or value['hook_count'] != 1
            or type(value['trigger_count']) is not int or value['trigger_count'] != 1
            or not isinstance(value['hooks'], list) or len(value['hooks']) != 1
            or not isinstance(value['triggers'], list) or len(value['triggers']) != 1):
        raise RuntimeError('Targeted Cron hook or trigger count/shape differs')
    roles = [r for r in identity['roles'] if r['rolname'] == 'supabase_admin']
    if len(roles) != 1 or roles[0]['rolsuper'] is not True:
        raise RuntimeError('Accepted Supabase superuser identity differs')
    admin = str(roles[0]['oid'])
    hook, trigger = value['hooks'][0], value['triggers'][0]
    hook_keys = {'oid', 'namespace_oid', 'namespace', 'name', 'owner_oid', 'owner', 'language', 'security_definer', 'configuration', 'kind', 'argument_types', 'return_type', 'body', 'body_bytes'}
    if not isinstance(hook, dict) or set(hook) != hook_keys or not oid(hook['oid']) or not oid(hook['namespace_oid']) or not oid(admin):
        raise RuntimeError('Targeted Cron hook identity shape differs')
    expected = {'namespace': 'extensions', 'name': 'grant_pg_cron_access', 'owner_oid': admin,
                'owner': 'supabase_admin', 'language': 'plpgsql', 'security_definer': False,
                'configuration': None, 'kind': 'f', 'argument_types': '', 'return_type': 'event_trigger'}
    if any(type(hook[k]) is not type(v) or hook[k] != v for k, v in expected.items()):
        raise RuntimeError('Targeted Cron hook typed identity differs')
    body = hook['body']
    if (not isinstance(body, str) or type(hook['body_bytes']) is not int or hook['body_bytes'] != BODY_BYTES
            or len(body.encode('utf-8')) != BODY_BYTES or hashlib.sha256(body.encode('utf-8')).hexdigest() != BODY_SHA256):
        raise RuntimeError('Targeted Cron hook public source differs')
    matched = [r for r in projection['routines'] if r['namespace'] == 'extensions' and r['name'] == 'grant_pg_cron_access']
    if len(matched) != 1 or any(type(matched[0].get(k)) is not type(hook[k]) or matched[0].get(k) != hook[k]
                                for k in ('oid', 'namespace_oid', *expected)):
        raise RuntimeError('Targeted Cron hook differs from preserved routine projection')
    trigger_expected = {'name': 'issue_pg_cron_access', 'event': 'ddl_command_end', 'owner_oid': admin,
                        'enabled': 'O', 'function_oid': hook['oid'], 'tags': ['CREATE EXTENSION']}
    if (not isinstance(trigger, dict) or set(trigger) != {'oid', *trigger_expected} or not oid(trigger['oid'])
            or any(trigger[k] != v for k, v in trigger_expected.items())):
        raise RuntimeError('Targeted Cron CREATE EXTENSION trigger differs')
    projected_triggers = [t for t in projection['event_triggers'] if t['name'] == 'issue_pg_cron_access']
    if projected_triggers != [trigger]:
        raise RuntimeError('Targeted Cron trigger differs from preserved projection')
    settings = value['settings']
    if (value['current_user'] != 'supabase_admin' or value['session_user'] != 'supabase_admin'
            or not isinstance(settings, dict) or set(settings) != set(SETTINGS)
            or settings[SETTINGS[0]] != '/etc/postgresql-custom/extension-custom-scripts'
            or settings[SETTINGS[2]] != 'supabase_admin'):
        raise RuntimeError('Cron custom script or creating Supabase session context differs')
    privileges = settings[SETTINGS[1]]
    if not isinstance(privileges, str) or len(privileges.encode('utf-8')) > 4096:
        raise RuntimeError('Supautils privileged extension list exceeds bounds')
    tokens = [s.strip() for s in privileges.split(',')]
    if (not 1 <= len(tokens) <= 128 or len(set(tokens)) != len(tokens) or 'pg_cron' not in tokens
            or any(t != 'uuid-ossp' and not re.fullmatch(r'[a-z][a-z0-9_]*', t) for t in tokens)):
        raise RuntimeError('Supautils pg_cron privileged extension context differs')


def admit_setup_witness(record, identity, before, after):
    if (not isinstance(record, dict) or record.get('scope') != 'pinned-original-cron-hook-provenance'
            or record.get('passed') is not True or record.get('image_id') != ORIGINAL_IMAGE
            or record.get('upstream_commit') != UPSTREAM_COMMIT):
        raise RuntimeError('Validated pinned Cron provenance required for setup admission')
    file = record.get('file')
    if (not isinstance(file, dict) or file.get('path') != SCRIPT_PATH
            or file.get('passed') is not True or type(file.get('bytes')) is not int
            or file['bytes'] != SCRIPT_BYTES or file.get('sha256') != SCRIPT_SHA256
            or not isinstance(file.get('source'), str)):
        raise RuntimeError('Validated original Cron script witness required')
    # Recheck the exact public bytes rather than relying on recorded success.
    if len(file['source']) > SCRIPT_BYTES:
        raise RuntimeError('Original Cron script witness exceeds bounds')
    admit_file(file['source'].encode('utf-8'))
    observations = []
    for label, projection in (('before', before), ('setup', after)):
        phase = record.get(label)
        if (not isinstance(phase, dict) or phase.get('passed') is not True
                or type(phase.get('bytes')) is not int or not 0 < phase['bytes'] <= SQL_BYTE_LIMIT
                or not isinstance(phase.get('sha256'), str)
                or re.fullmatch(r'[a-f0-9]{64}', phase['sha256']) is None
                or not isinstance(phase.get('source'), str)):
            raise RuntimeError('Validated bounded Cron provenance phase required: ' + label)
        if len(phase['source']) > SQL_BYTE_LIMIT:
            raise RuntimeError('Cron provenance phase source exceeds bounds')
        source = phase['source'].encode('utf-8')
        if (len(source) != phase['bytes'] or len(source) > SQL_BYTE_LIMIT
                or hashlib.sha256(source).hexdigest() != phase['sha256']):
            raise RuntimeError('Cron provenance phase byte identity differs')
        try:
            value = json.loads(source)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('Cron provenance phase JSON differs') from error
        canonical = lambda item: json.dumps(item, sort_keys=True, separators=(',', ':'))
        if canonical(value) != canonical(phase.get('observation')):
            raise RuntimeError('Cron provenance parsed observation differs')
        admit_sql(value, identity, projection)
        observations.append(value)
    if json.dumps(observations[0], sort_keys=True) != json.dumps(observations[1], sort_keys=True):
        raise RuntimeError('Original Cron provenance changed across extension setup')


def observe_file(record, call, container, output):
    record.update({'scope': 'pinned-original-cron-hook-provenance', 'passed': False,
                   'upstream_commit': UPSTREAM_COMMIT,
                   'limitations': ['Public Supautils v3.4.0 source ordering is research provenance, not installed binary attestation.',
                                   'No Cron ACL admission change or production acceptance follows from this observation.']})
    if not isinstance(container, str) or not re.fullmatch(r'[a-f0-9]{64}', container):
        raise RuntimeError('Cron provenance requires the exact database container ID')
    metadata = call(['docker', 'container', 'inspect', '--format', METADATA_TEMPLATE, container], binary=True)
    raw = metadata.stdout
    (output / 'cron-provenance-container.json.bin').write_bytes(raw)
    if metadata.returncode != 0 or metadata.stderr or len(raw) > METADATA_BYTE_LIMIT:
        raise RuntimeError('Cron container metadata diagnosed or exceeded bounds')
    inspected = json.loads(raw)
    if (not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict)
            or set(inspected[0]) != {'Id', 'Image'} or inspected[0].get('Id') != container
            or inspected[0].get('Image') != ORIGINAL_IMAGE):
        raise RuntimeError('Actual database image differs before Cron source read')
    record['image_id'] = inspected[0]['Image']
    result = call(['docker', 'exec', '--user', '100:101', container, '/usr/bin/timeout', '-s', 'KILL', '5', '/bin/sh', '-c', FILE_PAYLOAD], binary=True)
    raw = result.stdout
    (output / 'cron-provenance-after-create.sql.bin').write_bytes(raw)
    record['file'] = {'path': SCRIPT_PATH, 'artifact': 'cron-provenance-after-create.sql.bin',
                      'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'passed': False}
    if result.returncode != 0 or result.stderr:
        raise RuntimeError('Cron source read diagnosed or failed')
    admit_file(raw)
    record['file']['source'] = raw.decode('utf-8')
    record['file']['passed'] = True


def observe_sql(record, label, query, identity, projection, output):
    if label not in ('before', 'setup'):
        raise ValueError('Unknown Cron provenance observation phase')
    file = record.get('file', {})
    if (record.get('image_id') != ORIGINAL_IMAGE or file.get('passed') is not True
            or file.get('bytes') != SCRIPT_BYTES or file.get('sha256') != SCRIPT_SHA256):
        raise RuntimeError('Verified original image and file required before Cron SQL')
    if label in record:
        raise RuntimeError('Duplicate Cron provenance phase')
    if label == 'setup' and record.get('before', {}).get('passed') is not True:
        raise RuntimeError('Validated before phase required before Cron setup observation')
    raw = query(SQL)
    encoded = raw.encode('utf-8')
    artifact = 'cron-provenance-' + label + '.json.bin'
    (output / artifact).write_bytes(encoded)
    sample = {'artifact': artifact, 'bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest(), 'passed': False}
    record[label] = sample
    if len(encoded) > SQL_BYTE_LIMIT:
        raise RuntimeError('Targeted Cron provenance SQL byte bound exceeded')
    sample['source'] = raw
    value = json.loads(raw)
    sample['observation'] = value
    admit_sql(value, identity, projection)
    if label == 'setup' and value != record['before']['observation']:
        raise RuntimeError('Original Cron provenance changed across extension setup')
    sample['passed'] = True
    if label == 'setup':
        record['passed'] = True
