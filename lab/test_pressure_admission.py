"""Pressure parsing, refusal policy and allocation ordering."""
import unittest
from unittest.mock import patch
import pressure_admission as pressure
import resource_admission
import durable_runtime as runtime


class PressureTests(unittest.TestCase):
    def setUp(self):
        self.good = {name: {metric: 0.0 for metric in pressure.THRESHOLDS} for name in pressure.CONTAINERS}

    def test_parse_expected_line(self):
        sample='some avg10=5.25 avg60=2.50 avg300=0.10 total=999\nfull avg10=1.25 avg60=0.0 avg300=0.0 total=4\n'
        self.assertEqual(pressure.avg10(sample, 'some'), 5.25)
        self.assertEqual(pressure.avg10(sample, 'full'), 1.25)

    def test_bad_pressure_cannot_look_idle(self):
        for sample in ('', 'some avg60=0', 'some avg10=nan', 'some avg10=inf', 'some avg10=-1', 'some avg10=101', 'some avg10=0\nsome avg10=0'):
            with self.subTest(sample=sample), self.assertRaises((ValueError, KeyError)):
                pressure.avg10(sample, 'some')

    def test_each_pressure_boundary_refuses(self):
        self.assertIsNone(pressure.refusal(self.good))
        for container in pressure.CONTAINERS:
            for metric, threshold in pressure.THRESHOLDS.items():
                changed={name:dict(values) for name,values in self.good.items()}
                changed[container][metric]=threshold
                self.assertEqual(pressure.refusal(changed), metric)

    def test_incomplete_snapshot_fails_closed(self):
        with self.assertRaises(ValueError):
            pressure.refusal({})
        self.good[pressure.CONTAINERS[0]]['cpu_some10']=float('nan')
        with self.assertRaises(ValueError):
            pressure.refusal(self.good)

    def test_pressure_refusal_precedes_credentials(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.values={'environments':{}}
        self.good[pressure.CONTAINERS[0]]['cpu_some10']=75.0
        with patch.object(runtime, 'inspect', return_value={'owned':True}), patch.object(resource_admission, 'snapshot'), patch.object(resource_admission, 'refusal', return_value=None), patch.object(pressure, 'snapshot', return_value=self.good), patch.object(runtime, 'atomic') as persist:
            with self.assertRaises(runtime.AdmissionLimitError):
                target.provision('e_'+'c'*24)
            self.assertEqual(target.values['environments'], {})
            persist.assert_not_called()


if __name__ == '__main__':
    unittest.main()
