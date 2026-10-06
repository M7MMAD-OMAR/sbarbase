"""Source fixtures for budgeting and migration routing, not native acceptance."""
import unittest
from pooler_migration_contract import Pool, Environment, capacity, migration_connection

A = 'e_'+'a'*24
B = 'e_'+'b'*24


class PoolerMigrationTests(unittest.TestCase):
    def test_existing_four_environment_reserve(self):
        result = capacity([Environment('e_'+c*24) for c in 'abcd'], 100, 3, 0)
        self.assertTrue(result['fits'])
        self.assertEqual(result['headroom'], 13)

    def test_optional_services_are_aggregated(self):
        result = capacity([Environment(A, studio=True, realtime=True, direct=True), Environment(B)], 100, 3, 0)
        self.assertEqual(result['promised'], 80)
        self.assertEqual(result['environments'][A]['database'], 50)

    def test_optional_services_can_exhaust_cluster(self):
        self.assertFalse(capacity([Environment(A, studio=True, realtime=True, direct=True),
                                   Environment(B, studio=True, realtime=True, direct=True)], 100, 3, 0)['fits'])

    def test_both_modes_and_replicas_cost_backends(self):
        env = Environment(A, direct=True, pools=(Pool(A+'_developer', 'session', 2, 20, 2),
                                                Pool(A+'_developer', 'transaction', 3, 100, 2)))
        result = capacity([env], 100, 3, 0, metadata=4)
        self.assertEqual(result['environments'][A], {'database': 38, 'developer': 20,
                         'pool_backends': 10, 'pool_clients': 240})
        self.assertEqual(result['promised'], 54)

    def test_many_clients_are_not_free_database_slots(self):
        env = Environment(A, pools=(Pool(A+'_developer', 'transaction', 2, 1000),))
        result = capacity([env], 40, 3, 0, metadata=2)
        self.assertFalse(result['fits'])

    def test_exact_reserve_boundary(self):
        self.assertTrue(capacity([Environment(A)], 43, 3, 0)['fits'])
        self.assertFalse(capacity([Environment(A)], 42, 3, 0)['fits'])

    def test_pool_metadata_required(self):
        with self.assertRaises(ValueError):
            capacity([Environment(A, pools=(Pool(A+'_developer', 'session', 1, 1),))], 100, 3, 0)

    def test_duplicate_environment_refused(self):
        with self.assertRaises(ValueError):
            capacity([Environment(A), Environment(A)], 100, 3, 0)

    def test_duplicate_role_mode_refused(self):
        pool = Pool(A+'_developer', 'transaction', 1, 2)
        with self.assertRaises(ValueError):
            Environment(A, pools=(pool, pool)).reservation()

    def test_wrong_role_refused(self):
        with self.assertRaises(ValueError):
            Environment(A, pools=(Pool(B+'_developer', 'session', 1, 1),)).reservation()

    def test_nonfinite_or_invalid_limits_refused(self):
        for value in (True, -1, 1.5, float('inf'), '100'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capacity([Environment(A)], value, 3, 0)
        with self.assertRaises(ValueError):
            capacity([], 2, 3, 0)
        with self.assertRaises(ValueError):
            Environment(A, direct='false').reservation()
        for field in ('size', 'clients', 'replicas'):
            args = dict(role=A+'_developer', mode='session', size=2, clients=2, replicas=1)
            args[field] = 0
            with self.subTest(field=field), self.assertRaises(ValueError):
                Environment(A, pools=(Pool(**args),)).reservation()

    def test_no_assumption_that_zero_prepared_setting_is_required(self):
        result = migration_connection(A, 'direct', database=A, role=A+'_developer', max_prepared_transactions=10)
        self.assertEqual(result['max_prepared_transactions'], 10)
        self.assertFalse(result['reconcile_prepared'])

    def test_unresolved_prepared_transactions_need_reconciliation(self):
        result = migration_connection(A, 'direct', database=A, role=A+'_developer',
                                      max_prepared_transactions=10, prepared_transactions=2)
        self.assertTrue(result['reconcile_prepared'])

    def test_pooled_migrations_refused(self):
        for mode in ('session', 'transaction'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                migration_connection(A, mode, database=A, role=A+'_developer')

    def test_migration_identity_refused(self):
        for database, role in ((B, A+'_developer'), (A, B+'_developer'), (A, 'supabase_admin')):
            with self.subTest(database=database, role=role), self.assertRaises(ValueError):
                migration_connection(A, 'direct', database=database, role=role)

    def test_invalid_prepared_inventory_refused(self):
        with self.assertRaises(ValueError):
            migration_connection(A, 'direct', database=A, role=A+'_developer', prepared_transactions=1)


if __name__ == '__main__':
    unittest.main()
