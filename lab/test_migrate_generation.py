"""The operator command refuses the retained placement unless an attended run names it."""
import contextlib
import importlib.util
import io
import shutil
from pathlib import Path
import sys
import tempfile
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
    def run_main(self,arguments,*,target=RETAINED,reconcile=False):
        state=tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree,state,True)
        argv=['migrate-generation.py','--state',state,'--inventory',self.inventory,*arguments]
        with patch.object(sys,'argv',argv),\
             patch.object(command.hba_migration,'pinned',return_value={**PIN,'target':target}),\
             patch.object(command.hba_migration,'load',return_value={'old':target,'migration':'m','generation':PIN['generation']}),\
             patch.object(command.hba_migration,'publish_intent',return_value={'migration':'m','generation':PIN['generation']}) as intent,\
             patch.object(command.hba_migration,'execute',return_value=True) as execute,\
             patch.object(command,'plan_for',return_value={}):
            try:
                with contextlib.redirect_stdout(io.StringIO()):command.main()
                return intent,execute,None
            except RuntimeError as error:
                return intent,execute,error

    def setUp(self):
        handle=tempfile.NamedTemporaryFile('w',suffix='.conf',delete=False)
        handle.write('local all supabase_admin trust\n');handle.close()
        self.inventory=handle.name
        self.addCleanup(Path(self.inventory).unlink)

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

    def test_a_disposable_fixture_runs_without_the_override(self):
        intent,execute,error=self.run_main(BASE,target=DISPOSABLE)
        self.assertIsNone(error)
        intent.assert_called_once();execute.assert_called_once()


if __name__=='__main__':
    unittest.main()
