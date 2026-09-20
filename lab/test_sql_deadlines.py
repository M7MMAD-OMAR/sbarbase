"""Prevent silently applying new defaults underneath an existing REST pool."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import durable_runtime as runtime

class SqlDeadlineTests(unittest.TestCase):
    def test_changed_defaults_refuse_before_write_when_pool_is_running(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.sql=Mock(return_value=SimpleNamespace(stdout='f'))
        with patch.object(runtime,'inspect',return_value={'State':{'Running':True}}):
            with self.assertRaisesRegex(RuntimeError,'stopping the owned runtime'):
                target.rest_deadlines('e_'+'a'*24)
        self.assertEqual(target.sql.call_count,1)
        self.assertNotIn('ALTER ROLE',target.sql.call_args.args[0])

    def test_stopped_pool_can_receive_new_defaults(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.sql=Mock(return_value=SimpleNamespace(stdout='f'))
        with patch.object(runtime,'inspect',return_value={'State':{'Running':False}}):
            target.rest_deadlines('e_'+'a'*24)
        self.assertEqual(target.sql.call_count,2)

    def test_unchanged_running_pool_is_reconcilable(self):
        target=runtime.Runtime.__new__(runtime.Runtime)
        target.sql=Mock(return_value=SimpleNamespace(stdout='t'))
        with patch.object(runtime,'inspect',return_value={'State':{'Running':True}}):
            target.rest_deadlines('e_'+'a'*24)
        self.assertEqual(target.sql.call_count,2)
