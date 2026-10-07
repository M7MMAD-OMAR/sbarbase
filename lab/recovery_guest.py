"""Pure construction-protocol preparation, without network or guest authority.

The final execution packet must supply actual input closure, guest/process/cgroup
identities and independently admitted effect authority. These checks never do so.
"""
from dataclasses import dataclass
import ipaddress
import math
import re

from recovery_inventory import Refused, exact, integer, HASH

PUBLIC_CANDIDATES = frozenset({'registry-1.docker.io', 'auth.docker.io',
                              'production.cloudfront.docker.com', 'snapshot.ubuntu.com'})
MAX_REQUEST = 1 << 20
MAX_CONNECTIONS = 64
MAX_TOTAL_CONNECTIONS = 4096
MAX_SECONDS = 1800
MAX_BYTES = 24 << 30
CONNECT_SECONDS = 30
IDLE_SECONDS = 120
FQDN = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+')


# Reviewed candidate bytes. These metadata pins do not verify payload delivery.
PACKAGE_PINS = {
    'docker-ce': ('5:29.8.2-1~ubuntu.24.04~noble', 'b53a44a6ff13277710978adbdbfea27fcdd7ac9f198017e733d2b8dc2343f8dc', 24358876),
    'docker-ce-cli': ('5:29.8.2-1~ubuntu.24.04~noble', '008b3b49b474136063fe0fbe8999b0e77c3e8556d7f7de21f98f05715e2f76ba', 17549632),
    'containerd.io': ('2.3.6-1~ubuntu.24.04~noble', '2eb8c6e244fe6886f2fa2eee9ec418c4b9bb44eb44fca748504f57c23341aed2', 23155464),
    'docker-buildx-plugin': ('0.37.1-1~ubuntu.24.04~noble', '3c398af6eea280ddb57f32334d83867efd2bf07c4bc1be75fbd0a61c490ea925', 17272004),
    'docker-compose-plugin': ('5.6.0-1~ubuntu.24.04~noble', '132266d499d9873aafcbe5b515997b8abd714914fa32a62ccac705c1ede7ca81', 8079740),
}
HELPER_PINS = {
    'qemu-system-x86_64': '27cd395848940fc6482256d85096fc64bc4fe3f3e909824d51c202f8314cd9e9',
    'qemu-img': '4362e911c13bb90e39b69479462cd4adb840749b582f4892ca114eb7e94dcf2e',
    'genisoimage': '73444004f0e95190264cd7e098108e760960cf63ba833b669487b01d5bb83e99',
}


@dataclass(frozen=True)
class PublicConnect:
    host: str
    port: int = 443
    native_admitted: bool = False


def parse_connect(raw, endpoints=PUBLIC_CANDIDATES):
    """Closed complete CONNECT header; it cannot authorize a socket effect."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REQUEST:
        raise Refused('Bounded construction CONNECT bytes required')
    if type(endpoints) is not frozenset or not endpoints or any(type(h) is not str or not FQDN.fullmatch(h) for h in endpoints):
        raise Refused('Exact reviewed construction endpoint set required')
    try:
        text = raw.decode('ascii')
    except UnicodeError:
        raise Refused('ASCII construction CONNECT header required') from None
    if not text.endswith('\r\n\r\n') or '\r\n\r\n' in text[:-4]:
        raise Refused('CONNECT body, nesting and pipelined data forbidden')
    lines = text[:-4].split('\r\n')
    if any(any(ord(c) < 32 or ord(c) == 127 for c in line) for line in lines):
        raise Refused('Ambiguous construction CONNECT control character')
    request = lines[0].split(' ')
    if len(request) != 3 or request[0] != 'CONNECT' or request[2] != 'HTTP/1.1':
        raise Refused('Only exact HTTP CONNECT construction transport permitted')
    authority = request[1]
    if not authority.endswith(':443') or authority.count(':') != 1:
        raise Refused('Exact public FQDN port 443 required')
    hostname = authority[:-4]
    if hostname not in endpoints:
        raise Refused('Unreviewed construction endpoint')
    headers = {}
    for line in lines[1:]:
        if ':' not in line:
            raise Refused('Malformed construction CONNECT header')
        name, value = line.split(':', 1)
        if not re.fullmatch(r'[A-Za-z-]+', name) or not value.startswith(' ') or value.startswith('  '):
            raise Refused('Ambiguous construction CONNECT header')
        name = name.lower(); value = value[1:]
        if name in headers or name not in ('host', 'user-agent', 'proxy-connection'):
            raise Refused('Unknown or duplicate construction CONNECT header')
        if not value or len(value) > 2048 or value != value.strip():
            raise Refused('Invalid construction CONNECT header value')
        headers[name] = value
    if headers.get('host') != authority or headers.get('proxy-connection', 'Keep-Alive').lower() != 'keep-alive':
        raise Refused('Construction CONNECT host or connection differs')
    return PublicConnect(hostname)


def public_peer(address):
    if type(address) is not str or '%' in address:
        raise Refused('Unscoped literal public peer address required')
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        raise Refused('Literal construction peer address required') from None
    if (not value.is_global or value.is_multicast or value.is_reserved
            or value.is_loopback or value.is_link_local or value.is_unspecified
            or (value.version == 6 and (value.is_site_local or value.ipv4_mapped
                or value.sixtofour or value.teredo
                or value in ipaddress.ip_network('64:ff9b::/96')
                or value in ipaddress.ip_network('64:ff9b:1::/48')))):
        raise Refused('Nonpublic or translated construction peer forbidden')
    return value.compressed


def bind_resolution(request, observed_host, answers, selected_peer):
    """Require an exact hostname and wholly public fresh resolver observation."""
    if (type(request) is not PublicConnect or type(request.host) is not str or request.host not in PUBLIC_CANDIDATES
            or type(request.port) is not int or request.port != 443
            or observed_host != request.host or request.native_admitted is not False):
        raise Refused('Exact source-only construction request required')
    if type(answers) is not list or not 0 < len(answers) <= 64:
        raise Refused('Bounded fresh resolver answer vector required')
    normalized = [public_peer(a) for a in answers]
    if len(set(normalized)) != len(normalized) or public_peer(selected_peer) not in normalized:
        raise Refused('Selected construction peer differs from resolver observation')
    return (request.host, tuple(normalized), public_peer(selected_peer))


def monotonic(value):
    if type(value) not in (int, float) or value < 0 or value > 1 << 53 or not math.isfinite(value):
        raise Refused('Finite nonnegative monotonic observation required')
    return value


class ConstructionBudget:
    """Fixture-only protocol ledger. Refusal closes it permanently."""
    native_admitted = False

    def __init__(self, started):
        self.started = monotonic(started)
        self.last_observation = self.started
        self.received = self.sent = 0
        self.connections = {}
        self.used = set()
        self.closed = False

    def refuse(self, reason):
        self.closed = True
        raise Refused(reason)

    def observe(self, now):
        try:
            now = monotonic(now)
        except Refused:
            self.closed = True
            raise
        if self.closed or now < self.last_observation or now - self.started >= MAX_SECONDS:
            self.refuse('Construction ledger closed, clock changed or deadline exhausted')
        self.last_observation = now
        for phase, opened, activity in self.connections.values():
            if (phase == 'connecting' and now - opened >= CONNECT_SECONDS) or now - activity >= IDLE_SECONDS:
                self.refuse('Construction connect or idle deadline exhausted')

    def begin(self, identifier, now):
        self.observe(now)
        if (type(identifier) is not str or not re.fullmatch(r'[a-f0-9]{32}', identifier)
                or identifier in self.used or len(self.connections) >= MAX_CONNECTIONS
                or len(self.used) >= MAX_TOTAL_CONNECTIONS):
            self.refuse('Construction connection identity or limit refused')
        self.used.add(identifier); self.connections[identifier] = ('connecting', now, now)

    def connected(self, identifier, now):
        self.observe(now)
        if type(identifier) is not str or identifier not in self.connections or self.connections[identifier][0] != 'connecting':
            self.refuse('Construction connection phase differs')
        _, opened, _ = self.connections[identifier]
        self.connections[identifier] = ('connected', opened, now)

    def transfer(self, identifier, received, sent, now):
        self.observe(now)
        if (type(identifier) is not str or identifier not in self.connections or self.connections[identifier][0] != 'connected'
                or not integer(received) or not integer(sent)
                or self.received + received > MAX_BYTES or self.sent + sent > MAX_BYTES):
            self.refuse('Construction traffic identity or byte budget refused')
        self.received += received; self.sent += sent
        phase, opened, activity = self.connections[identifier]
        self.connections[identifier] = (phase, opened, now if received or sent else activity)

    def end(self, identifier, now):
        self.observe(now)
        if type(identifier) is not str or identifier not in self.connections:
            self.refuse('Unknown construction connection completion')
        del self.connections[identifier]

    def withdraw(self):
        self.closed = True
        # This is ledger closure only. It does not claim QMP/network withdrawal.


def _validate_public_lock(value):
    exact(value, {'schema', 'status', 'image', 'packages', 'helpers', 'candidate_endpoints',
                  'payload_bytes_verified', 'final_source', 'build_input_closure',
                  'native_authority'}, 'Complete public preparation lock required')
    if (type(value['schema']) is not int or value['schema'] != 1 or value['status'] != 'preparation-only'
            or value['payload_bytes_verified'] is not False or value['final_source'] is not None
            or value['build_input_closure'] is not None or value['native_authority'] != 'uninstalled'):
        raise Refused('Public preparation cannot assert native or payload acceptance')
    if type(value['candidate_endpoints']) is not list or set(value['candidate_endpoints']) != PUBLIC_CANDIDATES or len(value['candidate_endpoints']) != len(PUBLIC_CANDIDATES):
        raise Refused('Exact candidate endpoint vector required')
    exact(value['image'], {'url', 'sha256', 'signer'}, 'Pinned public base image metadata required')
    if (value['image']['url'] != 'https://cloud-images.ubuntu.com/releases/noble/release-20260926/ubuntu-24.04-server-cloudimg-amd64.img'
            or value['image']['sha256'] != '6a81c37564db9b1ee84e141922625e1d7c5b389b99bb3c572e0243607d5bb4d2'
            or value['image']['signer'] != 'D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81'):
        raise Refused('Unreviewed public base image metadata')
    if type(value['packages']) is not list or len(value['packages']) != 5 or type(value['helpers']) is not list or len(value['helpers']) != 3:
        raise Refused('Complete candidate package and helper vectors required')
    names = set()
    for group, keys in ((value['packages'], {'name', 'version', 'sha256', 'bytes'}),
                        (value['helpers'], {'name', 'path', 'sha256'})):
        for item in group:
            exact(item, keys, 'Exact public candidate identity required')
            if type(item['name']) is not str or item['name'] in names or not item['name']:
                raise Refused('Duplicate public candidate identity')
            names.add(item['name'])
            if type(item['sha256']) is not str or not HASH.fullmatch(item['sha256']):
                raise Refused('Public candidate byte pin required')
            if 'bytes' in keys and (not integer(item['bytes'], 1) or type(item['version']) is not str or not item['version']):
                raise Refused('Public package version and byte count required')
            if 'bytes' in keys and PACKAGE_PINS.get(item['name']) != (item['version'], item['sha256'], item['bytes']):
                raise Refused('Unreviewed package version or byte pin')
            if 'path' in keys and item['path'] != '/usr/bin/' + item['name']:
                raise Refused('Explicit public system helper path required')
            if 'path' in keys and HELPER_PINS.get(item['name']) != item['sha256']:
                raise Refused('Unreviewed system helper byte pin')
    if names != {'docker-ce', 'docker-ce-cli', 'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin',
                 'qemu-system-x86_64', 'qemu-img', 'genisoimage'}:
        raise Refused('Unknown public package or helper')
    return value


def validate_public_lock(value):
    try:
        return _validate_public_lock(value)
    except (TypeError, KeyError, AttributeError, OverflowError):
        raise Refused('Malformed public preparation lock') from None


def require_construction_authority(*_):
    """No operational authority adapter is installed at this source checkpoint."""
    raise Refused('Guest construction effect authority is uninstalled')
