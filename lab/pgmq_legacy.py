"""Legacy PGMQ queues across backups, restores and the database-local repair.

Before the repair in lab/postgres/pgmq-after-create.sql, an environment's pgmq extension
belongs to the cluster's `postgres` role (the shape the unmodified upstream hook leaves) and its
queue tables and `pgmq.meta` are extension members. pg_dump skips extension members, so a
native logical backup of such an environment holds no queue and no queue metadata, even when
the tables are named explicitly (lab/pgmq-check.py reproduces this).

This module keeps those queues recoverable while the repair is deferred:

- capture() writes a supplement beside a backup: every queue's rows, read in the same exported
  snapshot as the native dump, as SQL that recreates the queues detached from the extension,
  with the exact grants, row level security and policies they had (format 2). pg_dump keeps
  only the grants of an extension member, never its row level security or policies, and its
  grants name tables the replay has not created yet: replay_omits() lists them so the restore
  leaves them out and the supplement sets them once the tables exist.
- archive_shape() reads a native archive's table of contents: an archive with the extension
  and without `pgmq.meta` data is legacy, and restore refuses it without a supplement unless
  the operator accepts the loss (lab/restore_cutover.py).
- qualify() is the only path that runs the repair on an environment with legacy queues: a
  verified backup with the supplement first, then the repair in one transaction, then a native
  backup verified to hold every queue.

Partitioned queues need pg_partman and are never captured: they are listed as unsupported, and
qualify() refuses them. A queue whose tables carry a trigger, a rule, an index pgmq does not create
or a publication keeps its messages, grants and policies in the supplement, but neither the dump
nor the supplement keeps that definition: it is listed as unsupported too, so a restore needs
--accept-pgmq-loss and qualify() refuses it. A queue dropped or created again while the backup
reads it is left out of that backup and listed the same way, and also under `changed`: that is
a passing state, not a shape, so lab/backup.py takes the backup again from a new snapshot a few
times, and a run whose queues still changed fails (an upgrade then changes nothing).

Every backup that lists something unsupported counts apart in rotation (lab/backup.py prune), so
it never takes the place of an older backup that holds every queue.

Restored queues are owned by the environment's developer role. Grants the original owner (the
cluster's `postgres` role on a legacy extension) held as owner are not recreated, and every grant
is recreated by the restoring session, so the original grantor of a grant passed on WITH GRANT
OPTION is not kept.

A format 1 supplement (release 0.13.1) holds no grants or row level security. Its queues come
back closed: only the developer role, which owns them, can reach them until the grants are
applied again; restore_notice() says so.
"""
import hashlib
import json
import os
import re
import subprocess

FILE = 'pgmq.sql'
FORMAT = 2
FORMATS = (1, 2)
EXPORTED = re.compile(r'[0-9A-F]+-[0-9A-F]+-[0-9]+')
ENVIRONMENT = re.compile(r'e_[a-f0-9]{24}')
# What pgmq.format_table_name accepts and keeps as is; anything else is reported, never quoted in.
QUEUE = re.compile(r'[a-z0-9_]{1,47}')
COLUMN = re.compile(r'[a-z_][a-z0-9_]{0,62}')
SUPPORTED_VERSION = '1.5.1'
# The columns pgmq 1.5.1 gives a non-partitioned queue and its archive, in order. A restored queue
# is created by pgmq itself, so only these columns can be written back into it.
QUEUE_COLUMNS = ('msg_id', 'read_ct', 'enqueued_at', 'vt', 'message', 'headers')
ARCHIVE_COLUMNS = ('msg_id', 'read_ct', 'enqueued_at', 'archived_at', 'vt', 'message', 'headers')

EXTENSION_SQL = """SELECT json_build_object('version', e.extversion, 'owner', r.rolname, 'schema', n.nspname)
FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_roles r ON r.oid = e.extowner
JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pgmq';"""
QUEUES_SQL = """SELECT coalesce(json_agg(json_build_object('name', m.queue_name, 'partitioned', m.is_partitioned,
  'unlogged', m.is_unlogged, 'created_at', m.created_at) ORDER BY m.queue_name), '[]'::json) FROM pgmq.meta m;"""
COLUMNS_SQL = """SELECT coalesce(json_object_agg(c.relname, (SELECT json_agg(a.attname ORDER BY a.attnum)
  FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)), '{}'::json)
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'pgmq' AND c.relkind IN ('r', 'p') AND pg_catalog.left(c.relname, 2) IN ('q_', 'a_');"""


# The indexes pgmq 1.5.1 creates for a non-partitioned queue (pgmq-check.py compares a real one).
def expected_indexes(name):
    return {f'q_{name}_pkey', f'q_{name}_vt_idx', f'a_{name}_pkey', f'archived_at_idx_{name}'}


RELATIONS = "('q_' || {name}, 'a_' || {name}, 'q_' || {name} || '_msg_id_seq')"

# Every statement read in the snapshot with an empty search_path, so expressions come out
# schema qualified the way pg_dump writes them. Quoting is the server's own.
ACCESS_SQL = """SET LOCAL search_path = '';
WITH rel AS (
  SELECT c.oid, c.relname, c.relkind, c.relowner, c.relacl, c.relrowsecurity, c.relforcerowsecurity
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'pgmq' AND c.relname IN {relations}),
who AS (SELECT 0::oid AS oid, 'PUBLIC'::text AS name
        UNION ALL SELECT r.oid, pg_catalog.quote_ident(r.rolname) FROM pg_catalog.pg_roles r),
statements AS (
  SELECT 1 AS step, r.relname AS rel, pg_catalog.format('GRANT %s ON %s pgmq.%I TO %s%s;', a.privilege_type,
    CASE r.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, r.relname, w.name,
    CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END) AS statement, a.grantee
  FROM rel r CROSS JOIN LATERAL pg_catalog.aclexplode(r.relacl) a JOIN who w ON w.oid = a.grantee
  WHERE a.grantee <> r.relowner
  UNION ALL
  SELECT 2, r.relname, pg_catalog.format('GRANT %s (%I) ON TABLE pgmq.%I TO %s%s;', a.privilege_type, t.attname,
    r.relname, w.name, CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END), a.grantee
  FROM rel r JOIN pg_catalog.pg_attribute t ON t.attrelid = r.oid AND t.attnum > 0 AND NOT t.attisdropped
  CROSS JOIN LATERAL pg_catalog.aclexplode(t.attacl) a JOIN who w ON w.oid = a.grantee
  WHERE a.grantee <> r.relowner
  UNION ALL
  SELECT 3, r.relname, pg_catalog.format('ALTER TABLE pgmq.%I ENABLE ROW LEVEL SECURITY;', r.relname), NULL
  FROM rel r WHERE r.relrowsecurity
  UNION ALL
  SELECT 4, r.relname, pg_catalog.format('ALTER TABLE pgmq.%I FORCE ROW LEVEL SECURITY;', r.relname), NULL
  FROM rel r WHERE r.relforcerowsecurity
  UNION ALL
  SELECT 5, r.relname, pg_catalog.format('CREATE POLICY %I ON pgmq.%I AS %s FOR %s TO %s%s%s;', p.polname, r.relname,
    CASE WHEN p.polpermissive THEN 'PERMISSIVE' ELSE 'RESTRICTIVE' END,
    CASE p.polcmd WHEN 'r' THEN 'SELECT' WHEN 'a' THEN 'INSERT' WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' ELSE 'ALL' END,
    (SELECT pg_catalog.string_agg(w.name, ', ' ORDER BY w.name) FROM pg_catalog.unnest(p.polroles) g JOIN who w ON w.oid = g),
    CASE WHEN p.polqual IS NULL THEN '' ELSE ' USING (' || pg_catalog.pg_get_expr(p.polqual, p.polrelid) || ')' END,
    CASE WHEN p.polwithcheck IS NULL THEN '' ELSE ' WITH CHECK (' || pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) || ')' END),
    NULL
  FROM rel r JOIN pg_catalog.pg_policy p ON p.polrelid = r.oid)
SELECT pg_catalog.json_build_object(
  'statements', coalesce((SELECT pg_catalog.json_agg(s.statement ORDER BY s.step, s.rel, s.statement) FROM statements s), '[]'::json),
  'roles', coalesce((SELECT pg_catalog.json_agg(DISTINCT r.rolname ORDER BY r.rolname) FROM pg_catalog.pg_roles r
    WHERE r.oid IN (SELECT s.grantee FROM statements s WHERE s.grantee IS NOT NULL)
       OR r.oid IN (SELECT g FROM rel x JOIN pg_catalog.pg_policy p ON p.polrelid = x.oid
                    CROSS JOIN LATERAL pg_catalog.unnest(p.polroles) g)), '[]'::json));"""

# What neither the dump nor the supplement keeps on a legacy queue table.
EXTRAS_SQL = """SELECT pg_catalog.json_build_object(
  'triggers', coalesce((SELECT pg_catalog.json_agg(c.relname || '.' || t.tgname ORDER BY 1)
    FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'pgmq' AND NOT t.tgisinternal AND c.relname IN {relations}), '[]'::json),
  'rules', coalesce((SELECT pg_catalog.json_agg(c.relname || '.' || w.rulename ORDER BY 1)
    FROM pg_catalog.pg_rewrite w JOIN pg_catalog.pg_class c ON c.oid = w.ev_class
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'pgmq' AND c.relname IN {relations}), '[]'::json),
  'indexes', coalesce((SELECT pg_catalog.json_agg(i.relname ORDER BY 1)
    FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid
    JOIN pg_catalog.pg_class c ON c.oid = x.indrelid JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'pgmq' AND c.relname IN {relations}), '[]'::json),
  'constraints', coalesce((SELECT pg_catalog.json_agg(c.relname || '.' || k.conname ORDER BY 1)
    FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid = k.conrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'pgmq' AND c.relname IN {relations}
      AND k.conname NOT IN ('q_' || {name} || '_pkey', 'a_' || {name} || '_pkey')), '[]'::json),
  'publications', coalesce((SELECT pg_catalog.json_agg(p.pubname || ':' || c.relname ORDER BY 1)
    FROM pg_catalog.pg_publication_rel r JOIN pg_catalog.pg_publication p ON p.oid = r.prpubid
    JOIN pg_catalog.pg_class c ON c.oid = r.prrelid JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'pgmq' AND c.relname IN {relations}), '[]'::json));"""


class LegacyError(RuntimeError):
    pass


class Changed(LegacyError):
    """One queue's tables were dropped or created again since the snapshot."""


def relations(name):
    return RELATIONS.format(name=literal(name))


def closed_sql(name):
    """Revoke every grant a restored queue relation has beyond its owner's: the replay's default
    privileges may otherwise give the new tables access the originals never had."""
    return f"""DO $sbarbase_pgmq$
DECLARE grant_row record;
BEGIN
  FOR grant_row IN
    SELECT DISTINCT c.relkind, c.relname, a.grantee FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
    WHERE n.nspname = 'pgmq' AND c.relname IN {relations(name)} AND a.grantee <> c.relowner
  LOOP
    EXECUTE pg_catalog.format('REVOKE ALL ON %s pgmq.%I FROM %s CASCADE',
      CASE WHEN grant_row.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, grant_row.relname,
      CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
           ELSE pg_catalog.quote_ident(pg_catalog.pg_get_userbyid(grant_row.grantee)) END);
  END LOOP;
END $sbarbase_pgmq$;
"""


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
        elif not all(isinstance(table, list) and all(COLUMN.fullmatch(str(c)) for c in table) and set(standard) <= set(table)
                     for table, standard in zip(tables, (QUEUE_COLUMNS, ARCHIVE_COLUMNS))):
            unsupported.append(f'queue {name}: its tables are missing or lack a column pgmq {SUPPORTED_VERSION} creates')
        else:
            # Its messages are kept either way; a definition neither the dump nor the supplement
            # keeps is still listed, so a restore of it is never silent.
            extras = [f'column {kind}_{name}.{column}' for kind, table, standard
                      in (('q', tables[0], QUEUE_COLUMNS), ('a', tables[1], ARCHIVE_COLUMNS))
                      for column in table if column not in standard]
            if extras := extras + unkept(sql, e, exported, name):
                unsupported.append(f'queue {name}: ' + '; '.join(extras)
                                   + ' would not be kept; its messages are kept')
            supported.append({'name': name, 'unlogged': bool(queue.get('unlogged')),
                              'created_at': queue.get('created_at'),
                              'queue_columns': [column for column in tables[0] if column in QUEUE_COLUMNS],
                              'archive_columns': [column for column in tables[1] if column in ARCHIVE_COLUMNS]})
    for table in sorted(set(columns) - named):
        unsupported.append(f'table pgmq.{table} belongs to no queue in pgmq.meta')
    return {'version': found['version'], 'queues': supported, 'unsupported': unsupported}


def unkept(sql, e, exported, name):
    """What a queue's tables carry that neither the dump nor the supplement keeps."""
    found = json.loads(sql(in_snapshot(EXTRAS_SQL.format(relations=relations(name), name=literal(name)), exported), e))
    extras = [f'trigger {item}' for item in found['triggers']] + [f'rule {item}' for item in found['rules']]
    extras += [f'constraint {item}' for item in found['constraints']]
    extras += [f'index {item}' for item in sorted(set(found['indexes']) - expected_indexes(name))]
    extras += [f'publication {item}' for item in found['publications']]
    return extras


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


IDENTITY_SQL = """SELECT coalesce(pg_catalog.json_object_agg(c.relname, c.oid::text), '{{}}'::json)
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'pgmq' AND c.relname IN {relations};"""


def identity(api, e, exported, name):
    """{relation: oid} of a queue's tables and sequence, in the snapshot or (None) as they are now."""
    return json.loads(api.sql(in_snapshot(IDENTITY_SQL.format(relations=relations(name)), exported), e))


def moved(api, e, exported, name):
    """Whether a queue's relations differ now from the snapshot's; False when that cannot be read."""
    try:
        return identity(api, e, exported, name) != identity(api, e, None, name)
    except Exception:
        return False


def unchanged(api, e, exported, name):
    """Refuse a queue whose tables or sequence were dropped or created again since the snapshot:
    the supplement names them, so it would read other relations than the dump's."""
    if identity(api, e, exported, name) != identity(api, e, None, name):
        raise Changed(f'The legacy queue {name} changed during the backup; take the backup again')


def sequence_value(api, e, exported, name):
    """(value, is_called) for a restored queue sequence: never below the highest message id the
    snapshot holds. A sequence is not transactional, so it is read live; a queue dropped and
    created again during the backup can have a younger sequence, and a dropped one none."""
    highest = api.sql(in_snapshot(f'SELECT greatest((SELECT max(msg_id) FROM pgmq.{quote("q_" + name)}), '
                                  f'(SELECT max(msg_id) FROM pgmq.{quote("a_" + name)}), 0);', exported), e).strip()
    live = api.sql("SELECT coalesce(last_value::text, '') || '|' || start_value FROM pg_catalog.pg_sequences "
                   f"WHERE schemaname = 'pgmq' AND sequencename = {literal('q_' + name + '_msg_id_seq')};", e).strip()
    if not re.fullmatch(r'[0-9]+', highest) or live and not re.fullmatch(r'[0-9]*\|[0-9]+', live):
        raise LegacyError('A legacy queue sequence could not be read')
    last, start = live.split('|') if live else ('', '1')
    if last and int(last) >= int(highest):
        return int(last), True
    if int(highest) > 0:
        return int(highest), True
    return int(start), False


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
    counts, roles, unsupported, changed = [], set(), list(found['unsupported']), []
    target = path / FILE
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write((f'-- Sbarbase legacy PGMQ supplement, format {FORMAT}. Replayed by lab/restore_cutover.py.\n'
                      'SET client_min_messages = warning;\n').encode())
        for queue in found['queues']:
            # A queue that changed while it was read is cut back out of the file, and only it.
            handle.flush()
            start = handle.tell()
            try:
                counts.append(capture_queue(api, e, exported, queue, owner, handle, roles))
            except Exception as error:
                # A read that failed because the queue was dropped in between is that change too;
                # any other failure fails the whole supplement.
                if not isinstance(error, Changed) and not moved(api, e, exported, queue['name']):
                    raise
                handle.flush()
                handle.seek(start)
                handle.truncate()
                unsupported.append(f"queue {queue['name']}: dropped or created again during the backup, "
                                   'its messages are not in this backup')
                changed.append(queue['name'])
    data = target.read_bytes()
    return {'format': FORMAT, 'file': FILE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'version': found['version'], 'queues': counts, 'unsupported': unsupported,
            'changed': changed, 'roles': sorted(roles)}


def capture_queue(api, e, exported, queue, owner, handle, roles):
    """Append one queue to the supplement; its roles join `roles` only once it is all written."""
    name = queue['name']
    create = 'create_unlogged' if queue['unlogged'] else 'create_non_partitioned'
    unchanged(api, e, exported, name)
    access = json.loads(api.sql(in_snapshot(ACCESS_SQL.format(relations=relations(name)), exported), e))
    if not isinstance(access.get('statements'), list) or not isinstance(access.get('roles'), list):
        raise LegacyError('The access of a legacy queue could not be read')
    handle.write((f'SELECT pgmq.{create}({literal(name)});\n' + detach_sql(name)
                  + f'ALTER TABLE pgmq.{quote("q_" + name)} OWNER TO {owner};\n'
                  + f'ALTER TABLE pgmq.{quote("a_" + name)} OWNER TO {owner};\n'
                  + closed_sql(name) + ''.join(statement + '\n' for statement in access['statements'])
                  ).encode())
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
    last, called = sequence_value(api, e, exported, name)
    # Still the same relations once read: a drop in between would have changed what the
    # names resolve to while their rows were copied.
    unchanged(api, e, exported, name)
    handle.write((f"SELECT pg_catalog.setval('pgmq.{quote('q_' + name + '_msg_id_seq')}', {last}, "
                  f"{'true' if called else 'false'});\n"
                  f'UPDATE pgmq.meta SET created_at = {literal(queue["created_at"])}::timestamptz '
                  f'WHERE queue_name = {literal(name)};\n').encode())
    roles.update(access['roles'])
    return {'name': name, 'messages': rows['q'], 'archived': rows['a'], 'unlogged': queue['unlogged']}


def not_kept(reason):
    """The manifest entry of a legacy backup whose queues could not be captured. `failed` marks
    an error, which fails the backup run, from a known unsupported shape, which is recorded."""
    return {'format': FORMAT, 'file': None, 'queues': [], 'unsupported': [reason], 'failed': True}


def verify(entry, path):
    """The supplement named by a manifest entry, matching its digest; its bytes (None when the
    backup records that its queues could not be kept)."""
    if isinstance(entry, dict) and entry.get('format') in FORMATS and entry.get('file') is None \
            and entry.get('unsupported'):
        if (path / FILE).exists():
            raise LegacyError('A legacy PGMQ supplement is present that the manifest does not name')
        return None
    if not isinstance(entry, dict) or entry.get('format') not in FORMATS or entry.get('file') != FILE:
        raise LegacyError('Unknown legacy PGMQ supplement format')
    if entry['format'] == 2 and not (isinstance(entry.get('roles'), list)
                                     and all(isinstance(role, str) for role in entry['roles'])):
        raise LegacyError('The legacy PGMQ supplement roles are malformed')
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
        return ('This backup does not keep everything its PGMQ queues had (' + '; '.join(entry['unsupported']) + '). '
                'Restore an older backup that keeps every queue (lab/backup.py list), or pass --accept-pgmq-loss '
                'to restore this one without what is listed')
    return None


# Archive entries that hang on one pgmq relation by name; pg_dump writes these for a table it dumps.
DEPENDENT = (re.compile(r'\s*\d+ \d+ (?:CONSTRAINT|FK CONSTRAINT|RULE|TRIGGER|POLICY|ROW SECURITY) pgmq ([a-z0-9_]+)\b'),
             re.compile(r'\s*\d+ \d+ COMMENT pgmq (?:TABLE|COLUMN|SEQUENCE) ([a-z0-9_]+)\b'),
             re.compile(r'\s*\d+ \d+ PUBLICATION TABLE pgmq \S+ ([a-z0-9_]+)\b'))


def replay_dependents(toc):
    """Archive entries of a legacy archive that hang on a pgmq relation it does not create, sorted.

    pg_dump in the pinned image writes none for an extension member (lab/pgmq-access-check.py
    proves it for a trigger, rule, index, constraint, publication, policy and row level security):
    only grants, which replay_omits leaves out. Another pg_dump that wrote one would stop the
    replay part way, so a restore refuses such an archive before it changes anything."""
    defined, dependent = set(), set()
    for line in toc.splitlines():
        if line.startswith(';') or ';' not in line:
            continue
        entry = line.split(';', 1)[1]
        if match := re.match(r'\s*\d+ \d+ (?:TABLE|SEQUENCE) pgmq ([a-z0-9_]+) ', entry):
            defined.add(match[1])
            continue
        for pattern in DEPENDENT:
            if match := pattern.match(entry):
                dependent.add(match[1])
                break
    return sorted(dependent - defined)


def replay_omits(toc):
    """The pgmq queue relations a legacy archive grants on without defining them: extension
    members pg_dump leaves out but whose grants it keeps. The replay runs before the supplement
    creates them, so a restore leaves those grants out; a format 2 supplement sets the exact
    ones, and the queues of an older one come back closed."""
    defined, granted = set(), set()
    for line in toc.splitlines():
        if line.startswith(';') or ';' not in line:
            continue
        entry = line.split(';', 1)[1]
        if match := re.match(r'\s*\d+ \d+ (?:TABLE|SEQUENCE) pgmq ([a-z0-9_]+) ', entry):
            defined.add(match[1])
        elif match := re.match(r'\s*\d+ \d+ ACL pgmq (?:TABLE|SEQUENCE|COLUMN) ([a-z0-9_]+)[. ]', entry):
            granted.add(match[1])
    return frozenset(name for name in granted - defined if re.fullmatch(r'[aq]_[a-z0-9_]{1,59}', name))


def restore_notice(manifest):
    """What an operator must do after restoring a legacy archive, or None."""
    entry = manifest.get('pgmq') if isinstance(manifest, dict) else None
    if isinstance(entry, dict) and entry.get('format') == 1 and entry.get('queues'):
        return ('This backup was taken by release 0.13.1, which did not record the grants, row level '
                'security or policies of its PGMQ queues. The queues and their messages are restored, '
                'closed to every role but the developer role: apply their grants and policies again')
    return None
