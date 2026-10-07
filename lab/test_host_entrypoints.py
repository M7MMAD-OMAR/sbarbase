"""Admission failures must precede directory, lock and daemon effects."""
import ast
from contextlib import ExitStack
import json
import os
import runpy
import shlex
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dev
import docker_profile as profile
import durable_runtime
import install_server
import installation_runtime
import resource_admission
import resource_policy
import run as component
import supervised_run_check as supervised

ROOT = Path(__file__).resolve().parents[1]
REFUSAL = profile.ProfileError('architecture_unvalidated')


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.callback(os.chdir, os.getcwd())
        self.state = self.folder / 'state'
        self.private = self.folder / 'private'
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(profile, 'require_supported', side_effect=REFUSAL))

    def unchanged(self):
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_foreground_refuses_before_state_or_supervisor_locks(self):
        with patch.object(sys, 'argv', ['dev.py']), patch.object(dev, 'STATE', self.state), patch.object(dev, 'ROOT', self.folder):
            with self.assertRaises(profile.ProfileError):
                dev.main()
        self.unchanged()

    def test_component_up_refuses_before_state_secrets_or_resources(self):
        with patch.object(component, 'STATE', self.state), patch.object(component, 'PRIVATE', self.private), patch.object(component, 'docker') as docker:
            with self.assertRaises(profile.ProfileError): component.up()
            docker.assert_not_called()
        self.unchanged()

    def test_durable_constructor_refuses_before_state_and_credentials(self):
        with patch.object(durable_runtime, 'STATE', self.state), patch.object(durable_runtime, 'PRIVATE', self.private), patch.object(durable_runtime, 'atomic') as write:
            with self.assertRaises(profile.ProfileError): durable_runtime.Runtime()
            write.assert_not_called()
        self.unchanged()

    def test_installation_refuses_before_settlement_or_startup_lease(self):
        with patch.object(durable_runtime.effect_receipt, 'require_settled') as settle, patch.object(durable_runtime, 'Runtime') as runtime, patch.object(durable_runtime, 'STATE', self.state):
            with self.assertRaises(profile.ProfileError): installation_runtime.main('up')
            settle.assert_not_called(); runtime.assert_not_called()
        self.unchanged()

    def test_pulls_refuse_even_without_profile_environment(self):
        with patch.object(install_server, 'pinned_images') as pins, patch.object(install_server, 'docker') as docker, patch.object(install_server, 'pull_image') as pull:
            with self.assertRaises(SystemExit): install_server.ensure_images()
            pins.assert_not_called(); docker.assert_not_called(); pull.assert_not_called()
        self.unchanged()

    def test_install_refuses_before_operation_lock_or_dependency_install(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps({'OSType': 'linux'}))
        with patch.object(install_server, 'docker', return_value=result), patch.object(install_server, 'versions', return_value=[]), patch.object(install_server, 'capacity', return_value=[]), patch.object(install_server, 'operation_lock') as lock, patch.object(install_server, 'ensure_images') as pull, patch.object(install_server, 'npm_install') as dependencies:
            with self.assertRaisesRegex(SystemExit, 'nothing was installed'): install_server.install(None)
            lock.assert_not_called(); pull.assert_not_called(); dependencies.assert_not_called()
        self.unchanged()

    def test_unit_render_apply_refuses_before_creating_unit_or_service_changes(self):
        for apply in (False, True):
            with self.subTest(apply=apply), patch.object(install_server, 'ROOT', self.folder), patch.object(install_server, 'run') as run:
                with self.assertRaises(profile.ProfileError): install_server.supervise(apply=apply)
                run.assert_not_called()
            self.unchanged()

    def test_shipped_and_rendered_service_refuse_before_any_startup_effect(self):
        checkout=self.folder/'checkout'
        deploy=checkout/'deploy'
        deploy.mkdir(parents=True)
        shutil.copy2(ROOT/'deploy'/'host-preflight.sh',deploy/'host-preflight.sh')
        tools=checkout/'tools'
        tools.mkdir()
        docker=tools/'docker'
        docker.write_text('#!/bin/sh\nexit 97\n')
        docker.chmod(0o755)
        before={str(p.relative_to(self.folder)):p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        shipped=install_server.SERVICE_UNIT.read_text()
        rendered=install_server.rendered_unit(checkout,Path('/srv/service'),'service','/usr/local/bin')
        for text in (shipped,rendered):
            with self.subTest(unit='shipped' if text==shipped else 'rendered'):
                commands=[line.split('=',1)[1] for line in text.splitlines() if line.startswith('ExecStartPre=')]
                command=shlex.split(commands[0])
                self.assertEqual(command[0],'/bin/sh')
                self.assertTrue(command[1].endswith('/deploy/host-preflight.sh'))
                self.assertEqual(command[2:],['--runtime'])
                # The shipped default path is substituted only for this owned execution.
                command[1]=str(deploy/'host-preflight.sh')
                result=subprocess.run(command,cwd=checkout,env={'PATH':str(tools)+':/usr/bin:/bin','SBARBASE_DOCKER_DATA_ROOT':str(checkout/'missing-data')},capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('[configured_path_unavailable]',result.stderr)
                after={str(p.relative_to(self.folder)):p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
                self.assertEqual(after,before)
                self.assertFalse((checkout/'.lab').exists())

    def test_native_acceptance_refuses_before_dependencies_or_rehearsal(self):
        checkout=self.folder/'checkout'
        deploy=checkout/'deploy'
        deploy.mkdir(parents=True)
        for name in ('server-acceptance.sh','host-preflight.sh'):
            shutil.copy2(ROOT/'deploy'/name,deploy/name)
        tools=checkout/'tools'
        tools.mkdir()
        docker=tools/'docker'
        docker.write_text('#!/bin/sh\nexit 97\n')
        docker.chmod(0o755)
        before={str(p.relative_to(self.folder)):p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        result=subprocess.run(['/bin/bash',str(deploy/'server-acceptance.sh'),'--rehearse'],cwd=checkout,env={'PATH':str(tools)+':/usr/bin:/bin','SBARBASE_DOCKER_DATA_ROOT':str(checkout/'missing-data')},capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('[configured_path_unavailable]',result.stderr)
        self.assertNotIn('== prerequisites',result.stdout)
        self.assertFalse((checkout/'node_modules').exists())
        self.assertFalse((checkout/'.lab').exists())
        after={str(p.relative_to(self.folder)):p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        self.assertEqual(after,before)

    def test_supervised_mirror_refuses_before_unit_service_and_evidence_effects(self):
        with patch.object(sys,'argv',['supervised_run_check.py','--timeout','0']), patch.object(supervised,'USER_UNIT_DIR',self.folder/'units'), patch.object(supervised,'UNIT_PATH',self.folder/'units'/'fixture.service'), patch.object(supervised,'EVIDENCE',self.folder/'evidence.json'), patch.object(supervised,'systemctl') as service:
            with self.assertRaisesRegex(SystemExit,'architecture_unvalidated'):supervised.main()
            service.assert_not_called()
        self.unchanged()

    def test_resource_measurement_refuses_before_container_exec(self):
        with patch.object(resource_admission, 'docker') as docker:
            with self.assertRaises(profile.ProfileError): resource_admission.snapshot()
            docker.assert_not_called()

    def test_remote_endpoint_refuses_before_daemon_request(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'ssh://untrusted.example'}), patch.object(install_server, 'docker') as docker:
            findings = install_server.daemon()
            self.assertEqual(findings[0][0], 'blocker')
            docker.assert_not_called()

    def test_cli_refusal_leaves_owned_tree_unchanged_before_guard_or_diagnostics(self):
        scripts = self.folder / 'lab'
        scripts.mkdir()
        cases = (('dev.py', []), ('worker.py', ['--upstream']),
                 ('installation_runtime.py', ['up']), ('durable_runtime.py', ['up']), ('run.py', ['up']))
        for name, arguments in cases:
            shutil.copy2(ROOT / 'lab' / name, scripts / name)
        before = {str(p.relative_to(self.folder)): p.read_bytes() for p in scripts.iterdir()}
        for name, arguments in cases:
            with self.subTest(name=name), patch.object(sys, 'argv', [name, *arguments]), patch.object(profile, 'require_or_exit', side_effect=SystemExit('profile refusal')) as guard:
                with self.assertRaisesRegex(SystemExit, 'profile refusal'):
                    runpy.run_path(str(scripts / name), run_name='__main__')
                guard.assert_called_once_with()
            after = {str(p.relative_to(self.folder)): p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((self.folder / '.lab').exists())

    def test_cli_worker_and_runtime_guards_precede_mkdir_and_lock_creation(self):
        # Bind executable boundary tests above to the separate CLI startup ordering.
        for file in ('worker.py', 'installation_runtime.py', 'durable_runtime.py', 'run.py'):
            tree = ast.parse((ROOT / 'lab' / file).read_text())
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
            guards = [n.lineno for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr in ('require_supported', 'require_or_exit')]
            cli = [n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and ast.unparse(n.test).startswith('__name__')]
            block = cli[0] if cli else tree
            effects = [n.lineno for n in ast.walk(block) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('mkdir', 'open', 'acquire')]
            self.assertTrue(guards, file)
            self.assertLess(min(guards), min(effects), file)



class EntryPointFixtureCleanupTests(unittest.TestCase):
    def test_refused_cli_fixture_restores_cwd_before_removing_owned_directory(self):
        saved_cwd = os.getcwd()
        case = EntryPointTests('test_cli_refusal_leaves_owned_tree_unchanged_before_guard_or_diagnostics')
        result = unittest.TestResult()
        try:
            case.run(result)
            self.assertEqual(result.testsRun, 1)
            self.assertTrue(result.wasSuccessful(), (result.errors, result.failures))
            self.assertEqual(result.skipped, [])
            self.assertFalse(case.folder.exists())
            self.assertEqual(os.getcwd(), saved_cwd)
        finally:
            # This regression owns the nested copied-script invocation, including its baseline failure.
            os.chdir(saved_cwd)

class CapabilityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.selected = profile.DockerProfile('/var/lib/docker', '/var/run/docker.sock', True, 'fixture')

    def result(self, code=0, stdout='local-v1 preflight passed; supported-profile acceptance unproven; production unproven\n', stderr=''):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def test_same_shell_contract_is_used_by_runtime_with_bounded_probe(self):
        with patch.object(profile.subprocess, 'run', return_value=self.result()) as probe:
            profile.validate_capabilities(self.selected)
            command, = probe.call_args.args
            self.assertEqual(command[0], '/bin/sh')
            self.assertEqual(command[-1], '--runtime')
            self.assertEqual(probe.call_args.kwargs['timeout'], 45)
            self.assertEqual(probe.call_args.kwargs['env']['DOCKER_HOST'], profile.CONTROLLER_ENDPOINT)

    def test_safe_reason_and_action_survive_bridge(self):
        diagnostic = 'Docker profile refused [filesystem_unvalidated]: Unsupported filesystem. Action: use the candidate filesystem.'
        with patch.object(profile.subprocess, 'run', return_value=self.result(1, '', diagnostic)):
            with self.assertRaises(profile.ProfileError) as error: profile.validate_capabilities(self.selected)
        self.assertEqual(error.exception.reason, 'filesystem_unvalidated')
        self.assertEqual(str(error.exception), diagnostic)

    def test_failed_missing_timeout_and_malformed_probes_fail_closed(self):
        variants = [self.result(2, '', '/bin/sh: missing script'), self.result(0, 'pass'), self.result(1, '', 'secret raw stderr')]
        for result in variants:
            with self.subTest(result=result), patch.object(profile.subprocess, 'run', return_value=result):
                with self.assertRaises(profile.ProfileError) as error: profile.validate_capabilities(self.selected)
                self.assertNotIn('secret', str(error.exception))
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('probe', 45)):
            with self.subTest(error=error), patch.object(profile.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(profile.ProfileError, 'host_capability_probe_unavailable'): profile.validate_capabilities(self.selected)

    def test_unconfigured_remote_and_context_cannot_skip_policy(self):
        for env in ({'DOCKER_HOST': 'tcp://127.0.0.1:2375'}, {'DOCKER_CONTEXT': 'remote'}):
            with self.subTest(env=env), self.assertRaises(profile.ProfileError): profile.from_environment(env)

    def test_success_pins_default_endpoint_without_home_or_saved_context(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(profile, 'validate', return_value='fixture-daemon'):
            self.assertEqual(profile.require_supported(), 'fixture-daemon')
            self.assertEqual(os.environ['DOCKER_HOST'], 'unix:///var/run/docker.sock')
            self.assertEqual(profile.docker_command('info'), ['docker', '--host', 'unix:///var/run/docker.sock', 'info'])

    def test_mirrored_unit_preserves_declared_profile_socket_and_data_root(self):
        declarations={'SBARBASE_DOCKER_PROFILE':'local-v1','SBARBASE_DOCKER_SOCKET':'/run/custom.sock','SBARBASE_DOCKER_DATA_ROOT':'/srv/custom docker%root','DOCKER_HOST':'unix:///run/custom.sock'}
        with patch.dict(os.environ,declarations,clear=True):
            self.assertEqual(supervised.docker_environment(),declarations)
            text=supervised.unit_text()
            self.assertIn('Environment="SBARBASE_DOCKER_SOCKET=/run/custom.sock"',text)
            self.assertIn('Environment="SBARBASE_DOCKER_DATA_ROOT=/srv/custom docker%%root"',text)
            forwarded=profile.from_environment(supervised.docker_environment())
            self.assertEqual(forwarded.socket,'/run/custom.sock')
            self.assertEqual(forwarded.data_root,'/srv/custom docker%root')
        self.assertEqual(supervised.unit_environment('SBARBASE_DOCKER_DATA_ROOT','/srv/a"b'), 'Environment="SBARBASE_DOCKER_DATA_ROOT=/srv/a\\"b"\n')
        for env in ({'DOCKER_CONTEXT':'chosen'}, {'DOCKER_HOST':'ssh://refused.example'}, {'SBARBASE_DOCKER_DATA_ROOT':'/srv/bad\nroot'}):
            with self.subTest(env=env),patch.dict(os.environ,env,clear=True),self.assertRaises(profile.ProfileError):supervised.unit_text()
        for value in ('/srv/bad\nroot','/srv/bad\troot','/srv/bad\x7froot'):
            with self.subTest(value=repr(value)),self.assertRaises(profile.ProfileError):supervised.unit_environment('SBARBASE_DOCKER_DATA_ROOT',value)

    def test_no_environment_pin_is_written_after_refusal(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(profile, 'validate', side_effect=REFUSAL):
            with self.assertRaises(profile.ProfileError): profile.require_supported()
            self.assertNotIn('DOCKER_HOST', os.environ)

    def test_block_device_resolution_cannot_use_legacy_profile_bypass(self):
        resource_policy._device.clear()
        with patch.dict(os.environ, {}, clear=True), patch.object(profile, 'validated_identity', side_effect=REFUSAL) as admission, patch.object(resource_policy, 'io_device') as discover:
            with self.assertRaises(resource_policy.ResourcePolicyError): resource_policy.device()
            admission.assert_called_once(); discover.assert_not_called()

    def test_shell_is_baked_and_whitelisted_for_rollback_admission(self):
        self.assertIn('COPY deploy/host-preflight.sh /usr/local/lib/sbarbase/host-preflight.sh', (ROOT / 'Dockerfile').read_text())
        self.assertIn('!deploy/host-preflight.sh', (ROOT / '.dockerignore').read_text())
        with patch.object(profile, '__file__', '/usr/local/lib/sbarbase/docker_profile.py'):
            self.assertEqual(profile.capability_script(), Path('/usr/local/lib/sbarbase/host-preflight.sh'))


if __name__ == '__main__': unittest.main()
