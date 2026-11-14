"""Disposable real check: legacy PGMQ queues keep their rows, grants, row level security and
policies through a backup and a restore (lab/pgmq_legacy.py, lab/restore_sql.py).

Starts one database container from the pinned image on an internal network, never touches an
existing database, and removes everything it made. Writes docs/evidence/pgmq-access-<date>.json
with --evidence.

What it covers, on real PostgreSQL and real pgmq 1.5.1:
- the 0.13.1 failure: replaying a legacy archive whose queue has a grant stops at that grant;
- a format 2 supplement restores the exact queue grants (table, archive, sequence, column),
  row level security with FORCE, a permissive and a restrictive policy, and nothing more;
- row level security filters rows for `authenticated`, and `pgmq.send` works through the grants;
- a queue dropped and created again during the backup is left out of it and listed, never copied
  from another table, while the other queues are kept;
- a restored sequence continues past every id the source handed out while the backup ran;
- an unlogged queue comes back unlogged with its rows;
- a queue whose tables carry a trigger, a rule, an extra index, constraint or column, or a
  publication keeps its messages; the lost definition is listed, and restore refuses it without the
  flag; pg_dump writes no archive entry for any of them, nor for a queue's policies or row level
  security, so the archive replays (lab/pgmq_legacy.py replay_dependents);
- `--accept-pgmq-loss` without a supplement replays without error;
- a format 1 supplement (the real 0.13.1 code) restores the rows with the queues closed;
- the qualified repair keeps all of it, and a native dump then holds the policies.

It plans the archive with lab/restore_cutover.archive_sql and replays it, the supplement and the
database metadata in one HeldSession, as the cutover does; not the fencing, renames, journal or
services around it.
"""
import datetime
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import durable_runtime as runtime
import pgmq_legacy
import restore_cutover
import studio
from restore_session import HeldSession, SessionError

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'sbarbase-pgmq-access-' + uuid.uuid4().hex[:10]
DB, NET = PREFIX + '-db', PREFIX + '-net'
E = 'e_' + 'c' * 24
PASSWORD = 'isolated-pgmq-access-fixture-only'
RESULTS = []
# Every file the check writes, removed at the end (never /tmp: other sessions may clear it).
WORK = Path(tempfile.mkdtemp(prefix='sbarbase-pgmq-access-', dir=Path.home() / '.cache'))


def docker(*args, data=None, check=True, binary=False):
    result = subprocess.run(['docker', *args], input=data, capture_output=True, text=not binary, timeout=180)
    if check and result.returncode:
        raise RuntimeError(result.stderr if not binary else result.stderr.decode())
    return result


def psql(database, user='supabase_admin'):
    # TCP, as lab/pgmq-check.py: the image's first start answers on the socket before it restarts.
    return ['docker', 'exec', '-i', '-e', 'PGPASSWORD=' + PASSWORD, DB, 'psql', '-X', '-h', '127.0.0.1', '-qAt',
            '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database]


def sql(text, database=E, check=True):
    result = subprocess.run(psql(database), input=text, capture_output=True, text=True, timeout=180)
    if check and result.returncode:
        raise RuntimeError(result.stderr)
    return result


def value(text, database=E):
    return sql(text, database).stdout.strip()


def record(name, passed, detail=''):
    RESULTS.append({'check': name, 'passed': bool(passed), **({'detail': detail} if detail else {})})
    print(('PASS ' if passed else 'FAIL ') + name + (f': {detail}' if detail and not passed else ''))
    if not passed:
        raise SystemExit(1)


class Api:
    @staticmethod
    def sql(query, database):
        return value(query, database)

    @staticmethod
    def psql(database):
        return psql(database)


ACCESS = """SELECT coalesce(json_agg(row ORDER BY row), '[]'::json) FROM (
  SELECT json_build_array(c.relname, coalesce(r.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable)::text AS row
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a
  LEFT JOIN pg_roles r ON r.oid = a.grantee
  WHERE n.nspname = 'pgmq' AND c.relname ~ '^(q|a)_' AND a.grantee <> c.relowner
  UNION ALL
  SELECT json_build_array(c.relname || '.' || t.attname, coalesce(r.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable)::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute t ON t.attrelid = c.oid AND t.attnum > 0 AND NOT t.attisdropped
  CROSS JOIN LATERAL aclexplode(t.attacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
  WHERE n.nspname = 'pgmq' AND c.relname ~ '^(q|a)_' AND a.grantee <> c.relowner
  UNION ALL
  SELECT json_build_array(c.relname, 'rls', c.relrowsecurity, c.relforcerowsecurity)::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'pgmq' AND c.relname ~ '^(q|a)_' AND c.relkind = 'r'
  UNION ALL
  SELECT json_build_array(tablename, policyname, permissive, roles, cmd, qual, with_check)::text
  FROM pg_policies WHERE schemaname = 'pgmq') x;"""


def access(database):
    return json.loads(value(ACCESS, database))


class Shim:
    """The two parts of the backup API lab/restore_cutover.archive_sql uses."""
    BackupError = RuntimeError

    @staticmethod
    def run(argv, *, stdin=None, stdout=subprocess.PIPE, text=True):
        result = subprocess.run(argv, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text, timeout=600)
        if result.returncode:
            raise RuntimeError('pg_restore failed')
        return result


def stage(name, dump, omit, supplement=None):
    """Replay as lab/restore_cutover.py does: the archive planned by archive_sql, then in one
    HeldSession the replay, the supplement and the database metadata. Returns (plan, failure)."""
    archive = Path(tempfile.mkdtemp(prefix='sbarbase-pgmq-access-archive-', dir=WORK))
    (archive / 'database.dump').write_bytes(dump)
    with restore_cutover.archive_sql(Shim, {'db': DB}, archive, E, name, omit) as (plan, replay):
        sql(plan.create, 'postgres')
        diagnostic = ''
        try:
            with HeldSession(psql(name)) as held:
                held.execute('SET max_parallel_workers_per_gather=0; SET max_parallel_maintenance_workers=0;')
                try:
                    held.replay(replay)
                    if supplement is not None:
                        held.execute(supplement)
                    held.execute('\n'.join(plan.metadata))
                except SessionError:
                    diagnostic = held.private_diagnostic.decode(errors='replace')
                    raise
        except SessionError as error:
            return plan, diagnostic or str(error)
    return plan, None


def main():
    evidence = '--evidence' in sys.argv[1:]
    started = time.time()
    try:
        docker('network', 'create', '--internal', NET)
        image = json.loads((ROOT / 'lab/distro-image.lock.json').read_text())['id']
        docker('run', '-d', '--rm', '--name', DB, '--network', NET, '--label', 'io.sbarbase.owner=' + PREFIX,
               '-e', 'POSTGRES_PASSWORD=' + PASSWORD, image)
        for _ in range(120):
            if sql('SELECT 1;', 'postgres', check=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Database did not start')
        r = object.__new__(runtime.Runtime)
        sql('CREATE DATABASE ' + E + '; REVOKE CONNECT ON DATABASE ' + E + ' FROM PUBLIC; '
            'CREATE ROLE ' + E + '_auth NOLOGIN; CREATE ROLE ' + E + '_storage NOLOGIN;', 'postgres')
        sql('CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE SCHEMA extensions;')
        sql(r.developer_sql(E, PASSWORD))
        sql(r.developer_grants_sql(E))
        sql(studio.role_sql(E, PASSWORD))
        # The shape an installation made before the repair has: the extension owned by postgres.
        sql("""CREATE EXTENSION pgmq WITH SCHEMA pgmq;
SELECT pgmq.create('jobs'); SELECT pgmq.create('plain'); SELECT pgmq.create('hooked');
SELECT pgmq.create_unlogged('fast'); SELECT pgmq.send('fast', '{"n": 1}'); SELECT pgmq.send('fast', '{"n": 2}');
SELECT pgmq.send('jobs', '{"public": true, "n": 1}'); SELECT pgmq.send('jobs', '{"public": false, "n": 2}');
SELECT pgmq.send('jobs', '{"public": true, "n": 3}'); SELECT pgmq.archive('jobs', 3::bigint);
SELECT pgmq.send('plain', '{"n": 1}'); SELECT pgmq.send('plain', '{"n": 2}'); SELECT pgmq.send('plain', '{"n": 3}');
GRANT USAGE ON SCHEMA pgmq TO authenticated;
GRANT SELECT, INSERT, UPDATE ON pgmq.q_jobs TO authenticated;
GRANT SELECT ON pgmq.a_jobs TO authenticated WITH GRANT OPTION;
GRANT USAGE, SELECT ON SEQUENCE pgmq.q_jobs_msg_id_seq TO authenticated;
GRANT SELECT (message) ON pgmq.q_jobs TO anon;
ALTER TABLE pgmq.q_jobs ENABLE ROW LEVEL SECURITY; ALTER TABLE pgmq.q_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY readable ON pgmq.q_jobs FOR SELECT TO authenticated USING ((message ->> 'public')::boolean);
CREATE POLICY writable ON pgmq.q_jobs FOR INSERT TO authenticated WITH CHECK ((message ->> 'public')::boolean);
CREATE POLICY fresh ON pgmq.q_jobs AS RESTRICTIVE FOR ALL TO authenticated USING (read_ct < 5);
CREATE FUNCTION public.noop() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
CREATE TRIGGER noop BEFORE INSERT ON pgmq.q_hooked FOR EACH ROW EXECUTE FUNCTION public.noop();
SELECT pgmq.send('hooked', '{"n": 1}');
SELECT pgmq.create('indexed'); CREATE INDEX indexed_extra ON pgmq.q_indexed (enqueued_at);
SELECT pgmq.create('ruled'); CREATE RULE keep_rows AS ON DELETE TO pgmq.q_ruled WHERE false DO INSTEAD NOTHING;
SELECT pgmq.create('checked'); ALTER TABLE pgmq.q_checked ADD CONSTRAINT positive CHECK (read_ct >= 0);
SELECT pgmq.create('widened'); ALTER TABLE pgmq.q_widened ADD COLUMN tenant text;
SELECT pgmq.create('published'); CREATE PUBLICATION queues FOR TABLE pgmq.q_published;
SELECT pgmq.send(name, '{"n": 1}') FROM unnest(ARRAY['indexed', 'ruled', 'checked', 'widened', 'published']) name;
UPDATE pgmq.q_widened SET tenant = 'a';""")
        record('fixture is a legacy extension', pgmq_legacy.legacy(pgmq_legacy.extension(Api.sql, E)))
        container = json.loads(docker('inspect', DB).stdout)[0]['Id']
        studio.studio_extensions.install(lambda *args, data=None: docker(*args, data=data), container, image, PREFIX)
        original = access(E)
        record('fixture has queue grants, row level security and policies',
               any('readable' in row for row in original) and any('"anon"' in row for row in original))

        # The backup: native dump and supplement read in one exported snapshot.
        holder = subprocess.Popen(psql(E), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        holder.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n')
        holder.stdin.flush()
        exported = holder.stdout.readline().strip()
        # A queue dropped and created again after the snapshot: its name now resolves to another
        # table than the dump's, and the capture refuses instead of copying it.
        sql("SELECT pgmq.drop_queue('plain'); SELECT pgmq.create('plain');")
        raced = Path(tempfile.mkdtemp(prefix='sbarbase-pgmq-access-race-', dir=WORK))
        raced_entry = pgmq_legacy.capture(Api, E, exported, raced)
        raced_text = pgmq_legacy.verify(raced_entry, raced).decode()
        record('a queue dropped and created again during the backup is left out and listed; the others are kept',
               'plain' not in [queue['name'] for queue in raced_entry['queues']]
               and any(item.startswith('queue plain: dropped or created again') for item in raced_entry['unsupported'])
               and "'plain'" not in raced_text and 'jobs' in [queue['name'] for queue in raced_entry['queues']],
               json.dumps(raced_entry))
        holder.stdin.write('COMMIT;\n')
        holder.stdin.close()
        holder.wait(timeout=60)
        sql("SELECT pgmq.send('plain', '{\"n\": 1}'); SELECT pgmq.send('plain', '{\"n\": 2}'); "
            "SELECT pgmq.send('plain', '{\"n\": 3}');")
        holder = subprocess.Popen(psql(E), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        holder.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n')
        holder.stdin.flush()
        exported = holder.stdout.readline().strip()
        # A message sent after the snapshot moves the live sequence, not the saved rows.
        sql("SELECT pgmq.send('plain', '{\"n\": 99}');")
        dump = docker('exec', DB, 'pg_dump', '-U', 'supabase_admin', '-Fc', '--snapshot=' + exported, '-d', E,
                      binary=True).stdout
        folder = Path(tempfile.mkdtemp(prefix='sbarbase-pgmq-access-', dir=WORK))
        entry = pgmq_legacy.capture(Api, E, exported, folder)
        holder.stdin.write('COMMIT;\n')
        holder.stdin.close()
        holder.wait(timeout=60)
        supplement = pgmq_legacy.verify(entry, folder).decode()
        extras = ['checked', 'hooked', 'indexed', 'published', 'ruled', 'widened']
        record('supplement keeps every queue, those with definitions it cannot keep too, and names its roles',
               [queue['name'] for queue in entry['queues']] == sorted(['fast', 'jobs', 'plain', *extras])
               and {'anon', 'authenticated'} <= set(entry['roles']), json.dumps(entry))
        lost = ' | '.join(entry['unsupported'])
        record('each definition neither the dump nor the supplement keeps is listed, with its messages kept',
               all(word in lost for word in ('trigger q_hooked.noop', 'index indexed_extra', 'rule q_ruled.keep_rows',
                                             'constraint q_checked.positive', 'column q_widened.tenant',
                                             'publication queues:q_published'))
               and len(entry['unsupported']) == len(extras) and lost.count('its messages are kept') == len(extras), lost)
        record('restore refuses such a backup without the flag and takes it with the flag',
               pgmq_legacy.restorable('legacy', {'pgmq': entry}) is not None
               and pgmq_legacy.restorable('legacy', {'pgmq': entry}, accept_loss=True) is None)
        record('pgmq 1.5.1 creates exactly the indexes the check expects',
               not re.search(r'\bindex (?!indexed_extra\b)', lost), lost)
        toc = docker('exec', '-i', DB, 'pg_restore', '-l', data=dump, binary=True).stdout.decode()
        record('pg_dump writes no archive entry hanging on a queue table it leaves out (policy, row level '
               'security, trigger, rule, index, constraint, publication)',
               pgmq_legacy.replay_dependents(toc) == []
               and not re.search(r'(INDEX pgmq indexed_extra|PUBLICATION TABLE pgmq|POLICY pgmq|ROW SECURITY pgmq)', toc),
               '\n'.join(line for line in toc.splitlines() if 'pgmq' in line))
        omit = pgmq_legacy.replay_omits(toc)
        record('archive is legacy and its queue grants are listed for omission',
               pgmq_legacy.archive_shape(toc) == 'legacy'
               and {'q_jobs', 'a_jobs', 'q_jobs_msg_id_seq'} <= omit and 'meta' not in omit, sorted(omit))

        # 0.13.1: the archive replay stops at the first grant on a queue table it does not create.
        _, failed = stage(E + '_stage_old', dump, frozenset())
        record('0.13.1 replay of a legacy archive with a queue grant fails (the reviewed defect)',
               failed and re.search(r'relation "pgmq\.[aq]_jobs" does not exist', failed), str(failed)[-300:])
        sql('DROP DATABASE ' + E + '_stage_old;', 'postgres')

        # --accept-pgmq-loss without a supplement now replays.
        plan, failed = stage(E + '_stage_loss', dump, omit)
        record('accept-loss replay without a supplement succeeds', failed is None and plan.omitted >= 4,
               f'{plan.omitted} {failed}')
        sql('DROP DATABASE ' + E + '_stage_loss;', 'postgres')

        # Format 2: exact access.
        target = E + '_stage_new'
        _, failed = stage(target, dump, omit, supplement)
        record('format 2 supplement replays in the held restore session', failed is None, str(failed))
        restored = access(target)
        record('restored queue access equals the original, with nothing added',
               restored == original,
               json.dumps({'original': original, 'restored': restored}))
        rows = value("SELECT string_agg(message->>'n', ',' ORDER BY msg_id) FROM pgmq.q_jobs;"
                     "SELECT count(*) FROM pgmq.a_jobs; SELECT count(*) FROM pgmq.q_plain;", target).split()
        record('queue rows and archive rows are restored as the snapshot had them', rows == ['1,2', '1', '3'], rows)
        kept = value(' '.join(f'SELECT count(*) FROM pgmq.q_{name};' for name in extras), target).split()
        record('the messages of queues with definitions the backup cannot keep are restored', kept == ['1'] * len(extras), kept)
        fast = value("SELECT relpersistence FROM pg_class WHERE relname = 'q_fast';"
                     "SELECT count(*) FROM pgmq.q_fast; SELECT pgmq.send('fast', '{}');", target).split()
        record('an unlogged queue comes back unlogged with its rows and sequence', fast == ['u', '2', '3'], fast)
        seen = value("SET ROLE authenticated; SELECT string_agg(message->>'n', ',') FROM pgmq.q_jobs;", target)
        record('row level security filters rows for authenticated after restore', seen == '1', seen)
        sent = value("SET ROLE authenticated; SELECT pgmq.send('jobs', '{\"public\": true, \"n\": 4}');", target)
        record('pgmq.send works through the restored grants and policies', sent == '4', sent)
        denied = sql("SET ROLE authenticated; SELECT pgmq.send('jobs', '{\"public\": false}');", target, check=False)
        record('the restored insert policy still refuses a private message', denied.returncode != 0)
        plain = value("SELECT pgmq.send('plain', '{\"n\": 5}');", target)
        record('the restored sequence continues past every id handed out by the backup', plain == '5', plain)

        # The qualified repair a restore runs afterwards keeps it all; a native dump then holds it.
        sql(studio.studio_extensions.prepare_sql(E, repair=True), target)
        record('the repair keeps the restored access', access(target) == restored)
        native = docker('exec', DB, 'pg_dump', '-U', 'supabase_admin', '-d', target, '--schema=pgmq').stdout
        record('a native dump after the repair holds the policies and row level security',
               'CREATE POLICY readable ON pgmq.q_jobs' in native and 'ALTER TABLE ONLY pgmq.q_jobs FORCE ROW LEVEL SECURITY' in native
               and 'GRANT SELECT(message) ON TABLE pgmq.q_jobs TO anon' in native,
               '\n'.join(line for line in native.splitlines() if 'q_jobs' in line and not line.startswith('--')))
        sql('DROP DATABASE ' + target + ';', 'postgres')

        # Format 1, written by the real 0.13.1 code: the rows come back, the queues closed.
        old = Path(tempfile.mkdtemp(prefix='sbarbase-pgmq-access-old-', dir=WORK))
        source = subprocess.run(['git', 'show', '992be52:lab/pgmq_legacy.py'], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout
        (old / 'pgmq_legacy_0131.py').write_text(source)
        spec = importlib.util.spec_from_file_location('pgmq_legacy_0131', old / 'pgmq_legacy_0131.py')
        legacy_0131 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_0131)
        # 0.13.1 copied an added column it could not write back; the other shapes it read as they were.
        sql("SELECT pgmq.drop_queue(name) FROM unnest(ARRAY['hooked', 'indexed', 'ruled', 'checked', 'widened', "
            "'published']) name;")
        holder = subprocess.Popen(psql(E), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        holder.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n')
        holder.stdin.flush()
        exported = holder.stdout.readline().strip()
        dump_old = docker('exec', DB, 'pg_dump', '-U', 'supabase_admin', '-Fc', '--snapshot=' + exported, '-d', E,
                          binary=True).stdout
        entry_old = legacy_0131.capture(Api, E, exported, old)
        holder.stdin.write('COMMIT;\n')
        holder.stdin.close()
        holder.wait(timeout=60)
        closed = pgmq_legacy.verify(entry_old, old).decode() + ''.join(
            pgmq_legacy.closed_sql(queue['name']) for queue in entry_old['queues'])
        toc_old = docker('exec', '-i', DB, 'pg_restore', '-l', data=dump_old, binary=True).stdout.decode()
        target = E + '_stage_f1'
        _, failed = stage(target, dump_old, pgmq_legacy.replay_omits(toc_old), closed)
        record('format 1 supplement replays in the held restore session', failed is None, str(failed))
        left = [row for row in access(target) if '"rls"' not in row]
        record('format 1 restore brings the rows back with no grant or policy on the queues',
               entry_old['format'] == 1 and left == [] and value('SELECT count(*) FROM pgmq.q_jobs;', target) == '2',
               json.dumps(left))
        record('format 1 restore says the queues come back closed',
               'closed' in (pgmq_legacy.restore_notice({'pgmq': entry_old}) or ''))
        sql('DROP DATABASE ' + target + ';', 'postgres')
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
        docker('rm', '-f', DB, check=False)
        docker('network', 'rm', NET, check=False)
    if evidence:
        path = ROOT / 'docs/evidence' / f'pgmq-access-{datetime.date.today().isoformat()}.json'
        path.write_text(json.dumps({
            'scope': 'Disposable container from lab/distro-image.lock.json with pgmq 1.5.1; archive, supplement '
                     'and metadata replayed in one session through lab/restore_sql.py. Not the fenced cutover, '
                     'not a retained or production database.',
            'image': json.loads((ROOT / 'lab/distro-image.lock.json').read_text())['tag'],
            'seconds': round(time.time() - started, 1), 'checks': RESULTS}, indent=2) + '\n')
        print('evidence written to ' + str(path.relative_to(ROOT)))


if __name__ == '__main__':
    main()
