"""Soft delete and restore of a durable upstream environment: exact effects, crash safety, neighbors untouched."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import durable_inventory as inventory
import durable_lifecycle as lifecycle
import test_durable_inventory as base
from durable_inventory import Refused
from durable_lifecycle import Effects

RUNTIME, OTHER, INSTALLATION = base.RUNTIME, base.OTHER, base.INSTALLATION


def accept(request):
    pass


class Steps:
    """Drives the effects in the order src/control/lifecycle.ts does: inspect everything first, then effect
    each resource (containers first to quarantine, the tenant first to restore), then readiness on restore."""

    def __init__(self, host, runtime=RUNTIME, verify=accept):
        self.host = host
        self.runtime = runtime
        self.resources = inventory.derive(runtime, INSTALLATION, host)['resources']
        self.verify = verify
        self.ticks = 0
        self.effects = Effects(host, verify, sleep=lambda seconds: None, clock=self.clock)

    def clock(self):
        self.ticks += 1
        return self.ticks

    def request(self, action, resource, operation, epoch=1):
        return {'action': action, 'resource': resource, 'runtime': self.runtime, 'epoch': epoch, 'operation': operation}

    def run(self, action, operation, epoch=1):
        resources = list(self.resources)
        tenant = [item for item in resources if item['kind'] == 'durable-tenant']
        containers = [item for item in resources if item['kind'] == 'durable-container']
        ordered = containers + tenant if action == 'quarantine' else tenant + containers
        for item in resources:
            self.effects.apply(self.request('inspect', item, operation, epoch))
        results = [self.effects.apply(self.request(action, item, operation, epoch)) for item in ordered]
        if action == 'restore':
            for item in ordered:
                self.effects.apply(self.request('readiness', item, operation, epoch))
        return results


class Effect(unittest.TestCase):
    def setUp(self):
        self.host = base.FakeHost((RUNTIME, OTHER))
        self.steps = Steps(self.host)
        self.before = self.host.snapshot(RUNTIME)
        self.neighbor = self.host.snapshot(OTHER)
        self.delete, self.restore = str(uuid4()), str(uuid4())

    def test_quarantine_stops_closes_and_unpublishes_but_destroys_nothing(self):
        results = self.steps.run('quarantine', self.delete)
        self.assertEqual([item['outcome'] for item in results], ['quarantined'] * 3)
        after = self.host.snapshot(RUNTIME)
        self.assertTrue(all(state['Running'] is False for state in after['containers'].values()))
        self.assertEqual(len(after['containers']), 2)
        self.assertTrue(all(role['login'] is False for role in after['roles'].values()))
        self.assertIsNone(after['entry'])
        self.assertEqual(after['database'], self.before['database'])
        self.assertTrue(after['secret'])
        self.assertEqual(self.host.snapshot(OTHER), self.neighbor)
        self.assertNotIn(OTHER, ' '.join(self.host.log))

    def test_restore_returns_everything_quarantine_recorded(self):
        self.steps.run('quarantine', self.delete)
        results = self.steps.run('restore', self.restore, epoch=1)
        self.assertEqual([item['outcome'] for item in results], ['restored'] * 3)
        after = self.host.snapshot(RUNTIME)
        self.assertEqual(after['roles'], self.before['roles'])
        self.assertEqual(after['containers'], self.before['containers'])
        self.assertEqual(after['entry'], self.before['entry'])
        self.assertEqual(self.host.snapshot(OTHER), self.neighbor)

    def test_restore_publishes_the_new_address_of_a_restarted_container(self):
        self.steps.run('quarantine', self.delete)
        self.host.items[inventory.container_name(RUNTIME, 'rest')]['NetworkSettings']['Networks'][inventory.NETWORK]['IPAddress'] = '172.30.9.9'
        self.steps.run('restore', self.restore)
        entry = self.host.published[RUNTIME]
        self.assertEqual(entry['rest'], 'http://172.30.9.9:3000')
        self.assertEqual(entry['storage'], self.before['entry']['storage'])

    def test_a_restore_that_never_becomes_healthy_is_refused_not_declared_done(self):
        self.steps.run('quarantine', self.delete)
        tenant = self.steps.resources[2]
        self.steps.effects.apply(self.steps.request('restore', tenant, self.restore))
        self.host.healthy = False
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('restore', self.steps.resources[0], self.restore))
        self.assertEqual(str(caught.exception), 'container_readiness_unproven')
        self.assertNotEqual(self.host.journals[RUNTIME]['containers']['auth']['phase'], 'restored')

    def test_purge_is_refused_whatever_the_catalog_says(self):
        self.steps.run('quarantine', self.delete)
        for item in self.steps.resources:
            with self.assertRaises(Refused) as caught:
                self.steps.effects.apply(self.steps.request('purge', item, str(uuid4())))
            self.assertEqual(str(caught.exception), 'durable_purge_unavailable')
        self.assertEqual(self.host.snapshot(OTHER), self.neighbor)
        self.assertEqual(self.host.databases[RUNTIME], self.before['database'])

    def test_an_effect_without_a_recorded_intent_changes_nothing(self):
        effects = Effects(self.host, None)
        with self.assertRaises(Refused) as caught:
            effects.apply(self.steps.request('quarantine', self.steps.resources[0], self.delete))
        self.assertEqual(str(caught.exception), 'lifecycle_intent_required')
        self.assertEqual(self.host.snapshot(RUNTIME), self.before)
        self.assertEqual(self.host.log, [])

    def test_exact_resources_only(self):
        foreign = copy.deepcopy(self.steps.resources[0])
        foreign['id'] = inventory.container_name(OTHER, 'auth')
        tenant = copy.deepcopy(self.steps.resources[2])
        tenant['durable']['roles'].append(OTHER + '_auth')
        for resource in (foreign, {**self.steps.resources[0], 'runtime': OTHER}, {**self.steps.resources[0], 'service': 'db'},
                         {**self.steps.resources[2], 'id': OTHER}, {'kind': 'container', 'id': 'x'}, tenant):
            with self.assertRaises(Refused) as caught:
                self.steps.effects.apply(self.steps.request('quarantine', resource, self.delete))
            self.assertEqual(str(caught.exception), 'invalid_resource_contract')
        self.assertEqual(self.host.log, [])
        self.assertEqual(self.host.snapshot(OTHER), self.neighbor)

    def test_the_database_logins_close_only_after_every_container_stopped(self):
        tenant = self.steps.resources[2]
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('quarantine', tenant, self.delete))
        self.assertEqual(str(caught.exception), 'container_still_running')
        self.assertEqual(self.host.snapshot(RUNTIME), self.before)

    def test_containers_come_back_only_after_the_logins_and_the_entry(self):
        self.steps.run('quarantine', self.delete)
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('restore', self.steps.resources[0], self.restore))
        self.assertEqual(str(caught.exception), 'tenant_not_restored')
        self.assertFalse(self.host.items[inventory.container_name(RUNTIME, 'auth')]['State']['Running'])

    def test_a_container_that_stopped_being_ours_is_not_touched(self):
        self.host.items[inventory.container_name(RUNTIME, 'rest')]['Config']['Labels']['io.sbarbase.owner'] = 'recovery-target'
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('quarantine', self.steps.resources[1], self.delete))
        self.assertEqual(str(caught.exception), 'container_foreign')
        self.assertEqual(self.host.log, [])

    def test_a_recreated_container_is_not_the_one_that_was_recorded(self):
        self.steps.effects.apply(self.steps.request('quarantine', self.steps.resources[0], self.delete))
        self.host.items[inventory.container_name(RUNTIME, 'auth')]['Id'] = '9' * 64
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('quarantine', self.steps.resources[0], self.delete))
        self.assertEqual(str(caught.exception), 'container_identity_changed')

    def test_missing_retained_resources_are_refused_and_inspected_as_absent(self):
        del self.host.items[inventory.container_name(RUNTIME, 'rest')]
        self.assertEqual(self.steps.effects.apply(self.steps.request('inspect', self.steps.resources[1], self.delete))['outcome'], 'absent')
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('quarantine', self.steps.resources[1], self.delete))
        self.assertEqual(str(caught.exception), 'retained_resource_missing')
        del self.host.databases[RUNTIME]
        self.assertEqual(self.steps.effects.apply(self.steps.request('inspect', self.steps.resources[2], self.delete))['outcome'], 'absent')

    def test_a_second_delete_while_the_first_is_not_restored_is_refused(self):
        self.steps.run('quarantine', self.delete)
        with self.assertRaises(Refused) as caught:
            self.steps.effects.apply(self.steps.request('quarantine', self.steps.resources[0], str(uuid4()), epoch=2))
        self.assertEqual(str(caught.exception), 'journal_foreign_operation')

    def test_delete_restore_delete_restore_cycles(self):
        for cycle in range(1, 3):
            self.steps.run('quarantine', str(uuid4()), epoch=cycle)
            self.assertIsNone(self.host.snapshot(RUNTIME)['entry'])
            self.steps.run('restore', str(uuid4()), epoch=cycle)
            self.assertEqual(self.host.snapshot(RUNTIME)['roles'], self.before['roles'])
            self.assertEqual(self.host.snapshot(RUNTIME)['entry'], self.before['entry'])

    def test_a_container_that_was_off_is_not_started_by_restore(self):
        host = base.FakeHost()
        host.add_container(inventory.container_name(RUNTIME, 'realtime'), running=False)
        steps = Steps(host)
        steps.run('quarantine', self.delete)
        steps.run('restore', self.restore)
        self.assertFalse(host.items[inventory.container_name(RUNTIME, 'realtime')]['State']['Running'])
        self.assertNotIn('start ' + inventory.container_name(RUNTIME, 'realtime'), host.log)


class Crashes(unittest.TestCase):
    """A crash at every mutating step, before and after it takes effect, then the same operation resumes."""

    def cycle(self, action, crash, prepare=lambda steps: None):
        host = base.FakeHost((RUNTIME, OTHER))
        steps = Steps(host)
        neighbor = host.snapshot(OTHER)
        delete, restore = str(uuid4()), str(uuid4())
        if action == 'restore':
            steps.run('quarantine', delete)
        operation = delete if action == 'quarantine' else restore
        host.count, host.crash = 0, crash
        crashed = False
        try:
            steps.run(action, operation)
        except base.Crash:
            crashed = True
        host.crash = None
        host.first_count = host.count
        steps.run(action, operation)
        self.assertEqual(host.snapshot(OTHER), neighbor)
        return host, crashed, steps, delete

    def test_quarantine_resumes_after_a_crash_at_every_step(self):
        original = base.FakeHost((RUNTIME, OTHER)).snapshot(RUNTIME)
        clean, _, _, _ = self.cycle('quarantine', None)
        expected = clean.snapshot(RUNTIME)
        steps_total = clean.first_count
        self.assertGreaterEqual(steps_total, 7)
        for number in range(1, steps_total + 1):
            for when in ('before', 'after'):
                with self.subTest(step=number, when=when):
                    host, crashed, steps, _ = self.cycle('quarantine', (number, when))
                    self.assertTrue(crashed)
                    self.assertEqual(host.snapshot(RUNTIME), expected)
                    self.assertEqual(host.journals[RUNTIME]['tenant']['phase'], 'quarantined')
                    # What the interrupted attempt recorded is what a restore brings back.
                    steps.run('restore', str(uuid4()))
                    self.assertEqual(host.snapshot(RUNTIME), original)

    def test_restore_resumes_after_a_crash_at_every_step_and_returns_the_original(self):
        original = base.FakeHost((RUNTIME, OTHER)).snapshot(RUNTIME)
        probe = base.FakeHost((RUNTIME, OTHER))
        steps = Steps(probe)
        steps.run('quarantine', str(uuid4()))
        probe.count = 0
        steps.run('restore', str(uuid4()))
        steps_total = probe.count
        self.assertGreaterEqual(steps_total, 5)
        for number in range(1, steps_total + 1):
            for when in ('before', 'after'):
                with self.subTest(step=number, when=when):
                    host, crashed, _, _ = self.cycle('restore', (number, when))
                    self.assertTrue(crashed)
                    self.assertEqual(host.snapshot(RUNTIME), original)

    def test_a_crash_after_the_journal_but_before_any_effect_loses_nothing(self):
        # The first mutation of a quarantine is its journal write: crash after it, the intent is durable.
        host, crashed, _, _ = self.cycle('quarantine', (1, 'after'))
        self.assertTrue(crashed)
        self.assertIn('journal', host.log)

    def test_the_logins_recorded_before_closing_are_the_ones_restored(self):
        # Developer access was off: its login stays closed after a crash in the middle of closing the others.
        host = base.FakeHost()
        host.roles[RUNTIME + '_developer'] = {'login': False, 'super': False}
        delete = str(uuid4())
        for number in range(1, 20):
            fresh = copy.deepcopy(host)
            fresh_steps = Steps(fresh)
            fresh.count, fresh.crash = 0, (number, 'after')
            try:
                fresh_steps.run('quarantine', delete)
            except base.Crash:
                fresh.crash = None
                fresh_steps.run('quarantine', delete)
                fresh_steps.run('restore', str(uuid4()))
                self.assertFalse(fresh.roles[RUNTIME + '_developer']['login'])
                self.assertTrue(fresh.roles[RUNTIME + '_auth']['login'])


class Intent(unittest.TestCase):
    """The default verifier reads the catalog read only and refuses anything it does not durably record."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'control.sqlite'
        self.host = base.FakeHost()
        self.resources = inventory.derive(RUNTIME, INSTALLATION, self.host)['resources']
        self.operation = str(uuid4())
        connection = sqlite3.connect(self.path)
        connection.execute('CREATE TABLE environment_lifecycle(environment TEXT, runtime TEXT, state TEXT, epoch INTEGER, operation TEXT, inventory TEXT, coverage TEXT)')
        connection.execute('INSERT INTO environment_lifecycle VALUES (?,?,?,?,?,?,?)',
                           ('env', RUNTIME, 'deleting', 1, self.operation, json.dumps(self.resources), 'durable-environment'))
        connection.commit()
        connection.close()
        self.effects = Effects(self.host, lifecycle.catalog_verifier(self.path), sleep=lambda seconds: None)

    def request(self, **change):
        return {'action': 'quarantine', 'resource': self.resources[0], 'runtime': RUNTIME, 'epoch': 1, 'operation': self.operation, **change}

    def update(self, column, value):
        connection = sqlite3.connect(self.path)
        connection.execute('UPDATE environment_lifecycle SET ' + column + '=?', (value,))
        connection.commit()
        connection.close()

    def test_a_recorded_intent_is_effected(self):
        self.assertEqual(self.effects.apply(self.request())['outcome'], 'quarantined')

    def test_every_mismatch_changes_nothing(self):
        cases = [self.request(operation=str(uuid4())), self.request(epoch=2), self.request(action='restore'),
                 self.request(resource=self.resources[1] | {'resource': str(uuid4())})]
        for request in cases:
            with self.subTest(request=request['action'] + request['operation']):
                with self.assertRaises(Refused) as caught:
                    self.effects.apply(request)
                self.assertEqual(str(caught.exception), 'lifecycle_intent_unrecorded')
        self.update('coverage', 'dedicated-resources')
        with self.assertRaises(Refused):
            self.effects.apply(self.request())
        self.assertEqual(self.host.log, [])


if __name__ == '__main__':
    unittest.main()
