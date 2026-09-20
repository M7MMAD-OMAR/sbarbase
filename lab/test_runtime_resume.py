"""Published resume must not repair schema, reserve credentials or create tenants."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import durable_runtime as runtime


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.e='e_'+'a'*24
        self.target=runtime.Runtime.__new__(runtime.Runtime)
        self.target.values={'environments':{self.e:{}}}
        self.target.sql=Mock(return_value=SimpleNamespace(stdout='t'))
        self.target.provision_database=Mock(side_effect=AssertionError('schema repair'))
        self.target.hba=Mock(side_effect=AssertionError('HBA rewrite'))
        self.target.activate_services=Mock()

    def test_published_resume_validates_without_database_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);(state/'endpoints.json').write_text(json.dumps({self.e:{}}))
            with patch.object(runtime,'STATE',state),patch.object(runtime.source_fence,'is_fenced',return_value=False),patch.object(runtime,'inspect',return_value={'owned':True}):
                self.target.resume(self.e)
        self.target.activate_services.assert_called_once_with(self.e,{},creating=False)
        self.assertTrue(all(call.args[0].startswith('SELECT') for call in self.target.sql.call_args_list))
        self.target.provision_database.assert_not_called();self.target.hba.assert_not_called()

    def test_missing_publication_or_deadline_drift_refuses_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            with patch.object(runtime,'STATE',state),patch.object(runtime.source_fence,'is_fenced',return_value=False),patch.object(runtime,'inspect',return_value={'owned':True}):
                with self.assertRaisesRegex(RuntimeError,'published'):self.target.resume(self.e)
                (state/'endpoints.json').write_text(json.dumps({self.e:{}}))
                self.target.sql.return_value.stdout='f'
                with self.assertRaisesRegex(RuntimeError,'deadlines'):self.target.resume(self.e)
        self.target.activate_services.assert_not_called()

    def test_resume_container_disappearance_cannot_fall_back_to_create(self):
        self.target.pins={'auth':{'id':'fixture'}}
        with patch.object(runtime,'inspect',return_value=None),patch.object(runtime.lab,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'cannot create'):
                self.target.launch('fixture','auth',{},'256m',.25,existing_only=True)
            docker.assert_not_called()

    def test_missing_storage_tenant_on_resume_never_posts(self):
        del self.target.activate_services
        self.target.values['storage_admin']='fixture'
        self.target.launch=Mock();self.target.endpoint=Mock(return_value='http://fixture');self.target.wait=Mock()
        with patch.object(runtime.lab,'auth_configuration',return_value={}),patch.object(runtime.lab,'rest_configuration',return_value={}),patch.object(runtime,'http',return_value=(404,None)) as http:
            with self.assertRaisesRegex(RuntimeError,'Missing Storage tenant'):
                self.target.activate_services(self.e,{},creating=False)
        self.assertEqual(http.call_count,1)
        self.assertEqual(len(http.call_args.args),1)
        self.assertTrue(all(call.kwargs['existing_only'] for call in self.target.launch.call_args_list))
