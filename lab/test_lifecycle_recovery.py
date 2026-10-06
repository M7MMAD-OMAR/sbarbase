"""Private receipt and actual current canonical admission refusals.

Synthetic inventory exercises metadata validation only. It has no native proof,
encrypted artifact or real resource, and can never supply production admission.
"""
from pathlib import Path
import hashlib
import os
import tempfile
import unittest
from uuid import UUID
from unittest.mock import MagicMock, patch

import lifecycle_recovery as recovery


def synthetic_registry_denial_fixture():
    """Canonical metadata only, deliberately without any accepted native evidence."""
    import recovery_inventory as inventory
    runtime = 'e_' + 'a' * 24
    source_installation, destination_installation = str(UUID(int=100)), str(UUID(int=200))
    identity = {'algorithm': 'sbarbase-public-source-v1', 'sha256': '0' * 64, 'files': 1}

    def resources(installation, offset):
        common = {'installation': installation, 'runtime': runtime}
        values = [{'kind': 'container', 'id': format(offset + index, 'x') * 64,
                   'resource': str(UUID(int=offset + index)), 'service': service, **common}
                  for index, service in enumerate(('database', 'auth', 'rest', 'storage'))]
        values.extend([
            {'kind': 'volume', 'id': 'synthetic-never-enrolled-' + str(offset),
             'resource': str(UUID(int=offset + 4)), 'createdAt': '2026-10-06T00:00:00Z', **common},
            {'kind': 'directory', 'id': '/synthetic-never-enrolled/' + str(offset) + '/' + runtime,
             'resource': str(UUID(int=offset + 5)), 'device': 1, 'inode': 1,
             'marker': '.sbarbase-lifecycle-owner.json', **common},
        ])
        return values

    source, destination = resources(source_installation, 1), resources(destination_installation, 7)
    bound = {'environment': str(UUID(int=300)), 'runtime': runtime, 'epoch': 1,
             'coverage': 'dedicated-resources', 'placement': 'native-dedicated',
             'inventoryDigest': inventory.catalog_inventory_digest(source), 'placementDigest': '0' * 64}
    scopes = {(kind, None) for kind in inventory.INSTALLATION_MATERIALS}
    scopes.update((kind, runtime) for kind in inventory.ENVIRONMENT_MATERIALS)
    enabled = {'management_identity', 'auth', 'rest', 'storage'}
    for feature in enabled:
        scope = None if feature == 'management_identity' else runtime
        scopes.update((kind, scope) for kind in inventory.FEATURE_MATERIALS[feature])
    material_names = {(kind, scope): ('installation' if scope is None else 'application') + '/' + kind
                      for kind, scope in scopes}
    payload = b'Synthetic metadata only, no retained data or accepted proof'
    materials = [{'name': material_names[(kind, scope)], 'kind': kind, 'runtime': scope,
                  'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
                 for kind, scope in sorted(scopes, key=lambda item: (item[0], item[1] or ''))]
    features = [{'id': feature, 'runtime': None if feature == 'management_identity' else runtime,
                 'enabled': feature in enabled,
                 'materials': sorted(material_names[(kind, None if feature == 'management_identity' else runtime)]
                                     for kind in inventory.FEATURE_MATERIALS[feature]) if feature in enabled else []}
                for feature in sorted(inventory.FEATURE_MATERIALS)]
    secrets = [{'id': name, 'policy': 'rotate-platform-access',
                'material': material_names[('runtime-secrets', None)]}
               for name in ('postgres-root', 'storage-admin', 'storage-control', 'management-session')]
    secrets.extend({'id': 'runtime:' + runtime + ':' + name, 'policy': 'rotate-platform-access',
                    'material': material_names[('runtime-secrets', runtime)]}
                   for name in ('auth-db', 'rest-db', 'storage-db', 'jwt'))
    secrets.extend([
        {'id': 'storage-url-signing:' + runtime, 'policy': 'preserve-data-key',
         'material': material_names[('signing-key-state', runtime)]},
        {'id': 'storage-data-encryption', 'policy': 'preserve-data-key',
         'material': material_names[('data-keys', None)]},
    ])
    manifest = {'schema': 1, 'format': 'sbarbase-installation-recovery-v1',
                'source_host': {'host': 'synthetic-source', 'daemon': 'synthetic-source-daemon',
                                'installation': source_installation},
                'source': identity, 'environments': [{'binding': bound, 'resources': source}],
                'materials': materials, 'features': features, 'secrets': secrets,
                'external_stores': [{'runtime': runtime, 'feature': 'storage', 'policy': 'included-file-bytes',
                                    'store': None, 'objects': [], 'credentials_material': None}]}
    receipt = {'schema': 1, 'format': 'sbarbase-recovery-admission-v1', 'binding': bound,
               'recovery_set': manifest, 'recovery_set_sha256': inventory.recovery_set_digest(manifest),
               'encrypted_artifact': {'format': 'sbarbase-recovery-stream-v1', 'bytes': 1, 'sha256': '0' * 64},
               'destination': {'host': {'host': 'synthetic-target', 'daemon': 'synthetic-target-daemon',
                                        'installation': destination_installation}, 'resources': destination,
                               'placements': [{'runtime': runtime, 'placement': 'native-dedicated',
                                               'coverage': 'dedicated-resources'}], 'empty_inventory_sha256': '0' * 64},
               'source': identity, 'acceptance': {},
               'native_observation': {'path': 'synthetic-no-native-proof.json', 'sha256': '0' * 64}}
    return receipt, bound, source


class PrivateReceiptTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.path = self.root / 'receipt.json'
        self.path.write_text('{"schema":1}')
        self.path.chmod(0o600)

    def tearDown(self):
        self.folder.cleanup()

    def test_private_bounded_receipt_reads_exact_object(self):
        self.assertEqual(recovery.private_receipt(str(self.path)), {'schema': 1})

    def test_world_readable_receipt_refuses(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'ownership'):
            recovery.private_receipt(str(self.path))

    def test_symlink_final_and_parent_refuse(self):
        alias = self.root / 'alias.json'
        alias.symlink_to(self.path)
        with self.assertRaises(OSError):
            recovery.private_receipt(str(alias))
        directory = self.root / 'alias-directory'
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            recovery.private_receipt(str(directory / self.path.name))

    def test_hardlinked_receipt_refuses(self):
        os.link(self.path, self.root / 'second.json')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            recovery.private_receipt(str(self.path))

    def test_empty_oversized_and_non_object_receipts_refuse(self):
        for payload in (b'', b' ' * (recovery.MAX_RECEIPT_BYTES + 1), b'[]'):
            self.path.write_bytes(payload)
            with self.assertRaises(ValueError):
                recovery.private_receipt(str(self.path))

    def test_duplicate_identity_fields_refuse(self):
        self.path.write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            recovery.private_receipt(str(self.path))

    def test_missing_shared_api_and_fixture_cannot_admit_production(self):
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            recovery.admission(str(self.root), str(self.path), {'coverage': 'dedicated-resources'}, [])
        with self.assertRaisesRegex(ValueError, 'fixture'):
            recovery.admission(str(self.root), str(self.path), {'coverage': 'disposable-fixture'}, [])

    def test_atomic_publication_moves_complete_bytes(self):
        parent, name = recovery.parent_fd(str(self.root / 'published.json'))
        try:
            recovery.publish_exact(parent, self.path.name, name)
        finally:
            os.close(parent)
        self.assertFalse(self.path.exists())
        self.assertEqual(recovery.private_receipt(str(self.root / 'published.json')), {'schema': 1})

    def test_atomic_publication_refuses_overwrite(self):
        target = self.root / 'published.json'
        target.write_text('retained receipt')
        parent, name = recovery.parent_fd(str(target))
        try:
            with self.assertRaises(FileExistsError):
                recovery.publish_exact(parent, self.path.name, name)
        finally:
            os.close(parent)
        self.assertEqual(target.read_text(), 'retained receipt')
        self.assertTrue(self.path.exists())


class ConnectionTests(unittest.TestCase):
    def test_catalog_binding_closes_readonly_connection_on_early_refusal(self):
        database = MagicMock()
        database.execute.return_value.fetchone.return_value = None
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery.sqlite3, 'connect', return_value=database):
            with self.assertRaisesRegex(ValueError, 'Retained environment'):
                recovery.catalog_binding(str(Path(directory) / 'catalog.sqlite'), 'environment')
        database.close.assert_called_once_with()


class CurrentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.checkout = Path(__file__).resolve().parents[1]
        self.api = recovery.shared_api(str(self.checkout))
        self.assertEqual(Path(self.api.__file__), self.checkout / 'lab/recovery_receipt.py')

    def test_real_current_registry_refuses_incomplete_acceptance_for_each_placement(self):
        locator = self.api.RegistryAcceptance(self.checkout)
        for placements in ({'legacy-shared'}, {'native-dedicated'}, {'legacy-shared', 'native-dedicated'}):
            with self.subTest(placements=placements), self.assertRaisesRegex(
                    self.api.Refused, 'Complete current SB-05 and SB-13a acceptance required'):
                locator.current(placements)

    def test_canonical_receipt_reaches_real_current_registry_denial(self):
        receipt, bound, source = synthetic_registry_denial_fixture()
        # These canonical checks establish metadata shape, never native recovery.
        self.api.expected_observation(receipt)
        with self.assertRaisesRegex(self.api.Refused, 'Complete current SB-05 and SB-13a acceptance required'):
            recovery.verify(str(self.checkout), receipt, bound, source)


if __name__ == '__main__':
    unittest.main()
