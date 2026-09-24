"""Adoption intent validation, checkpoint resume, refusal and review-fix paths."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import tempfile
import unittest
import uuid
import atomic_hba
import hba_adoption as adoption
import hba_authority as authority
import hba_apply
import hba_generation
import hba_settlement
import hba_target


def target_fixture(cid='a'*64):
    return hba_target.Target(cid,'fixture-db','fixture','sha256:'+'c'*64)


def inspect_info(target,running,mounts=None):
    return {'Id':target.container_id,'Name':'/'+target.name,'Image':target.image,
            'Config':{'Labels':{'io.sbarbase.owner':target.owner}},
            'State':{'Running':running},
            'Mounts':mounts if mounts is not None else [
                {'Type':'volume','Name':'fixture-data','Source':'/var/lib/docker/volumes/fixture-data',
                 'Destination':adoption.PGDATA,'Mode':'z'}]}


def lock_patch():
    return patch('hba_startup.ownership.require_lock',
                 side_effect=lambda state,name,descriptor:(name,))


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name);self.target=target_fixture()
        self.docker=Mock()

    def docker_returns(self,info):
        self.docker.return_value=SimpleNamespace(stdout=json.dumps([info]));return info

    def intent(self):
        self.docker_returns(inspect_info(self.target,False))
        return adoption.publish_intent(self.docker,self.state,name=self.target.name,owner=self.target.owner,image=self.target.image)

    def effect(self,content,running=False,mounts=None,backend='no'):
        def docker_effect(*args,**kwargs):
            if args[0]=='inspect':return SimpleNamespace(stdout=json.dumps([inspect_info(self.target,running,mounts)]))
            if args[0]=='exec' and 'pg_isready' in args:return SimpleNamespace(returncode=0)
            if args[:4]==('exec',self.target.container_id,'sh','-c') and adoption.BACKEND_REGISTRY in args[4]:
                return SimpleNamespace(stdout=backend)
            if args[:2]==('exec',self.target.container_id) and 'psql' in args:return SimpleNamespace(stdout='1')
            if args[:2]==('exec',self.target.container_id) and args[-1]==adoption.HBA_PATH:
                return SimpleNamespace(stdout=content)
            return Mock()
        return docker_effect

    def prepared_fixture(self,content,generation):
        prepared=atomic_hba.Prepared(self.target.container_id,'d'*64,
                                     '# sbarbase-hba-revision: '+str(uuid.uuid4())+'\n'+content)
        snapshot=authority.Snapshot(self.target.container_id,generation,authority.encode(
            {'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}))
        outcome={'version':1,'kind':'retired-applied-reload-acknowledged'}
        return prepared,snapshot,outcome

    def stage_patches(self,prepared,snapshot,outcome):
        items=[patch('hba_startup.require_clear'),lock_patch(),
               patch('hba_target.observed',return_value=self.target.container_id),
               patch('hba_adoption.time.sleep'),
               patch('atomic_hba.prepare',return_value=prepared),
               patch('hba_authority.read',return_value=snapshot),
               patch('hba_journal.begin',return_value=None),
               patch('hba_apply.execute',return_value={'phase':'applied-reload-acknowledged'}),
               patch('hba_settlement.complete_owned',return_value=outcome),
               patch('hba_apply.file_digest',return_value=authority.digest(prepared.content))]
        for item in items:item.start();self.addCleanup(item.stop)
        return items

    def test_intent_captures_stopped_inventory_and_is_exclusive(self):
        record=self.intent()
        self.assertEqual(record['initial_state'],'stopped')
        self.assertEqual(record['volume'],'fixture-data')
        self.assertNotEqual(record['adoption'],record['generation'])
        self.assertEqual(adoption.load(self.state),record)
        with self.assertRaises(FileExistsError):
            self.intent()

    def test_intent_refuses_running_or_foreign_owner_or_mountless_source(self):
        self.docker_returns(inspect_info(self.target,True))
        with self.assertRaisesRegex(RuntimeError,'not stopped'):
            adoption.publish_intent(self.docker,self.state,name=self.target.name,owner=self.target.owner,image=self.target.image)
        foreign=inspect_info(self.target,False)
        foreign['Config']['Labels']['io.sbarbase.owner']='foreign-owner'
        self.docker_returns(foreign)
        with self.assertRaisesRegex(RuntimeError,'identity changed'):
            adoption.publish_intent(self.docker,self.state,name=self.target.name,owner=self.target.owner,image=self.target.image)
        info=inspect_info(self.target,False);info['Mounts']=[]
        self.docker_returns(info)
        with self.assertRaisesRegex(RuntimeError,'pgdata mount missing'):
            adoption.publish_intent(self.docker,self.state,name=self.target.name,owner=self.target.owner,image=self.target.image)

    def test_completed_adoption_refuses_repeat(self):
        self.intent()
        (self.state/adoption.CHECKPOINTS).mkdir(mode=0o700)
        (self.state/adoption.CHECKPOINTS/'completed.json').write_text(authority.encode(
            {'version':1,'phase':'completed','adoption':str(uuid.uuid4()),'intent':'0'*64,'target':{'foreign':True}}))
        with self.assertRaisesRegex(RuntimeError,'already completed'):
            adoption.execute(self.docker,self.state,target=self.target)

    def test_conflicting_generation_pin_blocks_adoption_before_start(self):
        self.intent()
        hba_generation.publish(self.state,self.target,str(uuid.uuid4()))
        with lock_patch(),patch('hba_startup.require_clear'):
            with self.assertRaisesRegex(RuntimeError,'conflicts with adoption intent'):
                adoption.execute(self.docker,self.state,target=self.target)
        self.assertEqual([call for call in self.docker.call_args_list if call.args[0]=='start'],[])

    def test_full_adoption_preserves_rules_and_never_repeats_init(self):
        record=self.intent();generation=record['generation']
        content='local all all trust\nhost all all 0.0.0.0/0 reject\n'
        prepared,snapshot,outcome=self.prepared_fixture(content,generation)
        self.docker.side_effect=self.effect(content)
        self.stage_patches(prepared,snapshot,outcome)
        with patch('hba_authority.initialize',return_value=snapshot) as initialize:
            completed=adoption.execute(self.docker,self.state,target=self.target)
            self.assertEqual(initialize.call_count,1)
        self.assertEqual(completed['phase'],'completed')
        self.assertFalse((self.state/adoption.NAME).exists())
        for phase in adoption.PHASES:
            self.assertTrue((self.state/adoption.CHECKPOINTS/(phase+'.json')).exists())
        self.assertEqual(adoption.read_checkpoint(self.state,'generation-initialized')['mode'],'initialized')
        with self.assertRaisesRegex(RuntimeError,'already completed'):
            adoption.execute(self.docker,self.state,target=self.target)

    def test_resume_after_initialized_pin_observes_generation_without_new_init(self):
        record=self.intent();generation=record['generation']
        hba_generation.publish(self.state,self.target,generation)
        content='local all all trust\n'
        prepared,snapshot,outcome=self.prepared_fixture(content,generation)
        # Simulate the crashed first attempt: database start already checkpointed.
        (self.state/adoption.CHECKPOINTS).mkdir(mode=0o700)
        adoption.checkpoint(self.state,'database-started',
                            {'adoption':record['adoption'],'intent':'0'*64,'mode':'stopped-start'})
        self.docker.side_effect=self.effect(content,backend='yes')
        self.stage_patches(prepared,snapshot,outcome)
        with patch('hba_authority.initialize') as initialize:
            adoption.execute(self.docker,self.state,target=self.target)
            initialize.assert_not_called()
        self.assertFalse((self.state/adoption.NAME).exists())

    def test_uncertain_stop_keeps_adoption_incomplete(self):
        record=self.intent();generation=record['generation']
        content='local all all trust\n'
        prepared,snapshot,outcome=self.prepared_fixture(content,generation)
        base=self.effect(content)
        def docker_effect(*args,**kwargs):
            if args[0]=='stop':raise RuntimeError('docker stop failed')
            return base(*args,**kwargs)
        self.docker.side_effect=docker_effect
        self.stage_patches(prepared,snapshot,outcome)
        with patch('hba_authority.initialize',return_value=snapshot):
            with self.assertRaisesRegex(RuntimeError,'docker stop failed'):
                adoption.execute(self.docker,self.state,target=self.target)
        self.assertTrue((self.state/adoption.NAME).exists())
        self.assertFalse((self.state/adoption.CHECKPOINTS/'source-stopped.json').exists())
        self.assertFalse((self.state/adoption.CHECKPOINTS/'completed.json').exists())

    # MF-1: the source-stopped to completed window must be resumable.
    def test_resume_after_source_stopped_reaches_completion_without_database_work(self):
        record=self.intent()
        (self.state/adoption.CHECKPOINTS).mkdir(mode=0o700)
        for phase in ('database-started','generation-initialized','hba-completed','source-stopped'):
            adoption.checkpoint(self.state,phase,{'adoption':record['adoption'],'intent':'0'*64,'mode':'x'})
        calls=[]
        def docker_effect(*args,**kwargs):
            calls.append(args)
            if args[0]=='inspect':return SimpleNamespace(stdout=json.dumps([inspect_info(self.target,False)]))
            return Mock()
        self.docker.side_effect=docker_effect
        with patch('hba_startup.require_clear'),lock_patch(),patch('hba_apply.execute') as apply_execute, \
             patch('hba_apply.file_digest'),patch('hba_settlement.complete_owned') as complete:
            completed=adoption.execute(self.docker,self.state,target=self.target)
        self.assertEqual(completed['phase'],'completed')
        self.assertEqual(apply_execute.call_count,0)
        self.assertEqual(complete.call_count,0)
        self.assertFalse(any(args[0] in ('start','stop') for args in calls))
        self.assertFalse(any(args[0]=='exec' for args in calls))

    # MF-2: a preexisting backend marker must refuse without writing a pin.
    def test_preexisting_backend_authority_refuses_without_publishing_pin(self):
        record=self.intent()
        content='local all all trust\n'
        prepared,snapshot,outcome=self.prepared_fixture(content,record['generation'])
        self.docker.side_effect=self.effect(content,backend='yes')
        self.stage_patches(prepared,snapshot,outcome)
        with patch('hba_authority.initialize') as initialize:
            with self.assertRaisesRegex(RuntimeError,'explicit adjudication'):
                adoption.execute(self.docker,self.state,target=self.target)
            initialize.assert_not_called()
        self.assertFalse((self.state/hba_generation.NAME).exists())

    # MF-2: a durable pin with no committed marker resolves by one same-generation INIT.
    def test_pin_without_committed_marker_dispatches_same_generation_once(self):
        record=self.intent();generation=record['generation']
        hba_generation.publish(self.state,self.target,generation)
        content='local all all trust\n'
        prepared,snapshot,outcome=self.prepared_fixture(content,generation)
        self.docker.side_effect=self.effect(content,backend='no')
        self.stage_patches(prepared,snapshot,outcome)
        with patch('hba_authority.initialize',return_value=snapshot) as initialize:
            adoption.execute(self.docker,self.state,target=self.target)
            self.assertEqual(initialize.call_count,1)
            self.assertEqual(initialize.call_args.args[2],generation)
        self.assertEqual(adoption.read_checkpoint(self.state,'generation-initialized')['mode'],'dispatched-after-pin')

    # MF-3: an unreadable checkpoint is not absent, and must not replay the HBA stage.
    @unittest.skipIf(os.geteuid()==0,'root reads a mode 000 file, so an unreadable checkpoint cannot be made')
    def test_unreadable_checkpoint_blocks_instead_of_replaying_hba(self):
        record=self.intent();generation=record['generation']
        content='local all all trust\n'
        prepared,snapshot,outcome=self.prepared_fixture(content,generation)
        (self.state/adoption.CHECKPOINTS).mkdir(mode=0o700)
        for phase in ('database-started','generation-initialized','hba-completed'):
            adoption.checkpoint(self.state,phase,{'adoption':record['adoption'],'intent':'0'*64,'mode':'x'})
        target_path=self.state/adoption.CHECKPOINTS/'hba-completed.json'
        target_path.chmod(0o000);self.addCleanup(target_path.chmod,0o600)
        self.docker.side_effect=self.effect(content,backend='yes')
        self.stage_patches(prepared,snapshot,outcome)
        with self.assertRaises(PermissionError):
            adoption.execute(self.docker,self.state,target=self.target)
        hba_apply.execute.assert_not_called()

    # MF-4: a symlinked checkpoint is rejected rather than trusted.
    def test_symlinked_checkpoint_is_rejected(self):
        record=self.intent()
        (self.state/adoption.CHECKPOINTS).mkdir(mode=0o700)
        adoption.checkpoint(self.state,'database-started',
                            {'adoption':record['adoption'],'intent':'0'*64,'mode':'x'})
        real=self.state/'elsewhere.json'
        real.write_text(authority.encode({'version':1,'phase':'database-started','adoption':record['adoption'],'intent':'0'*64,'mode':'x'}))
        real.chmod(0o600)
        link=self.state/adoption.CHECKPOINTS/'database-started.json'
        link.unlink();link.symlink_to(real)
        with self.assertRaises(OSError):
            adoption.read_checkpoint(self.state,'database-started')

    # MF-5: mount identity is revalidated before the source is started.
    def test_changed_pgdata_mount_refuses_before_start(self):
        self.intent()
        changed=[{'Type':'volume','Name':'other-data','Source':'/var/lib/docker/volumes/other-data',
                  'Destination':adoption.PGDATA,'Mode':'z'}]
        self.docker.side_effect=self.effect('local all all trust\n',mounts=changed)
        with patch('hba_startup.require_clear'),lock_patch():
            with self.assertRaisesRegex(RuntimeError,'mount identity changed'):
                adoption.execute(self.docker,self.state,target=self.target)
        self.assertEqual([call for call in self.docker.call_args_list if call.args[0]=='start'],[])


if __name__=='__main__':unittest.main()