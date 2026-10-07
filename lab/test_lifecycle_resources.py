"""Real temporary filesystem identity and interrupted purge reconciliation checks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import lifecycle_resources as lifecycle


class DirectoryLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.installation = str(uuid4())
        self.runtime = 'e_' + uuid4().hex[:24]
        self.resource_id = str(uuid4())
        self.path = self.root / self.runtime / self.resource_id
        self.path.mkdir(parents=True)
        identity = self.path.stat()
        self.resource = {'kind': 'directory', 'id': str(self.path), 'installation': self.installation,
                         'runtime': self.runtime, 'resource': self.resource_id,
                         'device': identity.st_dev, 'inode': identity.st_ino,
                         'marker': '.sbarbase-lifecycle-owner.json'}
        (self.path / self.resource['marker']).write_text(json.dumps({key: self.resource[key]
            for key in ('installation', 'runtime', 'resource')}))
        (self.path / 'payload').write_bytes(b'a' * 16384)
        self.neighbor = self.root / ('e_' + uuid4().hex[:24])
        self.neighbor.mkdir()
        (self.neighbor / 'payload').write_bytes(b'neighbor')
        self.adapter = lifecycle.Adapter(self.root, self.installation)
        self.operation = str(uuid4())

    def apply(self, action, resource=None):
        return self.adapter.apply({'action': action, 'resource': resource or self.resource,
                                  'runtime': self.runtime, 'epoch': 1, 'operation': self.operation})

    def test_retention_and_restore_preserve_bytes(self):
        self.assertEqual(self.apply('quarantine')['outcome'], 'quarantined')
        self.assertEqual(self.apply('restore')['outcome'], 'restored')
        self.assertEqual((self.path / 'payload').read_bytes(), b'a' * 16384)

    def test_purge_measures_reclaim_and_preserves_neighbor(self):
        measured = self.apply('inspect')['observedBytes']
        result = self.apply('purge')
        self.assertEqual(result, {'outcome': 'purged', 'reclaimedBytes': measured})
        self.assertFalse(self.path.exists())
        self.assertEqual((self.neighbor / 'payload').read_bytes(), b'neighbor')
        self.assertEqual(self.apply('purge')['outcome'], 'absent')

    def test_replay_after_durable_rename(self):
        grave = self.root / (self.runtime + '-' + self.resource_id + '.purging')
        os.rename(self.path, grave)
        self.assertEqual(self.apply('inspect')['outcome'], 'present')
        self.assertEqual(self.apply('purge')['outcome'], 'purged')
        self.assertFalse(grave.exists())

    def test_replay_after_marker_unlink(self):
        grave = self.root / (self.runtime + '-' + self.resource_id + '.purging')
        os.rename(self.path, grave)
        (grave / 'payload').unlink()
        (grave / self.resource['marker']).unlink()
        self.assertEqual(self.apply('purge')['outcome'], 'purged')
        self.assertFalse(grave.exists())

    def test_foreign_marker_refuses_without_changes(self):
        marker = self.path / self.resource['marker']
        marker.write_text('{}')
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge')
        self.assertTrue((self.path / 'payload').exists())

    def test_wrong_inode_refuses_without_changes(self):
        resource = {**self.resource, 'inode': self.resource['inode'] + 1}
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge', resource)
        self.assertTrue((self.path / 'payload').exists())

    def test_symlink_refuses_before_any_effect(self):
        (self.path / 'foreign').symlink_to(self.neighbor, target_is_directory=True)
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge')
        self.assertTrue((self.path / 'payload').exists())
        self.assertEqual((self.neighbor / 'payload').read_bytes(), b'neighbor')

    def test_hardlink_refuses_before_any_effect(self):
        os.link(self.neighbor / 'payload', self.path / 'foreign')
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge')
        self.assertTrue((self.path / 'payload').exists())

    def test_grave_collision_refuses_before_any_effect(self):
        (self.root / (self.runtime + '-' + self.resource_id + '.purging')).mkdir()
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge')
        self.assertTrue((self.path / 'payload').exists())

    def test_restore_refuses_after_purge_intent_rename(self):
        grave = self.root / (self.runtime + '-' + self.resource_id + '.purging')
        os.rename(self.path, grave)
        with self.assertRaises(lifecycle.Refused):
            self.apply('restore')

    def test_parent_symlink_refuses(self):
        parent = self.path.parent
        renamed = self.root / 'renamed'
        parent.rename(renamed)
        parent.symlink_to(renamed, target_is_directory=True)
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge')

    def test_wrong_runtime_refuses(self):
        resource = {**self.resource, 'runtime': 'e_' + uuid4().hex[:24]}
        with self.assertRaises(lifecycle.Refused):
            self.apply('purge', resource)

    def test_shared_tenant_tree_removal_syncs_its_surviving_parent(self):
        tenant = self.root / self.runtime
        alternate = self.root / 'original'
        self.path.rename(alternate)
        tenant.rmdir()
        alternate.rename(tenant)
        resource = {**self.resource, 'id': str(tenant)}
        result = self.apply('purge', resource)
        self.assertEqual(result['outcome'], 'purged')
        self.assertFalse(tenant.exists())
        self.assertEqual((self.neighbor / 'payload').read_bytes(), b'neighbor')

    def refuse_mount_at(self, path):
        original = lifecycle.mount_id
        def changed(fd):
            observed = original(fd)
            return observed + 1 if os.readlink('/proc/self/fd/' + str(fd)) == str(path) else observed
        with patch.object(lifecycle, 'mount_id', side_effect=changed):
            with self.assertRaisesRegex(lifecycle.Refused, 'mount_boundary'):
                self.apply('purge')
        self.assertTrue((self.path / 'payload').exists())
        self.assertEqual((self.neighbor / 'payload').read_bytes(), b'neighbor')

    def test_same_device_resource_mount_identity_refuses_before_effects(self):
        self.refuse_mount_at(self.path)

    def test_same_device_descendant_directory_mount_identity_refuses(self):
        nested = self.path / 'nested'
        nested.mkdir()
        (nested / 'payload').write_bytes(b'kept nested bytes')
        self.refuse_mount_at(nested)
        self.assertEqual((nested / 'payload').read_bytes(), b'kept nested bytes')

    def test_same_device_regular_file_mount_identity_refuses(self):
        self.refuse_mount_at(self.path / 'payload')

    def test_missing_mount_identity_refuses_without_unlinking(self):
        with patch.object(lifecycle, 'mount_id', side_effect=lifecycle.Refused('directory_mount_identity_unavailable')):
            with self.assertRaisesRegex(lifecycle.Refused, 'identity_unavailable'):
                self.apply('purge')
        self.assertTrue((self.path / 'payload').exists())
        self.assertEqual((self.neighbor / 'payload').read_bytes(), b'neighbor')


class ContainerServiceIdentity(unittest.TestCase):
    def descriptor(self):
        resource = {'kind': 'container', 'id': 'a' * 64, 'resource': str(uuid4()),
                    'installation': str(uuid4()), 'runtime': 'e_' + uuid4().hex[:24], 'service': 'storage'}
        marks = {'io.sbarbase.owner': lifecycle.OWNER, 'io.sbarbase.installation': resource['installation'],
                 'io.sbarbase.environment': resource['runtime'], 'io.sbarbase.resource': resource['resource'],
                 'io.sbarbase.role': 'environment', 'io.sbarbase.service': 'storage'}
        return resource, {'Id': resource['id'], 'Config': {'Labels': marks, 'Healthcheck': {'Test': ['CMD', 'health']}}}

    def test_foreign_service_label_cannot_supply_native_coverage(self):
        resource, observed = self.descriptor()
        observed['Config']['Labels']['io.sbarbase.service'] = 'auth'
        docker = object.__new__(lifecycle.Docker)
        docker.request = Mock(return_value=(200, observed))
        with self.assertRaisesRegex(lifecycle.Refused, 'service_identity_mismatch'):
            docker.container(resource)
        docker.request.assert_called_once()

    def test_declared_native_service_requires_health_contract(self):
        resource, observed = self.descriptor()
        observed['Config']['Healthcheck'] = None
        docker = object.__new__(lifecycle.Docker)
        docker.request = Mock(return_value=(200, observed))
        with self.assertRaisesRegex(lifecycle.Refused, 'readiness_contract_missing'):
            docker.container(resource)
        docker.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
