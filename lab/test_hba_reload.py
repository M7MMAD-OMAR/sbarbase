"""Signal acknowledgment must not hide invalid HBA files or failed reloads."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import durable_runtime


class HBAReloadTests(unittest.TestCase):
    def setUp(self):
        self.runtime=durable_runtime.Runtime.__new__(durable_runtime.Runtime)

    def test_parse_error_prevents_reload_signal(self):
        self.runtime.sql=Mock(return_value=SimpleNamespace(stdout='1'))
        with self.assertRaisesRegex(RuntimeError,'parse failure'):self.runtime.reload_hba()
        self.assertEqual(self.runtime.sql.call_count,1)
        self.assertNotIn('pg_reload_conf',self.runtime.sql.call_args.args[0])

    def test_failed_signal_cannot_report_success(self):
        self.runtime.sql=Mock(side_effect=[SimpleNamespace(stdout='0'),SimpleNamespace(stdout='f')])
        with self.assertRaisesRegex(RuntimeError,'not acknowledged'):self.runtime.reload_hba()

    def test_valid_file_and_acknowledged_signal_complete(self):
        self.runtime.sql=Mock(side_effect=[SimpleNamespace(stdout='0'),SimpleNamespace(stdout='t')])
        self.runtime.reload_hba()
        self.assertEqual(self.runtime.sql.call_count,2)
