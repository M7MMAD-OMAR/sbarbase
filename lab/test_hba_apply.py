"""Native HBA apply ordering, one-attempt refusal and live startup identity."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import atomic_hba
import hba_apply as apply
import hba_authority as authority
import hba_generation
import hba_journal as journal
import hba_startup
import hba_target
import hba_settlement
import hba_reconcile


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.state=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        generation=str(uuid.uuid4());self.token=str(uuid.uuid4())
        self.target=hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'c'*64)
        hba_generation.publish(self.state,self.target,generation)
        snapshot=authority.Snapshot('a'*64,generation,authority.encode({'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}))
        self.prepared=atomic_hba.Prepared('a'*64,'b'*64,'local all all trust\n')
        self.stack.enter_context(patch.object(hba_target,'require'))
        self.lease=self.stack.enter_context(hba_startup.acquire(self.state))
        self.active=self.lease.begin(Mock(),snapshot,self.prepared,self.token,target=self.target)
        self.stack.enter_context(patch.object(authority,'read',return_value=self.active))
        self.stack.enter_context(patch.object(apply,'file_digest',return_value=authority.digest(self.prepared.content)))
        self.publication=self.stack.enter_context(patch.object(authority,'apply'))
        self.sql=self.stack.enter_context(patch.object(apply,'sql',side_effect=['0','t']))

    def execute(self,context=None):
        return apply.execute(Mock(),self.state,self.lease.descriptors,target=self.target,startup=context or self.lease)

    def completion(self):return self.state/apply.COMPLETIONS/(self.token+'.json')

    def test_attempt_is_persisted_before_apply_and_completion_binds_journal(self):
        def publish(*args):
            value=json.loads((self.state/apply.ATTEMPTS/(self.token+'.json')).read_text())['record']
            self.assertEqual(value['phase'],'apply-started')
            self.assertEqual(value['journal_digest'],authority.digest(journal.read_text(self.state/journal.NAME)))
        self.publication.side_effect=publish
        witness=self.execute()
        self.assertEqual(witness['activation'],'unknown');self.assertTrue(witness['reload_acknowledged'])
        self.assertEqual(json.loads(self.completion().read_text())['record'],witness)
        self.assertEqual(self.completion().stat().st_mode&0o777,0o600)
        self.assertTrue((self.state/journal.NAME).exists())
        with self.assertRaises(FileExistsError):self.execute()
        self.publication.assert_called_once();self.assertEqual(self.sql.call_count,2)

    def test_live_completion_preserves_ownership_without_reapplying(self):
        witness=self.execute()
        record=journal.load(self.state/journal.NAME)
        result={'observed_digest':authority.digest(record['content'])}
        with patch.object(hba_reconcile,'retire_locked',return_value=(record,result)), patch.object(hba_reconcile,'fresh_ownership',side_effect=AssertionError('fresh lock acquisition')):
            outcome=hba_settlement.complete_owned(Mock(),self.state,self.lease.descriptors,target=self.target,startup=self.lease)
        self.assertEqual(outcome['witness'],witness)
        self.assertFalse((self.state/journal.NAME).exists())
        self.assertTrue(self.lease.active)
        self.publication.assert_called_once();self.assertEqual(self.sql.call_count,2)

    def test_live_completion_rejects_fresh_or_expired_context_before_retirement(self):
        self.execute()
        fresh=hba_startup.Startup(self.state,self.lease.descriptors);fresh.attempted=True
        with patch.object(hba_reconcile,'retire_locked') as retire:
            with self.assertRaisesRegex(RuntimeError,'originating'):
                hba_settlement.complete_owned(Mock(),self.state,self.lease.descriptors,target=self.target,startup=fresh)
            self.lease.active=False
            with self.assertRaisesRegex(RuntimeError,'originating'):
                hba_settlement.complete_owned(Mock(),self.state,self.lease.descriptors,target=self.target,startup=self.lease)
            retire.assert_not_called()
        self.assertTrue((self.state/journal.NAME).exists())

    def test_parser_error_never_requests_reload_or_emits_completion(self):
        self.sql.side_effect=['1']
        with self.assertRaisesRegex(RuntimeError,'parser'):self.execute()
        self.assertEqual(self.sql.call_count,1);self.assertFalse(self.completion().exists())
        self.assertTrue((self.state/journal.NAME).exists())
        with self.assertRaises(FileExistsError):self.execute()
        self.publication.assert_called_once()

    def test_reload_refusal_leaves_attempt_and_journal(self):
        self.sql.side_effect=['0','f']
        with self.assertRaisesRegex(RuntimeError,'acknowledged'):self.execute()
        self.assertFalse(self.completion().exists());self.assertTrue((self.state/apply.ATTEMPTS/(self.token+'.json')).exists())

    def test_uncertain_apply_cannot_dispatch_twice(self):
        self.publication.side_effect=RuntimeError('lost apply acknowledgment')
        with self.assertRaisesRegex(RuntimeError,'lost'):self.execute()
        with self.assertRaises(FileExistsError):self.execute()
        self.publication.assert_called_once();self.sql.assert_not_called();self.assertFalse(self.completion().exists())

    def test_fresh_or_expired_context_cannot_resume_startup_apply(self):
        fresh=hba_startup.Startup(self.state,self.lease.descriptors)
        fresh.attempted=True
        with self.assertRaisesRegex(RuntimeError,'originating'):self.execute(fresh)
        self.lease.active=False
        with self.assertRaisesRegex(RuntimeError,'originating'):self.execute()
        self.publication.assert_not_called();self.assertFalse((self.state/apply.ATTEMPTS).exists())

    def test_attempt_file_sync_failure_prevents_apply_and_replay(self):
        count=0;actual=os.fsync
        def sync(fd):
            nonlocal count
            count+=1
            if count==2:raise OSError('attempt sync failed')
            actual(fd)
        with patch.object(os,'fsync',side_effect=sync):
            with self.assertRaises(OSError):self.execute()
        with self.assertRaises(FileExistsError):self.execute()
        self.publication.assert_not_called();self.sql.assert_not_called()


if __name__=='__main__':unittest.main()
