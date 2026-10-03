"""Private cutover identity journal and conservative PostgreSQL worker admission.

Journal intent precedes each mutation. Recovery must compare these identities
with native state; a saved phase alone never authorizes destructive recovery.
The cutover orchestrator consumes these guarded decisions.
"""
import json
import re


class OperationError(RuntimeError):
    pass


PHASES = (
    'initial', 'stage-create-intent', 'stage-created', 'stage-fenced', 'stage-ready',
    'stop-intent', 'writers-stopped', 'old-fence-intent', 'old-fenced',
    'original-rename-intent', 'original-renamed', 'stage-rename-intent', 'stage-renamed',
    'files-aside-intent', 'files-aside', 'files-replace-intent', 'files-ready',
    'checkpoint-intent', 'data-ready', 'open-intent', 'opened',
    'rollback-intent', 'rollback-open-intent', 'original-opened', 'rolled-back', 'completed',
)
REQUIRED = {'version', 'phase', 'scope', 'backup', 'stamp', 'container_id', 'database',
            'original_oid', 'stage', 'stage_oid', 'previous', 'archive', 'files', 'record', 'storage_cid'}
HASH = re.compile(r'[a-f0-9]{64}')
OID = re.compile(r'[1-9][0-9]*')
SCOPE = re.compile(r'(?:e_[a-f0-9]{24}|storage)')
STAMP = re.compile(r'[0-9]{8}t[0-9]{6}z')


def validate(value):
    """Refuse unknown fields, malformed identities and impossible saved state."""
    if not isinstance(value, dict) or set(value) != REQUIRED or type(value['version']) is not int \
            or value['version'] != 1 or value['phase'] not in PHASES:
        raise OperationError('Restore operation journal is malformed')
    scope, stamp = value['scope'], value['stamp']
    if not isinstance(scope, str) or not SCOPE.fullmatch(scope) or not isinstance(stamp, str) \
            or not STAMP.fullmatch(stamp) or not isinstance(value['backup'], str) \
            or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z', value['backup']):
        raise OperationError('Restore operation identity is malformed')
    database = 'storage_metadata' if scope == 'storage' else scope
    if value['database'] != database or value['stage'] != database + '_stage_' + stamp \
            or value['previous'] != database + '_pre_' + stamp \
            or not isinstance(value['container_id'], str) or not HASH.fullmatch(value['container_id']) \
            or not isinstance(value['storage_cid'], str) or not HASH.fullmatch(value['storage_cid']) \
            or not isinstance(value['original_oid'], str) or not OID.fullmatch(value['original_oid']) \
            or (value['stage_oid'] is not None and
                (not isinstance(value['stage_oid'], str) or not OID.fullmatch(value['stage_oid'])
                 or value['stage_oid'] == value['original_oid'])):
        raise OperationError('Restore operation native identity is malformed')
    archive = value['archive']
    if not isinstance(archive, dict) or set(archive) != {'manifest', 'database', 'objects'} \
            or not all(isinstance(archive[key], str) and HASH.fullmatch(archive[key]) for key in ('manifest', 'database')) \
            or (scope == 'storage' and archive['objects'] is not None) \
            or (scope != 'storage' and (not isinstance(archive['objects'], str) or not HASH.fullmatch(archive['objects']))):
        raise OperationError('Restore operation archive identity is malformed')
    files = value['files']
    if scope == 'storage':
        if files is not None:
            raise OperationError('Shared metadata restore may not replace object files')
    elif not isinstance(files, dict) or set(files) != {'aside', 'stage', 'original', 'restored'} \
            or files['aside'] != '.pre-restore-' + scope + '-' + stamp \
            or files['stage'] != '.stage-restore-' + scope + '-' + stamp:
        raise OperationError('Restore operation file identity is malformed')
    else:
        for item in (files['original'], files['restored']):
            if item is None:
                continue
            if not isinstance(item, dict) or set(item) != {'exists', 'device', 'inode', 'sha256'} \
                    or type(item['exists']) is not bool:
                raise OperationError('Restore operation file tree identity is malformed')
            if item['exists']:
                if not all(isinstance(item[key], str) and OID.fullmatch(item[key]) for key in ('device', 'inode')) \
                        or not isinstance(item['sha256'], str) or not HASH.fullmatch(item['sha256']):
                    raise OperationError('Restore operation file tree identity is malformed')
            elif any(item[key] is not None for key in ('device', 'inode', 'sha256')):
                raise OperationError('Missing file tree has unexpected identity')
    record = value['record']
    # The expected record's count value is validated by the admitted archive,
    # not invented from this mutable journal.
    expected_keys = {'restored_at', 'backup', 'previous_database', 'counts'}
    if scope != 'storage':
        expected_keys.add('previous_files')
    if not isinstance(record, dict) or set(record) != expected_keys \
            or record['restored_at'] != stamp or record['backup'] != value['backup'] \
            or record['previous_database'] != value['previous'] or not isinstance(record['counts'], dict) \
            or (scope != 'storage' and record['previous_files'] != files['aside']):
        raise OperationError('Restore operation completion record is malformed')
    if PHASES.index(value['phase']) >= PHASES.index('stage-created') and value['phase'] not in ('rollback-intent', 'rollback-open-intent', 'original-opened', 'rolled-back') \
            and value['stage_oid'] is None:
        raise OperationError('Restore operation stage identity is missing')
    return value


def journal_path(state, scope):
    if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
        raise OperationError('Unsupported restore operation scope')
    return state / ('restore-operation-' + scope + '.json')


def read_journal(state, scope):
    path = journal_path(state, scope)
    try:
        if path.parent.is_symlink() or not path.parent.is_dir() or path.parent.stat().st_mode & 0o777 != 0o700 \
                or path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:
            raise OperationError('Restore operation journal is not private')
        value = validate(json.loads(path.read_text()))
    except (OSError, ValueError):
        raise OperationError('Restore operation journal is unavailable') from None
    if value['scope'] != scope:
        raise OperationError('Restore operation journal scope differs')
    return value


def publish(state, value, atomic):
    validate(value)
    atomic(journal_path(state, value['scope']), value)


def recovery_database_action(journal, inventory):
    """Classify native DB state without authorizing any SQL or file mutation.

    In particular, ALLOWtrue on the restored OID is authoritative evidence of
    reopening even when a crash prevented publication of the opened phase.
    The caller must separately prove immutable file identities and writer
    barriers before performing any proposed rollback.
    """
    validate(journal)
    names = (journal['database'], journal['stage'], journal['previous'])
    if not isinstance(inventory, dict) or set(inventory) != set(names):
        raise OperationError('Restore recovery database inventory is incomplete')
    observed = {}
    for name in names:
        row = inventory[name]
        if row is None:
            observed[name] = None
            continue
        if not isinstance(row, dict) or set(row) != {'oid', 'allow_connections'} \
                or not isinstance(row['oid'], str) or not OID.fullmatch(row['oid']) \
                or type(row['allow_connections']) is not bool:
            raise OperationError('Restore recovery database identity is malformed')
        observed[name] = row
    target, stage, previous = (observed[name] for name in names)
    old_oid, new_oid = journal['original_oid'], journal['stage_oid']
    for name, row in observed.items():
        if row is not None and row['oid'] not in (old_oid, new_oid):
            raise OperationError('Restore recovery refuses same-name database replacement')
    if len([row['oid'] for row in observed.values() if row is not None]) \
            != len({row['oid'] for row in observed.values() if row is not None}):
        raise OperationError('Restore recovery database identities are ambiguous')
    if target and target['oid'] == new_oid:
        if not previous or previous['oid'] != old_oid or previous['allow_connections'] or stage is not None:
            raise OperationError('Restore cutover predecessor identity differs')
        if target['allow_connections']:
            return 'preserve-restored-writes'
        if journal['phase'] in ('opened', 'completed'):
            # A later administrator fence never makes an opened restore safe
            # to destructively replay or roll back.
            return 'preserve-restored-writes'
        return 'rollback-fenced-cutover'
    if target and target['oid'] == old_oid:
        creation_phase = journal['phase'] in ('initial', 'stage-create-intent', 'stage-created')
        if previous is not None or (stage is not None and (stage['oid'] != new_oid or stage['allow_connections'] and not creation_phase)):
            raise OperationError('Restore staging identity differs')
        if journal['phase'] in ('opened', 'completed'):
            raise OperationError('Opened restore no longer names the restored database')
        if journal['phase'] in ('rollback-open-intent', 'original-opened', 'rolled-back'):
            if stage is not None or previous is not None:
                raise OperationError('Reopened original has unexpected cutover databases')
            if target['allow_connections'] or journal['phase'] in ('original-opened', 'rolled-back'):
                return 'preserve-original-writes'
        return 'preserve-original'
    if target is None and previous and previous['oid'] == old_oid and not previous['allow_connections'] \
            and stage and stage['oid'] == new_oid and not stage['allow_connections']:
        if journal['phase'] in ('opened', 'completed'):
            raise OperationError('Opened restore cutover identity differs')
        return 'rollback-first-rename'
    raise OperationError('Restore recovery native state is unsupported')


WORKER_QUERY = """SELECT json_build_object(
 'version', current_setting('server_version_num')::integer,
 'shared', current_setting('shared_preload_libraries'),
 'session', current_setting('session_preload_libraries'),
 'local', current_setting('local_preload_libraries'),
 'workers', coalesce((SELECT json_agg(DISTINCT backend_type) FROM pg_stat_activity
                     WHERE backend_type <> 'client backend'), '[]'::json),
 'native', json_build_object(
   'cron_database', current_setting('cron.database_name',true),
   'cron_background', current_setting('cron.use_background_workers',true),
   'net_database', current_setting('pg_net.database_name',true),
   'versions', (SELECT json_object_agg(name,default_version) FROM pg_available_extensions
                WHERE name IN ('pg_cron','pg_net','pgsodium','pg_stat_statements','pgaudit')),
   'installed', (SELECT coalesce(json_object_agg(extname,extversion),'{}'::json) FROM pg_extension),
   'scopes', coalesce((SELECT json_agg(json_build_object('role',r.rolname,'database',d.datname,'setting',setting))
                       FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid=s.setrole
                       LEFT JOIN pg_database d ON d.oid=s.setdatabase, unnest(s.setconfig) setting
                       WHERE split_part(setting,'=',1) IN ('session_preload_libraries','local_preload_libraries',
                                                          'shared_preload_libraries','cron.use_background_workers')), '[]'::json),
   'workers', coalesce((SELECT json_agg(json_build_object('backend_type',backend_type,'datname',datname))
                       FROM pg_stat_activity WHERE backend_type IN ('pg_cron launcher','pg_net 0.20.4 worker')), '[]'::json)),
 'subscriptions', (SELECT count(*) FROM pg_subscription WHERE subenabled),
 'scoped_preloads', coalesce((SELECT json_agg(setting) FROM pg_db_role_setting,
                         unnest(setconfig) setting WHERE split_part(setting,'=',1) IN
                         ('session_preload_libraries','local_preload_libraries',
                          'shared_preload_libraries','cron.use_background_workers')), '[]'::json));"""


def admit_workers(value, *, image=None, database=None):
    """Classify core workers or the exact pinned, separate-database native defaults."""
    if not isinstance(value, dict) or set(value) not in ({'version', 'shared', 'session', 'local', 'workers', 'subscriptions', 'scoped_preloads'},
                                                        {'version', 'shared', 'session', 'local', 'workers', 'subscriptions', 'scoped_preloads', 'native'}) \
            or type(value['version']) is not int or not 170000 <= value['version'] < 180000:
        raise OperationError('PostgreSQL restore worker contract is unsupported')
    if not isinstance(value['workers'], list) or any(not isinstance(item, str) for item in value['workers']):
        raise OperationError('PostgreSQL worker inventory is unverifiable')
    for key in ('shared', 'session', 'local'):
        if not isinstance(value[key], str):
            raise OperationError('PostgreSQL preload inventory is unverifiable')
    preloads = [item.strip() for item in value['shared'].split(',') if item.strip()]
    native = any(item not in ('pg_stat_statements', 'pgaudit') for item in preloads) or value['session'].strip()
    if native:
        admit_native_defaults(value, image, database, preloads)
    elif value['local'].strip():
        raise OperationError('Unclassified privileged PostgreSQL preload refuses restore')
    allowed = {'autovacuum launcher', 'autovacuum worker', 'background writer', 'checkpointer',
               'walwriter', 'logical replication launcher', 'walsender'}
    if native:
        allowed.update(('pg_cron launcher', 'pg_net 0.20.4 worker'))
    if not isinstance(value['workers'], list) or any(not isinstance(item, str) or item not in allowed for item in value['workers']) \
            or type(value['subscriptions']) is not int or value['subscriptions'] != 0 \
            or not isinstance(value['scoped_preloads'], list) or (not native and value['scoped_preloads']):
        raise OperationError('Unclassified PostgreSQL worker or scoped preload refuses restore')
    return True


NATIVE_IMAGE = 'sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86'
NATIVE_PRELOADS = {'pg_stat_statements', 'pgaudit', 'plpgsql', 'plpgsql_check', 'pg_cron', 'pg_net',
                   'pgsodium', 'auto_explain', 'pg_tle', 'plan_filter', 'supabase_vault'}
NATIVE_VERSIONS = {'pg_cron': '1.6.4', 'pg_net': '0.20.4', 'pgsodium': '3.1.8',
                   'pg_stat_statements': '1.11', 'pgaudit': '17.1'}


def admit_native_defaults(value, image, database, preloads):
    # Preflight binds the running DB to the distributed image before this call.
    # Client-mode cron and pg_net remain attached to postgres, never the replaced
    # environment or Storage database. Their libraries connect with flags zero.
    if value['version'] != 170006 or image != NATIVE_IMAGE or not isinstance(database, str) or not re.fullmatch(r'(e_[a-f0-9]{24}|storage_metadata)', database) \
            or set(preloads) != NATIVE_PRELOADS or len(preloads) != len(NATIVE_PRELOADS) \
            or value['session'].strip() != 'supautils' or value['local'].strip():
        raise OperationError('Unclassified privileged PostgreSQL preload refuses restore')
    profile = value.get('native')
    if not isinstance(profile, dict) or set(profile) != {'cron_database', 'cron_background', 'net_database', 'versions', 'installed', 'workers', 'scopes'} \
            or profile['cron_database'] != 'postgres' or profile['cron_background'] != 'off' \
            or profile['net_database'] != 'postgres' or profile['versions'] != NATIVE_VERSIONS:
        raise OperationError('Pinned native PostgreSQL worker configuration differs')
    setting = 'session_preload_libraries=supautils, safeupdate'
    if value['scoped_preloads'] != [setting] or profile['scopes'] != [{'role': 'authenticator', 'database': None, 'setting': setting}]:
        raise OperationError('Pinned native scoped preload identity differs')
    if not isinstance(profile['installed'], dict) or any(profile['installed'].get(name, version) != version for name, version in NATIVE_VERSIONS.items()):
        raise OperationError('Pinned native PostgreSQL extension version differs')
    workers = profile['workers']
    expected = {'pg_cron launcher', 'pg_net 0.20.4 worker'} & set(value['workers']) if isinstance(value['workers'], list) else set()
    if not isinstance(workers, list) or len(workers) != len(expected):
        raise OperationError('Native worker database identity is unverifiable')
    observed = set()
    for worker in workers:
        if not isinstance(worker, dict) or set(worker) != {'backend_type', 'datname'} \
                or worker['datname'] != 'postgres' or worker['backend_type'] not in expected or worker['backend_type'] in observed:
            raise OperationError('Native worker database identity differs from maintenance')
        observed.add(worker['backend_type'])
    if observed != expected:
        raise OperationError('Native worker inventory differs')
