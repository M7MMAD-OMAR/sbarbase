"""The mirrored user unit must stay faithful to the shipped system unit."""
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import supervised_run_check as supervised

ROOT=Path(__file__).resolve().parent.parent


def directives(text):
    found={}
    for line in text.splitlines():
        match=re.match(r'(ExecStartPre|ExecStart|WorkingDirectory|Environment|Restart|TimeoutStopSec)=(.+)',line.strip())
        if match:found.setdefault(match.group(1),[]).append(match.group(2))
    return found


class MirrorFidelityTests(unittest.TestCase):
    def test_the_mirror_uses_the_same_python_and_entry_points(self):
        shipped=directives((ROOT/'deploy'/'sbarbase.service').read_text())
        mirror=directives(supervised.unit_text())
        # The shipped unit runs from /opt/sbarbase on a server; the mirror runs from
        # this checkout, so compare the interpreter and the script path inside it.
        def entry(value):
            parts=value.split()
            script='/'.join(parts[1].split('/')[-2:]) if len(parts)>1 else ''
            return parts[0],script,' '.join(parts[2:])
        for key in ('ExecStartPre','ExecStart'):
            self.assertIn(key,shipped,key)
            self.assertIn(key,mirror,key)
            self.assertEqual([entry(item) for item in shipped[key]],
                             [entry(item) for item in mirror[key]],
                             key+' differs from the shipped unit')

    def test_the_mirror_keeps_the_preflight_gate_and_the_working_directory(self):
        mirror=directives(supervised.unit_text())
        self.assertIn('upgrade_guard.py',mirror['ExecStartPre'][0])
        self.assertIn('lab/leftover_runtime.py',mirror['ExecStartPre'][1])
        self.assertTrue(mirror['ExecStartPre'][2].endswith('lab/install_server.py check'))
        self.assertEqual(mirror['WorkingDirectory'][0],str(ROOT))
        self.assertIn('PATH=',mirror['Environment'][1])
        self.assertEqual(mirror['Restart'][0],'no')

    def test_the_shipped_unit_still_gates_on_the_preflight(self):
        shipped=directives((ROOT/'deploy'/'sbarbase.service').read_text())
        self.assertTrue(shipped['ExecStartPre'][-1].endswith('lab/install_server.py check'))
        self.assertTrue(shipped['ExecStart'][0].endswith('lab/dev.py'))

    def test_a_leftover_runtime_is_stopped_after_the_guard_and_before_the_preflight(self):
        """A killed supervisor leaves the owned containers running; the preflight refuses on them,
        so the step that stops them comes between the guard and the preflight, and a checkout the
        guard moved back to a version without the file skips it instead of failing the start."""
        shipped=directives((ROOT/'deploy'/'sbarbase.service').read_text())['ExecStartPre']
        self.assertEqual(len(shipped),3)
        self.assertIn('upgrade_guard.py',shipped[0])
        self.assertEqual(shipped[1],"/bin/sh -c 'if [ -f lab/leftover_runtime.py ]; then exec /usr/bin/python3 lab/leftover_runtime.py; fi'")
        self.assertTrue(shipped[2].endswith('lab/install_server.py check'))

    def test_the_upgrade_guard_runs_first_and_the_unit_never_stops_restarting(self):
        text=(ROOT/'deploy'/'sbarbase.service').read_text()
        shipped=directives(text)
        # Before any code of the checked out version, the preflight included: a preflight that
        # fails after an upgrade is one of the starts the guard counts.
        self.assertIn(".lab/upgrades/guard.py",shipped['ExecStartPre'][0])
        self.assertIn("lab/upgrade_guard.py'",shipped['ExecStartPre'][0])
        self.assertIn('SBARBASE_GUARDED=1',shipped['Environment'])
        unit=text.split('[Service]')[0]
        self.assertIn('StartLimitIntervalSec=0',unit)
        self.assertRegex(text,r'\nTimeoutStartSec=[1-9]\d{2,}\n')

    def test_the_scope_states_what_this_does_not_prove(self):
        source=(ROOT/'lab'/'supervised_run_check.py').read_text()
        self.assertIn('Not the shipped system unit',source)
        self.assertIn('not an empty-host install',source)

    def test_the_temporary_unit_is_always_removed(self):
        source=(ROOT/'lab'/'supervised_run_check.py').read_text()
        finally_block=source.split('finally:')[1]
        self.assertIn('UNIT_PATH.unlink(missing_ok=True)',finally_block)
        self.assertIn("record('temporary unit file removed'",finally_block)


class ServiceEnvironmentTests(unittest.TestCase):
    """A service does not inherit the caller's shell, so what it needs must be stated."""

    def test_only_present_docker_settings_are_forwarded(self):
        with patch.dict(os.environ,{'DOCKER_HOST':'unix:///var/run/docker.sock'},clear=False):
            os.environ.pop('DOCKER_CONTEXT',None)
            forwarded=supervised.docker_environment()
        self.assertEqual(forwarded,{'DOCKER_HOST':'unix:///var/run/docker.sock'})
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(supervised.docker_environment(),{})

    def test_the_mirror_unit_carries_the_docker_host_when_the_shell_has_one(self):
        with patch.dict(os.environ,{'DOCKER_HOST':'unix:///var/run/docker.sock'},clear=False):
            text=supervised.unit_text()
        self.assertIn('Environment=DOCKER_HOST=unix:///var/run/docker.sock',text)
        with patch.dict(os.environ,{},clear=True):
            self.assertNotIn('Environment=DOCKER_HOST=',supervised.unit_text())

    def test_an_unreachable_daemon_names_what_was_tried(self):
        import install_server
        with patch.dict(os.environ,{'DOCKER_HOST':'unix:///var/run/docker.sock'},clear=False), \
             patch.object(install_server,'docker') as docker:
            docker.return_value=type('R',(),{'returncode':1,'stdout':'','stderr':''})()
            findings=install_server.daemon()
        self.assertEqual(findings[0][0],'blocker')
        self.assertIn('unix:///var/run/docker.sock',findings[0][1])
        self.assertIn('a system service must reach the socket',findings[0][1])

    def test_without_a_docker_host_the_context_endpoint_is_named(self):
        import install_server
        with patch.dict(os.environ,{},clear=True), \
             patch.object(install_server,'docker') as docker, \
             patch.object(install_server,'resolved_endpoint',return_value='unix:///home/x/.docker/desktop/docker.sock'):
            docker.return_value=type('R',(),{'returncode':1,'stdout':'','stderr':''})()
            findings=install_server.daemon()
        self.assertIn('desktop/docker.sock',findings[0][1])


if __name__=='__main__':unittest.main()