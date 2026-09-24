"""Phase 0 of importing a Supabase project into one Sbarbase environment: inspect, read only.

It reads a source database (Supabase Cloud, a self-hosted compose stack, or any PostgreSQL
that carries the Supabase schemas) and states, before anything is dumped or allocated,
what an import would refuse, what it would warn about, and what an operator must redo by
hand. It never writes to the source and never allocates on the target.

The decision is a pure function, ``assess(source, reference)``: ``source`` is what
``gather`` read from the source, ``reference`` is the same reading taken from an Sbarbase
environment on the pinned images (``--capture-reference``). Comparing against a reading of
the real target, not a table typed into this file, keeps the check honest when pins move.

The connection string is read from standard input or ``SBARBASE_IMPORT_SOURCE_URL``, never
from an argument, and is handed to ``psql`` as libpq environment variables, so the password
appears in no process listing and in no report.

Scope: a dry run. The dump, restore, object copy, key and verification phases are designed
in docs/engineering/plans/2026-09-23-verification-and-migration-plan.md and do not exist yet.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

# Roles every Supabase database carries or that PostgreSQL owns. Anything else is a custom
# role the project created; in a shared cluster roles are cluster wide, so an import must
# namespace or refuse each one.
RESERVED_ROLES = frozenset((
    'postgres', 'supabase_admin', 'supabase_auth_admin', 'supabase_storage_admin', 'authenticator',
    'anon', 'authenticated', 'service_role', 'dashboard_user', 'pgbouncer', 'supabase_replication_admin',
    'supabase_read_only_user', 'supabase_realtime_admin', 'supabase_functions_admin', 'supabase_etl_admin',
    'supabase_privileged_role', 'pgsodium_keyholder', 'pgsodium_keyiduser', 'pgsodium_keymaker',
    'cli_login_postgres', 'pgtle_admin', 'supabase_superuser'))

# Extensions that load but whose behaviour needs a service Sbarbase does not run yet.
INACTIVE_EXTENSIONS = {
    'pg_cron': 'scheduled jobs would not run; they are imported disabled',
    'pg_net': 'outbound HTTP from SQL has no worker here',
    'pg_graphql': 'GraphQL is not routed through the gateway',
    'wrappers': 'foreign data wrappers need their remote credentials re-entered',
}

TARGET_MAJOR = 17

# Each query returns one column per line, fields separated by a unit separator. A table that
# may not exist is read through to_regclass so an older or trimmed source still answers.
QUERIES = {
    'server_version_num': "SELECT current_setting('server_version_num')",
    'extensions': 'SELECT extname, extversion FROM pg_extension ORDER BY 1',
    'available_extensions': 'SELECT name FROM pg_available_extensions ORDER BY 1',
    'roles': "SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY 1",
    'auth_migrations': 'SELECT version FROM auth.schema_migrations ORDER BY 1',
    'storage_migrations': 'SELECT name FROM storage.migrations ORDER BY id',
    'app_migrations': 'SELECT count(*) FROM supabase_migrations.schema_migrations',
    'publications': ('SELECT p.pubname, count(t.tablename) FROM pg_publication p LEFT JOIN pg_publication_tables t '
                     'ON t.pubname=p.pubname GROUP BY 1 ORDER BY 1'),
    'vault_secrets': 'SELECT count(*) FROM vault.secrets',
    'cron_jobs': 'SELECT count(*) FROM cron.job',
    'auth_users': 'SELECT count(*) FROM auth.users',
    'auth_identities': "SELECT provider, count(*) FROM auth.identities GROUP BY 1 ORDER BY 1",
    'buckets': 'SELECT count(*) FROM storage.buckets',
    'objects': "SELECT count(*), coalesce(sum((metadata->>'size')::bigint),0) FROM storage.objects",
    'schemas': ("SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN "
                "('information_schema') ORDER BY 1"),
}

# Queries whose table may be absent; the gather records None instead of failing.
OPTIONAL = {'auth_migrations': 'auth.schema_migrations', 'storage_migrations': 'storage.migrations',
            'app_migrations': 'supabase_migrations.schema_migrations', 'vault_secrets': 'vault.secrets',
            'cron_jobs': 'cron.job', 'auth_users': 'auth.users', 'auth_identities': 'auth.identities',
            'buckets': 'storage.buckets', 'objects': 'storage.objects'}

SEPARATOR = '\x1f'


def connection_environment(url):
    """libpq variables for a postgres:// URL. Raises ValueError on anything else."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ('postgres', 'postgresql') or not parts.hostname:
        raise ValueError('Source must be a postgres:// or postgresql:// URL with a host')
    environment = {'PGHOST': parts.hostname, 'PGPORT': str(parts.port or 5432),
                   'PGDATABASE': unquote(parts.path.lstrip('/')) or 'postgres',
                   'PGCONNECT_TIMEOUT': '15', 'PGAPPNAME': 'sbarbase-import-inspect'}
    if parts.username:
        environment['PGUSER'] = unquote(parts.username)
    if parts.password is not None:
        environment['PGPASSWORD'] = unquote(parts.password)
    query = parse_qs(parts.query)
    environment['PGSSLMODE'] = query.get('sslmode', ['prefer'])[0]
    # A read only session: nothing this probe runs can change the source.
    environment['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=30000'
    return environment


def psql_executor(environment, psql='psql'):
    """An executor that runs one query through psql with the connection in the environment."""
    def execute(sql):
        result = subprocess.run([psql, '-X', '-A', '-t', '-q', '-v', 'ON_ERROR_STOP=1', '-F', SEPARATOR, '-c', sql],
                                env={**os.environ, **environment}, capture_output=True, text=True, timeout=60)
        if result.returncode:
            # The first line of the server's error only; it never contains the password.
            raise RuntimeError((result.stderr.strip().splitlines() or ['psql failed'])[0])
        return [line.split(SEPARATOR) for line in result.stdout.splitlines() if line]
    return execute


def gather(execute):
    """Read every fact the assessment needs. ``execute(sql)`` returns rows of strings."""
    present = {}
    for key, relation in OPTIONAL.items():
        present[key] = execute(f"SELECT to_regclass('{relation}') IS NOT NULL")[0][0] == 't'
    facts = {}
    for key, sql in QUERIES.items():
        if key in OPTIONAL and not present[key]:
            facts[key] = None
            continue
        facts[key] = execute(sql)
    single = lambda rows: None if rows is None else int(rows[0][0])
    objects = facts['objects']
    return {
        'major': int(facts['server_version_num'][0][0]) // 10000,
        'extensions': {row[0]: row[1] for row in facts['extensions']},
        'available_extensions': sorted(row[0] for row in facts['available_extensions']),
        'roles': sorted(row[0] for row in facts['roles']),
        'auth_migrations': None if facts['auth_migrations'] is None else [row[0] for row in facts['auth_migrations']],
        'storage_migrations': None if facts['storage_migrations'] is None else [row[0] for row in facts['storage_migrations']],
        'app_migrations': single(facts['app_migrations']),
        'publications': {row[0]: int(row[1]) for row in facts['publications']},
        'vault_secrets': single(facts['vault_secrets']),
        'cron_jobs': single(facts['cron_jobs']),
        'auth_users': single(facts['auth_users']),
        'auth_identities': None if facts['auth_identities'] is None else {row[0]: int(row[1]) for row in facts['auth_identities']},
        'buckets': single(facts['buckets']),
        'objects': None if objects is None else {'count': int(objects[0][0]), 'bytes': int(objects[0][1])},
        'schemas': [row[0] for row in facts['schemas']],
    }


def assess(source, reference):
    """What an import of ``source`` into an environment like ``reference`` would do.

    Refusals stop an import before any dump. Warnings name behaviour that changes. Manual
    items are settings that live outside the database and must be entered again.
    """
    refusals, warnings, manual = [], [], []
    if source['major'] > TARGET_MAJOR:
        refusals.append(f"source PostgreSQL {source['major']} is newer than the target's {TARGET_MAJOR}")
    elif source['major'] < 15:
        warnings.append(f"source PostgreSQL {source['major']} is older than any Supabase release this was checked against")
    for kind, label in (('auth_migrations', 'Auth'), ('storage_migrations', 'Storage')):
        if source[kind] is None:
            if kind == 'auth_migrations':
                refusals.append('source has no Auth migration table; it is not a Supabase database')
            else:
                warnings.append('Storage never ran on the source: there are no buckets or objects to copy')
            continue
        unknown = sorted(set(source[kind]) - set(reference[kind] or ()))
        if unknown:
            refusals.append(f'source {label} schema has {len(unknown)} migration(s) the pinned {label} does not know '
                            f'(first: {unknown[0]}); import after the pin is updated')
        missing = len(set(reference[kind] or ()) - set(source[kind]))
        if missing:
            warnings.append(f'source {label} schema is {missing} migration(s) behind; the pinned {label} applies them first')
    available = set(reference['available_extensions'])
    for name in sorted(source['extensions']):
        if name not in available:
            refusals.append(f'extension {name} is not available in the target image')
        elif name in INACTIVE_EXTENSIONS:
            warnings.append(f'extension {name}: {INACTIVE_EXTENSIONS[name]}')
    custom = [role for role in source['roles'] if role not in RESERVED_ROLES]
    if custom:
        warnings.append(f'{len(custom)} custom role(s) ({", ".join(custom[:5])}) will be renamed per environment, '
                        'with grants and policies rewritten; their passwords are not carried')
    if source['vault_secrets']:
        refusals.append(f"{source['vault_secrets']} Vault secret(s): their root key is not in a dump; "
                        'provide it or empty the vault first')
    if source['cron_jobs']:
        warnings.append(f"{source['cron_jobs']} cron job(s) will be imported disabled")
    # Every Supabase database has the publication; it matters only when a table is in it.
    realtime = [name for name, tables in source['publications'].items() if name.startswith('supabase_realtime') and tables]
    if realtime:
        warnings.append('Realtime publications are kept but inactive: Sbarbase does not run Realtime yet')
    identities = source['auth_identities'] or {}
    external = sorted(provider for provider in identities if provider not in ('email', 'phone', 'anonymous'))
    if external:
        manual.append(f'OAuth providers ({", ".join(external)}): register the new callback URL and enter client secrets; '
                      'OAuth through the gateway is not available yet')
    manual.extend([
        'SMTP settings and email templates',
        'Site URL and redirect allow list',
        'Edge Functions: source is listed only, nothing is deployed',
        'API keys: new publishable keys are issued; the legacy JWT secret keeps existing sessions only if supplied',
    ])
    summary = {'auth_users': source['auth_users'], 'buckets': source['buckets'], 'objects': source['objects'],
               'app_migrations': source['app_migrations']}
    return {'importable': not refusals, 'refusals': refusals, 'warnings': warnings, 'manual': manual,
            'summary': summary}


def read_url(stream, environment):
    value = environment.get('SBARBASE_IMPORT_SOURCE_URL') or stream.readline()
    if not value or not value.strip():
        raise SystemExit('Give the source URL on standard input or in SBARBASE_IMPORT_SOURCE_URL.')
    return value.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--reference', type=Path, help='a reading of an Sbarbase environment (from --capture-reference)')
    parser.add_argument('--capture-reference', type=Path, help='read the given database and save it as a reference')
    parser.add_argument('--report', type=Path, help='where to write the JSON report')
    parser.add_argument('--psql', default='psql', help='psql executable')
    args = parser.parse_args(argv)
    if bool(args.reference) == bool(args.capture_reference):
        parser.error('give exactly one of --reference or --capture-reference')
    facts = gather(psql_executor(connection_environment(read_url(sys.stdin, os.environ)), args.psql))
    now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    if args.capture_reference:
        args.capture_reference.write_text(json.dumps({'captured_at': now, **facts}, indent=2) + '\n')
        print(f'reference written: {args.capture_reference}')
        return 0
    reference = json.loads(args.reference.read_text())
    report = {'inspected_at': now, 'scope': 'Read-only inspection (phase 0). Nothing was dumped, copied or allocated.',
              'source': facts, **assess(facts, reference)}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    for label in ('refusals', 'warnings', 'manual'):
        for line in report[label]:
            print(f'{label[:-1] if label != "manual" else "manual"}: {line}')
    print('importable' if report['importable'] else 'not importable: resolve the refusals above')
    return 0 if report['importable'] else 3


if __name__ == '__main__':
    sys.exit(main())
