"""The installer must be able to start the runtime it installs.

The owned runtime takes the operation lock itself and holds it for its
lifetime. An installer that kept that lock across the start would refuse its
own runtime on every host, which is what this module pins down.
"""
import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_server


def result(returncode=0,stdout='',stderr=''):
    return type('R',(),{'returncode':returncode,'stdout':stdout,'stderr':stderr})()


class InstallLockHandoffTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.state=Path(self.directory.name)/'upstream'
        self.state.mkdir(parents=True)
        self.private=Path(self.directory.name)/'private'
        self.probes=[]

    def install(self,install_result=None,extra=None):
        """Run install() with every external effect stubbed, watching the lock."""
        lock_path=self.state/'operation.lock'

        def fake_run(command,*,check=True,stdin=None,env=None,cwd=None):
            if 'installation_runtime.py' in ' '.join(command):
                try:
                    with lock_path.open('a') as handle:
                        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    self.probes.append('free')
                except BlockingIOError:
                    self.probes.append('held')
                return install_result or result()
            return result()

        with patch.object(install_server,'preflight',return_value=[]), \
             patch.object(install_server,'STATE',self.state), \
             patch.object(install_server,'PRIVATE',self.private), \
             patch.object(install_server,'pinned_images',return_value=[]), \
             patch.object(install_server,'npm_install'), \
             patch.object(install_server,'run',side_effect=fake_run), \
             patch.object(install_server.console_build_check,'verify',return_value=([],{})):
            install_server.install(extra)

    def test_the_operation_lock_is_free_when_the_runtime_starts(self):
        self.install()
        self.assertEqual(self.probes,['free'])

    def test_the_installer_stops_when_the_runtime_refuses_to_start(self):
        with self.assertRaises(SystemExit) as raised:
            self.install(install_result=result(1,'','Runtime startup failed: lock held'))
        self.assertIn('Runtime startup failed',str(raised.exception))

    def test_the_installer_releases_the_lock_even_when_a_step_fails(self):
        with patch.object(install_server,'preflight',return_value=[]), \
             patch.object(install_server,'STATE',self.state), \
             patch.object(install_server,'PRIVATE',self.private), \
             patch.object(install_server,'pinned_images',return_value=[]), \
             patch.object(install_server,'npm_install'), \
             patch.object(install_server,'run',side_effect=SystemExit('bun run build:ui failed')):
            with self.assertRaises(SystemExit):
                install_server.install(None)
        with (self.state/'operation.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)  # must not raise

    def test_a_second_installer_is_refused_while_one_holds_the_lock(self):
        with patch.object(install_server,'STATE',self.state):
            held=install_server.operation_lock()
            try:
                with self.assertRaises(SystemExit) as raised:
                    install_server.operation_lock()
                self.assertIn('Another installation operation',str(raised.exception))
            finally:
                held.close()


class PlanTests(unittest.TestCase):
    def test_the_plan_names_the_lock_handoff(self):
        source=(Path(__file__).resolve().parent/'install_server.py').read_text()
        self.assertIn('take the installation operation lock',source)
        self.assertIn('installation_runtime.py up',source)
        self.assertIn('Released before the owned runtime starts',source)


if __name__=='__main__':unittest.main()