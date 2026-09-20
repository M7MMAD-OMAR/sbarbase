"""Fresh ownership and conservative results for exact HBA authority retirement."""
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import uuid
import atomic_hba
import hba_authority as authority
import hba_journal as journal
import hba_reconcile as reconcile
import hba_target


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.state=Path(self.temp.name)
        for name in reconcile.NAMES:(self.state/name).touch(mode=0o600)
        self.generation=str(uuid.uuid4());self.token=str(uuid.uuid4());self.cid='a'*64
        self.prepared=atomic_hba.Prepared(self.cid,'b'*64,'local all all reject\n')
        self.target=hba_target.Target(self.cid,'fixture-db','fixture','sha256:'+'c'*64)
        initial=self.snapshot('absent')
        journal.begin(Mock(),self.state/journal.NAME,initial,self.prepared,self.token,{'kind':'startup','startup':str(uuid.uuid4())})
        self.record=journal.load(self.state/journal.NAME)
        self.original=(self.state/journal.NAME).read_bytes()
        self.protected={'worker-effect.json':b'opaque pending receipt','control.sqlite':b'opaque control state'}
        for name,content in self.protected.items():(self.state/name).write_bytes(content)

    def snapshot(self,status):
        entries={}
        if status!='absent':entries[self.token]={'binding':self.record['binding'],'state':status}
        return authority.Snapshot(self.cid,self.generation,authority.encode({'version':1,'generation':self.generation,'revision':str(uuid.uuid4()),'operations':entries}))

    def test_absent_active_and_revoked_retire_only_exact_token(self):
        for status in ('absent','active','revoked'):
            with self.subTest(status=status),ExitStack() as stack:
                read=stack.enter_context(patch.object(authority,'read',side_effect=[self.snapshot(status),self.snapshot('revoked')]))
                update=stack.enter_context(patch.object(authority,'update'))
                stack.enter_context(patch.object(hba_target,'require'))
                docker=Mock(return_value=SimpleNamespace(stdout='b'*64+'  /etc/postgresql/pg_hba.conf\n'))
                result=reconcile.retire(docker,self.state,target=self.target)
                self.assertEqual(result['authority'],'revoked');self.assertEqual(result['observed_content'],'matches-before')
                self.assertEqual(result['application'],'unknown');self.assertEqual(result['activation'],'unknown')
                self.assertEqual(update.call_count,0 if status=='revoked' else 1)
                if status!='revoked':
                    self.assertEqual(update.call_args.args[2:],(self.token,self.record['binding']))
                    self.assertEqual(update.call_args.kwargs,{'revoke':True})
                self.assertEqual((self.state/journal.NAME).read_bytes(),self.original)
                for name,content in self.protected.items():self.assertEqual((self.state/name).read_bytes(),content)
                docker.assert_called_once_with('exec',self.cid,'sha256sum','/etc/postgresql/pg_hba.conf')

    def test_surviving_lock_holder_blocks_before_any_backend_read(self):
        for name in reconcile.NAMES:
            descriptor=os.open(self.state/name,os.O_RDWR)
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
                docker=Mock()
                with self.assertRaises(BlockingIOError):reconcile.retire(docker,self.state,target=self.target)
                docker.assert_not_called()
            finally:os.close(descriptor)

    def test_uncertain_revocation_preserves_journal_and_never_observes_success(self):
        docker=Mock()
        with patch.object(authority,'read',return_value=self.snapshot('active')),patch.object(hba_target,'require'),patch.object(authority,'update',side_effect=RuntimeError('lost acknowledgment')) as update:
            with self.assertRaisesRegex(RuntimeError,'lost'):reconcile.retire(docker,self.state,target=self.target)
            update.assert_called_once();docker.assert_not_called()
        self.assertEqual((self.state/journal.NAME).read_bytes(),self.original)

    def test_missing_registry_or_binding_conflict_refuses_mutation(self):
        for missing in (True,False):
            value=self.snapshot('active')
            record=authority.decode(value.text,self.generation);record['operations'][self.token]['binding']='d'*64
            bad=authority.Snapshot(self.cid,self.generation,authority.encode(record))
            with patch.object(authority,'read',side_effect=RuntimeError('missing') if missing else None,return_value=bad),patch.object(hba_target,'require'),patch.object(authority,'update') as update:
                with self.assertRaises(RuntimeError):reconcile.retire(Mock(),self.state,target=self.target)
                update.assert_not_called()

    def test_other_active_operation_is_not_retired(self):
        value=self.snapshot('absent');record=authority.decode(value.text,self.generation)
        record['operations'][str(uuid.uuid4())]={'binding':'d'*64,'state':'active'}
        other=authority.Snapshot(self.cid,self.generation,authority.encode(record))
        with patch.object(authority,'read',return_value=other),patch.object(hba_target,'require'),patch.object(authority,'update') as update:
            with self.assertRaisesRegex(RuntimeError,'Another'):reconcile.retire(Mock(),self.state,target=self.target)
            update.assert_not_called()

    def test_content_match_is_not_application_or_activation_proof(self):
        for digest,label in ((authority.digest(self.prepared.content),'matches-desired'),('d'*64,'different')):
            with patch.object(authority,'read',return_value=self.snapshot('revoked')),patch.object(hba_target,'require'):
                result=reconcile.retire(Mock(return_value=SimpleNamespace(stdout=digest+'  /etc/postgresql/pg_hba.conf')),self.state,target=self.target)
                self.assertEqual(result['observed_content'],label)
                self.assertEqual(result['application'],'unknown');self.assertEqual(result['activation'],'unknown')


if __name__=='__main__':unittest.main()
