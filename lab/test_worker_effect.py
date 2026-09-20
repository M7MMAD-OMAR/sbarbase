"""Actual Bun child inheritance, with harmless effects and temporary OS locks."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class WorkerEffectTests(unittest.TestCase):
    def test_startup_failures_release_lock_without_effects(self):
        for mismatch in (False,True):
            with self.subTest(mismatch=mismatch),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);path=base/'worker.lock';other=base/'other';other.touch()
                with path.open('a') as held:
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    command=['/no-such-sbarbase-command'] if not mismatch else ['/usr/bin/python3','-c',"raise SystemExit(0)"]
                    script="import {spawnWorkerEffect} from './lab/worker-effect'; const c=spawnWorkerEffect("+json.dumps(command)+",Number(process.env.TEST_WORKER_FD),"+json.dumps(str(other if mismatch else path))+",{environment:'fixture',runtime:'e_fixture',claim:'claim',attempt:1}); process.exitCode=await c.exited;"
                    worker=subprocess.Popen(['bun','-e',script],cwd=ROOT,pass_fds=(held.fileno(),),env=dict(os.environ,TEST_WORKER_FD=str(held.fileno())))
                    held.close()
                    self.assertNotEqual(worker.wait(timeout=5),0)
                    with path.open('a') as contender:
                        fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_failed_or_killed_effect_keeps_pending_receipt_and_refuses_replay(self):
        for code in ("raise SystemExit(1)","import signal;os.kill(os.getpid(),signal.SIGKILL)"):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);lock=base/'worker.lock';marker=base/'mutated'
                effect="import os,pathlib; pathlib.Path("+repr(str(marker))+").write_text('partial'); "+code
                with lock.open('a') as held:
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    command=['/usr/bin/python3','-c',effect]
                    script="import {spawnWorkerEffect} from './lab/worker-effect'; const c=spawnWorkerEffect("+json.dumps(command)+",Number(process.env.TEST_WORKER_FD),"+json.dumps(str(lock))+",{environment:'fixture',runtime:'e_fixture',claim:'claim',attempt:1}); process.exitCode=await c.exited;"
                    options=dict(cwd=ROOT,pass_fds=(held.fileno(),),env=dict(os.environ,TEST_WORKER_FD=str(held.fileno())))
                    self.assertNotEqual(subprocess.run(['bun','-e',script],timeout=5,**options).returncode,0)
                    receipt=base/'worker-effect.json';before=receipt.read_bytes()
                    self.assertTrue(marker.exists());self.assertEqual(json.loads(before)['phase'],'pending')
                    marker.unlink()
                    self.assertNotEqual(subprocess.run(['bun','-e',script],timeout=5,**options).returncode,0)
                    self.assertFalse(marker.exists());self.assertEqual(receipt.read_bytes(),before)

    def test_effect_retains_lock_after_worker_sigkill(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);ready=base/'ready';release=base/'release';done=base/'done'
            effect="import pathlib,time; p=pathlib.Path("+repr(directory)+"); (p/'ready').touch(); deadline=time.monotonic()+10\nwhile not (p/'release').exists() and time.monotonic()<deadline: time.sleep(.02)\n(p/'done').touch()"
            with (base/'worker.lock').open('a') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                command=['/usr/bin/python3','-c',effect]
                script="import {spawnWorkerEffect} from './lab/worker-effect'; const c=spawnWorkerEffect("+json.dumps(command)+",Number(process.env.TEST_WORKER_FD),"+json.dumps(str(base/'worker.lock'))+",{environment:'fixture',runtime:'e_fixture',claim:'claim',attempt:1}); await c.exited;"
                worker=subprocess.Popen(['bun','-e',script],cwd=ROOT,pass_fds=(held.fileno(),),env=dict(os.environ,TEST_WORKER_FD=str(held.fileno())))
                try:
                    deadline=time.monotonic()+5
                    while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(ready.exists())
                    held.close();worker.kill();worker.wait(timeout=5)
                    with (base/'worker.lock').open('a') as contender:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:
                    release.touch()
                    if worker.poll() is None:worker.wait(timeout=5)
                    deadline=time.monotonic()+5
                    while not done.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(done.exists())
                deadline=time.monotonic()+5
                with (base/'worker.lock').open('a') as contender:
                    while True:
                        try:fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                        except BlockingIOError:
                            if time.monotonic()>=deadline:raise
                            time.sleep(.02)
