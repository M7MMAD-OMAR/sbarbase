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
                 listing=None,cp_failure=(),stop_returns=0,rm_returns=0,role='t',new_identifier=NEW_CONTAINER):
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
                return self.result(stdout=json.dumps([{**retired_info(),'Id':identifier,'State':{'Running':True},
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

    def test_an_absent_retired_container_is_recreated_on_the_recorded_volume(self):
        state=Path(self.temp.name)/'absent'
        state.mkdir()
        hba_generation.publish(state,TARGET,self.generation)
        record=migration.publish_intent(state,target=TARGET,generation=self.generation,volume='fixture-pgdata',
                                        inventory=authority.digest(DESIRED),retired_state='absent-verified')
        migration.checkpoint(state,'old-captured',{'migration':record['migration'],
                                                   'intent':authority.digest(authority.canonical(record)),
                                                   'retired':{'container_id':None,'identity':'absent-verified','mounts':[],
                                                              'volume':'fixture-pgdata','hba_digest':None}})
        with patch('resource_policy.io_flags',return_value=['--device-read-bps','/dev/x:1mb']) as flags:
            identifier,mounts=migration.create_replacement(self.docker,state,record,
                                                           migration.validate_replacement(record,self.replacement()))
        self.assertEqual(identifier,NEW_CONTAINER)
        self.assertEqual([args for args in self.docker.calls if args[0]=='rm'],[])
        run=[args for args in self.docker.calls if args[0]=='run'][0]
        self.assertIn('io.sbarbase.tier=system',run)
        self.assertIn('fixture-net',run)
        flags.assert_called_once_with('system.db')
        self.assertEqual(mounts[0]['name'],'fixture-pgdata')

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
        with patch.object(hba_generation,'read_existing'), patch('hba_runtime.SourceHBA') as writer:
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

    def test_a_deliberate_rule_difference_is_reported_and_not_swallowed(self):
        self.docker.published=revision()+'local all all trust\n'
        self.docker.retired_rules=None
        self.execute()
        rules=migration.read_evidence(self.state,self.intent['migration'])['rules']
        self.assertEqual(rules['difference'],'different')
        self.assertFalse(rules['inventory_matches_observed'])
        self.assertFalse(rules['retired_matches_observed'])
        self.assertNotEqual(rules['observed_rules_digest'],rules['retired_rules_digest'])

    def test_completion_removes_the_record_and_reconciliation_refuses_twice(self):
        self.docker.published=revision()+DESIRED
        self.docker.retired_rules=None
        self.assertTrue(self.execute())
        self.assertFalse(migration.present(self.state))
        self.assertTrue(hba_generation.load(self.state)['generation']!=self.generation)
        with self.assertRaisesRegex(RuntimeError,'No generation migration record'):
            migration.execute(self.docker,self.state,replacement=self.replacement(),desired=DESIRED)


if __name__=='__main__':unittest.main()