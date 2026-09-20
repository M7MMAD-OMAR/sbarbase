"""Bounded, persistent upstream runtime. Experimental, local and unpublished."""
import effect_receipt
import hba_runtime
import hba_startup
from guarded_sql_executor import GuardedSQL
import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import run as lab
import resource_admission
import connection_budget
import pressure_admission
import source_fence

class AdmissionLimitError(RuntimeError):
    pass


OWNER = 'durable-upstream'
PREFIX = 'sbarbase-durable'
DB = PREFIX + '-db'
NETWORK = PREFIX + '-net'
STATE = lab.STATE / 'upstream'
PRIVATE = lab.PRIVATE / 'upstream'


def atomic(path, value):
    pending = path.with_suffix('.pending')
    lab.secure_file(pending, json.dumps(value))
    with pending.open('rb') as handle:
        os.fsync(handle.fileno())
    os.replace(pending, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def inspect(kind, name):
    result = lab.docker(kind, 'inspect', name, check=False) if kind != 'container' else lab.docker('inspect', name, check=False)
    if result.returncode:
        # An inspect transport/error result is not proof of absence.
        listing = {'container': ('ps','-a','--no-trunc','--format','{{.ID}} {{.Names}}'),
                   'volume': ('volume','ls','--format','{{.Name}}'),
                   'network': ('network','ls','--no-trunc','--format','{{.ID}} {{.Name}}')}[kind]
        entries=[line.split() for line in lab.docker(*listing).stdout.splitlines()]
        if any(name in fields or (kind!='volume' and len(name)>=12 and fields and fields[0].startswith(name)) for fields in entries):
            raise RuntimeError('Runtime resource inspection unavailable')
        return None
    item = json.loads(result.stdout)[0]
    labels = item.get('Config', {}).get('Labels', {}) if kind == 'container' else item.get('Labels', {})
    if (labels or {}).get('io.sbarbase.owner') != OWNER:
        raise RuntimeError('Runtime resource ownership collision')
    return item


def http(url, method='GET', body=None, headers=None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def token(secret, role):
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    # Stable internal tenant keys survive retries; never exposed to applications.
    message = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({'role': role, 'iss': 'sbarbase-internal'})
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    return message + '.' + signature


class Runtime:
    def __init__(self,*,startup=None,operation_fd=None,worker_runtime=None):
        STATE.mkdir(parents=True, exist_ok=True)
        PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(PRIVATE, 0o700)
        # Verify ignore rules before persisting generated credentials.
        import subprocess
        if subprocess.run(['git', 'check-ignore', '-q', str(PRIVATE/'runtime.json')], cwd=lab.ROOT).returncode:
            raise RuntimeError('Runtime secrets must be ignored')
        self.pins = json.loads((lab.ROOT/'lab/images.lock.json').read_text())
        for component, filename in [('db', 'distro-image.lock.json'), ('storage', 'storage-image.lock.json')]:
            self.pins[component] = json.loads((lab.ROOT/'lab'/filename).read_text())
        self.hba_writer = (hba_runtime.SourceHBA(lab.docker,STATE,DB,OWNER,self.pins['db']['id'],startup=startup,operation_fd=operation_fd)
                           if startup is not None or operation_fd is not None else None)
        if startup is not None:
            self.hba_writer.before_start(inspect('container',DB),inspect('volume',PREFIX+'-pgdata') is not None)
        elif operation_fd is not None:
            self.hba_writer.worker_preflight(worker_runtime)
        self.path = PRIVATE/'runtime.json'
        if not self.path.exists():
            atomic(self.path, {**{key: secrets.token_hex(32) for key in ('admin', 'storage_control', 'storage_admin', 'encryption')}, 'environments': {}})
        self.values = json.loads(self.path.read_text())
        if 'management' not in self.values:
            self.values['management'] = {k: secrets.token_hex(32) for k in ('auth', 'jwt')}
            atomic(self.path, self.values)

    def sql(self, query, database='postgres', check=True):
        return lab.docker('exec', '-i', DB, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database, '-qAt', data=query, check=check)

    def launch(self, name, component, env, memory, cpus, volumes=(), command=(), existing_only=False):
        image = self.pins[component]['id']
        actual = inspect('container', name)
        if existing_only and not actual:
            raise RuntimeError('Resume cannot create a missing service container')
        if actual:
            expected = json.loads(lab.docker('image', 'inspect', image).stdout)[0]['Id']
            configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
            if actual['Image'] != expected or any(configured.get(k) != v for k, v in env.items()):
                raise RuntimeError('Runtime drift requires explicit reconciliation')
            mounts = {(m.get('Name'), m['Destination']) for m in actual['Mounts']}
            if any((name, destination) not in mounts for name, destination in volumes):
                raise RuntimeError('Runtime persistent volume mismatch')
            if NETWORK not in actual['NetworkSettings']['Networks']:
                raise RuntimeError('Runtime network mismatch')
            lab.docker('start', actual['Id'])
            return actual['Id'],False
        path = PRIVATE/(name+'.env')
        lab.secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
        args = ['run', '-d', '--name', name, '--label', 'io.sbarbase.owner='+OWNER, '--network', NETWORK,
                '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus), '--pids-limit', '128',
                '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(path)]
        for volume, destination in volumes:
            if not inspect('volume', volume):
                lab.docker('volume', 'create', '--label', 'io.sbarbase.owner='+OWNER, volume)
            args += ['-v', volume+':'+destination]
        return lab.docker(*args, image, *command).stdout.strip(),True

    def endpoint(self, name, port):
        item = inspect('container', name)
        address = item['NetworkSettings']['Networks'][NETWORK]['IPAddress']
        if not address:
            raise RuntimeError('Runtime endpoint unavailable')
        return f'http://{address}:{port}'

    def wait(self, url, headers=None):
        for _ in range(60):
            try:
                if http(url, headers=headers)[0] == 200:
                    return
            except (OSError, TimeoutError):
                pass
            time.sleep(.5)
        raise RuntimeError('Runtime readiness timed out')

    def hba(self):
        lines = ['local all supabase_admin trust', 'host storage_metadata storage_control 0.0.0.0/0 scram-sha-256',
                 'host management management_auth 0.0.0.0/0 scram-sha-256']
        for e in self.values['environments']:
            if not re.fullmatch(r'e_[a-f0-9]{24}', e):
                raise RuntimeError('Invalid runtime inventory')
            lines += [f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256' for role in ('auth', 'rest', 'storage')]
        lines += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
        if self.hba_writer is None:raise RuntimeError('Explicit HBA ownership required')
        self.hba_writer.publish('\n'.join(lines)+'\n')

    def reload_hba(self):
        if self.sql('SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;').stdout.strip()!='0':
            raise RuntimeError('HBA parse failure requires explicit reconciliation')
        if self.sql('SELECT pg_reload_conf();').stdout.strip()!='t':
            raise RuntimeError('HBA reload signal was not acknowledged')

    def start(self):
        effect_receipt.require_settled(STATE)
        if lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip():
            raise RuntimeError('Staged source mode requires stopped recovery targets')
        if self.hba_writer is None or self.hba_writer.startup is None:raise RuntimeError('Explicit startup HBA ownership required')
        self.hba_writer.startup.verify()
        available = int(next(x.split()[1] for x in lab.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
        if available < 6*1024*1024:
            raise RuntimeError('Insufficient runtime memory headroom')
        if not inspect('network', NETWORK):
            lab.docker('network', 'create', '--internal', '--label', 'io.sbarbase.owner='+OWNER, NETWORK)
        launched_cid,created=self.launch(DB, 'db', {'POSTGRES_PASSWORD': self.values['admin'], 'POSTGRES_HOST': '/var/run/postgresql', 'POSTGRES_DB': 'postgres'},
                    '1024m', 1, [(PREFIX+'-pgdata', '/var/lib/postgresql/data')],
                    ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none'))
        for _ in range(120):
            result = self.sql("SELECT to_regrole('supabase_privileged_role') IS NOT NULL;", check=False)
            if result.returncode == 0 and result.stdout.strip() == 't' and lab.docker('exec', DB, 'pg_isready', '-h', '127.0.0.1', check=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Database readiness timed out')
        self.hba_writer.ready(launched_cid,created=created)
        if self.sql("SELECT 1 FROM pg_roles WHERE rolname='storage_control';").stdout.strip() != '1':
            self.sql(f"CREATE ROLE storage_control LOGIN NOINHERIT PASSWORD '{self.values['storage_control']}';")
        if self.sql("SELECT 1 FROM pg_database WHERE datname='storage_metadata';").stdout.strip() != '1':
            self.sql('CREATE DATABASE storage_metadata OWNER storage_control;')
        self.sql(f'ALTER ROLE storage_control CONNECTION LIMIT {connection_budget.SERVICE_LIMIT}; ALTER DATABASE storage_metadata CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};')
        self.sql('REVOKE ALL ON DATABASE storage_metadata FROM PUBLIC;')
        # Management publishes the complete inventory HBA before starting Auth.
        self.management()
        self.launch(PREFIX+'-storage', 'storage', {
            'MULTI_TENANT': 'true', 'MULTITENANT_DATABASE_URL': f"postgres://storage_control:{self.values['storage_control']}@{DB}:5432/storage_metadata",
            'ENCRYPTION_KEY': self.values['encryption'], 'ADMIN_API_KEYS': self.values['storage_admin'], 'DB_INSTALL_ROLES': 'false',
            'STORAGE_BACKEND': 'file', 'GLOBAL_S3_BUCKET': 'sbarbase-lab', 'FILE_STORAGE_BACKEND_PATH': '/tmp/storage-data', 'REGION': 'local',
            'FILE_SIZE_LIMIT': '1048576', 'DATABASE_MAX_CONNECTIONS': '3', 'MULTITENANT_DATABASE_MAX_CONNECTIONS': '3',
            'PG_QUEUE_ENABLE': 'false', 'ENABLE_IMAGE_TRANSFORMATION': 'false', 'S3_PROTOCOL_ENABLED': 'false',
            'X_FORWARDED_HOST_REGEXP': r'^(e_[a-f0-9]{24})\.storage\.internal$', 'LOG_LEVEL': 'error'},
            '512m', .5, [(PREFIX+'-objects', '/tmp/storage-data')])
        self.wait(self.endpoint(PREFIX+'-storage', 5001)+'/tenants', {'apikey': self.values['storage_admin']})
        self.resume_published_environments()

    def resume_published_environments(self):
        # Credential reservations must only be dispatched by an authorized worker.
        path = STATE/'endpoints.json'
        published = json.loads(path.read_text()) if path.exists() else {}
        for e in tuple(self.values['environments']):
            if e in published and not source_fence.is_fenced(self.sql,e):
                self.resume(e)

    def resume(self, e):
        """Resume published state without native schema repair or tenant creation."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        path=STATE/'endpoints.json'
        published=json.loads(path.read_text()) if path.exists() else {}
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Resume requires a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        for service in ('auth','rest'):
            if not inspect('container',PREFIX+'-'+e+'-'+service):
                raise RuntimeError('Resume requires retained service containers')
        self.rest_deadlines(e,validate_only=True)
        self.activate_services(e,self.values['environments'][e],creating=False)

    def rest_deadlines(self, e, executor=None, validate_only=False):
        execute = executor or self.sql
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        # Role defaults affect new logins. Refuse silent changes under a warm pool.
        deadline_current = execute(f"SELECT EXISTS(SELECT 1 FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole JOIN pg_database d ON d.oid=s.setdatabase WHERE r.rolname='{e}_rest' AND d.datname='{e}' AND s.setconfig @> ARRAY['statement_timeout=8s','transaction_timeout=12s']);").stdout.strip() == 't'
        if validate_only:
            if not deadline_current:raise RuntimeError('Existing REST deadlines require explicit reconciliation')
            return
        rest_container = inspect('container', PREFIX+'-'+e+'-rest')
        if not deadline_current and rest_container and rest_container.get('State', {}).get('Running'):
            raise RuntimeError('REST deadline changes require stopping the owned runtime first')
        execute(f"ALTER ROLE {e}_rest IN DATABASE {e} SET statement_timeout = '8s'; ALTER ROLE {e}_rest IN DATABASE {e} SET transaction_timeout = '12s';")

    def provision_database(self, e, v, executor=None):
        """All native per-environment SQL, excluding shared HBA and services."""
        execute = executor or self.sql
        lab.provision_environment(e, v, executor=execute)
        execute('CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions; CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions; GRANT USAGE ON SCHEMA extensions TO anon,authenticated,service_role;', e)
        if execute(f"SELECT 1 FROM pg_roles WHERE rolname='{e}_storage';").stdout.strip() != '1':
            execute(f"CREATE ROLE {e}_storage LOGIN NOINHERIT PASSWORD '{v['storage']}';")
        execute('; '.join(f'ALTER ROLE {e}_{role} CONNECTION LIMIT {connection_budget.SERVICE_LIMIT}' for role in ('auth', 'rest', 'storage'))+';')
        execute(f'ALTER DATABASE {e} CONNECTION LIMIT {connection_budget.ENVIRONMENT_LIMIT};')
        self.rest_deadlines(e, executor=execute)
        execute(f'GRANT anon,authenticated,service_role TO {e}_storage; GRANT CONNECT ON DATABASE {e} TO {e}_storage;')
        execute(f'CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION {e}_storage; GRANT USAGE ON SCHEMA storage TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES FOR ROLE {e}_storage IN SCHEMA storage GRANT ALL ON TABLES TO anon,authenticated,service_role; ALTER DEFAULT PRIVILEGES FOR ROLE {e}_storage IN SCHEMA storage GRANT ALL ON SEQUENCES TO anon,authenticated,service_role;', e)

    def provision(self, e):
        effect_receipt.require_permission(STATE,e)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        new_environment = e not in self.values['environments']
        if new_environment or os.environ.get('SBARBASE_EFFECT_TOKEN'):
            # With management Auth: at most four environments, 3840 MiB/3.75 CPUs.
            if len(self.values['environments']) + int(new_environment) > 4:
                raise AdmissionLimitError('Local runtime admission limit reached')
            try:
                reason = resource_admission.refusal(resource_admission.snapshot())
            except Exception:
                raise RuntimeError('Resource measurement unavailable') from None
            if reason:
                raise AdmissionLimitError('Resource headroom unavailable')
            if pressure_admission.refusal(pressure_admission.snapshot()):
                raise AdmissionLimitError('Runtime pressure exceeds admission threshold')
            limits = self.sql("SELECT current_setting('max_connections'), current_setting('superuser_reserved_connections'), current_setting('reserved_connections');").stdout.strip().split('|')
            if len(limits) != 3 or not connection_budget.fits(len(self.values['environments'])+int(new_environment), *(int(value) for value in limits)):
                raise AdmissionLimitError('Connection budget unavailable')
        effect_receipt.sql_identity(STATE,e,'preflight')
        if new_environment:
            self.values['environments'][e] = {k: secrets.token_hex(32) for k in ('auth', 'rest', 'storage', 'jwt')}
            atomic(self.path, self.values)
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        v = self.values['environments'][e]
        effect_receipt.native_stage(STATE,e,'database')
        identity=effect_receipt.sql_identity(STATE,e,'database')
        guarded=GuardedSQL(self.sql,*identity)
        self.provision_database(e, v, executor=guarded)
        guarded.close()
        effect_receipt.native_stage(STATE,e,'services')
        self.hba()
        self.activate_services(e,v,creating=True)

    def activate_services(self,e,v,*,creating):
        endpoints = {}
        for service, builder, port, suffix in [('auth', lab.auth_configuration, 9999, '/health'), ('rest', lab.rest_configuration, 3000, '/')]:
            name = PREFIX+'-'+e+'-'+service
            self.launch(name, service, builder(e, v, DB), '256m', .25, existing_only=not creating)
            endpoints[service] = self.endpoint(name, port)
            self.wait(endpoints[service]+suffix)
        if creating:effect_receipt.native_stage(STATE,e,'storage')
        admin = self.endpoint(PREFIX+'-storage', 5001)
        headers = {'apikey': self.values['storage_admin'], 'content-type': 'application/json'}
        status, _ = http(admin+'/tenants/'+e, headers=headers)
        if status == 404:
            if not creating:raise RuntimeError('Missing Storage tenant requires explicit reconciliation')
            payload = {'anonKey': token(v['jwt'], 'anon'), 'serviceKey': token(v['jwt'], 'service_role'), 'jwtSecret': v['jwt'],
                       'databaseUrl': f"postgres://{e}_storage:{v['storage']}@{DB}:5432/{e}", 'maxConnections': 3,
                       'features': {'s3Protocol': {'enabled': False}, 'imageTransformation': {'enabled': False}}}
            status, _ = http(admin+'/tenants/'+e, 'POST', json.dumps(payload).encode(), headers)
            if status != 201:
                raise RuntimeError('Storage tenant registration failed')
        elif status != 200:
            raise RuntimeError('Storage tenant lookup failed')
        public = self.endpoint(PREFIX+'-storage', 5000)
        self.wait(public+'/bucket', {'authorization': 'Bearer '+token(v['jwt'], 'service_role'), 'x-forwarded-host': e+'.storage.internal'})
        if self.sql("SELECT to_regclass('storage.objects') IS NOT NULL AND to_regprocedure('auth.uid()') IS NOT NULL;", e).stdout.strip() != 't':
            raise RuntimeError('Environment migrations incomplete')
        endpoints['storage'] = {'url': public, 'tenantHost': e+'.storage.internal'}
        endpoints['serviceConcurrency'] = {'rest': int(lab.rest_configuration(e, v, DB)['PGRST_DB_POOL'])}
        if creating:effect_receipt.native_stage(STATE,e,'publication')
        path = STATE/'endpoints.json'
        all_endpoints = json.loads(path.read_text()) if path.exists() else {}
        all_endpoints[e] = endpoints
        atomic(path, all_endpoints)

    def management(self):
        """Dedicated identity realm; it has no application REST or Storage route."""
        values = self.values['management']
        if self.sql("SELECT 1 FROM pg_roles WHERE rolname='management_auth';").stdout.strip() != '1':
            self.sql(f"CREATE ROLE management_auth LOGIN NOINHERIT PASSWORD '{values['auth']}';")
        if self.sql("SELECT 1 FROM pg_database WHERE datname='management';").stdout.strip() != '1':
            self.sql('CREATE DATABASE management;')
        self.sql('REVOKE ALL ON DATABASE management FROM PUBLIC; GRANT CONNECT ON DATABASE management TO management_auth;')
        self.sql('CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION management_auth;', 'management')
        self.sql(f'ALTER ROLE management_auth CONNECTION LIMIT {connection_budget.SERVICE_LIMIT}; ALTER DATABASE management CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};')
        self.sql('ALTER ROLE management_auth IN DATABASE management SET search_path TO auth;')
        self.hba()
        config = lab.auth_configuration('management', values, DB)
        config.update({'GOTRUE_DISABLE_SIGNUP': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'false',
                       'GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED': 'false'})
        name = PREFIX+'-management-auth'
        self.launch(name, 'auth', config, '256m', .25)
        endpoint = self.endpoint(name, 9999)
        self.wait(endpoint+'/health')
        atomic(STATE/'management.json', {'auth': endpoint})


def stop():
    names = lab.docker('ps', '-q', '--filter', 'label=io.sbarbase.owner='+OWNER).stdout.split()
    if names:
        lab.docker('stop', *names)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['up', 'stop', 'provision'])
    parser.add_argument('environment', nargs='?')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        if args.command=='up':
            inherited=os.environ.get('SBARBASE_WORKER_FD')
            with hba_startup.acquire(STATE,worker_fd=int(inherited) if inherited else None) as startup:
                Runtime(startup=startup).start()
        else:
            with (STATE/'operation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command=='stop':stop()
                else:
                    effect_receipt.native_stage(STATE,args.environment or '', 'preflight')
                    runtime=Runtime(operation_fd=lock.fileno(),worker_runtime=args.environment or '')
                    try:runtime.provision(args.environment or '')
                    except AdmissionLimitError:
                        effect_receipt.native_outcome(STATE,args.environment or '',75,'durable-provision-v1')
                        raise
                    effect_receipt.native_outcome(STATE,args.environment or '',0,'durable-provision-v1')
        print('Durable upstream runtime operation completed.')
    except AdmissionLimitError:
        # Stable local worker protocol. Never classify failures from raw stderr.
        print('Local runtime admission limit reached.')
        raise SystemExit(75)
    except Exception:
        # Secrets, SQL and HTTP response bodies must never reach console output.
        raise SystemExit('Durable runtime operation failed; retained state is available for reconciliation.')
