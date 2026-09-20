"""Source integration refuses legacy adoption, uncertain creation and replay."""
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import atomic_hba
import durable_runtime
import installation_runtime
import hba_apply
import hba_authority
import hba_generation
import hba_runtime
import hba_settlement
import hba_startup
import hba_target


class SourceHBATests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.state=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.lease=self.stack.enter_context(hba_startup.acquire(self.state))
        self.target=hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'b'*64)
        self.generation=str(uuid.uuid4());self.docker=Mock()
        self.existing={'Id':self.target.container_id,'Name':'/fixture-db','Image':self.target.image,
                       'Config':{'Labels':{'io.sbarbase.owner':'fixture'}}}

    def writer(self):
        return hba_runtime.SourceHBA(self.docker,self.state,'fixture-db','fixture',self.target.image,startup=self.lease)

    def test_existing_legacy_container_cannot_initialize(self):
        writer=self.writer()
        with patch.object(self.lease,'initialize') as initialize:
            with self.assertRaises(FileNotFoundError):writer.before_start(self.existing,True)
            initialize.assert_not_called()
        self.docker.assert_not_called()

    def test_missing_container_with_retained_volume_or_pin_requires_migration(self):
        with self.assertRaisesRegex(RuntimeError,'migration'):self.writer().before_start(None,True)
        hba_generation.publish(self.state,self.target,self.generation)
        with self.assertRaisesRegex(RuntimeError,'migration'):self.writer().before_start(None,False)
        self.docker.assert_not_called()

    def test_fresh_start_requires_positive_matching_creation_evidence(self):
        writer=self.writer();writer.before_start(None,False)
        with patch.object(self.lease,'initialize') as initialize:
            for evidence in (False,None,1):
                with self.subTest(evidence=evidence):
                    with self.assertRaisesRegex(RuntimeError,'creation evidence'):writer.ready(self.target.container_id,created=evidence)
            initialize.assert_not_called()
        self.docker.assert_not_called()

    def test_created_container_must_match_captured_id_before_initialization(self):
        writer=self.writer();writer.before_start(None,False)
        with patch.object(hba_target,'capture',return_value=self.target),patch.object(self.lease,'initialize') as initialize:
            with self.assertRaisesRegex(RuntimeError,'identity changed'):writer.ready('c'*64,created=True)
            initialize.assert_not_called()

    def test_pinned_restart_reads_existing_generation_without_initialization(self):
        hba_generation.publish(self.state,self.target,self.generation)
        before=(self.state/hba_generation.NAME).read_bytes()
        writer=self.writer();writer.before_start(self.existing,True)
        with patch.object(hba_target,'capture',return_value=self.target),patch.object(hba_generation,'read_existing',return_value=hba_authority.Snapshot(self.target.container_id,self.generation,hba_authority.encode({'version':1,'generation':self.generation,'revision':str(uuid.uuid4()),'operations':{}}))) as read,patch.object(self.lease,'initialize') as initialize:
            writer.ready(self.target.container_id,created=False)
            initialize.assert_not_called();read.assert_called_once_with(self.docker,self.state,target=self.target)
        self.assertEqual((self.state/hba_generation.NAME).read_bytes(),before)

    def test_pending_backend_authority_blocks_restart_before_new_effects(self):
        hba_generation.publish(self.state,self.target,self.generation)
        writer=self.writer();writer.before_start(self.existing,True)
        snapshot=hba_authority.Snapshot(self.target.container_id,self.generation,hba_authority.encode({'version':1,'generation':self.generation,'revision':str(uuid.uuid4()),'operations':{str(uuid.uuid4()):{'binding':'c'*64,'state':'active'}}}))
        with patch.object(hba_target,'capture',return_value=self.target),patch.object(hba_generation,'read_existing',return_value=snapshot),patch.object(self.lease,'initialize') as initialize:
            with self.assertRaisesRegex(RuntimeError,'requires reconciliation'):writer.ready(self.target.container_id,created=False)
            initialize.assert_not_called()
        self.assertIsNone(writer.target)

    def test_foreign_or_replaced_container_refused_before_start(self):
        hba_generation.publish(self.state,self.target,self.generation)
        for changed in ({'Id':'c'*64},{'Image':'sha256:'+'c'*64},{'Name':'/other'}):
            with self.subTest(changed=changed):
                with self.assertRaises(RuntimeError):self.writer().before_start({**self.existing,**changed},True)
        self.docker.assert_not_called()

    def test_uncertain_apply_never_falls_back_or_retries_in_same_writer(self):
        writer=self.writer();writer.target=self.target
        prepared=atomic_hba.Prepared(self.target.container_id,'c'*64,'local all all trust\n')
        with patch.object(hba_generation,'read_existing'),patch.object(atomic_hba,'prepare',return_value=prepared),\
             patch.object(self.lease,'begin') as begin,patch.object(hba_apply,'execute',side_effect=RuntimeError('uncertain apply')) as execute,\
             patch.object(hba_settlement,'complete_owned') as complete,patch.object(atomic_hba,'replace') as fallback:
            with self.assertRaisesRegex(RuntimeError,'uncertain'):writer.publish(prepared.content)
            with self.assertRaisesRegex(RuntimeError,'already attempted'):writer.publish(prepared.content)
            begin.assert_called_once();execute.assert_called_once();complete.assert_not_called();fallback.assert_not_called()


class StartupEntryTests(unittest.TestCase):
    def test_missing_ownership_refuses_before_constructing_source_or_target(self):
        with patch.object(durable_runtime.effect_receipt,'require_settled'),patch.object(durable_runtime,'Runtime') as source,patch.object(installation_runtime,'TargetRuntime') as target:
            with self.assertRaisesRegex(RuntimeError,'startup ownership'):installation_runtime.main('up')
            source.assert_not_called();target.assert_not_called()

    def test_expired_source_context_refuses_before_service_or_sql_mutation(self):
        source=object.__new__(durable_runtime.Runtime)
        source.hba_writer=Mock();source.hba_writer.startup.verify.side_effect=RuntimeError('expired owner')
        source.launch=Mock();source.sql=Mock()
        with patch.object(durable_runtime.effect_receipt,'require_settled'),patch.object(durable_runtime.lab,'docker',return_value=SimpleNamespace(stdout='')):
            with self.assertRaisesRegex(RuntimeError,'expired'):source.start()
        source.launch.assert_not_called();source.sql.assert_not_called()


class ResourceInspectionTests(unittest.TestCase):
    def test_inspect_error_is_not_absence_when_resource_is_listed(self):
        with patch.object(durable_runtime.lab,'docker',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(stdout='fixture-db\n')]):
            with self.assertRaisesRegex(RuntimeError,'inspection unavailable'):durable_runtime.inspect('container','fixture-db')

    def test_inspect_error_cannot_hide_a_listed_container_id(self):
        for identifier in ('a'*12,'a'*64):
            with self.subTest(identifier=identifier),patch.object(durable_runtime.lab,'docker',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(stdout='a'*64+' fixture-db\n')]):
                with self.assertRaisesRegex(RuntimeError,'inspection unavailable'):durable_runtime.inspect('container',identifier)

    def test_failed_absence_verification_is_fatal(self):
        with patch.object(durable_runtime.lab,'docker',side_effect=[SimpleNamespace(returncode=1),RuntimeError('daemon unavailable')]):
            with self.assertRaisesRegex(RuntimeError,'daemon'):durable_runtime.inspect('volume','fixture-volume')

    def test_successful_listing_can_confirm_absence(self):
        with patch.object(durable_runtime.lab,'docker',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(stdout='another-db\n')]):
            self.assertIsNone(durable_runtime.inspect('container','fixture-db'))


if __name__=='__main__':unittest.main()
