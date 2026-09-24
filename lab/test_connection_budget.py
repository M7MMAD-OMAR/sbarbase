"""Connection headroom accounting, independent of a running database."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import durable_runtime as runtime
import resource_admission
import pressure_admission
import connection_budget as budget


class ConnectionBudgetTests(unittest.TestCase):
    def test_four_environments_fit_default_cluster_with_reserve(self):
        self.assertTrue(budget.fits(4, 100, 3, 0))
        self.assertFalse(budget.fits(5, 100, 3, 0))

    def test_reserved_slots_are_not_allocatable(self):
        self.assertFalse(budget.fits(4, 100, 3, 4))
        self.assertTrue(budget.fits(4, 100, 3, 3))

    def test_insufficient_cluster_slots_refuse_before_allocation(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.values={'environments':{}}
        target.sql=lambda query: SimpleNamespace(stdout='20|3|0')
        with patch.object(runtime, 'inspect', return_value={'owned':True}), patch.object(runtime, 'owned_usage_bytes', return_value=0), patch.object(runtime.resource_policy, 'restart_fits', return_value=True), patch.object(resource_admission, 'snapshot'), patch.object(resource_admission, 'refusal', return_value=None), patch.object(pressure_admission, 'snapshot'), patch.object(pressure_admission, 'refusal', return_value=None), patch.object(runtime, 'atomic') as persist:
            with self.assertRaises(runtime.AdmissionLimitError):
                target.provision('e_'+'b'*24)
            self.assertEqual(target.values['environments'], {})
            persist.assert_not_called()

    def test_invalid_measurements_fail_closed(self):
        for args in ((-1,100,3,0),(4,100,-1,0),(4,'100',3,0)):
            with self.assertRaises(ValueError):
                budget.fits(*args)


if __name__ == '__main__':
    unittest.main()
