"""Strict applied evidence gates successful HBA-slot settlement only."""
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
import hba_journal as journal
import hba_reconcile as reconcile
import hba_settlement as settlement


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.state=Path(self.temp.name)
        for name in reconcile.NAMES:(self.state/name).touch(mode=0o600)
        identity={'kind':'startup','startup':str(uuid.uuid4())}
        prepared=atomic_hba.Prepared('a'*64,'b'*64,'local all all trust\n')
        self.record={'version':1,'token':str(uuid.uuid4()),'generation':str(uuid.uuid4()),'container':'a'*64,'expected':'b'*64,'content':prepared.content,'identity':identity,'registry':'c'*64,'binding':authority.operation_binding(prepared,identity)}
        journal.publish(self.state/journal.NAME,self.record)
        original=journal.read_text(self.state/journal.NAME)
        base={'version':1,'journal':self.record,'journal_digest':authority.digest(original)}
        apply.publish(self.state,apply.ATTEMPTS,self.record['token'],{**base,'phase':'apply-started'})
        self.witness={**base,'phase':'applied-reload-acknowledged','content_digest':authority.digest(prepared.content),'parser_errors':0,'reload_acknowledged':True,'activation':'unknown'}
        apply.publish(self.state,apply.COMPLETIONS,self.record['token'],self.witness)
        self.result={'observed_digest':authority.digest(prepared.content)}

    def complete(self):
        with patch.object(reconcile,'retire_locked',return_value=(self.record,self.result)):
            return settlement.complete_applied(Mock(),self.state,target=None)

    def test_exact_evidence_archived_without_job_or_activation_claim(self):
        (self.state/'worker-effect.json').write_bytes(b'pending worker')
        (self.state/'control.sqlite').write_bytes(b'unchanged control')
        outcome=self.complete()
        self.assertEqual(outcome['kind'],'retired-applied-reload-acknowledged')
        self.assertEqual(outcome['activation'],'unknown');self.assertEqual(outcome['witness'],self.witness)
        self.assertEqual(settlement.read(self.state,self.record['token']),outcome)
        self.assertFalse((self.state/journal.NAME).exists())
        self.assertEqual((self.state/'worker-effect.json').read_bytes(),b'pending worker')
        self.assertEqual((self.state/'control.sqlite').read_bytes(),b'unchanged control')

    def test_missing_attempt_or_completion_never_retires_or_unlinks(self):
        for directory in (apply.ATTEMPTS,apply.COMPLETIONS):
            path=self.state/directory/(self.record['token']+'.json');before=path.read_bytes();path.unlink()
            with patch.object(reconcile,'retire_locked') as retire:
                with self.assertRaises(FileNotFoundError):settlement.complete_applied(Mock(),self.state,target=None)
                retire.assert_not_called()
            path.write_bytes(before);path.chmod(0o600)
        self.assertTrue((self.state/journal.NAME).exists())

    def test_wrong_exact_values_and_json_types_are_rejected(self):
        for key,value in [('version',True),('parser_errors',False),('parser_errors',0.0),('reload_acknowledged',1),('journal_digest','d'*64),('content_digest','d'*64),('activation','active'),('unexpected','field')]:
            changed={**self.witness,key:value}
            with self.subTest(key=key,value=value):
                with self.assertRaises(ValueError):apply.validate_completion(changed,self.record,self.witness['journal_digest'])

    def test_raw_journal_change_invalidates_both_evidence_files(self):
        path=self.state/journal.NAME;path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
        with self.assertRaisesRegex(ValueError,'attempt'):self.complete()
        self.assertTrue(path.exists())

    def test_changed_current_file_stays_pending_even_with_valid_witness(self):
        self.result['observed_digest']='d'*64
        with self.assertRaisesRegex(RuntimeError,'desired'):self.complete()
        self.assertTrue((self.state/journal.NAME).exists());self.assertFalse((self.state/settlement.DIRECTORY).exists())

    def test_partial_public_or_symlink_witness_cannot_authorize_settlement(self):
        path=self.state/apply.COMPLETIONS/(self.record['token']+'.json');original=path.read_bytes()
        path.write_text('{')
        with self.assertRaises(ValueError):self.complete()
        path.write_bytes(original);path.chmod(0o644)
        with self.assertRaises(ValueError):self.complete()
        path.unlink();path.symlink_to(self.state/'missing')
        with self.assertRaises(OSError):self.complete()
        self.assertTrue((self.state/journal.NAME).exists())

    def test_final_sync_failure_reports_uncertainty_with_readable_success_archive(self):
        calls=0;actual=os.fsync
        def sync(fd):
            nonlocal calls
            calls+=1
            if calls==4:raise OSError('final directory sync failed')
            actual(fd)
        with patch.object(os,'fsync',side_effect=sync):
            with self.assertRaises(OSError):self.complete()
        self.assertFalse((self.state/journal.NAME).exists())
        outcome=settlement.read(self.state,self.record['token'])
        self.assertEqual(outcome['witness'],self.witness);self.assertEqual(outcome['activation'],'unknown')

    def test_retry_after_durable_archive_never_calls_apply_or_reload(self):
        with patch.object(Path,'unlink',side_effect=OSError('interrupted unlink')):
            with self.assertRaises(OSError):self.complete()
        with patch.object(apply,'execute') as execute,patch.object(apply,'sql') as sql:
            outcome=self.complete()
            execute.assert_not_called();sql.assert_not_called()
        self.assertEqual(outcome['witness'],self.witness)


if __name__=='__main__':unittest.main()
