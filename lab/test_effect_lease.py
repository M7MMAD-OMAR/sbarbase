"""A new open description must not bypass an inherited lease."""
import os
from pathlib import Path
import tempfile
import unittest
import effect_lease


class EffectLeaseTests(unittest.TestCase):
    def test_new_owner_refused_until_last_inherited_descriptor_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            owner=effect_lease.acquire(state,timeout=0)
            inherited=os.dup(owner)
            os.close(owner)
            try:
                with self.assertRaises(RuntimeError):effect_lease.acquire(state,timeout=0)
            finally:os.close(inherited)
            replacement=effect_lease.acquire(state,timeout=0)
            self.assertTrue(os.get_inheritable(replacement));os.close(replacement)

    def test_export_avoids_bun_descriptor_swap_aliasing(self):
        import subprocess,json
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            with (state/'effect.lock').open('a') as effect,(state/'worker.lock').open('a') as worker:
                first,second=effect_lease.export_descriptors(worker.fileno(),effect.fileno())
                try:
                    self.assertGreaterEqual(first,10);self.assertGreaterEqual(second,10)
                    code='import os;assert os.fstat(3).st_ino==os.stat('+repr(str(state/'worker.lock'))+').st_ino;assert os.fstat(4).st_ino==os.stat('+repr(str(state/'effect.lock'))+').st_ino'
                    script='const c=Bun.spawn('+json.dumps(['/usr/bin/python3','-c',code])+',{stdio:["ignore","ignore","ignore",'+str(first)+','+str(second)+']});process.exitCode=await c.exited;'
                    result=subprocess.run(['bun','-e',script],pass_fds=(first,second),timeout=5)
                    self.assertEqual(result.returncode,0)
                finally:os.close(first);os.close(second)
