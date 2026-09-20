"""Write-ahead native stages and the boundary before external mutation."""
import json
import sqlite3
from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import durable_runtime
import effect_receipt


class NativeStageTests(unittest.TestCase):
    def fixture(self,state):
        token='12345678-1234-1234-1234-123456789abc';runtime='e_'+'a'*24
        receipt={'version':1,'phase':'pending','token':token,'native':'durable-provision-v1','stageProtocol':1,
                 'job':{'environment':'fixture','runtime':runtime,'claim':'22345678-1234-1234-1234-123456789abc','attempt':1}}
        (state/'worker-effect.json').write_text(json.dumps(receipt))
        with closing(sqlite3.connect(state/'control.sqlite')) as db, db:
            db.execute('CREATE TABLE provision_jobs(environment TEXT,runtime TEXT,claim TEXT,attempt INTEGER,state TEXT)')
            db.execute('INSERT INTO provision_jobs VALUES (?,?,?,?,?)',('fixture',runtime,receipt['job']['claim'],1,'running'))
        return receipt,runtime,state/'effect-stages'/(token+'.json')

    def test_stages_only_advance_and_outcome_requires_correct_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt,runtime,path=self.fixture(state)
            with patch.dict(os.environ,SBARBASE_EFFECT_TOKEN=receipt['token']),patch.object(effect_receipt,'require_permission'):
                effect_receipt.native_stage(state,runtime,'preflight')
                with self.assertRaises(FileExistsError):effect_receipt.native_stage(state,runtime,'preflight')
                with self.assertRaises(RuntimeError):effect_receipt.native_stage(state,runtime,'storage')
                with self.assertRaises(RuntimeError):effect_receipt.native_outcome(state,runtime,0,'durable-provision-v1')
                effect_receipt.native_stage(state,runtime,'database')
                with self.assertRaises(RuntimeError):effect_receipt.native_outcome(state,runtime,75,'durable-provision-v1')
                for stage in ('services','storage','publication'):effect_receipt.native_stage(state,runtime,stage)
                effect_receipt.native_outcome(state,runtime,0,'durable-provision-v1')
            self.assertEqual(json.loads(path.read_text())['stage'],'publication')
            self.assertTrue((state/'effect-outcomes'/(receipt['token']+'.json')).exists())

    def test_external_mutation_follows_durable_database_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt,runtime,path=self.fixture(state)
            instance=object.__new__(durable_runtime.Runtime);instance.values={'environments':{runtime:{}}}
            instance.sql=Mock(return_value=Mock(stdout='100|3|0'))
            def mutate(*args,**kwargs):
                self.assertEqual(json.loads(path.read_text())['stage'],'database')
                raise RuntimeError('boundary observed')
            instance.sql=Mock(return_value=Mock(stdout='100|3|0'))
            with patch.dict(os.environ,SBARBASE_EFFECT_TOKEN=receipt['token']),\
                 patch.object(effect_receipt,'require_permission'),patch.object(durable_runtime,'STATE',state),\
                 patch.object(durable_runtime,'inspect',return_value={"owned":True}),\
                 patch.object(durable_runtime.resource_admission,'snapshot',return_value=None),\
                 patch.object(durable_runtime.resource_admission,'refusal',return_value=None),\
                 patch.object(durable_runtime.pressure_admission,'snapshot',return_value=None),\
                 patch.object(durable_runtime.pressure_admission,'refusal',return_value=None),\
                 patch.object(durable_runtime.source_fence,'is_fenced',return_value=False),\
                 patch.object(durable_runtime,'GuardedSQL',side_effect=mutate) as effect:
                effect_receipt.native_stage(state,runtime,'preflight')
                with self.assertRaisesRegex(RuntimeError,'boundary observed'):instance.provision(runtime)
                effect.assert_called_once()

    def test_failed_boundary_persistence_prevents_external_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt,runtime,path=self.fixture(state)
            instance=object.__new__(durable_runtime.Runtime);instance.values={'environments':{runtime:{}}}
            instance.sql=Mock(return_value=Mock(stdout='100|3|0'))
            with patch.dict(os.environ,SBARBASE_EFFECT_TOKEN=receipt['token']),\
                 patch.object(effect_receipt,'require_permission'),patch.object(durable_runtime,'STATE',state),\
                 patch.object(durable_runtime,'inspect',return_value={"owned":True}),\
                 patch.object(durable_runtime.resource_admission,'snapshot',return_value=None),\
                 patch.object(durable_runtime.resource_admission,'refusal',return_value=None),\
                 patch.object(durable_runtime.pressure_admission,'snapshot',return_value=None),\
                 patch.object(durable_runtime.pressure_admission,'refusal',return_value=None),\
                 patch.object(durable_runtime.source_fence,'is_fenced',return_value=False),\
                 patch.object(durable_runtime.lab,'provision_environment') as effect:
                effect_receipt.native_stage(state,runtime,'preflight')
                with patch.object(effect_receipt,'atomic_record',side_effect=OSError('injected fsync failure')):
                    with self.assertRaises(OSError):instance.provision(runtime)
                effect.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['stage'],'preflight')

    def test_retained_preflight_credentials_do_not_skip_fresh_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);receipt,runtime,path=self.fixture(state)
            instance=object.__new__(durable_runtime.Runtime);instance.values={'environments':{runtime:{}}}
            with patch.dict(os.environ,SBARBASE_EFFECT_TOKEN=receipt['token']),\
                 patch.object(effect_receipt,'require_permission'),patch.object(durable_runtime,'STATE',state),\
                 patch.object(durable_runtime,'inspect',return_value={'owned':True}),\
                 patch.object(durable_runtime.resource_admission,'snapshot',side_effect=RuntimeError('unavailable')),\
                 patch.object(durable_runtime.lab,'provision_environment') as effect:
                effect_receipt.native_stage(state,runtime,'preflight')
                with self.assertRaisesRegex(RuntimeError,'Resource measurement unavailable'):instance.provision(runtime)
                effect.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['stage'],'preflight')


    def test_startup_resumes_published_environments_only(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            instance=object.__new__(durable_runtime.Runtime)
            instance.values={'environments':{'published':{},'reservation':{},'moved':{}}}
            instance.resume=Mock()
            with patch.object(durable_runtime,'STATE',state),patch.object(durable_runtime.source_fence,'is_fenced',side_effect=lambda sql,e:e=='moved'):
                instance.resume_published_environments()
                instance.resume.assert_not_called()
                (state/'endpoints.json').write_text(json.dumps({'published':{},'moved':{}}))
                instance.resume_published_environments()
                instance.resume.assert_called_once_with('published')
