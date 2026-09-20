"""Real startup flock acquisition, stale contexts and interruption markers."""
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import uuid
from unittest.mock import patch
import atomic_hba
import hba_authority as authority
import hba_journal as journal
import hba_startup as startup
import hba_target
import hba_generation


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.state=Path(self.temp.name)
        generation=str(uuid.uuid4())
        self.snapshot=authority.Snapshot('a'*64,generation,authority.encode({'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}))
        self.prepared=atomic_hba.Prepared('a'*64,'b'*64,'local all all trust\n')
        self.target=hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'c'*64)
        hba_generation.publish(self.state,self.target,generation)
        self.dispatched=0

    def docker(self,*args,**kwargs):
        if args==('inspect','a'*64):
            return SimpleNamespace(stdout=json.dumps([{'Id':'a'*64,'Name':'/fixture-db','Image':self.target.image,'Config':{'Labels':{'io.sbarbase.owner':'fixture'}},'State':{'Running':True}}]))
        self.dispatched+=1
        self.assertEqual(journal.load(self.state/journal.NAME)['identity']['kind'],'startup')

    def begin(self,lease):return lease.begin(self.docker,self.snapshot,self.prepared,str(uuid.uuid4()),target=self.target)

    def test_malformed_descriptor_counts_are_rejected(self):
        for count in (0,1,2,4):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError,'three'):startup.Startup(self.state,range(10,10+count))
        with startup.acquire(self.state) as lease:
            lease.descriptors=()
            with self.assertRaisesRegex(ValueError,'three'):self.begin(lease)
        self.assertEqual(self.dispatched,0)

    def test_fresh_context_begins_once_and_retains_immutable_identity(self):
        with startup.acquire(self.state) as lease:
            self.begin(lease)
            self.assertEqual(journal.load(self.state/journal.NAME)['identity'],lease.identity)
            with self.assertRaisesRegex(RuntimeError,'reconciliation'):self.begin(lease)
        self.assertEqual(self.dispatched,1)
        with self.assertRaisesRegex(RuntimeError,'expired'):self.begin(lease)

    def test_missing_generation_pin_blocks_startup_intent(self):
        (self.state/hba_generation.NAME).unlink()
        with startup.acquire(self.state) as lease:
            with self.assertRaises(FileNotFoundError):self.begin(lease)
        self.assertEqual(self.dispatched,0);self.assertFalse((self.state/journal.NAME).exists())

    def test_pending_or_dangling_markers_block_all_startup(self):
        for name in ('worker-effect.json',journal.NAME):
            for dangling in (False,True):
                with self.subTest(name=name,dangling=dangling):
                    path=self.state/name
                    if dangling:path.symlink_to(self.state/'missing')
                    else:path.write_text('{torn')
                    try:
                        with self.assertRaisesRegex(RuntimeError,'reconciliation'):
                            with startup.acquire(self.state):pass
                    finally:path.unlink()
        self.assertEqual(self.dispatched,0)

    def test_each_fresh_lock_refuses_an_existing_owner(self):
        for name in startup.NAMES:
            with self.subTest(name=name):
                fd=os.open(self.state/name,os.O_CREAT|os.O_RDWR,0o600)
                try:
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    with self.assertRaises(BlockingIOError):
                        with startup.acquire(self.state):pass
                finally:os.close(fd)
        with startup.acquire(self.state):pass

    def test_inherited_worker_stays_locked_after_context_exit(self):
        fd=os.open(self.state/'worker.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with startup.acquire(self.state,worker_fd=fd):pass
            other=os.open(self.state/'worker.lock',os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(other)
        finally:os.close(fd)

    def test_inherited_worker_cannot_bypass_surviving_effect_owner(self):
        worker=os.open(self.state/'worker.lock',os.O_CREAT|os.O_RDWR,0o600)
        effect=os.open(self.state/'effect.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(effect,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                with startup.acquire(self.state,worker_fd=worker):pass
        finally:os.close(effect);os.close(worker)

    def test_marker_created_after_acquisition_blocks_before_dispatch(self):
        with startup.acquire(self.state) as lease:
            (self.state/'worker-effect.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'reconciliation'):self.begin(lease)
        self.assertEqual(self.dispatched,0);self.assertFalse((self.state/journal.NAME).exists())

    def test_lookup_errors_are_not_treated_as_missing_journals(self):
        with patch.object(Path,'lstat',side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):startup.require_clear(self.state)

    def test_failed_attempt_cannot_reuse_context_even_without_a_journal(self):
        with startup.acquire(self.state) as lease:
            with patch.object(startup.hba_target,'require',side_effect=RuntimeError('wrong target')):
                with self.assertRaisesRegex(RuntimeError,'wrong target'):self.begin(lease)
            with self.assertRaisesRegex(RuntimeError,'already attempted'):self.begin(lease)
        self.assertFalse((self.state/journal.NAME).exists());self.assertEqual(self.dispatched,0)

    def test_forked_child_cannot_reuse_parent_context(self):
        with startup.acquire(self.state) as lease:
            pid=os.fork()
            if pid==0:
                try:self.begin(lease)
                except RuntimeError:os._exit(0)
                except BaseException:os._exit(2)
                os._exit(1)
            _,status=os.waitpid(pid,0)
            self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertFalse((self.state/journal.NAME).exists())


if __name__=='__main__':unittest.main()
