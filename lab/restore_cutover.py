"""Fenced v1 archive replacement and identity-bound explicit recovery.

All native effects go through the existing backup API. Trusted administrator
SQL archives and the admitted writer contract are required. Ordinary clients
are fenced by ALLOW_CONNECTIONS, not by connection limits or random names.
"""
import backup_consistency
import contextlib
import json
import tempfile
import time

import restore_barrier
import restore_files
import restore_operation as operation
from restore_session import HeldSession
from restore_sql import plan_archive


def storage(api, cid=None, stopped=False):
    result = api.run(['docker', 'container', 'inspect', api.STORAGE_CONTAINER])
    if result.stderr:
        raise api.BackupError('Storage writer inspection emitted diagnostics')
    records = json.loads(result.stdout)
    if not isinstance(records, list) or len(records) != 1:
        raise api.BackupError('Storage writer inspection is ambiguous')
    _, image_id = api.resolve_storage_image()
    return restore_barrier.admit_storage(records[0], name=api.STORAGE_CONTAINER, owner=api.DATABASE_OWNER,
        image_id=image_id, volume=api.OBJECTS_VOLUME, tenant_parent=api.TENANT_PARENT, container_id=cid, stopped=stopped)


def inventory(api, journal):
    names = (journal['database'], journal['stage'], journal['previous'])
    query = "SELECT coalesce(json_object_agg(datname,json_build_object('oid',oid::text,'allow_connections',datallowconn)),'{}'::json) FROM pg_database WHERE datname IN (" \
        + ','.join("'" + name + "'" for name in names) + ');'
    rows = json.loads(api.sql(query))
    return {name: rows.get(name) for name in names}


def require_database(api, name, oid):
    """Bind each name-based effect to the admitted native database identity.

    This catches accidental replacement at workflow boundaries. The trusted
    administrator contract excludes concurrent hostile catalog DDL between
    this check and its immediately following native operation.
    """
    if oid is None or api.database_oid(name) != oid:
        raise api.BackupError('Restore database identity changed before native operation')


def recovery_checkpoint(api, journal):
    """Distinguish this attempt from validated completed historical state."""
    path = api.completion_path(journal['scope'])
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise api.BackupError('Restore recovery checkpoint is unverifiable')
    try:
        reference = json.loads(path.read_text())
    except (OSError, ValueError):
        raise api.BackupError('Restore recovery checkpoint is unverifiable') from None
    if not isinstance(reference, dict):
        raise api.BackupError('Restore recovery checkpoint is malformed')
    saved, archive, record = api.load_completion(journal['scope'], reference.get('backup'), reference.get('stamp'))
    current = (saved['backup'], saved['stamp']) == (journal['backup'], journal['stamp'])
    if current:
        if saved['database_oid'] != journal['stage_oid'] or saved['container_id'] != journal['container_id'] \
                or saved.get('storage_container_id', journal['storage_cid']) != journal['storage_cid']:
            raise api.BackupError('Restore rollback checkpoint identity differs')
        return saved
    if saved['status'] != 'completed':
        raise api.BackupError('Foreign pending checkpoint refuses restore recovery')
    api.verify_completion_success(archive, saved['stamp'], record)
    # Historical completion is archive/record proof, not authority over this
    # attempt's current DB OID or container. Preserve it during early rollback.
    return None


def prepared(api, database):
    if api.sql("SELECT count(*) FROM pg_prepared_xacts WHERE database='" + database + "';") != '0':
        raise api.BackupError('Prepared transactions refuse restore before rename')


def drain(api, database, retained=None, timeout=30):
    condition = "datname='" + database + "'" + ('' if retained is None else ' AND pid <> ' + str(int(retained)))
    deadline = time.monotonic() + timeout
    while True:
        api.sql('SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE ' + condition + ';')
        if api.sql('SELECT count(*) FROM pg_stat_activity WHERE ' + condition + ';') == '0':
            prepared(api, database)
            return
        if time.monotonic() >= deadline:
            raise api.BackupError('Restore database backends did not drain')
        time.sleep(.1)


def tree(api, action, name, expected=None, target=''):
    result = api.helper(restore_files.tree_command(action), restore_files.TREE_SCRIPT, name,
                        json.dumps(expected), target, writable=action != 'identity')
    if result.stderr:
        raise api.BackupError('Object identity operation emitted diagnostics')
    return json.loads(result.stdout)


def phase(api, journal, name):
    journal['phase'] = name
    operation.publish(api.STATE, journal, api.atomic_private)


@contextlib.contextmanager
def archive_sql(api, admitted, archive, database, stage):
    with tempfile.TemporaryFile() as generated, tempfile.TemporaryFile() as replay:
        with (archive / 'database.dump').open('rb') as source:
            result = api.run(['docker', 'exec', '-i', admitted['db'], 'pg_restore', '--create', '--exit-on-error',
                              '--file=-'], stdin=source, stdout=generated, text=False)
        if result.stderr:
            raise api.BackupError('Native archive planning emitted diagnostics')
        generated.seek(0)
        # The anonymous spool remains private and is never a host dependency.
        import io
        with io.TextIOWrapper(generated, encoding='utf-8') as source, io.TextIOWrapper(replay, encoding='utf-8', write_through=True) as output:
            plan = plan_archive(source, output, database, stage)
            output.flush()
            replay.seek(0)
            yield plan, replay


def restore(api, scope, name, now):
    api.no_pending_completion()
    database = api.STORAGE_DATABASE if scope == api.STORAGE else scope
    archive = api.BACKUPS / scope / name
    manifest = api.verify_storage(archive) if scope == api.STORAGE else api.verify(scope, archive)
    if scope == api.STORAGE:
        missing = [item for item in api.environments() if item not in manifest['tenants']]
        if missing:
            raise api.BackupError('Shared metadata archive omits published environments: ' + ', '.join(missing))
    elif scope not in api.published():
        raise api.BackupError('Only a published environment can be restored')
    admitted = api.preflight()
    storage_cid = storage(api)
    workers = json.loads(api.sql(operation.WORKER_QUERY))
    operation.admit_workers(workers, image=api.image_pin('distro-image.lock.json')['id'] if 'native' in workers else None, database=database)
    prepared(api, database)
    stamp = now.strftime('%Y%m%dt%H%M%Sz')
    stage, previous = database + '_stage_' + stamp, database + '_pre_' + stamp
    original_oid = api.database_oid(database)
    if api.sql("SELECT datallowconn FROM pg_database WHERE oid=" + original_oid + ';') != 't':
        raise api.BackupError('Restore target is already fenced')
    if api.sql("SELECT count(*) FROM pg_database WHERE datname IN ('" + stage + "','" + previous + "');") != '0':
        raise api.BackupError('Restore stage or predecessor already exists')
    if scope != api.STORAGE:
        restore_files.admit_tar(archive / 'objects.tar', scope)
    record = {'restored_at': stamp, 'backup': name, 'previous_database': previous, 'counts': manifest['counts']}
    files = None
    if scope != api.STORAGE:
        aside = '.pre-restore-' + scope + '-' + stamp
        record['previous_files'] = aside
        files = {'aside': aside, 'stage': '.stage-restore-' + scope + '-' + stamp,
                 'original': None, 'restored': None}
    journal = {'version': 1, 'phase': 'initial', 'scope': scope, 'backup': name, 'stamp': stamp,
               'container_id': admitted['db'], 'storage_cid': storage_cid, 'database': database,
               'original_oid': original_oid, 'stage': stage, 'stage_oid': None, 'previous': previous,
               'archive': {'manifest': api.digest(archive / 'manifest.json'), 'database': api.digest(archive / 'database.dump'),
                           'objects': None if scope == api.STORAGE else api.digest(archive / 'objects.tar')},
               'files': files, 'record': record}
    with archive_sql(api, admitted, archive, database, stage) as (plan, replay):
        if plan.roles:
            roles = ','.join("'" + role.replace("'", "''") + "'" for role in plan.roles)
            observed = json.loads(api.sql("SELECT coalesce(json_agg(rolname ORDER BY rolname),'[]'::json) FROM pg_roles WHERE rolname IN (" + roles + ');'))
            if observed != list(plan.roles):
                raise api.BackupError('Archive owner or grantee role is missing before restore')
        if api.sql("SELECT to_regprocedure('pg_catalog.pg_nextoid(regclass,name,regclass)') IS NOT NULL;") != 't':
            raise api.BackupError('Pinned PostgreSQL stage identity allocator is unavailable')
        phase(api, journal, 'initial')
        try:
            stage_oid = api.sql("SELECT pg_catalog.pg_nextoid('pg_catalog.pg_database'::regclass,'oid'::name,'pg_catalog.pg_database_oid_index'::regclass);")
            if not operation.OID.fullmatch(stage_oid) or not 16384 <= int(stage_oid) <= 4294967295 \
                    or stage_oid == original_oid:
                raise api.BackupError('Native stage identity allocation is invalid')
            journal['stage_oid'] = stage_oid
            phase(api, journal, 'stage-create-intent')
            api.sql(plan.create.rstrip().rstrip(';') + ' OID = ' + stage_oid + ';')
            if api.database_oid(stage) != stage_oid:
                raise api.BackupError('Created stage identity differs from durable intent')
            phase(api, journal, 'stage-created')
            argv = ['docker', 'exec', '-i', admitted['db'], 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                    '-U', 'supabase_admin', '-d', stage]
            with HeldSession(argv) as held:
                pid, oid = held.execute('SELECT pg_backend_pid(), (SELECT oid FROM pg_database WHERE datname=current_database());').split('|')
                if oid != journal['stage_oid']:
                    raise api.BackupError('Retained stage OID differs')
                require_database(api, stage, oid)
                api.sql('ALTER DATABASE ' + stage + ' ALLOW_CONNECTIONS false;')
                if api.database_oid(stage) != oid:
                    raise api.BackupError('Fenced stage OID differs')
                require_database(api, stage, oid)
                drain(api, stage, retained=pid)
                phase(api, journal, 'stage-fenced')
                held.execute('SET max_parallel_workers_per_gather=0; SET max_parallel_maintenance_workers=0;')
                held.replay(replay)
                held.execute('\n'.join(plan.metadata))
                actual = api.parse_storage_counts(held.execute(api.STORAGE_COUNTS)) if scope == api.STORAGE else api.parse_counts(held.execute(api.COUNTS))
                expected = {'tenants': manifest['counts']['tenants'], 'ids': manifest['tenants']} if scope == api.STORAGE else manifest['counts']
                if actual != expected or held.execute('SELECT pg_backend_pid();') != pid:
                    raise api.BackupError('Retained restore validation differs from archive')
            require_database(api, stage, stage_oid)
            drain(api, stage)
            phase(api, journal, 'stage-ready')
            phase(api, journal, 'stop-intent')
            api.run(['docker', 'stop', storage_cid])
            storage(api, storage_cid, stopped=True)
            if scope != api.STORAGE:
                api.run(['docker', 'stop', *api.service_names(scope)])
            phase(api, journal, 'writers-stopped')
            phase(api, journal, 'old-fence-intent')
            require_database(api, database, original_oid)
            api.sql('ALTER DATABASE ' + database + ' ALLOW_CONNECTIONS false;')
            require_database(api, database, original_oid)
            drain(api, database)
            phase(api, journal, 'old-fenced')
            if files is not None:
                files['original'] = tree(api, 'identity', scope)
                if tree(api, 'identity', files['aside'])['exists'] or tree(api, 'identity', files['stage'])['exists']:
                    raise api.BackupError('Object stage or predecessor already exists')
                phase(api, journal, 'old-fenced')
                storage(api, storage_cid, stopped=True)
                api.helper('mkdir "$1"', '/data/' + api.TENANT_PARENT + '/' + files['stage'], writable=True)
                with (archive / 'objects.tar').open('rb') as source:
                    api.helper('python3 -c "$1" "$2"', backup_consistency.EXTRACT_SCRIPT,
                               '/data/' + api.TENANT_PARENT + '/' + files['stage'],
                               writable=True, stdin=source, text=False)
                files['restored'] = tree(api, 'sync', files['stage'] + '/' + scope)
                phase(api, journal, 'old-fenced')
            phase(api, journal, 'original-rename-intent')
            require_database(api, database, original_oid)
            api.sql('ALTER DATABASE ' + database + ' RENAME TO ' + previous + ';')
            phase(api, journal, 'original-renamed')
            phase(api, journal, 'stage-rename-intent')
            require_database(api, stage, stage_oid)
            require_database(api, previous, original_oid)
            api.sql('ALTER DATABASE ' + stage + ' RENAME TO ' + database + ';')
            phase(api, journal, 'stage-renamed')
            if files is not None:
                storage(api, storage_cid, stopped=True)
                phase(api, journal, 'files-aside-intent')
                if files['original']['exists']:
                    tree(api, 'rename', scope, files['original'], files['aside'])
                phase(api, journal, 'files-aside')
                phase(api, journal, 'files-replace-intent')
                tree(api, 'rename', files['stage'] + '/' + scope, files['restored'], scope)
                phase(api, journal, 'files-ready')
            storage(api, storage_cid, stopped=True)
            phase(api, journal, 'checkpoint-intent')
            api.checkpoint_restore(scope, name, stamp, admitted, record, storage_cid=storage_cid)
            phase(api, journal, 'data-ready')
            phase(api, journal, 'open-intent')
            require_database(api, database, stage_oid)
            require_database(api, previous, original_oid)
            api.sql('ALTER DATABASE ' + database + ' ALLOW_CONNECTIONS true;')
            phase(api, journal, 'opened')
        except BaseException:
            # Recovery is explicit. A phase-write failure or process interruption
            # must never trigger an inferred destructive rollback from local flags.
            raise api.BackupError('Restore interrupted; preserve all data and run: backup.py recover-restore '
                                  + scope + ' ' + name + ' ' + stamp) from None
    result = api.complete_restore(scope, name, stamp)
    phase(api, journal, 'completed')
    return result


def recover(api, scope, name, stamp):
    journal = operation.read_journal(api.STATE, scope)
    if (journal['backup'], journal['stamp']) != (name, stamp):
        raise api.BackupError('Restore recovery identity differs')
    archive = api.BACKUPS / scope / name
    manifest = api.verify_storage(archive) if scope == api.STORAGE else api.verify(scope, archive)
    admitted = api.preflight()
    if admitted['db'] != journal['container_id'] or journal['archive'] != {
        'manifest': api.digest(archive / 'manifest.json'), 'database': api.digest(archive / 'database.dump'),
        'objects': None if scope == api.STORAGE else api.digest(archive / 'objects.tar')} \
            or journal['record']['counts'] != manifest['counts']:
        raise api.BackupError('Restore recovery archive or container identity differs')
    storage(api, journal['storage_cid'])
    state = inventory(api, journal)
    action = operation.recovery_database_action(journal, state)
    active_checkpoint = recovery_checkpoint(api, journal)
    if action == 'preserve-original-writes':
        if active_checkpoint is not None:
            raise api.BackupError('Original reopening has an active restore checkpoint')
        require_database(api, journal['database'], journal['original_oid'])
        if not state[journal['database']]['allow_connections']:
            api.sql('ALTER DATABASE ' + journal['database'] + ' ALLOW_CONNECTIONS true;')
        phase(api, journal, 'original-opened')
        api.wait_storage(api.start_storage())
        if scope != api.STORAGE:
            api.start_services(scope)
            api.wait_healthy(scope)
        phase(api, journal, 'rolled-back')
        return {'status': 'rolled-back', 'backup': name}
    if action == 'preserve-restored-writes' or journal['phase'] == 'open-intent' \
            or active_checkpoint is not None and active_checkpoint['status'] == 'completed':
        api.load_completion(scope, name, stamp)
        if state[journal['database']] is None or state[journal['database']]['oid'] != journal['stage_oid']:
            raise api.BackupError('Restore reopening identity differs')
        require_database(api, journal['database'], journal['stage_oid'])
        api.sql('ALTER DATABASE ' + journal['database'] + ' ALLOW_CONNECTIONS true;')
        phase(api, journal, 'opened')
        result = api.complete_restore(scope, name, stamp)
        phase(api, journal, 'completed')
        return result
    workers = json.loads(api.sql(operation.WORKER_QUERY))
    operation.admit_workers(workers, image=api.image_pin('distro-image.lock.json')['id'] if 'native' in workers else None, database=journal['database'])
    api.run(['docker', 'stop', journal['storage_cid']])
    storage(api, journal['storage_cid'], stopped=True)
    if scope != api.STORAGE:
        api.run(['docker', 'stop', *api.service_names(scope)])
    if inventory(api, journal) != state:
        raise api.BackupError('Restore recovery identities changed after writer stop')
    # Freeze both admitted OIDs once more before verifying immutable file trees.
    for db, row in state.items():
        if row is not None:
            require_database(api, db, row['oid'])
            api.sql('ALTER DATABASE ' + db + ' ALLOW_CONNECTIONS false;')
            require_database(api, db, row['oid'])
            drain(api, db)
    state = inventory(api, journal)
    action = operation.recovery_database_action(journal, state)
    files = journal['files']
    if files is not None and files['original'] is not None:
        current, aside = tree(api, 'identity', scope), tree(api, 'identity', files['aside'])
        old, new = files['original'], files['restored']
        if current != old and current != new and current['exists'] or aside['exists'] and aside != old:
            raise api.BackupError('Restore recovery object tree identity differs')
        if old['exists'] and current != old and aside != old:
            raise api.BackupError('Restore recovery original objects are unavailable')
        phase(api, journal, 'rollback-intent')
        if current != old and current['exists']:
            tree(api, 'remove', scope, new)
        if aside['exists']:
            tree(api, 'rename', files['aside'], old, scope)
    phase(api, journal, 'rollback-intent')
    if action == 'rollback-fenced-cutover':
        require_database(api, journal['database'], journal['stage_oid'])
        api.sql('ALTER DATABASE ' + journal['database'] + ' RENAME TO ' + journal['stage'] + ';')
        require_database(api, journal['previous'], journal['original_oid'])
        api.sql('ALTER DATABASE ' + journal['previous'] + ' RENAME TO ' + journal['database'] + ';')
    elif action == 'rollback-first-rename':
        require_database(api, journal['previous'], journal['original_oid'])
        api.sql('ALTER DATABASE ' + journal['previous'] + ' RENAME TO ' + journal['database'] + ';')
    stage_row = inventory(api, journal)[journal['stage']]
    if journal['stage_oid'] is not None and stage_row is not None and stage_row['oid'] == journal['stage_oid']:
        require_database(api, journal['stage'], journal['stage_oid'])
        api.sql('DROP DATABASE ' + journal['stage'] + ';')
    if active_checkpoint is not None:
        checkpoint = api.completion_path(scope)
        checkpoint.unlink()
        api.fsync_directory(checkpoint.parent)
    phase(api, journal, 'rollback-open-intent')
    require_database(api, journal['database'], journal['original_oid'])
    api.sql('ALTER DATABASE ' + journal['database'] + ' ALLOW_CONNECTIONS true;')
    phase(api, journal, 'original-opened')
    api.wait_storage(api.start_storage())
    if scope != api.STORAGE:
        api.start_services(scope)
        api.wait_healthy(scope)
    phase(api, journal, 'rolled-back')
    return {'status': 'rolled-back', 'backup': name}
