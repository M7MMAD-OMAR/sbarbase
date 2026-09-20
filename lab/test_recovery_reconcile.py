"""Reconciliation must not infer success or touch foreign recovery resources."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from recovery_reconcile import reconcile


class ReconcileTests(unittest.TestCase):
    def run_case(self,status='initializing',foreign=False,fail=False,inventory_error=False):
        prefix='sbarbase-restore-0123456789ab';calls=[];saved=[]
        states={key:{'Name':'/'+prefix+'-'+key,'Config':{'Labels':{'io.sbarbase.owner':'foreign' if foreign and key=='auth' else 'recovery-target'}},'State':{'Running':True}} for key in ('db','auth')}
        def command(*args):
            calls.append(args)
            if args[0]=='ps':
                if inventory_error:raise RuntimeError('daemon unavailable')
                return SimpleNamespace(stdout='\n'.join(key+' '+prefix+'-'+key for key in states))
            if args[0]=='stop':
                if fail and args[1]=='auth':raise RuntimeError('stop failed')
                states[args[1]]['State']['Running']=False
                return SimpleNamespace(stdout='')
            return SimpleNamespace(stdout=json.dumps([states[args[-1]]]))
        with TemporaryDirectory() as directory:
            record=Path(directory)/'target.json';record.write_text(json.dumps({'prefix':prefix,'database':prefix+'-db','network':prefix+'-net','volume':prefix+'-pgdata','status':status}))
            error=None;result=None
            try:result=reconcile(record,lambda path,d:saved.append(json.loads(json.dumps(d))),command)
            except RuntimeError as exc:error=exc
        return calls,saved,result,error

    def test_interrupted_operation_stops_services_first_without_promoting(self):
        calls,saved,result,error=self.run_case()
        self.assertIsNone(error)
        self.assertLess(calls.index(('stop','auth')),calls.index(('stop','db')))
        self.assertEqual(saved[-1]['status'],'interrupted')
        self.assertFalse(result['database_verified'])

    def test_verified_database_status_is_preserved(self):
        _,saved,result,error=self.run_case(status='database-restored')
        self.assertIsNone(error);self.assertTrue(result['database_verified'])
        self.assertEqual(saved[-1]['status'],'database-restored')

    def test_foreign_container_refuses_before_any_stop(self):
        calls,saved,_,error=self.run_case(foreign=True)
        self.assertIsNotNone(error);self.assertFalse(saved)
        self.assertFalse(any(c[0]=='stop' for c in calls))

    def test_service_stop_failure_still_attempts_database(self):
        calls,saved,_,error=self.run_case(fail=True)
        self.assertIsNotNone(error);self.assertIn(('stop','db'),calls)
        self.assertEqual(saved[-1]['reconcile_state'],'stop-failed')

    def test_daemon_failure_is_not_empty_inventory(self):
        calls,saved,_,error=self.run_case(inventory_error=True)
        self.assertIsNotNone(error);self.assertFalse(saved)
        self.assertFalse(any(c[0]=='stop' for c in calls))
