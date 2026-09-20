"""Host persistence ordering and exact immutable HBA journal identities."""
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock,patch
import atomic_hba
import hba_authority as authority
import hba_journal as journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'hba-operation.json'
        self.generation=str(uuid.uuid4());self.token=str(uuid.uuid4());self.cid='a'*64
        self.snapshot=authority.Snapshot(self.cid,self.generation,authority.encode({'version':1,'generation':self.generation,'revision':str(uuid.uuid4()),'operations':{}}))
        self.prepared=atomic_hba.Prepared(self.cid,'b'*64,'local all all trust\n')
        self.identity={'kind':'startup','startup':str(uuid.uuid4())}

    def begin(self,docker):return journal.begin(docker,self.path,self.snapshot,self.prepared,self.token,self.identity)

    def test_fsync_file_and_directory_precede_dispatch_and_record_cannot_overwrite(self):
        events=[]
        actual=journal.os.fsync
        def sync(fd):events.append('sync');actual(fd)
        def dispatch(*args,**kwargs):
            self.assertEqual(events,['sync','sync'])
            self.assertEqual(journal.load(self.path)['token'],self.token)
            events.append('dispatch')
        with patch.object(journal.os,'fsync',side_effect=sync):self.begin(dispatch)
        before=self.path.read_bytes()
        docker=Mock()
        with self.assertRaises(FileExistsError):self.begin(docker)
        docker.assert_not_called();self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_file_and_directory_sync_failures_block_dispatch_and_keep_record(self):
        for failure in (1,2):
            with self.subTest(failure=failure):
                directory=Path(self.temp.name)/str(failure);directory.mkdir()
                path=directory/journal.NAME;docker=Mock();count=0
                def sync(fd):
                    nonlocal count
                    count+=1
                    if count==failure:raise OSError('sync failed')
                with patch.object(journal.os,'fsync',side_effect=sync):
                    with self.assertRaises(OSError):journal.begin(docker,path,self.snapshot,self.prepared,self.token,self.identity)
                docker.assert_not_called();self.assertTrue(path.exists())
                with self.assertRaises(FileExistsError):journal.begin(docker,path,self.snapshot,self.prepared,self.token,self.identity)

    def test_lost_register_ack_keeps_exact_intent_and_does_not_retry(self):
        docker=Mock(side_effect=RuntimeError('lost acknowledgment'))
        with self.assertRaisesRegex(RuntimeError,'lost'):self.begin(docker)
        self.assertEqual(docker.call_count,1)
        saved=journal.load(self.path)
        self.assertEqual(saved['registry'],authority.digest(self.snapshot.text))
        self.assertEqual(saved['identity'],self.identity)
        with self.assertRaises(FileExistsError):self.begin(docker)
        self.assertEqual(docker.call_count,1)

    def test_bad_identity_and_container_fail_before_journal_or_dispatch(self):
        docker=Mock()
        for operation in ({'kind':'startup'}, {'kind':'worker','runtime':'e_'+'c'*24,'receipt':self.token,'claim':self.token,'attempt':True}):
            with self.assertRaises(ValueError):journal.begin(docker,self.path,self.snapshot,self.prepared,self.token,operation)
            self.assertFalse(self.path.exists())
        with self.assertRaises(ValueError):journal.begin(docker,self.path,self.snapshot,atomic_hba.Prepared('c'*64,'b'*64,'test\n'),self.token,self.identity)
        docker.assert_not_called()

    def test_incomplete_payload_cannot_publish_or_dispatch(self):
        docker=Mock()
        for content in ('no newline','bad\x00text\n',None):
            prepared=atomic_hba.Prepared(self.cid,'b'*64,content)
            with self.assertRaises(ValueError):journal.begin(docker,self.path,self.snapshot,prepared,self.token,self.identity)
            self.assertFalse(self.path.exists())
        docker.assert_not_called()

    def test_torn_and_symlink_records_are_not_new_intents(self):
        self.path.write_text('{');self.path.chmod(0o600)
        docker=Mock()
        with self.assertRaises(ValueError):journal.inspect(docker,self.path)
        with self.assertRaises(FileExistsError):self.begin(docker)
        directory=Path(self.temp.name)/'links';directory.mkdir()
        link=directory/journal.NAME;link.symlink_to(self.path)
        with self.assertRaises(OSError):journal.load(link)
        docker.assert_not_called()

    def test_public_or_oversized_journal_refuses_inspection(self):
        self.begin(Mock())
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'private'):journal.load(self.path)
        self.path.chmod(0o600)
        with self.path.open('r+b') as target:target.truncate(journal.MAX_BYTES+1)
        with self.assertRaisesRegex(ValueError,'large'):journal.load(self.path)

    def test_inspection_never_infers_file_application_and_rejects_binding_conflict(self):
        self.begin(Mock())
        saved=journal.load(self.path)
        for state in ('absent','active','revoked','conflict'):
            record=authority.decode(self.snapshot.text,self.generation)
            if state!='absent':record['operations'][self.token]={'state':'active' if state=='conflict' else state,'binding':'c'*64 if state=='conflict' else saved['binding']}
            snapshot=authority.Snapshot(self.cid,self.generation,authority.encode(record))
            docker=Mock()
            with patch.object(authority,'read',return_value=snapshot) as read:
                if state=='conflict':
                    with self.assertRaisesRegex(RuntimeError,'binding'):journal.inspect(docker,self.path)
                else:
                    result=journal.inspect(docker,self.path)
                    self.assertEqual(result['authority'],state)
                    self.assertEqual(result['application'],'unknown');self.assertEqual(result['activation'],'unknown')
                read.assert_called_once_with(docker,self.cid,self.generation)
            docker.assert_not_called()


if __name__=='__main__':unittest.main()
