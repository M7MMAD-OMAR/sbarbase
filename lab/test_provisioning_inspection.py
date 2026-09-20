"""Read-only inspector snapshots, evidence binding and secret redaction."""
import fcntl
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import provisioning_inspection as inspection


class ProvisioningInspectionTests(unittest.TestCase):
    def fixture(self,state):
        for name in ('worker.lock','effect.lock','operation.lock'):(state/name).touch()
        job={'environment':'environment-id','runtime':'e_'+'a'*24,'claim':'private-claim','attempt':1}
        receipt={'version':1,'phase':'pending','token':'12345678-1234-1234-1234-123456789abc','native':'durable-provision-v1','job':job}
        (state/'worker-effect.json').write_text(json.dumps(receipt))
        with closing(sqlite3.connect(state/'control.sqlite')) as db,db:
            db.executescript('CREATE TABLE provision_jobs(environment TEXT,runtime TEXT,state TEXT,attempt INTEGER,claim TEXT); CREATE TABLE provision_effect_results(environment TEXT,attempt INTEGER,runtime TEXT,claim TEXT,exit_code INTEGER);')
            db.execute('INSERT INTO provision_jobs VALUES (?,?,?,?,?)',('environment-id',job['runtime'],'running',1,job['claim']))
        return receipt

    def fake_docker(self,*args,**kwargs):
        if args[0]=='ps':return 'a'*64 if args[-1].endswith('durable-upstream') else ''
        if args[0]=='inspect':return json.dumps([{'Id':'a'*64,'Name':'/sbarbase-durable-db',
            'Config':{'Labels':{'io.sbarbase.owner':'durable-upstream'},'Env':['PASSWORD=do-not-print']},
            'State':{'Running':False,'Error':'sensitive-error'},'ExecIDs':None}])
        self.fail('Stopped database must not receive exec')

    def test_unresolved_snapshot_does_not_mutate_or_expose_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt=self.fixture(state)
            before={p.name:p.read_bytes() for p in state.iterdir()}
            result=inspection.inspect_state(state,self.fake_docker)
            self.assertEqual(result['catalog']['status'],'current_claim_matches')
            self.assertEqual(result['native_witness'],'missing');self.assertFalse(result['safe_to_replay'])
            self.assertEqual(result['database']['status'],'source_stopped_not_queried')
            self.assertEqual(result['next_action'],'inspect_unresolved_effects')
            text=json.dumps(result)
            for secret in ('do-not-print','sensitive-error','private-claim',receipt['token']):self.assertNotIn(secret,text)
            self.assertEqual(before,{p.name:p.read_bytes() for p in state.iterdir()})

    def test_busy_and_missing_locks_do_not_contact_docker_or_create_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            with patch.object(inspection,'owned_inventory') as inventory:
                self.assertEqual(inspection.inspect_state(state)['status'],'lock_files_missing')
                self.assertEqual(list(state.iterdir()),[])
                self.fixture(state)
                with (state/'effect.lock').open('r') as held:
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    self.assertEqual(inspection.inspect_state(state)['status'],'busy')
                inventory.assert_not_called()

    def test_matching_witness_is_observation_only_and_mismatch_blocks_recommendation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt=self.fixture(state);outcomes=state/'effect-outcomes';outcomes.mkdir()
            path=outcomes/(receipt['token']+'.json')
            path.write_text(json.dumps({**receipt,'phase':'native-completed','exitCode':0}))
            result=inspection.inspect_state(state,self.fake_docker)
            self.assertEqual(result['native_witness'],'matching')
            self.assertEqual(result['next_action'],'settle_known_outcome_under_fresh_lease')
            self.assertFalse(result['safe_to_replay']);self.assertTrue((state/'worker-effect.json').exists())
            with closing(sqlite3.connect(state/'control.sqlite')) as db,db:db.execute("UPDATE provision_jobs SET claim='different'")
            result=inspection.inspect_state(state,self.fake_docker)
            self.assertEqual(result['catalog']['status'],'mismatch')
            self.assertEqual(result['next_action'],'inspect_unresolved_effects')
            path.write_text('{partial')
            self.assertEqual(inspection.inspect_state(state,self.fake_docker)['native_witness'],'malformed')

    def test_docker_failures_and_wrong_owner_are_not_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);self.fixture(state)
            def failure(*args,**kwargs):raise RuntimeError('sensitive daemon output')
            result=inspection.inspect_state(state,failure)
            self.assertEqual(result['docker_status'],'unavailable');self.assertIsNone(result['containers'])
            self.assertNotIn('sensitive daemon output',json.dumps(result))
            def collision(*args,**kwargs):
                value=self.fake_docker(*args,**kwargs)
                return value.replace('durable-upstream','unrelated') if args[0]=='inspect' else value
            self.assertEqual(inspection.inspect_state(state,collision)['docker_status'],'unavailable')

    def test_database_observation_is_bounded_read_only_and_uses_exact_owned_id(self):
        calls=[]
        def command(*args,**kwargs):
            calls.append((args,kwargs));return json.dumps({'database_exists':True,'allows_connections':False,'sessions':0,'scoped_roles':3,'secret':'discard'})
        inventory=[{'name':'sbarbase-durable-db','owner':'durable-upstream','id':'a'*64,'running':True}]
        result=inspection.database_status(inventory,'e_'+'a'*24,command)
        self.assertEqual(result['status'],'observed');self.assertNotIn('secret',result)
        self.assertEqual(calls[0][0][2],'a'*64)
        sql=calls[0][1]['data'];self.assertIn('BEGIN READ ONLY',sql);self.assertIn("statement_timeout='2s'",sql)
        self.assertNotIn('rolpassword',sql);self.assertNotIn('SELECT *',sql)
        with self.assertRaises(ValueError):inspection.database_status(inventory,"'; DROP DATABASE x",command)
        self.assertEqual(len(calls),1)

    def test_boolean_versions_and_unobserved_owned_source_are_not_valid_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt=self.fixture(state)
            (state/'worker-effect.json').write_text(json.dumps({**receipt,'version':True}))
            result=inspection.inspect_state(state,self.fake_docker)
            self.assertEqual(result['receipt_status'],'malformed')
            (state/'worker-effect.json').write_text(json.dumps(receipt))
            outcomes=state/'effect-outcomes';outcomes.mkdir()
            (outcomes/(receipt['token']+'.json')).write_text(json.dumps({**receipt,'version':True,'phase':'native-completed','exitCode':0}))
            self.assertEqual(inspection.inspect_state(state,self.fake_docker)['native_witness'],'mismatch')
            with patch.object(inspection,'docker') as command:
                self.assertEqual(inspection.database_status([],receipt['job']['runtime'],command)['status'],'owned_source_not_observed')
                command.assert_not_called()
