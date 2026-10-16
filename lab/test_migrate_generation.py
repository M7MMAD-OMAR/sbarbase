"""The operator command refuses the retained placement unless an attended run names it."""
import contextlib
import importlib.util
import io
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC=importlib.util.spec_from_file_location('migrate_generation',Path(__file__).with_name('migrate-generation.py'))
command=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(command)

RETAINED={'container_id':'a'*64,'name':'sbarbase-durable-db','owner':'durable-upstream','image':'sha256:'+'c'*64}
DISPOSABLE={'container_id':'a'*64,'name':'fixture-db','owner':'fixture','image':'sha256:'+'c'*64}
PIN={'version':1,'generation':'3d427363-a624-40d9-ab91-f1ec05fdb8d1'}
BASE=['--volume','sbarbase-durable-pgdata','--network','sbarbase-durable-net','--tier','system.db',
      '--retired','stopped','--assert-retired-will-not-return']


UPGRADE_STATE=command.UPGRADE_STATE


class RefuseRetained(unittest.TestCase):
    def test_the_retained_placement_is_refused_by_default(self):
        with self.assertRaisesRegex(RuntimeError,'deliberate operator run'):
            command.refuse_retained('sbarbase-durable-db','durable-upstream')
        with self.assertRaisesRegex(RuntimeError,'deliberate operator run'):
            command.refuse_retained('sbarbase-restore-0123456789ab-db','recovery-target')
        with self.assertRaisesRegex(RuntimeError,'deliberate operator run'):
            command.refuse_retained('sbarbase-durable-db','someone-else')
        with self.assertRaisesRegex(RuntimeError,'deliberate operator run'):
            command.refuse_retained('fixture-db','durable-upstream')

    def test_a_disposable_placement_needs_no_override(self):
        self.assertTrue(command.refuse_retained('fixture-db','fixture'))

    def test_an_attended_run_that_names_the_pinned_container_passes(self):
        self.assertTrue(command.refuse_retained('sbarbase-durable-db','durable-upstream',
                                                attended=True,confirmed='sbarbase-durable-db'))

    def test_a_mismatched_name_refuses(self):
        for name in ('sbarbase-durable','sbarbase-durable-db ','sbarbase-restore-0123456789ab-db',''):
            with self.assertRaisesRegex(RuntimeError,'does not match'):
                command.refuse_retained('sbarbase-durable-db','durable-upstream',attended=True,confirmed=name)

    def test_either_flag_alone_refuses(self):
        with self.assertRaisesRegex(RuntimeError,'needs both'):
            command.refuse_retained('sbarbase-durable-db','durable-upstream',attended=True)
        with self.assertRaisesRegex(RuntimeError,'needs both'):
            command.refuse_retained('sbarbase-durable-db','durable-upstream',confirmed='sbarbase-durable-db')
        with self.assertRaisesRegex(RuntimeError,'needs both'):
            command.refuse_retained('fixture-db','fixture',confirmed='fixture-db')


class Main(unittest.TestCase):
    def run_main(self,arguments,*,target=RETAINED,reconcile=False,empty_record=False):
        state=tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree,state,True)
        self.state=Path(state)
        if empty_record:
            (self.state/command.hba_migration.hba_generation.MIGRATION).mkdir(mode=0o700)
        argv=['migrate-generation.py','--state',state,'--inventory',self.inventory,*arguments]
        with patch.object(sys,'argv',argv),\
             patch.object(command.hba_migration,'pinned',return_value={**PIN,'target':target}),\
             patch.object(command.hba_migration,'load',return_value={'old':target,'migration':'m','generation':PIN['generation']}),\
             patch.object(command.hba_migration,'publish_intent',return_value={'migration':'m','generation':PIN['generation']}) as intent,\
             patch.object(command.hba_migration,'execute',return_value=True) as execute,\
             patch.object(command,'plan_for',return_value={}) as self.plan:
            try:
                with contextlib.redirect_stdout(io.StringIO()):command.main()
                return intent,execute,None
            except RuntimeError as error:
                return intent,execute,error

    def setUp(self):
        upgrades=tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree,upgrades,True)
        self.upgrade=Path(upgrades)/'state.json'
        patcher=patch.object(command,'UPGRADE_STATE',self.upgrade)
        patcher.start();self.addCleanup(patcher.stop)
        with tempfile.NamedTemporaryFile('w',suffix='.conf',delete=False) as handle:
            self.inventory=handle.name
            self.addCleanup(Path(self.inventory).unlink)
            handle.write('local all supabase_admin trust\n')

    def test_the_default_refusal_precedes_the_intent(self):
        intent,execute,error=self.run_main(BASE)
        self.assertRegex(str(error),'deliberate operator run')
        intent.assert_not_called();execute.assert_not_called()

    def test_the_override_publishes_the_intent_and_runs(self):
        intent,execute,error=self.run_main([*BASE,'--attended-retained','--confirm-retained','sbarbase-durable-db'])
        self.assertIsNone(error)
        intent.assert_called_once();execute.assert_called_once()

    def test_a_mismatched_or_partial_override_refuses_before_the_intent(self):
        for extra in (['--attended-retained','--confirm-retained','sbarbase-durable-storage'],
                      ['--attended-retained'],['--confirm-retained','sbarbase-durable-db']):
            intent,execute,error=self.run_main([*BASE,*extra])
            self.assertIsNotNone(error,extra)
            intent.assert_not_called();execute.assert_not_called()

    def test_reconciliation_carries_the_same_gate(self):
        _,execute,error=self.run_main([*BASE,'--reconcile'])
        self.assertRegex(str(error),'deliberate operator run')
        execute.assert_not_called()
        _,execute,error=self.run_main([*BASE,'--reconcile','--attended-retained','--confirm-retained','sbarbase-durable-db'])
        self.assertIsNone(error)
        execute.assert_called_once()

    def test_an_unconfirmed_upgrade_or_rollback_refuses_before_the_intent(self):
        for phase in ('applied','rolling_back'):
            self.upgrade.write_text('{"phase": "%s"}'%phase)
            intent,execute,error=self.run_main(BASE,target=DISPOSABLE)
            self.assertRegex(str(error),'not confirmed yet',phase)
            intent.assert_not_called();execute.assert_not_called()
        for text in ('{"phase": ',"[]"):
            self.upgrade.write_text(text)
            intent,_,error=self.run_main(BASE,target=DISPOSABLE)
            self.assertRegex(str(error),'review it',text)
            intent.assert_not_called()

    def test_a_settled_upgrade_record_lets_the_migration_run(self):
        for phase in ('confirmed','rolled_back','failed'):
            self.upgrade.write_text('{"phase": "%s"}'%phase)
            intent,execute,error=self.run_main(BASE,target=DISPOSABLE)
            self.assertIsNone(error,phase)
            intent.assert_called_once();execute.assert_called_once()

    def test_finishing_an_interrupted_migration_is_not_held_back(self):
        self.upgrade.write_text('{"phase": "applied"}')
        _,execute,error=self.run_main([*BASE,'--reconcile'],target=DISPOSABLE)
        self.assertIsNone(error)
        execute.assert_called_once()

    def test_an_empty_record_left_before_its_intent_is_removed_where_every_run_comes_in(self):
        _,execute,error=self.run_main([*BASE,'--reconcile'],target=DISPOSABLE,empty_record=True)
        self.assertRegex(str(error),'without --reconcile')
        execute.assert_not_called()
        self.assertFalse(command.hba_migration.present(self.state))
        intent,execute,error=self.run_main(BASE,target=DISPOSABLE,empty_record=True)
        self.assertIsNone(error)
        intent.assert_called_once();execute.assert_called_once()

    def test_a_record_with_anything_in_it_is_kept(self):
        state=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,state,True)
        directory=Path(state)/command.hba_migration.hba_generation.MIGRATION
        directory.mkdir(mode=0o700);(directory/'intent.json').write_text('{"record":')
        self.assertFalse(command.hba_migration.discard_unpublished(state))
        self.assertTrue(directory.exists())

    def test_a_new_volume_goes_into_the_intent_and_reconcile_refuses_the_flag(self):
        volume='sbarbase-durable-pgdata-0123456789ab'
        with patch.object(command.hba_migration,'require_cluster_volume') as probe:
            intent,execute,error=self.run_main([*BASE,'--to-volume',volume],target=DISPOSABLE)
        self.assertIsNone(error)
        self.assertEqual(intent.call_args.kwargs['new_volume'],volume)
        self.assertEqual(probe.call_args.args[1:],(volume,DISPOSABLE['owner'],DISPOSABLE['image'],self.state))
        intent,_,error=self.run_main(BASE,target=DISPOSABLE)
        self.assertIsNone(intent.call_args.kwargs['new_volume'])
        _,execute,error=self.run_main([*BASE,'--reconcile','--to-volume',volume],target=DISPOSABLE)
        self.assertRegex(str(error),'drop --to-volume')
        execute.assert_not_called()

    def test_the_probe_runs_on_the_lock_image_when_both_flags_are_given(self):
        image='sha256:'+'d'*64
        with patch.object(command,'lock_image',return_value=image), \
                patch.object(command.hba_migration,'require_cluster_volume') as probe:
            intent,execute,error=self.run_main([*BASE,'--image-from-lock','--to-volume','sbarbase-durable-pgdata-0123456789ab'],
                                               target=DISPOSABLE)
        self.assertIsNone(error)
        self.assertEqual(probe.call_args.args[3],image)
        self.assertEqual((intent.call_args.kwargs['image'],intent.call_args.kwargs['new_volume']),
                         (image,'sbarbase-durable-pgdata-0123456789ab'))

    def test_a_volume_that_cannot_take_the_database_refuses_before_the_intent(self):
        same=[*BASE[:BASE.index('--volume')],'--volume','sbarbase-durable-pgdata-0123456789ab',*BASE[BASE.index('--volume')+2:]]
        intent,execute,error=self.run_main([*same,'--to-volume','sbarbase-durable-pgdata-0123456789ab'],target=DISPOSABLE)
        self.assertRegex(str(error),'rebuilt database volume')
        intent.assert_not_called();execute.assert_not_called()
        for volume in ('sbarbase-durable-pgdata', 'elsewhere'):
            intent,execute,error=self.run_main([*BASE,'--to-volume',volume],target=DISPOSABLE)
            self.assertRegex(str(error),'rebuilt database volume')
            intent.assert_not_called();execute.assert_not_called()
        with patch.object(command.hba_migration,'require_cluster_volume',side_effect=RuntimeError('holds no PostgreSQL data directory')):
            intent,execute,error=self.run_main([*BASE,'--to-volume','sbarbase-durable-pgdata-0123456789ab'],target=DISPOSABLE)
        self.assertRegex(str(error),'no PostgreSQL data directory')
        intent.assert_not_called();execute.assert_not_called()

    def test_supersede_stuck_supersedes_and_finishes_onto_the_rebuilt_volume(self):
        volume='sbarbase-durable-pgdata-0123456789ab'
        superseding={'migration':'n','supersedes':'m','generation':PIN['generation']}
        with patch.object(command.hba_migration,'require_cluster_volume') as probe, \
                patch.object(command.hba_migration,'supersede',return_value=superseding) as supersede:
            intent,execute,error=self.run_main([*BASE,'--to-volume',volume,'--supersede-stuck'],target=DISPOSABLE)
        self.assertIsNone(error)
        intent.assert_not_called()
        probe.assert_called_once()
        kwargs=supersede.call_args.kwargs
        self.assertEqual((kwargs['target'].container_id,kwargs['new_volume'],kwargs['image']),(DISPOSABLE['container_id'],volume,None))
        self.assertEqual(kwargs['inventory'],command.hba_authority.digest(Path(self.inventory).read_text()))
        with patch.object(command,'plan_for',lambda old,options,image=None:{'image':image}):
            stuck_plan=kwargs['stuck_replacement']({'image':'sha256:'+'e'*64,'old':DISPOSABLE})
        self.assertEqual(stuck_plan['image'],'sha256:'+'e'*64,'the stuck replacement is proven on the image its intent recorded')
        execute.assert_called_once()
        # The replacement onto the rebuilt volume is planned on the image the migration was given.
        self.assertEqual(self.plan.call_args.args[2],None)

    def test_supersede_stuck_with_the_lock_image_supersedes_onto_that_image(self):
        volume='sbarbase-durable-pgdata-0123456789ab'
        lock='sha256:'+'f'*64
        with patch.object(command,'lock_image',return_value=lock), \
                patch.object(command.hba_migration,'require_cluster_volume') as probe, \
                patch.object(command.hba_migration,'supersede',return_value={'migration':'n','supersedes':'m','generation':PIN['generation']}) as supersede:
            intent,execute,error=self.run_main([*BASE,'--image-from-lock','--to-volume',volume,'--supersede-stuck'],target=DISPOSABLE)
        self.assertIsNone(error)
        self.assertEqual(supersede.call_args.kwargs['image'],lock)
        self.assertEqual(probe.call_args.args[1:4],(volume,DISPOSABLE['owner'],lock))
        self.assertEqual(self.plan.call_args.args[2],lock)

    def test_supersede_stuck_needs_a_rebuilt_volume_a_stopped_container_and_no_reconcile(self):
        volume='sbarbase-durable-pgdata-0123456789ab'
        for arguments,reason in (([*BASE,'--supersede-stuck'],'give --to-volume'),
                                 ([*BASE,'--reconcile','--to-volume',volume,'--supersede-stuck'],'without --reconcile')):
            with self.subTest(reason=reason), patch.object(command.hba_migration,'supersede') as supersede:
                intent,execute,error=self.run_main(arguments,target=DISPOSABLE)
            self.assertRegex(str(error),reason)
            supersede.assert_not_called();execute.assert_not_called()
        absent=[*BASE[:BASE.index('--retired')],'--retired','absent','--to-volume',volume,'--supersede-stuck']
        with patch.object(command.hba_migration,'require_cluster_volume'), \
                patch.object(command.hba_migration,'supersede') as supersede:
            intent,execute,error=self.run_main(absent,target=DISPOSABLE)
        self.assertRegex(str(error),'stopped its retired container')
        supersede.assert_not_called()

    def test_the_retained_placement_refuses_a_volume_other_than_the_one_it_mounts(self):
        attended=['--attended-retained','--confirm-retained','sbarbase-durable-db']
        other=[*BASE[:BASE.index('--volume')],'--volume','sbarbase-durable-pgdata-0123456789ab',*BASE[BASE.index('--volume')+2:]]
        intent,execute,error=self.run_main([*other,*attended])
        self.assertRegex(str(error),'volume the installation mounts')
        intent.assert_not_called();execute.assert_not_called()
        intent,execute,error=self.run_main([*BASE,*attended])
        self.assertIsNone(error)
        # A disposable placement, or a retained recovery target, keeps its own volume.
        intent,execute,error=self.run_main(other,target=DISPOSABLE)
        self.assertIsNone(error)
        restore={'container_id':'a'*64,'name':'sbarbase-restore-0123456789ab-db','owner':'recovery-target','image':'sha256:'+'c'*64}
        own=[*BASE[:BASE.index('--volume')],'--volume','sbarbase-restore-0123456789ab-pgdata',*BASE[BASE.index('--volume')+2:]]
        intent,execute,error=self.run_main([*own,'--attended-retained','--confirm-retained',restore['name']],target=restore)
        self.assertIsNone(error)
        # The guard needs both the runtime's owner and its durable name: either alone keeps its volume.
        owned={**DISPOSABLE,'owner':'durable-upstream'}
        intent,execute,error=self.run_main([*other,'--attended-retained','--confirm-retained','fixture-db'],target=owned)
        self.assertIsNone(error)
        named={**RETAINED,'owner':'recovery-target'}
        intent,execute,error=self.run_main([*other,*attended],target=named)
        self.assertIsNone(error)
        restore_owned={**restore,'owner':'durable-upstream'}
        intent,execute,error=self.run_main([*own,'--attended-retained','--confirm-retained',restore['name']],target=restore_owned)
        self.assertIsNone(error)
        near={**RETAINED,'name':'sbarbase-durabledb'}
        intent,execute,error=self.run_main([*other,'--attended-retained','--confirm-retained',near['name']],target=near)
        self.assertIsNone(error)

    def test_the_record_path_is_the_one_the_upgrade_command_writes(self):
        import upgrade
        self.assertEqual(UPGRADE_STATE,upgrade.STATE_FILE)

    def test_the_image_from_the_lock_goes_into_the_intent_and_the_replacement(self):
        image='sha256:'+'d'*64
        with patch.object(command,'lock_image',return_value=image):
            intent,execute,error=self.run_main([*BASE,'--image-from-lock'],target=DISPOSABLE)
        self.assertIsNone(error)
        self.assertEqual(intent.call_args.kwargs['image'],image)
        self.assertEqual(self.plan.call_args.args[2],image)
        # Without the flag the pinned image stays: no image is recorded.
        intent,_,error=self.run_main(BASE,target=DISPOSABLE)
        self.assertIsNone(intent.call_args.kwargs['image'])

    def test_reconcile_finishes_onto_the_recorded_image_and_refuses_the_lock_flag(self):
        image='sha256:'+'d'*64
        _,execute,error=self.run_main([*BASE,'--reconcile','--image-from-lock'],target=DISPOSABLE)
        self.assertRegex(str(error),'drop --image-from-lock')
        execute.assert_not_called()
        with patch.object(command.hba_migration,'target_image',return_value=image):
            _,execute,error=self.run_main([*BASE,'--reconcile'],target=DISPOSABLE)
        self.assertIsNone(error)
        self.assertEqual(self.plan.call_args.args[2],image)

    def test_the_lock_image_is_the_one_the_daemon_proves(self):
        import json as _json
        import subprocess
        root=Path(tempfile.mkdtemp());self.addCleanup(shutil.rmtree,root,True)
        (root/'lab').mkdir()
        index='sha256:'+'a'*64
        (root/'lab'/'distro-image.lock.json').write_text(_json.dumps(
            {'tag':'public.ecr.aws/supabase/postgres:17.11.0.004','id':index,
             'digests':['public.ecr.aws/supabase/postgres@'+index]}))
        config='sha256:'+'b'*64
        asked=[]

        def daemon(*args,**kwargs):
            asked.append(args)
            return subprocess.CompletedProcess(args,0,_json.dumps([{'Id':config,'RepoDigests':['public.ecr.aws/supabase/postgres@'+index]}]),'')
        with patch.object(command,'docker',daemon):
            self.assertEqual(command.lock_image(root),config)
        self.assertEqual(asked,[('image','inspect','public.ecr.aws/supabase/postgres@'+index)])

        def other(*args,**kwargs):
            return subprocess.CompletedProcess(args,0,_json.dumps([{'Id':config,'RepoDigests':['public.ecr.aws/supabase/postgres@sha256:'+'c'*64]}]),'')
        with patch.object(command,'docker',other), self.assertRaises(command.image_identity.IdentityError):
            command.lock_image(root)

    def test_the_plan_carries_the_image_it_is_given_or_the_pinned_one(self):
        old=command.hba_target.Target(**DISPOSABLE)
        env=tempfile.NamedTemporaryFile(delete=False);env.close();self.addCleanup(Path(env.name).unlink)
        options=SimpleNamespace(env_file=env.name,tier='system.db',memory='1024m',cpus='1',network='fixture-net')
        self.assertEqual(command.plan_for(old,options)['image'],DISPOSABLE['image'])
        self.assertEqual(command.plan_for(old,options,'sha256:'+'d'*64)['image'],'sha256:'+'d'*64)
        self.assertEqual((command.plan_for(old,options)['name'],command.plan_for(old,options)['owner']),
                         (DISPOSABLE['name'],DISPOSABLE['owner']))

    def test_a_disposable_fixture_runs_without_the_override(self):
        intent,execute,error=self.run_main(BASE,target=DISPOSABLE)
        self.assertIsNone(error)
        intent.assert_called_once();execute.assert_called_once()


if __name__=='__main__':
    unittest.main()
