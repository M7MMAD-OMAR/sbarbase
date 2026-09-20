"""Adoption intent validation, checkpoint resume and refusal paths (mocked docker)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import tempfile
import unittest
import uuid
import atomic_hba
import hba_adoption as adoption
import hba_authority as authority
import hba_generation
import hba_settlement
import hba_target


def target_fixture(cid='a'*64):
    return hba_target.Target(cid,'fixture-db','fixture','sha256:'+'c'*64)


def inspect_info(target,running):
    return {'Id':target.container_id,'Name':'/'+target.name,'Image':target.image,
            'Config':{'Labels':{'io.sbarbase.owner':target.owner}},
            'State':{'Running':running},
            'Mounts':[{'Type':'volume','Name':'fixture-data','Source':'/var/lib/docker/volumes/fixture-data',
                       'Destination':adoption.PGDATA,'Mode':'z'}]}


LOCK_SIDES=[(1,2),(3,4),(5,6)]

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
        prepared=atomic_hba.Prepared(self.target.container_id,'d'*64,
                                     '# sbarbase-hba-revision: '+str(uuid.uuid4())+'\n'+content)
        snapshot=authority.Snapshot(self.target.container_id,generation,authority.encode(
            {'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}))
        outcome={'version':1,'kind':'retired-applied-reload-acknowledged'}
        def docker_effect(*args,**kwargs):
            if args[0]=='inspect':return SimpleNamespace(stdout=json.dumps([inspect_info(self.target,False)]))
            if args[0]=='exec' and 'pg_isready' in args:return SimpleNamespace(returncode=0)
            if args[:2]==('exec',self.target.container_id) and 'psql' in args:return SimpleNamespace(stdout='1')
            if args[:2]==('exec',self.target.container_id) and args[-1]==adoption.HBA_PATH:
                return SimpleNamespace(stdout=content)
            return Mock()
        self.docker.side_effect=docker_effect
        with patch('hba_startup.require_clear'), lock_patch(), \
             patch('hba_target.observed',return_value=self.target.container_id), \
             patch('atomic_hba.prepare',return_value=prepared) as prepare, \
             patch('hba_authority.read',return_value=snapshot), \
             patch('hba_authority.initialize',return_value=snapshot) as initialize, \
             patch('hba_journal.begin',return_value=None), \
             patch('hba_apply.execute',return_value={'phase':'applied-reload-acknowledged'}) as apply_execute, \
             patch('hba_settlement.complete_owned',return_value=outcome) as complete, \
             patch('hba_apply.file_digest',return_value=authority.digest(prepared.content)):
            completed=adoption.execute(self.docker,self.state,target=self.target)
            self.assertEqual(completed['phase'],'completed')
            self.assertEqual(initialize.call_count,1)
            self.assertEqual(complete.call_count,1)
            self.assertEqual(apply_execute.call_count,1)
            self.assertFalse((self.state/adoption.NAME).exists())
            for phase in adoption.PHASES:
                self.assertTrue((self.state/adoption.CHECKPOINTS/(phase+'.json')).exists())
            # The observed rules are handed to prepare unchanged (prepare adds the marker).
            self.assertEqual(prepare.call_args.args[2],content)
            with self.assertRaisesRegex(RuntimeError,'already completed'):
                adoption.execute(self.docker,self.state,target=self.target)

    def test_resume_after_initialized_pin_observes_generation_without_new_init(self):
        record=self.intent();generation=record['generation']
        hba_generation.publish(self.state,self.target,generation)
        content='local all all trust\n'
        prepared=atomic_hba.Prepared(self.target.container_id,'d'*64,
                                     '# sbarbase-hba-revision: '+str(uuid.uuid4())+'\n'+content)
        snapshot=authority.Snapshot(self.target.container_id,generation,authority.encode(
            {'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}))
        # Simulate the crashed first attempt: database start already checkpointed.
        (self.state/adoption.CHECKPOINTS).mkdir(mode=0o700)
        adoption.checkpoint(self.state,'database-started',
                            {'adoption':record['adoption'],'intent':'0'*64,'mode':'stopped-start'})
        def docker_effect(*args,**kwargs):
            if args[0]=='inspect':return SimpleNamespace(stdout=json.dumps([inspect_info(self.target,False)]))
            if args[0]=='exec' and 'pg_isready' in args:return SimpleNamespace(returncode=0)
            if args[:2]==('exec',self.target.container_id) and 'psql' in args:return SimpleNamespace(stdout='1')
            if args[:2]==('exec',self.target.container_id) and args[-1]==adoption.HBA_PATH:
                return SimpleNamespace(stdout=content)
            return Mock()
        self.docker.side_effect=docker_effect
        with patch('hba_startup.require_clear'), lock_patch(), \
             patch('hba_target.observed',return_value=self.target.container_id), \
             patch('atomic_hba.prepare',return_value=prepared), \
             patch('hba_authority.read',return_value=snapshot), \
             patch('hba_authority.initialize') as initialize, \
             patch('hba_journal.begin',return_value=None), \
             patch('hba_apply.execute',return_value={'phase':'applied-reload-acknowledged'}), \
             patch('hba_settlement.complete_owned',return_value={'kind':'retired-applied-reload-acknowledged'}), \
             patch('hba_apply.file_digest',return_value=authority.digest(prepared.content)):
            adoption.execute(self.docker,self.state,target=self.target)
            initialize.assert_not_called()
            self.assertFalse((self.state/adoption.NAME).exists())

    def test_uncertain_stop_keeps_adoption_incomplete(self):
        record=self.intent();generation=record['generation']
        content='local all all trust\n'
        prepared=atomic_hba.Prepared(self.target.container_id,'d'*64,
                                     '# sbarbase-hba-revision: '+str(uuid.uuid4())+'\n'+content)
        snapshot=authority.Snapshot(self.target.container_id,generation,authority.encode(
            {'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}))
        outcome={'version':1,'kind':'retired-applied-reload-acknowledged'}
        def docker_effect(*args,**kwargs):
            if args[0]=='stop':raise RuntimeError('docker stop failed')
            if args[0]=='inspect':return SimpleNamespace(stdout=json.dumps([inspect_info(self.target,False)]))
            if args[0]=='exec' and 'pg_isready' in args:return SimpleNamespace(returncode=0)
            if args[:2]==('exec',self.target.container_id) and 'psql' in args:return SimpleNamespace(stdout='1')
            if args[:2]==('exec',self.target.container_id) and args[-1]==adoption.HBA_PATH:
                return SimpleNamespace(stdout=content)
            return Mock()
        self.docker.side_effect=docker_effect
        with patch('hba_startup.require_clear'), lock_patch(), \
             patch('hba_target.observed',return_value=self.target.container_id), \
             patch('atomic_hba.prepare',return_value=prepared), \
             patch('hba_authority.read',return_value=snapshot), \
             patch('hba_authority.initialize',return_value=snapshot), \
             patch('hba_journal.begin',return_value=None), \
             patch('hba_apply.execute',return_value={'phase':'applied-reload-acknowledged'}), \
             patch('hba_settlement.complete_owned',return_value=outcome), \
             patch('hba_apply.file_digest',return_value=authority.digest(prepared.content)):
            with self.assertRaisesRegex(RuntimeError,'docker stop failed'):
                adoption.execute(self.docker,self.state,target=self.target)
        self.assertTrue((self.state/adoption.NAME).exists())
        self.assertFalse((self.state/adoption.CHECKPOINTS/'source-stopped.json').exists())
        self.assertFalse((self.state/adoption.CHECKPOINTS/'completed.json').exists())


if __name__=='__main__':unittest.main()
