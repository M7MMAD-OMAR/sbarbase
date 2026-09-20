"""Admission guard ordering without Docker or private runtime state."""
import unittest
from unittest.mock import patch
import durable_runtime as runtime


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime.Runtime.__new__(runtime.Runtime)
        self.runtime.values = {'environments': {'e_'+f'{i:024x}': {} for i in range(4)}}

    def test_full_runtime_refuses_before_persistence_or_sql(self):
        with patch.object(runtime, 'inspect', return_value={'owned': True}), patch.object(runtime, 'atomic') as persist, patch.object(runtime.lab, 'provision_environment') as provision:
            with self.assertRaises(runtime.AdmissionLimitError):
                self.runtime.provision('e_'+'f'*24)
            persist.assert_not_called()
            provision.assert_not_called()
            self.assertEqual(len(self.runtime.values['environments']), 4)

    def test_existing_environment_can_reconcile_at_limit(self):
        with patch.object(runtime, 'inspect', return_value={'owned': True}), patch.object(runtime, 'atomic') as persist, patch.object(runtime.lab, 'provision_environment', side_effect=RuntimeError('reached reconciliation')):
            with self.assertRaisesRegex(RuntimeError, 'reached reconciliation'):
                self.runtime.provision('e_'+'0'*24)
            persist.assert_not_called()


if __name__ == '__main__':
    unittest.main()
