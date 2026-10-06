#!/usr/bin/env python3
"""Offline capability contract and detached execution-proof verification."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


REGISTRY = 'deploy/capabilities/registry.json'
PROOF_DIRECTORY = 'deploy/capabilities/proof'
REFERENCE = 'docs/engineering/benchmarks/supabase-v0.8.2.source.json'
REFERENCE_BUNDLE = 'docs/engineering/benchmarks/supabase-v0.8.2.bundle.json'
LEDGER = 'docs/engineering/gauntlet-ledger.json'
COMMIT = '564eab8ad7840b13324f68b1bfac074ef8d51c21'
PLACEMENTS = ('legacy-shared', 'native-dedicated', 'cloud-only')
REQUIRED_CAPABILITIES = {f'SB-{number:02d}' for number in range(1, 19) if number != 13} | {
    'SB-13a', 'SB-13b', 'sql-rest-rpc-rls', 'auth-core', 'auth-advanced', 'api-keys',
    'storage-standard', 'storage-advanced', 'realtime', 'functions', 'studio',
    'database-features', 'native-cron-effects', 'backups-pitr-replicas',
    'management-api', 'managed-only', 'foundation-reference-distribution', 'public-release'}
NATIVE_ADMISSION_TESTS = {'SB-13a.' + name for name in (
    'original-startup', 'native-identities', 'cron-http-effects', 'sdk-flow', 'fenced-recovery')}
REQUIRED_SLICE_COVERAGE = {
    'G0': {'SB-01', 'foundation-reference-distribution'},
    'G12': {'SB-03', 'public-release'},
}
FULL_SLICE_TESTS = {
    'foundation-reference-distribution': {
        'public-clone-immutable-linux-build', 'no-private-source-runtime',
        'reference-image-packet', 'release-capability-evidence-identity',
        'required-missing-check-refusal', 'distribution-reproducibility'},
    'public-release': {
        'claimed-profile-acceptance', 'all-claimed-slices-current', 'public-pilot',
        'seven-day-soak', 'licenses', 'contributor-setup',
        'vulnerability-reporting', 'support-windows'},
}
EXECUTION_IDENTITY_FIELDS = ('schema_version', 'scope', 'source', 'registry_sha256',
                             'bindings_sha256', 'reference', 'host_profile',
                             'observed_at', 'expires_at')
# Fixed public source boundary: callers cannot narrow it through a proof packet.
SOURCE_DIRECTORIES = ('src', 'lab', 'deploy', 'ui', 'tests', '.github')
SOURCE_FILES = ('package.json', 'bun.lock', 'compose.yaml', 'Dockerfile',
                '.dockerignore', 'tsconfig.json', 'tsconfig.control.json',
                'vite.config.mts', 'release.json', 'LICENSE', 'NOTICE',
                'PROJECT_GOAL.md', REFERENCE, REFERENCE_BUNDLE, LEDGER,
                'docs/engineering/plans/2026-10-03-gauntlet-execution-method.md',
                'docs/engineering/plans/2026-10-03-product-and-portability-plan.md')
REQUIRED_BINDINGS = {
    'reference': REFERENCE,
    'reference-bundle': REFERENCE_BUNDLE,
    'images': 'lab/images.lock.json',
    'storage-images': 'lab/storage-image.lock.json',
    'realtime-images': 'lab/realtime-image.lock.json',
    'functions-images': 'lab/functions-image.lock.json',
    'studio-images': 'lab/studio-image.lock.json',
    'host-images': 'lab/distro-image.lock.json',
    'compose': 'compose.yaml',
    'host-config': 'lab/docker_profile.py',
    'catalog-schema': 'src/control/catalog.ts',
    'placement-config': 'src/control/placement.ts',
    'native-fixture': 'lab/fixtures/native_cron_setup.json',
    'registry-schema': 'deploy/capabilities/registry.schema.json',
    'evidence-schema': 'deploy/capabilities/evidence.schema.json',
}
_reference_spec = importlib.util.spec_from_file_location(
    'capability_reference_bundle', Path(__file__).resolve().parent / 'reference_bundle.py')
reference_bundle = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(reference_bundle)


class Refusal(ValueError):
    """A malformed or incomplete contract must produce a visible refusal."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode('ascii')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_file(root, name):
    """Reject traversal, absolute paths and every symlink component."""
    if (not isinstance(name, str) or not name or '\\' in name
            or any(part in ('', '.', '..') for part in name.split('/'))
            or PurePosixPath(name).is_absolute()):
        raise Refusal('unsafe repository path')
    target = root
    for part in name.split('/'):
        target = target / part
        if target.is_symlink():
            raise Refusal(f'{name}: symlink is forbidden')
    if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
        raise Refusal(f'{name}: missing regular file')
    return target


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal('duplicate JSON key')
        result[key] = value
    return result


def read_json(root, name):
    try:
        return json.loads(safe_file(root, name).read_text(encoding='utf-8'),
                          object_pairs_hook=no_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal('nonfinite JSON number')))
    except (OSError, UnicodeError, ValueError) as error:
        raise Refusal(f'{name}: cannot read JSON ({error})') from error


def validate_schema(value, schema, definitions=None, path='$'):
    """Validate the closed JSON Schema subset used by the shipped contracts."""
    if not isinstance(schema, dict):
        raise Refusal('invalid schema document')
    definitions = definitions if definitions is not None else schema.get('$defs', {})
    if not isinstance(definitions, dict):
        raise Refusal('invalid schema definitions')
    if '$ref' in schema:
        return validate_schema(value, definitions[schema['$ref'].split('/')[-1]], definitions, path)
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool}
    if 'type' in schema and type(value) is not types[schema['type']]:
        raise Refusal(f'{path}: expected {schema["type"]}')
    if 'enum' in schema and value not in schema['enum']:
        raise Refusal(f'{path}: unsupported value')
    if 'const' in schema and value != schema['const']:
        raise Refusal(f'{path}: constant mismatch')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                raise Refusal(f'{path}: missing {key}')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise Refusal(f'{path}: unknown field')
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], definitions, f'{path}.{key}')
            elif isinstance(schema.get('additionalProperties'), dict):
                validate_schema(item, schema['additionalProperties'], definitions, f'{path}.{key}')
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            raise Refusal(f'{path}: empty or incomplete inventory')
        if schema.get('uniqueItems') and len({canonical(item) for item in value}) != len(value):
            raise Refusal(f'{path}: duplicate inventory')
        for number, item in enumerate(value):
            validate_schema(item, schema.get('items', {}), definitions, f'{path}[{number}]')
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            raise Refusal(f'{path}: empty string')
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise Refusal(f'{path}: pattern mismatch')
    elif type(value) is int and value < schema.get('minimum', value):
        raise Refusal(f'{path}: below minimum')


def source_identity(root):
    """Hash path inventory, complete bytes and permission modes, without host paths."""
    root = Path(root).resolve()
    names = set(SOURCE_FILES)
    for directory in SOURCE_DIRECTORIES:
        start = root / directory
        if start.is_symlink() or not start.is_dir():
            raise Refusal(f'{directory}: missing regular source directory')
        for current, directories, files in os.walk(start, followlinks=False):
            for entry in directories + files:
                if (Path(current) / entry).is_symlink():
                    raise Refusal('source inventory contains symlink')
            directories[:] = [entry for entry in directories if entry != '__pycache__'
                              and (Path(current) / entry).relative_to(root).as_posix() != PROOF_DIRECTORY]
            for entry in files:
                if not entry.endswith(('.pyc', '.pyo')):
                    names.add((Path(current) / entry).relative_to(root).as_posix())
    inventory = []
    for name in sorted(names):
        file = safe_file(root, name)
        data = file.read_bytes()
        inventory.append({'path': name, 'sha256': sha(data), 'bytes': len(data),
                          'mode': stat.S_IMODE(file.stat().st_mode)})
    return {'algorithm': 'sbarbase-public-source-v1', 'sha256': sha(canonical(inventory)),
            'files': len(inventory)}


def checked_bindings(root, registry):
    bindings = registry['bindings']
    if len({item['id'] for item in bindings}) != len(bindings):
        raise Refusal('duplicate binding id')
    by_id = {item['id']: item for item in bindings}
    if {key: value['path'] for key, value in by_id.items()} != REQUIRED_BINDINGS:
        raise Refusal('required config, lock, schema or fixture binding inventory differs')
    for item in bindings:
        if sha(safe_file(root, item['path']).read_bytes()) != item['sha256']:
            raise Refusal(f'{item["id"]}: binding checksum mismatch')
    return sha(canonical(bindings))


def load_registry(root):
    registry = read_json(root, REGISTRY)
    validate_schema(registry, read_json(root, 'deploy/capabilities/registry.schema.json'))
    if registry['schema_version'] != 1 or registry['source_algorithm'] != 'sbarbase-public-source-v1':
        raise Refusal('unsupported registry version or source algorithm')
    reference = read_json(root, REFERENCE)
    if not isinstance(reference, dict):
        raise Refusal('invalid pinned source record')
    expected = {'repository': 'https://github.com/supabase/supabase',
                'tag': 'self-hosted/v0.8.2', 'commit': COMMIT}
    if registry['reference'] != expected or any(reference.get(key) != value for key, value in expected.items()):
        raise Refusal('pinned Supabase reference mismatch')
    package = read_json(root, 'package.json')
    if not isinstance(package, dict) or registry['version'] != package.get('version'):
        raise Refusal('Sbarbase version mismatch')
    checked_bindings(root, registry)
    capabilities = registry['capabilities']
    if not capabilities or len({item['id'] for item in capabilities}) != len(capabilities):
        raise Refusal('empty or duplicate capability inventory')
    if not REQUIRED_CAPABILITIES.issubset({item['id'] for item in capabilities}):
        raise Refusal('required roadmap or feature capability inventory is incomplete')
    for slice_id, required_ids in REQUIRED_SLICE_COVERAGE.items():
        if not required_ids.issubset({item['id'] for item in capabilities
                                      if slice_id in item['gauntlet_slices']}):
            raise Refusal(f'{slice_id}: mandatory complete slice coverage is incomplete')
    bundle = read_json(root, REFERENCE_BUNDLE)
    if not isinstance(bundle, dict) or any(bundle.get(key) != value for key, value in expected.items()):
        raise Refusal('pinned Supabase reference bundle mismatch')
    ledger = read_json(root, LEDGER)
    if not isinstance(ledger, dict):
        raise Refusal('invalid historical ledger')
    for capability in capabilities:
        if set(capability['placements']) != set(PLACEMENTS):
            raise Refusal('incomplete or unknown placement inventory')
        for name in capability['history']:
            if name['path'] != LEDGER or not name['pointer'].startswith('/'):
                raise Refusal('historical evidence must reference the existing ledger')
            cursor = ledger
            try:
                for key in name['pointer'][1:].split('/'):
                    cursor = cursor[int(key)] if isinstance(cursor, list) else cursor[key]
            except (KeyError, IndexError, ValueError, TypeError) as error:
                raise Refusal('missing historical ledger pointer') from error
        for placement in capability['placements'].values():
            if (placement['implementation']['state'] == 'implemented'
                    and not placement['implementation']['sources']):
                raise Refusal('implemented capability has no source inventory')
            for name in placement['implementation']['sources']:
                safe_file(root, name)
            ids = [test['id'] for test in placement['tests']]
            if not ids or len(set(ids)) != len(ids):
                raise Refusal('empty or duplicate required test inventory')
            if (capability['id'] == 'SB-13a' and {test['id'] for test in placement['tests']
                    if test['gate'] == 'acceptance'} != NATIVE_ADMISSION_TESTS):
                raise Refusal('native admission must include startup, identity, effects, SDK and recovery')
            if capability['id'] in FULL_SLICE_TESTS:
                expected_ids = {capability['id'] + '.' + name for name in FULL_SLICE_TESTS[capability['id']]}
                if {test['id'] for test in placement['tests'] if test['gate'] == 'acceptance'} != expected_ids:
                    raise Refusal('complete slice mandatory test inventory differs')
            for test in placement['tests']:
                if test['runner']:
                    safe_file(root, test['runner'])
                if not test['fixtures'] or not set(test['fixtures']).issubset(REQUIRED_BINDINGS):
                    raise Refusal('missing or unknown fixture/config binding')
                if (test['kind'] != 'runtime' and
                        (capability['id'] != 'SB-01' or test['gate'] != 'acceptance')):
                    raise Refusal('source or unit evidence cannot accept runtime capabilities')
                if test['kind'] == 'runtime' and test['runner'].startswith('lab/test_'):
                    raise Refusal('unit regression runner cannot accept runtime contract')
            for gate in ('acceptance', 'production'):
                if not any(test['gate'] == gate for test in placement['tests']):
                    raise Refusal(f'{gate}: missing required test inventory')
    return registry


def date(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('timezone missing')
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as error:
        raise Refusal('invalid evidence timestamp') from error


def verify_proof(root, registry, capability, placement_name, gate, name, now=None, identity=None):
    if not name.startswith(PROOF_DIRECTORY + '/'):
        raise Refusal('proof must use the detached repository proof directory')
    proof = read_json(root, name)
    evidence_schema = read_json(root, 'deploy/capabilities/evidence.schema.json')
    validate_schema(proof, evidence_schema)
    placement = capability['placements'][placement_name]
    expected_scope = {'capability': capability['id'], 'placement': placement_name, 'gate': gate,
                      'contract': placement['contract']}
    if proof['scope'] != expected_scope:
        raise Refusal('evidence scope or placement mismatch')
    if proof['source'] != (identity or source_identity(root)):
        raise Refusal('source identity mismatch')
    if proof['registry_sha256'] != sha(canonical(registry)):
        raise Refusal('registry contract mismatch')
    if proof['bindings_sha256'] != checked_bindings(root, registry) or proof['reference'] != registry['reference']:
        raise Refusal('config, fixture or reference mismatch')
    if proof['host_profile'] != placement['host_profile']:
        raise Refusal('host profile mismatch')
    if capability['id'] == 'foundation-reference-distribution':
        try:
            metadata_errors = reference_bundle.packet_errors(read_json(root, REFERENCE_BUNDLE))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
            raise Refusal('reference image packet is malformed') from error
        if metadata_errors:
            raise Refusal('reference image packet remains incomplete: ' + '; '.join(metadata_errors))
    now = now or datetime.now(timezone.utc)
    observed = date(proof['observed_at'])
    expires = date(proof['expires_at'])
    if not observed <= now < expires or not observed < expires <= observed + timedelta(days=30):
        raise Refusal('missing, stale or future evidence window')
    required = [item for item in placement['tests'] if item['gate'] == gate]
    if not required or any(not item['runner'] or not item['command'] for item in required):
        raise Refusal('required execution runner is unimplemented')
    expected_tests = {test['id']: test for test in required}
    actual = proof['tests']
    if len(actual) != len(expected_tests) or {test['id'] for test in actual} != set(expected_tests):
        raise Refusal('required test inventory mismatch')
    used_logs = set()
    for test in actual:
        requirement = expected_tests[test['id']]
        if (test['kind'] != requirement['kind'] or test['contract'] != requirement['contract']
                or test['fixtures'] != requirement['fixtures']):
            raise Refusal('test kind, scope or fixture mismatch')
        if (test['outcome'] != 'passed' or type(test['exit_code']) is not int or test['exit_code'] != 0
                or type(test['assertions']) is not int or test['assertions'] < 1
                or any(type(test[key]) is not int or test[key] != 0 for key in ('failures', 'errors', 'skipped'))):
            raise Refusal('failed, skipped or empty execution evidence')
        if test['command'] != requirement['command']:
            raise Refusal('execution command does not bind required runner')
        for log in test['logs']:
            if not log['path'].startswith(PROOF_DIRECTORY + '/') or log['path'] in used_logs:
                raise Refusal('unsafe or reused raw log')
            used_logs.add(log['path'])
            content = safe_file(root, log['path']).read_bytes()
            if not content.strip() or sha(content) != log['sha256']:
                raise Refusal('missing, empty or tampered raw log')
        # A checksummed raw observation binds the reported result to its exact test.
        if test['observation'] not in test['logs']:
            raise Refusal('raw observation is not a checksummed log')
        observation = read_json(root, test['observation']['path'])
        try:
            validate_schema(observation, {'$ref': '#/$defs/raw_observation'}, evidence_schema['$defs'])
        except Refusal as error:
            raise Refusal(f'raw observation invalid: {error}') from error
        expected = {key: test[key] for key in ('id', 'kind', 'contract', 'fixtures', 'outcome',
                                              'exit_code', 'assertions', 'failures', 'errors', 'skipped', 'command')}
        expected['execution'] = {key: proof[key] for key in EXECUTION_IDENTITY_FIELDS}
        if observation != expected:
            raise Refusal('raw observation disagrees with reported test')
    return proof


def check(root, require_acceptance=False, selections=None, now=None):
    """Return a derived status view and all refusals; historical status is context."""
    root = Path(root).resolve()
    errors, rows = [], []
    try:
        registry = load_registry(root)
        identity = source_identity(root)
        for capability in registry['capabilities']:
            for placement_name, placement in capability['placements'].items():
                row = {'capability': capability['id'], 'placement': placement_name,
                       'title': capability['title'], 'contract': placement['contract'],
                       'source': identity,
                       'host_profile': placement['host_profile'], 'limitations': placement['limitations'],
                       'required_tests': placement['tests'], 'history': capability['history'],
                       'evidence': {gate: placement[gate]['evidence'] for gate in ('acceptance', 'production')},
                       'implementation': placement['implementation']['state'],
                       'acceptance': 'unproven', 'production': 'unproven', 'errors': []}
                for gate in ('acceptance', 'production'):
                    claim = placement[gate]
                    verified = []
                    for name in claim['evidence']:
                        try:
                            verify_proof(root, registry, capability, placement_name, gate,
                                         name, now=now, identity=identity)
                            verified.append(name)
                        except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError) as error:
                            row['errors'].append(f'{gate}: {error}')
                    if claim['state'] == 'claimed' and not verified:
                        row['errors'].append(f'{gate}: claimed state lacks valid proof')
                    if verified and not row['errors']:
                        row[gate] = 'accepted'
                if row['production'] == 'accepted' and row['acceptance'] != 'accepted':
                    row['production'] = 'unproven'
                    row['errors'].append('production requires valid acceptance proof')
                if row['acceptance'] == 'accepted' and row['implementation'] != 'implemented':
                    row['acceptance'] = row['production'] = 'unproven'
                    row['errors'].append('acceptance requires implemented source')
                selected = selections is None or (capability['id'], placement_name) in selections
                if require_acceptance and selected and row['acceptance'] != 'accepted':
                    row['errors'].append('acceptance: required current proof missing')
                errors.extend(f'{capability["id"]}/{placement_name}: {error}' for error in row['errors'])
                rows.append(row)
        if source_identity(root) != identity:
            for row in rows:
                row['acceptance'] = row['production'] = 'unproven'
            errors.append('source identity changed during verification')
        if selections is not None and not selections.issubset({(row['capability'], row['placement']) for row in rows}):
            errors.append('unknown capability or placement selection')
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError) as error:
        for row in rows:
            row['acceptance'] = row['production'] = 'unproven'
        errors.append(f'capability registry: {error}')
    return rows, errors


def coverage(root, slice_id):
    """A passed planning slice needs all declared current acceptance coverage."""
    try:
        registry = load_registry(Path(root).resolve())
        selections = {(item['id'], name) for item in registry['capabilities']
                      if (slice_id in item['gauntlet_slices'] or
                          (slice_id == 'G12' and item['gauntlet_slices'])) for name in PLACEMENTS
                      if name != 'cloud-only'}
        if not selections:
            return [f'{slice_id}: no capability coverage contract']
        return check(root, require_acceptance=True, selections=selections)[1]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError) as error:
        return [f'{slice_id}: invalid coverage contract ({error})']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('validate', 'status', 'accept', 'identity'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--capability')
    parser.add_argument('--placement', choices=PLACEMENTS)
    args = parser.parse_args(argv)
    if bool(args.capability) != bool(args.placement):
        parser.error('--capability and --placement are required together')
    if args.command == 'identity':
        try:
            print(json.dumps(source_identity(args.root), sort_keys=True, indent=2))
            return 0
        except (OSError, ValueError) as error:
            print(json.dumps({'errors': [str(error)]}))
            return 1
    selections = {(args.capability, args.placement)} if args.capability else None
    rows, errors = check(args.root, require_acceptance=args.command == 'accept', selections=selections)
    output = {'schema_version': 1, 'status': rows, 'errors': errors, 'history_ledger': LEDGER,
              'proof_trust': 'self-attested; checksums do not prove execution or reviewer independence'}
    if rows:
        try:
            registry = load_registry(args.root.resolve())
            bundle = read_json(args.root.resolve(), REFERENCE_BUNDLE)
            try:
                bundle_errors = reference_bundle.packet_errors(bundle)
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                bundle_errors = ['malformed reference metadata packet']
            output.update({'source': source_identity(args.root), 'reference': registry['reference'],
                           'bindings': registry['bindings'], 'version': registry['version'],
                           'reference_bundle': {'path': REFERENCE_BUNDLE,
                                                'status': bundle.get('status', 'unknown'),
                                                'metadata_errors': bundle_errors}})
            if output['source'] != rows[0]['source']:
                errors.append('source identity changed during report')
                for row in rows:
                    row['acceptance'] = row['production'] = 'unproven'
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError) as error:
            errors.append(f'capability registry changed during report: {error}')
            for row in rows:
                row['acceptance'] = row['production'] = 'unproven'
    print(json.dumps(output, sort_keys=True, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
