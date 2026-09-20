"""Per-target authority state and recovery-target HBA writer (mocked docker)."""
from contextlib import ExitStack
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import hba_generation
import hba_runtime
import hba_startup
import hba_target

PREFIX='sbarbase-restore-0123456789ab'


class TargetStateTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.installation=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def test_invalid_or_traversing_prefix_is_refused(self):
        for prefix in ('../../etc','sbarbase-restore-zzzzzzzzzzzz','sbarbase-durable-db','',None,'sbarbase-restore-0123456789ab/../x'):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):hba_runtime.target_state(self.installation,prefix)

    def test_state_is_private_and_below_the_installation(self):
        state=hba_runtime.prepare_target_state(self.installation,PREFIX)
        self.assertEqual(state,self.installation/'targets'/PREFIX)
        self.assertEqual(state.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(state.stat().st_mode&0o777,0o700)
        self.assertTrue(str(state).startswith(str(self.installation)))

    def test_world_readable_state_directory_is_refused(self):
        state=hba_runtime.prepare_target_state(self.installation,PREFIX)
        state.chmod(0o755);self.addCleanup(state.chmod,0o700)
        with self.assertRaisesRegex(ValueError,'private'):
            hba_runtime.prepare_target_state(self.installation,PREFIX)

    def test_two_targets_keep_distinct_locks_and_pins(self):
        other='sbarbase-restore-fedcba987654'
        image='sha256:'+'b'*64
        first_state=hba_runtime.prepare_target_state(self.installation,PREFIX)
        second_state=hba_runtime.prepare_target_state(self.installation,other)
        with hba_startup.acquire(first_state) as lease:
            first=hba_runtime.TargetHBA(Mock(),self.installation,PREFIX,'fixture-db','recovery-target',image,startup=lease)
        with hba_startup.acquire(second_state) as other_lease:
            second=hba_runtime.TargetHBA(Mock(),self.installation,other,'fixture-db','recovery-target',image,startup=other_lease)
        self.assertNotEqual(first.state,second.state)
        target=hba_target.Target('a'*64,'fixture-db','recovery-target',image)
        with hba_startup.acquire(first.state) as lease:
            hba_generation.publish(first.state,target,str(uuid.uuid4()))
            self.assertTrue((first.state/hba_generation.NAME).exists())
            self.assertFalse((second.state/hba_generation.NAME).exists())
            self.assertEqual(len(lease.descriptors),3)
        self.assertTrue(os.path.exists(self.installation/hba_runtime.TARGETS))

    def test_target_writer_refuses_targets_outside_its_own_state(self):
        image='sha256:'+'b'*64
        target=hba_target.Target('a'*64,'fixture-db','recovery-target',image)
        state=hba_runtime.prepare_target_state(self.installation,PREFIX)
        hba_generation.publish(state,target,str(uuid.uuid4()))
        existing={'Id':target.container_id,'Name':'/'+target.name,'Image':image,
                  'Config':{'Labels':{'io.sbarbase.owner':'recovery-target'}}}
        with hba_startup.acquire(state) as lease:
            writer=hba_runtime.TargetHBA(Mock(),self.installation,PREFIX,'fixture-db','recovery-target',image,startup=lease)
            writer.before_start(existing,True)
            self.assertEqual(writer.state,state)

    def test_target_writer_requires_explicit_ownership(self):
        image='sha256:'+'b'*64
        with self.assertRaisesRegex(ValueError,'Exactly one HBA owner'):
            hba_runtime.TargetHBA(Mock(),self.installation,PREFIX,'fixture-db','recovery-target',image)

    def test_fresh_target_creation_requires_creation_evidence(self):
        image='sha256:'+'b'*64
        state=hba_runtime.prepare_target_state(self.installation,PREFIX)
        with hba_startup.acquire(state) as lease:
            writer=hba_runtime.TargetHBA(Mock(),self.installation,PREFIX,'fixture-db','recovery-target',image,startup=lease)
            for evidence in ('false',1,None):
                with self.subTest(evidence=evidence):
                    with self.assertRaises(ValueError):writer.before_create(preexisting_volume=evidence)
            with self.assertRaisesRegex(RuntimeError,'explicit adoption'):
                writer.before_create(preexisting_volume=True)
            self.assertIsNone(writer.fresh)
            writer.before_create(preexisting_volume=False)
            self.assertTrue(writer.fresh)
            with self.assertRaisesRegex(RuntimeError,'already attempted'):
                writer.before_create(preexisting_volume=False)
        self.assertFalse((state/hba_generation.NAME).exists())

    def test_pending_journal_blocks_fresh_target_creation(self):
        image='sha256:'+'b'*64
        state=hba_runtime.prepare_target_state(self.installation,PREFIX)
        with hba_startup.acquire(state) as lease:
            (state/'hba-operation.json').write_text('{}')
            writer=hba_runtime.TargetHBA(Mock(),self.installation,PREFIX,'fixture-db','recovery-target',image,startup=lease)
            with self.assertRaisesRegex(RuntimeError,'requires reconciliation'):
                writer.before_create(preexisting_volume=False)


if __name__=='__main__':unittest.main()