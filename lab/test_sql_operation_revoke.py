"""Coordinator failures cannot become completed cross-database barriers."""
import json
import unittest
import sql_operation_revoke as revoke


class SQLRevokeTests(unittest.TestCase):
    def setUp(self):
        self.args=('e_'+'a'*24,'12345678-1234-1234-1234-123456789abc','22345678-1234-1234-1234-123456789abc',1)
        self.meta={'cluster':'123','database':'postgres','control_oid':5,'target':{'oid':42,'allows_connections':True}}

    def executor(self,fail=None,change=False):
        self.calls=[];observations=0
        def execute(database,script):
            nonlocal observations
            self.calls.append((database,script))
            if script.startswith('SELECT json_build_object'):
                observations+=1
                value=self.meta
                if change and observations==3:value={**value,'target':{'oid':43,'allows_connections':True}}
                return json.dumps(value)
            if database==fail:raise RuntimeError('Injected uncertain commit')
            return ''
        return execute

    def test_target_failure_is_incomplete_and_retry_revisits_both_databases(self):
        with self.assertRaisesRegex(RuntimeError,'uncertain'):revoke.revoke_pair(self.executor(fail=self.args[0]),*self.args)
        result=revoke.revoke_pair(self.executor(),*self.args)
        self.assertEqual(result['target_oid'],42)
        self.assertEqual([db for db,q in self.calls if not q.startswith('SELECT')],['postgres',self.args[0]])

    def test_closed_target_and_replaced_target_never_certify_completion(self):
        with self.assertRaisesRegex(RuntimeError,'identity changed'):revoke.revoke_pair(self.executor(change=True),*self.args)
        self.meta['target']['allows_connections']=False
        with self.assertRaisesRegex(RuntimeError,'Closed'):revoke.revoke_pair(self.executor(),*self.args)
        self.assertFalse(any(db==self.args[0] for db,q in self.calls))

    def test_boolean_oid_is_not_identity_and_control_failure_does_not_touch_target(self):
        self.meta['control_oid']=True
        with self.assertRaisesRegex(RuntimeError,'identity unavailable'):revoke.revoke_pair(self.executor(),*self.args)
        self.meta['control_oid']=5
        with self.assertRaisesRegex(RuntimeError,'uncertain'):revoke.revoke_pair(self.executor(fail='postgres'),*self.args)
        self.assertFalse(any(db==self.args[0] for db,q in self.calls))

    def test_registration_requires_guarded_control_observation_and_pins_target(self):
        calls=[]
        def execute(database,script):
            calls.append((database,script))
            return json.dumps(self.meta) if database=='postgres' else ''
        binding=revoke.register_target(execute,*self.args)
        self.assertEqual(binding['token'],self.args[1])
        control,target=calls
        self.assertIn('SQL operation is not active',control[1])
        self.assertLess(control[1].index('SQL operation is not active'),control[1].index('SELECT json_build_object'))
        self.assertIn('<>42',target[1])
        self.assertIn("<>'123'",target[1])
        self.assertLess(target[1].index('pg_advisory_lock'),target[1].index('Database identity changed'))
        self.assertLess(target[1].index('Database identity changed'),target[1].index('CREATE SCHEMA'))

    def test_registration_never_dispatches_after_control_failure_or_absent_target(self):
        calls=[]
        def failed(database,script):
            calls.append(database)
            raise RuntimeError('Control refused')
        with self.assertRaisesRegex(RuntimeError,'Control refused'):revoke.register_target(failed,*self.args)
        self.assertEqual(calls,['postgres'])
        for target in (None,{'oid':42,'allows_connections':False}):
            calls.clear()
            def closed(database,script):
                calls.append(database)
                return json.dumps({**self.meta,'target':target})
            with self.assertRaisesRegex(RuntimeError,'existing open'):revoke.register_target(closed,*self.args)
            self.assertEqual(calls,['postgres'])
