"""Source-only network admission and exact owned cleanup regressions."""
import importlib.util
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('management_mfa_fixture', Path(__file__).with_name('management-mfa-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

NAME = 'sb06-mfa-source-test-net'
OWNER = 'sb06-mfa-source-test'
CID = 'c' * 64


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'admission'
        self.created, self.state, self.report, self.commands = [], {}, {}, []
        self.receipt = {'version': 1, 'fixture': 'identity-' + NAME,
                        'subnet': fixture.fixture_subnet.select_subnet('identity-' + NAME, []),
                        'observed_monotonic': time.monotonic(),
                        'routes_sha256': 'a' * 64, 'networks_sha256': 'b' * 64}
        self.record = {'Id': CID, 'Name': NAME, 'Driver': 'bridge', 'Scope': 'local',
                       'Internal': True, 'Labels': {'io.sbarbase.owner': OWNER}, 'Containers': {},
                       'IPAM': {'Driver': 'default', 'Config': [{'Subnet': self.receipt['subnet']}]}}

    def docker(self, *args, data=None):
        self.commands.append(args)
        if args[:2] == ('network', 'ls'):
            return ''
        if args[:2] == ('network', 'create'):
            return CID + '\n'
        if args[:2] == ('network', 'inspect'):
            return json.dumps([self.record])
        if args == ('network', 'rm', CID):
            return CID + '\n'
        self.fail('Unexpected Docker command: ' + repr(args))

    def create(self, receipt=None):
        with patch.object(fixture.fixture_subnet, 'observe', return_value=self.receipt if receipt is None else receipt), \
                patch.object(fixture, 'docker', side_effect=self.docker):
            fixture.create_network(NAME, OWNER, self.output, self.created, self.state, self.report)

    def clean(self, record=None, result=None):
        if result is None:
            result = subprocess.CompletedProcess([], 0, json.dumps([self.record if record is None else record]), '')
        with patch.object(fixture.subprocess, 'run', return_value=result) as inspect, \
                patch.object(fixture, 'docker', side_effect=self.docker):
            fixture.cleanup_network(NAME, OWNER, self.state)
        return inspect

    def test_safe_success_binds_subnet_exact_identity_and_evidence(self):
        self.create()
        self.assertEqual(self.created, [('network', NAME)])
        self.assertEqual(self.state, {'id': CID})
        command = next(c for c in self.commands if c[:2] == ('network', 'create'))
        self.assertEqual(command, ('network', 'create', '--driver', 'bridge', '--internal', '--subnet',
                                   self.receipt['subnet'], '--label', 'io.sbarbase.owner=' + OWNER, NAME))
        self.assertEqual(json.loads((self.output / 'network-inspect.json').read_text()), [self.record])
        inspect = self.clean()
        self.assertEqual(inspect.call_args.args[0], ['docker', 'network', 'inspect', CID])
        self.assertEqual(inspect.call_args.kwargs['timeout'], 15)
        self.assertEqual(self.commands[-1], ('network', 'rm', CID))

    def test_overlap_uses_real_bounded_observer_nonoverlapping_choice(self):
        subnet = fixture.fixture_subnet
        original_observe = subnet.observe
        overlap = self.receipt['subnet']
        existing_id = 'a' * 64
        captured = []

        def capture(argv, timeout, stdout_limit, stderr_limit):
            captured.append((argv, timeout, stdout_limit, stderr_limit))
            if argv[1:3] == ['network', 'ls']:
                raw = (existing_id + '\n').encode()
            elif argv[1:3] == ['network', 'inspect']:
                raw = json.dumps({'id': existing_id, 'driver': 'bridge',
                                  'ipam': {'Driver': 'default', 'Config': [{'Subnet': overlap}]}}).encode()
            else:
                raw = b'[{"dst":"127.0.0.0/8","table":"local"}]'
            return {'stdout': raw, 'stderr': b'', 'exit_code': 0, 'refusal': None}

        def observe(identity, directory):
            result = original_observe(identity, directory, capture=capture)
            self.record['IPAM']['Config'][0]['Subnet'] = result['subnet']
            return result

        with patch.object(subnet, 'observe', side_effect=observe), patch.object(fixture, 'docker', side_effect=self.docker):
            fixture.create_network(NAME, OWNER, self.output, self.created, self.state, self.report)
        self.assertFalse(ipaddress.ip_network(overlap).overlaps(ipaddress.ip_network(self.report['network_admission']['subnet'])))
        self.assertEqual(len(captured), 5)
        self.assertTrue(all(0 < entry[1] <= 5 and entry[2:] == (1024 * 1024, 16 * 1024) for entry in captured))
        self.assertTrue((self.output / 'subnet-observations.json').is_file())

    def test_stale_receipt_refuses_before_create_or_cleanup_intent(self):
        self.receipt['observed_monotonic'] = time.monotonic() - 61
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.create()
        self.assertFalse(any(c[:2] == ('network', 'create') for c in self.commands))
        self.assertEqual(self.created, [])

    def test_malformed_and_wrong_fixture_receipts_refuse_before_create(self):
        for update in ({'routes_sha256': 'invalid'}, {'fixture': 'identity-other'}, {'subnet': '192.0.2.0/28'}):
            with self.subTest(update=update):
                self.output = Path(self.directory.name) / ('admission-' + str(len(self.commands)))
                receipt = {**self.receipt, **update}
                with self.assertRaises(ValueError):
                    self.create(receipt)
                self.assertFalse(any(c[:2] == ('network', 'create') for c in self.commands))
                self.assertEqual(self.created, [])

    def test_metadata_subnet_mismatch_retains_owned_cleanup(self):
        self.record['IPAM']['Config'][0]['Subnet'] = '10.240.255.240/28' if self.receipt['subnet'] != '10.240.255.240/28' else '10.240.0.0/28'
        with self.assertRaisesRegex(RuntimeError, 'subnet differs'):
            self.create()
        self.assertEqual(self.created, [('network', NAME)])
        self.clean()
        self.assertEqual(self.commands[-1], ('network', 'rm', CID))

    def test_owner_id_name_and_consumers_mismatches_never_remove(self):
        for field, value in [('Labels', {'io.sbarbase.owner': 'neighbor'}), ('Id', 'd' * 64),
                             ('Name', 'neighbor'), ('Containers', {'neighbor': {}}), ('Internal', False)]:
            with self.subTest(field=field):
                self.state = {'id': CID}
                record = {**self.record, field: value}
                with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                    self.clean(record)
                self.assertFalse(any(c[:2] == ('network', 'rm') for c in self.commands))

    def test_inspected_neighbor_refuses_admission_and_cleanup(self):
        self.record['Labels']['io.sbarbase.owner'] = 'neighbor'
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            self.create()
        self.assertNotIn('network', self.report)
        self.assertEqual(self.created, [('network', NAME)])
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            self.clean()
        self.assertFalse(any(c[:2] == ('network', 'rm') for c in self.commands))

    def test_lost_create_response_retains_cleanup_and_renews_identity(self):
        def docker(*args, data=None):
            if args[:2] == ('network', 'create'):
                raise subprocess.TimeoutExpired(['docker', *args], 30)
            return self.docker(*args, data=data)
        with patch.object(fixture.fixture_subnet, 'observe', return_value=self.receipt), \
                patch.object(fixture, 'docker', side_effect=docker), self.assertRaises(subprocess.TimeoutExpired):
            fixture.create_network(NAME, OWNER, self.output, self.created, self.state, self.report)
        self.assertEqual(self.created, [('network', NAME)])
        self.assertEqual(self.state, {})
        inspect = self.clean()
        self.assertEqual(inspect.call_args.args[0], ['docker', 'network', 'inspect', NAME])
        self.assertEqual(self.commands[-1], ('network', 'rm', CID))

    def test_cleanup_recognized_absence_only_unknown_error_refuses(self):
        result = subprocess.CompletedProcess([], 1, '[]\n', 'Error response from daemon: network ' + NAME + ' not found\n')
        self.clean(result=result)
        for result in [subprocess.CompletedProcess([], 1, '[]\n', 'permission denied\n'),
                       subprocess.CompletedProcess([], 1, '', 'Error response from daemon: network ' + NAME + ' not found\n')]:
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'inspection refused'):
                self.clean(result=result)
        self.assertEqual(self.commands, [])

    def test_bounded_observation_failure_prevents_all_docker_mutation(self):
        subnet = fixture.fixture_subnet
        original_observe = subnet.observe
        def capture(argv, timeout, stdout_limit, stderr_limit):
            self.assertLessEqual(timeout, 5)
            return {'stdout': b'original bytes', 'stderr': b'observation refused', 'exit_code': 1, 'refusal': None}
        with patch.object(subnet, 'observe', side_effect=lambda identity, directory: original_observe(identity, directory, capture=capture)), \
                patch.object(fixture, 'docker', side_effect=self.docker), self.assertRaises(ValueError):
            fixture.create_network(NAME, OWNER, self.output, self.created, self.state, self.report)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.created, [])
        self.assertEqual((self.output / 'subnet-observation-01-stdout.bin').read_bytes(), b'original bytes')
        self.assertTrue((self.output / 'subnet-observations.json').is_file())

    def test_existing_name_refuses_before_network_creation(self):
        with patch.object(fixture.fixture_subnet, 'observe', return_value=self.receipt), \
                patch.object(fixture, 'docker', return_value=CID + '\n'), self.assertRaisesRegex(RuntimeError, 'already exists'):
            fixture.create_network(NAME, OWNER, self.output, self.created, self.state, self.report)
        self.assertEqual(self.created, [])


class BrowserPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.module = self.root / 'lab/mfa-browser/node_modules/playwright/index.mjs'
        self.ui = self.root / '.lab/ui/index.html'
        for path in (self.module, self.ui):
            path.parent.mkdir(parents=True)
            path.write_text('diagnostic input')
        self.browser = self.root / 'chromium'
        self.browser.write_text('#!/bin/sh\nexit 0\n')
        self.browser.chmod(0o700)

    def preflight(self):
        with patch.object(fixture, 'ROOT', self.root), \
                patch.dict(fixture.os.environ, {}, clear=True), \
                patch.object(fixture.shutil, 'which', return_value=str(self.browser)):
            return fixture.browser_preflight('mfa')

    def test_prepared_inputs_return_resolved_executable(self):
        self.assertEqual(self.preflight(), str(self.browser.resolve()))

    def test_missing_public_package_refuses_before_docker(self):
        self.module.unlink()
        with patch.object(fixture, 'ROOT', self.root), \
                patch.object(fixture.sys, 'argv', ['fixture', '--output', str(self.root / 'report.json')]), \
                patch.dict(fixture.os.environ, {}, clear=True), \
                patch.object(fixture.shutil, 'which', return_value=str(self.browser)), \
                patch.object(fixture, 'docker') as docker, \
                self.assertRaisesRegex(RuntimeError, 'public Playwright package'):
            fixture.main()
        docker.assert_not_called()

    def test_missing_ui_refuses_before_docker(self):
        self.ui.unlink()
        with patch.object(fixture, 'ROOT', self.root), \
                patch.object(fixture.sys, 'argv', ['fixture', '--output', str(self.root / 'report.json')]), \
                patch.dict(fixture.os.environ, {}, clear=True), \
                patch.object(fixture.shutil, 'which', return_value=str(self.browser)), \
                patch.object(fixture, 'docker') as docker, \
                self.assertRaisesRegex(RuntimeError, 'built UI index'):
            fixture.main()
        docker.assert_not_called()

    def test_configured_invalid_browser_is_not_silently_replaced(self):
        with patch.object(fixture, 'ROOT', self.root), \
                patch.dict(fixture.os.environ, {'CHROMIUM_BIN': str(self.root / 'missing')}, clear=True), \
                patch.object(fixture.shutil, 'which') as which, \
                self.assertRaisesRegex(RuntimeError, 'executable Chromium'):
            fixture.browser_preflight('mfa')
        which.assert_not_called()

    def test_nonexecutable_browser_refuses(self):
        self.browser.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'executable Chromium'):
            self.preflight()

    def test_cli_contract_does_not_require_browser_inputs(self):
        self.module.unlink()
        self.ui.unlink()
        with patch.object(fixture, 'ROOT', self.root), \
                patch.object(fixture.shutil, 'which', side_effect=AssertionError('Browser lookup forbidden')):
            self.assertIsNone(fixture.browser_preflight('cli'))


if __name__ == '__main__':
    unittest.main()
