"""Portable inventory validation; these fixtures do not establish native recovery."""
import copy
import hashlib
import unittest
from unittest.mock import patch
import subprocess
from uuid import UUID

import recovery_inventory as inventory

RUNTIME = 'e_' + 'a' * 24
INSTALLATION = '11111111-1111-1111-1111-111111111111'


def resource_fixture(runtime=RUNTIME, installation=INSTALLATION, seed=100):
    common = {'installation': installation, 'runtime': runtime}
    values = [{'kind': 'container', 'id': format(seed + i, '064x'), 'resource': str(UUID(int=seed + i)),
               **common, 'service': service} for i, service in enumerate(('database', 'auth', 'rest', 'storage'))]
    values += [{'kind': 'volume', 'id': 'owned_' + str(seed), 'createdAt': '2026-10-06T00:00:00Z',
                'resource': str(UUID(int=seed + 4)), **common},
               {'kind': 'directory', 'id': '/owned/' + str(seed) + '/' + runtime, 'device': 1, 'inode': seed,
                'marker': '.sbarbase-lifecycle-owner.json', 'resource': str(UUID(int=seed + 5)), **common}]
    return values


def shared_fixture(runtime=RUNTIME, installation=INSTALLATION, seed=100):
    values = resource_fixture(runtime, installation, seed)
    values = [r for r in values if r.get('service') in ('auth', 'rest') or r['kind'] == 'directory']
    files = {k: values[-1][k] for k in ('id', 'resource', 'device', 'inode', 'marker')}
    values.append({'kind': 'shared-database', 'id': runtime, 'resource': str(UUID(int=seed + 6)),
                   'installation': installation, 'runtime': runtime,
                   'identity': {'engine': {'id': format(seed + 7, '064x'), 'owner': installation, 'daemon': 'source-daemon'},
                                'database': {'name': runtime, 'oid': 100, 'ownerOid': 101}, 'files': files,
                                'roles': [{'name': runtime + '_' + k, 'oid': 102 + i, 'login': True}
                                          for i, k in enumerate(('auth', 'rest', 'storage'))],
                                'tenant': {'database': 'storage_metadata', 'oid': 107, 'id': runtime,
                                           'rowDigest': 'a' * 64, 'writers': [format(seed + 8, '064x')]}}})
    return values


def refresh(value):
    for env in value['environments']:
        env['binding']['inventoryDigest'] = inventory.catalog_inventory_digest(env['resources'])
    return value


def add_material(value, kind, runtime):
    name = (runtime or 'installation') + '/' + kind
    value['materials'].append({'name': name, 'kind': kind, 'runtime': runtime, 'bytes': 1,
                               'sha256': hashlib.sha256(kind.encode()).hexdigest()})
    return name


def manifest(count=1):
    runtimes = ['e_' + c * 24 for c in 'ab'[:count]]
    environments = []
    for i, runtime in enumerate(runtimes):
        source_resources = resource_fixture(runtime, seed=100 * (i + 1))
        bound = {'environment': str(UUID(int=1000 + i)), 'runtime': runtime,
                 'epoch': 3, 'coverage': 'dedicated-resources', 'placement': 'native-dedicated',
                 'inventoryDigest': inventory.catalog_inventory_digest(source_resources), 'placementDigest': 'b' * 64}
        environments.append({'binding': bound, 'resources': source_resources})
    materials = []
    for kind in sorted(inventory.INSTALLATION_MATERIALS):
        materials.append({'name': 'installation/' + kind, 'kind': kind, 'runtime': None,
                          'bytes': 1, 'sha256': hashlib.sha256(kind.encode()).hexdigest()})
    runtime_kinds = inventory.ENVIRONMENT_MATERIALS | {'auth-recovery-settings', 'runtime-secrets', 'service-settings', 'signing-key-state'}
    for runtime in runtimes:
        for kind in sorted(runtime_kinds):
            materials.append({'name': runtime + '/' + kind, 'kind': kind, 'runtime': runtime,
                              'bytes': 1, 'sha256': hashlib.sha256(kind.encode()).hexdigest()})
    by_kind = {(m['kind'], m['runtime']): m['name'] for m in materials}
    enabled = {'auth', 'rest', 'storage'}
    features = [{'id': key, 'runtime': runtime, 'enabled': key in enabled,
                 'materials': sorted(by_kind[k, runtime] for k in inventory.FEATURE_MATERIALS[key]) if key in enabled else []}
                for runtime in runtimes for key in sorted(inventory.FEATURE_MATERIALS) if key != 'management_identity']
    features.append({'id': 'management_identity', 'runtime': None, 'enabled': True,
                     'materials': sorted(by_kind[k, None] for k in inventory.FEATURE_MATERIALS['management_identity'])})
    rotated = ['postgres-root', 'storage-admin', 'storage-control', 'management-session']
    secrets = [{'id': key, 'policy': 'rotate-platform-access', 'material': by_kind['runtime-secrets', None]} for key in rotated]
    for runtime in runtimes:
        secrets += [{'id': 'runtime:' + runtime + ':' + key, 'policy': 'rotate-platform-access',
                     'material': by_kind['runtime-secrets', runtime]} for key in ('auth-db', 'rest-db', 'storage-db', 'jwt')]
        secrets.append({'id': 'storage-url-signing:' + runtime, 'policy': 'preserve-data-key', 'material': by_kind['signing-key-state', runtime]})
    secrets.append({'id': 'storage-data-encryption', 'policy': 'rewrap-data-key', 'material': by_kind['data-keys', None]})
    return {'schema': 1, 'format': 'sbarbase-installation-recovery-v1',
            'source_host': {'host': 'source-host', 'daemon': 'source-daemon', 'installation': INSTALLATION},
            'source': {'algorithm': 'sbarbase-public-source-v1', 'sha256': 'c' * 64, 'files': 20},
            'environments': environments,
            'materials': materials, 'features': features,
            'external_stores': [{'runtime': r, 'feature': 'storage', 'policy': 'included-file-bytes', 'store': None,
                                'objects': [], 'credentials_material': None} for r in runtimes],
            'secrets': secrets}


def external_copy_manifest():
    value = manifest()
    feature = next(i for i in value['features'] if i['id'] == 'external_object_store')
    feature.update(enabled=True, materials=[add_material(value, 'external-store-inventory', RUNTIME)])
    credentials = add_material(value, 'external-store-credentials', RUNTIME)
    name = add_material(value, 'external-object-bytes', RUNTIME)
    member = next(i for i in value['materials'] if i['name'] == name)
    value['external_stores'][0].update(policy='authenticated-independent-copy',
        store={'provider': 's3', 'endpoint': 'https://store.invalid', 'bucket': 'data'},
        credentials_material=credentials,
        objects=[{'key': 'object', 'version': 'immutable-version', 'bytes': member['bytes'], 'sha256': member['sha256'], 'material': name}])
    return value


class InventoryTests(unittest.TestCase):
    def test_complete_portable_shape_is_accepted_without_effects(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('No external effects')):
            value = manifest()
            self.assertIs(inventory.validate_manifest(value), value)

    def test_set_digest_is_stable_under_object_key_order(self):
        value = manifest()
        reversed_keys = dict(reversed(list(value.items())))
        self.assertEqual(inventory.recovery_set_digest(value), inventory.recovery_set_digest(reversed_keys))

    def test_changed_member_bytes_digest_changes_set_digest(self):
        value = manifest(); changed = copy.deepcopy(value)
        changed['materials'][0]['sha256'] = 'd' * 64
        self.assertNotEqual(inventory.recovery_set_digest(value), inventory.recovery_set_digest(changed))

    def test_environment_database_and_files_are_both_mandatory(self):
        for kind in ('application-database', 'storage-object-files'):
            value = manifest(); value['materials'] = [m for m in value['materials'] if m['kind'] != kind]
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_installation_secrets_and_catalog_cannot_be_omitted(self):
        for kind in ('catalog', 'runtime-secrets', 'data-keys'):
            value = manifest(); value['materials'] = [m for m in value['materials'] if m['kind'] != kind]
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_unknown_duplicate_or_missing_feature_refuses(self):
        for action in ('unknown', 'duplicate', 'missing'):
            value = manifest()
            if action == 'unknown': value['features'][0]['id'] = 'new-native-feature'
            elif action == 'duplicate': value['features'].append(copy.deepcopy(value['features'][0]))
            else: value['features'].pop()
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_enabled_functions_cannot_silently_drop_code_and_secrets(self):
        value = manifest(); next(i for i in value['features'] if i['id'] == 'functions')['enabled'] = True
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_vault_requires_a_classified_recoverable_data_key(self):
        value = manifest(); entry = next(i for i in value['features'] if i['id'] == 'vault')
        entry.update(enabled=True, materials=[add_material(value, 'data-keys', RUNTIME), add_material(value, 'extension-config', RUNTIME)])
        with self.assertRaisesRegex(inventory.Refused, 'Vault'): inventory.validate_manifest(value)

    def test_missing_rotation_or_reused_platform_credentials_refuse(self):
        for action in ('remove', 'preserve'):
            value = manifest(); selected = next(i for i in value['secrets'] if i['id'] == 'postgres-root')
            if action == 'remove': value['secrets'].remove(selected)
            else: selected['policy'] = 'preserve-data-key'
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_signed_url_material_cannot_rotate_away(self):
        value = manifest(); next(i for i in value['secrets'] if i['id'].startswith('storage-url-signing:'))['policy'] = 'rotate-platform-access'
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_unknown_external_policy_and_contradictory_feature_state_refuse(self):
        for action in ('unknown', 'external'):
            value = manifest()
            if action == 'unknown': value['external_stores'][0]['policy'] = 'trust-current-bucket'
            else: next(i for i in value['features'] if i['id'] == 'external_object_store')['enabled'] = True
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_external_objects_require_version_and_sha256(self):
        value = {'runtime': RUNTIME, 'feature': 'storage', 'policy': 'immutable-external-dependency', 'store': {'provider': 's3', 'endpoint': 'https://store.invalid', 'bucket': 'data'},
                 'objects': [{'key': 'object', 'version': '', 'bytes': 4, 'sha256': 'a' * 64, 'material': None}],
                 'credentials_material': 'installation/runtime-secrets'}
        with self.assertRaises(inventory.Refused): inventory.external(value)

    def test_traversal_absolute_duplicate_and_unknown_material_refuse(self):
        for name in ('../escape', '/absolute', 'installation//empty'):
            value = manifest(); value['materials'][0]['name'] = name
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        value = manifest(); value['materials'].append(copy.deepcopy(value['materials'][0]))
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        value = manifest(); value['materials'][0]['kind'] = 'unknown-state'
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_wrong_runtime_epoch_boolean_and_fixture_coverage_refuse(self):
        for key, replacement in (('runtime', 'source-db'), ('epoch', True), ('coverage', 'fixture')):
            value = manifest(); value['environments'][0]['binding'][key] = replacement
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_duplicate_application_and_foreign_installation_refuse(self):
        value = manifest(); value['environments'].append(copy.deepcopy(value['environments'][0]))
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        value = manifest(); value['environments'][0]['resources'][0]['installation'] = '99999999-9999-9999-9999-999999999999'
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_malformed_json_records_are_closed_refusals(self):
        for value in (None, [], {}, {'schema': True}):
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        value = manifest(); value['features'][0]['materials'] = [[]]
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_two_complete_runtime_scopes_are_admitted_structurally(self):
        value = manifest(2)
        self.assertIs(inventory.validate_manifest(value), value)

    def test_complete_shared_snapshot_binds_tenant_files(self):
        value = manifest(); env = value['environments'][0]
        env['resources'] = shared_fixture()
        env['binding'].update(placement='legacy-shared', coverage='complete-shared-resources')
        self.assertIs(inventory.validate_manifest(refresh(value)), value)
        env['resources'][2]['inode'] += 1
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(refresh(value))

    def test_missing_kind_specific_identity_and_incomplete_coverage_refuse(self):
        for action in ('mutable-container', 'directory-inode', 'volume-created', 'unknown', 'incomplete', 'shared-identity'):
            value = manifest(); env = value['environments'][0]
            if action == 'mutable-container': env['resources'][0]['id'] = 'mutable-name'
            elif action == 'directory-inode': env['resources'][-1]['inode'] = True
            elif action == 'volume-created': env['resources'][-2].pop('createdAt')
            elif action == 'unknown': env['resources'][0]['accepted'] = True
            elif action == 'incomplete': env['resources'] = env['resources'][:1]
            else:
                env['resources'] = shared_fixture(); env['resources'][-1].pop('identity')
                env['binding'].update(placement='legacy-shared', coverage='complete-shared-resources')
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(refresh(value))

    def test_duplicate_resource_uuid_and_cross_runtime_alias_refuse(self):
        value = manifest(); env = value['environments'][0]
        env['resources'][1]['resource'] = env['resources'][0]['resource']
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(refresh(value))
        value = manifest(2); value['environments'][1]['resources'][0]['id'] = value['environments'][0]['resources'][0]['id']
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(refresh(value))

    def test_every_environment_inventory_digest_is_checked(self):
        for index in (0, 1):
            value = manifest(2); value['environments'][index]['binding']['inventoryDigest'] = '0' * 64
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_runtime_capability_material_cannot_cover_another_runtime(self):
        value = manifest(2)
        one = [add_material(value, kind, RUNTIME) for kind in ('functions-bundle', 'functions-secrets')]
        for row in value['features']:
            if row['id'] == 'functions': row.update(enabled=True, materials=one)
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        value = manifest(2); value['features'] = [r for r in value['features'] if not (r['id'] == 'functions' and r['runtime'] != RUNTIME)]
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_scoped_independent_copy_binds_exact_member_bytes(self):
        value = external_copy_manifest()
        self.assertIs(inventory.validate_manifest(value), value)
        for key, replacement in (('bytes', 2), ('sha256', 'f' * 64), ('material', 'installation/catalog')):
            value = external_copy_manifest(); value['external_stores'][0]['objects'][0][key] = replacement
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        value = external_copy_manifest(); value['external_stores'][0]['credentials_material'] = 'installation/catalog'
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_external_scope_omission_or_credential_address_refuses(self):
        value = manifest(2); value['external_stores'].pop()
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
        for address in ('http://store.invalid', 'https://secret@store.invalid', 'https://store.invalid?token=hidden', 'https://store.invalid/#credential'):
            value = external_copy_manifest(); value['external_stores'][0]['store']['endpoint'] = address
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_secret_material_cannot_change_runtime_or_purpose(self):
        value = manifest(2); entry = next(i for i in value['secrets'] if i['id'] == 'runtime:' + RUNTIME + ':auth-db')
        entry['material'] = 'installation/catalog'
        with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)

    def test_dedicated_services_are_exactly_database_auth_rest_storage(self):
        for action in ('missing-storage', 'duplicate-auth', 'extra-container'):
            value = manifest(); rows = value['environments'][0]['resources']
            if action == 'missing-storage': rows[:] = [r for r in rows if r.get('service') != 'storage']
            elif action == 'duplicate-auth': next(r for r in rows if r.get('service') == 'storage')['service'] = 'auth'
            else:
                duplicate = copy.deepcopy(rows[0]); duplicate.update(id='f' * 64, resource=str(UUID(int=999)))
                rows.append(duplicate)
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(refresh(value))

    def test_malformed_bracket_and_invalid_port_are_typed_refusals(self):
        for endpoint in ('https://[invalid', 'https://store.invalid:bad', 'https://store.invalid:0', 'https://store.invalid:65536'):
            value = external_copy_manifest(); value['external_stores'][0]['store']['endpoint'] = endpoint
            with self.assertRaises(inventory.Refused): inventory.external(value['external_stores'][0])
            with self.assertRaises(inventory.Refused): inventory.validate_manifest(value)
