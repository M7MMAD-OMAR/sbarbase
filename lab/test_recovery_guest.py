"""Pure public construction protocol refusals, never network admission."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

import recovery_guest as guest
from recovery_inventory import Refused


def request(host='snapshot.ubuntu.com'):
    return f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode()


class GuestTests(unittest.TestCase):
    def test_reviewed_candidate_connect_is_descriptive_only(self):
        for host in guest.PUBLIC_CANDIDATES:
            parsed = guest.parse_connect(request(host))
            self.assertEqual(parsed.host, host)
            self.assertIs(parsed.native_admitted, False)

    def test_unknown_raw_ip_alternate_port_credentials_and_wildcards_refuse(self):
        for host in ('private.example', '127.0.0.1', '169.254.169.254', 'snapshot.ubuntu.com.',
                     '*.docker.io', 'SNAPSHOT.UBUNTU.COM', 'user@snapshot.ubuntu.com', '[::1]'):
            with self.assertRaises(Refused): guest.parse_connect(request(host))
        with self.assertRaises(Refused): guest.parse_connect(request().replace(b':443', b':444'))

    def test_connect_smuggling_nesting_and_body_refuse(self):
        original = request()
        for raw in (original + b'x', original + original, original.replace(b'\r\n', b'\n'),
                    original.replace(b'CONNECT ', b'CONNECT  '), original.replace(b'CONNECT', b'GET'),
                    original.replace(b'HTTP/1.1', b'HTTP/1.0'), original.replace(b'Host:', b' Host:'),
                    original.replace(b'Host: ', b'Host:\t'), original.replace(b'Host: ', b'Host:  ')):
            with self.assertRaises(Refused): guest.parse_connect(raw)

    def test_duplicate_unknown_and_credential_headers_refuse(self):
        for header in ('Host: snapshot.ubuntu.com:443', 'Proxy-Authorization: secret',
                       'Content-Length: 0', 'Transfer-Encoding: chunked', 'Connection: upgrade'):
            raw = request()[:-2] + header.encode() + b'\r\n\r\n'
            with self.assertRaises(Refused): guest.parse_connect(raw)

    def test_hostname_mismatch_and_request_budget_refuse(self):
        for raw in (request().replace(b'Host: snapshot', b'Host: registry'),
                    b'x' * (guest.MAX_REQUEST + 1), 'not bytes', b'\xff'):
            with self.assertRaises(Refused): guest.parse_connect(raw)

    def test_optional_known_client_headers_are_bounded(self):
        raw = request()[:-2] + b'User-Agent: Go-http-client/1.1\r\nProxy-Connection: Keep-Alive\r\n\r\n'
        self.assertFalse(guest.parse_connect(raw).native_admitted)
        for value in (b'x' * 2049, b'\x7f', b'Upgrade'):
            changed = request()[:-2] + b'Proxy-Connection: ' + value + b'\r\n\r\n'
            with self.assertRaises(Refused): guest.parse_connect(changed)

    def test_private_reserved_translated_multicast_and_metadata_peers_refuse(self):
        for peer in ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254',
                     '0.0.0.0', '100.64.0.1', '224.0.0.1', '240.0.0.1', '192.0.2.1', '::1',
                     '::', 'fc00::1', 'fe80::1', 'fe80::1%eth0', 'ff00::1', 'fec0::1',
                     '2001:db8::1', '::ffff:8.8.8.8', '64:ff9b::a00:1', '2002:0808:0808::1'):
            with self.subTest(peer=peer):
                with self.assertRaises(Refused): guest.public_peer(peer)

    def test_public_resolution_requires_all_answers_and_selected_peer_match(self):
        parsed = guest.parse_connect(request())
        bound = guest.bind_resolution(parsed, parsed.host, ['8.8.8.8', '2606:4700:4700::1111'], '8.8.8.8')
        self.assertEqual(bound[2], '8.8.8.8')
        for host, answers, selected in ((parsed.host, ['8.8.8.8', '10.0.0.1'], '8.8.8.8'),
                                        (parsed.host, ['8.8.8.8'], '1.1.1.1'),
                                        ('foreign.example', ['8.8.8.8'], '8.8.8.8'),
                                        (parsed.host, ['8.8.8.8', '8.8.8.8'], '8.8.8.8'),
                                        (parsed.host, [], '8.8.8.8')):
            with self.assertRaises(Refused): guest.bind_resolution(parsed, host, answers, selected)

    def test_fixture_ledger_observes_connection_lifecycle(self):
        ledger = guest.ConstructionBudget(10); identifier = 'a' * 32
        ledger.begin(identifier, 11); ledger.connected(identifier, 12)
        ledger.transfer(identifier, 10, 20, 13); ledger.end(identifier, 14)
        self.assertEqual((ledger.received, ledger.sent), (10, 20))
        self.assertFalse(ledger.native_admitted)
        ledger.withdraw()
        with self.assertRaises(Refused): ledger.begin('b' * 32, 15)

    def test_connection_identity_reuse_and_count_exhaustion_close_ledger(self):
        ledger = guest.ConstructionBudget(0); identifier = 'a' * 32
        ledger.begin(identifier, 1); ledger.connected(identifier, 2); ledger.end(identifier, 3)
        with self.assertRaises(Refused): ledger.begin(identifier, 4)
        self.assertTrue(ledger.closed)
        ledger = guest.ConstructionBudget(0)
        for i in range(guest.MAX_CONNECTIONS): ledger.begin(f'{i:032x}', 1)
        with self.assertRaises(Refused): ledger.begin(f'{guest.MAX_CONNECTIONS:032x}', 1)
        self.assertTrue(ledger.closed)

    def test_connect_idle_and_global_deadlines_close_before_effect(self):
        for phase, deadline in (('connecting', guest.CONNECT_SECONDS), ('connected', guest.IDLE_SECONDS),
                                ('connected', guest.MAX_SECONDS)):
            ledger = guest.ConstructionBudget(0); identifier = 'a' * 32
            ledger.begin(identifier, 0)
            if phase == 'connected': ledger.connected(identifier, 0)
            with self.assertRaises(Refused): ledger.observe(deadline)
            self.assertTrue(ledger.closed)

    def test_invalid_clock_and_backwards_observation_permanently_close(self):
        for now in (True, float('nan'), float('inf'), -1, '3', 1 << 1000):
            ledger = guest.ConstructionBudget(0)
            with self.assertRaises(Refused): ledger.observe(now)
            self.assertTrue(ledger.closed)
        ledger = guest.ConstructionBudget(0); ledger.observe(5)
        with self.assertRaises(Refused): ledger.observe(4)

    def test_total_connection_churn_is_bounded(self):
        ledger = guest.ConstructionBudget(0)
        for i in range(guest.MAX_TOTAL_CONNECTIONS):
            identifier = f'{i:032x}'
            ledger.begin(identifier, 0); ledger.connected(identifier, 0); ledger.end(identifier, 0)
        with self.assertRaises(Refused): ledger.begin(f'{guest.MAX_TOTAL_CONNECTIONS:032x}', 0)
        self.assertTrue(ledger.closed)

    def test_independent_incoming_outgoing_byte_budgets_refuse(self):
        for received, sent in ((guest.MAX_BYTES + 1, 0), (0, guest.MAX_BYTES + 1), (True, 0), (0, 1.0)):
            ledger = guest.ConstructionBudget(0); identifier = 'a' * 32
            ledger.begin(identifier, 0); ledger.connected(identifier, 1)
            with self.assertRaises(Refused): ledger.transfer(identifier, received, sent, 2)
            self.assertTrue(ledger.closed)

    def test_unknown_connection_and_phase_refuse(self):
        for method in ('connected', 'transfer', 'end'):
            ledger = guest.ConstructionBudget(0)
            with self.assertRaises(Refused):
                if method == 'transfer': ledger.transfer('a' * 32, 1, 0, 1)
                else: getattr(ledger, method)('a' * 32, 1)
        ledger = guest.ConstructionBudget(0); ledger.begin('a' * 32, 0)
        with self.assertRaises(Refused): ledger.transfer('a' * 32, 1, 0, 1)

    def test_unhashable_connection_identity_closes_ledger(self):
        for method in ('begin', 'connected', 'transfer', 'end'):
            ledger = guest.ConstructionBudget(0)
            with self.assertRaises(Refused):
                if method == 'transfer': ledger.transfer([], 1, 0, 1)
                else: getattr(ledger, method)([], 1)
            self.assertTrue(ledger.closed)

    def test_hand_constructed_wrong_request_port_and_authority_refuse(self):
        for parsed in (guest.PublicConnect('snapshot.ubuntu.com', 444),
                       guest.PublicConnect('snapshot.ubuntu.com', 443.0),
                       guest.PublicConnect('snapshot.ubuntu.com', native_admitted=True),
                       guest.PublicConnect('foreign.example')):
            with self.assertRaises(Refused): guest.bind_resolution(parsed, parsed.host, ['8.8.8.8'], '8.8.8.8')
        for host in ([], {}, None, True):
            with self.assertRaises(Refused): guest.bind_resolution(guest.PublicConnect(host), 'snapshot.ubuntu.com', ['8.8.8.8'], '8.8.8.8')

    def test_zero_byte_transfer_does_not_refresh_idle_activity(self):
        ledger = guest.ConstructionBudget(0); identifier = 'a' * 32
        ledger.begin(identifier, 0); ledger.connected(identifier, 0)
        ledger.transfer(identifier, 0, 0, 119)
        self.assertEqual(ledger.connections[identifier][2], 0)
        with self.assertRaises(Refused): ledger.observe(120)
        self.assertTrue(ledger.closed)

    def test_actual_traffic_refreshes_idle_activity(self):
        ledger = guest.ConstructionBudget(0); identifier = 'a' * 32
        ledger.begin(identifier, 0); ledger.connected(identifier, 0)
        ledger.transfer(identifier, 1, 0, 119); ledger.observe(120)
        self.assertEqual(ledger.connections[identifier][2], 119)
        with self.assertRaises(Refused): ledger.observe(239)

    def test_lock_is_preparation_only_and_cannot_assert_payload_acceptance(self):
        value = json.loads((Path(__file__).parent / 'recovery-guest.lock.json').read_text())
        self.assertIs(guest.validate_public_lock(value), value)
        for key, changed in (('payload_bytes_verified', True), ('final_source', {'sha256': 'a' * 64}),
                             ('build_input_closure', {}), ('native_authority', 'accepted'), ('schema', True)):
            invalid = copy.deepcopy(value); invalid[key] = changed
            with self.assertRaises(Refused): guest.validate_public_lock(invalid)

    def test_lock_unknown_missing_duplicate_and_invalid_hash_refuse(self):
        value = json.loads((Path(__file__).parent / 'recovery-guest.lock.json').read_text())
        for action in ('unknown', 'missing', 'duplicate', 'hash'):
            invalid = copy.deepcopy(value)
            if action == 'unknown': invalid['helpers'][0]['name'] = 'foreign'
            elif action == 'missing': invalid['packages'].pop()
            elif action == 'duplicate': invalid['helpers'][1] = invalid['helpers'][0]
            else: invalid['packages'][0]['sha256'] = ''
            with self.assertRaises(Refused): guest.validate_public_lock(invalid)

    def test_lock_candidate_versions_pins_sizes_and_groups_are_bound(self):
        value = json.loads((Path(__file__).parent / 'recovery-guest.lock.json').read_text())
        for group, key, changed in (('packages', 'version', 'arbitrary-version'), ('packages', 'sha256', '0' * 64),
                                    ('packages', 'bytes', 1), ('helpers', 'sha256', '1' * 64)):
            invalid = copy.deepcopy(value); invalid[group][0][key] = changed
            with self.assertRaises(Refused): guest.validate_public_lock(invalid)
        invalid = copy.deepcopy(value)
        invalid['packages'][0]['name'] = 'qemu-img'
        invalid['helpers'][1].update(name='docker-ce', path='/usr/bin/docker-ce')
        with self.assertRaises(Refused): guest.validate_public_lock(invalid)

    def test_malformed_lock_container_values_are_closed_refusals(self):
        value = json.loads((Path(__file__).parent / 'recovery-guest.lock.json').read_text())
        for key, changed in (('candidate_endpoints', [{}]), ('packages', [None] * 5),
                             ('helpers', [None] * 3), ('image', None)):
            invalid = copy.deepcopy(value); invalid[key] = changed
            with self.assertRaises(Refused): guest.validate_public_lock(invalid)

    def test_native_network_guest_and_subprocess_effects_stay_denied(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('Network effect forbidden')), \
             patch.object(subprocess, 'run', side_effect=AssertionError('Guest effect forbidden')):
            guest.parse_connect(request())
            for supplied in (None, True, {'accepted': True}, guest.ConstructionBudget(0)):
                with self.assertRaises(Refused): guest.require_construction_authority(supplied)
