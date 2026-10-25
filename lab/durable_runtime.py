"""Bounded, persistent upstream runtime. Experimental, local and unpublished."""
import effect_receipt
import database_volume
import hba_runtime
import hba_startup
import image_identity
from guarded_sql_executor import GuardedSQL
import argparse
import base64
import contextlib
import fcntl
import sys
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import run as lab
import resource_admission
import docker_profile
import resource_policy
import connection_budget
import pressure_admission
import source_fence
import mail_config
import auth_settings
import scheduled_jobs
import data_api
import mail_state
import private_directories
import kept_runtime
import runtime_files
import rolling

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
        # The developer login exists once an owner turns direct database access on, and is NOLOGIN while it is off.
        # The scheduler's login exists once Scheduled jobs were turned on, NOLOGIN while they are off
        # (docs/engineering/SCHEDULED-JOBS.md).
        lines += [f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256' for role in ('auth', 'rest', 'storage', 'studio', 'realtime', 'developer', 'cron')]
    lines += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
    return '\n'.join(lines)+'\n'


# The installation-wide environment guard. src/control/catalog.ts ENVIRONMENT_LIMIT mirrors it
# so the API refuses before queueing; tests/hierarchy.test.ts checks the two agree. Eight since
# 2026-10-16: the connection budget at MAX_CONNECTIONS holds eight, and a drill of eight
# environment databases under full load on the 1 GiB, one CPU database tier peaked at 592 MiB
# (docs/evidence/capacity-drill-2026-10-16.json). Memory, CPU and pressure admission still decide
# whether a given server takes the next one.
ENVIRONMENT_LIMIT = 8


def available_memory_bytes():
    return int(next(x.split()[1] for x in lab.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024


def recovery_target_items():
    """The current recovery target's containers, the same listing the preflight counts."""
    return resource_policy.recovery_target_items(STATE, lab.docker)


def restart_placement(environments, realtime=0, functions=0):
    """(MiB, CPUs) the next start runs: the source rows plus the current recovery target."""
    return resource_policy.restart_placement(resource_policy.start_placement(environments, realtime, functions), recovery_target_items())


def owned_usage_bytes():
    """Memory the running owned containers use now, from the daemon.

    On a moved installation the current recovery target's running containers are
    included: the restart check counts them in the placement, and while they run their use is already
    missing from MemAvailable, so leaving them out would count them twice.
    """
    names = lab.docker('ps', '--filter', 'label=io.sbarbase.owner='+OWNER, '--format', '{{.Names}}').stdout.split()
    prefix = resource_policy.started_recovery_target_prefix(STATE)
    if prefix:
        names += [name for name in lab.docker('ps', '--filter', 'label=io.sbarbase.owner='+resource_policy.RECOVERY_TARGET_OWNER,
                                               '--format', '{{.Names}}').stdout.split() if name.startswith(prefix+'-')]
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


class DataApiRefusal(RuntimeError):
    """Data API settings the database cannot serve; the message names only schema names."""


class DataApiBusy(RuntimeError):
    """Another runtime operation changed the environment's settings while a console change wrote them; ask again."""


class DataApiUnrecorded(RuntimeError):
    """A console change wrote the settings and told PostgREST, then could not record the outcome."""


class AdmissionLimitError(RuntimeError):
    pass


class OperationBusy(RuntimeError):
    """A lock this change needs is held, or a write pause holds the environment: nothing was opened; ask again."""


# The exit status of a console change (realtime, functions, database, signing, auth) that found a lock
# held or the environment paused and changed nothing the next try would not redo: lab/realtime.py and
# lab/auth_settings.py leave its row pending and the supervisor asks again. 75 stays the admission
# limit, which those commands report as a refusal.
BUSY = 76
CONSOLE_CHANGES = ('realtime', 'functions', 'database', 'signing', 'auth', 'cron')


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
REPLACEABLE = ('auth', 'rest', 'storage', 'realtime', 'functions')


# Operator settings of a stateless service that a restart may apply by recreating it.
# The role settings the platform leaves on each environment's REST login, in its own database.
# Its two deadlines: a request REST serves ends within the longer one (lab/rolling.py drains for it).
REST_STATEMENT_SECONDS = 8
REST_TRANSACTION_SECONDS = 12
REST_PINS = ("ALTER ROLE {e}_rest IN DATABASE {e} SET search_path = pg_catalog; "
             f"ALTER ROLE {{e}}_rest IN DATABASE {{e}} SET statement_timeout = '{REST_STATEMENT_SECONDS}s'; "
             f"ALTER ROLE {{e}}_rest IN DATABASE {{e}} SET transaction_timeout = '{REST_TRANSACTION_SECONDS}s';")
# The REST login's role settings are rows tenant code can lock (it can run `ALTER ROLE` on itself in
# a transaction it leaves open), so every platform statement that writes them is bounded: it
# gives up with an error instead of waiting, and the start stops or the change is refused.
REST_PIN_BOUNDS = "SET lock_timeout = '5s'; SET statement_timeout = '30s'; "
# The developer login's setup and grants wait for transactions the environment's logins leave open
# (a role setting the login writes for itself, a no-op GRANT on a table in `public`), so both are
# bounded and run under the environment's database access lock alone (database_command).
DEVELOPER_BOUNDS = "SET lock_timeout = '10s'; SET statement_timeout = '60s'; "
# For a script under the operation lock that waits for rows a session of the environment can hold, inside its own
# BEGIN ... COMMIT (realtime_database; scheduled_jobs.limit_sql is the same bound for the connection limit).
ROW_BOUNDS = "SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s';"


def rest_pin_sql(e, end_sessions=False, password=None):
    """Leave the REST login exactly REST_PINS in its own database and no other role setting, in any
    database or globally (tenant code acting as that login can set its own, and a global or foreign
    one makes a restore refuse). With `end_sessions`, for a path that starts REST, the login's
    backends are ended first so that none holds the rows open; the REST container is not serving
    then. Run by Runtime.rest_deadlines, backup.start_services and the independent restore target.

    One transaction, with the generated `password` too when given: ended as separate statements, a
    session PostgREST opens again in between (a signing rotation's old REST still serves) could hold the
    rows the next statement writes (found in review)."""
    if not re.fullmatch(r'e_[a-f0-9]{24}', e):
        raise RuntimeError('Invalid environment runtime identifier')
    if password is not None and not re.fullmatch(r'[a-f0-9]{64}', password):
        raise RuntimeError('Invalid REST login password')
    ended = (f"SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(a.pid)) FROM pg_catalog.pg_stat_activity a "
             f"WHERE a.usename = '{e}_rest' AND a.pid <> pg_catalog.pg_backend_pid(); ") if end_sessions else ''
    # The password right after the sessions are ended, the row a reconnected session could take first.
    word = f"ALTER ROLE {e}_rest PASSWORD '{password}'; " if password is not None else ''
    return (data_api.SAFE + REST_PIN_BOUNDS + 'BEGIN; ' + ended + word + f"""DO $$ DECLARE item record; BEGIN
  FOR item IN SELECT d.datname FROM pg_catalog.pg_db_role_setting s LEFT JOIN pg_catalog.pg_database d ON d.oid = s.setdatabase
              WHERE s.setrole = '{e}_rest'::pg_catalog.regrole LOOP
    IF item.datname IS NULL THEN
      ALTER ROLE {e}_rest RESET ALL;
    ELSE
      EXECUTE pg_catalog.format('ALTER ROLE {e}_rest IN DATABASE %I RESET ALL', item.datname);
    END IF;
  END LOOP;
END $$;
{REST_PINS.format(e=e)} COMMIT;""")


def rest_settings_sql(e):
    """Every role setting of the REST login, in any database or globally, as one sorted line."""
    if not re.fullmatch(r'e_[a-f0-9]{24}', e):
        raise RuntimeError('Invalid environment runtime identifier')
    return (data_api.SAFE + f"SELECT coalesce(pg_catalog.string_agg(coalesce(d.datname, '*') || ':' || setting, ',' "
            f"ORDER BY coalesce(d.datname, '*'), setting), '') FROM pg_catalog.pg_db_role_setting s LEFT JOIN pg_catalog.pg_database d "
            f"ON d.oid = s.setdatabase, pg_catalog.unnest(s.setconfig) setting WHERE s.setrole = '{e}_rest'::pg_catalog.regrole;")


def rest_exact(e):
    """What rest_settings_sql reads when the login has exactly REST_PINS."""
    return (f'{e}:search_path=pg_catalog,{e}:statement_timeout={REST_STATEMENT_SECONDS}s,'
            f'{e}:transaction_timeout={REST_TRANSACTION_SECONDS}s')


def rest_password_sql(e, password, end_sessions=False):
    """Give the REST login back the password the platform generated for it (kept in the runtime file).
    Tenant code acting as that login can change its own password, and PostgREST then cannot connect:
    its readiness wait fails and, since a resume does not isolate environments, so does the whole start.
    The value is a generated hex token and is checked before it is put into the statement.

    A request whose function changes that password and then runs until its deadline holds the login's
    row, and the write would wait for it (found in review: it stopped a signing rotation, whose old REST
    still serves). With `end_sessions` the login's backends are ended first, in the same transaction."""
    if not re.fullmatch(r'e_[a-f0-9]{24}', e) or not re.fullmatch(r'[a-f0-9]{64}', password or ''):
        raise RuntimeError('Invalid REST login password')
    ended = (f"SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(a.pid)) FROM pg_catalog.pg_stat_activity a "
             f"WHERE a.usename = '{e}_rest' AND a.pid <> pg_catalog.pg_backend_pid(); ") if end_sessions else ''
    return (data_api.SAFE + REST_PIN_BOUNDS + 'BEGIN; ' + ended +
            f"ALTER ROLE {e}_rest PASSWORD '{password}'; COMMIT;")


# A signing rotation ends every Auth session of the environment: a refresh token would otherwise give a
# leaked session a new token signed with the new key. Tenant code can add triggers and rules to these
# tables (the developer login is a member of the Auth login), and they run as whoever runs the statement:
# as the superuser, a trigger on auth.sessions made the developer login a superuser (lab/signing-revoke-check.py).
# So the statement runs as the Auth login itself, the role tenant code already has. Tenant code can also hold
# these rows, so it is bounded as REST_PIN_BOUNDS is, under the operation lock.
SESSIONS_REVOKE = (data_api.SAFE + REST_PIN_BOUNDS +
                   'BEGIN; UPDATE auth.refresh_tokens SET revoked = true WHERE NOT revoked; DELETE FROM auth.sessions; COMMIT;')
# A trigger can lift the bounds above for the statements after its own (SET statement_timeout = 0), so the
# client gives up too, and then ends the Auth login's sessions as the superuser, which runs no tenant code.
SESSIONS_REVOKE_SECONDS = 45


def revoke_auth_sessions(e, password, container=None):
    """End every Auth session of one environment, connected as its Auth login with the generated password
    (read from stdin, never in an argument). True when the revocation committed; False when it failed or
    ran out of time, and the rotation is then unfinished."""
    if not re.fullmatch(r'e_[a-f0-9]{24}', e) or not re.fullmatch(r'[a-f0-9]{64}', password or ''):
        raise RuntimeError('Invalid Auth login password')
    container = container or DB
    command = docker_profile.docker_command(
        'exec', '-i', container, 'sh', '-c',
        'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$1" -d "$2" -qAt',
        'sbarbase-revoke', f'{e}_auth', e)
    try:
        return subprocess.run(command, input=password + '\n' + SESSIONS_REVOKE, text=True, capture_output=True,
                              timeout=SESSIONS_REVOKE_SECONDS).returncode == 0
    except subprocess.TimeoutExpired:
        # A killed client does not end its server session; the superuser does, and runs no tenant code doing it.
        lab.docker('exec', '-i', container, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', 'postgres', '-qAt',
                   data=f"SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(pid)) FROM pg_catalog.pg_stat_activity "
                        f"WHERE usename = '{e}_auth';", check=False)
        return False


# A retained REST container without the Data API's db-pre-config function (lab/data_api.py) is
# recreated with it once: it is stateless, and the function exists before it starts.
TUNABLE = {'storage': frozenset({'FILE_SIZE_LIMIT'}), 'rest': frozenset(data_api.CONTAINER_ENV)}


def upload_limit():
    """The largest upload in bytes, from SBARBASE_UPLOAD_LIMIT_MB (1 to 5120, 50 by default).
    The gateway reads the same variable (src/gateway/handler.ts `uploadLimit`)."""
    value = os.environ.get('SBARBASE_UPLOAD_LIMIT_MB', '').strip() or '50'
    if not value.isdigit() or not 1 <= int(value) <= 5120:
        raise RuntimeError('SBARBASE_UPLOAD_LIMIT_MB must be a whole number from 1 to 5120')
    return int(value) * 1024 * 1024


def settings_only(component, configured, desired):
    """True when a retained Auth differs from its desired configuration only in the operator's
    sign-in settings (lab/auth_settings.py), or a retained Storage only in its upload limit,
    in either direction."""
    if component == 'auth':
        owned = auth_settings.owned
    elif component in TUNABLE:
        owned = TUNABLE[component].__contains__
    else:
        return False
    changed = {key for key in set(configured) | set(desired) if configured.get(key) != desired.get(key)}
    # A key the image sets itself and the desired configuration never names is not a change.
    changed = {key for key in changed if key in desired or owned(key)}
    return bool(changed) and all(owned(key) for key in changed)


# The one key each service reads the environment's JWT signing secret from.
SIGNING_KEYS = {'auth': 'GOTRUE_JWT_SECRET', 'rest': 'PGRST_JWT_SECRET'}


def signing_only(component, configured, desired):
    """True when a retained Auth or REST differs from its desired configuration in its signing
    secret (a rotation, `Runtime.rotate_signing`), and otherwise not at all or only as
    `settings_only` allows."""
    key = SIGNING_KEYS.get(component)
    if key is None or configured.get(key) == desired.get(key):
        return False
    patched = {**configured, key: desired.get(key)}
    return all(patched.get(k) == v for k, v in desired.items()) or settings_only(component, patched, desired)


def load_settings(e):
    """The environment's sign-in settings; a file that fails validation is reported and left out."""
    try:
        return auth_settings.load(e)
    except (auth_settings.SettingsError, ValueError, OSError) as error:
        print(f'Sign-in settings for {e} are not valid ({error}); Auth starts without them.', file=sys.stderr)
        return None


REALTIME_PORT = 4000
REALTIME_BOOT_SECONDS = 180
FUNCTIONS_PORT = 9000
FUNCTIONS_BOOT_SECONDS = 60
# Edge Functions reach the internet (npm: and jsr: imports, other APIs) through one outbound network
# whose containers cannot reach each other; every other service stays on internal networks only
# (docs/engineering/FUNCTIONS-NETWORK-ISOLATION.md).
EGRESS = PREFIX + '-outbound'
# The outbound network before it turned traffic between its containers off; removed once empty.
LEGACY_EGRESS = PREFIX + '-egress'
NO_ICC = 'com.docker.network.bridge.enable_icc'
FUNCTIONS_NETWORK = re.compile(re.escape(PREFIX) + r'-(e_[a-f0-9]{24})-fn')
# The loopback API listens on each environment network's gateway address at this port and
# forwards to the shared Storage with that environment's tenant set (src/control/functions-storage.ts).
FUNCTIONS_STORAGE_PORT = int(os.environ.get('SBARBASE_FUNCTIONS_STORAGE_PORT', '25433'))


def functions_network(e):
    """The environment's own internal network: its Functions, its Auth and REST. Functions join no
    other internal network, so no other environment's service has an address they can reach."""
    return f'{PREFIX}-{e}-fn'


def functions_address(network):
    """The environment network's IPv4 gateway, the host's own address on it, and its subnet."""
    item = inspect('network', network) or {}
    for entry in (item.get('IPAM') or {}).get('Config') or []:
        subnet, gateway = entry.get('Subnet') or '', entry.get('Gateway') or ''
        if subnet and ':' not in subnet and gateway:
            return gateway, subnet
    raise RuntimeError('Environment network has no IPv4 gateway')


def published_endpoints():
    return runtime_files.read_endpoints(STATE)


def realtime_on(e):
    """Whether this environment runs its own Realtime."""
    return bool(published_endpoints().get(e, {}).get('realtime'))


def realtime_count():
    return sum(1 for entry in published_endpoints().values() if isinstance(entry, dict) and entry.get('realtime'))


def direct_on(e):
    """Whether this environment's developer login may connect (direct database access)."""
    return bool(published_endpoints().get(e, {}).get('database'))


def cron_on(e):
    """Whether this environment's Scheduled jobs are on (lab/scheduler.ts runs them)."""
    return bool(published_endpoints().get(e, {}).get('cron'))


def studio_running(e):
    return e in runtime_files.studio_sessions(STATE)


def current_database_limit(e, *, studio=None, realtime=None, direct=None, cron=None):
    """The environment database's connection limit for the logins it runs now; a caller that is
    changing one of them names its new value and the others are read from the published state."""
    return connection_budget.database_limit(studio=studio_running(e) if studio is None else studio,
                                            realtime=realtime_on(e) if realtime is None else realtime,
                                            direct=direct_on(e) if direct is None else direct,
                                            cron=cron_on(e) if cron is None else cron)


def functions_on(e):
    """Whether this environment runs its own Edge Functions."""
    return bool(published_endpoints().get(e, {}).get('functions'))


def functions_count():
    return sum(1 for entry in published_endpoints().values() if isinstance(entry, dict) and entry.get('functions'))


# How functions_start runs an environment's Edge Functions; functions_kept compares a running one with it.
FUNCTIONS_COMMAND = ('start', '--main-service', '/home/deno/main', '-p', str(FUNCTIONS_PORT))


def launch_spec(component, reference, memory, cpus, tier, network, volumes=(), binds=(), command=()):
    """A digest of everything launch gives a new container besides its settings: the image, the tier's
    limits, the network, the mounts, the command and the log options. Recorded as a label, so a container
    kept across an upgrade restart can be compared with what this version would create."""
    flags = resource_policy.container_flags(tier)
    described = [component, reference, memory, str(cpus), sorted(flags.items()), list(resource_policy.io_flags(tier)), network,
                 [list(item) for item in volumes], [[str(source), target] for source, target in binds], list(command),
                 STOP_DATABASE_SECONDS if component == 'db' else None, 'max-size=5m', 'max-file=2']
    return hashlib.sha256(json.dumps(described, sort_keys=True, default=str).encode()).hexdigest()[:32]


# The keys functions get. Each carries the time it was made, so a running container's are compared
# by what they grant, not byte for byte (valid_service_token).
FUNCTIONS_KEYS = {'SUPABASE_ANON_KEY': 'anon', 'SUPABASE_SERVICE_ROLE_KEY': 'service_role'}


def functions_binds(e):
    return [(lab.ROOT/'lab'/'functions'/'main', '/home/deno/main'), (functions_code(e), '/home/deno/functions'), (functions_private(e), '/run/sbarbase')]


def valid_service_token(token, secret, role, margin=30 * 24 * 3600):
    """True when `token` is an HS256 token service_token made with `secret` for `role`, issued by
    sbarbase, and valid for at least `margin` seconds more."""
    try:
        head, body, signature = (token or '').split('.')
        expected = base64.urlsafe_b64encode(hmac.new(secret.encode(), (head + '.' + body).encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
        if not hmac.compare_digest(expected, signature):
            return False
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        header = json.loads(base64.urlsafe_b64decode(head + '=' * (-len(head) % 4)))
    except (ValueError, AttributeError):
        return False
    return (header == {'alg': 'HS256', 'typ': 'JWT'} and set(claims) == {'role', 'iss', 'iat', 'exp'}
            and claims.get('role') == role and claims.get('iss') == 'sbarbase'
            and isinstance(claims.get('exp'), int) and claims['exp'] > time.time() + margin)


def functions_code(e):
    """Deployed function code, written by the console (src/control/functions.ts)."""
    return STATE/'functions'/e


def functions_private(e):
    """The environment's function secrets, written by the console; never in the code folder."""
    return PRIVATE/'functions'/e


def realtime_tenant(e):
    """Realtime reads its tenant from the first label of the Host header; the 24 hex digits keep it a plain name."""
    return e[2:]


def realtime_slot_suffix(e):
    """Unique per environment, and short enough that Realtime's longest slot name,
    `supabase_realtime_messages_replication_slot_<suffix>`, stays within PostgreSQL's
    63 characters: a truncated name makes the pinned Realtime's replication crash."""
    return e[2:14]


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


def database_mounts():
    """The database container's one persistent mount: the volume lab/database_volume.py names."""
    return [(database_volume.current(STATE), '/var/lib/postgresql/data')]


def ensure_egress():
    """The outbound network, created with traffic between its containers off. One that allows it
    is refused, and the earlier shared network is removed once nothing uses it."""
    item = inspect('network', EGRESS)
    if not item:
        lab.docker('network', 'create', '--opt', NO_ICC+'=false', '--label', 'io.sbarbase.owner='+OWNER, EGRESS)
    elif (item.get('Options') or {}).get(NO_ICC) != 'false' or item.get('Internal'):
        raise RuntimeError('Outbound network lets its containers reach each other; explicit reconciliation required')
    # A stopped container still names its networks, and `network inspect` lists only running ones.
    if inspect('network', LEGACY_EGRESS) and not lab.docker('ps', '-aq', '--filter', 'network='+LEGACY_EGRESS).stdout.strip():
        lab.docker('network', 'rm', LEGACY_EGRESS, check=False)


def functions_links(name, component):
    """The environment networks a service joins besides the runtime network: an environment's Auth
    and REST join their own environment's when it exists. The shared Storage joins none; Functions
    reach it through the host listener, which sets the tenant itself."""
    match = re.fullmatch(re.escape(PREFIX) + r'-(e_[a-f0-9]{24})-(auth|rest)', name)
    if match and component == match.group(2) and inspect('network', functions_network(match.group(1))):
        return [functions_network(match.group(1))]
    return []


def join(container, networks):
    """Connect a container to each of these networks it is not on yet."""
    if not networks:
        return
    item = inspect('container', container)
    attached = item['NetworkSettings']['Networks'] if item else {}
    for network in networks:
        if network not in attached:
            lab.docker('network', 'connect', network, container)


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


# Schemas the platform creates in an environment's database, now or when a feature is turned on.
# The developer login may create other schemas, never these, nor any starting with sbarbase or supabase.
RESERVED_SCHEMAS = ('auth', 'storage', 'extensions', 'realtime', '_realtime', 'vault', 'pgsodium',
                    'pgsodium_masks', 'cron', 'net', 'graphql', 'graphql_public', 'pgbouncer',
                    '_analytics', '_supavisor', 'information_schema', 'public')
# Extensions an environment's own login may not create on a shared server: they reach the server's
# network, see every environment's activity or data, load code, write files on the server (pgroonga
# creates its files at any path it is given), or hold the server's root key. The
# image's supautils would otherwise create them as the superuser for any login.
RESERVED_EXTENSIONS = ('dblink', 'postgres_fdw', 'http', 'pg_net', 'wrappers', 'pg_cron', 'pg_tle',
                       'pgsodium', 'pg_stat_statements', 'pg_stat_monitor', 'pg_walinspect',
                       'pg_buffercache', 'pg_prewarm', 'pg_repack', 'pgaudit', 'supautils', 'pljava',
                       'plcoffee', 'plls', 'orioledb', 'file_fdw', 'pgroonga', 'pgroonga_database')


def sql_literal(value):
    """A SQL string literal with every quote doubled."""
    return "'"+str(value).replace("'","''")+"'"


def token(secret, role):
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    # Stable internal tenant keys survive retries; never exposed to applications.
    message = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({'role': role, 'iss': 'sbarbase-internal'})
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    return message + '.' + signature


def studio_identity_sql(e):
    """Studio works as the environment's developer login, as Supabase Studio works as a project's
    `postgres` user: the Studio login may SET ROLE to it and takes it on connecting, so Studio can
    change the tables, policies and schemas that migrations made, and what Studio makes belongs to
    the same owner. The session user stays `<e>_studio`, so its connection limit and access rule
    still apply. The older reverse membership is removed first, since PostgreSQL refuses a cycle,
    and what Studio made under it goes to the developer. Without a developer login nothing changes.
    Run by the superuser; ownership moves only when run in the environment's own database, where the
    developer login may create functions in `public`: the search path is pinned to pg_catalog and
    the calls name it, so none of them can run as the superuser."""
    studio, developer = f'{e}_studio', f'{e}_developer'
    return f"""SET search_path TO pg_catalog, pg_temp;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{studio}')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{developer}') THEN
    IF pg_catalog.pg_has_role('{developer}'::pg_catalog.name, '{studio}'::pg_catalog.name, 'MEMBER'::pg_catalog.text) THEN
      EXECUTE 'REVOKE {studio} FROM {developer}';
    END IF;
    IF pg_catalog.current_database() = '{e}'::pg_catalog.name THEN
      EXECUTE 'REASSIGN OWNED BY {studio} TO {developer}';
    END IF;
    EXECUTE 'GRANT {developer} TO {studio}';
    EXECUTE 'ALTER ROLE {studio} IN DATABASE {e} SET role = {developer}';
  END IF;
END $$;"""


class Runtime:
    def __init__(self,*,startup=None,operation_fd=None,worker_runtime=None):
        docker_profile.require_supported()
        lab.private_dir(STATE)
        private_directories.prepare(lab.ROOT)
        # Verify ignore rules before persisting generated credentials.
        import subprocess
        if subprocess.run(['git', 'check-ignore', '-q', str(runtime_files.runtime_secret_path_for_tool(PRIVATE))], cwd=lab.ROOT).returncode:
            raise RuntimeError('Runtime secrets must be ignored')
        self.pins = json.loads((lab.ROOT/'lab/images.lock.json').read_text())
        for component, filename in [('db', 'distro-image.lock.json'), ('storage', 'storage-image.lock.json'),
                                    ('realtime', 'realtime-image.lock.json'), ('functions', 'functions-image.lock.json')]:
            self.pins[component] = json.loads((lab.ROOT/'lab'/filename).read_text())
        database_image = self.image('db')[1] if startup is not None or operation_fd is not None else None
        self.hba_writer = (hba_runtime.SourceHBA(lab.docker,STATE,DB,OWNER,database_image,startup=startup,operation_fd=operation_fd)
                           if startup is not None or operation_fd is not None else None)
        if startup is not None:
            self.hba_writer.before_start(inspect('container',DB),inspect('volume',database_volume.current(STATE)) is not None)
        elif operation_fd is not None:
            self.hba_writer.worker_preflight(worker_runtime)
        self.kept = None
        self.touched = set()
        self.path = runtime_files.runtime_secret_path_for_tool(PRIVATE)
        if not self.path.exists():
            runtime_files.write_runtime_secret({**{key: secrets.token_hex(32) for key in ('admin', 'storage_control', 'storage_admin', 'encryption')}, 'environments': {}}, self.path.parent)
        self.values = runtime_files.read_runtime_secret(self.path.parent)
        if 'management' not in self.values:
            self.values['management'] = {k: secrets.token_hex(32) for k in ('auth', 'jwt')}
            runtime_files.write_runtime_secret(self.values, self.path.parent)

    def sql(self, query, database='postgres', check=True):
        return lab.docker('exec', '-i', DB, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database, '-qAt', data=query, check=check)

    def image(self, component):
        """Prove the exact immutable repository reference on this daemon."""
        reference = image_identity.reference(self.pins[component])
        result = lab.docker('image', 'inspect', reference, check=False)
        if result.returncode:
            raise image_identity.inspection_failure(reference, result.stdout, result.stderr)
        return reference, image_identity.resolved_id(reference, image_identity.record(result.stdout))

    def launch(self, name, component, env, memory, cpus, volumes=(), command=(), existing_only=False, tier=None, binds=(), rotating=False, network=NETWORK):
        reference, expected = self.image(component)
        image = self.pins[component]['id']
        actual = inspect('container', name)
        if existing_only and not actual:
            raise RuntimeError('Resume cannot create a missing service container')
        if actual:
            configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
            # The comparison runs in both directions for the mail keys. The desired
            # keys alone miss the removal direction: an operator who deletes an
            # environment's mail configuration would otherwise have the retained
            # Auth container restarted with its live SMTP credentials and rate
            # limits, and mail would keep flowing from a configuration that no
            # longer exists (reconcile_mail names the same hazard for its own path).
            stale = [key for key in configured if is_mail_key(key) and key not in env]
            if actual['Image'] != expected or any(configured.get(k) != v for k, v in env.items()) or stale:
                # An Auth or REST whose only change is its pin, when SBARBASE_ROLLING_SERVICES names it, keeps
                # its previous image: the supervisor replaces it side by side once the upgrade is confirmed
                # (lab/rolling.py). Its settings must match this version's exactly.
                matches = not stale and all(configured.get(k) == v for k, v in env.items())
                if not (actual['Image'] != expected and matches and not volumes and not binds and not command
                        and rolling.defer(self, name, component, actual, expected, image)):
                    # A new signing secret is accepted only while its environment has a recorded rotation.
                    # The plan's own replacement goes ahead in place too, when the switch no longer defers it.
                    if not upgrade_allows(component, image) and not rolling.planned(name, component, actual, expected) and not (actual['Image'] == expected and (
                            settings_only(component, configured, env) or (rotating and signing_only(component, configured, env)))):
                        raise RuntimeError('Runtime drift requires explicit reconciliation')
                    # An upgrade or rollback: replace the stateless container with the pinned
                    # image and the configuration this version computes, keeping its volumes. One a planned
                    # upgrade restart left running stops as a stop would, before it is removed.
                    if actual.get('State', {}).get('Running'):
                        lab.docker('stop', actual['Id'])
                    lab.docker('rm', '-f', actual['Id'])
                    actual = None
        if actual:
            mounts = {(m.get('Name'), m['Destination']) for m in actual['Mounts']}
            if any((name, destination) not in mounts for name, destination in volumes):
                raise RuntimeError('Runtime persistent volume mismatch')
            if network not in actual['NetworkSettings']['Networks']:
                raise RuntimeError('Runtime network mismatch')
            lab.docker('start', actual['Id'])
            join(name, functions_links(name, component))
            self.adopted(actual['Id'])
            return actual['Id'],False
        flags = resource_policy.container_flags(tier)
        if (flags['memory'] is not None and flags['memory'] != memory) or (flags['cpus'] is not None and float(flags['cpus']) != float(cpus)):
            raise RuntimeError('Tier placement disagrees with the requested limits')
        path = PRIVATE/(name+'.env')
        lab.secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
        args = ['run', '-d', '--pull=never', '--name', name, '--label', 'io.sbarbase.owner='+OWNER, '--label', 'io.sbarbase.tier='+flags['label'], '--network', network,
                '--label', 'io.sbarbase.spec='+launch_spec(component, reference, memory, cpus, tier, network, volumes, binds, command),
                *(('--stop-timeout', str(STOP_DATABASE_SECONDS)) if component == 'db' else ()),
                '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus), '--pids-limit', str(flags['pids']),
                '--cpu-shares', str(flags['shares']), '--blkio-weight', str(flags['weight']),
                *resource_policy.io_flags(tier),
                '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(path)]
        for volume, destination in volumes:
            if not inspect('volume', volume):
                lab.docker('volume', 'create', '--label', 'io.sbarbase.owner='+OWNER, volume)
            args += ['-v', volume+':'+destination]
        for source, destination in binds:
            args += ['-v', f'{source}:{destination}:ro']
        created = lab.docker(*args, reference, *command).stdout.strip()
        # A recreated Auth, REST or Storage would otherwise drop off the environment networks.
        join(name, functions_links(name, component))
        self.adopted(created)
        return created,True

    def adopted(self, cid):
        """Notes a container this start runs, so retire_unadopted leaves it alone."""
        if not hasattr(self, 'touched'):
            self.touched = set()
        self.touched.add(cid)

    def retire_unadopted(self):
        """Stops each container a planned upgrade restart kept that this start neither started nor
        adopted: a kind this version no longer runs would otherwise keep running unmanaged, and be
        credited memory the placement does not count. Studio is left to its own reset (lab/studio.py)."""
        kept = getattr(self, 'kept', None)
        if not kept:
            return
        touched = getattr(self, 'touched', set())
        for cid, entry in kept['containers'].items():
            if cid in touched or str(entry.get('name', '')).endswith(kept_runtime.STUDIO):
                continue
            item = inspect('container', cid)
            if item and item.get('State', {}).get('Running'):
                print(f"{entry.get('name')} was kept by a planned upgrade restart and this version does not run it; it is stopped.", flush=True)
                lab.docker('stop', cid)

    def endpoint(self, name, port, network=NETWORK):
        item = inspect('container', name)
        address = item['NetworkSettings']['Networks'][network]['IPAddress']
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
        # A value of the switch that names anything else refuses every start, not only one that changes a pin.
        rolling.chosen()
        if lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip():
            raise RuntimeError('Staged source mode requires stopped recovery targets')
        if self.hba_writer is None or self.hba_writer.startup is None:raise RuntimeError('Explicit startup HBA ownership required')
        self.hba_writer.startup.verify()
        available = int(next(x.split()[1] for x in lab.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
        placement, _ = resource_policy.start_placement(len(self.values['environments']), realtime_count(), functions_count())
        # The containers a planned upgrade restart left running (lab/kept_runtime.py) are adopted below
        # through launch's own identity and drift checks; the memory they hold now, which the host no
        # longer counts as available, is counted back, since the placement counts them again.
        self.kept, why = kept_runtime.current(STATE, grace=kept_runtime.LATER_GRACE)
        if self.kept is None and kept_runtime.path(STATE).exists():
            # The settle let this start adopt them and the record no longer holds: nothing here could
            # tell which of them to retire, so the start stops and its supervisor stops them all.
            raise RuntimeError('The containers a planned upgrade restart kept can no longer be adopted ('+why+')')
        if self.kept is not None:
            available += kept_runtime.credit_mib(self.kept['containers']) * 1024
        # The console process a handover leaves serving (lab/handover.py) holds memory its exit returns.
        available += kept_runtime.admission_credit_mib() * 1024
        if available < (placement + resource_policy.START_RESERVE_MIB) * 1024:
            raise RuntimeError('Insufficient runtime memory headroom')
        if not inspect('network', NETWORK):
            lab.docker('network', 'create', '--internal', '--label', 'io.sbarbase.owner='+OWNER, NETWORK)
        launched_cid,created=self.launch(DB, 'db', {'POSTGRES_PASSWORD': self.values['admin'], 'POSTGRES_HOST': '/var/run/postgresql', 'POSTGRES_DB': 'postgres'},
                    '1024m', 1, database_mounts(),
                    ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none'), tier='system.db')
        self.wait_database()
        self.ensure_connection_capacity()
        self.hba_writer.ready(launched_cid,created=created)
        # Where direct database access connects (src/http/database-proxy.ts); the address changes only with the container.
        runtime_files.write_database(self.endpoint(DB, 5432).split('//')[1].split(':')[0], 5432, STATE)
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
            'FILE_SIZE_LIMIT': str(upload_limit()), 'DATABASE_MAX_CONNECTIONS': '3', 'MULTITENANT_DATABASE_MAX_CONNECTIONS': '3',
            'PG_QUEUE_ENABLE': 'false', 'ENABLE_IMAGE_TRANSFORMATION': 'false', 'S3_PROTOCOL_ENABLED': 'false',
            'X_FORWARDED_HOST_REGEXP': r'^(e_[a-f0-9]{24})\.storage\.internal$', 'LOG_LEVEL': 'error'},
            '512m', .5, [(PREFIX+'-objects', '/tmp/storage-data')], tier='system.storage')
        self.wait(self.endpoint(PREFIX+'-storage', 5001)+'/tenants', {'apikey': self.values['storage_admin']})
        # A side by side replacement a crash interrupted is finished or undone before anything resumes,
        # and the start of an upgrade begins a new plan of what the supervisor replaces after it.
        # One record in a state repair does not recognise holds that environment only (lab/rolling.py repair_or_hold).
        self.held = rolling.repair_or_hold(self)
        rolling.begin(self)
        self.resume_published_environments()
        rolling.finish_start(self)
        self.retire_unadopted()

    def resume_published_environments(self):
        # Credential reservations must only be dispatched by an authorized worker.
        published = runtime_files.read_endpoints(STATE, strict=True)
        for e in tuple(self.values['environments']):
            if e in getattr(self,'held',{}):
                continue
            if e in published and not source_fence.is_fenced(self.sql,e):
                self.resume(e)

    def resume(self, e):
        """Resume published state without native schema repair or tenant creation."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        published = runtime_files.read_endpoints(STATE, strict=True)
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Resume requires a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        rolling.require_settled(e,plan=False)
        for service in ('auth','rest'):
            if not inspect('container',PREFIX+'-'+e+'-'+service):
                raise RuntimeError('Resume requires retained service containers')
        # Tenant code can act as the REST login (RESET ROLE in a function PostgREST runs) and change
        # its own role settings, so the platform's deadlines are written again rather than refused.
        # A REST container that is still running (the runner died without its stop stage) would reconnect
        # at once and could take the rows again before the pin, so it is stopped first; `launch` starts it.
        rest=PREFIX+'-'+e+'-rest'
        item=inspect('container',rest)
        # Also one a planned upgrade restart left running (lab/kept_runtime.py): no session of the REST
        # login may outlive the pin with settings tenant code chose, so REST stops for it, unless
        # SBARBASE_ROLLING_SERVICES names `rest`: then the kept one keeps serving, and every session of
        # its login is ended once more after the pin (lab/rolling.py pin_serving).
        running=bool(item and item.get('State',{}).get('Running'))
        serving=running and rolling.keeps_serving(self,e,item) and rolling.pin_serving(self,e)
        if not serving:
            if running:
                lab.docker('stop',rest)
            self.rest_deadlines(e,rewrite=True,end_sessions=True)
        # The catalog says what the Data API serves; a restored or rebuilt database gets it again here.
        self.data_api(e,refuse_missing=False)
        if serving:
            # That write ends the login's sessions again before its own commit; one the serving REST opened
            # in between has the settings from before it, so every session ends once more.
            self.sql(rolling.sessions_sql(e))
        self.activate_services(e,self.values['environments'][e],creating=False)
        self.scheduled_jobs_back(e)

    def kept_running(self, item):
        """True when this running container is one a planned upgrade restart left, unchanged since."""
        kept = getattr(self, 'kept', None)
        if not kept or not item or not item.get('State', {}).get('Running'):
            return False
        entry = kept['containers'].get(item.get('Id'))
        return isinstance(entry, dict) and entry.get('image') == item.get('Image') and entry.get('name') == item.get('Name', '').lstrip('/')

    def rest_deadlines(self, e, executor=None, validate_only=False, rewrite=False, end_sessions=False):
        execute = executor or self.sql
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if rewrite:
            # Tenant code acting as the REST login can set any of its own role settings, in any
            # database and globally. A start leaves it exactly the platform's three in its own database
            # (REST_PINS): anything else changes how its sessions run, and a global one makes a restore
            # refuse. The search path keeps a function planted in `public` from taking the place of a
            # built-in in the query PostgREST loads its configuration with.
            exact = execute(rest_settings_sql(e)).stdout.strip()
            # The password too, whenever the runtime file holds it: a settings save (end_sessions False)
            # leaves it alone, since REST is serving, and every path that starts REST writes it again.
            password = (((getattr(self, 'values', None) or {}).get('environments') or {}).get(e) or {}).get('rest') if end_sessions else None
            # Generated passwords are 64 hex characters (secrets.token_hex(32)); anything else is not ours to write.
            if not (password and re.fullmatch(r'[a-f0-9]{64}', password)):
                password = None
            if exact != rest_exact(e):
                # In one transaction with the password, after ending the sessions (rest_pin_sql).
                execute(rest_pin_sql(e, end_sessions, password))
            elif password:
                execute(rest_password_sql(e, password, end_sessions=True))
            return
        # Role defaults affect new logins. Refuse silent changes under a warm pool.
        deadline_current = execute(f"SELECT EXISTS(SELECT 1 FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole JOIN pg_database d ON d.oid=s.setdatabase WHERE r.rolname='{e}_rest' AND d.datname='{e}' AND s.setconfig @> ARRAY['statement_timeout=8s','transaction_timeout=12s']);").stdout.strip() == 't'
        if validate_only:
            if not deadline_current:raise RuntimeError('Existing REST deadlines require explicit reconciliation')
            return
        rest_container = inspect('container', PREFIX+'-'+e+'-rest')
        if not deadline_current and rest_container and rest_container.get('State', {}).get('Running'):
            raise RuntimeError('REST deadline changes require stopping the owned runtime first')
        # Bounded like every other statement that writes these rows (REST_PIN_BOUNDS).
        execute(data_api.SAFE + REST_PIN_BOUNDS + REST_PINS.format(e=e))

    def data_api(self, e, executor=None, refuse_missing=True):
        """Write the environment's Data API settings in its own database, as sbarbase_rest.settings(),
        and tell PostgREST to read them (lab/data_api.py). Returns the schemas left out.

        Asked for by the console (refuse_missing), the saved settings are written, and a schema the
        database does not have refuses the whole change: PostgREST keeps what it serves, and
        lab/data_api.py records the new settings as applied only on success. At a start, resume or
        in-place restore, the settings last applied are written again; a schema missing by then is
        left out, since PostgREST would otherwise answer every request with 503.

        Either way this writes only the served settings and its own report to the catalog row, never
        the saved settings or their state: those belong to the console and lab/data_api.py, so a
        change saved meanwhile is neither lost nor marked applied by a start. The one other write is
        a new row for an environment that has none (a moved database's settings, or the defaults when
        `public` is gone), which `INSERT OR IGNORE` never lets replace a row the console wrote."""
        outcome = self.data_api_write(e, executor, refuse_missing)
        return [] if outcome is None else self.data_api_finish(e, outcome, executor, refuse_missing)

    def ensure_guard(self, e, executor=None):
        """Install the Data API's guards and settings functions when the presence check finds anything
        missing. Installing takes locks a session of the environment's REST login can hold (an open GRANT
        on the platform's schema), so a path that does not end those sessions first installs only then."""
        execute = executor or self.sql
        if execute(data_api.guard_present_sql(e), e).stdout.strip() != 't':
            execute(data_api.guard_sql(e), e)

    def data_api_write(self, e, executor=None, refuse_missing=True):
        """The part of data_api() a tenant session can make wait: the guard, the pin and the write. A
        console change runs it under its environment's lock alone (data_api_command). None when there
        was nothing left to do."""
        execute = executor or self.sql
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        catalog = STATE/'control.sqlite'
        desired, applied, _state, _failure = data_api.row(e, catalog)
        settings = desired if refuse_missing else applied
        if refuse_missing and settings is None:
            raise DataApiRefusal('There are no saved Data API settings to apply.')
        # Installed only when anything is missing (a restore, a rebuilt cluster): a console change does
        # not take locks a tenant session can hold when there is nothing to install (found in review).
        self.ensure_guard(e, execute)
        # Every path that starts REST writes the settings first, and pre_config refuses a session whose
        # search path is not pg_catalog: a restored archive from before the pin would otherwise make
        # PostgREST fall back to its container's empty schema at startup, serving nothing. So the pin comes first.
        self.rest_deadlines(e, executor=execute, rewrite=True, end_sessions=not refuse_missing)
        current = execute(data_api.serving_sql(e), e).stdout
        if settings is None and desired is None:
            # No row: a database moved here from another installation keeps the settings it
            # brings, as far as they are valid and its schemas exist; anything else is reset.
            settings = data_api.adoptable(current)
            if settings is not None:
                # Adopted even when a schema it names is missing: the write below leaves that one out
                # and reports it, where a reset would publish `public`.
                if not data_api.adopt(e, settings, catalog):
                    # The console saved settings meanwhile; they are applied by the supervisor, and
                    # until then the database keeps what it brought.
                    data_api.record_served(e, data_api.serving(current), catalog)
                    return None
        if settings is None and data_api.existing(execute(data_api.present_sql(data_api.DEFAULT), e).stdout) != {'public'}:
            # The defaults name `public`, and it is gone: PostgREST would answer 503 to everything. The
            # defaults are recorded as this environment's settings and go through the write below like
            # any others, which serves the empty schema, retries and claims the lock, and says why. With
            # the row, the next start serves `public` again once it exists, instead of taking the empty
            # schema for settings a moved database brought.
            settings = dict(data_api.DEFAULT)
            data_api.adopt(e, settings, catalog, actor='system:start')
        # Back to the defaults, which name `public`, through the same write as any other settings: checked
        # again under the lock, retried and claimed, and on failure reported rather than raised into the
        # start (found in review: a separate reset left `public` served while gone, then stopped every start).
        reset = settings is None and any(line.startswith('pgrst.') for line in current.splitlines())
        if reset:
            settings = dict(data_api.DEFAULT)
        if settings is None:
            data_api.record_served(e, None, catalog)
            data_api.record_report(e, None, catalog)
            return None
        # The write checks existence again under the guard's lock (data_api.write_sql). A schema the
        # tenant drops in between makes it roll back, and the settings are worked out again.
        written, missing, wrote = False, [], False
        for attempt in range(3):
            existing = data_api.existing(execute(data_api.present_sql(settings), e).stdout)
            served, missing = data_api.effective(settings, existing)
            if reset and missing:
                # `public` went after the check above: the defaults are recorded as this environment's
                # settings and written like any others, which serves the empty schema and says why.
                data_api.adopt(e, settings, catalog, actor='system:start')
                reset = False
            if missing and refuse_missing:
                raise DataApiRefusal(data_api.missing_text(missing))
            if not refuse_missing and data_api.serving(current) == served:
                written = True
                break
            # After a first busy try, a start or restore does not leave what is served to a tenant
            # session: the write ends the sessions holding or waiting for the lock and queues for it
            # itself, so a session that asks for it later waits behind the platform.
            claim = not refuse_missing and attempt > 0
            result = execute(data_api.write_sql(e, served, claim=claim, reset=reset), e, check=False)
            if not result.returncode:
                written = wrote = True
                break
            error = getattr(result, 'stderr', '') or ''
            if data_api.RACE in error:
                continue
            if data_api.BUSY in error or 'lock timeout' in error:
                # A schema change in this environment holds the lock; it ends with its transaction.
                time.sleep(1)
                continue
            if refuse_missing:
                raise RuntimeError('The Data API settings could not be written')
            break
        return served, missing, written, wrote, settings

    def data_api_finish(self, e, outcome, executor=None, refuse_missing=True, verify=False, told=None):
        """Tell PostgREST and record the outcome of data_api_write; `told` when the caller signalled it
        already. With `verify`, for a console change that wrote without the installation's lock, a start
        or restore that wrote other settings meanwhile makes it ask again (DataApiBusy) instead of
        recording settings the database no longer holds."""
        execute = executor or self.sql
        catalog = STATE/'control.sqlite'
        served, missing, written, wrote, settings = outcome
        if verify and wrote:
            read = execute(data_api.serving_sql(e), e, check=False)
            # A read-back that fails proves nothing either way: asked again, never taken as a match (found in review).
            if read.returncode or data_api.serving(read.stdout) != served:
                raise DataApiBusy('The Data API settings changed meanwhile, or could not be read back')
        not_told = False
        if wrote:
            # After the commit, and through the environment's own REST container, never the cluster's
            # shared notification queue (data_api.RELOAD_SQL says why). If it cannot be signalled the
            # settings stay written and are recorded as applied, with a report: they are what the
            # database, the guard and Studio hold, and PostgREST takes them at its next reload or start.
            not_told = not (self.tell_rest(e) if told is None else told)
            if not_told:
                print(f'PostgREST of {e} could not be told to reload its Data API settings.', file=sys.stderr)
        if refuse_missing and not written:
            raise DataApiRefusal('A schema change in this environment kept the settings from being applied, or a schema '
                                 'was dropped meanwhile. Save them again; a session that holds its lock blocks this until it ends.')
        # What PostgREST serves, read back from the database rather than assumed, and this run's
        # own report, replaced every time so an earlier one never lingers.
        read = execute(data_api.serving_sql(e), e, check=False)
        if read.returncode:
            if not wrote:
                raise RuntimeError('The Data API settings could not be read back')
            # The write is committed and PostgREST was told: a read-back that fails now must not turn a
            # change that took effect into a failed one. What was written is what the database holds.
            held = served
        else:
            held = data_api.serving(read.stdout)
        if not written:
            print(f'Data API settings for {e} were not written at start.', file=sys.stderr)
            report = data_api.unwritten_text(held)
        elif missing:
            report = data_api.left_out_text(missing, served['schemas'] == [data_api.EMPTY])
        else:
            report = None
        if not_told:
            report = (report + ' ' if report else '') + data_api.not_told_text()
        if verify:
            # A console change is recorded as applied here, under the operation lock: recorded after the
            # command ends, a start in between could write the earlier settings back first. In one
            # transaction, and an error is raised, not passed over: the command then exits 4 rather than
            # leaving the row pending, or applied beside a stale served list (both found in review).
            # A catalog busy with another writer is usually free again within seconds, so it is tried again.
            for attempt in range(3):
                try:
                    data_api.record_applied(e, held, report, settings, catalog)
                    break
                except data_api.sqlite3.Error:
                    if attempt == 2:
                        raise
                    time.sleep(1)
            return missing
        data_api.record_served(e, held, catalog)
        data_api.record_report(e, report, catalog)
        return missing

    def tell_rest(self, e):
        """Make the environment's PostgREST read its settings and schema cache again. SIGUSR1 reloads the
        schema cache and, on the pinned PostgREST, the configuration with it (measured; SIGUSR2 alone
        reloads only the configuration and so does not load a newly exposed schema). True when it is not
        running (it reads the settings when it starts) or the signal was sent. It goes to this
        environment's own container: unlike NOTIFY it uses nothing the cluster shares."""
        name = PREFIX+'-'+e+'-rest'
        try:
            item = inspect('container', name)
            if not (item and item.get('State', {}).get('Running')):
                return True
            lab.docker('kill', '-s', 'SIGUSR1', name)
        except Exception:
            # The write has been committed: an inspection or signal that fails is "not told", never a failed change.
            return False
        return True

    def data_api_observe(self, e):
        """After a restore whose Data API step failed: record what the database serves now and say so."""
        catalog = STATE/'control.sqlite'
        held = data_api.serving(self.sql(data_api.serving_sql(e), e).stdout)
        data_api.record_served(e, held, catalog)
        data_api.record_report(e, data_api.unwritten_text(held), catalog)

    def data_api_change(self, e):
        """The console's change, with the checks every per-environment command makes first."""
        self.data_api_checks(e)
        return self.data_api(e)

    def data_api_checks(self, e):
        """The checks every per-environment command makes first."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB):
            raise RuntimeError('Start the upstream runtime first')
        if e not in published_endpoints() or e not in self.values['environments']:
            raise RuntimeError('Data API settings need a published environment')
        if source_fence.is_fenced(self.sql, e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')

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
        # The Data API's guards and settings functions, before REST first starts (lab/data_api.py).
        execute(data_api.guard_sql(e), e)
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
            # With management Auth: at most eight environments, 5888 MiB/5.75 CPUs before Realtime or Functions.
            if len(self.values['environments']) + int(new_environment) > ENVIRONMENT_LIMIT:
                raise AdmissionLimitError('Local runtime admission limit reached')
            try:
                reason = resource_admission.refusal(resource_admission.snapshot())
                placement, cpus = restart_placement(len(self.values['environments']) + int(new_environment), realtime_count(), functions_count())
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
            runtime_files.write_runtime_secret(self.values, self.path.parent)
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
        # A rotation recorded in the runtime secret and not finished yet, whether it runs now or was
        # interrupted: every service that holds the signing secret takes the new one.
        rotating = not creating and bool(v.get('jwt_rotating'))
        # This environment's own mail configuration, read once per invocation.
        # mail_config.load returns None when the environment has no mail file,
        # and the builder then returns exactly the dict it returned before.
        mail = mail_config.load(e)
        for service, port, suffix in [('auth', 9999, '/health'), ('rest', 3000, '/')]:
            name = PREFIX+'-'+e+'-'+service
            # No per-environment class field exists yet, so both environment
            # services launch under the production row (docs/engineering/RESOURCE-POLICY.md 3.2).
            config = self.service_configuration(e, v, service, mail)
            self.launch(name, service, config, '256m', .25, existing_only=not creating, tier='production', rotating=rotating)
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
        elif rotating:
            payload = {'anonKey': token(v['jwt'], 'anon'), 'serviceKey': token(v['jwt'], 'service_role'), 'jwtSecret': v['jwt']}
            status, _ = http(admin+'/tenants/'+e, 'PATCH', json.dumps(payload).encode(), headers)
            if status not in (200, 204):
                raise RuntimeError('Storage tenant update failed')
        public = self.endpoint(PREFIX+'-storage', 5000)
        self.wait(public+'/bucket', {'authorization': 'Bearer '+token(v['jwt'], 'service_role'), 'x-forwarded-host': e+'.storage.internal'})
        if self.sql("SELECT to_regclass('storage.objects') IS NOT NULL AND to_regprocedure('auth.uid()') IS NOT NULL;", e).stdout.strip() != 't':
            raise RuntimeError('Environment migrations incomplete')
        endpoints['storage'] = {'url': public, 'tenantHost': e+'.storage.internal'}
        endpoints['serviceConcurrency'] = {'rest': int(lab.rest_configuration(e, v, DB)['PGRST_DB_POOL'])}
        if creating:effect_receipt.native_stage(STATE,e,'publication')
        all_endpoints = runtime_files.read_endpoints(STATE, strict=True)
        previous = all_endpoints.get(e, {}).get('realtime')
        if previous and not creating:
            # Realtime was turned on for this environment: bring it back, and let it run its
            # schema migrations again only when its pinned image changed.
            endpoints['realtime'] = self.realtime_start(e, migrate=rotating or previous.get('migrated') != self.pins['realtime']['id'])
        if all_endpoints.get(e, {}).get('functions') and not creating:
            endpoints['functions'] = self.functions_back(e, all_endpoints[e]['functions'])
        # Direct database access is the developer login's own state, not a service to restart.
        if all_endpoints.get(e, {}).get('database') and not creating:
            endpoints['database'] = all_endpoints[e]['database']
        # Scheduled jobs are the scheduler login's state; the scheduler process runs them.
        if all_endpoints.get(e, {}).get('cron') and not creating:
            endpoints['cron'] = all_endpoints[e]['cron']
        all_endpoints[e] = endpoints
        runtime_files.write_endpoints(all_endpoints, STATE)
        if rotating:
            # Not as the superuser, and bounded (SESSIONS_REVOKE). Sessions one environment's tenant code holds
            # never stop a start: the mark stays, the rotation is reported unfinished, and the next start or
            # rotation ends them. `rotate_signing` itself fails then.
            if revoke_auth_sessions(e, v['auth']):
                v.pop('jwt_rotating', None)
                runtime_files.write_runtime_secret(self.values, self.path.parent)
            else:
                print(f'Environment {e}: the signing rotation is unfinished, its Auth sessions could not be ended; rotate again.', file=sys.stderr)

    def service_configuration(self, e, v, service, mail=None):
        """The settings this version runs the environment's Auth or REST with (activate_services, and
        lab/rolling.py for a side by side replacement)."""
        if service == 'auth':
            return lab.auth_configuration(e, v, DB, mail, load_settings(e))
        # The function that applies the Data API settings, and the empty schema as the container's
        # own: a load that function refuses falls back to these, never to `public`.
        config = lab.rest_configuration(e, v, DB)
        config = {**config, **data_api.CONTAINER_ENV}
        return config

    def functions_back(self, e, entry):
        """Edge Functions come back with the environment's other services, on a start or a signing key
        rotation, except while a write pause holds it: they stay stopped, and keep their entry, until
        write_pause.py resume starts them again."""
        import write_pause
        return entry if write_pause.is_paused(e) else self.functions_start(e)

    def rotate_signing(self, e):
        """Give one environment a new JWT signing secret. Every token signed with the old one stops
        working and every session ends: people sign in again, and Studio and Edge Functions get new keys. Publishable and secret API
        keys are not JWTs and keep working. The new secret and a rotation mark are written first,
        so a rotation that stops halfway is finished by the next start (`activate_services`)."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        if e not in published_endpoints() or e not in self.values['environments']:
            raise RuntimeError('Rotation needs a published environment')
        if source_fence.is_fenced(self.sql, e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        rolling.require_settled(e)
        for service in ('auth', 'rest'):
            if not inspect('container', PREFIX+'-'+e+'-'+service):
                raise RuntimeError('Rotation needs the retained service containers')
        v = self.values['environments'][e]
        if not v.get('jwt_rotating'):
            v['jwt'] = secrets.token_hex(32)
            v['jwt_rotating'] = True
            runtime_files.write_runtime_secret(self.values, self.path.parent)
        # The recreated REST container reads its settings at startup, so the login is pinned first, and
        # its sessions are ended so that tenant code holding the role-setting rows open cannot stop a
        # rotation (REST is recreated right after).
        self.rest_deadlines(e, rewrite=True, end_sessions=True)
        self.activate_services(e, v, creating=False)
        if v.get('jwt_rotating'):
            raise RuntimeError('Auth sessions could not be ended; the rotation is unfinished')

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
        published = runtime_files.read_endpoints(STATE, strict=True)
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Reconcile requires a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        rolling.require_settled(e)
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
        published = runtime_files.read_endpoints(STATE, strict=True)
        if e not in published or e not in self.values['environments']:
            raise RuntimeError('Sign-in settings need a published environment')
        if source_fence.is_fenced(self.sql,e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        rolling.require_settled(e)
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
        endpoints = runtime_files.read_endpoints(STATE, strict=True)
        if e in endpoints:
            endpoints[e]['auth'] = self.endpoint(name, 9999)
            runtime_files.write_endpoints(endpoints, STATE)

    def realtime_values(self, e):
        """Realtime's own credentials for this environment, generated once and kept with the others."""
        v = self.values['environments'][e]
        missing = {'realtime': 32, 'realtime_api': 32, 'realtime_base': 64, 'realtime_enc': 8}
        if any(key not in v for key in missing):
            for key, size in missing.items():
                v.setdefault(key, secrets.token_hex(size))
            runtime_files.write_runtime_secret(self.values, self.path.parent)
        return v

    def realtime_configuration(self, e, v):
        return {'PORT': str(REALTIME_PORT), 'DB_HOST': DB, 'DB_PORT': '5432', 'DB_NAME': e, 'DB_USER': f'{e}_realtime',
                'DB_PASSWORD': v['realtime'], 'DB_AFTER_CONNECT_QUERY': 'SET search_path TO _realtime', 'DB_POOL_SIZE': '2',
                'DB_ENC_KEY': v['realtime_enc'], 'DB_IP_VERSION': 'ipv4', 'API_JWT_SECRET': v['realtime_api'],
                'METRICS_JWT_SECRET': v['realtime_api'], 'SECRET_KEY_BASE': v['realtime_base'], 'APP_NAME': 'realtime',
                'ERL_AFLAGS': '-proto_dist inet_tcp', 'DNS_NODES': "''", 'RLIMIT_NOFILE': '10000', 'SEED_SELF_HOST': 'false',
                'RUN_JANITOR': 'true', 'DISABLE_HEALTHCHECK_LOGGING': 'true', 'LOG_LEVEL': 'error', 'REGION': 'local',
                'SLOT_NAME_SUFFIX': realtime_slot_suffix(e)}

    def realtime_database(self, e, v):
        """The environment's Realtime login and schemas. The login reaches only this database (HBA). Both scripts
        run under the operation lock and wait for rows a session of the environment can hold (the database's,
        with a no-op GRANT left open; a scheduled job can do so too), so each is one bounded transaction
        (ROW_BOUNDS) that fails after its bound instead (docs/engineering/SCHEDULED-JOBS.md)."""
        role = f'{e}_realtime'
        self.sql(f"""BEGIN; {ROW_BOUNDS}
DO $$ BEGIN
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
GRANT supabase_realtime_admin TO {role};
-- Realtime quiets its change poller's own logging; only that one setting is granted.
GRANT SET ON PARAMETER log_min_messages TO {role}, supabase_realtime_admin;
COMMIT;""")
        self.sql(f"""BEGIN; {ROW_BOUNDS}
DO $$ BEGIN
  -- Realtime's own migration hands `realtime` to the superuser, so a later start finds it that way.
  IF EXISTS (SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner WHERE n.nspname IN ('realtime', '_realtime')
      AND r.rolname <> '{role}' AND NOT r.rolsuper) THEN
    RAISE EXCEPTION 'A Realtime schema belongs to another login';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_publication p JOIN pg_roles r ON r.oid = p.pubowner WHERE p.pubname = 'supabase_realtime'
      AND NOT r.rolsuper AND r.rolname <> '{e}_developer') THEN
    RAISE EXCEPTION 'The Realtime publication belongs to another login';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS _realtime AUTHORIZATION {role};
CREATE SCHEMA IF NOT EXISTS realtime AUTHORIZATION {role};
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime FOR TABLES IN SCHEMA public;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{e}_developer') THEN
    ALTER PUBLICATION supabase_realtime OWNER TO {e}_developer;
  END IF;
END $$;
COMMIT;""", e)
        self.sql(scheduled_jobs.limit_sql(e, current_database_limit(e, realtime=True)))

    def realtime_start(self, e, migrate):
        """Run the environment's Realtime. With `migrate`, its login is a superuser only while Realtime
        creates its own schema and tenant, the way upstream expects, and loses it before this returns."""
        v = self.realtime_values(e)
        name = PREFIX+'-'+e+'-realtime'
        if migrate:
            self.realtime_database(e, v)
            retire(name)
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
        if migrate:
            # Closing those sessions can leave a Realtime process holding a dead connection (a
            # database-change listener that never hears another row). A restart opens every
            # connection again as the ordinary login.
            lab.docker('restart', '-t', '10', name)
            base = self.endpoint(name, REALTIME_PORT)
            wait_ready(base+'/healthcheck', failure='Realtime did not start', attempts=REALTIME_BOOT_SECONDS*2)
        return {'url': base, 'tenantHost': realtime_tenant(e)+'.realtime', 'migrated': self.pins['realtime']['id']}

    def realtime_grants(self, e):
        """Under the operation lock, and a session acting as `authenticated` (a scheduled job, as the developer)
        can hold the realtime schema's or its tables' rows with a no-op GRANT: one bounded transaction."""
        role = f'{e}_realtime'
        self.sql(f"""BEGIN; {ROW_BOUNDS}
GRANT USAGE, CREATE ON SCHEMA realtime TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA realtime TO {role};
GRANT ALL ON ALL SEQUENCES IN SCHEMA realtime TO {role};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA realtime TO {role};
GRANT USAGE, CREATE ON SCHEMA _realtime TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA _realtime TO {role};
GRANT ALL ON ALL SEQUENCES IN SCHEMA _realtime TO {role};
COMMIT;""", e)

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

    def developer_sql(self, e, password):
        """The environment's developer login: what a project's own `postgres` user does on Supabase,
        within one database. It may create and change anything in `public`, and, as a member of
        the environment's Auth and Storage logins, add triggers on `auth.users` and policies on
        `storage.objects`. It may create schemas of its own, never under a name the platform creates
        itself (RESERVED_SCHEMAS): a schema it made first, under a name the platform later creates
        with IF NOT EXISTS (Realtime's, whose migration runs as superuser), would run its code with
        those rights. The right to create schemas comes last, in developer_grants_sql, after the guard.
        It is not a superuser and cannot reach another environment's database.

        The login itself is not opened here: developer_login_sql does that, under the operation lock and
        the write pause guard. Bounded, since a session acting as the login can hold its role rows (a
        password or role setting it changes for itself in a transaction it leaves open)."""
        role = f'{e}_developer'
        members = ', '.join(f'{e}_{service}' for service in ('auth', 'storage')) + ', anon, authenticated, service_role'
        return data_api.SAFE + DEVELOPER_BOUNDS + f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} NOLOGIN;
  END IF;
END $$;
ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT BYPASSRLS
  CONNECTION LIMIT {connection_budget.DIRECT_CONNECTIONS} PASSWORD {sql_literal(password)};
ALTER ROLE {role} IN DATABASE {e} SET search_path TO public, extensions;
GRANT CONNECT, TEMPORARY ON DATABASE {e} TO {role};
GRANT {members} TO {role};
"""

    def developer_grants_sql(self, e):
        """Run by the superuser in the environment's database. The reserved schema guard is in place
        before the login may create schemas, and Vault before the login is given it. The guard is an
        event trigger the superuser owns, so the login can neither drop nor disable it."""
        role = f'{e}_developer'
        reserved = ', '.join(sql_literal(name) for name in RESERVED_SCHEMAS)
        extensions = ', '.join(sql_literal(name) for name in RESERVED_EXTENSIONS)
        # The search path is pinned to pg_catalog for the whole script: the developer login may create
        # functions in `public`, and one that matches a call below better than pg_catalog's (pg_has_role
        # in studio_identity_sql) would run as the superuser. Bounded like guard_sql, since its grants wait
        # for transactions the environment's logins leave open. The exposed schema guard itself is
        # not part of it (found in review: installed every time, a REST login session holding the platform's
        # catalog rows held up this script under the operation lock, and every other environment's database
        # toggle behind it); developer_ready refuses when it is missing, and the import installs it first.
        return data_api.SAFE + DEVELOPER_BOUNDS + f"""CREATE SCHEMA IF NOT EXISTS sbarbase_guard;
REVOKE ALL ON SCHEMA sbarbase_guard FROM PUBLIC;
CREATE OR REPLACE FUNCTION sbarbase_guard.reserved_schemas() RETURNS event_trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $guard$
DECLARE item record;
BEGIN
  -- The session's login, not the current role: supautils and SECURITY DEFINER code run as the superuser.
  IF (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = session_user) THEN
    RETURN;
  END IF;
  -- A reserved name is refused when the schema would belong to a login other than the superuser; an
  -- extension the image creates as the superuser (pg_graphql's `graphql`) may still bring its own.
  FOR item IN SELECT n.nspname FROM pg_catalog.pg_event_trigger_ddl_commands() c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.objid
      JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner WHERE c.object_type = 'schema' AND NOT r.rolsuper LOOP
    IF item.nspname IN ({reserved}) OR item.nspname LIKE 'sbarbase%' OR item.nspname LIKE 'supabase%' THEN
      RAISE EXCEPTION 'The schema name % is reserved for the platform', item.nspname USING ERRCODE = '42501';
    END IF;
  END LOOP;
  FOR item IN SELECT e.extname FROM pg_catalog.pg_event_trigger_ddl_commands() c
      JOIN pg_catalog.pg_extension e ON e.oid = c.objid WHERE c.object_type = 'extension' LOOP
    IF item.extname IN ({extensions}) THEN
      RAISE EXCEPTION 'The extension % is not available to an environment on a shared server', item.extname USING ERRCODE = '42501';
    END IF;
  END LOOP;
END $guard$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'sbarbase_reserved_schemas') THEN
    CREATE EVENT TRIGGER sbarbase_reserved_schemas ON ddl_command_end
      WHEN TAG IN ('CREATE SCHEMA', 'ALTER SCHEMA', 'CREATE EXTENSION', 'ALTER EXTENSION')
      EXECUTE FUNCTION sbarbase_guard.reserved_schemas();
  END IF;
END $$;
ALTER EVENT TRIGGER sbarbase_reserved_schemas ENABLE ALWAYS;
-- Vault, with the grants the upstream image gives a project's `postgres` user and service_role, without
-- the right to pass them on: a grant to anon would let anyone read the secrets through the API.
CREATE EXTENSION IF NOT EXISTS supabase_vault;
GRANT USAGE ON SCHEMA vault TO {role};
GRANT SELECT, DELETE, TRUNCATE, REFERENCES ON vault.secrets, vault.decrypted_secrets TO {role};
GRANT EXECUTE ON FUNCTION vault.create_secret, vault.update_secret, vault._crypto_aead_det_decrypt TO {role};
GRANT USAGE ON SCHEMA vault TO service_role;
GRANT SELECT, DELETE ON vault.secrets, vault.decrypted_secrets TO service_role;
GRANT EXECUTE ON FUNCTION vault.create_secret, vault.update_secret, vault._crypto_aead_det_decrypt TO service_role;
-- The API's default grants, given once, the first time (before supabase_migrations exists), as Supabase
-- gives them once at project creation: a migration that later narrows them keeps its choice when
-- access is turned off and on again.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'supabase_migrations') THEN
    ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
    ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
    ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon, authenticated, service_role;
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS supabase_migrations AUTHORIZATION {role};
GRANT USAGE, CREATE ON SCHEMA supabase_migrations TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA supabase_migrations TO {role};
GRANT USAGE, CREATE ON SCHEMA public, extensions TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA public TO {role};
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role};
-- Realtime's publication always exists and belongs to the login, as it belongs to a project's
-- `postgres` user on Supabase, so migrations may add tables to it. It publishes the `public` schema.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime FOR TABLES IN SCHEMA public;
  END IF;
END $$;
ALTER PUBLICATION supabase_realtime OWNER TO {role};
GRANT CREATE ON DATABASE {e} TO {role};
{studio_identity_sql(e)}"""

    def database_preconditions(self, e):
        """What a console change of direct database access checks first, each time it takes the operation lock."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB):
            raise RuntimeError('Start the upstream runtime first')
        if e not in published_endpoints() or e not in self.values['environments']:
            raise RuntimeError('Database access needs a published environment')
        if source_fence.is_fenced(self.sql, e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')

    def database_turn(self, e, on):
        """Turn one environment's direct database access off, the caller holding the operation lock. Turning
        it on is database_command: its grants can wait for a tenant, so they run outside that lock."""
        if on:
            raise RuntimeError('Turning database access on is database_command')
        self.database_preconditions(e)
        role = f'{e}_developer'
        with database_lock(e):
            self.sql(developer_login_sql(e, False) +
                     f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '{role}';")
            self.sql(scheduled_jobs.limit_sql(e, current_database_limit(e, direct=False)))
            endpoints = published_endpoints()
            endpoints[e].pop('database', None)
            runtime_files.write_endpoints(endpoints, STATE)

    def developer_ready(self, e):
        """The saved password, once the login's access rule and the exposed schema guard are in place."""
        role = f'{e}_developer'
        if self.sql(f"SELECT count(*) FROM pg_hba_file_rules WHERE '{role}' = ANY(user_name) AND error IS NULL;").stdout.strip() != '1':
            raise RuntimeError('Database access rule not published; restart Sbarbase once')
        try:
            password = json.loads((PRIVATE/'database'/f'{e}.json').read_text())['password']
        except (OSError, ValueError, KeyError):
            raise RuntimeError('No developer password was saved') from None
        if not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', password):
            raise RuntimeError('The saved developer password is not valid')
        # The exposed schema guard is installed when the environment is provisioned and repaired by a start or
        # restore. Missing here, it is not installed with REST serving, where a REST login session could hold
        # it up (found in review): the turn is refused, as for the rule above.
        if self.sql(data_api.guard_present_sql(e), e).stdout.strip() != 't':
            raise RuntimeError('Data API guard missing; restart Sbarbase once')
        return password

    def developer_admission(self, e):
        """Room in the cluster for the login's connections, unless it already has them. Under the operation
        lock: other environments' changes read and promise the same headroom."""
        if not direct_on(e):
            available, promised = (int(value) for value in self.sql(
                "SELECT current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int "
                "- current_setting('reserved_connections')::int, coalesce(sum(greatest(datconnlimit, 0)), 0) "
                "FROM pg_database WHERE datallowconn AND datname <> 'template1';").stdout.strip().split('|'))
            if promised + connection_budget.DIRECT_CONNECTIONS > available:
                raise AdmissionLimitError('Connection headroom unavailable')

    def developer_prepare(self, e, password):
        """The login's role, its password and its grants, with the login itself left as it is. This is what a
        tenant can make wait, each statement within DEVELOPER_BOUNDS, so a console change runs it under the
        environment's database access lock alone. Returns the database's clock after the password was
        written: a session that signed in before it may have used the old one."""
        self.sql(self.developer_sql(e, password))
        self.sql(self.developer_grants_sql(e), e)
        # Scheduled jobs run as the developer: its grants on cron and net, and the scheduler login's membership.
        self.sql(scheduled_jobs.grants_sql(e), e)
        return self.sql('SELECT pg_catalog.clock_timestamp();').stdout.strip()

    def developer_open(self, e, written):
        """Open the login, set the connection limit, close the sessions of an older password and publish.
        Run under the operation lock and the write pause guard; the one statement a tenant could make wait,
        opening the login, ends the sessions that hold role rows first (developer_login_sql)."""
        role = f'{e}_developer'
        self.developer_admission(e)
        self.sql(developer_login_sql(e, True))
        self.sql(scheduled_jobs.limit_sql(e, current_database_limit(e, direct=True)))
        if not re.fullmatch(r'[0-9 :.+-]{19,40}', written or ''):
            raise RuntimeError('The time the password was written is not known')
        self.sql(f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '{role}' "
                 f"AND backend_start < '{written}'::timestamptz;")
        endpoints = published_endpoints()
        endpoints[e]['database'] = {'user': role, 'database': e}
        runtime_files.write_endpoints(endpoints, STATE)

    def developer_on(self, e):
        """The developer login on, with the password the console saved last, in one go. Only for
        write_pause.py resume, which holds the operation lock and the pause lock for its whole length, as a
        start does: its grants are bounded, and it does not wait for the environment's database access
        lock, which a console change holds while its own grants run (resume then stops and is run again).
        The console's change is database_command."""
        with database_lock(e):
            password = self.developer_ready(e)
            self.developer_admission(e)
            self.developer_open(e, self.developer_prepare(e, password))

    def wait_database(self):
        for _ in range(120):
            result = self.sql("SELECT to_regrole('supabase_privileged_role') IS NOT NULL;", check=False)
            if result.returncode == 0 and result.stdout.strip() == 't' and lab.docker('exec', DB, 'pg_isready', '-h', '127.0.0.1', check=False).returncode == 0:
                return
            time.sleep(.5)
        raise RuntimeError('Database readiness timed out')

    def ensure_connection_capacity(self):
        """Give the shared database room for connection_budget.MAX_CONNECTIONS.

        First the container's process limit, one process per connection: a retained container
        keeps the limit it was created with, so a lower one is raised in place (docker update, no
        restart). Then max_connections: ALTER SYSTEM, which lives in the data directory, and one
        restart of the same container. A rebuilt cluster starts at the image's 100 and is raised on
        its first start. Nothing here lowers either value. When the raise fails but the current
        value still holds this installation's environments, the start goes on with a warning;
        otherwise it is refused.
        A way back to an older version keeps both higher values, which it does not mind; an older
        version whose own guard is lower than the environments now held refuses their jobs."""
        pids = resource_policy.TIERS['system.db'].pids
        item = inspect('container', DB)
        if item and 0 < (item.get('HostConfig', {}).get('PidsLimit') or 0) < pids:
            lab.docker('update', '--pids-limit', str(pids), DB)

        def current():
            result = self.sql("SELECT current_setting('max_connections');", check=False)
            value = result.stdout.strip()
            if result.returncode or not value.isdigit():
                raise RuntimeError('Database connection limit unreadable')
            return int(value)

        def enough_for_now(value):
            return connection_budget.fits(len(self.values['environments']), value, 3, 0)
        value = current()
        if value >= connection_budget.MAX_CONNECTIONS:
            return
        if self.sql(f'ALTER SYSTEM SET max_connections = {connection_budget.MAX_CONNECTIONS};', check=False).returncode:
            if enough_for_now(value):
                print('Database connection limit could not be raised; the current one still holds these environments', flush=True)
                return
            raise RuntimeError('Database connection limit could not be raised')
        print(f'Raising the database connection limit to {connection_budget.MAX_CONNECTIONS}; the database restarts once', flush=True)
        checkpoint()
        lab.docker('restart', '-t', '60', DB)
        self.wait_database()
        if current() < connection_budget.MAX_CONNECTIONS:
            raise RuntimeError('Database connection limit did not take effect')

    def functions_configuration(self, e, v, gateway):
        """The main service's settings: where the code and secrets are, the keys functions get, and
        the addresses of this environment's own services. Auth and REST are on the environment
        network; Storage is the host listener at its gateway, which pins this environment's tenant."""
        ten_years = 10 * 365 * 24 * 3600
        return {'SB_FUNCTIONS_DIR': '/home/deno/functions', 'SB_SECRETS_FILE': '/run/sbarbase/secrets.json',
                'SB_SELF_URL': f'http://{PREFIX}-{e}-functions:{FUNCTIONS_PORT}', 'SB_JWT_SECRET': v['jwt'],
                'SUPABASE_ANON_KEY': service_token(v['jwt'], {'role': 'anon', 'iss': 'sbarbase'}, ten_years),
                'SUPABASE_SERVICE_ROLE_KEY': service_token(v['jwt'], {'role': 'service_role', 'iss': 'sbarbase'}, ten_years),
                'SB_AUTH_URL': f'http://{PREFIX}-{e}-auth:9999', 'SB_REST_URL': f'http://{PREFIX}-{e}-rest:3000',
                'SB_STORAGE_URL': f'http://{gateway}:{FUNCTIONS_STORAGE_PORT}'}

    def functions_start(self, e):
        """Run the environment's Edge Functions: a fresh container of the pinned edge-runtime, with
        this environment's code and secrets mounted read-only and nothing of any other environment."""
        v = self.values['environments'][e]
        name = PREFIX+'-'+e+'-functions'
        code, private = functions_code(e), functions_private(e)
        code.mkdir(parents=True, exist_ok=True)
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not (private/'secrets.json').exists():
            lab.secure_file(private/'secrets.json', '{}')
        # Functions run code that handles visitor input, so they sit on their environment's own
        # network, never on the runtime network every environment's services share.
        network = functions_network(e)
        # Removed first, so a container from before the outbound network changed no longer names
        # the earlier one when ensure_egress decides whether that one can go. One a planned upgrade
        # restart left running, on exactly this network and the outbound one, with the image,
        # settings and mounts this start would give it, keeps serving instead (functions_kept).
        item = inspect('container', name)
        kept = item['Id'] if item and self.functions_kept(e, v, item, network) else None
        if item and not kept:
            retire(name)
        item = inspect('network', network)
        if not item:
            lab.docker('network', 'create', '--internal', '--label', 'io.sbarbase.owner='+OWNER, '--label', 'io.sbarbase.functions='+e, network)
        elif not item.get('Internal') or (item.get('Labels') or {}).get('io.sbarbase.functions') != e:
            raise RuntimeError('Environment network mismatch; explicit reconciliation required')
        for service in ('auth', 'rest'):
            join(PREFIX+'-'+e+'-'+service, [network])
        # Storage takes its tenant from a header the caller sets, so it is never on an environment
        # network: an earlier version attached it to every one, and this takes it off again.
        storage = inspect('container', PREFIX+'-storage')
        if storage and network in storage['NetworkSettings']['Networks']:
            lab.docker('network', 'disconnect', '--force', network, PREFIX+'-storage')
        gateway, subnet = functions_address(network)
        ensure_egress()
        # A kept one was checked against all of this already (functions_kept); launch would compare
        # its two keys with freshly made ones and refuse them as drift.
        if kept:
            self.adopted(kept)
        else:
            self.launch(name, 'functions', self.functions_configuration(e, v, gateway), '384m', .5, tier='production.functions',
                        binds=functions_binds(e), command=FUNCTIONS_COMMAND, network=network)
            lab.docker('network', 'connect', EGRESS, name)
        base = self.endpoint(name, FUNCTIONS_PORT, network)
        # A name no function can have: the main service answers 404 once it serves.
        for _ in range(FUNCTIONS_BOOT_SECONDS * 2):
            try:
                if http(base+'/-')[0] == 404:
                    break
            except OSError:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Edge Functions did not start')
        # The loopback API serves Storage at the gateway only to sources inside this subnet: Linux
        # accepts a packet for any host address on any interface, so the address alone is not enough.
        return {'url': base, 'image': self.pins['functions']['id'],
                'storage': {'host': gateway, 'port': FUNCTIONS_STORAGE_PORT, 'subnet': subnet}}

    def functions_kept(self, e, v, item, network):
        """A Functions container a planned upgrade restart left running, that functions_start would start
        exactly so: the pinned image, launch's own description of it (launch_spec: the tier's limits, the
        command, the mounts, the logs), its environment's network and the outbound one only, the three
        mounts read-only, the settings functions_configuration gives
        (its two keys need only still be valid ones of this environment, since they carry when they were
        made), and the main service's code unchanged since the commit it was started with: the runtime
        reads that code once, when it starts."""
        if not self.kept_running(item):
            return False
        try:
            networks = set(item['NetworkSettings']['Networks'])
            if networks != {network, EGRESS} or not inspect('network', network):
                return False
            reference, image = self.image('functions')
            spec = launch_spec('functions', reference, '384m', .5, 'production.functions', network, binds=functions_binds(e), command=FUNCTIONS_COMMAND)
            if item['Image'] != image or (item['Config'].get('Labels') or {}).get('io.sbarbase.spec') != spec:
                return False
            gateway, _subnet = functions_address(network)
            configured = dict(entry.split('=', 1) for entry in item['Config'].get('Env', []) if '=' in entry)
            wanted = self.functions_configuration(e, v, gateway)
            # Both ways: a setting this version no longer gives must not stay on a kept container. What
            # the image itself sets is not ours.
            own = json.loads(lab.docker('image', 'inspect', '--format', '{{json .Config.Env}}', reference).stdout or 'null') or []
            image = {entry.split('=', 1)[0] for entry in own if '=' in entry}
            if set(configured) - (image - set(wanted)) != set(wanted):
                return False
            for key, value in wanted.items():
                if key in FUNCTIONS_KEYS:
                    if not valid_service_token(configured.get(key), v['jwt'], FUNCTIONS_KEYS[key]):
                        return False
                elif configured.get(key) != value:
                    return False
            mounts = {(m.get('Source'), m.get('Destination'), m.get('RW')) for m in item.get('Mounts', [])}
            for source, target in functions_binds(e):
                if (str(source), target, False) not in mounts:
                    return False
            origin = (self.kept or {}).get('from')
            return bool(origin) and subprocess.run(['git', 'diff', '--quiet', origin, 'HEAD', '--', 'lab/functions/main'],
                                                   cwd=lab.ROOT, capture_output=True, timeout=30).returncode == 0
        except (KeyError, RuntimeError, ValueError, OSError, subprocess.SubprocessError, resource_policy.ResourcePolicyError):
            return False

    def functions_turn(self, e, on):
        """Turn one environment's Edge Functions on or off: an operator action from the console."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB) or not inspect('container', PREFIX+'-storage'):
            raise RuntimeError('Start the upstream runtime first')
        endpoints = published_endpoints()
        if e not in endpoints or e not in self.values['environments']:
            raise RuntimeError('Edge Functions need a published environment')
        name = PREFIX+'-'+e+'-functions'
        guard = contextlib.ExitStack()
        if on:
            import write_pause
            try:
                guard.enter_context(write_pause.unpaused(e, 'turn Edge Functions on'))
            except write_pause.PauseError as error:
                raise RuntimeError(str(error)) from None
        with guard:
            self.functions_switch(e, on, name)

    def functions_switch(self, e, on, name):
        if on:
            if not functions_on(e):
                placement, cpus = restart_placement(len(self.values['environments']), realtime_count(), functions_count() + 1)
                if not resource_policy.restart_fits(placement, cpus, available_memory_bytes(), owned_usage_bytes(), os.cpu_count() or 0):
                    raise AdmissionLimitError('Restart headroom unavailable')
            entry = self.functions_start(e)
        else:
            entry = None
            if inspect('container', name):
                lab.docker('rm', '-f', name)
            self.functions_network_remove(e)
        endpoints = published_endpoints()
        if entry:
            endpoints[e]['functions'] = entry
        else:
            endpoints[e].pop('functions', None)
        runtime_files.write_endpoints(endpoints, STATE)

    def functions_network_remove(self, e):
        """Take the environment's Auth and REST (and Storage, attached by an earlier version) off its
        Functions network, then remove it."""
        network = functions_network(e)
        item = inspect('network', network)
        if not item:
            return
        # A stopped member is not listed by `network inspect`, yet would fail to start once its
        # network is gone, so the environment's own services are named here as well.
        members = {entry['Name'] for entry in (item.get('Containers') or {}).values()}
        members |= {PREFIX+'-'+e+'-auth', PREFIX+'-'+e+'-rest', PREFIX+'-storage'}
        for member in sorted(members):
            lab.docker('network', 'disconnect', '--force', network, member, check=False)
        lab.docker('network', 'rm', network)

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
                placement, cpus = restart_placement(len(self.values['environments']), realtime_count() + 1, functions_count())
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
            self.sql(scheduled_jobs.limit_sql(e, current_database_limit(e, realtime=False)))
        endpoints = published_endpoints()
        if entry:
            endpoints[e]['realtime'] = entry
        else:
            endpoints[e].pop('realtime', None)
        runtime_files.write_endpoints(endpoints, STATE)

    def scheduler_password(self, e):
        """The scheduler login's password, generated once and kept with the environment's other credentials,
        so a cluster rebuild can recreate the login (lab/cluster_rebuild.py)."""
        v = self.values['environments'][e]
        if not scheduled_jobs.password_ok(v.get('cron')):
            v['cron'] = secrets.token_hex(32)
            runtime_files.write_runtime_secret(self.values, self.path.parent)
        return v['cron']

    def scheduled_jobs_turn(self, e, on):
        """Turn one environment's Scheduled jobs on or off: an operator action from the console, under the
        operation lock (docs/engineering/SCHEDULED-JOBS.md). What a tenant session can make wait (the schemas
        and grants in its database) runs in bounded transactions after the login's own sessions ended, so the
        lock is held for at most their bounds. Off closes the login, takes back what was granted to it and
        keeps the schemas and the jobs; on again picks them up."""
        effect_receipt.require_settled(STATE)
        if not re.fullmatch(r'e_[a-f0-9]{24}', e):
            raise RuntimeError('Invalid environment runtime identifier')
        if not inspect('container', DB):
            raise RuntimeError('Start the upstream runtime first')
        if e not in published_endpoints() or e not in self.values['environments']:
            raise RuntimeError('Scheduled jobs need a published environment')
        if source_fence.is_fenced(self.sql, e):
            raise RuntimeError('Environment database is fenced; explicit reconciliation required')
        import write_pause
        login = f'{e}_cron'
        if not on:
            self.sql(scheduled_jobs.close_sql(e))
            self.sql(scheduled_jobs.revoke_sql(e), e)
            self.sql(scheduled_jobs.limit_sql(e, current_database_limit(e, cron=False)))
            endpoints = published_endpoints()
            endpoints[e].pop('cron', None)
            runtime_files.write_endpoints(endpoints, STATE)
            return
        if write_pause.is_paused(e):
            raise OperationBusy('A write pause holds the environment')
        if self.sql(f"SELECT count(*) FROM pg_hba_file_rules WHERE '{login}' = ANY(user_name) AND error IS NULL;").stdout.strip() != '1':
            raise RuntimeError('Scheduled jobs access rule not published; restart Sbarbase once')
        if not cron_on(e):
            available, promised = (int(value) for value in self.sql(
                "SELECT current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int "
                "- current_setting('reserved_connections')::int, coalesce(sum(greatest(datconnlimit, 0)), 0) "
                "FROM pg_database WHERE datallowconn AND datname <> 'template1';").stdout.strip().split('|'))
            if promised + connection_budget.SCHEDULER_CONNECTIONS > available:
                raise AdmissionLimitError('Connection headroom unavailable')
        # Closed while its grants are written, so no session of an older turn holds what they need.
        self.sql(scheduled_jobs.close_sql(e))
        self.sql(scheduled_jobs.login_sql(e, self.scheduler_password(e)))
        self.sql(scheduled_jobs.shim_sql(e, login=True), e)
        self.sql(scheduled_jobs.limit_sql(e, current_database_limit(e, cron=True)))
        self.sql(scheduled_jobs.open_sql(e))
        endpoints = published_endpoints()
        endpoints[e]['cron'] = {'user': login}
        runtime_files.write_endpoints(endpoints, STATE)

    def scheduled_jobs_back(self, e):
        """At a start, the scheduler login as Scheduled jobs say: its password, limit and role settings
        written again (a job acting as it may have changed its own settings), the schemas and grants
        brought up to date (scheduled_jobs_repair), and the login opened unless a write pause holds the
        environment. Each statement is bounded and none waits for a tenant (SCHEDULED-JOBS.md)."""
        if not cron_on(e):
            return
        import write_pause
        try:
            self.sql(scheduled_jobs.close_sql(e))
            self.sql(scheduled_jobs.login_sql(e, self.scheduler_password(e)))
            self.scheduled_jobs_repair(e)
            if not write_pause.is_paused(e):
                self.sql(scheduled_jobs.open_sql(e))
        except Exception as error:
            # Never stops the start of this or any other environment: the login stays as it is (closed, if the
            # close ran), runs wait, and the next start or turn on tries again.
            print(f'Scheduled jobs of {e} could not be resumed ({type(error).__name__}); the next start tries again.', file=sys.stderr)

    def scheduled_jobs_repair(self, e):
        """The schemas and grants written again where Scheduled jobs are on: a restored database may be an
        archive taken while they were off (no grants to the login) or before they were first turned on (no
        schemas). Bounded; a failure is reported and never stops a start or a restore, and the next start
        tries again (the runner meanwhile retries and logs why it cannot read its tables)."""
        if not cron_on(e):
            return
        try:
            self.sql(scheduled_jobs.shim_sql(e, login=True), e)
        except Exception as error:
            print(f'Scheduled jobs of {e} could not be set up again ({type(error).__name__}); the next start tries again.', file=sys.stderr)

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
        runtime_files.update_management(endpoint, STATE)


def developer_login_sql(e, on):
    """Open or close the developer login. A session acting as that login (its own, Studio's, or a request
    running a function the login owns) can change the login's password or settings in a transaction it
    leaves open, and the role row would then hold this statement. It runs under the operation lock, so
    the environment's sessions holding role rows in an open transaction are ended first, in the same
    transaction, and the statement is bounded for the one that could start in between."""
    if not re.fullmatch(r'e_[a-f0-9]{24}', e):
        raise RuntimeError('Invalid environment runtime identifier')
    role = f'{e}_developer'
    ended = ("SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(l.pid)) FROM pg_catalog.pg_locks l "
             "JOIN pg_catalog.pg_stat_activity a ON a.pid = l.pid JOIN pg_catalog.pg_roles r ON r.rolname = a.usename "
             "WHERE l.locktype = 'relation' AND l.relation = 'pg_catalog.pg_authid'::pg_catalog.regclass "
             f"AND l.granted AND l.mode <> 'AccessShareLock' AND a.datname = '{e}' AND NOT r.rolsuper "
             "AND l.pid <> pg_catalog.pg_backend_pid(); ")
    change = (f'ALTER ROLE {role} LOGIN; ' if on else
              f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{role}') THEN "
              f"ALTER ROLE {role} NOLOGIN; END IF; END $$; ")
    return data_api.SAFE + REST_PIN_BOUNDS + 'BEGIN; ' + ended + change + 'COMMIT; '


def database_lock(e):
    """This environment's database access lock, taken without waiting (BlockingIOError when it is held). A
    console change holds it while it writes the developer login's grants, outside the operation lock, and
    again with the operation lock to open the login; write_pause.py resume and turning access off try it.
    Nothing waits for it while holding the operation lock."""
    if not re.fullmatch(r'e_[a-f0-9]{24}', e):
        raise RuntimeError('Invalid environment runtime identifier')
    handle = (STATE/f'database-{e}.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle


def operation_lock(block=False):
    """The installation operation lock: tried once (BlockingIOError when it is held), or waited for."""
    handle = (STATE/'operation.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX if block else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle


def restart_studio(e, catalog=None):
    """Have the supervisor start the environment's running Studio again (lab/dev.py schedule_studios):
    `failed` with no reason is the state it starts again when Studio is wanted and stops when it is
    not, so a restart goes through its own recorded child, one per environment, like a Stop does."""
    import sqlite3
    with contextlib.closing(sqlite3.connect(catalog or STATE/'control.sqlite', timeout=5)) as database, database:
        changed = database.execute("UPDATE studio_sessions SET state='failed', failure=NULL, updated_at=? "
                                   "WHERE runtime=? AND state='running'", (int(time.time()*1000), e)).rowcount
    return bool(changed)


def database_command(e, on, runtime=None):
    """The console's change of direct database access. Turning it on writes grants on `public`, which a
    session acting through the API can make wait (a no-op GRANT left open), each within its bound: under
    the installation's operation lock that wait would hold every other environment's changes and starts
    too, and under the write pause lock every Studio start. So the checks run under the operation lock,
    the role, password and grants under this environment's database access lock alone, and the login is
    opened and published under the operation lock again, waited for, and the pause guard, tried. A pause
    taken in between, or either lock found held, is OperationBusy: nothing was opened, and the next try
    redoes the grants, which change nothing the second time."""
    import write_pause
    runtime = runtime or Runtime()
    if not on:
        with operation_lock():
            runtime.database_turn(e, on=False)
        return
    with database_lock(e):
        with operation_lock():
            runtime.database_preconditions(e)
            if write_pause.is_paused(e):
                raise OperationBusy('A write pause holds the environment')
            password = runtime.developer_ready(e)
            runtime.developer_admission(e)
        written = runtime.developer_prepare(e, password)
    # The environment's lock is let go while the operation lock is waited for and tried again inside, as
    # data_api_command does: write_pause.py resume holds the operation lock and tries it.
    with operation_lock(block=True), database_lock(e), contextlib.ExitStack() as guard:
        try:
            # Tried: a Studio start holds the pause lock while its own grants wait for a tenant. Held until the
            # login is open, so a pause starting meanwhile waits and then closes it.
            guard.enter_context(write_pause.unpaused(e, 'turn database access on', wait=0))
        except write_pause.PauseError:
            raise OperationBusy('A write pause or a Studio start holds the pause lock') from None
        runtime.database_preconditions(e)
        runtime.developer_open(e, written)


def data_api_command(e, runtime=None):
    """The console's Data API change. A tenant session can make its SQL wait (a transaction left open on
    the REST login's role settings or on the platform's schema, or the schema lock held), each within its
    bound; under the installation's operation lock that wait would hold every other environment's
    changes and starts too. So the guard, the pin and the write run under this environment's own lock,
    held throughout, and PostgREST is told right after the commit, by signalling its own container.
    The operation lock is held for the checks and then for recording the outcome, which reads the
    settings back and waits for nothing a tenant holds. A start that wrote other settings in between
    makes it ask again (DataApiBusy); either lock held at the start is exit 75."""
    with data_api.environment_lock(e):
        runtime = runtime or Runtime()
        with operation_lock():
            runtime.data_api_checks(e)
        outcome = runtime.data_api_write(e)
        # The guard and Studio read the new settings from now on, so PostgREST is told at once rather
        # than after waiting for the operation lock: until then it would serve a schema the guard no
        # longer protects. A start or rotation running meanwhile starts REST itself, which reads them.
        told = runtime.tell_rest(e) if outcome[3] else None
    # Recorded under the operation lock, waited for: another operation (a provisioning, a start) holds it
    # only for as long as it runs. The environment's lock is let go meanwhile and tried again inside,
    # without waiting: held across the wait, a write pause's restore, which holds the operation lock when
    # it reaches backup.start_services, would wait for it (found in review). Nothing waits for an
    # environment's lock while holding the operation lock. A restore that wrote in between is what the
    # read-back in data_api_finish sees, and the change is asked again.
    try:
        with operation_lock(block=True), data_api.environment_lock(e):
            runtime.data_api_finish(e, outcome, verify=True, told=told)
    except (DataApiBusy, DataApiRefusal):
        # Asked again, or refused with its reason: nothing this change wrote is left unrecorded.
        raise
    except BlockingIOError:
        # A restore of this environment kept its lock: what it wrote is what the next try reads back.
        raise DataApiBusy('An in-place restore holds the Data API settings') from None
    except Exception:
        # Written and told: "keeps serving its previous settings" would be false now.
        raise DataApiUnrecorded('The Data API settings were written but not recorded') from None
    # Studio reads the exposed schemas once, when it starts. The settings are served already, so a
    # Studio that fails to start again does not undo them; the supervisor records how it went.
    try:
        if not restart_studio(e) and studio_running(e):
            print('Studio could not start again; open it once more to see the new schemas.', file=sys.stderr)
    except Exception:
        print('Studio could not start again; open it once more to see the new schemas.', file=sys.stderr)


# The database stops last, after a CHECKPOINT, so its own shutdown checkpoint has little left to
# write and fits STOP_DATABASE_SECONDS; Docker's default 10 s could otherwise end it with SIGKILL
# and leave crash recovery to the next start. Both, with the services' 10 s, stay inside the
# supervisor's 90 s stop stage (lab/dev.py STOP_STAGE_SECONDS). A moved environment's recovery target
# stops first and can take longer than that stage alone (lab/target_runtime.py), as before. A database container this module creates from now on also carries the same stop
# timeout, so Docker's own stop at a host shutdown gives it the same time; one created earlier, or by
# a generation change (lab/hba_migration.py), keeps Docker's 10 s there.
CHECKPOINT_SECONDS = 15
STOP_DATABASE_SECONDS = 40


def checkpoint():
    """A CHECKPOINT before the database stops, bounded, so its own shutdown checkpoint is short."""
    try:
        subprocess.run(docker_profile.docker_command('exec', DB, 'psql', '-X', '-U', 'supabase_admin', '-d', 'postgres', '-qAt', '-c', 'CHECKPOINT;'),
                       capture_output=True, text=True, timeout=CHECKPOINT_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def retire(name):
    """Removes a container that is replaced, stopping it first when it runs (one a planned upgrade
    restart kept), so it ends as a stop would end it rather than with SIGKILL."""
    item = inspect('container', name)
    if not item:
        return
    if item.get('State', {}).get('Running'):
        lab.docker('stop', name)
    lab.docker('rm', '-f', name)


def stop():
    # A stopped runtime has nothing a planned upgrade restart kept (lab/kept_runtime.py).
    kept_runtime.discard(STATE)
    names = lab.docker('ps', '-q', '--no-trunc', '--filter', 'label=io.sbarbase.owner='+OWNER).stdout.split()
    if not names:
        return
    database = lab.docker('ps', '-q', '--no-trunc', '--filter', 'label=io.sbarbase.owner='+OWNER, '--filter', 'name=^/'+DB+'$').stdout.split()
    services = [name for name in names if name not in database]
    if services:
        lab.docker('stop', *services)
    if database:
        checkpoint()
        lab.docker('stop', '-t', str(STOP_DATABASE_SECONDS), *database)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['up', 'stop', 'provision', 'mail', 'auth', 'realtime', 'functions', 'database', 'signing', 'data-api', 'cron'])
    parser.add_argument('environment', nargs='?')
    parser.add_argument('--off', action='store_true')
    args = parser.parse_args()
    if args.command != 'stop':
        docker_profile.require_or_exit()
    lab.private_dir(STATE)
    try:
        if args.command=='up':
            inherited=os.environ.get('SBARBASE_WORKER_FD')
            with hba_startup.acquire(STATE,worker_fd=int(inherited) if inherited else None) as startup:
                Runtime(startup=startup).start()
        elif args.command=='data-api':
            if not args.environment:raise SystemExit('The data-api command needs an environment')
            data_api_command(args.environment)
        elif args.command=='database':
            if not args.environment:raise SystemExit('The database command needs an environment')
            database_command(args.environment, on=not args.off)
        else:
            with (STATE/'operation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command=='stop':stop()
                elif args.command=='realtime':
                    if not args.environment:raise SystemExit('The realtime command needs an environment')
                    Runtime().realtime_turn(args.environment, on=not args.off)
                elif args.command=='signing':
                    if not args.environment:raise SystemExit('The signing command needs an environment')
                    Runtime().rotate_signing(args.environment)
                    # A running Studio holds keys signed with the old secret; it starts again with new ones,
                    # through the supervisor and after this lock is released, since its grants can wait for a tenant.
                    if not restart_studio(args.environment) and studio_running(args.environment):
                        print('Studio could not start again; open it once more to use the new keys.', file=sys.stderr)
                elif args.command=='cron':
                    if not args.environment:raise SystemExit('The cron command needs an environment')
                    Runtime().scheduled_jobs_turn(args.environment, on=not args.off)
                elif args.command=='functions':
                    if not args.environment:raise SystemExit('The functions command needs an environment')
                    Runtime().functions_turn(args.environment, on=not args.off)
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
    except DataApiRefusal as refusal:
        # Only schema names the operator chose; lab/data_api.py shows this line in the console.
        print(str(refusal), file=sys.stderr)
        raise SystemExit(data_api.REFUSED)
    except DataApiBusy:
        # Written, but a start or restore wrote other settings before this change could record it: ask again.
        raise SystemExit(75)
    except DataApiUnrecorded:
        raise SystemExit(data_api.UNRECORDED)
    except AdmissionLimitError:
        # Stable local worker protocol. Never classify failures from raw stderr.
        print('Local runtime admission limit reached.')
        raise SystemExit(75)
    except (BlockingIOError, OperationBusy):
        # Another runtime operation, or another change of this environment, took a lock between the
        # supervisor's check and this command. For a console change that is "busy, ask again"
        # (lab/data_api.py, lab/realtime.py, lab/auth_settings.py apply), never a failed change.
        if args.command=='data-api':raise SystemExit(75)
        if args.command in CONSOLE_CHANGES:raise SystemExit(BUSY)
        raise SystemExit('Durable runtime operation failed; retained state is available for reconciliation.')
    except Exception:
        # Secrets, SQL and HTTP response bodies must never reach console output.
        raise SystemExit('Durable runtime operation failed; retained state is available for reconciliation.')
