"""Bounded local route admission for owned disposable bootstrap networks."""
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import sys
import time

from native_config_preservation_inspect import bounded_capture

POOL = ipaddress.ip_network('10.240.0.0/16')
MAX_NETWORKS = 256
MAX_ROUTES = 4096
MAX_AGE = 60


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate observation key')
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=unique_object)


def network_ids(raw):
    ids = raw.decode('ascii', errors='strict').splitlines()
    if (not ids or len(ids) > MAX_NETWORKS or len(set(ids)) != len(ids)
            or any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in ids)):
        raise ValueError('Network inventory is empty, duplicate, oversized or malformed')
    return sorted(ids)


def excluded_networks(raw, ids):
    records = [decode(line) for line in raw.splitlines()]
    if len(records) != len(ids):
        raise ValueError('Network inspection inventory differs')
    observed = set()
    excluded = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {'id', 'driver', 'ipam'} or record['id'] not in ids or record['id'] in observed:
            raise ValueError('Network inspection identity differs')
        observed.add(record['id'])
        driver = record['driver']
        if driver not in ('bridge', 'overlay', 'macvlan', 'ipvlan', 'host', 'null'):
            raise ValueError('Unknown network driver')
        ipam = record['ipam']
        if not isinstance(ipam, dict) or ipam.get('Driver') not in ('default', ''):
            raise ValueError('Unknown network IPAM driver')
        configs = ipam.get('Config')
        if configs is None and driver in ('host', 'null'):
            configs = []
        if not isinstance(configs, list) or len(configs) > 16 or (not configs and driver not in ('host', 'null')):
            raise ValueError('Network IPAM configuration is ambiguous')
        for config in configs:
            if not isinstance(config, dict) or not isinstance(config.get('Subnet'), str):
                raise ValueError('Network subnet is missing')
            subnet = ipaddress.ip_network(config['Subnet'], strict=True)
            if subnet.version == 4:
                excluded.append(subnet)
    return excluded


def excluded_routes(raw):
    routes = decode(raw)
    if not isinstance(routes, list) or not routes or len(routes) > MAX_ROUTES:
        raise ValueError('Route inventory is empty or oversized')
    excluded = []
    for route in routes:
        if not isinstance(route, dict) or not isinstance(route.get('dst'), str) or 'multipath' in route or 'nexthops' in route:
            raise ValueError('Route destination or multipath is ambiguous')
        if route['dst'] != 'default':
            subnet = ipaddress.ip_network(route['dst'], strict=False)
            if subnet.version != 4:
                raise ValueError('IPv4 route inventory contains another family')
            excluded.append(subnet)
        for key in ('gateway', 'prefsrc'):
            if key in route:
                address = ipaddress.ip_address(route[key])
                if address.version != 4:
                    raise ValueError('Route address family differs')
                excluded.append(ipaddress.ip_network(str(address) + '/32'))
    return excluded


def select_subnet(fixture, excluded):
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Fixture identity differs')
    start = int.from_bytes(hashlib.sha256(fixture.encode()).digest()[:2], 'big') % 4096
    for offset in range(4096):
        candidate = ipaddress.ip_network((int(POOL.network_address) + ((start + offset) % 4096) * 16, 28))
        if not any(candidate.overlaps(other) for other in excluded):
            return str(candidate)
    raise ValueError('Dedicated fixture subnet pool exhausted')


def admit_receipt(raw, fixture, now=None):
    if not isinstance(raw, str) or len(raw) > 2048:
        raise ValueError('Subnet receipt missing or oversized')
    value = decode(raw.encode())
    if (not isinstance(value, dict) or set(value) != {'version', 'fixture', 'subnet', 'observed_monotonic', 'routes_sha256', 'networks_sha256'}
            or type(value['version']) is not int or value['version'] != 1 or value['fixture'] != fixture):
        raise ValueError('Subnet receipt identity differs')
    subnet = ipaddress.ip_network(value['subnet'], strict=True)
    if subnet.version != 4 or subnet.prefixlen != 28 or not subnet.subnet_of(POOL) or str(subnet) != value['subnet']:
        raise ValueError('Subnet outside dedicated fixture pool')
    observed = value['observed_monotonic']
    age = (time.monotonic() if now is None else now) - observed if type(observed) in (int, float) else float('nan')
    if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
        raise ValueError('Subnet route snapshot is stale or future')
    if any(not isinstance(value[key], str) or not re.fullmatch(r'[a-f0-9]{64}', value[key]) for key in ('routes_sha256', 'networks_sha256')):
        raise ValueError('Subnet observation digest differs')
    return value


def observe(fixture, directory, capture=bounded_capture):
    directory = Path(directory)
    select_subnet(fixture, [])
    started = time.monotonic()
    end = started + 15
    observations = []

    def read(args):
        remaining = min(5, end - time.monotonic())
        if remaining <= 0:
            raise ValueError('Subnet observation deadline')
        result = capture(args, remaining, 1024 * 1024, 16 * 1024)
        ordinal = len(observations) + 1
        for stream in ('stdout', 'stderr'):
            path = directory / f'subnet-observation-{ordinal:02d}-{stream}.bin'
            with path.open('xb') as handle:
                handle.write(result[stream])
        observations.append({'argv': args, 'exit_code': result['exit_code'], 'refusal': result['refusal'],
                             'stdout_sha256': hashlib.sha256(result['stdout']).hexdigest(),
                             'stderr_sha256': hashlib.sha256(result['stderr']).hexdigest()})
        if result['exit_code'] != 0 or result['stderr'] or result['refusal']:
            raise ValueError('Subnet observation command refused')
        return result['stdout']

    try:
        ids = network_ids(read(['docker', 'network', 'ls', '--no-trunc', '--format', '{{.ID}}']))
        template = '{"id":{{json .Id}},"driver":{{json .Driver}},"ipam":{{json .IPAM}}}'
        networks = read(['docker', 'network', 'inspect', '--format', template, *ids])
        routes = read(['ip', '-j', '-4', 'route', 'show', 'table', 'all'])
        excluded = excluded_networks(networks, ids) + excluded_routes(routes)
        if network_ids(read(['docker', 'network', 'ls', '--no-trunc', '--format', '{{.ID}}'])) != ids:
            raise ValueError('Docker network inventory changed during admission')
        if decode(read(['ip', '-j', '-4', 'route', 'show', 'table', 'all'])) != decode(routes):
            raise ValueError('Host route inventory changed during admission')
        selected = select_subnet(fixture, excluded)
        if time.monotonic() >= end:
            raise ValueError('Subnet observation deadline')
        receipt = {'version': 1, 'fixture': fixture, 'subnet': selected,
                   'observed_monotonic': started,
                   'routes_sha256': hashlib.sha256(routes).hexdigest(),
                   'networks_sha256': hashlib.sha256(networks).hexdigest()}
        admit_receipt(json.dumps(receipt), fixture)
        with (directory / 'subnet-admission.json').open('x') as handle:
            json.dump(receipt, handle, sort_keys=True)
        return receipt
    finally:
        with (directory / 'subnet-observations.json').open('x') as handle:
            json.dump(observations, handle, indent=2)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Expected fixture identity and owned evidence directory')
    print(json.dumps(observe(sys.argv[1], sys.argv[2]), sort_keys=True))
