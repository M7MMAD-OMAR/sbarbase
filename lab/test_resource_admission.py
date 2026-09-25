"""Resource thresholds and fail-closed allocation ordering."""
from dataclasses import replace
import unittest
from unittest.mock import patch
import resource_admission as admission
import durable_runtime as runtime


class ResourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.good = admission.Snapshot(3*admission.GIB, 8*admission.GIB, 8*admission.GIB, 20000, 20000)

    def test_each_resource_can_refuse_independently(self):
        for field, value, reason in (
            ('available_memory', 2*admission.GIB, 'memory_headroom'),
            ('database_free_bytes', 5*admission.GIB, 'disk_headroom'),
            ('objects_free_bytes', 5*admission.GIB, 'disk_headroom'),
            ('database_free_inodes', 9999, 'inode_headroom'),
            ('objects_free_inodes', 9999, 'inode_headroom'),
            ('available_memory', -1, 'measurement_unavailable')):
            with self.subTest(field=field, reason=reason):
                self.assertEqual(admission.refusal(replace(self.good, **{field:value})), reason)

    def test_threshold_and_large_memory_do_not_override_disk(self):
        self.assertIsNone(admission.refusal(replace(self.good, available_memory=admission.MEMORY_RESERVE+admission.NEW_ENVIRONMENT_MEMORY)))
        self.assertEqual(admission.refusal(replace(self.good, available_memory=100*admission.GIB, objects_free_bytes=1)), 'disk_headroom')

    def test_btrfs_inode_unavailability_is_explicit(self):
        self.assertIsNone(admission.refusal(replace(self.good, database_free_inodes=None, objects_free_inodes=None)))
        output='Filesystem Inodes Used Available Capacity Mounted\n/dev/test 0 0 0 0% /data\n'
        with patch.object(admission, 'docker', side_effect=[output, '9123683e\n']):
            self.assertIsNone(admission.disk_free('owned', '/data', True))
        with patch.object(admission, 'docker', side_effect=[output, 'ef53\n']):
            self.assertEqual(admission.disk_free('owned', '/data', True), 0)

    def test_failed_measurement_never_allocates(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.values={'environments':{}}
        with patch.object(runtime, 'inspect', return_value={'owned':True}), patch.object(admission, 'snapshot', side_effect=RuntimeError('unavailable')), patch.object(runtime, 'atomic') as persist:
            with self.assertRaises(RuntimeError):
                target.provision('e_'+'a'*24)
            self.assertEqual(target.values['environments'], {})
            persist.assert_not_called()

    def test_low_memory_never_allocates(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.values={'environments':{}}
        with patch.object(runtime, 'inspect', return_value={'owned':True}), patch.object(runtime, 'owned_usage_bytes', return_value=0), patch.object(runtime.resource_policy, 'restart_fits', return_value=True), patch.object(runtime, 'recovery_target_items', return_value=[]), patch.object(admission, 'snapshot', return_value=replace(self.good, available_memory=0)), patch.object(runtime, 'atomic') as persist:
            with self.assertRaises(runtime.AdmissionLimitError):
                target.provision('e_'+'a'*24)
            self.assertEqual(target.values['environments'], {})
            persist.assert_not_called()


if __name__ == '__main__':
    unittest.main()
