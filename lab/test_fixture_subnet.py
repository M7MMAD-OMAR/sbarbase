"""Collision and uncertain-observation refusal for owned fixture routing."""
import copy
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy/verify'))
import fixture_subnet as subnet

FIXTURE = 'identity-public-test-0'
ID = '1' * 64
NETWORK = {'id': ID, 'driver': 'bridge', 'ipam': {'Driver': 'default', 'Config': [{'Subnet': '172.17.0.0/16'}]}}
ROUTES = [{'dst': 'default', 'gateway': '172.16.0.1'}, {'dst': '172.16.0.0/20'}, {'dst': '127.0.0.1', 'table': 'local'}]


class SubnetTests(unittest.TestCase):
    def receipt(self):
        return {'version': 1, 'fixture': FIXTURE, 'subnet': subnet.select_subnet(FIXTURE, []),
                'observed_monotonic': 100, 'routes_sha256': 'a' * 64, 'networks_sha256': 'b' * 64}

    def test_first_collision_selects_another_nonoverlapping_segment(self):
        first = ipaddress.ip_network(subnet.select_subnet(FIXTURE, []))
        second = ipaddress.ip_network(subnet.select_subnet(FIXTURE, [first]))
        self.assertFalse(first.overlaps(second))
        self.assertTrue(second.subnet_of(subnet.POOL))

    def test_exhausted_dedicated_pool_refuses(self):
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            subnet.select_subnet(FIXTURE, [ipaddress.ip_network('10.0.0.0/8')])

    def test_secondary_route_table_and_gateway_exclude_candidates(self):
        first = subnet.select_subnet(FIXTURE, [])
        routes = ROUTES + [{'dst': first, 'table': 77}, {'dst': 'default', 'gateway': '10.240.0.1'}]
        excluded = subnet.excluded_routes(json.dumps(routes).encode())
        selected = ipaddress.ip_network(subnet.select_subnet(FIXTURE, excluded))
        self.assertFalse(any(selected.overlaps(other) for other in excluded))

    def test_missing_ambiguous_and_malformed_routes_refuse(self):
        for routes in ([], [{}], [{'dst': 'bad'}], [{'dst': 'default', 'nexthops': []}], [{'dst': '::1'}]):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                subnet.excluded_routes(json.dumps(routes).encode())

    def test_duplicate_json_keys_refuse(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            subnet.decode(b'{"dst":"default","dst":"10.240.0.0/16"}')

    def test_network_identity_duplicates_and_unknown_ipam_refuse(self):
        bad = copy.deepcopy(NETWORK); bad['ipam']['Driver'] = 'unknown-plugin'
        for records in ([NETWORK, NETWORK], [bad], [{**NETWORK, 'id': '2' * 64}]):
            raw = b'\n'.join(json.dumps(record).encode() for record in records)
            with self.subTest(records=records), self.assertRaises(ValueError):
                subnet.excluded_networks(raw, [ID])

    def test_network_inventory_refuses_empty_duplicate_and_short_ids(self):
        for raw in (b'', (ID + '\n' + ID).encode(), b'abc\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                subnet.network_ids(raw)

    def test_nonallocating_builtin_networks_admit_null_config_only(self):
        for driver in ('host', 'null'):
            record = {'id': ID, 'driver': driver, 'ipam': {'Driver': 'default', 'Config': None}}
            self.assertEqual(subnet.excluded_networks(json.dumps(record).encode(), [ID]), [])
        for driver in ('bridge', 'unknown-plugin'):
            record = {'id': ID, 'driver': driver, 'ipam': {'Driver': 'default', 'Config': None}}
            with self.subTest(driver=driver), self.assertRaises(ValueError):
                subnet.excluded_networks(json.dumps(record).encode(), [ID])

    def test_receipt_refuses_stale_future_nonfinite_and_wrong_fixture(self):
        for change in ({'observed_monotonic': 39}, {'observed_monotonic': 101},
                       {'observed_monotonic': float('nan')}, {'observed_monotonic': True}, {'fixture': 'identity-other'}):
            value = {**self.receipt(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                subnet.admit_receipt(json.dumps(value), FIXTURE, 100)

    def test_receipt_rejects_outside_pool_wrong_prefix_and_extra_fields(self):
        for change in ({'subnet': '192.168.0.0/28'}, {'subnet': '10.240.0.0/24'}, {'extra': 1}, {'routes_sha256': 'bad'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                subnet.admit_receipt(json.dumps({**self.receipt(), **change}), FIXTURE, 100)
        self.assertEqual(subnet.admit_receipt(json.dumps(self.receipt()), FIXTURE, 160)['version'], 1)

    def fake_capture(self, *, route_drift=False, id_drift=False, stderr=b'', refusal=None):
        reads = {'routes': 0, 'ids': 0}
        def capture(args, timeout, stdout_limit, stderr_limit):
            self.assertLessEqual(timeout, 5)
            if args[:3] == ['docker', 'network', 'ls']:
                reads['ids'] += 1
                value = ('2' * 64 if id_drift and reads['ids'] == 2 else ID) + '\n'
            elif args[:3] == ['docker', 'network', 'inspect']:
                value = json.dumps(NETWORK) + '\n'
            else:
                reads['routes'] += 1
                routes = ROUTES + ([{'dst': '10.240.0.0/16'}] if route_drift and reads['routes'] == 2 else [])
                value = json.dumps(routes)
            return {'stdout': value.encode(), 'stderr': stderr, 'exit_code': 0, 'refusal': refusal}
        return capture

    def test_stable_observations_create_bound_receipt_and_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = subnet.observe(FIXTURE, directory, self.fake_capture())
            self.assertEqual(subnet.admit_receipt(json.dumps(receipt), FIXTURE)['fixture'], FIXTURE)
            self.assertEqual(len(list(Path(directory).glob('subnet-observation-*-stdout.bin'))), 5)
            self.assertEqual(json.loads((Path(directory) / 'subnet-admission.json').read_text()), receipt)

    def test_route_and_network_drift_refuse_without_admission(self):
        for options in ({'route_drift': True}, {'id_drift': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, 'changed'):
                    subnet.observe(FIXTURE, directory, self.fake_capture(**options))
                self.assertFalse((Path(directory) / 'subnet-admission.json').exists())
                self.assertTrue((Path(directory) / 'subnet-observations.json').exists())

    def test_diagnostic_and_capture_limit_refuse_preserving_original_bytes(self):
        for options in ({'stderr': b'daemon warning\n'}, {'refusal': 'stdout limit'}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, 'command refused'):
                    subnet.observe(FIXTURE, directory, self.fake_capture(**options))
                self.assertFalse((Path(directory) / 'subnet-admission.json').exists())
                self.assertTrue((Path(directory) / 'subnet-observation-01-stderr.bin').exists())


if __name__ == '__main__':
    unittest.main()
