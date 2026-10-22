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
        # Every check answers yes; asked which schemas exist (lab/data_api.py present_sql), `public` does.
        self.target.sql=Mock(side_effect=lambda query,*args,**kwargs:SimpleNamespace(
            stdout='public' if 'FROM pg_catalog.unnest(ARRAY[' in query and 'SELECT name' in query
            # The REST login's role settings, as provisioning leaves them (Runtime.rest_deadlines).
            else f'{self.e}:search_path=pg_catalog,{self.e}:statement_timeout=8s,{self.e}:transaction_timeout=12s' if "coalesce(d.datname, '*')" in query else self.answer))
        self.answer='t'
        self.target.provision_database=Mock(side_effect=AssertionError('schema repair'))
        self.target.hba=Mock(side_effect=AssertionError('HBA rewrite'))
        self.target.activate_services=Mock()

    def test_published_resume_validates_without_database_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);(state/'endpoints.json').write_text(json.dumps({self.e:{}}))
            with patch.object(runtime,'STATE',state),patch.object(runtime.source_fence,'is_fenced',return_value=False),patch.object(runtime,'inspect',return_value={'owned':True}):
                self.target.resume(self.e)
        self.target.activate_services.assert_called_once_with(self.e,{},creating=False)
        # A read may first pin its own session's search path (lab/data_api.py SAFE); that changes nothing stored.
        self.assertTrue(all(call.args[0].removeprefix('SET search_path TO pg_catalog, pg_temp; ').startswith('SELECT')
                            for call in self.target.sql.call_args_list))
        self.target.provision_database.assert_not_called();self.target.hba.assert_not_called()

    def test_missing_publication_refuses_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            with patch.object(runtime,'STATE',state),patch.object(runtime.source_fence,'is_fenced',return_value=False),patch.object(runtime,'inspect',return_value={'owned':True}):
                with self.assertRaisesRegex(RuntimeError,'published'):self.target.resume(self.e)
        self.target.activate_services.assert_not_called()

    def test_resume_stops_a_rest_container_that_is_still_running_before_it_pins(self):
        order=[]
        self.target.rest_deadlines=Mock(side_effect=lambda *a,**k:order.append('pin'))
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);(state/'endpoints.json').write_text(json.dumps({self.e:{}}))
            with patch.object(runtime,'STATE',state),patch.object(runtime.source_fence,'is_fenced',return_value=False),\
                    patch.object(runtime,'inspect',return_value={'owned':True,'State':{'Running':True}}),\
                    patch.object(runtime.lab,'docker',side_effect=lambda *a,**k:order.append(a[:2])):
                self.target.resume(self.e)
        self.assertEqual(order[:2],[('stop',runtime.PREFIX+'-'+self.e+'-rest'),'pin'])

    def test_deadline_drift_is_written_back_not_refused(self):
        # Tenant code can act as the REST login and change its own role settings; a refusal here
        # would let one environment stop the start of every other.
        self.target.rest_deadlines=Mock()
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);(state/'endpoints.json').write_text(json.dumps({self.e:{}}))
            with patch.object(runtime,'STATE',state),patch.object(runtime.source_fence,'is_fenced',return_value=False),patch.object(runtime,'inspect',return_value={'owned':True}):
                self.target.resume(self.e)
        self.target.rest_deadlines.assert_any_call(self.e,rewrite=True,end_sessions=True)
        e=self.e
        def run(answer):
            executed=[]
            runtime.Runtime.rest_deadlines(self.target,e,executor=lambda query,*args:executed.append(query) or SimpleNamespace(stdout=answer),rewrite=True)
            return executed
        # Exactly the platform's three settings in its own database: a start writes nothing.
        self.assertEqual(len(run(f'{e}:search_path=pg_catalog,{e}:statement_timeout=8s,{e}:transaction_timeout=12s')),1)
        # One more setting, here or in another database: every one is reset, then the deadlines written.
        for answer in (f'{e}:search_path=pg_catalog,{e}:statement_timeout=8s,{e}:transaction_timeout=12s,{e}:work_mem=64kB',
                       f'e_{"b"*24}:work_mem=64kB,{e}:search_path=pg_catalog,{e}:statement_timeout=8s,{e}:transaction_timeout=12s',
                       f'{e}:statement_timeout=8s,{e}:transaction_timeout=12s', ''):
            written=run(answer)[-1]
            self.assertIn(f"EXECUTE pg_catalog.format('ALTER ROLE {e}_rest IN DATABASE %I RESET ALL', item.datname)",written)
            self.assertIn("lock_timeout = '5s'",written)
            self.assertIn(f'ALTER ROLE {e}_rest RESET ALL;',written)
            self.assertTrue(written.rstrip().endswith(f"ALTER ROLE {e}_rest IN DATABASE {e} SET transaction_timeout = '12s'; COMMIT;"))
            self.assertIn(f"ALTER ROLE {e}_rest IN DATABASE {e} SET search_path = pg_catalog;",written)

    def test_resume_container_disappearance_cannot_fall_back_to_create(self):
        digest='sha256:'+'a'*64;reference='docker.io/supabase/gotrue@'+digest
        self.target.pins={'auth':{'id':digest,'tag':'supabase/gotrue:v1','digests':[reference]}}
        with patch.object(runtime,'inspect',return_value=None),patch.object(runtime.lab,'docker') as docker:
            docker.return_value=SimpleNamespace(returncode=0,stdout=json.dumps([{'Id':'sha256:'+'b'*64,'RepoDigests':[reference]}]),stderr='')
            with self.assertRaisesRegex(RuntimeError,'cannot create'):
                self.target.launch('fixture','auth',{},'256m',.25,existing_only=True)
            docker.assert_called_once_with('image','inspect',reference,check=False)

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
