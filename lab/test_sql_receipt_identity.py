"""Exact worker receipt, stage and catalog authority for native SQL."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import effect_receipt


class SQLReceiptIdentityTests(unittest.TestCase):
    def test_identity_requires_exact_stage_and_current_catalog_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);runtime='e_'+'a'*24
            token='12345678-1234-1234-1234-123456789abc';claim='22345678-1234-1234-1234-123456789abc'
            receipt={'version':1,'phase':'pending','token':token,'native':'durable-provision-v1','stageProtocol':1,
                     'job':{'environment':'fixture','runtime':runtime,'claim':claim,'attempt':1}}
            (state/'worker-effect.json').write_text(json.dumps(receipt))
            stages=state/'effect-stages';stages.mkdir()
            stage={**receipt,'stage':'database','stageIndex':1}
            (stages/(token+'.json')).write_text(json.dumps(stage))
            with closing(sqlite3.connect(state/'control.sqlite')) as db,db:
                db.execute('CREATE TABLE provision_jobs(environment TEXT,runtime TEXT,claim TEXT,attempt INTEGER,state TEXT)')
                db.execute('INSERT INTO provision_jobs VALUES (?,?,?,?,?)',('fixture',runtime,claim,1,'running'))
            with patch.object(effect_receipt,'require_permission') as ownership,patch.dict(os.environ,SBARBASE_EFFECT_TOKEN=token):
                self.assertEqual(effect_receipt.sql_identity(state,runtime,'database'),(runtime,token,claim,1))
                ownership.assert_called_with(state,runtime)
                with self.assertRaisesRegex(RuntimeError,'stage identity'):effect_receipt.sql_identity(state,runtime,'preflight')
                for field,value in [('claim','different'),('state','failed'),('attempt',2)]:
                    with closing(sqlite3.connect(state/'control.sqlite')) as db,db:db.execute(f'UPDATE provision_jobs SET {field}=?',(value,))
                    with self.assertRaisesRegex(RuntimeError,'catalog claim'):effect_receipt.sql_identity(state,runtime,'database')
                    with closing(sqlite3.connect(state/'control.sqlite')) as db,db:db.execute('UPDATE provision_jobs SET claim=?,state=?,attempt=?',(claim,'running',1))
                for field,value in [('version',True),('stageProtocol',True),('native','component-provision-v1'),('token','wrong')]:
                    (state/'worker-effect.json').write_text(json.dumps({**receipt,field:value}))
                    with self.assertRaisesRegex(RuntimeError,'receipt identity'):effect_receipt.sql_identity(state,runtime,'database')

    def test_missing_receipt_cannot_authorize_direct_native_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'worker receipt'):
                effect_receipt.sql_identity(Path(directory),'e_'+'a'*24,'preflight')
