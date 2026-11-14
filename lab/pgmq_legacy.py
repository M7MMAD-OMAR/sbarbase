"""Legacy PGMQ queues across backups, restores and the database-local repair.

Before the repair in lab/postgres/pgmq-after-create.sql, an environment's pgmq extension
belongs to the cluster's `postgres` role (the shape the unmodified upstream hook leaves) and its
queue tables and `pgmq.meta` are extension members. pg_dump skips extension members, so a
native logical backup of such an environment holds no queue and no queue metadata, even when
the tables are named explicitly (lab/pgmq-check.py reproduces this).

This module keeps those queues recoverable while the repair is deferred:

- capture() writes a supplement beside a backup: every queue's rows, read in the same exported
  snapshot as the native dump, as SQL that recreates the queues detached from the extension.
- archive_shape() reads a native archive's table of contents: an archive with the extension
  and without `pgmq.meta` data is legacy, and restore refuses it without a supplement unless
  the operator accepts the loss (lab/restore_cutover.py).
- qualify() is the only path that runs the repair on an environment with legacy queues: a
  verified backup with the supplement first, then the repair in one transaction, then a native
  backup verified to hold every queue.

Partitioned queues need pg_partman and are never captured: they are listed as unsupported, and
qualify() refuses them.
"""
import hashlib
import json
import os
import re
import subprocess

FILE = 'pgmq.sql'
FORMAT = 1
EXPORTED = re.compile(r'[0-9A-F]+-[0-9A-F]+-[0-9]+')
ENVIRONMENT = re.compile(r'e_[a-f0-9]{24}')
# What pgmq.format_table_name accepts and keeps as is; anything else is reported, never quoted in.
QUEUE = re.compile(r'[a-z0-9_]{1,47}')
COLUMN = re.compile(r'[a-z_][a-z0-9_]{0,62}')
SUPPORTED_VERSION = '1.5.1'

EXTENSION_SQL = """SELECT json_build_object('version', e.extversion, 'owner', r.rolname, 'schema', n.nspname)
FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_roles r ON r.oid = e.extowner
JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pgmq';"""
QUEUES_SQL = """SELECT coalesce(json_agg(json_build_object('name', m.queue_name, 'partitioned', m.is_partitioned,
  'unlogged', m.is_unlogged, 'created_at', m.created_at) ORDER BY m.queue_name), '[]'::json) FROM pgmq.meta m;"""
COLUMNS_SQL = """SELECT coalesce(json_object_agg(c.relname, (SELECT json_agg(a.attname ORDER BY a.attnum)
  FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)), '{}'::json)
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'pgmq' AND c.relkind IN ('r', 'p') AND pg_catalog.left(c.relname, 2) IN ('q_', 'a_');"""


class LegacyError(RuntimeError):
    pass


def in_snapshot(query, exported):
    """A read-only transaction on an exported snapshot around one query (all of it when None)."""
    if exported is None:
        return query
    if not EXPORTED.fullmatch(exported or ''):
        raise LegacyError('Invalid database snapshot identity')
    return (f"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '{exported}';\n"
            f'{query}\nCOMMIT;')


def extension(sql, e, exported=None):
    """{version, owner, schema} of the environment's pgmq extension, or None when absent."""
    text = sql(in_snapshot(EXTENSION_SQL, exported), e).strip()
    return json.loads(text) if text else None


def legacy(found):
    """Whether an extension reading is the shape the repair has not reconciled yet."""
    return bool(found) and found.get('owner') == 'postgres'


def inventory(sql, e, exported=None):
    """What a supplement would hold: {version, queues: [...], unsupported: [...]} for a legacy
    extension, None when there is no extension or it is already repaired."""
    found = extension(sql, e, exported)
    if not legacy(found):
        return None
    queues = json.loads(sql(in_snapshot(QUEUES_SQL, exported), e))
    columns = json.loads(sql(in_snapshot(COLUMNS_SQL, exported), e))
    supported, unsupported = [], []
    named = set()
    if found.get('version') != SUPPORTED_VERSION or found.get('schema') != 'pgmq':
        unsupported.append(f"pgmq {found.get('version')} in schema {found.get('schema')}")
        return {'version': found.get('version'), 'queues': [], 'unsupported': unsupported}
    for queue in queues:
        name = queue.get('name')
        named.update({f'q_{name}', f'a_{name}'})
        tables = [columns.get(f'q_{name}'), columns.get(f'a_{name}')]
        if not isinstance(name, str) or not QUEUE.fullmatch(name):
            unsupported.append(f'queue {name!r}: name outside the supported form')
        elif queue.get('partitioned'):
            unsupported.append(f'queue {name}: partitioned queues need pg_partman')
        elif not all(isinstance(table, list) and table and all(COLUMN.fullmatch(str(c)) for c in table) for table in tables):
            unsupported.append(f'queue {name}: its tables are missing or have unexpected columns')
        else:
            supported.append({'name': name, 'unlogged': bool(queue.get('unlogged')),
                              'created_at': queue.get('created_at'), 'queue_columns': tables[0],
                              'archive_columns': tables[1]})
    for table in sorted(set(columns) - named):
        unsupported.append(f'table pgmq.{table} belongs to no queue in pgmq.meta')
    return {'version': found['version'], 'queues': supported, 'unsupported': unsupported}


def quote(identifier):
    if not COLUMN.fullmatch(identifier) and not re.fullmatch(r'[aq]_[a-z0-9_]{1,47}(_msg_id_seq)?', identifier):
        raise LegacyError('Unexpected PGMQ identifier')
    return '"' + identifier + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def detach_sql(name):
    """Detach one queue's tables and sequence from the extension, as the repaired pgmq.create does."""
    names = ', '.join(literal(item) for item in (f'q_{name}', f'a_{name}', f'q_{name}_msg_id_seq'))
    return f"""DO $sbarbase_pgmq$
DECLARE member record;
BEGIN
  FOR member IN
    SELECT c.relkind, c.relname FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_depend d ON d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = c.oid
      AND d.refclassid = 'pg_catalog.pg_extension'::regclass AND d.deptype = 'e'
    JOIN pg_catalog.pg_extension x ON x.oid = d.refobjid AND x.extname = 'pgmq'
    WHERE n.nspname = 'pgmq' AND c.relname IN ({names})
  LOOP
    EXECUTE pg_catalog.format('ALTER EXTENSION pgmq DROP %s pgmq.%I',
      CASE WHEN member.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, member.relname);
  END LOOP;
END $sbarbase_pgmq$;
"""


def copy_out(psql, query, handle):
    """Stream one COPY TO STDOUT into handle; returns the rows written (one line per row)."""
    process = subprocess.Popen(psql, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(query.encode())
        process.stdin.close()
        rows, last = 0, b'\n'
        while chunk := process.stdout.read(1 << 20):
            handle.write(chunk)
            rows += chunk.count(b'\n')
            last = chunk[-1:]
        diagnostics = process.stderr.read()
        if process.wait(timeout=600) or diagnostics or last != b'\n':
            raise LegacyError('A legacy queue could not be read for the backup')
        return rows
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def capture(api, e, exported, path):
    """Write the supplement for a legacy extension into path / FILE, reading the backup's
    exported snapshot. Returns the manifest entry, or None when nothing needs it. The script
    recreates each supported queue in the restored database, detached and owned by the
    environment's developer role, then its rows, its sequence and its creation time."""
    if not ENVIRONMENT.fullmatch(e):
        raise LegacyError('Invalid environment')
    found = inventory(api.sql, e, exported)
    if found is None:
        return None
    owner = quote(e + '_developer')
    counts = []
    target = path / FILE
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(('-- Sbarbase legacy PGMQ supplement, format 1. Replayed by lab/restore_cutover.py.\n'
                      'SET client_min_messages = warning;\n').encode())
        for queue in found['queues']:
            name = queue['name']
            create = 'create_unlogged' if queue['unlogged'] else 'create_non_partitioned'
            handle.write((f'SELECT pgmq.{create}({literal(name)});\n' + detach_sql(name)
                          + f'ALTER TABLE pgmq.{quote("q_" + name)} OWNER TO {owner};\n'
                          + f'ALTER TABLE pgmq.{quote("a_" + name)} OWNER TO {owner};\n').encode())
            rows = {}
            for kind, columns in (('q', queue['queue_columns']), ('a', queue['archive_columns'])):
                listed = ', '.join(quote(column) for column in columns)
                table = f'pgmq.{quote(kind + "_" + name)} ({listed})'
                handle.write(f'COPY {table} FROM stdin;\n'.encode())
                handle.flush()
                rows[kind] = copy_out(api.psql(e), in_snapshot(f'COPY {table} TO STDOUT;', exported), handle)
                handle.write(b'\\.\n')
                expected = api.sql(in_snapshot(f'SELECT count(*) FROM pgmq.{quote(kind + "_" + name)};', exported), e)
                if str(rows[kind]) != expected.strip():
                    raise LegacyError('A legacy queue backup row count differs from its snapshot')
            sequence = api.sql(f'SELECT last_value, is_called FROM pgmq.{quote("q_" + name + "_msg_id_seq")};', e)
            last, called = sequence.strip().split('|')
            if not re.fullmatch(r'[0-9]+', last) or called not in ('t', 'f'):
                raise LegacyError('A legacy queue sequence could not be read')
            handle.write((f"SELECT pg_catalog.setval('pgmq.{quote('q_' + name + '_msg_id_seq')}', {last}, "
                          f"{'true' if called == 't' else 'false'});\n"
                          f'UPDATE pgmq.meta SET created_at = {literal(queue["created_at"])}::timestamptz '
                          f'WHERE queue_name = {literal(name)};\n').encode())
            counts.append({'name': name, 'messages': rows['q'], 'archived': rows['a'], 'unlogged': queue['unlogged']})
    data = target.read_bytes()
    return {'format': FORMAT, 'file': FILE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'version': found['version'], 'queues': counts, 'unsupported': found['unsupported']}


def not_kept(reason):
    """The manifest entry of a legacy backup whose queues could not be captured."""
    return {'format': FORMAT, 'file': None, 'queues': [], 'unsupported': [reason]}


def verify(entry, path):
    """The supplement named by a manifest entry, matching its digest; its bytes (None when the
    backup records that its queues could not be kept)."""
    if isinstance(entry, dict) and entry.get('format') == FORMAT and entry.get('file') is None \
            and entry.get('unsupported'):
        if (path / FILE).exists():
            raise LegacyError('A legacy PGMQ supplement is present that the manifest does not name')
        return None
    if not isinstance(entry, dict) or entry.get('format') != FORMAT or entry.get('file') != FILE:
        raise LegacyError('Unknown legacy PGMQ supplement format')
    file = path / FILE
    if not file.is_file() or file.is_symlink():
        raise LegacyError('The legacy PGMQ supplement is missing')
    data = file.read_bytes()
    if len(data) != entry.get('bytes') or hashlib.sha256(data).hexdigest() != entry.get('sha256'):
        raise LegacyError('The legacy PGMQ supplement does not match its digest')
    return data


def archive_shape(toc):
    """'none', 'native' or 'legacy' from `pg_restore -l` output: legacy when the archive creates
    the extension and holds no `pgmq.meta` data, so any queue it had is not in it."""
    lines = [line.split(';', 1)[1] if ';' in line else line for line in toc.splitlines() if not line.startswith(';')]
    if not any(re.search(r'\bEXTENSION - pgmq\b', line) for line in lines):
        return 'none'
    if any(re.search(r'\bTABLE DATA pgmq meta\b', line) for line in lines):
        return 'native'
    return 'legacy'


def restorable(shape, manifest, accept_loss=False):
    """Why an archive of this shape may not be restored, or None. A legacy archive needs a
    supplement with no unsupported queue, or the operator's explicit acceptance."""
    if shape != 'legacy' or accept_loss:
        return None
    entry = manifest.get('pgmq')
    if not entry:
        return ('This backup was taken before the PGMQ repair and holds no queue data, so any queue the '
                'environment had would be lost. Restore a later backup, or pass --accept-pgmq-loss if the '
                'environment had no queues')
    if entry.get('unsupported'):
        return ('This backup could not keep every PGMQ queue (' + '; '.join(entry['unsupported']) + '). '
                'Pass --accept-pgmq-loss to restore it without them')
    return None
