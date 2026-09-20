import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('cutover_target_test',Path(__file__).with_name('cutover-target-check.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class MaintenanceTests(unittest.TestCase):
    def test_parent_pauses_route_left_active_by_failed_child(self):
        calls=[]
        def routing(value):
            calls.append(value)
            return {'e':{'maintenance':False,'revision':3}} if value['action']=='read' else {'revision':4}
        self.assertEqual(module.ensure_maintenance('e',routing),4)
        self.assertEqual(calls[-1],{'action':'pause','runtimes':['e'],'revision':3})

    def test_already_paused_route_does_not_get_new_revision(self):
        calls=[]
        def routing(value):calls.append(value);return {'e':{'maintenance':True,'revision':4}}
        self.assertEqual(module.ensure_maintenance('e',routing),4)
        self.assertEqual(len(calls),1)

    def test_failed_pause_is_not_reported_as_success(self):
        def routing(value):
            if value['action']=='read':return {'e':{'maintenance':False,'revision':3}}
            raise RuntimeError('stale or unavailable')
        with self.assertRaises(RuntimeError):module.ensure_maintenance('e',routing)
