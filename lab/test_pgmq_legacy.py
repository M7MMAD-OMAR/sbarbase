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
        # moved: {queue: live identity reads before it differs from the snapshot}; 0 at once.
        self.moved = {}
        self.live = {}
        self.queues = [{'name': 'jobs', 'partitioned': False, 'unlogged': False, 'created_at': '2026-11-14T10:00:00+00:00'}] \
            if queues is None else queues
        self.columns = {f'{kind}_{queue["name"]}': list(pgmq_legacy.QUEUE_COLUMNS if kind == 'q' else pgmq_legacy.ARCHIVE_COLUMNS)
                        for queue in self.queues for kind in 'qa'} if columns is None else columns
        self.seen = []

    def name_in(self, query):
        return next(queue['name'] for queue in self.queues if f"'q_' || '{queue['name']}'" in query
                    or f"q_{queue['name']}" in query)

    def sql(self, query, database):
        self.seen.append(query)
        if 'c.oid::text' in query:
            name = self.name_in(query)
            moved = False
            if name in self.moved and 'SET TRANSACTION SNAPSHOT' not in query:
                self.live[name] = self.live.get(name, 0) + 1
                moved = self.live[name] > self.moved[name]
            return json.dumps({f'q_{name}': '9' if moved else '1', f'a_{name}': '2', f'q_{name}_msg_id_seq': '3'})
        if 'pg_extension e JOIN' in query:
            return '' if self.owner is None else json.dumps({'version': self.version, 'owner': self.owner, 'schema': 'pgmq'})
        if 'FROM pgmq.meta m' in query:
            return json.dumps(self.queues)
        if 'json_object_agg' in query:
            return json.dumps(self.columns)
        if "'triggers'" in query:
            name = self.name_in(query)
            indexes = [f'q_{name}_pkey', f'q_{name}_vt_idx', f'a_{name}_pkey', f'archived_at_idx_{name}']
            return json.dumps({'triggers': [], 'rules': [], 'indexes': indexes, 'constraints': [], 'publications': [],
                               **self.extras})
        if "'statements'" in query:
            return json.dumps(self.access)
        if 'pg_sequences' in query:
            return self.sequence
        if 'greatest(' in query:
            return self.highest
        if 'count(*)' in query:
            return '0' if '"a_' in query else '2'
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

    def test_definitions_neither_dump_nor_supplement_keeps_are_listed_and_the_messages_kept(self):
        """0.13.2 left such a queue out of the supplement whole, so its messages were in no new backup."""
        for extras, word in (({'triggers': ['q_jobs.notify']}, 'trigger q_jobs.notify'),
                             ({'rules': ['q_jobs.r']}, 'rule q_jobs.r'),
                             ({'indexes': ['q_jobs_pkey', 'q_jobs_vt_idx', 'a_jobs_pkey', 'archived_at_idx_jobs', 'mine']},
                              'index mine'),
                             ({'constraints': ['q_jobs.positive']}, 'constraint q_jobs.positive'),
                             ({'publications': ['supabase_realtime:q_jobs']}, 'publication supabase_realtime:q_jobs')):
            found = pgmq_legacy.inventory(FakeDatabase(extras=extras).sql, E)
            self.assertEqual([queue['name'] for queue in found['queues']], ['jobs'])
            self.assertIn(word, found['unsupported'][0])
            self.assertIn('its messages are kept', found['unsupported'][0])

    def test_an_added_column_is_listed_and_left_out_of_the_copy(self):
        columns = {'q_jobs': [*pgmq_legacy.QUEUE_COLUMNS[:3], 'tenant', *pgmq_legacy.QUEUE_COLUMNS[3:]],
                   'a_jobs': list(pgmq_legacy.ARCHIVE_COLUMNS)}
        found = pgmq_legacy.inventory(FakeDatabase(columns=columns).sql, E)
        self.assertEqual(found['queues'][0]['queue_columns'], list(pgmq_legacy.QUEUE_COLUMNS))
        self.assertIn('column q_jobs.tenant would not be kept; its messages are kept', found['unsupported'][0])

    def test_a_table_missing_a_pgmq_column_is_not_captured(self):
        columns = {'q_jobs': list(pgmq_legacy.QUEUE_COLUMNS[:-1]), 'a_jobs': list(pgmq_legacy.ARCHIVE_COLUMNS)}
        found = pgmq_legacy.inventory(FakeDatabase(columns=columns).sql, E)
        self.assertEqual(found['queues'], [])
        self.assertIn('lack a column', found['unsupported'][0])

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
        self.assertIn('does not keep everything', pgmq_legacy.restorable('legacy', {'pgmq': {'unsupported': ['queue big']}}))
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

    def test_objects_on_queue_tables_the_archive_does_not_create_are_found(self):
        """The pinned pg_dump writes none for an extension member; another one that did would stop the replay."""
        self.assertEqual(pgmq_legacy.replay_dependents(LEGACY_TOC + '8; 0 0 ACL pgmq TABLE q_jobs postgres\n'), [])
        toc = LEGACY_TOC + ''.join(line + '\n' for line in (
            '8; 0 16844 ROW SECURITY pgmq q_jobs postgres', '9; 3256 16853 POLICY pgmq q_jobs own postgres',
            '10; 2620 16852 TRIGGER pgmq a_jobs hook postgres', '11; 2618 16850 RULE pgmq q_mail r postgres',
            '12; 2606 16848 CONSTRAINT pgmq q_other q_other_pkey postgres',
            '13; 6106 16851 PUBLICATION TABLE pgmq pub q_pub postgres', '14; 0 0 COMMENT pgmq COLUMN q_note.v postgres',
            # A table the archive creates keeps its own entries.
            '15; 1259 16844 TABLE pgmq q_mine postgres', '16; 3256 16853 POLICY pgmq q_mine p postgres'))
        self.assertEqual(pgmq_legacy.replay_dependents(toc), ['a_jobs', 'q_jobs', 'q_mail', 'q_note', 'q_other', 'q_pub'])

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
        self.assertIn('older backup that keeps every queue', pgmq_legacy.restorable('legacy', {'pgmq': entry}))

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
            data = rows['a' if '"a_' in query else 'q']
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

    def two_queues(self, moved):
        queues = [{'name': name, 'partitioned': False, 'unlogged': False, 'created_at': '2026-11-14T10:00:00+00:00'}
                  for name in ('jobs', 'mail')]
        database = FakeDatabase(queues=queues, access={'statements': [], 'roles': ['authenticated']})
        database.moved = moved
        return database

    def test_a_queue_changed_before_it_is_read_is_left_out_and_the_others_kept(self):
        entry, text = self.capture(self.two_queues({'jobs': 0}))
        self.assertEqual([queue['name'] for queue in entry['queues']], ['mail'])
        self.assertIn('queue jobs: dropped or created again during the backup', entry['unsupported'][0])
        self.assertEqual(entry['changed'], ['jobs'])
        self.assertNotIn("'jobs'", text)
        self.assertIn("SELECT pgmq.create_non_partitioned('mail');", text)
        self.assertTrue(backup.partial(self.write_manifest(entry)))

    def test_a_queue_changed_after_its_rows_were_written_is_cut_back_out_of_the_file(self):
        """The second identity check runs after the rows are in the file: they go, the rest stays whole."""
        entry, text = self.capture(self.two_queues({'jobs': 1}))
        self.assertEqual([queue['name'] for queue in entry['queues']], ['mail'])
        self.assertNotIn('jobs', text)
        self.assertEqual(text.count('\\.\n'), 2)
        self.assertTrue(text.startswith('-- Sbarbase legacy PGMQ supplement'))
        self.assertTrue(text.endswith("WHERE queue_name = 'mail';\n"))
        self.assertEqual(entry['bytes'], len(text.encode()))
        self.assertEqual(entry['sha256'], hashlib.sha256(text.encode()).hexdigest())

    def test_a_read_that_fails_because_the_queue_was_dropped_is_that_change(self):
        database = self.two_queues({})
        original = pgmq_legacy.copy_out
        def copy_out(psql, query, handle):
            if 'q_jobs' in query:
                database.moved = {'jobs': 0}
                handle.write(b'partial row')
                raise pgmq_legacy.LegacyError('A legacy queue could not be read for the backup')
            handle.write(b'')
            return 0 if '"a_' in query else 2
        pgmq_legacy.copy_out = copy_out
        try:
            with tempfile.TemporaryDirectory() as folder:
                entry = pgmq_legacy.capture(SimpleNamespace(sql=database.sql, psql=lambda e: []), E,
                                            '00000003-00000002-1', Path(folder))
                text = (Path(folder) / pgmq_legacy.FILE).read_text()
        finally:
            pgmq_legacy.copy_out = original
        self.assertEqual([queue['name'] for queue in entry['queues']], ['mail'])
        self.assertNotIn('partial row', text)

    def test_any_other_read_failure_still_fails_the_whole_supplement(self):
        database = self.two_queues({})
        original = pgmq_legacy.copy_out
        def copy_out(psql, query, handle):
            raise pgmq_legacy.LegacyError('A legacy queue could not be read for the backup')
        pgmq_legacy.copy_out = copy_out
        try:
            with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(pgmq_legacy.LegacyError, 'could not be read'):
                pgmq_legacy.capture(SimpleNamespace(sql=database.sql, psql=lambda e: []), E, '00000003-00000002-1', Path(folder))
        finally:
            pgmq_legacy.copy_out = original

    def write_manifest(self, entry):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, folder)
        (folder / 'manifest.json').write_text(json.dumps({'version': 2, 'pgmq': entry}))
        return folder

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
