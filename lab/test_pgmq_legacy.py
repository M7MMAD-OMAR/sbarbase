"""Legacy PGMQ queues stay recoverable while the repair is deferred (lab/pgmq_legacy.py)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import backup
import pgmq_legacy

E = 'e_' + 'a' * 24
LEGACY_TOC = """;
; Archive created at 2026-11-14
3; 3079 16384 EXTENSION - pgmq
4; 0 0 COMMENT - EXTENSION pgmq
5; 0 16400 TABLE DATA public items postgres
"""
NATIVE_TOC = LEGACY_TOC + '6; 0 16500 TABLE DATA pgmq meta e_developer\n7; 0 16510 TABLE DATA pgmq q_jobs e_developer\n'


class FakeDatabase:
    """Answers the inventory queries the way a legacy database does."""

    def __init__(self, owner='postgres', queues=None, columns=None, version='1.5.1', extras=None, access=None,
                 sequence='7|1', highest='2'):
        self.owner, self.version = owner, version
        self.extras = extras or {}
        self.access = access or {'statements': [], 'roles': []}
        self.sequence, self.highest = sequence, highest
        self.moved = False
        self.queues = [{'name': 'jobs', 'partitioned': False, 'unlogged': False, 'created_at': '2026-11-14T10:00:00+00:00'}] \
            if queues is None else queues
        self.columns = {'q_jobs': ['msg_id', 'read_ct', 'enqueued_at', 'vt', 'message', 'headers'],
                        'a_jobs': ['msg_id', 'read_ct', 'enqueued_at', 'archived_at', 'vt', 'message', 'headers']} \
            if columns is None else columns
        self.seen = []

    def sql(self, query, database):
        self.seen.append(query)
        if 'c.oid::text' in query:
            moved = self.moved and 'SET TRANSACTION SNAPSHOT' not in query
            return json.dumps({'q_jobs': '9' if moved else '1', 'a_jobs': '2', 'q_jobs_msg_id_seq': '3'})
        if 'pg_extension e JOIN' in query:
            return '' if self.owner is None else json.dumps({'version': self.version, 'owner': self.owner, 'schema': 'pgmq'})
        if 'FROM pgmq.meta m' in query:
            return json.dumps(self.queues)
        if 'json_object_agg' in query:
            return json.dumps(self.columns)
        if "'triggers'" in query:
            indexes = ['q_jobs_pkey', 'q_jobs_vt_idx', 'a_jobs_pkey', 'archived_at_idx_jobs']
            return json.dumps({'triggers': [], 'rules': [], 'indexes': indexes, 'publications': [], **self.extras})
        if "'statements'" in query:
            return json.dumps(self.access)
        if 'pg_sequences' in query:
            return self.sequence
        if 'greatest(' in query:
            return self.highest
        if 'count(*)' in query:
            return '2' if 'q_jobs' in query else '0'
        raise AssertionError(query)


class InventoryTests(unittest.TestCase):
    def test_absent_or_repaired_extension_needs_no_supplement(self):
        self.assertIsNone(pgmq_legacy.inventory(FakeDatabase(owner=None).sql, E))
        self.assertIsNone(pgmq_legacy.inventory(FakeDatabase(owner=E + '_developer').sql, E))

    def test_legacy_queues_are_listed_with_their_columns(self):
        found = pgmq_legacy.inventory(FakeDatabase().sql, E, '00000003-00000002-1')
        self.assertEqual([queue['name'] for queue in found['queues']], ['jobs'])
        self.assertEqual(found['unsupported'], [])

    def test_snapshot_reads_import_the_exported_snapshot(self):
        database = FakeDatabase()
        pgmq_legacy.inventory(database.sql, E, '00000003-00000002-1')
        self.assertTrue(all("SET TRANSACTION SNAPSHOT '00000003-00000002-1'" in query for query in database.seen))
        with self.assertRaises(pgmq_legacy.LegacyError):
            pgmq_legacy.inventory(database.sql, E, "x'; DROP TABLE y; --")

    def test_partitioned_odd_names_and_orphan_tables_are_unsupported(self):
        queues = [{'name': 'big', 'partitioned': True, 'unlogged': False, 'created_at': 'x'},
                  {'name': 'Bad"Name', 'partitioned': False, 'unlogged': False, 'created_at': 'x'}]
        found = pgmq_legacy.inventory(FakeDatabase(queues=queues, columns={'q_stray': ['msg_id']}).sql, E)
        self.assertEqual(found['queues'], [])
        self.assertEqual(len(found['unsupported']), 3)

    def test_definitions_neither_dump_nor_supplement_keeps_are_unsupported(self):
        for extras, word in (({'triggers': ['q_jobs.notify']}, 'trigger q_jobs.notify'),
                             ({'rules': ['q_jobs.r']}, 'rule q_jobs.r'),
                             ({'indexes': ['q_jobs_pkey', 'q_jobs_vt_idx', 'a_jobs_pkey', 'archived_at_idx_jobs', 'mine']},
                              'index mine'),
                             ({'publications': ['supabase_realtime:q_jobs']}, 'publication supabase_realtime:q_jobs')):
            found = pgmq_legacy.inventory(FakeDatabase(extras=extras).sql, E)
            self.assertEqual(found['queues'], [])
            self.assertIn(word, found['unsupported'][0])

    def test_another_version_is_unsupported(self):
        found = pgmq_legacy.inventory(FakeDatabase(version='1.4.4').sql, E)
        self.assertEqual(found['queues'], [])
        self.assertTrue(found['unsupported'])


class ArchiveTests(unittest.TestCase):
    def test_archive_shapes(self):
        self.assertEqual(pgmq_legacy.archive_shape('5; 0 16400 TABLE DATA public items postgres\n'), 'none')
        self.assertEqual(pgmq_legacy.archive_shape(LEGACY_TOC), 'legacy')
        self.assertEqual(pgmq_legacy.archive_shape(NATIVE_TOC), 'native')
        self.assertEqual(pgmq_legacy.archive_shape('; 3; 3079 16384 EXTENSION - pgmq\n'), 'none')

    def test_legacy_archive_needs_a_complete_supplement_or_explicit_loss(self):
        self.assertIn('--accept-pgmq-loss', pgmq_legacy.restorable('legacy', {}))
        self.assertIsNone(pgmq_legacy.restorable('legacy', {}, accept_loss=True))
        self.assertIsNone(pgmq_legacy.restorable('legacy', {'pgmq': {'unsupported': []}}))
        self.assertIn('could not keep', pgmq_legacy.restorable('legacy', {'pgmq': {'unsupported': ['queue big']}}))
        self.assertIsNone(pgmq_legacy.restorable('native', {}))
        self.assertIsNone(pgmq_legacy.restorable('none', {}))

    def test_supplement_digest_is_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = b'SELECT 1;\n'
            (path / pgmq_legacy.FILE).write_bytes(data)
            entry = {'format': 1, 'file': pgmq_legacy.FILE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            self.assertEqual(pgmq_legacy.verify(entry, path), data)
            (path / pgmq_legacy.FILE).write_bytes(b'SELECT 2;\n')
            with self.assertRaisesRegex(pgmq_legacy.LegacyError, 'digest'):
                pgmq_legacy.verify(entry, path)
            with self.assertRaisesRegex(pgmq_legacy.LegacyError, 'format'):
                pgmq_legacy.verify({**entry, 'file': '../x'}, path)

    def test_format_two_needs_its_roles_and_format_one_is_still_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = b'SELECT 1;\n'
            (path / pgmq_legacy.FILE).write_bytes(data)
            entry = {'format': 2, 'file': pgmq_legacy.FILE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            with self.assertRaisesRegex(pgmq_legacy.LegacyError, 'roles'):
                pgmq_legacy.verify(entry, path)
            self.assertEqual(pgmq_legacy.verify({**entry, 'roles': ['authenticated']}, path), data)
            self.assertEqual(pgmq_legacy.verify({**entry, 'format': 1}, path), data)
            with self.assertRaisesRegex(pgmq_legacy.LegacyError, 'format'):
                pgmq_legacy.verify({**entry, 'format': 3, 'roles': []}, path)

    def test_replay_leaves_out_grants_on_queue_tables_the_archive_does_not_create(self):
        toc = LEGACY_TOC + ('3471; 0 0 ACL pgmq TABLE meta postgres\n'
                            '3472; 0 0 ACL pgmq TABLE q_jobs supabase_admin\n'
                            '3473; 0 0 ACL pgmq TABLE a_jobs supabase_admin\n'
                            '3474; 0 0 ACL pgmq SEQUENCE q_jobs_msg_id_seq supabase_admin\n'
                            '3475; 0 0 ACL pgmq COLUMN q_other.message supabase_admin\n'
                            '20; 1259 16600 TABLE pgmq q_mine e_developer\n'
                            '3476; 0 0 ACL pgmq TABLE q_mine e_developer\n'
                            '2100; 826 16521 DEFAULT ACL pgmq DEFAULT PRIVILEGES FOR TABLES supabase_admin\n'
                            ';3477; 0 0 ACL pgmq TABLE q_commented supabase_admin\n')
        self.assertEqual(pgmq_legacy.replay_omits(toc),
                         {'q_jobs', 'a_jobs', 'q_jobs_msg_id_seq', 'q_other'})

    def test_a_format_one_restore_says_its_queues_come_back_closed(self):
        queue = [{'name': 'jobs', 'messages': 1, 'archived': 0, 'unlogged': False}]
        self.assertIn('closed', pgmq_legacy.restore_notice({'pgmq': {'format': 1, 'queues': queue}}))
        self.assertIsNone(pgmq_legacy.restore_notice({'pgmq': {'format': 2, 'queues': queue}}))
        self.assertIsNone(pgmq_legacy.restore_notice({'pgmq': {'format': 1, 'queues': []}}))
        self.assertIsNone(pgmq_legacy.restore_notice({}))

    def test_a_backup_that_could_not_keep_its_queues_says_so(self):
        entry = pgmq_legacy.not_kept('the legacy queues could not be read (x)')
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(pgmq_legacy.verify(entry, Path(folder)))
            (Path(folder) / pgmq_legacy.FILE).write_text('stray')
            with self.assertRaisesRegex(pgmq_legacy.LegacyError, 'does not name'):
                pgmq_legacy.verify(entry, Path(folder))
        self.assertIn('could not keep', pgmq_legacy.restorable('legacy', {'pgmq': entry}))

    def test_backup_files_include_the_supplement_only_when_present(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.assertEqual(backup.files_of(path), ['database.dump', 'objects.tar', 'manifest.json'])
            (path / pgmq_legacy.FILE).write_text('')
            self.assertEqual(backup.files_of(path)[-1], pgmq_legacy.FILE)


class SupplementTests(unittest.TestCase):
    def test_identifiers_are_validated_before_quoting(self):
        self.assertEqual(pgmq_legacy.quote('q_jobs_msg_id_seq'), '"q_jobs_msg_id_seq"')
        for value in ('a"b', 'x; DROP', 'Upper'):
            with self.assertRaises(pgmq_legacy.LegacyError):
                pgmq_legacy.quote(value)

    def capture(self, database):
        rows = {'q': b'1\t0\t2026\t2026\t{}\t\\N\n2\t0\t2026\t2026\t{}\t\\N\n', 'a': b''}
        original = pgmq_legacy.copy_out
        def copy_out(psql, query, handle):
            data = rows['q' if 'q_jobs' in query else 'a']
            handle.write(data)
            return data.count(b'\n')
        pgmq_legacy.copy_out = copy_out
        try:
            with tempfile.TemporaryDirectory() as folder:
                entry = pgmq_legacy.capture(SimpleNamespace(sql=database.sql, psql=lambda e: []), E,
                                            '00000003-00000002-1', Path(folder))
                return entry, (Path(folder) / pgmq_legacy.FILE).read_text()
        finally:
            pgmq_legacy.copy_out = original

    def test_capture_sets_exact_access_after_closing_the_new_tables(self):
        statements = ['GRANT SELECT ON TABLE pgmq.q_jobs TO authenticated;',
                      'ALTER TABLE pgmq.q_jobs ENABLE ROW LEVEL SECURITY;',
                      'CREATE POLICY own ON pgmq.q_jobs AS PERMISSIVE FOR SELECT TO authenticated USING (true);']
        database = FakeDatabase(access={'statements': statements, 'roles': ['authenticated']})
        entry, text = self.capture(database)
        self.assertEqual(entry['format'], 2)
        self.assertEqual(entry['roles'], ['authenticated'])
        revoke = text.index('REVOKE ALL ON %s pgmq.%I FROM %s CASCADE')
        self.assertLess(text.index(f'OWNER TO "{E}_developer"'), revoke)
        self.assertLess(revoke, text.index(statements[0]))
        self.assertLess(text.index(statements[2]), text.index('COPY pgmq."q_jobs"'))
        access = next(query for query in database.seen if "'statements'" in query)
        self.assertIn("SET LOCAL search_path = ''", access)
        self.assertIn("SET TRANSACTION SNAPSHOT '00000003-00000002-1'", access)

    def test_a_queue_dropped_and_created_again_during_the_backup_refuses_it(self):
        database = FakeDatabase()
        database.moved = True
        with self.assertRaisesRegex(pgmq_legacy.LegacyError, 'changed during the backup'):
            self.capture(database)

    def test_sequence_never_falls_below_the_snapshot_messages(self):
        for sequence, highest, expected in (('7|1', '2', "7, true"), ('3|1', '9', "9, true"),
                                            ('', '9', "9, true"), ('|1', '0', "1, false"), ('', '0', "1, false")):
            _, text = self.capture(FakeDatabase(sequence=sequence, highest=highest))
            self.assertIn(f"setval('pgmq.\"q_jobs_msg_id_seq\"', {expected})", text, (sequence, highest))

    def test_capture_writes_recreate_copy_and_sequence(self):
        database = FakeDatabase()
        sql = database.sql
        rows = {'q': b'1\t0\t2026\t2026\t{}\t\\N\n2\t0\t2026\t2026\t{}\t\\N\n', 'a': b''}
        calls = []
        def copy_out(psql, query, handle):
            calls.append(query)
            data = rows['q' if 'q_jobs' in query else 'a']
            handle.write(data)
            return data.count(b'\n')
        original = pgmq_legacy.copy_out
        pgmq_legacy.copy_out = copy_out
        try:
            with tempfile.TemporaryDirectory() as folder:
                entry = pgmq_legacy.capture(SimpleNamespace(sql=sql, psql=lambda e: []), E, '00000003-00000002-1', Path(folder))
                text = (Path(folder) / pgmq_legacy.FILE).read_text()
        finally:
            pgmq_legacy.copy_out = original
        self.assertEqual(entry['queues'], [{'name': 'jobs', 'messages': 2, 'archived': 0, 'unlogged': False}])
        self.assertIn("SELECT pgmq.create_non_partitioned('jobs');", text)
        self.assertIn('ALTER EXTENSION pgmq DROP', text)
        self.assertIn(f'OWNER TO "{E}_developer"', text)
        self.assertIn('COPY pgmq."q_jobs" ("msg_id"', text)
        self.assertIn("setval('pgmq.\"q_jobs_msg_id_seq\"', 7, true)", text)
        self.assertEqual(text.count('\\.\n'), 2)
        self.assertTrue(all("SET TRANSACTION SNAPSHOT" in query for query in calls))

    def test_row_count_mismatch_refuses_the_backup(self):
        database = FakeDatabase()
        def sql(query, e):
            if 'count(*)' in query:
                return '5'
            return database.sql(query, e)
        original = pgmq_legacy.copy_out
        pgmq_legacy.copy_out = lambda psql, query, handle: 0
        try:
            with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(pgmq_legacy.LegacyError, 'row count'):
                pgmq_legacy.capture(SimpleNamespace(sql=sql, psql=lambda e: []), E, '00000003-00000002-1', Path(folder))
        finally:
            pgmq_legacy.copy_out = original


if __name__ == '__main__':
    unittest.main()
