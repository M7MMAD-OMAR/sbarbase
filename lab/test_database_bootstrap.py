"""New databases remain closed until the permission transaction commits."""
from types import SimpleNamespace
import unittest
import run as lab


class DatabaseBootstrapTests(unittest.TestCase):
    def test_existing_closed_database_refuses_before_any_mutation(self):
        queries=[]
        def execute(query,database='postgres'):
            queries.append(query);return SimpleNamespace(stdout='f')
        with self.assertRaisesRegex(RuntimeError,'closed'):
            lab.provision_environment('e_fixture',{'auth':'a'*64,'rest':'b'*64},executor=execute)
        self.assertEqual(len(queries),1)
        self.assertTrue(queries[0].startswith('SELECT datallowconn'))

    def test_new_database_is_closed_and_reopens_only_in_permission_transaction(self):
        calls=[]
        def execute(query,database='postgres'):
            calls.append((query,database));return SimpleNamespace(stdout='')
        lab.provision_environment('e_fixture',{'auth':'a'*64,'rest':'b'*64},executor=execute)
        self.assertIn(('CREATE DATABASE e_fixture ALLOW_CONNECTIONS false;','postgres'),calls)
        transaction=next(query for query,database in calls if query.startswith('BEGIN;'))
        self.assertTrue(transaction.endswith('COMMIT;'))
        self.assertLess(transaction.index('REVOKE ALL'),transaction.index('ALLOW_CONNECTIONS true'))
        self.assertLess(transaction.index('GRANT CONNECT'),transaction.index('ALLOW_CONNECTIONS true'))

    def test_existing_open_database_is_never_treated_as_new(self):
        calls=[]
        def execute(query,database='postgres'):
            calls.append(query)
            return SimpleNamespace(stdout='t' if query.startswith('SELECT datallowconn') else '1')
        lab.provision_environment('e_fixture',{'auth':'a'*64,'rest':'b'*64},executor=execute)
        self.assertFalse(any('CREATE DATABASE' in query or 'ALLOW_CONNECTIONS true' in query for query in calls))
