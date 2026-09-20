"""Race injection around guardian child creation, with no external effects."""
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch
import worker_lock_exec as guard


class EffectGuardTests(unittest.TestCase):
    def test_stop_during_spawn_still_cleans_returned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'worker.lock';lock.touch()
            lease=Path(directory)/'effect.lock';lease.touch()
            handlers={};child=object()
            def register(sig,callback):handlers[sig]=callback
            def spawn(*args,**kwargs):
                handlers[signal.SIGTERM]()
                return child
            identity={'environment':'fixture','runtime':'e_fixture','claim':'claim','attempt':1}
            with patch.object(guard.sys,'argv',['guard',str(lock),json.dumps(identity),'10','unused']),\
                 patch.object(guard.signal,'signal',register),patch.object(guard.os,'setsid'),\
                 patch.object(guard.os,'fstat',side_effect=lambda fd:lock.stat() if fd==3 else lease.stat()),patch.object(guard.fcntl,'flock'),\
                 patch.object(guard.subprocess,'Popen',spawn),patch.object(guard,'child_status',return_value=None),\
                 patch.object(guard,'terminate_group') as cleanup,patch.object(guard,'complete') as complete:
                self.assertEqual(guard.main(),1)
                cleanup.assert_called_once_with(child,grace=.5)
                complete.assert_not_called()
            self.assertEqual(json.loads((lock.parent/'worker-effect.json').read_text())['phase'],'pending')
