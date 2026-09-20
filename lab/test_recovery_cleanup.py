"""Failure injection for actual database restore cleanup, without Docker writes."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('recovery_restore_test',Path(__file__).with_name('recovery-restore-db.py'))
restore=importlib.util.module_from_spec(spec);spec.loader.exec_module(restore)


class CleanupTests(unittest.TestCase):
    def execute(self,helper_owner='recovery-target',helper_failure=False,stop_failure=False,running=False,missing=False):
        calls=[];saved=[];descriptor={'status':'database-verified','stage':'verified'}
        def docker(*args,**kwargs):
            calls.append(args)
            if args[0]=='container' or args[0]=='inspect':
                name=args[-1]
                if missing:return SimpleNamespace(returncode=1,stdout='')
                owner=helper_owner if name=='helper' else 'recovery-target'
                return SimpleNamespace(returncode=0,stdout=json.dumps([{'Config':{'Labels':{'io.sbarbase.owner':owner}},'State':{'Running':running}}]))
            if args[:2]==('rm','-f') and helper_failure:raise RuntimeError('injected removal failure')
            if args[0]=='stop' and stop_failure:raise RuntimeError('injected stop failure')
            return SimpleNamespace(returncode=0,stdout='')
        error=None
        with patch.object(restore.lab,'docker',docker),patch.object(restore.runtime,'atomic',lambda path,value:saved.append(dict(value))):
            try:restore.cleanup_target(descriptor,Path('unused'),'helper','db')
            except RuntimeError as exc:error=exc
        return calls,saved,error

    def test_helper_failure_still_attempts_database_stop(self):
        calls,saved,error=self.execute(helper_failure=True)
        self.assertIn(('stop','db'),calls)
        self.assertIsNotNone(error)
        self.assertEqual(saved[-1]['status'],'cleanup-failed')

    def test_foreign_helper_is_untouched_but_database_stops(self):
        calls,saved,error=self.execute(helper_owner='unrelated')
        self.assertNotIn(('rm','-f','helper'),calls)
        self.assertIn(('stop','db'),calls)
        self.assertEqual(saved[-1]['status'],'cleanup-failed')

    def test_stop_failure_never_publishes_success(self):
        _,saved,error=self.execute(stop_failure=True)
        self.assertIsNotNone(error)
        self.assertEqual(saved[-1]['status'],'cleanup-failed')

    def test_running_after_stop_is_failure(self):
        _,saved,error=self.execute(running=True)
        self.assertIsNotNone(error)
        self.assertEqual(saved[-1]['status'],'cleanup-failed')

    def test_stopped_target_allows_success(self):
        _,saved,error=self.execute()
        self.assertIsNone(error)
        self.assertEqual(saved[-1]['status'],'database-restored')

    def test_missing_verified_target_cannot_be_success(self):
        _,saved,error=self.execute(missing=True)
        self.assertIsNotNone(error)
        self.assertEqual(saved[-1]['status'],'cleanup-failed')
