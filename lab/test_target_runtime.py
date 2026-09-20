import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import durable_runtime as runtime
from target_runtime import TargetRuntime


class TargetLifecycleTests(unittest.TestCase):
    def test_source_start_refuses_running_target_before_allocating(self):
        source=runtime.Runtime.__new__(runtime.Runtime)
        with patch.object(runtime.lab,'docker',return_value=SimpleNamespace(stdout='active-target')) as docker:
            with self.assertRaisesRegex(RuntimeError,'stopped recovery'):source.start()
            self.assertEqual(docker.call_count,1)

    def test_target_start_refuses_running_source_before_publication(self):
        target=TargetRuntime.__new__(TargetRuntime)
        with patch.object(runtime.lab,'docker',return_value=SimpleNamespace(stdout='active-source')) as docker:
            with self.assertRaisesRegex(RuntimeError,'stopped source'):target.start()
            self.assertEqual(docker.call_count,1)

    def test_stop_attempts_all_containers_even_if_maintenance_unavailable(self):
        target=TargetRuntime.__new__(TargetRuntime);target.operation={};target.payload=None
        target.identities={kind:kind for kind in ('db','auth','rest','storage')}
        def pause():raise RuntimeError('catalog unavailable')
        target.pause=pause;phases=[];target.phase=phases.append;calls=[]
        def docker(*args):
            calls.append(args)
            return SimpleNamespace(stdout=json.dumps([{'State':{'Running':False}}]))
        with patch.object(runtime.lab,'docker',docker):
            with self.assertRaisesRegex(RuntimeError,'incomplete'):target.stop()
        self.assertEqual([c[1] for c in calls if c[0]=='stop'],['storage','rest','auth','db'])
        self.assertEqual(phases,['target-stop-failed'])
