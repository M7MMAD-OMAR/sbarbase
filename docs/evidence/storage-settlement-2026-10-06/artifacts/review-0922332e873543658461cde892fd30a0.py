"""Independent source-only operational probes using owned temporary files."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path('/home/sbarah/.codex/worktrees/09c0/sbarbase')
sys.path.insert(0, str(PROJECT / 'lab'))
import storage_write_settlement as s
from test_storage_write_settlement import manifest, DiskFixtureAuthority


class OperationalProbes(unittest.TestCase):
    def prepare(self, root):
        declared = manifest(2)
        authority = DiskFixtureAuthority(declared)
        return declared, authority, s.SourceSettlement(declared, s.Journal(root), authority)

    def last(self, root):
        with s.Journal(root).locked() as journal:
            return journal.records[-1]['phase']

    def test_real_exit_after_applied_fixture_effect_never_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declared, authority, protocol = self.prepare(root)
            receipt = protocol.settle()
            marker = root / 'effect-marker'
            pid = os.fork()
            if pid == 0:
                def effect():
                    with marker.open('wb') as stream:
                        stream.write(b'one fixture effect')
                        stream.flush()
                        os.fsync(stream.fileno())
                    os._exit(71)
                protocol.record_fixture_effect(receipt, 'fixture-effect', effect)
                os._exit(72)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 71)
            self.assertEqual(self.last(root), 'effect-pending')
            restarted = s.SourceSettlement(declared, s.Journal(root), authority)
            with self.assertRaises(s.Refused):
                restarted.record_fixture_effect(receipt, 'fixture-effect', lambda: self.fail('Replay'))
            with self.assertRaises(s.Refused):
                restarted.settle()
            self.assertEqual(marker.read_bytes(), b'one fixture effect')

    def test_real_exit_after_durable_stop_pending_recovers_current_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declared, authority, protocol = self.prepare(root)
            marker = root / 'stopped-marker'
            pid = os.fork()
            if pid == 0:
                def stop(value, token):
                    marker.write_bytes(b'stopped')
                    os._exit(73)
                authority.stop = stop
                protocol.settle()
                os._exit(74)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 73)
            self.assertEqual(self.last(root), 'stop-pending')
            self.assertEqual(marker.read_bytes(), b'stopped')
            authority.stopped = True
            receipt = protocol.settle()
            self.assertNotIn('stop', authority.calls)
            self.assertEqual(receipt['evidence'], 'fixture')
            self.assertEqual(self.last(root), 'settled')

    def test_actual_process_lock_contention_blocks_second_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declared, authority, protocol = self.prepare(root)
            read_fd, write_fd = os.pipe()
            with s.Journal(root).locked():
                pid = os.fork()
                if pid == 0:
                    os.close(read_fd)
                    try:
                        protocol.settle()
                    except s.Refused:
                        os.write(write_fd, b'refused')
                        os._exit(0)
                    os._exit(75)
                os.close(write_fd)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                self.assertEqual(os.read(read_fd, 32), b'refused')
                os.close(read_fd)
            self.assertEqual(protocol.settle()['evidence'], 'fixture')

    def test_ordered_inventory_python_bun_match_adversarial_property_keys(self):
        cases = []
        for variant in range(12):
            row = {'tail': '日本語😀\u2028\u2029\b\f\n\r\t\x00', '4294967295': 'not-index',
                   '10': 10, '01': 'not-index', '2': 2, '0': 0, '__proto__': {'x': True},
                   'nested': {'9007199254740991': -9007199254740991, '3': 9007199254740991},
                   'empty': [], 'variant': variant}
            cases.append([row] if variant % 2 else [dict(reversed(list(row.items())))])
        script = "import {sourceSettlementInventoryDigest as digest} from './src/control/storage-settlement-contract.ts'; const cases=JSON.parse(await Bun.stdin.text()); console.log(JSON.stringify(cases.map(digest)));"
        result = subprocess.run(['bun', '-e', script], cwd=PROJECT, input=json.dumps(cases),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [s.ordered_inventory_digest(row) for row in cases])

    def test_foreign_manifest_cannot_reuse_settled_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declared, authority, protocol = self.prepare(root)
            receipt = protocol.settle()
            for key, value in [('operation', '11111111-1111-1111-1111-111111111111'),
                               ('management_epoch', 99), ('generation', 99), ('actor', 'foreign')]:
                changed = copy.deepcopy(declared)
                changed[key] = value
                other = s.SourceSettlement(changed, s.Journal(root), DiskFixtureAuthority(changed))
                with self.assertRaises(s.Refused):
                    other.revalidate(receipt)
                with self.assertRaises(s.Refused):
                    other.record_fixture_effect(receipt, 'fixture-effect', lambda: self.fail('Foreign effect'))

    def test_incomplete_record_remains_unchanged_without_callbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declared, authority, protocol = self.prepare(root)
            protocol.settle()
            target = root / 'journal.jsonl'
            with target.open('ab') as stream:
                stream.write(b'{"sequence":8')
            before = target.read_bytes()
            calls = list(authority.calls)
            with self.assertRaises(s.Refused):
                protocol.settle()
            self.assertEqual(before, target.read_bytes())
            self.assertEqual(calls, authority.calls)


if __name__ == '__main__':
    unittest.main(verbosity=2)
