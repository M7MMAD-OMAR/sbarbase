"""Input boundaries for experimental native SQL guarding."""
import unittest
import sql_operation_fence as fence


class SQLFenceTests(unittest.TestCase):
    def setUp(self):
        self.identity=('e_'+'a'*24,'12345678-1234-1234-1234-123456789abc','22345678-1234-1234-1234-123456789abc',1)

    def test_rejects_untrusted_identity_and_boolean_attempt(self):
        for index,value in ((0,"e_x'; DROP SCHEMA public;"),(1,'bad'),(2,'bad'),(3,True),(3,0),(3,2**31)):
            args=list(self.identity);args[index]=value
            for action in (fence.register,fence.revoke):
                with self.assertRaises(ValueError):action(*args)

    def test_caller_cannot_insert_psql_reconnect_or_meta_commands(self):
        for query in ('',r'\connect postgres',r"SELECT 1; \! true"):
            with self.assertRaises(ValueError):fence.guarded(*self.identity,query)

    def test_native_create_database_remains_outside_transaction_wrapper(self):
        script=fence.guarded(*self.identity,'CREATE DATABASE fixture ALLOW_CONNECTIONS false;')
        self.assertNotIn('BEGIN;',script)
        self.assertLess(script.index('pg_advisory_lock'),script.index('SQL operation is not active'))
        self.assertLess(script.index('END $guard$;'),script.index('CREATE DATABASE'))


    def test_backend_identity_pins_reject_malformed_values(self):
        for options in ({'expected_oid':True},{'expected_oid':0},{'expected_cluster':"1';"},{'expected_cluster':'0'}):
            with self.assertRaises(ValueError):fence.revoke(*self.identity,**options)
