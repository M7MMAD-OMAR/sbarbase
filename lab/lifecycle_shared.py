"""Unadmitted shared lifecycle source and exact ownership reconciliation helpers.

No trusted complete tenant writer and file isolation contract exists yet. Shared
migration and lifecycle entry points refuse before any external resource effect.
Shared engines and global roles are never removed.
"""
import hashlib
import ctypes
import signal
import sys
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import tempfile
import time
import uuid


class SharedRefusal(RuntimeError):
    pass


def require_writer_isolation():
    """A CID list or installation label cannot prove exclusive tenant file access."""
    raise SharedRefusal('Shared writer isolation unavailable')


def validate(resource):
    identity = resource.get('identity')
    runtime = resource['runtime']
    if (resource.get('kind') != 'shared-database' or resource['id'] != runtime
            or not isinstance(identity, dict) or set(identity) != {'engine', 'database', 'roles', 'tenant', 'files'}):
        raise SharedRefusal('shared_identity_required')
    engine, database, roles, tenant = (identity[key] for key in ('engine', 'database', 'roles', 'tenant'))
    if (set(engine) != {'id', 'owner', 'daemon'} or not re.fullmatch(r'[a-f0-9]{64}', engine['id'])
            or not engine['owner'] or engine['owner'] in ('recovery-target', 'restore-target')
            or set(database) != {'name', 'oid', 'ownerOid'} or database['name'] != runtime
            or any(type(database[key]) is not int or database[key] < 1 for key in ('oid', 'ownerOid'))):
        raise SharedRefusal('shared_database_identity_invalid')
    expected = {runtime + '_' + suffix for suffix in ('auth', 'rest', 'storage')}
    if not isinstance(roles, list) or not expected.issubset({role.get('name') for role in roles}):
        raise SharedRefusal('shared_roles_incomplete')
    names = set()
    for role in roles:
        if (set(role) != {'name', 'oid', 'login'} or role['name'] not in
                {runtime + '_' + suffix for suffix in ('auth', 'rest', 'storage', 'studio', 'developer')}
                or type(role['oid']) is not int or role['oid'] < 1 or type(role['login']) is not bool
                or role['name'] in names):
            raise SharedRefusal('shared_role_identity_invalid')
        names.add(role['name'])
    if (set(tenant) != {'database', 'oid', 'id', 'rowDigest', 'writers'}
            or tenant['database'] != 'storage_metadata' or tenant['id'] != runtime
            or type(tenant['oid']) is not int or tenant['oid'] < 1
            or not re.fullmatch(r'[a-f0-9]{64}', tenant['rowDigest'])
            or not isinstance(tenant['writers'], list) or not tenant['writers']
            or any(not re.fullmatch(r'[a-f0-9]{64}', cid) for cid in tenant['writers'])):
        raise SharedRefusal('shared_tenant_identity_invalid')
    files=identity['files']
    if (not isinstance(files,dict) or set(files)!={'id','resource','device','inode','marker'}
            or not isinstance(files['id'],str) or not files['id'].endswith('/'+runtime)
            or not re.fullmatch(r'[a-f0-9-]{36}',files['resource'])
            or files['marker']!='.sbarbase-lifecycle-owner.json'
            or any(type(files[key]) is not int or files[key]<1 for key in ('device','inode'))):
        raise SharedRefusal('shared_tenant_files_identity_required')
    return identity


def bind_child_parent():
    if sys.platform != 'linux':
        raise SharedRefusal('shared_sql_requires_linux')
    parent=os.getppid()
    if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGTERM,0,0,0)!=0:
        os._exit(1)
    if os.getppid()!=parent:
        os._exit(1)


class SQL:
    """One held psql session and PostgreSQL advisory ownership across all effects."""
    def __init__(self, docker, engine, database, resource):
        self.diagnostics = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(['docker', '--host', 'unix://' + docker.path, 'exec', '-i', engine,
            'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.diagnostics,preexec_fn=bind_child_parent)
        except Exception:
            self.diagnostics.close()
            raise
        self.closed=False
        self.buffer = b''
        try:
            self.command('SET statement_timeout=\'10s\'; SET lock_timeout=\'5s\';')
            key = int.from_bytes(hashlib.sha256(resource.encode()).digest()[:8], 'big', signed=True)
            result = self.command(f'SELECT pg_try_advisory_lock({key});')
            if result != ['t']:
                raise SharedRefusal('shared_sql_ownership_busy')
        except Exception:
            self.close()
            raise

    def command(self, query):
        sentinel = '__sbarbase_lifecycle_end__'
        try:
            self.process.stdin.write((query + "\nSELECT '" + sentinel + "';\n").encode())
            self.process.stdin.flush()
            lines, deadline = [], time.monotonic() + 15
            while True:
                while b'\n' in self.buffer:
                    line, self.buffer = self.buffer.split(b'\n', 1)
                    if line.decode() == sentinel:
                        return lines
                    lines.append(line.decode())
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise SharedRefusal('shared_sql_timeout')
                block = os.read(self.process.stdout.fileno(), 65536)
                if not block:
                    raise SharedRefusal('shared_sql_refused')
                self.buffer += block
                if len(self.buffer) > 1024 * 1024:
                    raise SharedRefusal('shared_sql_output_unbounded')
        except (OSError, UnicodeError) as error:
            raise SharedRefusal('shared_sql_unavailable') from error

    def one(self, query):
        result = self.command(query)
        if len(result) != 1:
            raise SharedRefusal('shared_sql_identity_unavailable')
        return result[0]

    def close(self):
        if self.closed:
            return
        self.closed=True
        try:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self.diagnostics.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def writer_ready(writer,resource=None,state=None):
    if writer.get('State',{}).get('Running') is not True:
        return False
    if (writer.get('Config',{}).get('Healthcheck') or {}).get('Test'):
        return writer['State'].get('Health',{}).get('Status')=='healthy'
    if resource is None or state is None:
        return False
    from urllib.parse import urlsplit
    import http.client
    endpoints=json.loads((Path(state)/'endpoints.json').read_text())
    endpoint=urlsplit(endpoints.get(resource['runtime'],{}).get('storage',{}).get('url',''))
    networks=writer.get('NetworkSettings',{}).get('Networks') or {}
    if (endpoint.scheme!='http' or endpoint.port!=5000 or endpoint.username or endpoint.password
            or endpoint.path not in ('','/') or endpoint.query or endpoint.fragment
            or not any(network.get('IPAddress')==endpoint.hostname for network in networks.values())):
        raise SharedRefusal('shared_file_writer_endpoint_identity_mismatch')
    connection=http.client.HTTPConnection(endpoint.hostname,endpoint.port,timeout=2)
    try:
        connection.request('GET','/status')
        return connection.getresponse().status==200
    except (OSError,http.client.HTTPException):
        return False
    finally:
        connection.close()


def writer_identities(docker,identity,drained=False,ready=False,resource=None,state=None):
    result=[]
    for cid in identity['tenant']['writers']:
        _,writer=docker.request('GET','/containers/'+cid+'/json')
        marks=writer.get('Config',{}).get('Labels') or {}
        status=writer.get('State',{})
        if (writer.get('Id')!=cid or marks.get('io.sbarbase.owner')!=identity['engine']['owner']
                or type(status.get('Running')) is not bool
                or any(key in marks for key in ('io.sbarbase.retained','io.sbarbase.recovery'))):
            raise SharedRefusal('shared_file_writer_identity_mismatch')
        if drained and status['Running']:
            raise SharedRefusal('shared_file_writer_not_drained')
        if ready and not writer_ready(writer,resource,state):
            raise SharedRefusal('shared_file_writer_readiness_unproven')
        result.append(writer)
    return result


def engine_identity(docker, identity, writers=False):
    if docker is None or docker.daemon_id != identity['engine']['daemon']:
        raise SharedRefusal('shared_daemon_identity_mismatch')
    engine = identity['engine']
    _, item = docker.request('GET', '/containers/' + engine['id'] + '/json')
    labels = item.get('Config', {}).get('Labels') or {}
    if (item.get('Id') != engine['id'] or labels.get('io.sbarbase.owner') != engine['owner']
            or any(key in labels for key in ('io.sbarbase.retained', 'io.sbarbase.recovery'))
            or item.get('State', {}).get('Running') is not True):
        raise SharedRefusal('shared_engine_identity_mismatch')
    writer_identities(docker,identity,drained=writers)
    return engine['id']


def marker(resource):
    return 'sbarbase-lifecycle:' + resource['installation'] + ':' + resource['resource'] + ':' + resource['runtime']


def database_row(sql, name):
    raw = sql.one(f"SELECT coalesce((SELECT json_build_object('oid',oid,'owner',datdba,'allow',datallowconn,'comment',shobj_description(oid,'pg_database')) FROM pg_database WHERE datname='{name}')::text,'null');")
    return json.loads(raw)


def roles_rows(sql, identity):
    names = ','.join("'" + role['name'] + "'" for role in identity['roles'])
    return json.loads(sql.one(f"SELECT coalesce(json_agg(json_build_object('name',rolname,'oid',oid,'login',rolcanlogin,'comment',shobj_description(oid,'pg_authid'),'super',rolsuper,'createRole',rolcreaterole,'createDB',rolcreatedb,'replication',rolreplication,'bypass',rolbypassrls) ORDER BY rolname)::text,'[]') FROM pg_roles WHERE rolname IN ({names});"))


def check_database(row, identity, resource, migrated=True):
    wanted = identity['database']
    if (row is None or row['oid'] != wanted['oid'] or row['owner'] != wanted['ownerOid']
            or migrated and row['comment'] != marker(resource)
            or not migrated and row['comment'] not in (None, marker(resource))):
        raise SharedRefusal('shared_database_oid_or_owner_mismatch')


def check_roles(rows, identity, resource, migrated=True, allow_missing=False):
    by_name = {row['name']: row for row in rows}
    for wanted in identity['roles']:
        row = by_name.get(wanted['name'])
        if row is None and allow_missing and wanted['name'] in allow_missing:
            continue
        if (row is None or row['oid'] != wanted['oid'] or any(row.get(key) for key in ('super','createRole','createDB','replication','bypass')) or migrated and row['comment'] != marker(resource)
                or not migrated and row['comment'] not in (None, marker(resource))):
            raise SharedRefusal('shared_role_oid_or_owner_mismatch')


def tenant_value(sql, identity):
    tenant = identity['tenant']
    row = database_row(sql, tenant['database'])
    if row is None or row['oid'] != tenant['oid']:
        raise SharedRefusal('storage_metadata_oid_mismatch')


def write_journal(path, value):
    if path.is_symlink():
        raise SharedRefusal('shared_journal_unsafe')
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.new')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def migration_receipt(state,resource,files,operation,epoch):
    path=Path(state)/('lifecycle-migration-'+resource['resource']+'.json')
    identity={'version':1,'resource':resource,'files':files,'operation':operation,'epoch':epoch}
    if path.exists():
        metadata=path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1 or metadata.st_uid!=os.getuid()
                or metadata.st_mode&0o077 or metadata.st_size>65536):
            raise SharedRefusal('shared_migration_receipt_unsafe')
        record=json.loads(path.read_text())
        if any(record.get(key)!=value for key,value in identity.items()):
            raise SharedRefusal('shared_migration_receipt_identity_mismatch')
    else:
        record={**identity,'stages':{}}
    return path,record


def publish_marker(directory,resource,files,operation,receipt_path,receipt):
    value={'installation':files['installation'],'runtime':files['runtime'],'resource':files['resource']}
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    temporary=files['marker']+'.'+operation+'.tmp'
    try:
        if (os.fstat(fd).st_dev,os.fstat(fd).st_ino)!=(files['device'],files['inode']):
            raise SharedRefusal('tenant_file_oid_mismatch')
        try:
            marker_fd=os.open(files['marker'],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
        except FileNotFoundError:
            marker_fd=None
        if marker_fd is not None:
            with os.fdopen(marker_fd,'r') as handle:
                metadata=os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1 or metadata.st_size>4096 or json.load(handle)!=value:
                    raise SharedRefusal('tenant_file_marker_collision')
        else:
            # The durable pending receipt reserves this exact internal temporary name.
            try:
                metadata=os.stat(temporary,dir_fd=fd,follow_symlinks=False)
                if (receipt['stages'].get('marker')!='pending' or not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink!=1 or metadata.st_uid!=os.getuid() or metadata.st_size>4096):
                    raise SharedRefusal('tenant_marker_temporary_collision')
                os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError:
                pass
            receipt['stages']['marker']='pending'
            write_journal(receipt_path,receipt)
            descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(descriptor,'w') as handle:
                json.dump(value,handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(temporary,files['marker'],src_dir_fd=fd,dst_dir_fd=fd)
            os.fsync(fd)
        os.fsync(fd)
        receipt['stages']['marker']='done'
        write_journal(receipt_path,receipt)
    finally:
        os.close(fd)


def migrate(resource, docker, root, state, operation, epoch, files):
    """Unadmitted migration source, gated before SQL or marker publication."""
    require_writer_isolation()
    identity=validate(resource)
    engine=engine_identity(docker,identity,writers=True)
    if (files.get('kind')!='directory' or files['runtime']!=resource['runtime']
            or files['installation']!=resource['installation'] or Path(files['id'])!=root/resource['runtime']):
        raise SharedRefusal('exact_tenant_file_identity_required')
    directory=Path(files['id'])
    if directory.is_symlink() or (directory.stat().st_dev,directory.stat().st_ino)!=(files['device'],files['inode']):
        raise SharedRefusal('tenant_file_oid_mismatch')
    if identity['files']!={key:files[key] for key in ('id','resource','device','inode','marker')}:
        raise SharedRefusal('paired_tenant_file_identity_mismatch')
    receipt_path,receipt=migration_receipt(state,resource,files,operation,epoch)
    mark=directory/files['marker']
    value={'installation':files['installation'],'runtime':files['runtime'],'resource':files['resource']}
    if mark.exists() and (mark.is_symlink() or json.loads(mark.read_text())!=value):
        raise SharedRefusal('tenant_file_marker_collision')
    from lifecycle_resources import tree
    descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        tree(descriptor,files['device'])
    finally:
        os.close(descriptor)
    with SQL(docker,engine,'storage_metadata',resource['resource']) as storage:
        actual=storage.one(f"SELECT to_jsonb(t)::text FROM public.tenants t WHERE id='{resource['runtime']}';")
        if hashlib.sha256(actual.encode()).hexdigest()!=identity['tenant']['rowDigest']:
            raise SharedRefusal('tenant_configuration_identity_mismatch')
    with SQL(docker,engine,'postgres',resource['resource']) as sql:
        sql.command('BEGIN;')
        row=database_row(sql,resource['runtime'])
        roles=roles_rows(sql,identity)
        check_database(row,identity,resource,migrated=False)
        check_roles(roles,identity,resource,migrated=False)
        tenant_value(sql,identity)
        settled=row['comment']==marker(resource) and all(role['comment']==marker(resource) for role in roles)
        if not settled:
            receipt['stages']['sql']='pending'
            write_journal(receipt_path,receipt)
            sql.command(f"COMMENT ON DATABASE {resource['runtime']} IS '{marker(resource)}';")
            for role in identity['roles']:
                sql.command(f"COMMENT ON ROLE {role['name']} IS '{marker(resource)}';")
        sql.command('COMMIT;')
        receipt['stages']['sql']='done'
        write_journal(receipt_path,receipt)
    publish_marker(directory,resource,files,operation,receipt_path,receipt)
    return {'outcome':'present','reclaimedBytes':0}


def execute(resource, action, docker, root, state, operation, epoch):
    require_writer_isolation()
    identity = validate(resource)
    engine = engine_identity(docker, identity, writers=action in ('quarantine','purge','inspect-quarantined'))
    path = Path(state) / ('lifecycle-shared-' + resource['resource'] + '-' + operation + '.json')
    journal = {'resource': resource, 'operation': operation, 'epoch': epoch, 'stages': {}}
    if path.exists():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
            raise SharedRefusal('shared_journal_unsafe')
        journal = json.loads(path.read_text())
        if journal['resource'] != resource or journal['operation'] != operation or journal['epoch'] != epoch:
            raise SharedRefusal('shared_journal_identity_mismatch')
    from lifecycle_resources import open_directory,tree
    file_resource={**identity['files'],'kind':'directory','runtime':resource['runtime'],'installation':resource['installation']}
    opened=open_directory(root,file_resource)
    if opened is None:
        if not journal['stages'].get('tenant')=='done':
            raise SharedRefusal('shared_tenant_files_missing')
    else:
        file_fd,_,_=opened
        try:
            tree(file_fd,file_resource['device'])
        finally:
            os.close(file_fd)
    with SQL(docker,engine,'storage_metadata',resource['resource']) as storage:
        lines=storage.command(f"SELECT to_jsonb(t)::text FROM public.tenants t WHERE id='{resource['runtime']}';")
        if lines:
            if len(lines)!=1 or hashlib.sha256(lines[0].encode()).hexdigest()!=identity['tenant']['rowDigest']:
                raise SharedRefusal('tenant_configuration_identity_mismatch')
        elif journal['stages'].get('tenant') not in ('pending','done'):
            raise SharedRefusal('tenant_absence_unproven')
    with SQL(docker, engine, 'postgres', resource['resource']) as sql:
        row = database_row(sql, resource['runtime'])
        if row is None:
            if journal['stages'].get('database') not in ('pending', 'done'):
                raise SharedRefusal('shared_database_absence_unproven')
        else:
            check_database(row, identity, resource)
        roles = roles_rows(sql, identity)
        missing={key for key,value in journal['stages'].items() if value in ('pending','done')}
        check_roles(roles, identity, resource, allow_missing=missing)
        tenant_value(sql, identity)
        # A role shared with a restored copy is never removed, including after source DB removal.
        oids = ','.join(str(role['oid']) for role in identity['roles'])
        foreign = sql.one(f"SELECT count(*) FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid IN ({oids}) AND dbid NOT IN (0,{identity['database']['oid']});")
        if foreign != '0':
            raise SharedRefusal('restore_copy_role_dependencies_retained')
        if sql.one(f"SELECT count(*) FROM pg_replication_slots WHERE database='{resource['runtime']}';")!='0':
            raise SharedRefusal('shared_replication_slots_retained')
        if sql.one(f"SELECT count(*) FROM pg_subscription WHERE subdbid={identity['database']['oid']};")!='0':
            raise SharedRefusal('shared_subscriptions_retained')
        if action=='readiness':
            if row is None or not row['allow'] or any(actual['login']!=next(wanted['login'] for wanted in identity['roles'] if wanted['name']==actual['name']) for actual in roles):
                raise SharedRefusal('shared_database_readiness_unproven')
            writer_identities(docker,identity,ready=True,resource=resource,state=state)
            return {'outcome':'restored','reclaimedBytes':0}
        if action in ('inspect','inspect-quarantined'):
            size = int(sql.one(f"SELECT pg_database_size({identity['database']['oid']});")) if row else 0
            complete=row is None and not roles and journal['stages'].get('tenant')=='done'
            return {'outcome': 'absent' if complete else 'present', 'reclaimedBytes': 0, 'observedBytes': size}
        if action == 'quarantine':
            sql.command('BEGIN;' + ''.join('ALTER ROLE ' + role['name'] + ' NOLOGIN;' for role in identity['roles']) +
                        'ALTER DATABASE ' + resource['runtime'] + ' ALLOW_CONNECTIONS false; COMMIT;')
            sql.command(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{resource['runtime']}' AND pid<>pg_backend_pid();")
            if sql.one(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{resource['runtime']}';") != '0':
                raise SharedRefusal('shared_sessions_not_drained')
            return {'outcome': 'quarantined', 'reclaimedBytes': 0}
        if action == 'restore':
            if row is None:
                raise SharedRefusal('shared_database_missing')
            sql.command('BEGIN; ALTER DATABASE ' + resource['runtime'] + ' ALLOW_CONNECTIONS true;' +
                ''.join('ALTER ROLE ' + role['name'] + (' LOGIN;' if role['login'] else ' NOLOGIN;') for role in identity['roles']) + 'COMMIT;')
            return {'outcome': 'restored', 'reclaimedBytes': 0}
        if action != 'purge':
            raise SharedRefusal('unsupported_shared_action')
        if row is not None:
            if row['allow'] or any(role['login'] for role in roles):
                raise SharedRefusal('shared_database_not_quarantined')
            if sql.one(f"SELECT (SELECT count(*) FROM pg_stat_activity WHERE datname='{resource['runtime']}')+(SELECT count(*) FROM pg_prepared_xacts WHERE database='{resource['runtime']}');") != '0':
                raise SharedRefusal('shared_transactions_not_drained')
            journal['stages']['database'] = 'pending'
            write_journal(path, journal)
            sql.command('DROP DATABASE ' + resource['runtime'] + ';')
        journal['stages']['database'] = 'done'
        write_journal(path, journal)
        for role in identity['roles']:
            if role['name'] in {item['name'] for item in roles}:
                journal['stages'][role['name']] = 'pending'
                write_journal(path, journal)
                sql.command('DROP ROLE ' + role['name'] + ';')
            journal['stages'][role['name']] = 'done'
            write_journal(path, journal)
    with SQL(docker, engine, 'storage_metadata', resource['resource']) as storage:
        storage.command('BEGIN;')
        lines = storage.command(f"SELECT to_jsonb(t)::text FROM public.tenants t WHERE id='{resource['runtime']}' FOR UPDATE;")
        if lines:
            if len(lines)!=1 or hashlib.sha256(lines[0].encode()).hexdigest()!=identity['tenant']['rowDigest']:
                raise SharedRefusal('tenant_configuration_identity_mismatch')
            journal['stages']['tenant'] = 'pending'
            write_journal(path, journal)
            storage.command(f"DELETE FROM public.tenants_jwks WHERE tenant_id='{resource['runtime']}'; DELETE FROM public.tenants WHERE id='{resource['runtime']}'; COMMIT;")
        elif journal['stages'].get('tenant') not in ('pending','done'):
            raise SharedRefusal('tenant_absence_unproven')
        else:
            storage.command('ROLLBACK;')
        journal['stages']['tenant']='done'
        write_journal(path,journal)
    return {'outcome': 'purged', 'reclaimedBytes': 0}


def validate_legacy_service(resource):
    legacy=resource.get('legacy')
    if (resource.get('kind')!='container' or resource.get('service') not in ('auth','rest')
            or not isinstance(legacy,dict) or set(legacy)!={'owner','image','createdAt','configDigest','endpoint','databaseEngine','databaseOid'}
            or not legacy['owner'] or legacy['owner'] in ('recovery-target','restore-target')
            or not re.fullmatch(r'sha256:[a-f0-9]{64}',legacy['image'])
            or not re.fullmatch(r'[a-f0-9]{64}',legacy['configDigest'])
            or not re.fullmatch(r'[a-f0-9]{64}',legacy['databaseEngine'])
            or type(legacy['databaseOid']) is not int or legacy['databaseOid']<1
            or not isinstance(legacy['createdAt'],str) or not legacy['createdAt']):
        raise SharedRefusal('legacy_service_identity_required')
    return legacy


def observe_legacy_service(docker,resource):
    legacy=validate_legacy_service(resource)
    code,item=docker.request('GET','/containers/'+resource['id']+'/json?size=true',(200,404))
    if code==404:
        _,inventory=docker.request('GET','/containers/json?all=true')
        if not isinstance(inventory,list) or any(container.get('Id')==resource['id'] for container in inventory):
            raise SharedRefusal('legacy_service_absence_unproven')
        return None
    labels=item.get('Config',{}).get('Labels') or {}
    config=hashlib.sha256(json.dumps(item.get('Config'),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if (item.get('Id')!=resource['id'] or item.get('Image')!=legacy['image'] or item.get('Created')!=legacy['createdAt']
            or config!=legacy['configDigest'] or labels.get('io.sbarbase.owner')!=legacy['owner']
            or any(key in labels for key in ('io.sbarbase.retained','io.sbarbase.recovery'))):
        raise SharedRefusal('legacy_service_positive_identity_mismatch')
    return item


def inspect_legacy_service(docker,state,resource,value=None):
    path=Path(state)/('lifecycle-service-'+resource['resource']+'.json')
    metadata=path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1 or metadata.st_uid!=os.getuid()
            or metadata.st_mode&0o077):
        raise SharedRefusal('legacy_service_enrollment_unavailable')
    record=json.loads(path.read_text())
    if record!={'version':1,'daemon':docker.daemon_id,'resource':resource}:
        raise SharedRefusal('legacy_service_enrollment_identity_mismatch')
    return observe_legacy_service(docker,resource)


def migrate_legacy_service(resource,shared_resource,docker,state,secrets_path):
    """Unadmitted migration source with positive legacy service bindings."""
    require_writer_isolation()
    from urllib.parse import urlsplit,unquote
    legacy=validate_legacy_service(resource)
    identity=validate(shared_resource)
    if (resource['runtime']!=shared_resource['runtime'] or resource['installation']!=shared_resource['installation']
            or legacy['databaseEngine']!=identity['engine']['id'] or legacy['databaseOid']!=identity['database']['oid']
            or legacy['owner']!=identity['engine']['owner']):
        raise SharedRefusal('legacy_service_database_binding_mismatch')
    engine=engine_identity(docker,identity)
    item=observe_legacy_service(docker,resource)
    if item is None or type(item.get('State',{}).get('Running')) is not bool:
        raise SharedRefusal('legacy_service_migration_requires_observed_identity')
    # Secrets and publication are explicit installation inputs; their fields never leave this process.
    secrets_path=Path(secrets_path)
    if (not secrets_path.is_absolute() or any(path.is_symlink() for path in (secrets_path,*secrets_path.parents))
            or not stat.S_ISREG(secrets_path.stat().st_mode) or secrets_path.stat().st_mode&0o077
            or secrets_path.stat().st_uid!=os.getuid()):
        raise SharedRefusal('legacy_service_private_configuration_required')
    secrets=json.loads(secrets_path.read_text())['environments'][resource['runtime']]
    endpoints=json.loads((Path(state)/'endpoints.json').read_text())
    service=resource['service']
    if endpoints.get(resource['runtime'],{}).get(service)!=legacy['endpoint']:
        raise SharedRefusal('legacy_service_publication_binding_mismatch')
    _,engine_item=docker.request('GET','/containers/'+engine+'/json')
    engine_names={engine_item.get('Name','').removeprefix('/')}
    engine_networks=engine_item.get('NetworkSettings',{}).get('Networks') or {}
    for network in engine_networks.values():
        engine_names.add(network.get('IPAddress'))
    environment={entry.split('=',1)[0]:entry.split('=',1)[1] for entry in item['Config'].get('Env',[]) if '=' in entry}
    database_url=environment.get('GOTRUE_DB_DATABASE_URL' if service=='auth' else 'PGRST_DB_URI','')
    database=urlsplit(database_url)
    if (database.scheme not in ('postgres','postgresql') or unquote(database.username or '')!=resource['runtime']+'_'+service
            or unquote(database.password or '')!=secrets[service] or database.hostname not in engine_names
            or database.port!=5432 or database.path!='/'+resource['runtime']
            or environment.get('GOTRUE_JWT_SECRET' if service=='auth' else 'PGRST_JWT_SECRET')!=secrets['jwt']):
        raise SharedRefusal('legacy_service_private_runtime_binding_mismatch')
    endpoint=urlsplit(legacy['endpoint'])
    networks=item.get('NetworkSettings',{}).get('Networks') or {}
    if (endpoint.scheme!='http' or endpoint.port!=(9999 if service=='auth' else 3000)
            or endpoint.username or endpoint.password or endpoint.path not in ('','/') or endpoint.query or endpoint.fragment
            or not any(name in engine_networks and network.get('NetworkID')==engine_networks[name].get('NetworkID')
                       and network.get('IPAddress')==endpoint.hostname for name,network in networks.items())):
        raise SharedRefusal('legacy_service_network_binding_mismatch')
    with SQL(docker,engine,'postgres',shared_resource['resource']) as sql:
        check_database(database_row(sql,resource['runtime']),identity,shared_resource)
        roles=roles_rows(sql,identity)
        check_roles(roles,identity,shared_resource)
        if resource['runtime']+'_'+service not in {role['name'] for role in roles}:
            raise SharedRefusal('legacy_service_role_identity_missing')
    record={'version':1,'daemon':docker.daemon_id,'resource':resource}
    path=Path(state)/('lifecycle-service-'+resource['resource']+'.json')
    if path.exists():
        inspect_legacy_service(docker,state,resource)
    else:
        write_journal(path,record)
    return {'outcome':'present','reclaimedBytes':0,'observedBytes':item.get('SizeRw',0)}


def legacy_service_ready(docker,state,resource,item):
    from urllib.parse import urlsplit
    import http.client
    inspect_legacy_service(docker,state,resource,item)
    endpoint=urlsplit(resource['legacy']['endpoint'])
    networks=item.get('NetworkSettings',{}).get('Networks') or {}
    if (endpoint.scheme!='http' or endpoint.port!=(9999 if resource['service']=='auth' else 3000)
            or not any(network.get('IPAddress')==endpoint.hostname for network in networks.values())):
        raise SharedRefusal('legacy_service_readiness_endpoint_mismatch')
    connection=http.client.HTTPConnection(endpoint.hostname,endpoint.port,timeout=2)
    try:
        connection.request('GET','/health' if resource['service']=='auth' else '/')
        return connection.getresponse().status==200
    except (OSError,http.client.HTTPException):
        return False
    finally:
        connection.close()
