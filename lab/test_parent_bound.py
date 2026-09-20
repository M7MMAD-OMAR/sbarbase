import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ParentBoundTests(unittest.TestCase):
    def test_wrong_parent_refuses_exec(self):
        result=subprocess.run(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()+1000000),'/usr/bin/python3','-c','print("should-not-run")'],cwd=ROOT,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0);self.assertNotIn('should-not-run',result.stdout)

    def test_parent_sigkill_delivers_termination_after_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            ready=Path(directory)/'ready';stopped=Path(directory)/'stopped'
            child="import signal,time,pathlib; signal.signal(signal.SIGTERM,lambda *_:(pathlib.Path("+repr(str(stopped))+").write_text('terminated'),exit(0))); pathlib.Path("+repr(str(ready))+").write_text('ready'); time.sleep(20)"
            parent="import os,subprocess,time; subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),'/usr/bin/python3','-c',"+repr(child)+"]); time.sleep(20)"
            process=subprocess.Popen(['/usr/bin/python3','-c',parent],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if ready.exists():break
                    time.sleep(.02)
                self.assertTrue(ready.exists());process.kill();process.wait(timeout=5)
                for _ in range(100):
                    if stopped.exists():break
                    time.sleep(.02)
                self.assertTrue(stopped.exists())
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
