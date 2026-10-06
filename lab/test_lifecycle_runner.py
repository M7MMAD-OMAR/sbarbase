"""Runner uncertainty checks using temporary evidence and a synthetic Docker engine.

No container, image, socket or retained data is accessed. The real runner main
function consumes mocked command results and writes only the test evidence tree.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5


CHECKOUT = Path(__file__).resolve().parents[1]
RUNNER_PATH = CHECKOUT / 'deploy/verify/lifecycle_checks.py'
SPEC = importlib.util.spec_from_file_location('synthetic_lifecycle_runner_checks', RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class SyntheticEngine:
    """Immutable identities derived from actual create arguments and planned names."""
    def __init__(self, output, mode='lost', ledger='readable', mismatch=None, foreign=None):
        self.output, self.mode, self.ledger = output, mode, ledger
        self.mismatch, self.foreign = mismatch, foreign
        self.commands, self.events, self.owners = [], [], {}
        self.protocol_errors = []
        self.image_id = 'sha256:' + hashlib.sha256(b'synthetic runner image').hexdigest()
        self.image_present = False
        self.helpers, self.volumes = {}, {}
        self.socket_owner = None
        self.packet = None

    @staticmethod
    def reply(code=0, value='', error=''):
        return subprocess.CompletedProcess([], code, value, error)

    @staticmethod
    def option(arguments, name):
        return arguments[arguments.index(name) + 1]

    @staticmethod
    def labels(arguments):
        return dict(arguments[index + 1].split('=', 1) for index, item in enumerate(arguments) if item == '--label')

    def planned(self, arguments):
        snapshot = json.loads((self.output / 'result.json').read_text())
        entry = snapshot['checks'][-1]
        if entry['allocation_name'] != self.option(arguments, '--name') or entry['execution_requested']:
            raise AssertionError('Create was not preceded by the exact pending allocation journal')
        return snapshot, entry

    def create(self, arguments):
        snapshot, entry = self.planned(arguments)
        name = self.option(arguments, '--name')
        cid = hashlib.sha256(name.encode()).hexdigest()
        image_index = arguments.index(self.image_id)
        mounts = []
        if '--mount' in arguments:
            fields = dict(part.split('=', 1) for part in self.option(arguments, '--mount').split(','))
            mounts.append({'Type': fields['type'], 'Source': fields['src'], 'Destination': fields['dst']})
        item = {'Id': cid, 'Name': '/' + name, 'Image': self.image_id,
                'Config': {'Labels': self.labels(arguments), 'Entrypoint': [self.option(arguments, '--entrypoint')],
                           'Cmd': arguments[image_index + 1:]},
                'HostConfig': {'Init': '--init' in arguments, 'NetworkMode': self.option(arguments, '--network'),
                               'Memory': int(self.option(arguments, '--memory')[:-1]) * 1024 * 1024,
                               'MemorySwap': int(self.option(arguments, '--memory-swap')[:-1]) * 1024 * 1024,
                               'NanoCpus': int(float(self.option(arguments, '--cpus')) * 1_000_000_000),
                               'PidsLimit': int(self.option(arguments, '--pids-limit')),
                               'Privileged': '--privileged' in arguments, 'PortBindings': {},
                               'CapDrop': [self.option(arguments, '--cap-drop')],
                               'SecurityOpt': [self.option(arguments, '--security-opt')]},
                'Mounts': mounts, 'State': {'Running': False, 'ExitCode': 0, 'StartedAt': '0001-01-01T00:00:00Z'}}
        self.owners[cid] = {'item': item, 'present': True, 'entry': copy.deepcopy(entry)}
        if mounts:
            self.socket_owner = cid
            if self.mismatch == 'mount':
                item['Mounts'][0]['Source'] = '/synthetic-foreign.sock'
            elif self.mismatch == 'bounds':
                item['HostConfig']['Memory'] = 1024 * 1024 * 1024
            elif self.mismatch == 'owner':
                item['Config']['Labels']['io.sbarbase.owner'] = 'foreign-owner'
            if self.mode == 'uncertain-started':
                item['State'].update({'Running': True, 'StartedAt': '2026-10-06T00:00:00Z'})
                self.allocate_helpers(snapshot, entry)
            if self.mode in ('uncertain-started', 'uncertain-never-started'):
                return self.reply(1, '', 'Create response lost after allocation')
        return self.reply(value=cid + '\n')

    def allocate_helpers(self, snapshot, entry):
        installation = str(uuid5(NAMESPACE_URL, snapshot['run']))
        runtime = 'e_' + hashlib.sha256(entry['allocation_name'].encode()).hexdigest()[:24]
        resource = str(uuid5(NAMESPACE_URL, entry['allocation_name'] + ':helper'))
        volume_resource = str(uuid5(NAMESPACE_URL, entry['allocation_name'] + ':volume'))
        name, volume_name = 'sb07-' + resource, 'sb07-' + volume_resource
        cid = hashlib.sha256(name.encode()).hexdigest()
        def marks(identifier):
            return {'io.sbarbase.owner': 'sbarbase-lifecycle', 'io.sbarbase.installation': installation,
                    'io.sbarbase.environment': runtime, 'io.sbarbase.resource': identifier,
                    'io.sbarbase.role': 'environment'}
        contract = {'kind': 'container', 'id': cid, 'installation': installation, 'runtime': runtime, 'resource': resource}
        helper = {'Id': cid, 'Name': '/' + name, 'Config': {'Labels': marks(resource)}, 'State': {'Running': True}}
        self.helpers[cid] = {'item': helper, 'present': True, 'name': name}
        volume = {'kind': 'volume', 'id': volume_name, 'installation': installation, 'runtime': runtime,
                  'resource': volume_resource, 'createdAt': '2026-10-06T00:00:00Z'}
        self.volumes[volume_name] = {'item': {'Name': volume_name, 'Labels': marks(volume_resource),
                                           'CreatedAt': volume['createdAt']}, 'present': True}
        container_entry = {'name': name, 'runtime': runtime, 'resource': resource}
        if self.mode != 'uncertain-started':
            container_entry['resource_contract'] = contract
        self.packet = {'installation': installation, 'containers': [container_entry],
                       'volumes': [{'planned': volume, 'resource': volume}]}
        if self.foreign == 'helper':
            helper['Config']['Labels']['io.sbarbase.installation'] = str(uuid5(NAMESPACE_URL, 'foreign-installation'))
        elif self.foreign == 'volume':
            self.volumes[volume_name]['item']['CreatedAt'] = '2026-10-07T00:00:00Z'

    def command(self, argv, timeout=600):
        try:
            return self.dispatch(argv, timeout)
        except Exception as error:
            self.protocol_errors.append(type(error).__name__ + ': ' + str(error))
            raise

    def dispatch(self, argv, timeout=600):
        self.commands.append(list(argv))
        if argv[:2] != ['docker', '--host']:
            raise AssertionError('Unexpected command transport')
        arguments = argv[3:]
        action = arguments[0]
        if action == 'build':
            self.image_tag = self.option(arguments, '--tag')
            self.image_labels = self.labels(arguments)
            self.image_present = True
            return self.reply()
        if action == 'image':
            if arguments[1] == 'inspect':
                return self.reply(value=json.dumps([{'Id': self.image_id, 'RepoTags': [self.image_tag],
                                                     'Config': {'Labels': self.image_labels}}])) if self.image_present else self.reply(1)
            if arguments[1] == 'rm':
                self.events.append('image-rm')
                if any(item['present'] for item in [*self.helpers.values(), *self.volumes.values()]):
                    raise AssertionError('Image removed before helper reconciliation')
                self.image_present = False
                return self.reply()
            if arguments[1] == 'ls':
                return self.reply(value=self.image_tag + '\n' if self.image_present else '')
        if action == 'info':
            return self.reply(value='synthetic-daemon\n')
        if action == 'create':
            return self.create(arguments)
        if action == 'start':
            cid = arguments[-1]
            owner = self.owners[cid]
            self.events.append(('start', cid))
            owner['item']['State'].update({'StartedAt': '2026-10-06T00:00:00Z', 'Running': False})
            if cid == self.socket_owner:
                self.allocate_helpers(json.loads((self.output / 'result.json').read_text()), owner['entry'])
            return self.reply(value=json.dumps({'total': 40 if cid == self.socket_owner else 28, 'failed': 0, 'skipped': 0}))
        if action == 'inspect':
            target = arguments[1]
            found = next((value for cid, value in self.owners.items() if target in (cid, value['item']['Name'][1:])), None)
            found = found or next((value for cid, value in self.helpers.items() if target in (cid, value['name'])), None)
            self.events.append(('inspect', target))
            return self.reply(value=json.dumps([found['item']])) if found and found['present'] else self.reply(1)
        if action == 'cp':
            cid = arguments[1].split(':', 1)[0]
            if cid == self.socket_owner:
                self.events.append(('copy-ledger', cid))
                if self.mode == 'copy-failed':
                    return self.reply(1, '', 'Synthetic copy failure')
                if self.ledger != 'missing':
                    payload = '{' if self.ledger == 'invalid-json' else json.dumps(
                        {**self.packet, 'installation': 'invalid'} if self.ledger == 'invalid-identity' else self.packet)
                    (Path(arguments[2]) / 'actual-resources.json').write_text(payload)
                if self.mode == 'lost':
                    self.owners[cid]['present'] = False
            return self.reply()
        if action == 'stop':
            cid = arguments[-1]
            self.events.append(('stop', cid))
            collection = self.owners if cid in self.owners else self.helpers
            collection[cid]['item']['State']['Running'] = False
            return self.reply()
        if action == 'rm':
            cid = arguments[1]
            self.events.append(('rm', cid))
            collection = self.owners if cid in self.owners else self.helpers
            collection[cid]['present'] = False
            return self.reply()
        if action == 'ps':
            values = [(cid, item['item']['Name'][1:]) for cid, item in {**self.owners, **self.helpers}.items() if item['present']]
            if '--filter' in arguments:
                wanted = self.option(arguments, '--filter').removeprefix('id=')
                values = [item for item in values if item[0] == wanted]
            with_names = '{{.Names}}' in self.option(arguments, '--format')
            self.events.append(('container-absence', tuple(cid for cid, _ in values)))
            return self.reply(value=''.join(cid + (' ' + name if with_names else '') + '\n' for cid, name in values))
        if action == 'volume':
            if arguments[1] == 'inspect':
                value = self.volumes.get(arguments[2])
                return self.reply(value=json.dumps([value['item']])) if value and value['present'] else self.reply(1)
            if arguments[1] == 'rm':
                name = arguments[2]
                self.events.append(('volume-rm', name))
                if any(item['present'] for item in self.helpers.values()):
                    return self.reply(1, '', 'Volume still mounted')
                self.volumes[name]['present'] = False
                return self.reply()
            if arguments[1] == 'ls':
                names = tuple(name for name, item in self.volumes.items() if item['present'])
                self.events.append(('volume-absence', names))
                return self.reply(value=''.join(name + '\n' for name in names))
        raise AssertionError('Unexpected synthetic Docker command: ' + repr(arguments))


class RunnerUncertaintyTests(unittest.TestCase):
    def run_scenario(self, **options):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name) / 'fresh-evidence'
        engine = SyntheticEngine(output, **options)
        original_read = Path.read_text
        def evidence_read(path, *args, **kwargs):
            if options.get('ledger') == 'unreadable' and path == output / 'actual-resources.json':
                raise OSError('Synthetic unreadable ledger')
            return original_read(path, *args, **kwargs)
        argv = [str(RUNNER_PATH), 'actual', '--output', str(output), '--docker-host', 'unix:///synthetic-lifecycle-test.sock']
        with patch.object(RUNNER, 'command', side_effect=engine.command),\
                patch.object(RUNNER, 'source_digest', return_value='a' * 64),\
                patch.object(RUNNER.subprocess, 'run', side_effect=AssertionError('Real subprocess forbidden')),\
                patch.object(sys, 'argv', argv), patch.dict(os.environ, {'SBARBASE_LIFECYCLE_ROLE': 'assigned'}),\
                patch.object(Path, 'read_text', evidence_read), contextlib.redirect_stdout(io.StringIO()):
            code = RUNNER.main()
        self.assertEqual(engine.protocol_errors, [], 'Synthetic protocol failure was hidden by runner cleanup')
        return code, json.loads((output / 'result.json').read_text()), engine

    def assert_image_preserved(self, result, engine):
        self.assertTrue(engine.image_present)
        self.assertNotIn('image-rm', engine.events)
        self.assertTrue(any(item.get('image_tag') and item.get('preserved_for_reconciliation') for item in result['cleanup']))

    def test_lost_executed_owner_reconciles_copied_helpers_before_image_removal(self):
        code, result, engine = self.run_scenario()
        self.assertEqual(code, 0)
        self.assertEqual((result['total'], result['failed']), (68, 0))
        self.assertFalse(engine.image_present)
        helper = next(iter(engine.helpers))
        volume = next(iter(engine.volumes))
        self.assertFalse(engine.helpers[helper]['present'] or engine.volumes[volume]['present'])
        self.assertLess(engine.events.index(('copy-ledger', engine.socket_owner)), engine.events.index(('rm', helper)))
        self.assertLess(engine.events.index(('rm', helper)), engine.events.index(('volume-rm', volume)))
        self.assertLess(engine.events.index(('volume-absence', ())), engine.events.index('image-rm'))
        self.assertTrue(any(item.get('helper') == helper and item.get('absent') for item in result['cleanup']))
        self.assertTrue(any(item.get('helper_volume') == volume and item.get('absent') for item in result['cleanup']))

    def test_lost_executed_owner_without_ledger_retains_image_and_unresolved_journal(self):
        code, result, engine = self.run_scenario(ledger='missing')
        self.assertEqual(code, 1)
        self.assert_image_preserved(result, engine)
        self.assertTrue(all(item['present'] for item in [*engine.helpers.values(), *engine.volumes.values()]))
        self.assertTrue(any(item.get('container') == engine.socket_owner and item.get('unresolved') for item in result['cleanup']))

    def test_unreadable_and_invalid_copied_ledgers_cannot_prove_cleanup(self):
        for ledger in ('unreadable', 'invalid-json', 'invalid-identity'):
            with self.subTest(ledger=ledger):
                code, result, engine = self.run_scenario(ledger=ledger)
                self.assertEqual(code, 1)
                self.assert_image_preserved(result, engine)
                self.assertTrue(all(item['present'] for item in [*engine.helpers.values(), *engine.volumes.values()]))

    def test_failed_ledger_copy_preserves_owner_and_image(self):
        code, result, engine = self.run_scenario(mode='copy-failed')
        self.assertEqual(code, 1)
        self.assert_image_preserved(result, engine)
        self.assertTrue(engine.owners[engine.socket_owner]['present'])
        self.assertNotIn(('rm', engine.socket_owner), engine.events)
        self.assertTrue(any(item.get('container') == engine.socket_owner and item.get('preserved_for_reconciliation') for item in result['cleanup']))

    def test_uncertain_create_recovers_exact_planned_owner_and_helper_names(self):
        code, result, engine = self.run_scenario(mode='uncertain-started')
        self.assertEqual(code, 1)
        entry = next(item for item in result['checks'] if item['socket'])
        self.assertFalse(entry['execution_requested'])
        self.assertIn(('inspect', entry['allocation_name']), engine.events)
        self.assertNotIn(('start', engine.socket_owner), engine.events)
        helper_name = engine.packet['containers'][0]['name']
        self.assertIn(('inspect', helper_name), engine.events)
        self.assertFalse(any(item.get('unresolved') for item in result['cleanup']))
        self.assertFalse(engine.image_present or engine.owners[engine.socket_owner]['present'])
        self.assertLess(engine.events.index(('stop', engine.socket_owner)), engine.events.index(('copy-ledger', engine.socket_owner)))
        self.assertLess(engine.events.index(('copy-ledger', engine.socket_owner)), engine.events.index(('rm', engine.socket_owner)))

    def test_conclusively_never_started_allocation_needs_no_helper_ledger(self):
        code, result, engine = self.run_scenario(mode='uncertain-never-started')
        self.assertEqual(code, 1)
        self.assertFalse(engine.helpers or engine.volumes)
        self.assertNotIn(('copy-ledger', engine.socket_owner), engine.events)
        self.assertNotIn(('start', engine.socket_owner), engine.events)
        self.assertIn(('rm', engine.socket_owner), engine.events)
        self.assertFalse(any(item.get('unresolved') for item in result['cleanup']))
        self.assertFalse(engine.image_present)

    def test_wrong_owner_mount_or_bounds_refuses_before_start_or_cleanup_mutation(self):
        for mismatch in ('owner', 'mount', 'bounds'):
            with self.subTest(mismatch=mismatch):
                code, result, engine = self.run_scenario(mode='identity-mismatch', mismatch=mismatch)
                self.assertEqual(code, 1)
                self.assert_image_preserved(result, engine)
                self.assertTrue(engine.owners[engine.socket_owner]['present'])
                for action in ('start', 'stop', 'rm'):
                    self.assertNotIn((action, engine.socket_owner), engine.events)

    def test_changed_helper_or_volume_identity_preserves_ambiguous_resources(self):
        for foreign in ('helper', 'volume'):
            with self.subTest(foreign=foreign):
                code, result, engine = self.run_scenario(foreign=foreign)
                self.assertEqual(code, 1)
                self.assert_image_preserved(result, engine)
                volume = next(iter(engine.volumes))
                self.assertTrue(engine.volumes[volume]['present'])
                self.assertNotIn(('volume-rm', volume), engine.events)
                if foreign == 'helper':
                    helper = next(iter(engine.helpers))
                    self.assertTrue(engine.helpers[helper]['present'])
                    self.assertNotIn(('stop', helper), engine.events)
                    self.assertNotIn(('rm', helper), engine.events)


if __name__ == '__main__':
    unittest.main()
