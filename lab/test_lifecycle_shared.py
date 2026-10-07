"""Unadmitted shared refusal checks and pure exact ownership receipt helpers."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lifecycle_shared as shared

RUNTIME='e_'+'a'*24
INSTALLATION='12345678-1234-1234-1234-123456789abc'
RESOURCE='22345678-1234-1234-1234-123456789abc'
FILES='32345678-1234-1234-1234-123456789abc'


def manifest(root):
    path=root/RUNTIME
    path.mkdir()
    metadata=path.stat()
    files={'kind':'directory','id':str(path),'resource':FILES,'installation':INSTALLATION,'runtime':RUNTIME,
           'device':metadata.st_dev,'inode':metadata.st_ino,'marker':'.sbarbase-lifecycle-owner.json'}
    identity={'engine':{'id':'a'*64,'owner':'owned-installation','daemon':'daemon-id'},
        'database':{'name':RUNTIME,'oid':100,'ownerOid':10},
        'roles':[{'name':RUNTIME+'_'+name,'oid':oid,'login':True} for oid,name in enumerate(('auth','rest','storage'),200)],
        'tenant':{'database':'storage_metadata','oid':110,'id':RUNTIME,'rowDigest':hashlib.sha256(b'{}').hexdigest(),'writers':['b'*64]},
        'files':{key:files[key] for key in ('id','resource','device','inode','marker')}}
    return {'kind':'shared-database','id':RUNTIME,'resource':RESOURCE,'installation':INSTALLATION,'runtime':RUNTIME,'identity':identity},files


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.resource,self.files=manifest(self.root)
        self.identity=self.resource['identity']

    def tearDown(self):
        self.folder.cleanup()

    def test_global_roles_and_restore_owner_are_never_enrolled(self):
        shared.validate(self.resource)
        self.identity['roles'][0]['name']='anon'
        with self.assertRaises(shared.SharedRefusal):
            shared.validate(self.resource)
        self.identity['roles'][0]['name']=RUNTIME+'_auth'
        self.identity['engine']['owner']='recovery-target'
        with self.assertRaises(shared.SharedRefusal):
            shared.validate(self.resource)

    def test_replaced_database_oid_and_foreign_comment_refuse(self):
        row={'oid':100,'owner':10,'comment':shared.marker(self.resource)}
        shared.check_database(row,self.identity,self.resource)
        row['oid']=101
        with self.assertRaises(shared.SharedRefusal):
            shared.check_database(row,self.identity,self.resource)
        row['oid']=100
        row['comment']='another resource'
        with self.assertRaises(shared.SharedRefusal):
            shared.check_database(row,self.identity,self.resource,migrated=False)

    def test_missing_role_requires_its_own_pending_effect(self):
        rows=[{**role,'comment':shared.marker(self.resource)} for role in self.identity['roles']][1:]
        with self.assertRaises(shared.SharedRefusal):
            shared.check_roles(rows,self.identity,self.resource,allow_missing={'database'})
        shared.check_roles(rows,self.identity,self.resource,allow_missing={RUNTIME+'_auth'})

    def test_running_shared_writer_is_refused_without_mutation(self):
        class Docker:
            daemon_id='daemon-id'
            def request(self,method,path):
                cid='a'*64 if '/'+('a'*64)+'/' in path else 'b'*64
                return 200,{'Id':cid,'Config':{'Labels':{'io.sbarbase.owner':'owned-installation'}},'State':{'Running':True}}
        with self.assertRaisesRegex(shared.SharedRefusal,'writer_not_drained'):
            shared.engine_identity(Docker(),self.identity,writers=True)

    def test_migration_refuses_unsupported_isolation_before_sql_or_marker_effects(self):
        with patch.object(shared,'engine_identity') as engine,patch.object(shared,'SQL') as sql,\
                patch.object(shared,'write_journal') as receipt,patch.object(shared,'publish_marker') as marker:
            with self.assertRaisesRegex(shared.SharedRefusal,'Shared writer isolation unavailable'):
                shared.migrate(self.resource,None,self.root,self.root,'operation',0,self.files)
            for effect in (engine,sql,receipt,marker):effect.assert_not_called()
        self.assertFalse((Path(self.files['id'])/self.files['marker']).exists())

    def test_marker_helper_refuses_collision_without_replacing_neighbor(self):
        path=Path(self.files['id'])/self.files['marker']
        path.write_text(json.dumps({'installation':'foreign'}))
        original=path.read_bytes()
        receipt_path,receipt=shared.migration_receipt(self.root,self.resource,self.files,RESOURCE,0)
        with self.assertRaisesRegex(shared.SharedRefusal,'marker_collision'):
            shared.publish_marker(Path(self.files['id']),self.resource,self.files,RESOURCE,receipt_path,receipt)
        self.assertEqual(path.read_bytes(),original)
        self.assertFalse(receipt_path.exists())

class LegacyIdentityTests(unittest.TestCase):
    def resource(self):
        config={'Labels':{'io.sbarbase.owner':'installation-owner'},'Env':['PGRST_DB_URI=private']}
        digest=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'kind':'container','id':'a'*64,'resource':RESOURCE,'installation':INSTALLATION,'runtime':RUNTIME,'service':'rest',
            'legacy':{'owner':'installation-owner','image':'sha256:'+'b'*64,'createdAt':'2026-10-06T00:00:00Z','configDigest':digest,
                      'endpoint':'http://10.0.0.2:3000','databaseEngine':'c'*64,'databaseOid':100}},config

    def test_legacy_owner_alone_cannot_establish_enrollment(self):
        resource,config=self.resource()
        class Docker:
            daemon_id='daemon-id'
            def request(self,*args):
                return 200,{'Id':'a'*64,'Image':'sha256:'+'b'*64,'Created':'2026-10-06T00:00:00Z','Config':config}
        with tempfile.TemporaryDirectory() as state:
            with self.assertRaises(FileNotFoundError):
                shared.inspect_legacy_service(Docker(),Path(state),resource)

    def test_stale_config_digest_and_recovery_owner_refuse(self):
        resource,config=self.resource()
        class Docker:
            def request(self,*args):
                return 200,{'Id':'a'*64,'Image':'sha256:'+'b'*64,'Created':'2026-10-06T00:00:00Z','Config':config}
        shared.observe_legacy_service(Docker(),resource)
        config['Env']=['PGRST_DB_URI=changed']
        with self.assertRaisesRegex(shared.SharedRefusal,'positive_identity'):
            shared.observe_legacy_service(Docker(),resource)
        resource['legacy']['owner']='recovery-target'
        with self.assertRaises(shared.SharedRefusal):
            shared.validate_legacy_service(resource)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.resource,self.files=manifest(self.root)
        self.identity=self.resource['identity']

    def tearDown(self):
        self.folder.cleanup()

    def test_running_writer_has_readonly_identity_and_readiness_but_fails_drain(self):
        calls=[]
        class Docker:
            daemon_id='daemon-id'
            def request(self,method,path):
                calls.append(method)
                cid='a'*64 if '/'+('a'*64)+'/' in path else 'b'*64
                return 200,{'Id':cid,'Config':{'Labels':{'io.sbarbase.owner':'owned-installation'},'Healthcheck':{'Test':['CMD','true']}},
                            'State':{'Running':True,'Health':{'Status':'healthy'}}}
        shared.engine_identity(Docker(),self.identity)
        shared.writer_identities(Docker(),self.identity,ready=True)
        with self.assertRaisesRegex(shared.SharedRefusal,'writer_not_drained'):
            shared.writer_identities(Docker(),self.identity,drained=True)
        self.assertTrue(all(method=='GET' for method in calls))

    def test_every_shared_action_refuses_before_container_sql_or_filesystem_effects(self):
        from unittest.mock import Mock
        docker=Mock()
        original=sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        with patch.object(shared,'engine_identity') as engine,patch.object(shared,'SQL') as sql,\
                patch.object(shared,'write_journal') as receipt:
            for action in ('inspect','inspect-quarantined','quarantine','restore','readiness','purge'):
                with self.subTest(action=action),self.assertRaisesRegex(shared.SharedRefusal,'Shared writer isolation unavailable'):
                    shared.execute(self.resource,action,docker,self.root,self.root,RESOURCE,1)
            for effect in (engine,sql,receipt,docker.request):effect.assert_not_called()
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')),original)

    def test_atomic_marker_helper_recovers_before_or_after_publish(self):
        receipt_path,receipt=shared.migration_receipt(self.root,self.resource,self.files,RESOURCE,0)
        rename=shared.os.rename
        def interrupt_before(*args,**kwargs):
            self.assertEqual(json.loads(receipt_path.read_text())['stages'],{'marker':'pending'})
            raise OSError('interrupted before marker rename')
        with patch.object(shared.os,'rename',side_effect=interrupt_before):
            with self.assertRaises(OSError):
                shared.publish_marker(Path(self.files['id']),self.resource,self.files,RESOURCE,receipt_path,receipt)
        self.assertFalse((Path(self.files['id'])/self.files['marker']).exists())
        def interrupt_after(*args,**kwargs):
            rename(*args,**kwargs)
            raise OSError('interrupted after marker rename')
        with patch.object(shared.os,'rename',side_effect=interrupt_after):
            with self.assertRaises(OSError):
                shared.publish_marker(Path(self.files['id']),self.resource,self.files,RESOURCE,receipt_path,receipt)
        shared.publish_marker(Path(self.files['id']),self.resource,self.files,RESOURCE,receipt_path,receipt)
        self.assertEqual(json.loads(receipt_path.read_text())['stages'],{'marker':'done'})
        marker=json.loads((Path(self.files['id'])/self.files['marker']).read_text())
        self.assertEqual(marker,{'installation':INSTALLATION,'runtime':RUNTIME,'resource':FILES})

    def test_pending_migration_refuses_another_operation_identity(self):
        path,record=shared.migration_receipt(self.root,self.resource,self.files,RESOURCE,0)
        record['stages']['sql']='pending'
        shared.write_journal(path,record)
        with self.assertRaisesRegex(shared.SharedRefusal,'identity_mismatch'):
            shared.migration_receipt(self.root,self.resource,self.files,FILES,0)

    def test_sql_close_closes_streams_and_is_idempotent(self):
        import io
        from types import SimpleNamespace
        stdin,stdout,diagnostics=io.BytesIO(),io.BytesIO(),io.BytesIO()
        session=shared.SQL.__new__(shared.SQL)
        session.closed=False
        session.diagnostics=diagnostics
        session.process=SimpleNamespace(stdin=stdin,stdout=stdout,stderr=None,poll=lambda:0,wait=lambda **kwargs:0)
        session.close()
        session.close()
        self.assertTrue(stdin.closed and stdout.closed and diagnostics.closed)

    def test_legacy_service_migration_cannot_bypass_shared_isolation(self):
        from unittest.mock import Mock
        docker=Mock()
        with patch.object(shared,'SQL') as sql,patch.object(shared,'write_journal') as receipt:
            with self.assertRaisesRegex(shared.SharedRefusal,'Shared writer isolation unavailable'):
                shared.migrate_legacy_service({},self.resource,docker,self.root,self.root/'unavailable-secrets')
            for effect in (sql,receipt,docker.request):effect.assert_not_called()

    def test_sql_close_reaps_process_when_stdin_close_has_broken_pipe(self):
        import io
        from types import SimpleNamespace
        events=[]
        class BrokenInput(io.BytesIO):
            def close(self):
                super().close()
                raise BrokenPipeError('closed child input')
        stdin,stdout,diagnostics=BrokenInput(),io.BytesIO(),io.BytesIO()
        session=shared.SQL.__new__(shared.SQL)
        session.closed=False
        session.diagnostics=diagnostics
        session.process=SimpleNamespace(stdin=stdin,stdout=stdout,stderr=None,poll=lambda:None,
                                        terminate=lambda:events.append('terminate'),
                                        wait=lambda **kwargs:events.append('wait'))
        session.close()
        self.assertEqual(events,['terminate','wait'])
        self.assertTrue(stdin.closed and stdout.closed and diagnostics.closed)


if __name__=='__main__':
    unittest.main()
