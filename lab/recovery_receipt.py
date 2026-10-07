"""Retention admission backed by actual current registry and per-set native proof.

Imports perform no filesystem, native or network effects. The trusted installed
consumer supplies checkout and Catalog snapshots, never the HTTP client.
"""
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path

from recovery_inventory import (Refused, HASH, binding, canonical, exact, host,
                                resources, recovery_set_digest,
                                catalog_inventory_digest, integer, coverage,
                                validate_manifest)
from recovery_inventory import MAX_HEADER

REQUIREMENTS = ('SB05-IDENTITY', 'SB05-INVENTORY', 'SB05-ENCRYPTION', 'SB05-AUTH',
                'SB05-STORAGE', 'SB05-ROTATION', 'SB05-FEATURES', 'SB05-EXTERNAL',
                'SB05-RESTORE', 'SB05-FRESH-HOST', 'SB05-ADMISSION')


def strict_json(raw):
    """Decode bounded JSON without ambiguous keys or nonfinite numbers."""
    if len(raw) > MAX_HEADER:
        raise Refused('Native recovery JSON exceeds header budget')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refused('Duplicate native recovery JSON key')
            result[key] = value
        return result

    def constant(_):
        raise Refused('Nonfinite native recovery JSON number')

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('Malformed native recovery JSON') from None


def require_identity(actual, expected, reason):
    """Compare closed JSON projections with exact scalar and container types."""
    canonical(actual); canonical(expected)

    def matches(left, right):
        if type(left) is not type(right):
            return False
        if type(right) is dict:
            return set(left) == set(right) and all(matches(left[k], right[k]) for k in right)
        if type(right) is list:
            return len(left) == len(right) and all(matches(a, b) for a, b in zip(left, right))
        return type(right) in (str, int, bool, type(None)) and left == right

    if not matches(actual, expected):
        raise Refused(reason)


@dataclass(frozen=True)
class RegistryAcceptance:
    """A trusted checkout locator, not an assertion or cached acceptance result."""
    checkout: Path

    def current(self, placements):
        root = Path(self.checkout)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise Refused('Explicit trusted checkout identity required')
        module_path = root / 'deploy/verify/capability_registry.py'
        if not module_path.is_file() or any(p.is_symlink() for p in (module_path, *module_path.parents)):
            raise Refused('Canonical current acceptance verifier unavailable')
        try:
            spec = importlib.util.spec_from_file_location('fresh_host_recovery_capability_registry', module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            placements = {placements} if isinstance(placements, str) else set(placements)
            selections = {(key, placement) for key in ('SB-05', 'SB-13a') for placement in placements}
            rows, errors = module.check(root, require_acceptance=True, selections=selections)
            selected = {r['capability'] + ':' + r['placement']: r for r in rows if (r['capability'], r['placement']) in selections}
            if errors or not selections or len(selected) != len(selections) or any(r['acceptance'] != 'accepted' for r in selected.values()):
                raise Refused('Complete current SB-05 and SB-13a acceptance required')
            for row in selected.values():
                require_identity(row['source'], next(iter(selected.values()))['source'],
                                 'Native acceptance source identities differ')
            return root, module, selected
        except Refused:
            raise
        except (OSError, ValueError, TypeError, KeyError, ImportError, AttributeError):
            raise Refused('Canonical current native acceptance unavailable') from None


def artifact(value):
    exact(value, {'format', 'bytes', 'sha256'}, 'Exact encrypted recovery artifact identity required')
    if (value['format'] != 'sbarbase-recovery-stream-v1' or not integer(value['bytes'], 1)
            or not isinstance(value['sha256'], str) or not HASH.fullmatch(value['sha256'])):
        raise Refused('Encrypted recovery artifact identity is invalid')


def expected_observation(receipt):
    """Exact native-result projection, to be found in an accepted proof log."""
    manifest = receipt['recovery_set']
    validate_manifest(manifest)
    target = receipt['destination']
    exact(target, {'host', 'resources', 'placements', 'empty_inventory_sha256'}, 'Fresh destination identity required')
    host(target['host']); resources(target['resources'])
    source = manifest['source_host']
    if any(target['host'][key] == source[key] for key in ('host', 'daemon', 'installation')):
        raise Refused('Destination is not a distinct fresh host and installation')
    if any(r['installation'] != target['host']['installation'] for r in target['resources']):
        raise Refused('Destination resource installation differs')
    if any(r['kind'] == 'shared-database' and r['identity']['engine']['daemon'] != target['host']['daemon'] for r in target['resources']):
        raise Refused('Destination shared engine daemon differs from target host')
    source_resources = [r for e in manifest['environments'] for r in e['resources']]
    source_ids = physical_containers(source_resources)
    source_uuids = {r['resource'] for r in source_resources}
    if (source_ids & physical_containers(target['resources'])
            or any(r['resource'] in source_uuids for r in target['resources'])):
        raise Refused('Destination reuses a source physical resource')
    runtime_ids = {e['binding']['runtime'] for e in manifest['environments']}
    if {r['runtime'] for r in target['resources']} != runtime_ids or not isinstance(target['placements'], list):
        raise Refused('Destination omits or adds application runtimes')
    placements = {}
    for item in target['placements']:
        exact(item, {'runtime', 'placement', 'coverage'}, 'Explicit destination runtime placement required')
        if item['runtime'] not in runtime_ids or item['runtime'] in placements:
            raise Refused('Unknown or duplicate destination runtime placement')
        coverage([r for r in target['resources'] if r['runtime'] == item['runtime']], item)
        placements[item['runtime']] = item
    if set(placements) != runtime_ids:
        raise Refused('Destination placement omits a recovered runtime')
    if not isinstance(target['empty_inventory_sha256'], str) or not HASH.fullmatch(target['empty_inventory_sha256']):
        raise Refused('Empty target observation identity required')
    return {'schema': 1, 'format': 'sbarbase-fresh-host-observation-v1', 'status': 'verified',
            'recovery_set_sha256': receipt['recovery_set_sha256'],
            'encrypted_artifact': receipt['encrypted_artifact'],
            'export_source': manifest['source'], 'restorer_source': receipt['source'],
            'source_host': source, 'target_host': target['host'],
            'target_resources': target['resources'],
            'target_placements': target['placements'],
            'empty_inventory_sha256': target['empty_inventory_sha256'],
            'source_state_access': 'excluded', 'requirements': list(REQUIREMENTS),
            'features': sorted(i['id'] + ':' + (i['runtime'] or 'installation') for i in manifest['features'] if i['enabled']),
            'rotated_credentials': sorted(i['id'] for i in manifest['secrets'] if i['policy'] == 'rotate-platform-access'),
            'preserved_key_ids': sorted(i['id'] for i in manifest['secrets'] if i['policy'] in ('preserve-data-key', 'rewrap-data-key')),
            'external_store_policies': [list(i) for i in sorted((i['runtime'], i['feature'], i['policy']) for i in manifest['external_stores'])]}


def physical_containers(values):
    result = {r['id'] for r in values if r['kind'] == 'container'}
    for item in values:
        if item['kind'] == 'shared-database':
            result.add(item['identity']['engine']['id'])
            result.update(item['identity']['tenant']['writers'])
    return result


def proof_snapshot(root, module, rows, observation):
    """Snapshot proof bytes and require this log in every required SB-05 placement."""
    snapshot = {}
    for row in rows.values():
        found = row['capability'] != 'SB-05'
        for name in row['evidence']['acceptance']:
            raw = module.safe_file(root, name).read_bytes()
            snapshot[name] = hashlib.sha256(raw).hexdigest()
            proof = strict_json(raw)
            if row['capability'] == 'SB-05':
                found = found or any(t['id'] == 'SB-05.complete-contract' and observation in t['logs'] for t in proof['tests'])
        if not found:
            raise Refused('Recovery result is not bound to every required current native placement proof')
    return snapshot


def unchanged_proofs(before, after):
    if before != after:
        raise Refused('Native proof contents changed during verification')


def verify_purge_receipt(receipt, expected_binding, currentAcceptance, sourceInventory):
    """Return exact verified receipt SHA256 or closed refusal; no purge effects."""
    try:
        exact(receipt, {'schema', 'format', 'binding', 'recovery_set', 'recovery_set_sha256',
                        'encrypted_artifact', 'destination', 'source', 'acceptance',
                        'native_observation'}, 'Complete native recovery receipt required')
        if type(receipt['schema']) is not int or receipt['schema'] != 1 or receipt['format'] != 'sbarbase-recovery-admission-v1':
            raise Refused('Unsupported recovery admission receipt')
        binding(expected_binding)
        require_identity(receipt['binding'], expected_binding, 'Current source lifecycle binding differs')
        computed = recovery_set_digest(receipt['recovery_set'])
        if receipt['recovery_set_sha256'] != computed:
            raise Refused('Recovery-set material digest differs')
        matches = [e for e in receipt['recovery_set']['environments'] if canonical(e['binding']) == canonical(expected_binding)]
        if len(matches) != 1:
            raise Refused('Exact current source resources are not covered')
        require_identity(matches[0]['resources'], sourceInventory, 'Exact current source resources are not covered')
        if catalog_inventory_digest(sourceInventory) != expected_binding['inventoryDigest']:
            raise Refused('Current Catalog resource identity changed')
        artifact(receipt['encrypted_artifact'])
        expected = expected_observation(receipt)
        # A boolean, status dictionary or caller callback cannot supply acceptance.
        if type(currentAcceptance) is not RegistryAcceptance:
            raise Refused('Canonical acceptance locator required; assertions and callbacks forbidden')
        placements = {e['binding']['placement'] for e in receipt['recovery_set']['environments']}
        placements.update(i['placement'] for i in receipt['destination']['placements'])
        root, module, rows = currentAcceptance.current(placements)
        exact(receipt['acceptance'], set(rows), 'Every current native placement acceptance proof required')
        expected_acceptance = {key: {'source': row['source'], 'evidence': row['evidence']['acceptance']}
                               for key, row in rows.items()}
        require_identity(receipt['source'], next(iter(rows.values()))['source'],
                         'Current source-bound capability proof differs')
        require_identity(receipt['acceptance'], expected_acceptance,
                         'Current source-bound capability proof differs')
        observation = receipt['native_observation']
        exact(observation, {'path', 'sha256'}, 'Checksummed native recovery result log required')
        if (not isinstance(observation['path'], str)
                or not observation['path'].startswith(module.PROOF_DIRECTORY + '/')
                or not isinstance(observation['sha256'], str) or not HASH.fullmatch(observation['sha256'])):
            raise Refused('Unsafe native recovery observation identity')
        before = proof_snapshot(root, module, rows, observation)
        raw = module.safe_file(root, observation['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != observation['sha256']:
            raise Refused('Native execution did not verify this exact recovery set and destination')
        require_identity(strict_json(raw), expected,
                         'Native execution did not verify this exact recovery set and destination')
        # Revalidate source, proof content and per-set association at a second observation.
        refreshed_root, refreshed_module, refreshed = currentAcceptance.current(placements)
        require_identity({key: {'source': row['source'], 'evidence': row['evidence']['acceptance']}
                          for key, row in refreshed.items()}, expected_acceptance,
                         'Current native acceptance changed during verification')
        unchanged_proofs(before, proof_snapshot(refreshed_root, refreshed_module, refreshed, observation))
        if hashlib.sha256(refreshed_module.safe_file(refreshed_root, observation['path']).read_bytes()).hexdigest() != observation['sha256']:
            raise Refused('Native recovery observation changed during verification')
        return hashlib.sha256(canonical(receipt)).hexdigest()
    except Refused:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise Refused('Malformed or unavailable native recovery receipt') from None
