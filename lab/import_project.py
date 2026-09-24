"""Import a Supabase project into one new, empty Sbarbase environment.

Usage (settings on stdin as JSON, never as arguments):
  /usr/bin/python3 lab/import_project.py <environment> [--dry-run] [--report PATH] <<'JSON'
  {"database_url": "postgresql://postgres:…@db.<ref>.supabase.co:5432/postgres",
   "api_url": "https://<ref>.supabase.co", "service_role_key": "…", "apikey": "…"?}
  JSON

<environment> is the environment id from the console or its runtime id (e_…). The source is
only read: a Supabase Cloud project (direct connection or session pooler), a self-hosted
stack, or another Sbarbase environment (its developer login and gateway address).

In order, it:
  0. inspects the source (lab/import_inspect.py) and refuses what the target cannot hold;
  1. checks the target is new and empty;
  2. restores the `public` schema, owned by the environment's developer login, with the
     source's grants to anon, authenticated and service_role;
  3. copies auth.users, auth.identities and auth.mfa_factors in the columns both sides have,
     so password hashes move and people sign in with their old passwords;
  4. copies the `public` rows;
  5. copies the buckets, then every object through the source's Storage API into this
     environment's Storage, and restores each object's owner and metadata;
  6. recreates the source's own triggers on auth and storage tables (after the rows, so a
     sign-up trigger does not run for imported users);
  7. compares row counts, users and objects, source against target, and writes a report.

Sessions and refresh tokens are not copied: people sign in again. API keys, OAuth providers,
SMTP, redirect URLs, Edge Functions and cron jobs are listed for the operator to set again.
With --dry-run it stops after step 0. A failure before step 7 leaves the environment to be
deleted and created again: the import never repairs a half-imported environment.
"""
import argparse
import datetime
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import backup
import durable_runtime as runtime
import import_inspect
import run as lab

TOOL = 'sbarbase-import-tool'
KEEP_ROLES = ('anon', 'authenticated', 'service_role', 'PUBLIC')
AUTH_TABLES = ('users', 'identities', 'mfa_factors')
MAX_OBJECT = 5 * 1024 ** 3


class ImportError_(RuntimeError):
    pass


# ---- the source, read through a throwaway container of the pinned database image -------------

class Source:
    """psql and pg_dump against the source, run in a container of the pinned database image so
    the host needs neither; the password is passed in a private env file, never an argument."""

    def __init__(self, url):
        self.environment = import_inspect.connection_environment(url)
        host = self.environment['PGHOST']
        local = host in ('127.0.0.1', 'localhost', '::1')
        network = 'host' if local else runtime.EGRESS
        if not local and not runtime.inspect('network', runtime.EGRESS):
            lab.docker('network', 'create', '--label', 'io.sbarbase.owner=' + runtime.OWNER, runtime.EGRESS)
        image = json.loads((lab.ROOT / 'lab' / 'distro-image.lock.json').read_text())['id']
        lab.docker('rm', '-f', TOOL, check=False)
        lab.docker('run', '-d', '--rm', '--name', TOOL, '--network', network, '--label', 'io.sbarbase.owner=' + runtime.OWNER,
                   '--entrypoint', 'sleep', image, 'infinity')
        handle, self.env_file = tempfile.mkstemp(prefix='sbarbase-import-', suffix='.env')
        with os.fdopen(handle, 'w') as file:
            # The source is read only, whatever the tools do.
            file.write(''.join(f'{key}={value}\n' for key, value in {**self.environment,
                       'PGOPTIONS': '-c default_transaction_read_only=on'}.items()))
        os.chmod(self.env_file, 0o600)

    def command(self, *argv):
        return ['docker', 'exec', '-i', '--env-file', self.env_file, TOOL, *argv]

    def query(self, sql):
        result = subprocess.run(self.command('psql', '-X', '-A', '-t', '-q', '-v', 'ON_ERROR_STOP=1', '-F',
                                             import_inspect.SEPARATOR, '-c', sql), capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise ImportError_('source: ' + ((result.stderr.strip().splitlines() or ['psql failed'])[0]))
        return [line.split(import_inspect.SEPARATOR) for line in result.stdout.splitlines() if line]

    def dump(self, *arguments):
        result = subprocess.run(self.command('pg_dump', '--no-owner', *arguments), capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise ImportError_('source dump: ' + ((result.stderr.strip().splitlines() or ['pg_dump failed'])[0]))
        return result.stdout

    def copy_out(self, sql):
        return subprocess.Popen(self.command('psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-c', f'COPY ({sql}) TO STDOUT'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def close(self):
        lab.docker('rm', '-f', TOOL, check=False)
        Path(self.env_file).unlink(missing_ok=True)


# ---- the target ---------------------------------------------------------------------------------

def target_sql(e, sql, role=None, replica=False, database=None):
    """Runs a script in the environment's database as the database owner, or as `role`."""
    prefix = ('SET session_replication_role = replica;\n' if replica else '') + (f'SET ROLE {role};\n' if role else '')
    result = lab.docker('exec', '-i', runtime.DB, 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-1', '-U', 'supabase_admin',
                        '-d', database or e, data=prefix + sql, check=False)
    if result.returncode:
        lines = [line for line in result.stderr.splitlines() if 'ERROR' in line] or result.stderr.strip().splitlines() or ['psql failed']
        raise ImportError_('target: ' + lines[0][:300])
    return result.stdout


def target_rows(e, sql):
    return [line.split('|') for line in target_sql(e, sql).splitlines() if line]


def columns(execute, schema, table):
    """Plain columns of a table: generated columns are computed, never copied."""
    rows = execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = '{table}' "
                   "AND is_generated = 'NEVER' ORDER BY ordinal_position")
    return [row[0] for row in rows]


def copy_table(source, e, schema, table):
    """Copies the rows of one table in the columns both sides have; returns the columns used."""
    wanted = columns(source.query, schema, table)
    have = set(columns(lambda sql: target_rows(e, sql), schema, table))
    common = [name for name in wanted if name in have]
    if not common:
        return []
    listed = ', '.join(f'"{name}"' for name in common)
    reader = source.copy_out(f'SELECT {listed} FROM {schema}.{table}')
    writer = subprocess.run(['docker', 'exec', '-i', runtime.DB, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin',
                             '-d', e, '-c', f'COPY {schema}.{table} ({listed}) FROM STDIN'], stdin=reader.stdout, capture_output=True, text=True)
    reader.stdout.close()
    reader.wait()
    if reader.returncode:
        raise ImportError_(f'source: reading {schema}.{table} failed')
    if writer.returncode:
        raise ImportError_(f'target: {schema}.{table}: ' + ((writer.stderr.strip().splitlines() or ['copy failed'])[0])[:300])
    return common


def schema_script(text):
    """The source's `public` schema, less what belongs to the target itself: the schema, its
    ownership and comment, and grants to roles that do not exist here."""
    kept = []
    for line in text.splitlines():
        if re.match(r'^(CREATE SCHEMA public;|COMMENT ON SCHEMA public|ALTER SCHEMA public OWNER|ALTER DEFAULT PRIVILEGES FOR ROLE)', line):
            continue
        grant = re.match(r'^(GRANT|REVOKE) .* (TO|FROM) (.+);$', line)
        if grant and any(role.strip().strip('"') not in KEEP_ROLES for role in grant.group(3).split(',')):
            continue
        if line.startswith(('\\restrict', '\\unrestrict')):
            continue
        kept.append(line)
    return '\n'.join(kept) + '\n'


# ---- storage objects through the Storage APIs -----------------------------------------------------

def http(url, method='GET', headers=None, body=None, timeout=300):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def copy_objects(source, e, settings, report):
    endpoints = runtime.published_endpoints()[e]
    secret = json.loads((runtime.PRIVATE / 'runtime.json').read_text())['environments'][e]['jwt']
    target_headers = {'authorization': 'Bearer ' + runtime.token(secret, 'service_role'), 'x-forwarded-host': endpoints['storage']['tenantHost'],
                      'x-upsert': 'true'}
    api = settings['api_url'].rstrip('/') + '/storage/v1'
    source_headers = {'authorization': 'Bearer ' + settings['service_role_key'], 'apikey': settings.get('apikey') or settings['service_role_key']}
    owner_column = 'owner_id' if 'owner_id' in columns(source.query, 'storage', 'objects') else 'owner'
    rows = source.query(f"SELECT bucket_id, name, coalesce(metadata->>'mimetype', 'application/octet-stream'), "
                        f"coalesce(metadata->>'cacheControl', 'max-age=3600'), coalesce({owner_column}::text, ''), created_at::text, "
                        "coalesce(user_metadata::text, 'null') FROM storage.objects ORDER BY bucket_id, name"
                        if 'user_metadata' in columns(source.query, 'storage', 'objects') else
                        f"SELECT bucket_id, name, coalesce(metadata->>'mimetype', 'application/octet-stream'), "
                        f"coalesce(metadata->>'cacheControl', 'max-age=3600'), coalesce({owner_column}::text, ''), created_at::text, 'null' "
                        "FROM storage.objects ORDER BY bucket_id, name")
    copied, failed, owners = 0, [], []
    for bucket, name, mimetype, cache, owner, created, meta in rows:
        path = urllib.parse.quote(bucket, safe='') + '/' + urllib.parse.quote(name, safe='/')
        status, data, _ = http(f'{api}/object/authenticated/{path}', headers=source_headers)
        if status != 200:
            failed.append(f'{bucket}/{name}: download {status}')
            continue
        cache_seconds = re.search(r'max-age=(\d+)', cache)
        status, answer, _ = http(f"{endpoints['storage']['url']}/object/{path}", 'POST', {**target_headers, 'content-type': mimetype,
                                 'cache-control': f'max-age={cache_seconds.group(1) if cache_seconds else 3600}'}, data)
        if status != 200:
            failed.append(f'{bucket}/{name}: upload {status} {answer[:120]!r}')
            continue
        copied += 1
        owners.append((bucket, name, owner, created, meta))
    # Owner and dates as they were, so policies on owner keep working.
    target_columns = set(columns(lambda sql: target_rows(e, sql), 'storage', 'objects'))
    target_owner = 'owner_id' if 'owner_id' in target_columns else 'owner'
    statements = []
    for bucket, name, owner, created, meta in owners:
        sets = [f"created_at = {runtime.sql_literal(created)}::timestamptz"]
        if owner:
            sets.append(f'{target_owner} = {runtime.sql_literal(owner)}' + ('' if target_owner == 'owner_id' else '::uuid'))
            if target_owner == 'owner_id' and 'owner' in target_columns and re.fullmatch(r'[0-9a-f-]{36}', owner):
                sets.append(f'owner = {runtime.sql_literal(owner)}::uuid')
        if meta != 'null' and 'user_metadata' in target_columns:
            sets.append(f'user_metadata = {runtime.sql_literal(meta)}::jsonb')
        statements.append(f"UPDATE storage.objects SET {', '.join(sets)} WHERE bucket_id = {runtime.sql_literal(bucket)} "
                          f'AND name = {runtime.sql_literal(name)};')
    if statements:
        target_sql(e, '\n'.join(statements))
    report['objects'] = {'source': len(rows), 'copied': copied, 'failed': failed[:20]}
    if failed:
        raise ImportError_(f'{len(failed)} object(s) could not be copied (first: {failed[0]})')


# ---- the import -----------------------------------------------------------------------------------

def read_settings(stream):
    try:
        settings = json.loads(stream.read())
    except ValueError:
        raise ImportError_('Send the settings as JSON on stdin') from None
    if not isinstance(settings, dict) or not isinstance(settings.get('database_url'), str):
        raise ImportError_('Give at least database_url')
    for key in ('api_url', 'service_role_key', 'apikey'):
        if key in settings and not isinstance(settings[key], str):
            raise ImportError_(f'{key} must be text')
    if settings.get('api_url'):
        parsed = urllib.parse.urlsplit(settings['api_url'])
        if parsed.scheme not in ('https', 'http') or not parsed.netloc:
            raise ImportError_('api_url is the project address, such as https://<ref>.supabase.co')
        if not settings.get('service_role_key'):
            raise ImportError_('Give service_role_key with api_url, to copy the Storage objects')
    return settings


def ensure_developer(e):
    """The environment's developer login owns what the import creates, as it would own a
    migration; it stays unable to sign in unless direct access is on."""
    work = runtime.Runtime()
    if not runtime.direct_on(e):
        # A throwaway password on a login that cannot sign in; turning access on sets a real one.
        target_sql(e, work.developer_sql(e, secrets.token_urlsafe(24)) + f'ALTER ROLE {e}_developer NOLOGIN;\n', database='postgres')
    target_sql(e, work.developer_grants_sql(e))


def run_import(e, settings, dry_run=False):
    report = {'environment': e, 'started_at': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'steps': []}
    step = lambda text: (report['steps'].append(text), print(text, flush=True))
    source = Source(settings['database_url'])
    try:
        facts = import_inspect.gather(source.query)
        reference = import_inspect.gather(lambda sql: [row for row in target_rows(e, sql)])
        assessment = import_inspect.assess(facts, reference)
        report.update({'refusals': assessment['refusals'], 'warnings': assessment['warnings'], 'manual': assessment['manual'],
                       'source': assessment['summary']})
        step(f"inspected the source: {facts['auth_users']} user(s), {facts['buckets'] or 0} bucket(s), "
             f"{(facts['objects'] or {}).get('count', 0)} object(s)")
        if assessment['refusals']:
            raise ImportError_('the source cannot be imported: ' + '; '.join(assessment['refusals']))
        if dry_run:
            return report
        counts = target_rows(e, "SELECT (SELECT count(*) FROM pg_tables WHERE schemaname = 'public'), (SELECT count(*) FROM auth.users), "
                                "(SELECT count(*) FROM storage.objects), (SELECT count(*) FROM storage.buckets)")[0]
        if any(int(value) for value in counts):
            raise ImportError_('the target environment is not empty; create a new environment to import into')
        if (facts['objects'] or {}).get('count') and not settings.get('api_url'):
            raise ImportError_('the source has Storage objects: give api_url and service_role_key to copy them')
        ensure_developer(e)
        role = f'{e}_developer'

        extensions = source.query("SELECT e.extname, n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace")
        present = {row[0] for row in target_rows(e, 'SELECT extname FROM pg_extension')}
        create = [f'CREATE SCHEMA IF NOT EXISTS "{schema}"; CREATE EXTENSION IF NOT EXISTS "{name}" WITH SCHEMA "{schema}";'
                  for name, schema in extensions if name not in present and name not in import_inspect.INACTIVE_EXTENSIONS]
        if create:
            target_sql(e, '\n'.join(create))
        step(f'extensions: {len(create)} added')

        script = schema_script(source.dump('--schema-only', '--schema=public'))
        # The developer login's default grants would give anon more than the source did; the
        # source's own grants are applied again on a clean slate, so a private table stays private.
        grants = '\n'.join(line for line in script.splitlines() if re.match(r'^(GRANT|REVOKE) ', line))
        target_sql(e, script + '\nREVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, service_role;\n'
                   'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, service_role;\n' + grants + '\n', role=role)
        tables = [row[0] for row in source.query("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
        step(f'public schema restored: {len(tables)} table(s)')

        for table in AUTH_TABLES:
            if source.query(f"SELECT to_regclass('auth.{table}') IS NOT NULL")[0][0] == 't' and \
                    target_rows(e, f"SELECT to_regclass('auth.{table}') IS NOT NULL")[0][0] == 't':
                copy_table(source, e, 'auth', table)
        step(f"auth: {target_rows(e, 'SELECT count(*) FROM auth.users')[0][0]} user(s) with their password hashes")

        data = source.dump('--data-only', '--schema=public', '--disable-triggers')
        target_sql(e, '\n'.join(line for line in data.splitlines()
                                 if not line.startswith(('\\restrict', '\\unrestrict'))) + '\n', replica=True)
        step('public rows copied')

        if source.query("SELECT to_regclass('storage.buckets') IS NOT NULL")[0][0] == 't':
            copy_table(source, e, 'storage', 'buckets')
            if (facts['objects'] or {}).get('count'):
                copy_objects(source, e, settings, report)
            step(f"storage: {target_rows(e, 'SELECT count(*) FROM storage.buckets')[0][0]} bucket(s), "
                 f"{target_rows(e, 'SELECT count(*) FROM storage.objects')[0][0]} object(s)")

        existing = {(row[0], row[1]) for row in target_rows(e, "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                                                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('auth','storage') AND NOT t.tgisinternal")}
        triggers = [row for row in source.query("SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                                "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
                                                "JOIN pg_namespace pn ON pn.oid = p.pronamespace "
                                                "WHERE n.nspname IN ('auth','storage') AND NOT t.tgisinternal AND pn.nspname = 'public'")
                    if (row[0], row[1]) not in existing]
        if triggers:
            target_sql(e, '\n'.join(row[2] + ';' for row in triggers), role=role)
        step(f'triggers on auth and storage recreated: {len(triggers)}')
        target_sql(e, "NOTIFY pgrst, 'reload schema';")

        mismatches = []
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            wanted, got = source.query(f'SELECT count(*) FROM public.{quoted}')[0][0], target_rows(e, f'SELECT count(*) FROM public.{quoted}')[0][0]
            if wanted != got:
                mismatches.append(f'public.{table}: {wanted} → {got}')
        for relation in ('auth.users', 'auth.identities', 'storage.buckets', 'storage.objects'):
            if source.query(f"SELECT to_regclass('{relation}') IS NOT NULL")[0][0] != 't':
                continue
            wanted, got = source.query(f'SELECT count(*) FROM {relation}')[0][0], target_rows(e, f'SELECT count(*) FROM {relation}')[0][0]
            if wanted != got:
                mismatches.append(f'{relation}: {wanted} → {got}')
        report['verified'] = not mismatches
        report['mismatches'] = mismatches
        if mismatches:
            raise ImportError_('counts differ after the import: ' + '; '.join(mismatches))
        step(f'verified: {len(tables)} table(s), users, identities, buckets and objects match the source')
        return report
    finally:
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Import a Supabase project into a new, empty environment')
    parser.add_argument('environment')
    parser.add_argument('--dry-run', action='store_true', help='inspect only')
    parser.add_argument('--report', type=Path, help='where to write the JSON report')
    args = parser.parse_args(argv)
    os.chdir(lab.ROOT)
    started = time.time()
    try:
        settings = read_settings(sys.stdin)
        e = backup.resolve(args.environment)
        if e not in runtime.published_endpoints():
            raise ImportError_('the environment is not ready')
        report = run_import(e, settings, args.dry_run)
        status = 0
    except (ImportError_, backup.BackupError, ValueError, OSError, RuntimeError) as error:
        report = {'environment': args.environment, 'failed': str(error)}
        print(f'refused: {error}', file=sys.stderr)
        status = 1
    report['seconds'] = round(time.time() - started)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    for label in ('warnings', 'manual'):
        for line in report.get(label, []):
            print(f'{label[:-1] if label == "warnings" else "manual"}: {line}')
    if status == 0 and not args.dry_run:
        print('imported. Next: create a publishable key in the console; people sign in again with their old passwords.')
    return status


if __name__ == '__main__':
    sys.exit(main())
