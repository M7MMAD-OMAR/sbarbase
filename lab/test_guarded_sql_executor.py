"""Scope and uncertain-result boundaries for actual provisioning SQL adapter."""
import json
from types import SimpleNamespace
import unittest
from guarded_sql_executor import GuardedSQL


class GuardedSQLTests(unittest.TestCase):
    def setUp(self):
        self.args=('e_'+'a'*24,'12345678-1234-1234-1234-123456789abc','22345678-1234-1234-1234-123456789abc',1)
        self.calls=[]
        self.fail_target=False
        self.meta={'cluster':'123','database':'postgres','control_oid':5,'target':{'oid':42,'allows_connections':True}}

    def transport(self,query,database='postgres',check=True):
        self.calls.append((database,query,check))
        if self.fail_target and database==self.args[0]:raise RuntimeError('Uncertain transport')
        return SimpleNamespace(returncode=0,stdout=json.dumps(self.meta) if 'SELECT json_build_object' in query else '17\n')

    def test_target_binding_and_scalar_output_are_preserved(self):
        executor=GuardedSQL(self.transport,*self.args)
        self.assertEqual(executor('SELECT 17;',self.args[0]).stdout,'17\n')
        target_scripts=[q for db,q,check in self.calls if db==self.args[0]]
        self.assertEqual(len(target_scripts),2)
        self.assertTrue(all('<>42' in q and "<>'123'" in q for q in target_scripts))
        self.assertIn('CREATE SCHEMA',target_scripts[0])
        self.assertNotIn('CREATE SCHEMA',target_scripts[1])

    def test_rejected_scope_or_unchecked_execution_never_reaches_transport(self):
        for options in ({'database':'neighbor'},{'check':False}):
            executor=GuardedSQL(self.transport,*self.args);before=len(self.calls)
            with self.assertRaises(ValueError):executor('SELECT 1;',**options)
            self.assertEqual(len(self.calls),before)
            with self.assertRaisesRegex(RuntimeError,'reconciliation'):executor('SELECT 1;')
            self.assertEqual(len(self.calls),before)

    def test_uncertain_target_registration_poisoning_prevents_new_binding(self):
        executor=GuardedSQL(self.transport,*self.args);self.fail_target=True
        with self.assertRaisesRegex(RuntimeError,'Uncertain'):executor('SELECT 1;',self.args[0])
        before=len(self.calls);self.fail_target=False;self.meta['target']['oid']=43
        with self.assertRaisesRegex(RuntimeError,'reconciliation'):executor('SELECT 1;',self.args[0])
        self.assertEqual(len(self.calls),before)

    def test_interrupted_dispatch_cannot_reuse_executor(self):
        executor=GuardedSQL(self.transport,*self.args)
        def interrupted(*args,**kwargs):raise KeyboardInterrupt()
        executor.transport=interrupted
        with self.assertRaises(KeyboardInterrupt):executor('SELECT 1;')
        executor.transport=self.transport;before=len(self.calls)
        with self.assertRaisesRegex(RuntimeError,'reconciliation'):executor('SELECT 1;')
        self.assertEqual(len(self.calls),before)
