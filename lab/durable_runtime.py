"""Bounded, persistent upstream runtime. Experimental, local and unpublished."""
import effect_receipt
import hba_runtime
import hba_startup
from guarded_sql_executor import GuardedSQL
import argparse
import base64
import fcntl
import sys
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
import resource_policy
import connection_budget
import pressure_admission
import source_fence
import mail_config
import auth_settings
import mail_state

MAIL_KEY_PREFIXES = ('GOTRUE_SMTP_', 'GOTRUE_MAILER_', 'GOTRUE_RATE_LIMIT_')


def is_mail_key(key):
    """An Auth setting that belongs to the mail configuration."""
    return key.startswith(MAIL_KEY_PREFIXES)


def run_locked(main, failure):
    """Run main under the installation operation lock. Any failure, a held lock included,
    exits with only the given message, so no sensitive output reaches the terminal."""
    try:
        with (STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except Exception:
        raise SystemExit(failure) from None


def hba_content(environments):
    """The desired rule inventory for one set of environment identifiers.

    The one builder both the runtime and the generation migration use, so a
    migrated database is re-derived from the same inventory the runtime publishes.
    """
    lines = ['local all supabase_admin trust', 'host storage_metadata storage_control 0.0.0.0/0 scram-sha-256',
             'host management management_auth 0.0.0.0/0 scram-sha-256']
    for e in environments:
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid runtime inventory')
        # The studio login exists only while an operator runs Studio for this environment
        # (lab/studio.py); outside that it is NOLOGIN, so the rule admits nobody.
        # Realtime's login exists once the environment turns Realtime on (docs/engineering/REALTIME.md).
        lines += [f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256' for role in ('auth', 'rest', 'storage', 'studio', 'realtime')]
    lines += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
    return '\n'.join(lines)+'\n'


# The installation-wide environment guard. src/control/catalog.ts ENVIRONMENT_LIMIT mirrors it
# so the API refuses before queueing; tests/hierarchy.test.ts checks the two agree.
ENVIRONMENT_LIMIT = 4


def available_memory_bytes():
    return int(next(x.split()[1] for x in lab.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024


def owned_usage_bytes():
    """Memory the running owned containers use now, from the daemon."""
    names = lab.docker('ps', '--filter', 'label=io.sbarbase.owner='+OWNER, '--format', '{{.Names}}').stdout.split()
    if not names:
        return 0
    units = {'B': 1, 'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3}
    total = 0
    for line in lab.docker('stats', '--no-stream', '--format', '{{.MemUsage}}', *names).stdout.splitlines():
        match = re.match(r'([\d.]+)(B|KiB|MiB|GiB) /', line.strip())
        if not match:
            raise RuntimeError('Container memory usage unreadable')
        total += int(float(match.group(1))*units[match.group(2)])
    return total


class AdmissionLimitError(RuntimeError):
    pass


OWNER = 'durable-upstream'
PREFIX = 'sbarbase-durable'
DB = PREFIX + '-db'
NETWORK = PREFIX + '-net'
STATE = lab.STATE / 'upstream'
PRIVATE = lab.PRIVATE / 'upstream'
# Written by lab/upgrade.py for one start after it moves the checkout. It names the exact
# pinned image of each service that start may replace; nothing else ever replaces one.
UPGRADE_INTENT = STATE / 'upgrade-intent.json'
# Services that keep no state in their container: Auth and Storage keep theirs in the
# database and the objects volume. The database is never replaced here.
REPLACEABLE = ('auth', 'rest', 'storage', 'realtime')


def settings_only(component, configured, desired):
    """True when a retained Auth differs from its desired configuration only in the operator's
    sign-in settings (lab/auth_settings.py), in either direction."""
    if component != 'auth':
        return False
    changed = {key for key in set(configured) | set(desired) if configured.get(key) != desired.get(key)}
    # A key the image sets itself and the desired configuration never names is not a change.
    changed = {key for key in changed if key in desired or auth_settings.owned(key)}
    return bool(changed) and all(auth_settings.owned(key) for key in changed)


def load_settings(e):
    """The environment's sign-in settings; a file that fails validation is reported and left out."""
    try:
        return auth_settings.load(e)
    except (auth_settings.SettingsError, ValueError, OSError) as error:
        print(f'Sign-in settings for {e} are not valid ({error}); Auth starts without them.', file=sys.stderr)
        return None


REALTIME_PORT = 4000
REALTIME_BOOT_SECONDS = 180


def published_endpoints():
    path = STATE/'endpoints.json'
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def realtime_on(e):
    """Whether this environment runs its own Realtime."""
    return bool(published_endpoints().get(e, {}).get('realtime'))


def realtime_count():
    return sum(1 for entry in published_endpoints().values() if isinstance(entry, dict) and entry.get('realtime'))


def realtime_tenant(e):
    """Realtime reads its tenant from the first label of the Host header; the 24 hex digits keep it a plain name."""
    return e[2:]


def service_token(secret, claims, seconds=300):
    """An HS256 token for one of Realtime's own APIs."""
    now = int(time.time())
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    message = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({**claims, 'iat': now, 'exp': now + seconds})
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    return message + '.' + signature


def upgrade_allows(component, image):
    """True when a recorded upgrade or rollback names exactly this pinned image for this service."""
    if component not in REPLACEABLE or not UPGRADE_INTENT.exists():
        return False
    try:
        intent = json.loads(UPGRADE_INTENT.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(intent, dict) and isinstance(intent.get('pins'), dict) and intent['pins'].get(component) == image


atomic = lab.atomic


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


def wait_ready(url, headers=None, failure='Runtime readiness timed out', attempts=60):
    """Poll every half second, thirty seconds by default, until url answers 200, then raise failure."""
    for _ in range(attempts):
        try:
            if http(url, headers=headers)[0] == 200:
                return
        except OSError:
            pass
        time.sleep(.5)
    raise RuntimeError(failure)


def sql_literal(value):
    """A SQL string literal with every quote doubled."""
    return "'"+str(value).replace("'","''")+"'"


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
        for component, filename in [('db', 'distro-image.lock.json'), ('storage', 'storage-image.lock.json'),
                                    ('realtime', 'realtime-image.lock.json')]:
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

    def launch(self, name, component, env, memory, cpus, volumes=(), command=(), existing_only=False, tier=None):
        image = self.pins[component]['id']
        actual = inspect('container', name)
        if existing_only and not actual:
            raise RuntimeError('Resume cannot create a missing service container')
        if actual:
            expected = json.loads(lab.docker('image', 'inspect', image).stdout)[0]['Id']
            configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
            # The comparison runs in both directions for the mail keys. The desired
            # keys alone miss the removal direction: an operator who deletes an
            # environment's mail configuration would otherwise have the retained
            # Auth container restarted with its live SMTP credentials and rate
            # limits, and mail would keep flowing from a configuration that no
            # longer exists (reconcile_mail names the same hazard for its own path).
            stale = [key for key in configured if is_mail_key(key) and key not in env]
            if actual['Image'] != expected or any(configured.get(k) != v for k, v in env.items()) or stale:
                if not upgrade_allows(component, image) and not (actual['Image'] == expected and settings_only(component, configured, env)):
                    raise RuntimeError('Runtime drift requires explicit reconciliation')
                # An upgrade or rollback: replace the stateless container with the pinned
                # image and the configuration this version computes, keeping its volumes.
                lab.docker('rm', '-f', actual['Id'])
                actual = None
        if actual:
            mounts = {(m.get('Name'), m['Destination']) for m in actual['Mounts']}
            if any((name, destination) not in mounts for name, destination in volumes):
                raise RuntimeError('Runtime persistent volume mismatch')
            if NETWORK not in actual['NetworkSettings']['Networks']:
                raise RuntimeError('Runtime network mismatch')
            lab.docker('start', actual['Id'])
            return actual['Id'],False
        flags = resource_policy.container_flags(tier)
        if (flags['memory'] is not None and flags['memory'] != memory) or (flags['cpus'] is not None and float(flags['cpus']) != float(cpus)):
            raise RuntimeError('Tier placement disagrees with the requested limits')
        path = PRIVATE/(name+'.env')
        lab.secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
        args = ['run', '-d', '--name', name, '--label', 'io.sbarbase.owner='+OWNER, '--label', 'io.sbarbase.tier='+flags['label'], '--network', NETWORK,
                '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus), '--pids-limit', str(flags['pids']),
                '--cpu-shares', str(flags['shares']), '--blkio-weight', str(flags['weight']),
                *resource_policy.io_flags(tier),
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
        wait_ready(url, headers)

    def hba(self):
        if self.hba_writer is None:raise RuntimeError('Explicit HBA ownership required')
        self.hba_writer.publish(hba_content(self.values['environments']))

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
        placement, _ = resource_policy.start_placement(len(self.values['environments']), realtime_count())
        if available < (placement + resource_policy.START_RESERVE_MIB) * 1024:
            raise RuntimeError('Insufficient runtime memory headroom')
        if not inspect('network', NETWORK):
            lab.docker('network', 'create', '--internal', '--label', 'io.sbarbase.owner='+OWNER, NETWORK)
        launched_cid,created=self.launch(DB, 'db', {'POSTGRES_PASSWORD': self.values['admin'], 'POSTGRES_HOST': '/var/run/postgresql', 'POSTGRES_DB': 'postgres'},
                    '1024m', 1, [(PREFIX+'-pgdata', '/var/lib/postgresql/data')],
                    ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none'), tier='system.db')
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
            '512m', .5, [(PREFIX+'-objects', '/tmp/storage-data')], tier='system.storage')
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
            if len(self.values['environments']) + int(new_environment) > ENVIRONMENT_LIMIT:
                raise AdmissionLimitError('Local runtime admission limit reached')
            try:
                reason = resource_admission.refusal(resource_admission.snapshot())
                placement, cpus = resource_policy.start_placement(len(self.values['environments']) + int(new_environment), realtime_count())
                restart = resource_policy.restart_fits(placement, cpus, available_memory_bytes(), owned_usage_bytes(), os.cpu_count() or 0)
            except Exception:
                raise RuntimeError('Resource measurement unavailable') from None
            if reason:
                raise AdmissionLimitError('Resource headroom unavailable')
            # An environment that fits while the placement runs, but whose limits
            # the next start cannot admit, would leave the service unable to come
            # back after a reboot. Refuse it now instead.
            if not restart:
                raise AdmissionLimitError('Restart headroom unavailable')
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
        # This environment's own mail configuration, read once per invocation.
        # mail_config.load returns None when the environment has no mail file,
        # and the builder then returns exactly the dict it returned before.
        mail = mail_config.load(e)
        for service, builder, port, suffix in [('auth', lab.auth_configuration, 9999, '/health'), ('rest', lab.rest_configuration, 3000, '/')]:
            name = PREFIX+'-'+e+'-'+service
            # No per-environment class field exists yet, so both environment
            # services launch under the production row (docs/engineering/RESOURCE-POLICY.md 3.2).
            config = builder(e, v, DB, mail, load_settings(e)) if service == 'auth' else builder(e, v, DB)
            self.launch(name, service, config, '256m', .25, existing_only=not creating, tier='production')
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
        previous = all_endpoints.get(e, {}).get('realtime')
        if previous and not creating:
            # Realtime was turned on for this environment: bring it back, and let it run its
            # schema migrations again only when its pinned image changed.
            endpoints['realtime'] = self.realtime_start(e, migrate=previous.get('migrated') != self.pins['realtime']['id'])
        all_endpoints[e] = endpoints
        atomic(path, all_endpoints)

    def reconcile_mail(self, e, off=False):
        """Apply one environment's mail configuration, or remove it with `off`.

        Auth reads its SMTP configuration at process start, so a mail change is a
        container recreate and never a live patch. The comparison below runs in
        both directions on purpose, as `launch` does for a reused container: a
        key dropped from the desired dict would otherwise keep a stale SMTP value
        inside it, and dropping every key is exactly what `off` does.
        The environment database holds all Auth state, so the container process
        loses nothing.
        """
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        path = STATE/'endpoints.json'
        published = json.loads(path.read_text()) if path.exists() else {}
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Reconcile requires a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        name = PREFIX+'-'+e+'-auth'
        actual = inspect('container', name)
        if not actual:
            raise RuntimeError('Reconcile requires the retained Auth container')
        mail = None if off else mail_config.load(e)
        desired = lab.auth_configuration(e, self.values['environments'][e], DB, mail, load_settings(e))
        configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
        if {k: v for k, v in configured.items() if is_mail_key(k)} == {k: v for k, v in desired.items() if is_mail_key(k)}:
            # The recorded state is the four state vocabulary the configuration
            # defines, so an unchanged reconcile records that the configuration is
            # applied. That nothing had to be recreated is a run outcome, and it is
            # what the line below reports, not a fifth state.
            mail_state.record(e, 'off' if off else 'applied', mail)
            print('Environment mail configuration is unchanged.')
            return
        lab.docker('rm', '-f', name)
        self.launch(name, 'auth', desired, '256m', .25, tier='production')
        self.wait(self.endpoint(name, 9999)+'/health')
        self.publish_auth(e, name)
        mail_state.record(e, 'off' if off else 'applied', mail)
        print('Environment Auth service recreated to apply the mail configuration.')

    def reconcile_auth(self, e):
        """Apply one environment's sign-in settings: recreate its Auth with them, as mail does."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        path = STATE/'endpoints.json'
        published = json.loads(path.read_text()) if path.exists() else {}
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Sign-in settings need a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        name = PREFIX+'-'+e+'-auth'
        if not inspect('container', name):
            raise RuntimeError('Sign-in settings need the retained Auth container')
        settings = auth_settings.load(e)
        desired = lab.auth_configuration(e, self.values['environments'][e], DB, mail_config.load(e), settings)
        # The running Auth steps aside rather than going away, so a new one that does not
        # start leaves the environment exactly as it was.
        previous = name+'-previous'
        if inspect('container', previous):
            lab.docker('rm', '-f', previous)
        lab.docker('stop', name)
        lab.docker('rename', name, previous)
        try:
            self.launch(name, 'auth', desired, '256m', .25, tier='production')
            self.wait(self.endpoint(name, 9999)+'/health')
        except Exception:
            if inspect('container', name):
                lab.docker('rm', '-f', name)
            lab.docker('rename', previous, name)
            lab.docker('start', name)
            self.wait(self.endpoint(name, 9999)+'/health')
            self.publish_auth(e, name)
            raise
        lab.docker('rm', '-f', previous)
        self.publish_auth(e, name)

    def publish_auth(self, e, name):
        """A recreated Auth may hold a new address; the gateway reads it from here."""
        path = STATE/'endpoints.json'
        endpoints = json.loads(path.read_text()) if path.exists() else {}
        if e in endpoints:
            endpoints[e]['auth'] = self.endpoint(name, 9999)
            atomic(path, endpoints)

    def realtime_values(self, e):
        """Realtime's own credentials for this environment, generated once and kept with the others."""
        v = self.values['environments'][e]
        missing = {'realtime': 32, 'realtime_api': 32, 'realtime_base': 64, 'realtime_enc': 8}
        if any(key not in v for key in missing):
            for key, size in missing.items():
                v.setdefault(key, secrets.token_hex(size))
            atomic(self.path, self.values)
        return v

    def realtime_configuration(self, e, v):
        return {'PORT': str(REALTIME_PORT), 'DB_HOST': DB, 'DB_PORT': '5432', 'DB_NAME': e, 'DB_USER': f'{e}_realtime',
                'DB_PASSWORD': v['realtime'], 'DB_AFTER_CONNECT_QUERY': 'SET search_path TO _realtime', 'DB_POOL_SIZE': '2',
                'DB_ENC_KEY': v['realtime_enc'], 'DB_IP_VERSION': 'ipv4', 'API_JWT_SECRET': v['realtime_api'],
                'METRICS_JWT_SECRET': v['realtime_api'], 'SECRET_KEY_BASE': v['realtime_base'], 'APP_NAME': 'realtime',
                'ERL_AFLAGS': '-proto_dist inet_tcp', 'DNS_NODES': "''", 'RLIMIT_NOFILE': '10000', 'SEED_SELF_HOST': 'false',
                'RUN_JANITOR': 'true', 'DISABLE_HEALTHCHECK_LOGGING': 'true', 'LOG_LEVEL': 'error', 'REGION': 'local',
                'SLOT_NAME_SUFFIX': realtime_tenant(e)}

    def realtime_database(self, e, v):
        """The environment's Realtime login and schemas. The login reaches only this database (HBA)."""
        role = f'{e}_realtime'
        self.sql(f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_realtime_admin') THEN
    CREATE ROLE supabase_realtime_admin NOINHERIT NOLOGIN NOREPLICATION;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} LOGIN INHERIT REPLICATION;
  END IF;
END $$;
ALTER ROLE {role} LOGIN INHERIT REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
  CONNECTION LIMIT {connection_budget.REALTIME_CONNECTIONS} PASSWORD '{v['realtime']}';
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE {e} TO {role};
GRANT anon, authenticated, service_role TO {role} WITH INHERIT FALSE, SET TRUE;
GRANT supabase_realtime_admin TO {role};""")
        self.sql(f"""CREATE SCHEMA IF NOT EXISTS _realtime AUTHORIZATION {role};
CREATE SCHEMA IF NOT EXISTS realtime AUTHORIZATION {role};
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime FOR TABLES IN SCHEMA public;
  END IF;
END $$;""", e)
        studio_running = e in (json.loads((STATE/'studio.json').read_text()).get('sessions', {}) if (STATE/'studio.json').exists() else {})
        self.sql(f'ALTER DATABASE {e} CONNECTION LIMIT {connection_budget.database_limit(studio=studio_running, realtime=True)};')

    def realtime_start(self, e, migrate):
        """Run the environment's Realtime. With `migrate`, its login is a superuser only while Realtime
        creates its own schema and tenant, the way upstream expects, and loses it before this returns."""
        v = self.realtime_values(e)
        name = PREFIX+'-'+e+'-realtime'
        if migrate:
            self.realtime_database(e, v)
            if inspect('container', name):
                lab.docker('rm', '-f', name)
            self.sql(f'ALTER ROLE {e}_realtime SUPERUSER;')
        try:
            self.launch(name, 'realtime', self.realtime_configuration(e, v), '320m', .25, tier='production.realtime')
            base = self.endpoint(name, REALTIME_PORT)
            wait_ready(base+'/healthcheck', failure='Realtime did not start', attempts=REALTIME_BOOT_SECONDS*2)
            if migrate:
                self.realtime_register(e, v, base)
                # What the migrations created or found belongs to the roles upstream picks; the
                # login keeps working on Realtime's own schema once it is no longer a superuser.
                self.realtime_grants(e)
        finally:
            if migrate:
                self.sql(f"ALTER ROLE {e}_realtime NOSUPERUSER; "
                         f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '{e}_realtime';")
        return {'url': base, 'tenantHost': realtime_tenant(e)+'.realtime', 'migrated': self.pins['realtime']['id']}

    def realtime_grants(self, e):
        role = f'{e}_realtime'
        self.sql(f"""GRANT USAGE, CREATE ON SCHEMA realtime TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA realtime TO {role};
GRANT ALL ON ALL SEQUENCES IN SCHEMA realtime TO {role};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA realtime TO {role};
GRANT USAGE, CREATE ON SCHEMA _realtime TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA _realtime TO {role};
GRANT ALL ON ALL SEQUENCES IN SCHEMA _realtime TO {role};""", e)

    def realtime_register(self, e, v, base):
        """Create the environment's tenant; Realtime runs its tenant migrations as it does so."""
        tenant = realtime_tenant(e)
        headers = {'authorization': 'Bearer '+service_token(v['realtime_api'], {'role': 'service_role'}), 'content-type': 'application/json'}
        status, _ = http(f'{base}/api/tenants/{tenant}', 'DELETE', headers=headers)
        if status not in (200, 204, 404):
            raise RuntimeError('Realtime tenant reset failed')
        body = {'tenant': {'name': tenant, 'external_id': tenant, 'jwt_secret': v['jwt'], 'extensions': [{'type': 'postgres_cdc_rls', 'settings': {
            'db_host': DB, 'db_name': e, 'db_user': f'{e}_realtime', 'db_password': v['realtime'], 'db_port': '5432',
            'region': 'local', 'poll_interval_ms': 100, 'poll_max_record_bytes': 1048576, 'publication': 'supabase_realtime',
            'slot_name': 'supabase_realtime_replication_slot', 'ssl_enforced': False}}]}}
        request = urllib.request.Request(f'{base}/api/tenants', data=json.dumps(body).encode(), method='POST', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=REALTIME_BOOT_SECONDS) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        if status not in (200, 201):
            raise RuntimeError('Realtime tenant registration failed')

    def realtime_turn(self, e, on):
        """Turn one environment's Realtime on or off: an operator action from the console."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        endpoints = published_endpoints()
        if e not in endpoints or e not in self.values['environments']:
            raise RuntimeError('Realtime needs a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        name = PREFIX+'-'+e+'-realtime'
        if on:
            if self.sql(f"SELECT count(*) FROM pg_hba_file_rules WHERE '{e}_realtime' = ANY(user_name) AND error IS NULL;").stdout.strip() != '1':
                raise RuntimeError('Realtime access rule not published; restart Sbarbase once')
            if not realtime_on(e):
                placement, cpus = resource_policy.start_placement(len(self.values['environments']), realtime_count() + 1)
                if not resource_policy.restart_fits(placement, cpus, available_memory_bytes(), owned_usage_bytes(), os.cpu_count() or 0):
                    raise AdmissionLimitError('Restart headroom unavailable')
                # The connections Realtime adds must fit what the cluster can serve, as Studio's do.
                available, promised = (int(value) for value in self.sql(
                    "SELECT current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int "
                    "- current_setting('reserved_connections')::int, coalesce(sum(greatest(datconnlimit, 0)), 0) "
                    "FROM pg_database WHERE datallowconn AND datname <> 'template1';").stdout.strip().split('|'))
                if promised + connection_budget.REALTIME_CONNECTIONS > available:
                    raise AdmissionLimitError('Connection headroom unavailable')
            entry = self.realtime_start(e, migrate=True)
        else:
            entry = None
            if inspect('container', name):
                lab.docker('rm', '-f', name)
            self.sql(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{e}_realtime') THEN "
                     f"ALTER ROLE {e}_realtime NOLOGIN; END IF; END $$; "
                     f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '{e}_realtime';")
            self.sql(f"SELECT count(pg_drop_replication_slot(slot_name)) FROM pg_replication_slots WHERE database = '{e}' AND NOT active;")
            studio_running = e in (json.loads((STATE/'studio.json').read_text()).get('sessions', {}) if (STATE/'studio.json').exists() else {})
            self.sql(f'ALTER DATABASE {e} CONNECTION LIMIT {connection_budget.database_limit(studio=studio_running)};')
        endpoints = published_endpoints()
        if entry:
            endpoints[e]['realtime'] = entry
        else:
            endpoints[e].pop('realtime', None)
        atomic(STATE/'endpoints.json', endpoints)

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
        self.launch(name, 'auth', config, '256m', .25, tier='system.management-auth')
        endpoint = self.endpoint(name, 9999)
        self.wait(endpoint+'/health')
        atomic(STATE/'management.json', {'auth': endpoint})


def stop():
    names = lab.docker('ps', '-q', '--filter', 'label=io.sbarbase.owner='+OWNER).stdout.split()
    if names:
        lab.docker('stop', *names)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['up', 'stop', 'provision', 'mail', 'auth', 'realtime'])
    parser.add_argument('environment', nargs='?')
    parser.add_argument('--off', action='store_true')
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
                elif args.command=='realtime':
                    if not args.environment:raise SystemExit('The realtime command needs an environment')
                    Runtime().realtime_turn(args.environment, on=not args.off)
                elif args.command=='auth':
                    if not args.environment:raise SystemExit('The auth command needs an environment')
                    Runtime().reconcile_auth(args.environment)
                elif args.command=='mail':
                    # An operator action, not a worker job: there is no receipt to
                    # inherit, so no HBA preflight runs and no worker runtime is
                    # named. Mutual exclusion is the operation lock held above.
                    if not args.environment:raise SystemExit('The mail command needs an environment')
                    Runtime().reconcile_mail(args.environment, off=args.off)
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
