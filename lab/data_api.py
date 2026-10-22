"""Per environment Data API settings: which schemas PostgREST exposes, its extra search path and
its row limit (docs/engineering/DATA-API-SCHEMAS.md).

The console and `PATCH /v1/projects/{ref}/postgrest` save the desired settings in the catalog's
`data_api_settings` row and mark it pending. The supervisor then runs

    /usr/bin/python3 lab/data_api.py apply <environment runtime id>

which calls `durable_runtime.py data-api <runtime>`. That writes the settings into the
environment's own database, as the function `sbarbase_rest.settings()`, and tells PostgREST to
read its configuration and schema again. Each REST container names `sbarbase_rest.pre_config`
as its db-pre-config function (CONTAINER_ENV, added by Runtime.activate_services), which applies
them, so a change needs no restart. They are never role settings: those live in `pg_db_role_setting`, a catalog the
whole cluster shares, and tenant code can act as the REST login that would have to read it. Every
start and resume writes them again from the catalog, so a restore or a rebuilt cluster serves what
the catalog says.

The row keeps the desired settings and, apart from them, the last settings applied. Only applied
settings are ever served: a change the database refused stays in the row as failed and is not
served after a restart either. An environment without applied settings serves what pre_config
supplies: `public` only.

The rules here are mirrored in src/control/data-api.ts. The database adds what only it can know:
whether each schema exists (durable_runtime.py data-api).
"""
import argparse
import fcntl
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / '.lab' / 'upstream' / 'control.sqlite'
OPERATION_LOCK = ROOT / '.lab' / 'upstream' / 'operation.lock'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
# Unquoted lowercase PostgreSQL identifiers only. PostgREST splits db-schemas on commas and the
# value travels through SQL, so a name that would need quoting is refused rather than escaped.
IDENTIFIER = re.compile(r'[a-z_][a-z0-9_]{0,62}')
MAX_SCHEMAS = 16
MAX_ROWS = 1_000_000
DEFAULT = {'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None}
# An empty schema the platform owns, served when none of the exposed schemas exists any more, so
# PostgREST answers 404 rather than 503 and nothing the owner left out (such as `public`) is published.
EMPTY = 'sbarbase_empty'
# The start of the failure a start records when it leaves a missing schema out.
LEFT_OUT = 'Not served because missing from the database'
# Schemas that belong to the platform, to Supabase's services or to PostgreSQL. Exposing one would
# publish Auth's users, Storage's metadata, Vault's secrets or the scheduler's queue, so none may be
# exposed whatever the grants say. This is Studio's own list of internal schemas (apps/studio
# hooks/useProtectedSchemas.ts) plus Sbarbase's, without the `*_public` schemas Studio allows.
INTERNAL = frozenset(('auth', 'storage', 'extensions', 'realtime', '_realtime', 'vault', 'pgsodium', 'pgsodium_masks',
                      'cron', 'net', 'graphql', 'pgbouncer', '_analytics', '_supavisor', 'information_schema',
                      'pgtle', 'pgmq', 'etl', 'supabase_functions', 'supabase_migrations', 'pg_catalog', 'pg_toast'))
# Studio lets a project expose it, and Supabase's config.toml lists it by default, but GraphQL is not
# served here: the schema never exists in an environment and its name is reserved.
GRAPHQL = 'graphql_public'
# The search path may also name `extensions`, where Supabase installs pgcrypto and uuid-ossp.
SEARCH_PATH_ALLOWED = frozenset(('extensions', 'sbarbase_empty'))
FIELDS = ('schemas', 'extra_search_path', 'max_rows')


class SettingsError(ValueError):
    """A message that names the field and the offending schema name, never anything secret."""


def platform_name(name):
    return name in INTERNAL or name.startswith(('pg_', 'sbarbase', 'supabase'))


def names(value, field, *, allowed=frozenset()):
    if not isinstance(value, list) or not value or len(value) > MAX_SCHEMAS:
        raise SettingsError(f'{field} needs between 1 and {MAX_SCHEMAS} schema names')
    seen = []
    for item in value:
        if not isinstance(item, str) or not IDENTIFIER.fullmatch(item):
            raise SettingsError(f'{field}: a schema name uses lowercase letters, digits and underscores, and starts with a letter or underscore')
        if item == GRAPHQL:
            raise SettingsError(f'{field}: GraphQL is not available on Sbarbase, so {GRAPHQL} cannot be exposed. '
                                'Remove it (Supabase\'s config.toml lists it by default).')
        if platform_name(item) and item not in allowed:
            raise SettingsError(f'{field}: the schema {item} belongs to the platform and cannot be used here')
        if item in seen:
            raise SettingsError(f'{field}: the schema {item} is listed twice')
        seen.append(item)
    return seen


def validate(settings):
    """The settings in their stored shape, or SettingsError naming the first bad field."""
    if not isinstance(settings, dict) or set(settings) - set(FIELDS):
        raise SettingsError('settings')
    rows = settings.get('max_rows')
    if rows is not None and (type(rows) is not int or not 1 <= rows <= MAX_ROWS):
        raise SettingsError(f'max_rows is a whole number from 1 to {MAX_ROWS}, or empty for no limit')
    schemas = settings.get('schemas')
    # No schema at all turns the Data API off: PostgREST then serves the empty platform schema.
    off = isinstance(schemas, list) and (schemas == [] or schemas == [EMPTY])
    return {'schemas': [EMPTY] if off else names(schemas, 'schemas'),
            'extra_search_path': names(settings.get('extra_search_path', DEFAULT['extra_search_path']), 'extra_search_path',
                                       allowed=SEARCH_PATH_ALLOWED),
            'max_rows': rows}


def from_row(schemas, search_path, max_rows):
    return validate({'schemas': [item for item in schemas.split(',') if item],
                     'extra_search_path': [item for item in search_path.split(',') if item], 'max_rows': max_rows})


def row(e, catalog=None, attempts=3):
    """(desired, applied, state, failure) of one environment; None for each part it does not have.
    A catalog that is busy past the retries raises rather than reading as "no settings", which
    would reset PostgREST to its defaults and could publish `public`."""
    catalog = catalog or CATALOG
    if not RUNTIME.fullmatch(e) or not Path(catalog).exists():
        return None, None, None, None
    for attempt in range(attempts):
        try:
            with closing(sqlite3.connect(f'file:{catalog}?mode=ro', uri=True, timeout=5)) as database:
                found = database.execute('SELECT schemas, extra_search_path, max_rows, applied_schemas, applied_extra_search_path, '
                                         'applied_max_rows, state, failure FROM data_api_settings WHERE runtime=?', (e,)).fetchone()
            break
        except sqlite3.OperationalError as error:
            # A catalog this release has not opened yet has no such table.
            if 'no such table' in str(error):
                return None, None, None, None
            if attempt == attempts - 1:
                raise RuntimeError('The catalog could not be read for the Data API settings') from None
            time.sleep(1)
    if not found:
        return None, None, None, None
    applied = from_row(found[3], found[4] or 'public', found[5]) if found[3] is not None else None
    return from_row(*found[:3]), applied, found[6], found[7]


def load(e, catalog=None):
    """The saved, desired settings of one environment, or None when it has none."""
    return row(e, catalog)[0]


def load_applied(e, catalog=None):
    """The settings last applied, or None when none ever were (PostgREST's defaults)."""
    return row(e, catalog)[1]


def effective(settings, existing):
    """(settings PostgREST may serve, schemas left out). PostgREST answers every request with 503
    while one exposed schema is missing (PGRST002), so a schema that is gone is left out of what it
    serves and reported, rather than taking the environment's whole API down. The first schema
    stays first when it exists. When none is left, the platform's empty schema stands in: falling
    back to `public` would publish a schema the owner chose not to expose."""
    present = [name for name in settings['schemas'] if name in existing]
    missing = [name for name in settings['schemas'] if name not in existing]
    path = [name for name in settings['extra_search_path'] if name in existing]
    # With the Data API off nothing is served, so a missing search path schema leaves nothing out.
    if settings['schemas'] != [EMPTY]:
        missing += [name for name in settings['extra_search_path'] if name not in existing and name not in missing]
    return {'schemas': present or [EMPTY], 'extra_search_path': path or [EMPTY],
            'max_rows': settings['max_rows']}, missing


# The schema in each environment's database that holds what its PostgREST serves, and the
# db-pre-config function every REST container names (CONTAINER_ENV below).
REST_SCHEMA = 'sbarbase_rest'
PRE_CONFIG = REST_SCHEMA + '.pre_config'
# Every key PostgREST v14.18 reads from the database (Config/Database.hs dbSettingsNames), and the
# three this platform writes itself. Checked by hand against the strings of the v14.18 binary (all 23
# present, no others); repeat it when PostgREST is upgraded (docs/engineering/UPSTREAM-UPDATE-POLICY.md).
PGRST_KEYS = ('db_aggregates_enabled', 'db_anon_role', 'db_pre_config', 'db_extra_search_path', 'db_max_rows',
              'db_plan_enabled', 'db_pre_request', 'db_prepared_statements', 'db_root_spec', 'db_schemas', 'db_tx_end',
              'db_hoisted_tx_settings', 'jwt_aud', 'jwt_role_claim_key', 'jwt_secret', 'jwt_secret_is_base64',
              'jwt_cache_max_lifetime', 'openapi_mode', 'openapi_security_active', 'openapi_server_proxy_uri',
              'server_cors_allowed_origins', 'server_trace_header', 'server_timing_enabled')
MANAGED_KEYS = ('db_schemas', 'db_extra_search_path', 'db_max_rows')
# What every REST container is started with. The function is named for the settings; the schemas are
# the empty one, so that a load pre_config refuses (and PostgREST then answers from the container's
# own configuration, not from what it had) serves nothing instead of `public`.
CONTAINER_ENV = {'PGRST_DB_PRE_CONFIG': 'sbarbase_rest.pre_config', 'PGRST_DB_SCHEMAS': EMPTY,
                 'PGRST_DB_EXTRA_SEARCH_PATH': EMPTY}
def carries_container_env(env):
    """True when a container's environment (a list of KEY=value strings, as docker inspect shows it)
    holds everything CONTAINER_ENV says. A REST container created by an earlier release names neither
    the settings function nor the empty schema, so it ignores the settings and serves `public`."""
    present = set(env or ())
    return all(f'{key}={value}' in present for key, value in CONTAINER_ENV.items())


PGRST_KEYS_SQL = ', '.join(f"'pgrst.{key}'" for key in PGRST_KEYS)
MANAGED_KEYS_SQL = ', '.join(f"'pgrst.{key}'" for key in MANAGED_KEYS)
# The two keys pre_config always sets itself, from the settings or to the default.
SET_ABOVE_SQL = "'pgrst.db_schemas', 'pgrst.db_extra_search_path'"


def settings_function(pairs):
    """`sbarbase_rest.settings()`, answering the pgrst settings PostgREST applies: no rows for its
    container's defaults. Every value has passed `validate`, so it holds identifiers, commas and
    digits only and is written as a literal."""
    values = ', '.join(f"('{key}', '{value}')" for key, value in pairs) or "(NULL, NULL)"
    where = '' if pairs else ' WHERE false'
    return (f'CREATE OR REPLACE FUNCTION {REST_SCHEMA}.settings() RETURNS TABLE(key text, value text) '
            'LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS '
            f'$settings$ SELECT * FROM (VALUES {values}) item(key, value){where} $settings$;')


def settings_sql(e, settings):
    """The settings this environment's PostgREST applies, written in its own database only."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    settings = validate(settings)
    pairs = [('pgrst.db_schemas', ','.join(settings['schemas'])),
             ('pgrst.db_extra_search_path', ','.join(settings['extra_search_path']))]
    if settings['max_rows'] is not None:
        pairs.append(('pgrst.db_max_rows', str(settings['max_rows'])))
    return settings_function(pairs)


def reset_sql(e):
    """Back to the container's own settings, for an environment whose catalog row is gone."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    return SAFE + "SET lock_timeout = '5s'; SET statement_timeout = '30s'; " + settings_function([])


# What PostgREST listens to for a reload. The platform does not send it: the notification queue is
# shared by the whole cluster and any login can fill it, which would stop another environment's
# settings write. The platform signals the environment's own REST container instead
# (durable_runtime.py tell_rest: SIGUSR1). Kept
# here for the live check, which stands in for a tenant's `NOTIFY pgrst`.
RELOAD_SQL = "NOTIFY pgrst, 'reload config'; NOTIFY pgrst, 'reload schema';"
# Every statement here runs as the superuser in an environment's database, where the developer login
# may create functions and operators in `public`. With the superuser's own search path, a tenant's
# `public.unnest(text[])` matches better than `pg_catalog.unnest(anyarray)` and runs as the superuser
# (found in review: it made the developer login a superuser of the cluster). So each statement
# starts from pg_catalog alone, and calls name pg_catalog too.
SAFE = 'SET search_path TO pg_catalog, pg_temp; '
# A transaction level advisory lock in the environment's database, held by the platform while it
# checks and writes the settings and by the guard while it checks a schema rename or drop. Either the
# drop waits and then sees the schema exposed, or the platform waits and then sees it gone: a schema
# cannot disappear between the check and the write.
LOCK_KEY = 7331002107
# The error a write raises when a schema it names disappeared before the lock was taken.
RACE = 'sbarbase_data_api_schema_gone'
# The error a write raises when a schema rename or drop in this environment holds the lock. The
# platform only tries the lock: a tenant session holding it must never make the platform wait
# while it holds the installation's operation lock.
BUSY = 'sbarbase_data_api_busy'


def release_sql():
    """End the sessions of this database that are not the superuser's and hold or wait for the
    platform's lock: with only the holder ended, the next waiting session would get it."""
    return ('SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(l.pid)) FROM pg_catalog.pg_locks l '
            'JOIN pg_catalog.pg_stat_activity a ON a.pid = l.pid '
            "WHERE l.locktype = 'advisory' AND l.database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()) "
            f'AND l.classid = {LOCK_KEY >> 32} AND l.objid = {LOCK_KEY & 0xFFFFFFFF} AND l.objsubid = 1 '
            'AND NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = a.usename);')


def adoptable(output):
    """The settings a moved database brings (the output of serving_sql), when they pass the rules."""
    if not any(line.startswith('pgrst.') for line in output.splitlines()):
        return None
    try:
        return validate(serving(output))
    except SettingsError:
        return None


def adopt(e, settings, catalog=None, actor='system:restore'):
    """A catalog row for settings a moved database brought: saved, applied and served alike. True
    when written; False when it cannot be kept in the catalog (a row the console wrote meanwhile,
    which is never replaced, or no catalog table yet): the database then keeps what it brought.
    A start also records the defaults this way when it cannot serve them (`public` is gone), so
    the empty schema it serves meanwhile is never later taken for settings a database brought."""
    catalog = catalog or CATALOG
    values = (','.join(settings['schemas']), ','.join(settings['extra_search_path']), settings['max_rows'])
    if not Path(catalog).exists():
        return False
    try:
        with closing(sqlite3.connect(catalog, timeout=15)) as database, database:
            cursor = database.execute('INSERT OR IGNORE INTO data_api_settings(runtime, schemas, extra_search_path, max_rows, '
                                      'applied_schemas, applied_extra_search_path, applied_max_rows, served_schemas, '
                                      'served_extra_search_path, served_max_rows, state, failure, actor, updated_at) '
                                      "VALUES (?,?,?,?,?,?,?,?,?,?,'applied',NULL,?,?)",
                                      (e, *values, *values, *values, actor, int(time.time() * 1000)))
            return cursor.rowcount == 1
    except sqlite3.Error as error:
        # A catalog this release has not opened yet: the database keeps what it brought.
        if 'no such table' in str(error):
            return False
        # Never read as "nothing to keep": that would reset PostgREST to `public`.
        raise RuntimeError('The catalog could not record the Data API settings the database brought') from None


def record_report(e, report, catalog=None):
    """The last start's own report (a schema left out, settings not written), or None; replaced every time."""
    catalog = catalog or CATALOG
    try:
        with closing(sqlite3.connect(catalog, timeout=5)) as database, database:
            database.execute('UPDATE data_api_settings SET start_report=? WHERE runtime=?', (report, e))
    except sqlite3.Error:
        pass


def not_told_text():
    """The settings are written and recorded as applied, but the environment's PostgREST could not be signalled."""
    return ('The Data API settings are saved and written, but PostgREST could not be told to reload them. '
            'It picks them up at its next reload or restart.')


def unwritten_text(served):
    """A start or restore that could not write the settings says what PostgREST serves instead."""
    shown = ', '.join(name for name in served['schemas'] if name != EMPTY) or 'no schema'
    return ('The Data API settings last applied could not be written when the environment started. '
            f'The database serves {shown} for now. Save the settings again.')


def write_sql(e, served, claim=False, reset=False):
    """One transaction: take the lock, check again that every schema it names exists and write the
    settings; PostgREST is told afterwards (Runtime.tell_rest). A schema gone by then rolls everything back with RACE.
    With `reset` it writes no settings, so the container's own apply, with `served` the defaults they mean.

    Without `claim` the lock is only tried (BUSY when held). With `claim`, for a start or restore,
    the sessions holding or waiting for it are ended first and the write waits for it, up to 3 s."""
    asked = sorted(set(served['schemas']) | set(served['extra_search_path']))
    if any(not IDENTIFIER.fullmatch(name) for name in asked):
        raise SettingsError('schemas')
    listed = ', '.join(f"'{name}'" for name in asked)
    lock = (f"SET LOCAL lock_timeout = '3s'; {release_sql()} SELECT pg_catalog.pg_advisory_xact_lock({LOCK_KEY}); "
            if claim else
            "SET LOCAL lock_timeout = '5s'; "
            f'DO $lock$ BEGIN IF NOT pg_catalog.pg_try_advisory_xact_lock({LOCK_KEY}) '
            f"THEN RAISE EXCEPTION '{BUSY}'; END IF; END $lock$; ")
    return ('BEGIN; SET LOCAL search_path TO pg_catalog, pg_temp; ' + lock +
            f'DO $check$ BEGIN IF EXISTS (SELECT 1 FROM pg_catalog.unnest(ARRAY[{listed}]::text[]) name '
            'WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = name)) '
            f"THEN RAISE EXCEPTION '{RACE}'; END IF; END $check$; "
            + (settings_function([]) if reset else settings_sql(e, served)) + ' COMMIT;')
def present_sql(settings):
    """Which of the settings' schemas exist, decided by the database: it answers only names it was
    asked about, and only exact matches. Reading every schema name and splitting the output instead
    would let a tenant's schema named "x" + newline + "y" make `y` look present, and PostgREST then
    answers every request with 503."""
    asked = sorted(set(settings['schemas']) | set(settings['extra_search_path']))
    if not asked or any(not IDENTIFIER.fullmatch(name) for name in asked):
        raise SettingsError('schemas')
    listed = ', '.join(f"'{name}'" for name in asked)
    return (SAFE + f'SELECT name FROM pg_catalog.unnest(ARRAY[{listed}]::text[]) name '
            'WHERE EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = name) ORDER BY 1;')


def existing(output):
    """The names present_sql answered: each one of the names it asked about."""
    return {line for line in output.splitlines() if IDENTIFIER.fullmatch(line)}


def serving_sql(e):
    """`key=value` lines of the pgrst settings this environment's PostgREST applies."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    return SAFE + f"SELECT key || '=' || value FROM {REST_SCHEMA}.settings() WHERE key IN ({MANAGED_KEYS_SQL}) ORDER BY 1;"


def serving(output):
    """What PostgREST serves, from the output of serving_sql: the defaults where nothing is set."""
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    schemas = [item for item in values.get('pgrst.db_schemas', '').split(',') if item]
    path = [item for item in values.get('pgrst.db_extra_search_path', '').split(',') if item]
    rows = values.get('pgrst.db_max_rows')
    return {'schemas': schemas or DEFAULT['schemas'], 'extra_search_path': path or DEFAULT['extra_search_path'],
            'max_rows': int(rows) if rows and rows.isdigit() else None}


# The platform's schemas and the functions in them run as the superuser (the guards, serving_sql, the
# start's reads of settings()), so a non-superuser owner of any of them would run code as the superuser.
OWNED_BY_OTHERS_SQL = (
    "EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner "
    "WHERE n.nspname IN ('sbarbase_rest', 'sbarbase_guard', 'sbarbase_empty') AND NOT r.rolsuper) "
    "OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
    "JOIN pg_catalog.pg_roles r ON r.oid = p.proowner "
    "WHERE n.nspname IN ('sbarbase_rest', 'sbarbase_guard') AND NOT r.rolsuper)")


def guard_sql(e):
    """An event trigger in the environment's database: PostgREST answers every request with 503 once
    an exposed schema is gone, so renaming or dropping one is refused until it is no longer exposed.
    With no setting, `public` is what PostgREST exposes, so `public` is protected. The superuser
    owns the trigger, and the platform's own work (a restore) runs as the superuser and passes."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    return SAFE + "SET lock_timeout = '10s'; SET statement_timeout = '60s'; " + f"""DO $owner$ BEGIN
  IF {OWNED_BY_OTHERS_SQL} THEN
    RAISE EXCEPTION 'The schemas sbarbase_rest, sbarbase_guard and sbarbase_empty, and the functions in them, must belong to a superuser' USING ERRCODE = '42501';
  END IF;
END $owner$;
CREATE SCHEMA IF NOT EXISTS sbarbase_guard;
REVOKE ALL ON SCHEMA sbarbase_guard FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS {EMPTY};
REVOKE ALL ON SCHEMA {EMPTY} FROM PUBLIC;
-- What this environment's PostgREST serves, kept in its own database (docs/engineering/
-- DATA-API-SCHEMAS.md). Only its REST login, which runs the db-pre-config function, may use them.
CREATE SCHEMA IF NOT EXISTS {REST_SCHEMA};
REVOKE ALL ON SCHEMA {REST_SCHEMA} FROM PUBLIC;
DO $$ BEGIN
  IF pg_catalog.to_regprocedure('{REST_SCHEMA}.settings()') IS NULL THEN
    EXECUTE $create${settings_function([])}$create$;
  END IF;
END $$;
-- No SET clause on purpose: the caller's own search path is what has to be checked, because the
-- statement PostgREST loads its configuration with resolves its unqualified names through it.
-- Only the three keys the platform manages are taken from settings(): a settings() that an archive brought
-- may answer more, and those rows are ignored (a key the session carries, below, is what is refused).
-- Every type is named in pg_catalog and no record variable is used: a temporary table in the
-- caller's session named like a type would otherwise take the place of it (found in review), and
-- coercing a row into a tenant's type runs the tenant's functions between the set_config calls.
CREATE OR REPLACE FUNCTION {PRE_CONFIG}() RETURNS void
  LANGUAGE plpgsql AS $pre$
DECLARE k pg_catalog.text; v pg_catalog.text; name pg_catalog.text; given pg_catalog.text[];
BEGIN
  IF pg_catalog.current_setting('search_path') OPERATOR(pg_catalog.<>) 'pg_catalog' THEN
    RAISE EXCEPTION 'The session search path is not pg_catalog before the Data API settings were read' USING ERRCODE = '42501';
  END IF;
  SELECT pg_catalog.array_agg(s.key) INTO given FROM {REST_SCHEMA}.settings() s WHERE s.key IN ({MANAGED_KEYS_SQL});
  FOR k, v IN SELECT s.key, s.value FROM {REST_SCHEMA}.settings() s WHERE s.key IN ({MANAGED_KEYS_SQL}) LOOP
    PERFORM pg_catalog.set_config(k, v, true);
  END LOOP;
  -- An environment with no settings serves what Supabase serves by default.
  IF NOT ('pgrst.db_schemas' OPERATOR(pg_catalog.=) ANY(coalesce(given, ARRAY[]::pg_catalog.text[]))) THEN
    PERFORM pg_catalog.set_config('pgrst.db_schemas', 'public', true);
  END IF;
  IF NOT ('pgrst.db_extra_search_path' OPERATOR(pg_catalog.=) ANY(coalesce(given, ARRAY[]::pg_catalog.text[]))) THEN
    PERFORM pg_catalog.set_config('pgrst.db_extra_search_path', 'public', true);
  END IF;
  -- PostgREST reads every key below with current_setting(), so a value left in a pooled
  -- connection's session would become configuration at the next reload. The three the platform
  -- manages may be empty (what a connection keeps once a local value ended); anything else set
  -- at all stops the load. A refused load leaves PostgREST either on what it had or, at startup and in
  -- some reloads, on its container's own configuration, which names the empty schema (CONTAINER_ENV):
  -- both were observed, and neither serves anything the owner did not expose.
  FOREACH name IN ARRAY ARRAY[{PGRST_KEYS_SQL}] LOOP
    IF name OPERATOR(pg_catalog.=) ANY(coalesce(given, ARRAY[]::pg_catalog.text[])) THEN CONTINUE; END IF;
    IF name IN ({SET_ABOVE_SQL}) THEN CONTINUE; END IF;
    IF pg_catalog.current_setting(name, true) IS NOT NULL
       AND (pg_catalog.current_setting(name, true) OPERATOR(pg_catalog.<>) '' OR name OPERATOR(pg_catalog.<>) 'pgrst.db_max_rows') THEN
      RAISE EXCEPTION 'The session sets % before the Data API settings were read', name USING ERRCODE = '42501';
    END IF;
  END LOOP;
  -- The values now in force are the ones the platform wrote, whatever ran in between.
  FOR k, v IN SELECT s.key, s.value FROM {REST_SCHEMA}.settings() s WHERE s.key IN ({MANAGED_KEYS_SQL}) LOOP
    IF pg_catalog.current_setting(k, true) IS DISTINCT FROM v THEN
      RAISE EXCEPTION 'The session sets % before the Data API settings were read', k USING ERRCODE = '42501';
    END IF;
  END LOOP;
END $pre$;
REVOKE ALL ON FUNCTION {REST_SCHEMA}.settings() FROM PUBLIC;
REVOKE ALL ON FUNCTION {PRE_CONFIG}() FROM PUBLIC;
DO $$ BEGIN
  IF pg_catalog.to_regrole('{e}_rest') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA {REST_SCHEMA} TO {e}_rest;
    GRANT EXECUTE ON FUNCTION {REST_SCHEMA}.settings(), {PRE_CONFIG}() TO {e}_rest;
  END IF;
END $$;
-- pg_temp last: left out, the session's own temporary schema would be searched first for types,
-- and the guard runs in the tenant's session.
CREATE OR REPLACE FUNCTION sbarbase_guard.exposed_schemas() RETURNS event_trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $guard$
DECLARE exposed text; item text;
BEGIN
  IF (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = session_user) THEN
    RETURN;
  END IF;
  -- The empty schema stays empty: an extension created or moved into it would put the
  -- superuser's functions where the platform serves when nothing else is left.
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_proc WHERE pronamespace = pg_catalog.to_regnamespace('{EMPTY}'))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relnamespace = pg_catalog.to_regnamespace('{EMPTY}'))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_type WHERE typnamespace = pg_catalog.to_regnamespace('{EMPTY}'))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extnamespace = pg_catalog.to_regnamespace('{EMPTY}')) THEN
    RAISE EXCEPTION 'The schema {EMPTY} belongs to the platform and stays empty' USING ERRCODE = '42501';
  END IF;
  -- The same for extensions in the schemas that hold the platform's own functions: privileged extensions run
  -- as the superuser through supautils, and nothing of the tenant's belongs there.
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extnamespace IN (pg_catalog.to_regnamespace('{REST_SCHEMA}'), pg_catalog.to_regnamespace('sbarbase_guard'))) THEN
    RAISE EXCEPTION 'The schemas {REST_SCHEMA} and sbarbase_guard belong to the platform and hold no extension' USING ERRCODE = '42501';
  END IF;
  IF TG_TAG IN ('ALTER SCHEMA', 'DROP SCHEMA', 'DROP OWNED', 'DROP EXTENSION') THEN
    -- Read committed only: each statement below then reads what the platform committed while this
    -- one waited for the lock. A snapshot taken earlier would not see a schema exposed meanwhile.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
      RAISE EXCEPTION 'Rename or drop schemas at the read committed isolation level' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock({LOCK_KEY});
  END IF;
  -- The exposed schemas and the extra search path: PostgREST stops serving if either names a missing schema.
  SELECT coalesce(nullif(max(value) FILTER (WHERE key = 'pgrst.db_schemas'), ''), 'public')
         || coalesce(',' || nullif(max(value) FILTER (WHERE key = 'pgrst.db_extra_search_path'), ''), '')
    INTO exposed FROM {REST_SCHEMA}.settings();
  FOREACH item IN ARRAY string_to_array(coalesce(exposed, 'public'), ',') LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = item) THEN
      RAISE EXCEPTION 'The schema % is exposed through the Data API. Remove it from the exposed schemas first.', item
        USING ERRCODE = '42501';
    END IF;
  END LOOP;
END $guard$;
-- Any command that drops a schema, a cascade through an extension the tenant owns included: the
-- command tag alone does not show it, sql_drop does.
CREATE OR REPLACE FUNCTION sbarbase_guard.exposed_schemas_dropped() RETURNS event_trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $guard$
DECLARE exposed text; item text;
BEGIN
  IF (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = session_user) THEN
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger_dropped_objects() WHERE object_type = 'schema') THEN
    RETURN;
  END IF;
  -- Read committed only: each statement below then reads what the platform committed while this
  -- one waited for the lock. A snapshot taken earlier would not see a schema exposed meanwhile.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'Rename or drop schemas at the read committed isolation level' USING ERRCODE = '42501';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock({LOCK_KEY});
  -- The exposed schemas and the extra search path: PostgREST stops serving if either names a missing schema.
  SELECT coalesce(nullif(max(value) FILTER (WHERE key = 'pgrst.db_schemas'), ''), 'public')
         || coalesce(',' || nullif(max(value) FILTER (WHERE key = 'pgrst.db_extra_search_path'), ''), '')
    INTO exposed FROM {REST_SCHEMA}.settings();
  FOREACH item IN ARRAY string_to_array(coalesce(exposed, 'public'), ',') LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = item) THEN
      RAISE EXCEPTION 'The schema % is exposed through the Data API. Remove it from the exposed schemas first.', item
        USING ERRCODE = '42501';
    END IF;
  END LOOP;
END $guard$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'sbarbase_exposed_schemas_dropped') THEN
    CREATE EVENT TRIGGER sbarbase_exposed_schemas_dropped ON sql_drop EXECUTE FUNCTION sbarbase_guard.exposed_schemas_dropped();
  END IF;
END $$;
ALTER EVENT TRIGGER sbarbase_exposed_schemas_dropped ENABLE ALWAYS;
DO $$ BEGIN
  -- An earlier trigger without DROP EXTENSION is replaced: dropping an extension the tenant owns,
  -- after adding an exposed schema to it, would drop that schema.
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'sbarbase_exposed_schemas'
             AND NOT evttags @> ARRAY['DROP EXTENSION']::pg_catalog.text[]) THEN
    DROP EVENT TRIGGER sbarbase_exposed_schemas;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'sbarbase_exposed_schemas') THEN
    CREATE EVENT TRIGGER sbarbase_exposed_schemas ON ddl_command_end
      WHEN TAG IN ('ALTER SCHEMA', 'DROP SCHEMA', 'DROP OWNED', 'CREATE EXTENSION', 'ALTER EXTENSION', 'DROP EXTENSION')
      EXECUTE FUNCTION sbarbase_guard.exposed_schemas();
  END IF;
END $$;
ALTER EVENT TRIGGER sbarbase_exposed_schemas ENABLE ALWAYS;
-- The reserved-schema guard (durable_runtime.developer_grants_sql) runs in the tenant's session too:
-- an environment whose guard predates it gets the same search path here, at its next start.
DO $$ BEGIN
  IF pg_catalog.to_regprocedure('sbarbase_guard.reserved_schemas()') IS NOT NULL THEN
    ALTER FUNCTION sbarbase_guard.reserved_schemas() SET search_path = pg_catalog, pg_temp;
  END IF;
END $$;
"""


def guard_present_sql(e):
    """`t` when the guards, the empty schema and the settings functions are all in place, and this
    environment's REST login may run the db-pre-config function. Without that PostgREST could not
    apply its settings, and PostgREST would serve nothing (the container's empty schema)."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    guards = "(to_regprocedure('sbarbase_guard.exposed_schemas()'), to_regprocedure('sbarbase_guard.exposed_schemas_dropped()'))"
    return (SAFE + "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'sbarbase_exposed_schemas_dropped' AND evtenabled = 'A') "
            "AND EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtname = 'sbarbase_exposed_schemas' AND evtenabled = 'A' "
            "AND evttags @> ARRAY['DROP EXTENSION']::pg_catalog.text[]) "
            f"AND to_regnamespace('{EMPTY}') IS NOT NULL "
            # Both guards of this release: the lock, the settings read from this database, run as their owner.
            f"AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc WHERE oid IN {guards} AND prosecdef "
            f"AND prosrc LIKE '%pg_advisory_xact_lock({LOCK_KEY})%' AND prosrc LIKE '%{REST_SCHEMA}.settings()%' "
            "AND proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]) = 2 "
            f"AND EXISTS (SELECT 1 FROM pg_catalog.pg_proc WHERE oid = to_regprocedure('{PRE_CONFIG}()') "
            "AND prosrc LIKE '%search path is not pg_catalog%' AND NOT prosecdef AND proconfig IS NULL) "
            f"AND CASE WHEN pg_catalog.to_regrole('{e}_rest') IS NULL THEN true ELSE "
            f"coalesce(pg_catalog.has_function_privilege('{e}_rest', to_regprocedure('{PRE_CONFIG}()'), 'EXECUTE'), false) "
            f"AND coalesce(pg_catalog.has_function_privilege('{e}_rest', to_regprocedure('{REST_SCHEMA}.settings()'), 'EXECUTE'), false) "
            f"AND coalesce(pg_catalog.has_schema_privilege('{e}_rest', to_regnamespace('{REST_SCHEMA}'), 'USAGE'), false) END "
            f"AND NOT ({OWNED_BY_OTHERS_SQL}) "
            "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc WHERE oid = to_regprocedure('sbarbase_guard.reserved_schemas()') "
            "AND NOT coalesce(proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[], false));")


def missing_text(missing):
    listed = ', '.join(missing)
    return (f'The schema {listed} does not exist in this environment\'s database. Create it first, then save again.'
            if len(missing) == 1 else
            f'The schemas {listed} do not exist in this environment\'s database. Create them first, then save again.')


def left_out_text(missing, nothing_left=False):
    served = 'No exposed schema is left, so no table is served.' if nothing_left else 'The rest is served.'
    return (f'{LEFT_OUT}: {", ".join(missing)}. {served} '
            'Create it again and restart Sbarbase, or save the settings, to serve it.')


def record_served(e, served, catalog=None):
    """What PostgREST serves now, written whenever the runtime writes its settings; None for
    the container's defaults."""
    catalog = catalog or CATALOG
    values = (None, None, None) if served is None else (
        ','.join(served['schemas']), ','.join(served['extra_search_path']), served['max_rows'])
    try:
        with closing(sqlite3.connect(catalog, timeout=5)) as database, database:
            database.execute('UPDATE data_api_settings SET served_schemas=?, served_extra_search_path=?, served_max_rows=? '
                             'WHERE runtime=?', (*values, e))
    except sqlite3.Error:
        pass


def record_applied(e, served, report, applied, catalog=None):
    """A console change's whole outcome, in one transaction: what PostgREST serves, its report, and the
    settings now applied. Unlike the other writers here it raises on a catalog error, so the change is
    never recorded in part, nor silently not at all."""
    with closing(sqlite3.connect(catalog or CATALOG, timeout=5)) as database, database:
        changed = database.execute(
            'UPDATE data_api_settings SET served_schemas=?, served_extra_search_path=?, served_max_rows=?, start_report=?, '
            'applied_schemas=?, applied_extra_search_path=?, applied_max_rows=?, state=?, failure=NULL, updated_at=? WHERE runtime=?',
            (','.join(served['schemas']), ','.join(served['extra_search_path']), served['max_rows'], report,
             ','.join(applied['schemas']), ','.join(applied['extra_search_path']), applied['max_rows'], 'applied',
             int(time.time() * 1000), e)).rowcount
        if changed != 1:
            raise sqlite3.OperationalError('The Data API settings row is missing')


def record(e, state, failure=None, catalog=None, applied=None):
    """The outcome in the catalog row; with `applied`, those settings become the ones served."""
    catalog = catalog or CATALOG
    try:
        with closing(sqlite3.connect(catalog, timeout=5)) as database, database:
            database.execute('UPDATE data_api_settings SET state=?, failure=?, updated_at=? WHERE runtime=?',
                             (state, failure, int(time.time() * 1000), e))
            if applied is not None:
                database.execute('UPDATE data_api_settings SET applied_schemas=?, applied_extra_search_path=?, applied_max_rows=? '
                                 'WHERE runtime=?', (','.join(applied['schemas']), ','.join(applied['extra_search_path']),
                                                     applied['max_rows'], e))
    except sqlite3.Error:
        pass


def environment_lock(e):
    """This environment's Data API lock, taken without waiting (BlockingIOError when it is held). A console
    change holds it for its write and again for recording the outcome, and the installation's operation
    lock only for its checks and that recording: the SQL a tenant session can make wait runs under this
    lock alone, so it delays only this environment. An in-place restore takes it too, so the two never
    interleave. Never waited for: an operation that holds the operation lock only tries it."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    handle = (OPERATION_LOCK.parent / f'data-api-{e}.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    return handle


def busy(e):
    """True while another runtime operation, or another change of this environment, holds its lock."""
    try:
        with OPERATION_LOCK.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with environment_lock(e):
            return False
    except BlockingIOError:
        return True


# durable_runtime.py data-api exits with this code, and prints the reason as its last line, when
# the settings name a schema the database does not have. Any other failure is reported generically.
REFUSED = 3
# And with this one when the settings were written and PostgREST told, but the outcome could not be recorded.
UNRECORDED = 4
UNRECORDED_TEXT = ('The Data API settings were written, but the outcome could not be recorded, so what this page '
                   'shows as served may be out of date, and Studio was not restarted. PostgREST takes them at its next '
                   'reload. Save them again: until then, the next start returns to the settings applied before.')


def apply(e):
    if busy(e):
        # Another runtime operation is running; the supervisor asks again shortly.
        return 75
    # The settings this run applies. The console refuses another change while this one is pending.
    desired = load(e)
    if desired is None:
        return 1
    result = subprocess.run(['/usr/bin/python3', 'lab/durable_runtime.py', 'data-api', e], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode == 75:
        # A lock was taken between the check above and the command, and nothing was written; or a start
        # or restore wrote other settings after this change did, and the database holds those. Ask again.
        return 75
    if result.returncode == REFUSED:
        lines = [line for line in result.stderr.strip().splitlines() if line]
        record(e, 'failed', lines[-1][:500] if lines else 'The Data API settings were refused.')
        return 1
    if result.returncode == UNRECORDED:
        record(e, 'failed', UNRECORDED_TEXT)
        return 1
    if result.returncode:
        record(e, 'failed', 'The Data API settings could not be applied. The environment keeps serving its previous settings.')
        return 1
    # The command recorded the change as applied itself, under the operation lock (Runtime.data_api_finish).
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply one environment's saved Data API settings")
    parser.add_argument('command', choices=('apply',))
    parser.add_argument('environment')
    args = parser.parse_args(argv)
    if not RUNTIME.fullmatch(args.environment):
        print('Invalid environment', file=sys.stderr)
        return 2
    return apply(args.environment)


if __name__ == '__main__':
    sys.exit(main())
