"""The runtime raises the cluster's max_connections at start (durable_runtime.ensure_connection_capacity)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import connection_budget
import durable_runtime


class Fake:
    def __init__(self, values, alter_fails=False):
        self.values = list(values)
        self.statements = []
        self.alter_fails = alter_fails
        self.waits = 0

    def sql(self, query, check=True):
        self.statements.append(query)
        if query.startswith('ALTER SYSTEM'):
            return SimpleNamespace(returncode=1 if self.alter_fails else 0, stdout='')
        return SimpleNamespace(returncode=0, stdout=f'{self.values.pop(0)}\n')

    def wait_database(self):
        self.waits += 1


def ensure(fake):
    return durable_runtime.Runtime.ensure_connection_capacity(fake)


class CapacityTests(unittest.TestCase):
    def test_eight_environments_fit_the_target_and_four_fit_the_image_default(self):
        self.assertTrue(connection_budget.fits(durable_runtime.ENVIRONMENT_LIMIT, connection_budget.MAX_CONNECTIONS, 3, 0))
        # What eight leave for Studio, Realtime and direct access, each admitted while it fits.
        spare = connection_budget.MAX_CONNECTIONS - 3 - (durable_runtime.ENVIRONMENT_LIMIT * connection_budget.ENVIRONMENT_LIMIT
                                                          + connection_budget.SHARED_LIMIT + connection_budget.OPERATIONS_RESERVE)
        self.assertEqual(spare, 31)
        self.assertTrue(connection_budget.fits(4, 100, 3, 0))
        self.assertFalse(connection_budget.fits(5, 100, 3, 0))

    def test_a_low_limit_is_raised_with_one_restart(self):
        fake = Fake([100, connection_budget.MAX_CONNECTIONS])
        with patch.object(durable_runtime.lab, 'docker') as docker:
            ensure(fake)
        self.assertIn(f'ALTER SYSTEM SET max_connections = {connection_budget.MAX_CONNECTIONS};', fake.statements)
        docker.assert_called_once_with('restart', '-t', '60', durable_runtime.DB)
        self.assertEqual(fake.waits, 1)

    def test_an_enough_or_higher_limit_is_left_alone(self):
        for value in (connection_budget.MAX_CONNECTIONS, 500):
            fake = Fake([value])
            with patch.object(durable_runtime.lab, 'docker') as docker:
                ensure(fake)
            docker.assert_not_called()
            self.assertFalse(any(query.startswith('ALTER SYSTEM') for query in fake.statements))

    def test_a_limit_that_does_not_take_or_cannot_be_set_refuses_the_start(self):
        with patch.object(durable_runtime.lab, 'docker'):
            with self.assertRaisesRegex(RuntimeError, 'did not take effect'):
                ensure(Fake([100, 100]))
            with self.assertRaisesRegex(RuntimeError, 'could not be raised'):
                ensure(Fake([100], alter_fails=True))
            with self.assertRaisesRegex(RuntimeError, 'unreadable'):
                ensure(Fake(['']))

    def test_it_runs_before_the_hba_writer_takes_the_database(self):
        source = open(durable_runtime.__file__).read()
        self.assertLess(source.index('self.ensure_connection_capacity()'), source.index('self.hba_writer.ready(launched_cid'))


if __name__ == '__main__':
    unittest.main()
