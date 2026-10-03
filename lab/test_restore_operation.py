import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from restore_operation import OperationError, admit_workers, publish, read_journal, recovery_database_action, validate


def journal(scope='e_' + 'a' * 24):
    stamp = '20261003t100000z'
    db = 'storage_metadata' if scope == 'storage' else scope
    record = {'restored_at': stamp, 'backup': '20261003T090000Z', 'previous_database': db + '_pre_' + stamp, 'counts': {}}
    files = None
    if scope != 'storage':
        aside = '.pre-restore-' + scope + '-' + stamp
        files = {'aside': aside, 'stage': '.stage-restore-' + scope + '-' + stamp, 'original': None, 'restored': None}
        record['previous_files'] = aside
    return {'version': 1, 'phase': 'initial', 'scope': scope, 'backup': '20261003T090000Z',
            'stamp': stamp, 'container_id': 'b' * 64, 'database': db, 'original_oid': '12345',
            'storage_cid': 'f' * 64,
            'stage': db + '_stage_' + stamp, 'stage_oid': None, 'previous': db + '_pre_' + stamp,
            'archive': {'manifest': 'c' * 64, 'database': 'd' * 64, 'objects': None if scope == 'storage' else 'e' * 64},
            'files': files, 'record': record}


def workers(**updates):
    return dict({'version': 170006, 'shared': 'pg_stat_statements,pgaudit', 'session': '', 'local': '',
                 'workers': ['autovacuum worker', 'logical replication launcher', 'walwriter'], 'subscriptions': 0,
                 'scoped_preloads': []}, **updates)


class RestoreOperationIdentityTests(unittest.TestCase):
    def test_initial_and_each_cutover_phase_admit_exact_identity(self):
        for scope in ('storage', 'e_' + 'a' * 24):
            value = journal(scope)
            self.assertIs(validate(value), value)
            value['stage_oid'] = '23456'
            for phase in ('stage-fenced', 'original-rename-intent', 'original-renamed',
                          'stage-rename-intent', 'stage-renamed', 'checkpoint-intent', 'data-ready',
                          'open-intent', 'opened', 'completed'):
                value['phase'] = phase
                self.assertIs(validate(value), value)

    def test_native_and_archive_identity_malformed_refuse(self):
        mutations = [('scope', '../other'), ('backup', 'invalid'), ('stamp', 'invalid'), ('container_id', 'b' * 12),
                     ('original_oid', '0'), ('stage_oid', '12345'), ('stage', 'other'), ('previous', 'other'),
                     ('database', 'postgres'), ('version', True), ('phase', 'unknown')]
        for key, item in mutations:
            with self.subTest(key=key), self.assertRaises(OperationError):
                value = journal()
                value[key] = item
                validate(value)
        value = journal()
        value['phase'] = 'stage-ready'
        with self.assertRaises(OperationError):
            validate(value)
        for key in ('manifest', 'database', 'objects'):
            with self.subTest(hash=key), self.assertRaises(OperationError):
                value = journal()
                value['archive'][key] = 'bad'
                validate(value)

    def test_exact_file_and_success_record_identity(self):
        value = journal()
        value['files']['original'] = {'exists': True, 'device': '42', 'inode': '123', 'sha256': 'f' * 64}
        validate(value)
        for key, item in [('inode', '0'), ('device', 'wrong'), ('sha256', 'bad'), ('exists', 1)]:
            altered = copy.deepcopy(value)
            altered['files']['original'][key] = item
            with self.subTest(key=key), self.assertRaises(OperationError):
                validate(altered)
        value['record']['previous_files'] = '.other'
        with self.assertRaises(OperationError):
            validate(value)

    def test_private_journal_and_unknown_fields_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            os.chmod(state, 0o700)
            value = journal()
            called = []
            def writer(path, data):
                called.append(path)
                path.write_text(json.dumps(data))
                path.chmod(0o600)
            publish(state, value, writer)
            self.assertEqual(read_journal(state, value['scope']), value)
            called[0].chmod(0o644)
            with self.assertRaises(OperationError):
                read_journal(state, value['scope'])
            value['unexpected'] = True
            with self.assertRaises(OperationError):
                publish(state, value, writer)

    def test_native_reopening_before_opened_phase_preserves_new_writes(self):
        value = journal()
        value['stage_oid'] = '23456'
        value['phase'] = 'open-intent'
        inventory = {value['database']: {'oid': '23456', 'allow_connections': True},
                     value['stage']: None, value['previous']: {'oid': '12345', 'allow_connections': False}}
        self.assertEqual(recovery_database_action(value, inventory), 'preserve-restored-writes')
        value['phase'] = 'opened'
        inventory[value['database']]['allow_connections'] = False
        self.assertEqual(recovery_database_action(value, inventory), 'preserve-restored-writes')

    def test_fenced_rename_gaps_are_distinguished_using_actual_oids(self):
        value = journal()
        value['stage_oid'] = '23456'
        value['phase'] = 'original-rename-intent'
        inventory = {value['database']: None, value['stage']: {'oid': '23456', 'allow_connections': False},
                     value['previous']: {'oid': '12345', 'allow_connections': False}}
        self.assertEqual(recovery_database_action(value, inventory), 'rollback-first-rename')
        inventory[value['database']], inventory[value['stage']] = inventory[value['stage']], None
        self.assertEqual(recovery_database_action(value, inventory), 'rollback-fenced-cutover')

    def test_original_reopening_is_irreversible_and_requires_absent_stage(self):
        value = journal()
        value['stage_oid'] = '23456'
        value['phase'] = 'rollback-open-intent'
        inventory = {value['database']: {'oid': '12345', 'allow_connections': True},
                     value['stage']: None, value['previous']: None}
        self.assertEqual(recovery_database_action(value, inventory), 'preserve-original-writes')
        inventory[value['database']]['allow_connections'] = False
        self.assertEqual(recovery_database_action(value, inventory), 'preserve-original')
        for phase in ('original-opened', 'rolled-back'):
            value['phase'] = phase
            self.assertEqual(recovery_database_action(value, inventory), 'preserve-original-writes')
        inventory[value['stage']] = {'oid': '23456', 'allow_connections': False}
        with self.assertRaises(OperationError):
            recovery_database_action(value, inventory)

    def test_same_name_replacement_and_unfenced_predecessor_refuse(self):
        value = journal()
        value['stage_oid'] = '23456'
        value['phase'] = 'open-intent'
        inventory = {value['database']: {'oid': '23456', 'allow_connections': True},
                     value['stage']: None, value['previous']: {'oid': '12345', 'allow_connections': False}}
        for key, row in [(value['database'], {'oid': '34567', 'allow_connections': True}),
                         (value['previous'], {'oid': '12345', 'allow_connections': True}),
                         (value['previous'], None), (value['stage'], {'oid': '23456', 'allow_connections': False})]:
            with self.subTest(key=key), self.assertRaises(OperationError):
                altered = copy.deepcopy(inventory)
                altered[key] = row
                recovery_database_action(value, altered)


class RestoreWorkerAdmissionTests(unittest.TestCase):
    def test_classified_core_workers_and_hook_only_preloads_admit(self):
        self.assertTrue(admit_workers(workers()))
        self.assertTrue(admit_workers(workers(shared='', workers=[])))

    def test_unknown_or_privileged_extension_workers_refuse(self):
        for changes in ({'shared': 'pg_net'}, {'shared': 'pg_cron'}, {'shared': 'timescaledb'},
                        {'shared': 'pg_stat_statements,arbitrary'}, {'session': 'arbitrary'}, {'local': 'arbitrary'},
                        {'workers': ['logical replication worker']}, {'workers': ['background worker']},
                        {'scoped_preloads': ['session_preload_libraries=arbitrary']},
                        {'scoped_preloads': ['cron.use_background_workers=on']}, {'version': 160006},
                        {'version': True}, {'workers': 'autovacuum worker'}):
            with self.subTest(changes=changes), self.assertRaises(OperationError):
                admit_workers(workers(**changes))

    def test_exact_native_defaults_admit_only_for_bound_image_and_separate_target(self):
        native = workers(shared='pg_stat_statements,pgaudit,plpgsql,plpgsql_check,pg_cron,pg_net,pgsodium,auto_explain,pg_tle,plan_filter,supabase_vault',
                         session='supautils', workers=['checkpointer', 'pg_cron launcher', 'pg_net 0.20.4 worker'],
                         scoped_preloads=['session_preload_libraries=supautils, safeupdate'],
                         native={'cron_database': 'postgres', 'cron_background': 'off', 'net_database': 'postgres',
                                 'versions': {'pg_cron': '1.6.4', 'pg_net': '0.20.4', 'pgsodium': '3.1.8',
                                              'pg_stat_statements': '1.11', 'pgaudit': '17.1'},
                                 'installed': {'pg_cron': '1.6.4', 'pg_net': '0.20.4'},
                                 'scopes': [{'role': 'authenticator', 'database': None,
                                             'setting': 'session_preload_libraries=supautils, safeupdate'}],
                                 'workers': [{'backend_type': 'pg_cron launcher', 'datname': 'postgres'},
                                             {'backend_type': 'pg_net 0.20.4 worker', 'datname': 'postgres'}]})
        image = 'sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86'
        target = 'e_' + 'a' * 24
        self.assertTrue(admit_workers(native, image=image, database=target))
        self.assertTrue(admit_workers(native, image=image, database='storage_metadata'))
        for key, value in (('image', None), ('image', 'sha256:' + 'f' * 64), ('database', 'postgres')):
            args = {'image': image, 'database': target, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(OperationError):
                admit_workers(native, **args)
        mutations = [('cron_background', 'on'), ('cron_database', target), ('net_database', target),
                     ('versions', {'pg_cron': '1.7.0'}), ('installed', {'pg_net': '0.19.5'}),
                     ('scopes', [{'role': 'postgres', 'database': None, 'setting': 'session_preload_libraries=supautils, safeupdate'}]),
                     ('workers', [{'backend_type': 'pg_net 0.20.4 worker', 'datname': target}])]
        for key, value in mutations:
            changed = copy.deepcopy(native)
            changed['native'][key] = value
            with self.subTest(key=key), self.assertRaises(OperationError):
                admit_workers(changed, image=image, database=target)
        for key, value in (('shared', native['shared'] + ',arbitrary'), ('session', 'supautils,arbitrary'),
                           ('workers', native['workers'] + ['pg_cron worker']), ('subscriptions', 1),
                           ('scoped_preloads', ['session_preload_libraries=supautils'])):
            changed = copy.deepcopy(native)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(OperationError):
                admit_workers(changed, image=image, database=target)

    def test_enabled_subscription_refuses_even_when_no_worker_is_currently_visible(self):
        for count in (1, True, '0'):
            with self.subTest(count=count), self.assertRaises(OperationError):
                admit_workers(workers(workers=['logical replication launcher'], subscriptions=count))


if __name__ == '__main__':
    unittest.main()
