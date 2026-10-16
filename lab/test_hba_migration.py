"""Exclusive generation migration record, refusals, order of effects and evidence."""
import json
import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch
import uuid
import database_volume
import hba_authority as authority
import hba_generation
import hba_journal
import hba_migration as migration
import hba_runtime
import hba_startup
import hba_target


TARGET=hba_target.Target('a'*64,'fixture-db','fixture','sha256:'+'c'*64)
DESIRED='local all supabase_admin trust\nhost all all 0.0.0.0/0 reject\n'
RETIRED_RULES='local all supabase_admin trust\nhost management management_auth 0.0.0.0/0 scram-sha-256\nhost all all 0.0.0.0/0 reject\n'
NEW_CONTAINER='b'*64
PGDATA=migration.PGDATA


def revision():
    return '# sbarbase-hba-revision: '+str(uuid.uuid4())+'\n'


def retired_info(volume='fixture-pgdata',running=False,name=None,owner=None,image=None,identifier=TARGET.container_id):
    return {'Id':identifier,'Name':'/'+(name or TARGET.name),'Image':image or TARGET.image,
            'Config':{'Labels':{'io.sbarbase.owner':owner or TARGET.owner}},
            'State':{'Running':running},
            'Mounts':[{'Type':'volume','Name':volume,'Source':'/var/lib/docker/volumes/'+volume,
                       'Destination':PGDATA,'Mode':'z'}]}


class FakeDocker:
    """The narrow Docker surface the migration uses, with per test knobs."""

    def __init__(self,state,*,retired=None,registry=None,marker='yes',published=None,
                 listing=None,cp_failure=(),stop_returns=0,rm_returns=0,role='t',new_identifier=NEW_CONTAINER,new_image=None):
        self.state=Path(state)
        self.retired=retired if retired is not None else retired_info()
        self.retired_rules=None
        self.registry=registry
        self.marker=marker
        self.published=published
        self.listing=listing
        self.cp_failure=set(cp_failure)
        self.stop_returns=stop_returns
        self.rm_returns=rm_returns
        self.role=role
        self.new_identifier=new_identifier
        self.new_image=new_image or TARGET.image
        self.calls=[]

    def __call__(self,*args,data=None,check=True):
        self.calls.append(args)
        return self.handle(args,data)

    def result(self,stdout='',returncode=0):
        return SimpleNamespace(stdout=stdout,stderr='',returncode=returncode)

    def handle(self,args,data):
        command=args[0]
        if command=='cp':
            source,destination=args[1],args[2]
            name=source.split(':',1)[1]
            if name in self.cp_failure:return self.result(returncode=1)
            if name==authority.PATH and self.registry is not None:
                Path(destination).write_text(self.registry)
                return self.result()
            if name==migration.HBA_PATH:
                Path(destination).write_text(RETIRED_RULES if self.retired_rules is None else self.retired_rules)
                return self.result()
            return self.result(returncode=1)
        if command=='ps':
            if self.listing is None:return self.result(stdout='')
            if isinstance(self.listing,str):return self.result(stdout=self.listing)
            return self.listing
        if command=='stop':return self.result(returncode=self.stop_returns)
        if command=='rm':return self.result(returncode=self.rm_returns)
        if command=='inspect':
            if args[1]=='--format':return self.result(stdout=self.new_identifier)
            identifier=args[1]
            if identifier==self.new_identifier:
                return self.result(stdout=json.dumps([{**retired_info(),'Id':identifier,'Image':self.new_image,'State':{'Running':True},
                                                       'Config':{'Labels':{'io.sbarbase.owner':TARGET.owner,
                                                                           'io.sbarbase.tier':'system'}}}]))
            if identifier==TARGET.container_id or identifier==TARGET.name:
                if self.retired is None:return self.result(returncode=1)
                return self.result(stdout=json.dumps([self.retired]))
            return self.result(returncode=1)
        if command=='run':
            return self.result(stdout=self.new_identifier)
        if command=='exec':
            return self.handle_exec(args)
        raise AssertionError('Unexpected docker call '+repr(args))

    def handle_exec(self,args):
        if 'pg_isready' in args:return self.result()
        if 'sh' in args and '-c' in args:
            script=args[args.index('-c')+1]
            if 'cat ' in script:return self.result(stdout=self.registry or '')
            if authority.MARKER in script:return self.result(stdout=self.marker)
            return self.result(stdout='')
        if 'psql' in args:return self.result(stdout=self.role)
        if 'cat' in args:
            if self.published is None:return self.result(returncode=1)
            return self.result(stdout=self.published)
        raise AssertionError('Unexpected docker exec '+repr(args))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        self.generation=str(uuid.uuid4())
        hba_generation.publish(self.state,TARGET,self.generation)

    def intent(self,**changes):
        values={'target':TARGET,'generation':self.generation,'volume':'fixture-pgdata',
                'inventory':authority.digest(DESIRED),'retired_state':'stopped-will-not-return'}
        values.update(changes)
        return migration.publish_intent(self.state,**values)

    def test_intent_is_exclusive_private_and_complete(self):
        record=self.intent()
        self.assertEqual(migration.load(self.state),record)
        self.assertEqual(set(record),{'version','migration','generation','old','volume','inventory','retired_state'})
        self.assertEqual(record['old'],asdict(TARGET))
        self.assertEqual(record['generation'],self.generation)
        for path in (migration.intent_path(self.state),migration.record_directory(self.state)):
            self.assertEqual(Path(path).stat().st_mode&0o777,0o700 if Path(path).is_dir() else 0o600)
        with self.assertRaisesRegex(RuntimeError,'already exists'):
            self.intent()

    def test_intent_requires_an_explicit_retired_state_and_record_shape(self):
        with self.assertRaisesRegex(ValueError,'explicit'):
            self.intent(retired_state='maybe')
        with self.assertRaisesRegex(ValueError,'shape'):
            migration.validate({'version':1,'migration':str(uuid.uuid4()),'generation':self.generation,
                                'old':asdict(TARGET),'volume':'v','inventory':authority.digest(DESIRED)})
        with self.assertRaisesRegex(ValueError,'volume'):
            self.intent(volume='')
        with self.assertRaisesRegex(ValueError,'identity'):
            self.intent(generation='not-a-uuid')

    def test_a_record_blocks_ordinary_startup_and_repeated_migration(self):
        self.intent()
        with self.assertRaisesRegex(RuntimeError,'Generation migration requires reconciliation'):
            hba_startup.require_clear(self.state)
        with self.assertRaisesRegex(RuntimeError,'Generation migration requires reconciliation'):
            with hba_startup.acquire(self.state):pass
        with self.assertRaisesRegex(RuntimeError,'already exists'):
            self.intent()
        with self.assertRaisesRegex(RuntimeError,'already exists'):
            migration.require_absent(self.state)

    def test_only_the_migration_lease_may_hold_the_record(self):
        self.intent()
        with hba_startup.acquire(self.state,migration=True) as lease:
            self.assertTrue(lease.migration)
            lease.verify()
        for name in (migration.intent_path(self.state),):
            name.unlink()
        os.rmdir(migration.record_directory(self.state))
        with self.assertRaisesRegex(RuntimeError,'requires a migration record'):
            with hba_startup.acquire(self.state,migration=True):pass

    def test_a_torn_record_blocks_startup_and_reconciliation(self):
        self.intent()
        path=migration.intent_path(self.state)
        path.write_text('{"record":')
        with self.assertRaises(ValueError):
            migration.load(self.state)
        with self.assertRaisesRegex(RuntimeError,'Generation migration requires reconciliation'):
            with hba_startup.acquire(self.state):pass
        with self.assertRaises(ValueError):
            migration.execute(Mock(),self.state,replacement={},desired=DESIRED)
        # The migration lease is obtainable, but the torn record is refused by load.
        with hba_startup.acquire(self.state,migration=True) as lease:
            lease.verify()
        with self.assertRaises(ValueError):
            migration.load(self.state)

    def test_a_missing_record_refuses_reconciliation(self):
        with self.assertRaisesRegex(RuntimeError,'No generation migration record'):
            migration.load(self.state)
        with self.assertRaisesRegex(RuntimeError,'No generation migration record'):
            migration.execute(Mock(),self.state,replacement={},desired=DESIRED)

    def test_checkpoints_are_immutable_and_bound_to_the_intent(self):
        record=self.intent()
        migration.checkpoint(self.state,'old-captured',{'migration':record['migration'],
                                                        'intent':authority.digest(authority.canonical(record)),
                                                        'retired':{'identity':'absent-verified'}})
        stored=migration.read_checkpoint(self.state,'old-captured')
        self.assertEqual(stored['phase'],'old-captured')
        with self.assertRaisesRegex(RuntimeError,'Conflicting'):
            migration.checkpoint(self.state,'old-captured',{'migration':record['migration'],
                                                            'intent':authority.digest(authority.canonical(record)),
                                                            'retired':{'identity':'changed'}})
        with self.assertRaisesRegex(RuntimeError,'another intent'):
            other=self.intent
            migration._bindings(self.state,{**record,'migration':str(uuid.uuid4())})

    def test_record_presence_blocks_before_start_and_worker_preflight(self):
        self.intent()
        writer=hba_runtime.SourceHBA(Mock(),self.state,TARGET.name,TARGET.owner,TARGET.image,startup=Mock())
        with self.assertRaisesRegex(RuntimeError,'Generation migration requires reconciliation'):
            writer.before_start(None,False)
        worker=hba_runtime.SourceHBA(Mock(),self.state,TARGET.name,TARGET.owner,TARGET.image,operation_fd=7)
        with self.assertRaisesRegex(RuntimeError,'Generation migration requires reconciliation'):
            worker.worker_preflight('e_'+'0'*24)
        self.assertTrue(hba_runtime.hba_migration_record(self.state))


class Fixture:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        self.generation=str(uuid.uuid4())
        hba_generation.publish(self.state,TARGET,self.generation)
        self.registry=authority.encode({'version':1,'generation':self.generation,
                                        'revision':str(uuid.uuid4()),'operations':{}})
        self.docker=FakeDocker(self.state,registry=self.registry)
        # The retired rules and the published rules are set per test.
        self.docker.retired_rules=None
        self.docker.published=revision()+DESIRED
        self.intent=migration.publish_intent(self.state,target=TARGET,generation=self.generation,
                                             volume='fixture-pgdata',inventory=authority.digest(DESIRED),
                                             retired_state='stopped-will-not-return')

    def outcome(self):
        return {'version':1,'kind':'retired-applied-reload-acknowledged','journal':{'token':str(uuid.uuid4())},
                'application':'publication-witnessed','activation':'unknown'}

    def execute(self,**kwargs):
        """Run the migration with the owned publication faked, never inferred.

        The pipeline's own behavior is covered by the HBA apply and settlement
        suites; these tests cover the migration's record, order and evidence.
        """
        outcome=self.outcome()
        with patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']), \
             patch.object(hba_generation,'read_existing'), \
             patch('hba_runtime.SourceHBA') as writer, patch('hba_migration.outcome_for',return_value=outcome):
            writer.return_value.publish.return_value=outcome
            return migration.execute(self.docker,self.state,replacement=self.replacement(),desired=DESIRED,**kwargs)

    def replacement(self):
        return {'name':TARGET.name,'owner':TARGET.owner,'image':TARGET.image,'tier':'system.db',
                'memory':'1024m','cpus':'1','network':'fixture-net','env_file':'/dev/null',
                'command':('postgres',)}


class PreconditionTests(Fixture,unittest.TestCase):
    def test_a_pending_journal_or_worker_receipt_refuses_before_capture(self):
        for name in ('hba-operation.json','worker-effect.json'):
            path=self.state/name
            path.write_text('{}')
            try:
                with self.assertRaisesRegex(RuntimeError,'reconciliation'):
                    self.execute()
            finally:path.unlink()
        self.assertFalse(migration.done(self.state,'old-captured'))
        self.assertFalse(any(args[0] in ('stop','rm','run') for args in self.docker.calls))

    def test_an_active_retired_operation_refuses_and_stays_pending(self):
        active=authority.encode({'version':1,'generation':self.generation,'revision':str(uuid.uuid4()),
                                 'operations':{str(uuid.uuid4()):{'binding':'d'*64,'state':'active'}}})
        self.docker.registry=active
        with self.assertRaisesRegex(RuntimeError,'active'):
            self.execute()
        self.assertFalse(migration.done(self.state,'old-captured'))
        self.assertFalse(migration.done(self.state,'new-captured'))

    def test_an_unreadable_registry_is_never_read_as_no_authority(self):
        self.docker.cp_failure={authority.PATH}
        with self.assertRaisesRegex(RuntimeError,'registry unavailable'):
            self.execute()

    def test_an_inspection_error_is_not_absence(self):
        absent_state=Path(self.temp.name)/'absent'
        absent_state.mkdir()
        intent={**self.intent,'retired_state':'absent-verified'}
        self.docker.listing='a'*64+' '+TARGET.name
        with self.assertRaisesRegex(RuntimeError,'absence is not proven'):
            migration.capture_retired(self.docker,absent_state,intent)
        self.docker.listing=SimpleNamespace(stdout='',returncode=1)
        with self.assertRaisesRegex(RuntimeError,'absence inspection unavailable'):
            migration.capture_retired(self.docker,absent_state,intent)
        self.docker.listing=None
        captured=migration.capture_retired(self.docker,absent_state,intent)
        self.assertEqual((captured['identity'],captured['authority'],captured['hba_digest']),
                         ('absent-verified','not-observable-with-retired-container',None))

    def test_a_different_pgdata_volume_is_a_different_database(self):
        self.docker.retired=retired_info(volume='other-pgdata')
        with self.assertRaisesRegex(RuntimeError,'volume differs'):
            self.execute()
        self.assertFalse(any(args[0]=='rm' for args in self.docker.calls))

    def test_a_running_retired_container_is_stopped_once_or_the_migration_refuses(self):
        self.docker.retired=retired_info(running=True)
        self.docker.stop_returns=1
        with self.assertRaisesRegex(RuntimeError,'could not be stopped'):
            self.execute()
        self.assertEqual([args for args in self.docker.calls if args[0]=='stop'],[('stop',TARGET.container_id,)])

    def test_an_absent_retired_container_refuses_before_publishing_intent(self):
        state=Path(self.temp.name)/'absent'
        state.mkdir()
        hba_generation.publish(state,TARGET,self.generation)
        pinned=hba_generation.load(state)
        self.docker.retired=None
        self.docker.cp_failure={migration.HBA_PATH,authority.PATH}
        with patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']):
            with self.assertRaisesRegex(RuntimeError,'Absent retired container.*verified archive'):
                migration.migrate(self.docker,state,target=TARGET,replacement=self.replacement(),
                                  desired=DESIRED,volume='fixture-pgdata',retired_state='absent-verified')
        self.assertFalse(migration.present(state))
        self.assertEqual(hba_generation.load(state),pinned)
        self.assertEqual(self.docker.calls,[])

    def test_an_existing_absent_intent_refuses_reconciliation_before_effects(self):
        legacy={**self.intent,'retired_state':'absent-verified'}
        migration.intent_path(self.state).write_text(authority.encode(legacy))
        with self.assertRaisesRegex(RuntimeError,'Absent retired container.*verified archive'):
            self.execute()
        self.assertEqual(migration.load(self.state),legacy)
        self.assertTrue(migration.present(self.state))
        self.assertFalse(migration.done(self.state,'old-captured'))
        self.assertEqual(self.docker.calls,[])

    def test_the_retired_authority_registry_is_preserved_for_audit(self):
        self.docker.retired_rules=None
        self.execute()
        evidence=migration.read_evidence(self.state,self.intent['migration'])
        self.assertEqual(evidence['rules']['retired_hba_digest'],authority.digest(RETIRED_RULES))
        self.assertEqual(evidence['retired_generation'],self.generation)
        archive=migration.archive_directory(self.state,self.intent['migration'])
        self.assertTrue((archive/'retired-registry.json').is_file() and (archive/'retired-pg_hba.conf').is_file())


class OrderTests(Fixture,unittest.TestCase):
    def published(self):
        return revision()+DESIRED

    def test_the_pin_is_replaced_by_archive_then_publish(self):
        record=migration.load(self.state)
        minted=str(uuid.uuid4())
        migration.checkpoint(self.state,'generation-minted',{'migration':record['migration'],
                                                             'intent':authority.digest(authority.canonical(record)),
                                                             'container_id':NEW_CONTAINER,'generation':minted})
        self.docker.marker='yes'
        with patch('hba_authority.initialize') as initialize:
            new_target=hba_target.Target(NEW_CONTAINER,TARGET.name,TARGET.owner,TARGET.image)
            with patch.object(hba_generation,'read_existing',side_effect=lambda docker,state,target:authority.Snapshot(target.container_id,minted,authority.encode({'version':1,'generation':minted,'revision':str(uuid.uuid4()),'operations':{}}))):
                snapshot=migration.initialize_generation(self.docker,self.state,record,{'container_id':NEW_CONTAINER,'generation':minted},new_target)
            initialize.assert_not_called()
        pin=hba_generation.load(self.state)
        self.assertEqual(pin['target'],asdict(new_target))
        self.assertEqual(pin['generation'],minted)
        archived=Path(migration.archive_directory(self.state,self.intent['migration']))/hba_generation.NAME
        self.assertEqual(json.loads(archived.read_text())['record']['generation'],self.generation)

    def test_the_generation_is_minted_once_and_never_again_on_retry(self):
        record=migration.load(self.state)
        minted=str(uuid.uuid4())
        migration.checkpoint(self.state,'generation-minted',{'migration':record['migration'],
                                                             'intent':authority.digest(authority.canonical(record)),
                                                             'container_id':NEW_CONTAINER,'generation':minted})
        self.docker.marker='no'
        outcome=self.outcome()
        with patch('hba_authority.initialize') as initialize, patch.object(hba_generation,'read_existing'), \
             patch('hba_runtime.SourceHBA') as writer, patch('hba_migration.outcome_for',return_value=outcome), \
             patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']):
            writer.return_value.publish.return_value=outcome
            migration.execute(self.docker,self.state,replacement=self.replacement(),desired=DESIRED)
        self.assertEqual(initialize.call_args.args[2],minted)

    def test_the_pin_and_the_journal_precede_the_publication(self):
        order=[]
        original_initialize=patch('hba_authority.initialize',side_effect=lambda *a,**k:order.append('registry-init'))
        record=migration.load(self.state)
        minted=str(uuid.uuid4())
        migration.checkpoint(self.state,'generation-minted',{'migration':record['migration'],
                                                             'intent':authority.digest(authority.canonical(record)),
                                                             'container_id':NEW_CONTAINER,'generation':minted})
        self.docker.marker='no'
        outcome=self.outcome()
        with original_initialize, patch.object(hba_generation,'read_existing'), \
             patch('hba_runtime.SourceHBA') as writer, patch('hba_migration.outcome_for',return_value=outcome), \
             patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']):
            writer.return_value.publish.side_effect=lambda content:order.append('publish') or outcome
            migration.execute(self.docker,self.state,replacement=self.replacement(),desired=DESIRED)
            pinned_at_publication=writer.call_args.kwargs['startup'].state
        self.assertEqual(order,['registry-init','publish'])
        self.assertEqual(pinned_at_publication,self.state)
        self.assertEqual(writer.return_value.target.container_id,NEW_CONTAINER)
        self.assertNotEqual(hba_generation.load(self.state)['generation'],self.generation)

    def test_the_database_keeps_refusing_while_the_record_is_present(self):
        record=migration.load(self.state)
        minted=str(uuid.uuid4())
        migration.checkpoint(self.state,'generation-minted',{'migration':record['migration'],
                                                             'intent':authority.digest(authority.canonical(record)),
                                                             'container_id':NEW_CONTAINER,'generation':minted})
        self.docker.marker='yes'
        with patch.object(hba_generation,'read_existing'), patch('hba_runtime.SourceHBA') as writer, \
             patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']):
            writer.return_value.publish.side_effect=RuntimeError('HBA reload signal not acknowledged')
            with self.assertRaisesRegex(RuntimeError,'reload signal not acknowledged'):
                migration.execute(self.docker,self.state,replacement=self.replacement(),desired=DESIRED)
        self.assertTrue(migration.present(self.state))
        self.assertFalse(migration.done(self.state,'rules-published'))
        self.assertFalse((migration.archive_directory(self.state,self.intent['migration'])/migration.EVIDENCE).exists())
        with self.assertRaisesRegex(RuntimeError,'reconciliation'):
            with hba_startup.acquire(self.state):pass
        hba_generation.load(self.state)


class EvidenceTests(Fixture,unittest.TestCase):
    def test_the_rule_comparison_is_a_real_comparison(self):
        self.docker.published=revision()+DESIRED
        self.docker.retired_rules=DESIRED
        self.execute()
        identical=migration.read_evidence(self.state,self.intent['migration'])['rules']
        self.assertEqual(identical['difference'],'identical')
        self.assertTrue(identical['inventory_matches_observed'] and identical['retired_matches_observed'])

    def test_a_rule_inventory_mismatch_preserves_the_migration_barrier(self):
        self.docker.published=revision()+'local all all trust\n'
        self.docker.retired_rules=None
        with self.assertRaisesRegex(RuntimeError,'Observed HBA rules differ from the migration inventory'):
            self.execute()
        self.assertTrue(migration.present(self.state))
        self.assertTrue(migration.done(self.state,'rules-published'))
        self.assertFalse(migration.done(self.state,'archived'))
        archive=migration.archive_directory(self.state,self.intent['migration'])
        self.assertFalse((archive/migration.EVIDENCE).exists())
        self.assertFalse((migration.record_directory(self.state)/migration.COMPLETED).exists())
        with self.assertRaisesRegex(RuntimeError,'Generation migration requires reconciliation'):
            hba_startup.require_clear(self.state)

    def test_archived_retry_rechecks_current_rules_before_removing_the_barrier(self):
        with patch.object(migration,'finish',side_effect=RuntimeError('completion interrupted')):
            with self.assertRaisesRegex(RuntimeError,'completion interrupted'):
                self.execute()
        archive=migration.archive_directory(self.state,self.intent['migration'])/migration.EVIDENCE
        preserved=archive.read_bytes()
        self.docker.published=revision()+'local all all trust\n'
        with self.assertRaisesRegex(RuntimeError,'Observed HBA rules differ from the migration inventory'):
            self.execute()
        self.assertTrue(migration.present(self.state))
        self.assertTrue(migration.done(self.state,'archived'))
        self.assertEqual(archive.read_bytes(),preserved)
        self.assertFalse((migration.record_directory(self.state)/migration.COMPLETED).exists())

    def test_legacy_archived_mismatch_never_authorizes_completion(self):
        with patch.object(migration,'finish',side_effect=RuntimeError('completion interrupted')):
            with self.assertRaisesRegex(RuntimeError,'completion interrupted'):
                self.execute()
        archive=migration.archive_directory(self.state,self.intent['migration'])/migration.EVIDENCE
        legacy=migration.read_evidence(self.state,self.intent['migration'])
        legacy['rules']['inventory_matches_observed']=False
        legacy['rules']['difference']='different'
        archive.write_text(authority.encode(legacy))
        preserved=archive.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'Archived HBA migration did not verify the inventory'):
            self.execute()
        self.assertTrue(migration.present(self.state))
        self.assertEqual(archive.read_bytes(),preserved)
        self.assertFalse((migration.record_directory(self.state)/migration.COMPLETED).exists())

    def test_completion_removes_the_record_and_reconciliation_refuses_twice(self):
        self.docker.published=revision()+DESIRED
        self.docker.retired_rules=None
        self.assertTrue(self.execute())
        self.assertFalse(migration.present(self.state))
        self.assertTrue(hba_generation.load(self.state)['generation']!=self.generation)
        with self.assertRaisesRegex(RuntimeError,'No generation migration record'):
            migration.execute(self.docker,self.state,replacement=self.replacement(),desired=DESIRED)



NEW_IMAGE='sha256:'+'d'*64


class ImageChangeTests(unittest.TestCase):
    """A generation change onto the image of a newer distribution lock: same name, owner and
    volume, the image the intent recorded, through every interruption."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        self.generation=str(uuid.uuid4())
        hba_generation.publish(self.state,TARGET,self.generation)
        self.registry=authority.encode({'version':1,'generation':self.generation,
                                        'revision':str(uuid.uuid4()),'operations':{}})
        self.docker=FakeDocker(self.state,registry=self.registry,new_image=NEW_IMAGE)
        self.docker.published=revision()+DESIRED

    def publish(self,image=NEW_IMAGE):
        return migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                        inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',
                                        image=image)

    def replacement(self,image=NEW_IMAGE):
        return {'name':TARGET.name,'owner':TARGET.owner,'image':image,'tier':'system.db',
                'memory':'1024m','cpus':'1','network':'fixture-net','env_file':'/dev/null','command':('postgres',)}

    def execute(self,image=NEW_IMAGE):
        # One publication per test, read back the same on every rerun, as hba_settlement does.
        outcome={'version':1,'kind':'retired-applied-reload-acknowledged','journal':{'token':self.__dict__.setdefault('token',str(uuid.uuid4()))},
                 'application':'publication-witnessed','activation':'unknown'}
        with patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']), \
             patch.object(hba_generation,'read_existing'), patch('hba_authority.initialize'), \
             patch('hba_runtime.SourceHBA') as writer, patch('hba_migration.outcome_for',return_value=outcome):
            writer.return_value.publish.return_value=outcome
            return migration.execute(self.docker,self.state,replacement=self.replacement(image),desired=DESIRED)

    def runs(self):
        return [call for call in self.docker.calls if call[0]=='run']

    def test_the_intent_records_a_changed_image_and_only_then(self):
        record=self.publish()
        self.assertEqual((record['version'],record['image']),(2,NEW_IMAGE))
        self.assertEqual(migration.target_image(migration.load(self.state)),NEW_IMAGE)
        import shutil
        shutil.rmtree(migration.record_directory(self.state))
        same=self.publish(image=TARGET.image)
        self.assertEqual(same['version'],1)
        self.assertNotIn('image',same)
        self.assertEqual(migration.target_image(same),TARGET.image)

    def test_an_intent_that_records_its_own_image_or_a_malformed_one_is_refused(self):
        record=self.publish()
        for image in (TARGET.image,'postgres:17','sha256:short'):
            with self.subTest(image=image), self.assertRaises(ValueError):
                migration.validate({**record,'image':image})
        with self.assertRaises(ValueError):
            migration.validate({**record,'version':1})
        legacy={key:value for key,value in record.items() if key!='image'}
        with self.assertRaises(ValueError):
            migration.validate({**legacy,'version':2})

    def test_the_replacement_must_run_exactly_the_recorded_image(self):
        intent=self.publish()
        for image in (TARGET.image,'sha256:'+'e'*64):
            with self.subTest(image=image), self.assertRaisesRegex(RuntimeError,'image differs from the one its intent recorded'):
                migration.validate_replacement(intent,self.replacement(image))
        self.assertEqual(migration.validate_replacement(intent,self.replacement())['image'],NEW_IMAGE)
        for key,value in (('name','other-db'),('owner','other')):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError,'changes the database identity'):
                migration.validate_replacement(intent,{**self.replacement(),key:value})

    def test_a_completed_change_pins_the_new_image_and_archives_the_old(self):
        intent=self.publish()
        self.assertTrue(self.execute())
        run=self.runs()[0]
        self.assertEqual(run[run.index('fixture-pgdata:'+PGDATA)+1],NEW_IMAGE)
        pin=hba_generation.load(self.state)
        self.assertEqual((pin['target']['image'],pin['target']['name'],pin['target']['owner']),(NEW_IMAGE,TARGET.name,TARGET.owner))
        archive=migration.archive_directory(self.state,intent['migration'])
        retired=json.loads((archive/hba_generation.NAME).read_text())['record']
        self.assertEqual(retired['target']['image'],TARGET.image)
        evidence=migration.read_evidence(self.state,intent['migration'])
        self.assertEqual((evidence['target']['image'],evidence['retired_image']),(NEW_IMAGE,TARGET.image))

    def test_every_interruption_resumes_onto_the_recorded_image(self):
        phases=list(migration.PHASES)
        for stop in phases:
            with self.subTest(after=stop):
                self.setUp()
                self.publish()
                original=migration.checkpoint

                def interrupted(state,phase,payload,stop=stop):
                    result=original(state,phase,payload)
                    if phase==stop:
                        raise RuntimeError('interrupted after '+phase)
                    return result
                with patch.object(migration,'checkpoint',interrupted), self.assertRaisesRegex(RuntimeError,'interrupted after'):
                    self.execute()
                self.assertTrue(migration.present(self.state))
                # The resume reads the image from the intent; asking for another refuses before any effect.
                calls=len(self.docker.calls)
                with self.assertRaisesRegex(RuntimeError,'image differs'):
                    self.execute(image=TARGET.image)
                self.assertEqual(len(self.docker.calls),calls)
                self.assertTrue(self.execute())
                self.assertFalse(migration.present(self.state))
                self.assertTrue(all(run[run.index('fixture-pgdata:'+PGDATA)+1]==NEW_IMAGE for run in self.runs()))
                self.assertEqual(len(self.runs()),1,'the replacement is created once')
                self.assertEqual(hba_generation.load(self.state)['target']['image'],NEW_IMAGE)


class LifelikeDocker(FakeDocker):
    """rm really removes the retired container, the replacement then answers to the shared name,
    a second run on a taken name fails, and the replacement can be stopped (a host restart)."""

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.created=False
        self.replacement_running=True
        self.ps_returns=0
        self.run_fails=False

    def replacement_info(self):
        return {**retired_info(volume=getattr(self,'replacement_volume','fixture-pgdata')),'Id':self.new_identifier,'Image':self.new_image,
                'State':{'Running':self.replacement_running},
                'Config':{'Labels':{'io.sbarbase.owner':TARGET.owner,'io.sbarbase.tier':'system'}}}

    def handle(self,args,data):
        command=args[0]
        if command=='run' and '--rm' in args:
            self.probes=getattr(self,'probes',[])+[args]
            return self.result(returncode=0 if getattr(self,'holds_cluster',True) else 1)
        if command=='rm':
            self.retired=None
            return self.result()
        if command=='ps':
            return self.result(returncode=self.ps_returns)
        if command=='run':
            # The replacement must be created on the volume the test expects, nothing else.
            volume=args[args.index('-v')+1].split(':')[0]
            if volume!=getattr(self,'replacement_volume','fixture-pgdata'):raise AssertionError('run on '+volume)
            if self.created or self.retired is not None or self.run_fails:return self.result(returncode=125)
            self.created=True
            return self.result(stdout=self.new_identifier)
        if command=='start':
            self.replacement_running=True
            return self.result()
        if command=='volume' and args[1]=='inspect':
            if args[2] not in getattr(self,'volumes',{'fixture-pgdata'}):return self.result(returncode=1)
            owner=getattr(self,'volume_owner',TARGET.owner)
            return self.result(stdout=json.dumps([{'Name':args[2],'Labels':{'io.sbarbase.owner':owner}}]))
        if command=='exec' and args[1]==self.new_identifier and not self.replacement_running:
            return self.result(returncode=1)
        if command=='inspect' and args[1] in (self.new_identifier,TARGET.name) and getattr(self,'gone',False):
            return self.result(returncode=1)
        if command=='inspect' and args[1] in (self.new_identifier,TARGET.name) and self.created:
            return self.result(stdout=json.dumps([self.replacement_info()]))
        return super().handle(args,data)


class ResumeAfterRemovalTests(ImageChangeTests.__base__):
    """An interruption after the retired container is removed but before the new-captured
    checkpoint is written: the rerun adopts the replacement it created, never a second one."""

    setUp=ImageChangeTests.setUp
    publish=ImageChangeTests.publish
    replacement=ImageChangeTests.replacement
    execute=ImageChangeTests.execute
    runs=ImageChangeTests.runs

    def lifelike(self):
        self.docker=LifelikeDocker(self.state,registry=self.registry,new_image=NEW_IMAGE)
        self.docker.published=revision()+DESIRED
        self.publish()

    def interrupt_before_readiness(self):
        with patch.object(migration,'wait_ready',side_effect=RuntimeError('Replacement database readiness timed out')):
            with self.assertRaisesRegex(RuntimeError,'readiness timed out'):
                self.execute()
        self.assertTrue(migration.present(self.state))
        self.assertIsNone(self.docker.retired)
        self.assertFalse(migration.done(self.state,'new-captured'))

    def test_a_rerun_adopts_the_replacement_and_finishes(self):
        self.lifelike()
        self.interrupt_before_readiness()
        self.execute()
        self.assertEqual(len(self.runs()),1)
        self.assertFalse(migration.present(self.state))
        self.assertEqual(migration.pinned(self.state)['target']['image'],NEW_IMAGE)

    def test_a_stopped_replacement_is_started_again(self):
        self.lifelike()
        self.interrupt_before_readiness()
        self.docker.replacement_running=False
        self.execute()
        self.assertIn(('start',NEW_CONTAINER),self.docker.calls)
        self.assertEqual(len(self.runs()),1)
        self.assertFalse(migration.present(self.state))

    def test_a_replacement_stopped_after_its_checkpoint_is_started_before_later_steps(self):
        self.lifelike()
        with patch.object(migration,'initialize_generation',side_effect=RuntimeError('stop here')):
            with self.assertRaisesRegex(RuntimeError,'stop here'):
                self.execute()
        self.assertTrue(migration.done(self.state,'new-captured'))
        self.docker.replacement_running=False
        awaited=[]
        real=migration.wait_ready
        with patch.object(migration,'wait_ready',lambda docker,identifier:awaited.append(identifier) or real(docker,identifier)):
            self.execute()
        self.assertIn(('start',NEW_CONTAINER),self.docker.calls)
        self.assertEqual(awaited,[NEW_CONTAINER],'a restarted replacement is awaited before later steps')
        self.assertFalse(migration.present(self.state))

    def test_a_failed_listing_is_not_read_as_a_removed_container(self):
        self.lifelike()
        self.interrupt_before_readiness()
        self.docker.ps_returns=1
        with self.assertRaisesRegex(RuntimeError,'Retired container inspection unavailable'):
            self.execute()
        self.assertTrue(migration.present(self.state))

    def test_a_container_under_the_name_with_another_image_or_owner_is_refused(self):
        for change in ({'Image':'sha256:'+'e'*64},{'Config':{'Labels':{'io.sbarbase.owner':'someone','io.sbarbase.tier':'system'}}},
                       {'Config':{'Labels':{'io.sbarbase.owner':TARGET.owner,'io.sbarbase.tier':'other'}}},
                       {'Id':TARGET.container_id}):
            with self.subTest(change=change):
                self.setUp();self.lifelike();self.interrupt_before_readiness()
                original=self.docker.replacement_info
                self.docker.replacement_info=lambda original=original,change=change:{**original(),**change}
                with self.assertRaisesRegex(RuntimeError,'Replacement container identity changed'):
                    self.execute()
                self.assertEqual(len(self.runs()),1)
                self.assertTrue(migration.present(self.state))

    def test_a_container_under_the_name_on_another_volume_is_refused(self):
        self.lifelike();self.interrupt_before_readiness()
        original=self.docker.replacement_info
        self.docker.replacement_info=lambda:{**original(),'Mounts':retired_info(volume='other-pgdata')['Mounts']}
        with self.assertRaisesRegex(RuntimeError,'volume differs'):
            self.execute()
        self.assertEqual(len(self.runs()),1)
        self.assertTrue(migration.present(self.state))

    def test_a_host_restart_after_the_archived_checkpoint_is_resumed(self):
        self.lifelike()
        with patch.object(migration,'finish',side_effect=RuntimeError('killed before finishing')):
            with self.assertRaisesRegex(RuntimeError,'killed before finishing'):
                self.execute()
        self.assertTrue(migration.done(self.state,'archived'))
        self.docker.replacement_running=False
        self.execute()
        self.assertIn(('start',NEW_CONTAINER),self.docker.calls)
        self.assertFalse(migration.present(self.state))

    def test_a_rerun_whose_recorded_replacement_is_gone_refuses_and_keeps_the_record(self):
        self.lifelike()
        with patch.object(migration,'initialize_generation',side_effect=RuntimeError('stop here')):
            with self.assertRaisesRegex(RuntimeError,'stop here'):
                self.execute()
        self.docker.gone=True  # the replacement vanished after its checkpoint
        with self.assertRaisesRegex(RuntimeError,'Replacement container inspection unavailable'):
            self.execute()
        self.assertTrue(migration.present(self.state))
        self.assertFalse(migration.done(self.state,'generation-initialized'))

    def test_a_kill_between_a_file_and_its_checkpoint_is_resumed(self):
        for phase in ('retired-archived','archived'):
            with self.subTest(phase=phase):
                self.setUp();self.__dict__.pop('token',None);self.lifelike()
                original=migration.checkpoint
                def interrupted(state,name,payload,phase=phase):
                    if name==phase:raise RuntimeError('killed after the file')
                    return original(state,name,payload)
                with patch.object(migration,'checkpoint',interrupted):
                    with self.assertRaisesRegex(RuntimeError,'killed after the file'):
                        self.execute()
                self.execute()
                self.assertFalse(migration.present(self.state))
                self.assertEqual(migration.pinned(self.state)['target']['image'],NEW_IMAGE)

    def test_a_file_that_differs_from_the_rerun_is_refused(self):
        self.lifelike()
        original=migration.checkpoint
        def interrupted(state,name,payload):
            if name=='retired-archived':raise RuntimeError('killed after the file')
            return original(state,name,payload)
        with patch.object(migration,'checkpoint',interrupted), self.assertRaises(RuntimeError):
            self.execute()
        path=migration._retired_directory(self.state,migration.load(self.state))/'retired-generation.json'
        record=migration._decode_envelope(path.read_text(),'x')
        path.unlink()
        migration._publish_exclusive(path,{**record,'hba_digest':'0'*64},'x')
        with self.assertRaisesRegex(RuntimeError,'differs from the one an earlier run wrote'):
            self.execute()
        self.assertTrue(migration.present(self.state))

    def test_a_kill_while_the_finished_record_is_removed_leaves_no_record(self):
        self.lifelike()
        real=migration.shutil.rmtree
        def torn(path,*args,**kwargs):
            # Part of the renamed copy goes, then the kill.
            (Path(path)/migration.CHECKPOINTS/'archived.json').unlink()
            raise OSError('killed while removing')
        with patch.object(migration.shutil,'rmtree',torn), self.assertRaisesRegex(OSError,'killed while removing'):
            self.execute()
        self.assertFalse(migration.present(self.state),'the record went in one rename')
        self.assertEqual(migration.pinned(self.state)['target']['image'],NEW_IMAGE)
        self.assertTrue((self.state/migration.REMOVING).exists())
        # The next removal clears what was left.
        migration.os.mkdir(migration.record_directory(self.state),0o700)
        migration._remove_record(self.state)
        self.assertFalse((self.state/migration.REMOVING).exists())
        self.assertIs(migration.shutil.rmtree,real)

    def test_a_kill_after_the_completion_marker_finishes_on_the_rerun(self):
        self.lifelike()
        with patch.object(migration,'_remove_record',side_effect=OSError('killed before the rename')), \
                self.assertRaises(OSError):
            self.execute()
        self.assertTrue(migration.present(self.state))
        self.assertTrue(self.execute())
        self.assertFalse(migration.present(self.state))

    def test_a_completion_marker_without_its_evidence_is_refused(self):
        self.lifelike()
        with patch.object(migration,'_remove_record',side_effect=OSError('killed before the rename')), \
                self.assertRaises(OSError):
            self.execute()
        intent=migration.load(self.state)
        (migration.archive_directory(self.state,intent['migration'])/migration.EVIDENCE).unlink()
        with self.assertRaisesRegex(RuntimeError,'not backed by its evidence'):
            self.execute()
        self.assertTrue(migration.present(self.state))

    def test_a_kill_before_the_intent_is_linked_in_starts_again(self):
        for leftover in (False,True):
            with self.subTest(leftover=leftover):
                self.setUp();self.__dict__.pop('token',None);self.lifelike()
                import shutil
                shutil.rmtree(migration.record_directory(self.state))
                real=migration.os.link
                def killed(source,destination):
                    if Path(destination).name==migration.hba_generation.MIGRATION_INTENT:
                        if leftover:
                            # A real kill leaves the temporary copy the finally clause would remove.
                            (Path(destination).parent/'.intent.json.0.pending').write_text('{"record":')
                        raise OSError('killed before the link')
                    return real(source,destination)
                with patch.object(migration.os,'link',killed), self.assertRaises(OSError):
                    migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                             inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',image=NEW_IMAGE)
                self.assertTrue(migration.present(self.state))
                self.assertFalse(migration.intent_path(self.state).exists(),'never a torn intent')
                with self.assertRaisesRegex(RuntimeError,'Start the migration again'):
                    self.execute()
                self.assertFalse(migration.present(self.state))
                self.assertEqual(self.docker.calls,[],'nothing had started')

    def test_a_record_holding_anything_but_temporary_copies_is_kept(self):
        self.lifelike()
        import shutil
        shutil.rmtree(migration.record_directory(self.state))
        migration.os.mkdir(migration.record_directory(self.state),0o700)
        (migration.record_directory(self.state)/'intent.json').write_text('{"record":')
        self.assertFalse(migration.discard_unpublished(self.state))
        self.assertTrue(migration.present(self.state))

    def test_a_kill_before_a_record_is_linked_leaves_no_torn_file(self):
        self.lifelike()
        real=migration.os.link
        def killed(source,destination):
            if Path(destination).name=='retired-archived.json':raise OSError('killed before the link')
            return real(source,destination)
        with patch.object(migration.os,'link',killed), self.assertRaisesRegex(OSError,'killed before the link'):
            self.execute()
        self.assertFalse(migration.done(self.state,'retired-archived'))
        leftovers=[path.name for path in (migration.record_directory(self.state)/migration.CHECKPOINTS).iterdir()]
        self.assertFalse([name for name in leftovers if name.endswith('.pending')],'the temporary copy is removed')
        self.execute()
        self.assertFalse(migration.present(self.state))

    def test_the_new_pin_is_written_whole(self):
        self.lifelike()
        real=migration.os.link
        def killed(source,destination):
            if Path(destination).name==hba_generation.NAME:raise OSError('killed before the pin')
            return real(source,destination)
        with patch.object(migration.os,'link',killed), self.assertRaisesRegex(OSError,'killed before the pin'):
            self.execute()
        self.assertFalse((self.state/hba_generation.NAME).exists(),'no torn pin under its name')
        self.execute()
        self.assertEqual(migration.pinned(self.state)['target']['image'],NEW_IMAGE)

    def test_nothing_under_the_name_creates_the_replacement(self):
        self.lifelike()
        self.docker.run_fails=True
        with self.assertRaisesRegex(RuntimeError,'could not be created'):
            self.execute()
        self.assertIsNone(self.docker.retired)
        self.docker.run_fails=False
        self.execute()
        self.assertEqual(len(self.runs()),2)  # the failed one, then the replacement
        self.assertTrue(self.docker.created)
        self.assertFalse(migration.present(self.state))


class VolumeChangeTests(ImageChangeTests.__base__):
    """P3 part 1: a generation change onto another, existing pgdata volume (a rebuilt cluster)."""

    setUp=ImageChangeTests.setUp
    replacement=ImageChangeTests.replacement
    execute=ImageChangeTests.execute
    runs=ImageChangeTests.runs
    NEW_VOLUME='sbarbase-durable-pgdata-0123456789ab'

    def lifelike(self,image=NEW_IMAGE,exists=True):
        self.docker=LifelikeDocker(self.state,registry=self.registry,new_image=image)
        self.docker.published=revision()+DESIRED
        self.docker.replacement_volume=self.NEW_VOLUME
        self.docker.volumes={'fixture-pgdata'}|({self.NEW_VOLUME} if exists else set())
        return migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                        inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',
                                        image=image,new_volume=self.NEW_VOLUME)

    def test_the_intent_records_another_volume_only_when_it_is_a_database_volume_that_changes(self):
        record=self.lifelike()
        self.assertEqual((record['version'],record['new_volume'],record['image']),(3,self.NEW_VOLUME,NEW_IMAGE))
        self.assertEqual(migration.target_volume(record),self.NEW_VOLUME)
        for volume in ('fixture-pgdata','other','sbarbase-durable-storage'):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                migration.validate({**record,'new_volume':volume})
        same_image={key:value for key,value in record.items() if key!='image'}
        self.assertEqual(migration.validate(same_image),same_image)
        with self.assertRaises(ValueError):
            migration.validate({**same_image,'version':2})

    def test_the_replacement_runs_on_the_new_volume_and_the_runtime_is_pointed_at_it(self):
        self.lifelike()
        self.execute()
        self.assertFalse(migration.present(self.state))
        self.assertEqual(database_volume.current(self.state),self.NEW_VOLUME)
        self.assertEqual(migration.pinned(self.state)['target']['image'],NEW_IMAGE)
        archived=[path for path in (self.state/migration.ARCHIVE).iterdir()]
        evidence=migration.read_evidence(self.state,archived[0].name)
        self.assertEqual(evidence['target_volume'],self.NEW_VOLUME)
        self.assertEqual(evidence['volume'],'fixture-pgdata')

    def test_the_same_image_on_a_new_volume_works_too(self):
        self.lifelike(image=TARGET.image)
        self.execute(image=TARGET.image)
        self.assertEqual(database_volume.current(self.state),self.NEW_VOLUME)

    def untouched(self):
        self.assertEqual([call for call in self.docker.calls if call[0] in ('stop','rm') or (call[0]=='run' and '--rm' not in call)],[])
        self.assertIsNotNone(self.docker.retired,'the retired container is still there')
        self.assertFalse(migration.done(self.state,'old-captured'))
        self.assertEqual(database_volume.current(self.state),database_volume.DEFAULT)

    def test_a_missing_empty_or_foreign_new_volume_refuses_before_the_retired_container_is_touched(self):
        for setup,reason in ((lambda d:setattr(d,'volumes',{'fixture-pgdata'}),'does not exist'),
                             (lambda d:setattr(d,'holds_cluster',False),'no PostgreSQL data directory'),
                             (lambda d:setattr(d,'volume_owner','recovery-target'),'not owned by the runtime')):
            with self.subTest(reason=reason):
                self.setUp();self.__dict__.pop('token',None)
                self.lifelike();setup(self.docker)
                with self.assertRaisesRegex(RuntimeError,reason):
                    self.execute()
                self.untouched()
                self.assertTrue(migration.present(self.state))

    def test_the_probe_reads_the_volume_read_only_on_the_target_image_without_network(self):
        self.lifelike()
        self.execute()
        probe=self.docker.probes[0]
        self.assertIn(self.NEW_VOLUME+':/data:ro',probe)
        self.assertIn(NEW_IMAGE,probe)
        self.assertEqual(probe[probe.index('--network')+1],'none')
        self.assertEqual(probe[probe.index('--pull')+1],'never')
        self.assertEqual(probe[-1],migration.PROBE)

    def test_the_probe_runs_again_on_a_rerun_until_the_replacement_runs_on_the_volume(self):
        self.lifelike()
        original=migration.checkpoint
        def interrupted(state,name,payload):
            if name=='retired-archived':raise RuntimeError('killed')
            return original(state,name,payload)
        with patch.object(migration,'checkpoint',interrupted), self.assertRaisesRegex(RuntimeError,'killed'):
            self.execute()
        self.assertTrue(migration.done(self.state,'old-captured'))
        self.docker.holds_cluster=False
        with self.assertRaisesRegex(RuntimeError,'no PostgreSQL data directory'):
            self.execute()
        self.assertIsNotNone(self.docker.retired,'still not removed')
        self.docker.holds_cluster=True
        self.execute()
        probes=len(self.docker.probes)
        self.assertGreaterEqual(probes,3)

    def test_the_probe_compares_the_data_directory_s_major_version_with_the_image_s_server(self):
        import os,subprocess,tempfile
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);data=root/'data';data.mkdir();bin_=root/'bin';bin_.mkdir()
            server=bin_/'postgres';server.write_text('#!/bin/sh\necho "postgres (PostgreSQL) 17.6"\n');server.chmod(0o755)
            script=migration.PROBE.replace('/data',str(data))
            def run():
                return subprocess.run(['sh','-c',script],env={**os.environ,'PATH':str(bin_)+':/usr/bin:/bin'}).returncode
            self.assertNotEqual(run(),0,'no PG_VERSION')
            for content,expected in (('17\n',0),('17',0),('',1),('\n',1),('15\n',1),('1\n',1),('.*\n',1),('17.6\n',1),('1 7\n',1)):
                with self.subTest(content=content):
                    (data/'PG_VERSION').write_text(content)
                    self.assertEqual(run()==0,expected==0)

    def test_a_volume_gone_between_the_probe_and_the_creation_is_refused(self):
        self.lifelike()
        with patch.object(migration,'require_cluster_volume'):
            self.docker.volumes={'fixture-pgdata'}
            with self.assertRaisesRegex(RuntimeError,'does not exist'):
                self.execute()
        self.assertEqual([call for call in self.docker.calls if call[0]=='run' and '--rm' not in call],[])
        self.assertTrue(migration.present(self.state))

    def test_a_volume_an_earlier_move_left_behind_is_never_a_target(self):
        self.lifelike()
        self.execute()
        self.assertEqual(migration.retired_volumes(self.state),{'fixture-pgdata'})
        self.docker.volumes|={'sbarbase-durable-pgdata-ffffffffffff'}
        with self.assertRaisesRegex(RuntimeError,'left behind'):
            migration.require_cluster_volume(self.docker,'fixture-pgdata',TARGET.owner,NEW_IMAGE,self.state)
        self.assertTrue(migration.require_cluster_volume(self.docker,'sbarbase-durable-pgdata-ffffffffffff',
                                                         TARGET.owner,NEW_IMAGE,self.state))

    def test_a_version_3_intent_checks_its_image_and_needs_its_volume(self):
        record=self.lifelike()
        for image in (TARGET.image,'postgres:17','sha256:short'):
            with self.subTest(image=image), self.assertRaises(ValueError):
                migration.validate({**record,'image':image})
        without_volume={key:value for key,value in record.items() if key!='new_volume'}
        with self.assertRaises(ValueError):
            migration.validate(without_volume)
        with self.assertRaises(ValueError):
            migration.validate({key:value for key,value in without_volume.items() if key!='image'}|{'version':3})

    def test_the_constant_is_never_a_target_volume(self):
        record=self.lifelike()
        with self.assertRaises(ValueError):
            migration.validate({**record,'volume':'sbarbase-durable-pgdata-ffffffffffff','new_volume':'sbarbase-durable-pgdata'})

    def test_the_new_volume_must_differ_from_the_retired_one(self):
        record=self.lifelike()
        with self.assertRaises(ValueError):
            migration.validate({**record,'volume':self.NEW_VOLUME})

    def test_a_replacement_created_on_another_volume_is_refused(self):
        self.lifelike()
        real=self.docker.replacement_info
        self.docker.replacement_info=lambda:{**real(),'Mounts':retired_info(volume='fixture-pgdata')['Mounts']}
        with self.assertRaisesRegex(RuntimeError,'volume differs'):
            self.execute()
        self.assertTrue(migration.present(self.state))

    def test_a_kill_after_the_volume_is_named_and_before_the_archive_is_resumed(self):
        self.lifelike()
        with patch.object(migration,'archive_evidence',side_effect=RuntimeError('killed')):
            with self.assertRaisesRegex(RuntimeError,'killed'):
                self.execute()
        self.assertEqual(database_volume.current(self.state),self.NEW_VOLUME)
        self.execute()
        self.assertFalse(migration.present(self.state))

    def test_the_volume_is_not_named_until_the_change_reaches_its_archive(self):
        self.lifelike()
        with patch.object(migration,'publish_rules',side_effect=RuntimeError('stop here')):
            with self.assertRaisesRegex(RuntimeError,'stop here'):
                self.execute()
        self.assertEqual(database_volume.current(self.state),database_volume.DEFAULT)

    def test_an_adopted_replacement_must_be_on_the_new_volume(self):
        self.lifelike()
        with patch.object(migration,'wait_ready',side_effect=RuntimeError('readiness timed out')):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.docker.replacement_volume='fixture-pgdata'
        with self.assertRaisesRegex(RuntimeError,'volume differs'):
            self.execute()


class ContainerDocker(LifelikeDocker):
    """One container under the durable name, tracked by identity: run creates it with the image and
    volume it was given, rm -f by id removes it, inspect by name or id finds only that one."""

    IDENTIFIERS=('b'*64,'d'*64,'e'*64)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.container=None
        self.made=0
        self.removed=[]

    def info(self):
        item=self.container
        return {**retired_info(volume=item['volume']),'Id':item['Id'],'Image':item['image'],
                'State':{'Running':True},'Config':{'Labels':{'io.sbarbase.owner':item.get('owner',TARGET.owner),
                                                            'io.sbarbase.tier':'system'}}}

    def handle(self,args,data):
        command=args[0]
        if command=='run' and '--rm' not in args:
            if self.retired is not None or self.container is not None:return self.result(returncode=125)
            volume,image=args[args.index('-v')+1].split(':')[0],args[args.index('-v')+2]
            self.container={'Id':self.IDENTIFIERS[self.made],'volume':volume,'image':image}
            self.made+=1
            self.new_identifier=self.container['Id']
            return self.result(stdout=self.container['Id'])
        if command=='rm':
            if args[-1]==TARGET.container_id:
                self.retired=None
            elif self.container is not None and args[-1]==self.container['Id']:
                self.removed.append(args[-1])
                self.container=None
            return self.result()
        if command=='inspect' and args[1]!='--format' and args[1]!=TARGET.container_id:
            if self.container is not None and args[1] in (self.container['Id'],TARGET.name):
                return self.result(stdout=json.dumps([self.info()]))
            if args[1]==TARGET.name and self.retired is not None:
                return super().handle(args,data)
            return self.result(returncode=1)
        if command=='exec' and args[1] in self.IDENTIFIERS:
            if self.container is None or self.container['Id']!=args[1]:return self.result(returncode=1)
            return self.handle_exec(args)
        return super().handle(args,data)


class SupersedeTests(ImageChangeTests.__base__):
    """P3 part 4: a move whose replacement never became ready, superseded onto a rebuilt volume."""

    setUp=ImageChangeTests.setUp
    replacement=ImageChangeTests.replacement
    NEW_VOLUME='sbarbase-durable-pgdata-0123456789ab'

    def execute(self,image):
        outcome={'version':1,'kind':'retired-applied-reload-acknowledged','journal':{'token':self.__dict__.setdefault('token',str(uuid.uuid4()))},
                 'application':'publication-witnessed','activation':'unknown'}
        with patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']), \
             patch.object(hba_generation,'read_existing'), patch('hba_authority.initialize'), \
             patch('hba_runtime.SourceHBA') as writer, patch('hba_migration.outcome_for',return_value=outcome):
            writer.return_value.publish.return_value=outcome
            return migration.execute(self.docker,self.state,replacement=self.replacement(image),desired=DESIRED)

    def stuck(self):
        """The P1 move's change onto NEW_IMAGE: the retired container removed, the replacement created
        on the old volume and never ready."""
        self.docker=ContainerDocker(self.state,registry=self.registry,new_image=NEW_IMAGE)
        self.docker.published=revision()+DESIRED
        self.docker.volumes={'fixture-pgdata',self.NEW_VOLUME}
        self.stuck_intent=migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                                   inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',
                                                   image=NEW_IMAGE)
        with patch.object(migration,'wait_ready',side_effect=RuntimeError('readiness timed out')), \
                self.assertRaisesRegex(RuntimeError,'readiness timed out'):
            self.execute(NEW_IMAGE)
        self.assertIsNone(self.docker.retired)
        self.assertEqual(self.docker.container['volume'],'fixture-pgdata')

    def supersede(self,image=TARGET.image,volume=None):
        return migration.supersede(self.docker,self.state,target=TARGET,
                                   stuck_replacement=lambda intent:self.replacement(migration.target_image(intent)),
                                   inventory=authority.digest(DESIRED),image=image,new_volume=volume or self.NEW_VOLUME)

    def superseded(self):
        return migration._read_superseded(self.state/migration.ARCHIVE/self.stuck_intent['migration']/migration.SUPERSEDED)

    def test_the_stuck_move_is_superseded_onto_the_rebuilt_volume_and_finishes(self):
        self.stuck()
        intent=self.supersede()
        archived=self.superseded()
        self.assertEqual((intent['version'],intent['supersedes'],intent['migration'],intent['new_volume']),
                         (4,self.stuck_intent['migration'],archived['superseded_by'],self.NEW_VOLUME))
        self.assertNotIn('image',intent,'the way back onto the retired image records no image')
        self.assertEqual(archived['intent'],self.stuck_intent)
        self.assertEqual(archived['replacement'],'b'*64)
        self.assertEqual(self.docker.removed,['b'*64])
        self.assertEqual(migration.retired_volumes(self.state),set(),'the superseded archive marks no volume')
        self.execute(TARGET.image)
        self.assertFalse(migration.present(self.state))
        self.assertEqual(database_volume.current(self.state),self.NEW_VOLUME)
        self.assertEqual(self.docker.container,{'Id':'d'*64,'volume':self.NEW_VOLUME,'image':TARGET.image})
        pin=migration.pinned(self.state)
        self.assertEqual((pin['target']['container_id'],pin['target']['image']),('d'*64,TARGET.image))
        evidence=migration.read_evidence(self.state,intent['migration'])
        self.assertEqual((evidence['supersedes'],evidence['retired_container'],evidence['target_volume']),
                         (self.stuck_intent['migration'],TARGET.container_id,self.NEW_VOLUME))
        self.assertEqual(migration.retired_volumes(self.state),{'fixture-pgdata'})
        # The stuck migration's own archive stays: its retired generation and what superseded it.
        stuck_archive=self.state/migration.ARCHIVE/self.stuck_intent['migration']
        self.assertTrue((stuck_archive/'retired-generation.json').exists())
        self.assertTrue((stuck_archive/migration.SUPERSEDED).exists())

    def test_the_captured_and_archived_retired_state_come_from_the_superseded_archive(self):
        self.stuck()
        captured=migration.read_checkpoint(self.state,'old-captured')['retired']
        archived=migration.read_checkpoint(self.state,'retired-archived')['retired_generation']
        intent=self.supersede(image=NEW_IMAGE)
        self.assertEqual(intent['image'],NEW_IMAGE)
        with patch.object(migration,'capture_retired',side_effect=AssertionError('no container to capture')), \
                patch.object(migration,'retired_archive',side_effect=AssertionError('no container to archive')), \
                patch.object(migration,'publish_rules',side_effect=RuntimeError('stop here')), \
                self.assertRaisesRegex(RuntimeError,'stop here'):
            self.execute(NEW_IMAGE)
        self.assertEqual(migration.read_checkpoint(self.state,'old-captured')['retired'],captured)
        self.assertEqual(migration.read_checkpoint(self.state,'retired-archived')['retired_generation'],archived)
        self.assertEqual(migration.read_checkpoint(self.state,'old-captured')['migration'],intent['migration'])
        self.execute(NEW_IMAGE)
        self.assertFalse(migration.present(self.state))

    def test_every_interruption_reaches_the_same_intent(self):
        for point in ('_remove_superseded_replacement','_remove_record','publish_intent'):
            with self.subTest(point=point):
                self.setUp()
                self.stuck()
                with patch.object(migration,point,side_effect=RuntimeError('killed')), \
                        self.assertRaisesRegex(RuntimeError,'killed'):
                    self.supersede()
                self.assertTrue(self.superseded())
                intent=self.supersede()
                self.assertEqual(intent['migration'],self.superseded()['superseded_by'])
                self.assertEqual(self.docker.removed,['b'*64])
                self.execute(TARGET.image)
                self.assertEqual(database_volume.current(self.state),self.NEW_VOLUME)

    def test_a_rerun_for_another_volume_image_or_inventory_is_refused(self):
        self.stuck()
        with patch.object(migration,'_remove_record',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        for kwargs in ({'image':NEW_IMAGE},{'volume':'sbarbase-durable-pgdata-ba9876543210'}):
            with self.subTest(**kwargs), self.assertRaisesRegex(RuntimeError,'archived for another'):
                self.supersede(**kwargs)
        self.assertTrue(migration.present(self.state))
        with patch.object(migration,'publish_intent',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        with self.assertRaisesRegex(RuntimeError,'archived for another'):
            self.supersede(image=NEW_IMAGE)

    def test_only_the_stuck_state_is_superseded(self):
        # The retired container still exists: the run stopped before it was removed.
        self.docker=ContainerDocker(self.state,registry=self.registry,new_image=NEW_IMAGE)
        self.docker.published=revision()+DESIRED
        migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                 inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',image=NEW_IMAGE)
        with patch.object(migration,'create_replacement',side_effect=RuntimeError('stopped')), self.assertRaises(RuntimeError):
            self.execute(NEW_IMAGE)
        with self.assertRaisesRegex(RuntimeError,'still exists'):
            self.supersede()
        self.assertFalse(list((self.state/migration.ARCHIVE).glob('*/'+migration.SUPERSEDED)))
        self.assertTrue(migration.present(self.state))

    def test_a_migration_past_its_replacement_capture_is_not_superseded(self):
        self.stuck()
        with patch.object(migration,'publish_rules',side_effect=RuntimeError('stop here')), self.assertRaises(RuntimeError):
            self.execute(NEW_IMAGE)
        self.assertTrue(migration.done(self.state,'new-captured'))
        with self.assertRaisesRegex(RuntimeError,'Only a migration stopped'):
            self.supersede()
        self.assertEqual(self.docker.removed,[])

    def test_a_pin_that_no_longer_names_the_retired_container_refuses(self):
        self.stuck()
        pin=migration.pinned(self.state)
        with patch.object(migration,'pinned',return_value={**pin,'generation':str(uuid.uuid4())}), \
                self.assertRaisesRegex(RuntimeError,'no longer names'):
            self.supersede()
        moved={**pin,'target':{**pin['target'],'container_id':'f'*64}}
        with patch.object(migration,'pinned',return_value=moved), self.assertRaisesRegex(RuntimeError,'no longer names'):
            self.supersede()
        other=hba_target.Target('f'*64,TARGET.name,TARGET.owner,TARGET.image)
        with self.assertRaisesRegex(RuntimeError,'no longer names'):
            migration.supersede(self.docker,self.state,target=other,stuck_replacement=lambda intent:self.replacement(NEW_IMAGE),
                                inventory=authority.digest(DESIRED),image=TARGET.image,new_volume=self.NEW_VOLUME)
        self.assertTrue(migration.present(self.state))
        self.assertEqual(self.docker.removed,[])

    def test_a_container_under_the_name_that_is_not_the_stuck_replacement_is_kept_and_refused(self):
        for change in ({'volume':self.NEW_VOLUME},{'image':'sha256:'+'9'*64},{'owner':'foreign'}):
            with self.subTest(change=change):
                self.setUp()
                self.stuck()
                self.docker.container.update(change)
                with self.assertRaisesRegex(RuntimeError,'differs|changed'):
                    self.supersede()
                self.assertEqual(self.docker.removed,[])
                self.assertTrue(migration.present(self.state))
                self.assertFalse(list((self.state/migration.ARCHIVE).glob('*/'+migration.SUPERSEDED)))

    def test_a_rerun_proves_the_archived_replacement_again_before_removing_it(self):
        self.stuck()
        original=self.docker.handle
        self.docker.handle=lambda args,data:self.docker.result(returncode=1) if args[0]=='rm' else original(args,data)
        with self.assertRaisesRegex(RuntimeError,'could not be removed'):
            self.supersede()
        self.docker.handle=original
        self.docker.container['volume']=self.NEW_VOLUME
        with self.assertRaisesRegex(RuntimeError,'differs|changed'):
            self.supersede()
        self.assertEqual(self.docker.removed,[])
        self.assertTrue(migration.present(self.state))

    def test_a_removal_the_daemon_acknowledges_but_does_not_perform_refuses(self):
        self.stuck()
        original=self.docker.handle
        self.docker.handle=lambda args,data:self.docker.result() if args[0]=='rm' else original(args,data)
        with self.assertRaisesRegex(RuntimeError,'could not be removed'):
            self.supersede()
        self.assertTrue(migration.present(self.state))

    def test_a_finished_superseding_migration_leaves_nothing_to_supersede(self):
        self.stuck()
        self.supersede()
        self.execute(TARGET.image)
        with self.assertRaisesRegex(RuntimeError,'No stuck'):
            self.supersede()

    def test_an_archive_that_names_another_superseding_migration_refuses_the_resume(self):
        self.stuck()
        intent=self.supersede()
        archived=self.superseded()
        with patch.object(migration,'_read_superseded',return_value={**archived,'superseded_by':str(uuid.uuid4())}), \
                self.assertRaisesRegex(RuntimeError,'names another migration'):
            self.execute(TARGET.image)
        self.assertFalse(migration.done(self.state,'old-captured'))
        self.assertEqual(intent['migration'],archived['superseded_by'])

    def test_a_waiting_superseded_migration_is_published_only_for_its_own_retired_container(self):
        self.stuck()
        with patch.object(migration,'publish_intent',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        self.assertFalse(migration.present(self.state))
        other=hba_target.Target('f'*64,TARGET.name,TARGET.owner,TARGET.image)
        with self.assertRaisesRegex(RuntimeError,'retired another container'):
            migration.supersede(self.docker,self.state,target=other,stuck_replacement=lambda intent:self.replacement(NEW_IMAGE),
                                inventory=authority.digest(DESIRED),image=TARGET.image,new_volume=self.NEW_VOLUME)
        self.assertFalse(migration.present(self.state))

    def test_a_missing_superseded_archive_refuses_the_resume_and_keeps_the_record(self):
        self.stuck()
        self.supersede()
        path=self.state/migration.ARCHIVE/self.stuck_intent['migration']/migration.SUPERSEDED
        path.rename(path.with_name('moved-aside.json'))
        with self.assertRaisesRegex(RuntimeError,'archive is missing'):
            self.execute(TARGET.image)
        self.assertTrue(migration.present(self.state))
        self.assertFalse(migration.done(self.state,'old-captured'))

    def test_checkpoints_of_another_intent_refuse_before_anything_is_written_or_removed(self):
        self.stuck()
        real=migration.read_checkpoint
        with patch.object(migration,'read_checkpoint',lambda state,phase:{**real(state,phase),'intent':'0'*64}), \
                self.assertRaisesRegex(RuntimeError,'another intent'):
            self.supersede()
        self.assertEqual(self.docker.removed,[])
        self.assertFalse(list((self.state/migration.ARCHIVE).glob('*/'+migration.SUPERSEDED)))

    def test_a_failed_inspection_is_never_read_as_no_replacement(self):
        self.stuck()
        original=self.docker.handle
        listed=self.docker.result(stdout='b'*64+' '+TARGET.name+'\n')

        def transient(args,data):
            if args[0]=='inspect' and args[1]==TARGET.name:return self.docker.result(returncode=1)
            if args[0]=='ps':return listed
            return original(args,data)
        self.docker.handle=transient
        with self.assertRaisesRegex(RuntimeError,'absence is not proven'):
            self.supersede()
        self.assertFalse(list((self.state/migration.ARCHIVE).glob('*/'+migration.SUPERSEDED)))
        self.assertTrue(migration.present(self.state))
        self.docker.handle=original
        self.supersede()
        self.assertEqual(self.superseded()['replacement'],'b'*64)

    def test_an_archived_replacement_whose_inspection_fails_is_not_read_as_removed(self):
        self.stuck()
        original=self.docker.handle
        self.docker.handle=lambda args,data:self.docker.result(returncode=1) if args[0]=='rm' else original(args,data)
        with self.assertRaises(RuntimeError):
            self.supersede()
        listed=self.docker.result(stdout='b'*64+' '+TARGET.name+'\n')

        def transient(args,data):
            if args[0]=='inspect' and args[1]=='b'*64:return self.docker.result(returncode=1)
            if args[0]=='ps':return listed
            return original(args,data)
        self.docker.handle=transient
        with self.assertRaisesRegex(RuntimeError,'absence is not proven'):
            self.supersede()
        self.assertTrue(migration.present(self.state))
        self.assertEqual(self.docker.removed,[])

    def test_a_removal_whose_container_is_still_listed_refuses(self):
        self.stuck()
        original=self.docker.handle
        removed=[]

        def lingering(args,data):
            if args[0]=='rm' and args[-1]=='b'*64:
                removed.append(True)
                return self.docker.result()
            if removed and args[0]=='inspect' and args[1]=='b'*64:return self.docker.result(returncode=1)
            if removed and args[0]=='ps':return self.docker.result(stdout='b'*64+' '+TARGET.name+'\n')
            return original(args,data)
        self.docker.handle=lingering
        with self.assertRaisesRegex(RuntimeError,'could not be removed|not proven'):
            self.supersede()
        self.assertTrue(migration.present(self.state))

    def test_a_version_4_intent_without_a_rebuilt_volume_is_malformed(self):
        self.stuck()
        intent=self.supersede()
        with self.assertRaises(ValueError):
            migration.validate({key:value for key,value in intent.items() if key!='new_volume'})

    def test_a_migration_being_superseded_is_not_reconciled_on_its_own(self):
        self.stuck()
        original=self.docker.handle
        self.docker.handle=lambda args,data:self.docker.result(returncode=1) if args[0]=='rm' else original(args,data)
        with self.assertRaisesRegex(RuntimeError,'could not be removed'):
            self.supersede()
        self.docker.handle=original
        with self.assertRaisesRegex(RuntimeError,'being superseded'):
            self.execute(NEW_IMAGE)
        self.assertFalse(migration.done(self.state,'new-captured'))
        self.assertEqual(self.docker.removed,[])

    def test_a_rerun_rechecks_the_stuck_state_before_removing_anything(self):
        self.stuck()
        with patch.object(migration,'_remove_superseded_replacement',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        # The stuck migration moved on meanwhile (its replacement captured after all).
        intent=migration.load(self.state)
        migration.checkpoint(self.state,'new-captured',migration._bind(intent,{'container_id':'b'*64,'mounts':[]}))
        with self.assertRaisesRegex(RuntimeError,'Only a migration stopped'):
            self.supersede()
        self.assertEqual(self.docker.removed,[])
        self.assertTrue(migration.done(self.state,'new-captured'))

    def test_no_other_migration_starts_while_a_superseded_one_waits(self):
        self.stuck()
        with patch.object(migration,'publish_intent',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        with self.assertRaisesRegex(RuntimeError,'cluster_takeover'):
            migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                     inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',image=NEW_IMAGE)
        self.assertFalse(migration.present(self.state))
        self.assertEqual(self.supersede()['supersedes'],self.stuck_intent['migration'])

    def test_a_stuck_migration_that_finished_after_all_is_not_waiting(self):
        self.stuck()
        with patch.object(migration,'publish_intent',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        self.assertIsNotNone(migration.pending_superseded(self.state))
        migration._publish_once(self.state/migration.ARCHIVE/self.stuck_intent['migration']/migration.EVIDENCE,
                                {'version':1,'kind':'generation-migration'},'Generation migration evidence')
        self.assertIsNone(migration.pending_superseded(self.state))

    def test_more_than_one_waiting_superseded_migration_refuses(self):
        self.stuck()
        with patch.object(migration,'publish_intent',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        archived=self.superseded()
        twin=self.state/migration.ARCHIVE/str(uuid.uuid4())
        twin.mkdir(mode=0o700)
        (twin/migration.SUPERSEDED).write_text('copy')
        records={str(twin/migration.SUPERSEDED):{**archived,'superseded_by':str(uuid.uuid4())}}
        with patch.object(migration,'_read_superseded',lambda path:records.get(str(path),archived)), \
                self.assertRaisesRegex(RuntimeError,'More than one'):
            self.supersede()
        self.assertFalse(migration.present(self.state))

    def test_an_archive_written_for_another_intent_is_refused(self):
        self.stuck()
        with patch.object(migration,'_remove_superseded_replacement',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        archived=self.superseded()
        with patch.object(migration,'_read_superseded',return_value={**archived,'intent':{**archived['intent'],'inventory':'0'*64}}), \
                self.assertRaisesRegex(RuntimeError,'belongs to another intent'):
            self.supersede()
        self.assertTrue(migration.present(self.state))

    def test_a_replacement_that_cannot_be_removed_keeps_the_record(self):
        self.stuck()
        original=self.docker.handle
        self.docker.handle=lambda args,data:self.docker.result(returncode=1) if args[0]=='rm' else original(args,data)
        with self.assertRaisesRegex(RuntimeError,'could not be removed'):
            self.supersede()
        self.assertTrue(migration.present(self.state))
        self.docker.handle=original
        self.supersede()
        self.assertEqual(self.docker.removed,['b'*64])

    def test_nothing_to_supersede_or_a_superseding_record_refuses(self):
        self.docker=ContainerDocker(self.state,registry=self.registry)
        with self.assertRaisesRegex(RuntimeError,'No stuck'):
            self.supersede()
        self.setUp()
        self.stuck()
        self.supersede()
        with self.assertRaisesRegex(RuntimeError,'--reconcile'):
            self.supersede()

    def test_a_superseded_record_that_does_not_hold_its_own_checkpoints_is_refused(self):
        self.stuck()
        with patch.object(migration,'_remove_record',side_effect=RuntimeError('killed')), self.assertRaises(RuntimeError):
            self.supersede()
        path=self.state/migration.ARCHIVE/self.stuck_intent['migration']/migration.SUPERSEDED
        record=self.superseded()
        other={**record['checkpoints'],'old-captured':{**record['checkpoints']['old-captured'],'intent':'0'*64}}
        for broken,reason in (({**record,'checkpoints':other},'does not hold its own checkpoints'),
                              ({**record,'checkpoints':[]},'malformed'),({**record,'kind':'other'},'malformed'),
                              ({**record,'superseded_by':record['migration']},'malformed'),
                              ({**record,'extra':1},'malformed')):
            with self.subTest(reason=reason), patch.object(migration,'_decode_envelope',return_value=broken), \
                    self.assertRaisesRegex(RuntimeError,reason):
                migration._read_superseded(path)

    def test_a_superseding_intent_names_another_migration_and_a_rebuilt_volume(self):
        self.stuck()
        intent=self.supersede()
        for broken in ({**intent,'supersedes':intent['migration']},{**intent,'supersedes':'x'},
                       {**intent,'retired_state':'absent-verified'}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                migration.validate(broken)
        with self.assertRaisesRegex(ValueError,'rebuilt volume'):
            migration.publish_intent(self.state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                     inventory=authority.digest(DESIRED),retired_state='stopped-will-not-return',
                                     supersedes=str(uuid.uuid4()))


if __name__=='__main__':unittest.main()
