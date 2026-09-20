"""Archive durability must precede releasing the pending HBA journal slot."""
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import atomic_hba
import hba_authority as authority
import hba_journal as journal
import hba_reconcile as reconcile
import hba_settlement as settlement


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        prepared=atomic_hba.Prepared('a'*64,'b'*64,'local all all reject\n')
        identity={'kind':'startup','startup':str(uuid.uuid4())}
        self.record={'version':1,'token':str(uuid.uuid4()),'generation':str(uuid.uuid4()),'container':'a'*64,'expected':'b'*64,
                     'content':prepared.content,'identity':identity,'registry':'c'*64,'binding':authority.operation_binding(prepared,identity)}
        self.result={'observed_content':'matches-before','observed_digest':'b'*64}
        self.state=self.fixture('main')

    def fixture(self,name):
        state=self.root/name;state.mkdir()
        for lock in reconcile.NAMES:(state/lock).touch(mode=0o600)
        journal.publish(state/journal.NAME,self.record)
        (state/'worker-effect.json').write_bytes(b'pending-worker-unchanged')
        (state/'control.sqlite').write_bytes(b'control-unchanged')
        return state

    def cancel(self,state=None):
        with patch.object(reconcile,'retire_locked',return_value=(self.record,self.result)):
            return settlement.cancel_baseline(Mock(),state or self.state,target=None)

    def test_archive_and_parent_are_synced_before_unlink_under_all_locks(self):
        events=[];sync=os.fsync;unlink=Path.unlink
        def synced(fd):events.append('sync');sync(fd)
        def remove(path,*args,**kwargs):
            self.assertGreaterEqual(events.count('sync'),3)
            saved=settlement.read(self.state,self.record['token'])
            self.assertEqual(saved['journal'],self.record)
            for name in reconcile.NAMES:
                fd=os.open(self.state/name,os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:os.close(fd)
            events.append('unlink');return unlink(path,*args,**kwargs)
        with patch.object(os,'fsync',side_effect=synced),patch.object(Path,'unlink',new=remove):outcome=self.cancel()
        self.assertEqual(events[-2:],['unlink','sync'])
        self.assertEqual(outcome['kind'],'retired-baseline-observed')
        self.assertFalse((self.state/journal.NAME).exists())
        self.assertEqual((self.state/'worker-effect.json').read_bytes(),b'pending-worker-unchanged')
        self.assertEqual((self.state/'control.sqlite').read_bytes(),b'control-unchanged')

    def test_pre_unlink_sync_failures_keep_pending_journal(self):
        for failure in (1,2,3):
            state=self.fixture(str(failure));count=0;actual=os.fsync
            def sync(fd):
                nonlocal count
                count+=1
                if count==failure:raise OSError('sync failed')
                actual(fd)
            with patch.object(os,'fsync',side_effect=sync):
                with self.assertRaises(OSError):self.cancel(state)
            self.assertTrue((state/journal.NAME).exists())

    def test_final_sync_failure_leaves_readable_durable_outcome(self):
        count=0;actual=os.fsync
        def sync(fd):
            nonlocal count
            count+=1
            if count==4:raise OSError('final sync failed')
            actual(fd)
        with patch.object(os,'fsync',side_effect=sync):
            with self.assertRaises(OSError):self.cancel()
        self.assertFalse((self.state/journal.NAME).exists())
        self.assertEqual(settlement.read(self.state,self.record['token'])['journal'],self.record)

    def test_retry_after_archive_before_unlink_uses_exact_archive(self):
        with patch.object(Path,'unlink',side_effect=OSError('lost before unlink')):
            with self.assertRaises(OSError):self.cancel()
        original=(self.state/settlement.DIRECTORY/(self.record['token']+'.json')).read_bytes()
        self.cancel()
        self.assertFalse((self.state/journal.NAME).exists())
        self.assertEqual((self.state/settlement.DIRECTORY/(self.record['token']+'.json')).read_bytes(),original)

    def test_nonbaseline_observations_stay_pending(self):
        for status in ('matches-desired','matches-before-and-desired','different'):
            self.result['observed_content']=status
            with self.assertRaisesRegex(RuntimeError,'baseline'):self.cancel()
            self.assertTrue((self.state/journal.NAME).exists())
            self.assertFalse((self.state/settlement.DIRECTORY).exists())

    def test_partial_existing_archive_blocks_slot_release(self):
        parent=settlement.directory(self.state,create=True)
        path=parent/(self.record['token']+'.json');path.write_text('{');path.chmod(0o600)
        with self.assertRaises(ValueError):self.cancel()
        self.assertTrue((self.state/journal.NAME).exists())

    def test_changed_line_endings_are_not_normalized_before_unlink(self):
        actual=settlement.persist
        def persist(state,outcome):
            actual(state,outcome)
            path=state/journal.NAME;path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
        with patch.object(settlement,'persist',side_effect=persist):
            with self.assertRaisesRegex(RuntimeError,'changed'):self.cancel()
        self.assertTrue((self.state/journal.NAME).exists())

    def test_changed_pending_bytes_are_not_removed(self):
        actual=settlement.persist
        def persist(state,outcome):
            actual(state,outcome)
            path=state/journal.NAME;path.write_text(path.read_text()+' ')
        with patch.object(settlement,'persist',side_effect=persist):
            with self.assertRaisesRegex(RuntimeError,'changed'):self.cancel()
        self.assertTrue((self.state/journal.NAME).exists())


if __name__=='__main__':unittest.main()
