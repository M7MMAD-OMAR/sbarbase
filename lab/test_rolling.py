"""Auth and REST replaced side by side after a pin change (lab/rolling.py), against a fake Docker and a
fake database: the start keeps the previous pin, the supervisor rolls environments one at a time after
the confirmation with a watch window and a way back, and a crash at every step is repaired."""
import copy
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import backup
import connection_budget
import data_api
import dev
import durable_runtime as runtime
import resource_admission
import rolling
import updates
import upgrade
import write_pause

E = 'e_' + 'a' * 24
F = 'e_' + 'b' * 24
G = 'e_' + 'c' * 24
PASSWORD = 'f' * 64
OLD_IMAGE, NEW_IMAGE = 'sha256:' + '1' * 64, 'sha256:' + '2' * 64
REST_SETTINGS = 'pg_db_role_setting s LEFT JOIN'


class Crash(BaseException):
    """The process dies here: no handler of the code under test runs."""


class Fake:
    """Docker, the database and a clock, as far as the replacement uses them. Every call is a step; with
    `crash` set, step number `crash` raises Crash before it has any effect."""

    def __init__(self, crash=None):
        self.crash, self.steps, self.calls, self.queries = crash, 0, [], []
        self.containers, self.networks, self.serial, self.clock = {}, set(), 0, 0.0
        # Addresses whose health fails, and how many answers each gives first.
        self.unhealthy, self.answers = set(), {}
        # The REST login's role settings as read back (None: exactly the platform's, for whichever environment).
        self.settings, self.budget, self.failing_sql = None, '197|160', ()

    def step(self, label):
        if self.crash is not None and self.steps == self.crash:
            raise Crash(label)
        self.steps += 1
        self.calls.append(label)

    def add(self, name, image, running=True):
        self.serial += 1
        cid = f'{self.serial:064x}'
        ip = f'172.30.0.{self.serial + 1}'
        self.containers[cid] = {'Id': cid, 'Name': '/' + name, 'Image': image, 'State': {'Running': running},
                                'Config': {'Env': ['X=old'], 'Labels': {'io.sbarbase.owner': runtime.OWNER}}, 'Mounts': [],
                                'NetworkSettings': {'Networks': {runtime.NETWORK: {'IPAddress': ip if running else ''}}}, 'ip': ip}
        return cid

    def find(self, name):
        for item in self.containers.values():
            if name in (item['Id'], item['Name'].lstrip('/')):
                return item
        return None

    def named(self, name):
        item = self.find(name)
        return item['Id'] if item else None

    def running(self, item, on):
        item['State']['Running'] = on
        item['NetworkSettings']['Networks'][runtime.NETWORK]['IPAddress'] = item['ip'] if on else ''

    def inspect(self, kind, name):
        if kind == 'network':
            return {'Containers': {}} if name in self.networks else None
        if kind == 'volume':
            return None
        item = self.find(name)
        return copy.deepcopy(item) if item else None

    def docker(self, *args, data=None, check=True):
        verb = args[0]
        if verb in ('image', 'ps'):
            return SimpleNamespace(returncode=0, stdout='[]' if verb == 'image' else '', stderr='')
        self.step(' '.join(args[:2]) if verb == 'network' else verb)
        out = ''
        if verb == 'run':
            name = args[args.index('--name') + 1]
            out = self.add(name, NEW_IMAGE)
            self.containers[out]['Config']['Env'] = ['X=new']
        elif verb in ('stop', 'start'):
            item = self.find(args[-1])
            if item is None:
                raise RuntimeError(f'Docker operation {verb} failed; exit 1')
            self.running(item, verb == 'start')
        elif verb == 'rm':
            item = self.find(args[-1])
            if item is None:
                raise RuntimeError('Docker operation rm failed; exit 1')
            del self.containers[item['Id']]
        elif verb == 'rename':
            item, taken = self.find(args[1]), self.find(args[2])
            if item is None or taken is not None:
                raise RuntimeError('Docker operation rename failed; exit 1')
            item['Name'] = '/' + args[2]
        return SimpleNamespace(returncode=0, stdout=out, stderr='')

    def sql(self, query, database='postgres', check=True):
        self.step('sql')
        if any(word in query for word in self.failing_sql):
            raise RuntimeError('lock timeout')
        self.queries.append(query)
        if 'datconnlimit' in query:
            if self.budget is None:
                raise RuntimeError('measurement')
            out = self.budget
        elif REST_SETTINGS in query and 'string_agg' in query:
            out = self.settings or runtime.rest_exact(re.search(r"'(e_[a-f0-9]{24})_rest'", query).group(1))
        elif 'sbarbase_guard' in query:
            out = 't'
        else:
            out = ''
        return SimpleNamespace(returncode=0, stdout=out, stderr='')

    def answers_at(self, address):
        if address in self.unhealthy:
            left = self.answers.get(address, 0)
            if left <= 0:
                return False
            self.answers[address] = left - 1
        return any(item['State']['Running'] and item['ip'] == address for item in self.containers.values())

    def wait_ready(self, url, headers=None, failure='Runtime readiness timed out', attempts=60):
        self.step('wait')
        if not self.answers_at(url.split('//')[1].split(':')[0]):
            raise RuntimeError(failure)

    def http(self, url, *args, **kwargs):
        self.step('health')
        return (200 if self.answers_at(url.split('//')[1].split(':')[0]) else 503), b''

    def sleep(self, seconds):
        self.clock += seconds

    def monotonic(self):
        return self.clock

    def limits(self, service='rest'):
        """The last connection limits written for the login and the database."""
        written = [q for q in self.queries if f'ALTER ROLE {E}_{service} CONNECTION LIMIT' in q]
        return written[-1] if written else None


class Fixture(unittest.TestCase):
    service = 'rest'

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'state').mkdir()
        (self.root / 'private').mkdir()
        environ = patch.dict(os.environ, {rolling.SWITCH: 'auth,rest', rolling.CANARY: ''})
        environ.start()
        self.addCleanup(environ.stop)
        self.fake = None

    @property
    def state(self):
        return self.root / 'state'

    def name(self, service=None, e=E):
        return rolling.canonical(e, service or self.service)

    def build(self, crash=None, running=True, environments=(E,), image=OLD_IMAGE):
        fake = self.fake = Fake(crash)
        # The environment runs Edge Functions: a new container joins their network too.
        fake.networks.add(runtime.functions_network(E))
        for folder in (self.state, self.root / 'private'):
            for leftover in folder.iterdir():
                if leftover.is_file():
                    leftover.unlink()
        self.old, endpoints = {}, {}
        for e in environments:
            for service in ('auth', 'rest'):
                self.old[e, service] = fake.add(self.name(service, e), image, running)
                (self.root / 'private' / (self.name(service, e) + '.env')).write_text('X=old\n')
            endpoints[e] = {s: f"http://{fake.containers[self.old[e, s]]['ip']}:{p}" for s, (p, _) in rolling.SERVICES.items()}
        self.old.update({s: self.old[E, s] for s in ('auth', 'rest') if (E, s) in self.old})
        (self.state / 'endpoints.json').write_text(json.dumps(endpoints))
        target = runtime.Runtime.__new__(runtime.Runtime)
        target.values = {'environments': {e: {'rest': PASSWORD} for e in environments}}
        target.kept = {'containers': {cid: {'image': image, 'name': fake.containers[cid]['Name'].lstrip('/')}
                                      for cid in fake.containers}}
        target.touched = set()
        target.pins = {'auth': {'id': 'pin-new'}, 'rest': {'id': 'pin-new'}}
        target.image = Mock(return_value=('docker.io/fixture@sha256:' + '9' * 64, NEW_IMAGE))
        target.service_configuration = Mock(return_value={'X': 'new'})
        target.sql = fake.sql
        self.target = target
        return target

    def plan(self, environments=(E,), services=('auth', 'rest'), **extra):
        plan = {'version': 1, 'stopped': None, 'canary': None,
                'environments': {e: {s: {'state': 'old', 'from': OLD_IMAGE, 'to': NEW_IMAGE} for s in services} for e in environments}}
        plan.update(extra)
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        return plan

    def intent(self, pins=None):
        (self.state / 'upgrade-intent.json').write_text(json.dumps({'pins': pins or {'auth': 'pin-new', 'rest': 'pin-new'}}))

    def patches(self):
        fake = self.fake

        def atomic(path, value):
            fake.step('write ' + path.name)
            runtime.lab.atomic(path, value)
        return [patch.object(runtime, 'STATE', self.state), patch.object(runtime, 'PRIVATE', self.root / 'private'),
                patch.object(data_api, 'OPERATION_LOCK', self.state / 'operation.lock'),
                patch.object(runtime, 'UPGRADE_INTENT', self.state / 'upgrade-intent.json'),
                patch.object(runtime, 'inspect', side_effect=fake.inspect),
                patch.object(runtime.lab, 'docker', side_effect=fake.docker),
                patch.object(runtime, 'atomic', side_effect=atomic),
                patch.object(runtime, 'wait_ready', side_effect=fake.wait_ready),
                patch.object(runtime, 'http', side_effect=fake.http),
                patch.object(runtime.source_fence, 'is_fenced', return_value=False),
                patch.object(resource_admission, 'snapshot', return_value=None),
                patch.object(resource_admission, 'refusal', return_value=None),
                patch.object(rolling.time, 'sleep', side_effect=fake.sleep),
                patch.object(rolling.time, 'monotonic', side_effect=fake.monotonic),
                patch('mail_config.load', return_value=None),
                # Moving the settings file is a step of its own, so a crash can fall between it and the rename.
                patch.object(rolling, 'take_settings', side_effect=lambda *a: (fake.step('settings'), self.take_settings(*a))),
                # The block device the IO limits name, which a launch would otherwise ask the daemon for.
                patch.object(runtime.resource_policy, 'device', return_value='/dev/fixture'),
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO)]

    take_settings = staticmethod(rolling.take_settings)

    def run_patched(self, function, *args, **kwargs):
        patches = self.patches()
        for item in patches:
            item.start()
        try:
            return function(*args, **kwargs)
        finally:
            for item in reversed(patches):
                item.stop()

    def roll(self):
        return self.run_patched(rolling.roll_once, self.target)

    def swap(self, service=None):
        service = service or self.service
        return self.run_patched(rolling.swap, self.target, E, service,
                                {'state': 'old', 'from': OLD_IMAGE, 'to': NEW_IMAGE})

    def endpoints(self, e=E):
        return json.loads((self.state / 'endpoints.json').read_text())[e]

    def record(self):
        path = self.state / rolling.NAME
        return json.loads(path.read_text()) if path.exists() else None

    def saved_plan(self):
        path = self.state / rolling.PLAN
        return json.loads(path.read_text()) if path.exists() else None

    def url(self, cid, service=None):
        return f"http://{self.fake.containers[cid]['ip']}:{rolling.SERVICES[service or self.service][0]}"

    def settings_of(self, name):
        path = self.root / 'private' / (name + '.env')
        return path.read_text() if path.exists() else None


class StartTests(Fixture):
    """The start keeps the previous pin for what the supervisor replaces later."""

    def launch(self, service='rest', env=None):
        return self.run_patched(lambda: self.target.launch(self.name(service), service, env or {'X': 'old'}, '256m', .25,
                                                           existing_only=True, tier='production'))

    def test_a_pin_change_keeps_the_previous_container_and_records_it_in_the_plan(self):
        self.build()
        self.intent()
        self.run_patched(rolling.begin, self.target)
        cid, created = self.launch()
        self.assertEqual((cid, created), (self.old['rest'], False))
        self.assertNotIn('rm', self.fake.calls)
        self.assertNotIn('run', self.fake.calls)
        self.assertEqual(self.saved_plan()['environments'], {E: {'rest': {'state': 'old', 'from': OLD_IMAGE, 'to': NEW_IMAGE}}})
        self.run_patched(rolling.finish_start, self.target)
        self.assertTrue(self.run_patched(rolling.due))

    def test_with_the_switch_none_or_zero_the_start_replaces_in_place_as_before(self):
        for value in ('none', '0'):
            with self.subTest(value):
                self.build()
                self.intent()
                with patch.dict(os.environ, {rolling.SWITCH: value}):
                    cid, created = self.launch()
                self.assertTrue(created)
                self.assertEqual(self.fake.calls[:3], ['stop', 'rm', 'run'])
                self.assertIsNone(self.saved_plan())

    def test_unset_or_empty_rolls_rest_and_replaces_auth_in_place(self):
        for value in (None, ''):
            with self.subTest(value):
                self.build()
                self.intent()
                environ = {k: v for k, v in os.environ.items() if k != rolling.SWITCH}
                if value is not None:
                    environ[rolling.SWITCH] = value
                with patch.dict(os.environ, environ, clear=True):
                    self.assertEqual(rolling.chosen(), frozenset({'rest'}))
                    cid, created = self.launch()
                    self.assertEqual((cid, created), (self.old['rest'], False))
                    self.assertNotIn('rm', self.fake.calls)
                    self.assertEqual(self.saved_plan()['environments'], {E: {'rest': {'state': 'old', 'from': OLD_IMAGE, 'to': NEW_IMAGE}}})
                    self.assertEqual(rolling.chosen() & {'auth'}, frozenset())

    def test_a_container_whose_settings_changed_too_is_replaced_in_place(self):
        self.build()
        self.intent()
        cid, created = self.launch(env={'X': 'changed'})
        self.assertTrue(created)
        self.assertIsNone(self.saved_plan())

    def test_a_later_start_keeps_what_the_plan_holds_and_refuses_any_other_drift(self):
        self.build()
        self.plan(services=('rest',))
        # No upgrade intent any more (the confirmation removed it): the plan alone allows the previous pin.
        self.assertEqual(self.launch()[0], self.old['rest'])
        with self.assertRaisesRegex(RuntimeError, 'drift'):
            self.launch('auth')
        # A plan for a container on another image, or for another pinned image, allows nothing.
        plan = self.saved_plan()
        plan['environments'][E]['rest']['from'] = 'sha256:' + '3' * 64
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        with self.assertRaisesRegex(RuntimeError, 'drift'):
            self.launch()
        self.plan(services=('rest',), environments=(E,))
        plan = self.saved_plan()
        plan['environments'][E]['rest']['to'] = 'sha256:' + '3' * 64
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        with self.assertRaisesRegex(RuntimeError, 'drift'):
            self.launch()

    def test_with_the_switch_turned_off_a_planned_replacement_finishes_in_place_and_the_plan_goes(self):
        for value in ('none', '0', 'auth'):
            with self.subTest(value):
                self.build()
                self.plan(services=('rest',))
                self.run_patched(rolling.begin, self.target)
                with patch.dict(os.environ, {rolling.SWITCH: value}):
                    cid, created = self.launch()
                    self.assertTrue(created)
                    self.assertEqual(self.fake.calls[:3], ['stop', 'rm', 'run'])
                    self.run_patched(rolling.finish_start, self.target)
                self.assertIsNone(self.saved_plan())

    def test_the_start_of_an_upgrade_begins_a_new_plan_and_the_start_keeps_only_what_it_deferred(self):
        self.build()
        self.plan(environments=(E, F), stopped='earlier')
        self.intent()
        self.run_patched(rolling.begin, self.target)
        self.assertIsNone(self.saved_plan())
        self.plan(environments=(E, F))
        self.target.deferred = {(E, 'rest')}
        self.run_patched(rolling.finish_start, self.target)
        self.assertEqual(list(self.saved_plan()['environments']), [E])
        self.assertEqual(list(self.saved_plan()['environments'][E]), ['rest'])
        self.target.deferred = set()
        self.run_patched(rolling.finish_start, self.target)
        self.assertIsNone(self.saved_plan())

    def start(self, order, switch='rest', real_repair=False):
        """Runtime.start with everything but its order of steps faked."""
        target = self.target
        target.values.update({key: 'f' * 64 for key in ('admin', 'storage_control', 'storage_admin', 'encryption')})
        target.hba_writer = Mock()
        for method in ('wait_database', 'ensure_connection_capacity', 'management', 'wait'):
            setattr(target, method, Mock())
        target.launch = Mock(side_effect=lambda *a, **k: order.append('docker') or ('f' * 64, False))
        target.endpoint = Mock(return_value='http://172.30.0.9:5432')
        target.sql = Mock(return_value=SimpleNamespace(returncode=0, stdout='1', stderr=''))
        target.resume_published_environments = Mock(side_effect=lambda: order.append('resume'))
        target.retire_unadopted = Mock()
        meminfo = Mock(return_value=SimpleNamespace(read_text=lambda: 'MemAvailable: 999999999 kB\n'))
        with patch.dict(os.environ, {rolling.SWITCH: switch}), \
                patch.object(runtime.effect_receipt, 'require_settled'), \
                patch.object(runtime.lab, 'Path', meminfo), \
                patch.object(runtime.resource_policy, 'start_placement', return_value=(1, 1)), \
                patch.object(runtime.kept_runtime, 'current', return_value=(None, 'no record')), \
                patch.object(rolling, 'repair', side_effect=(rolling.repair if real_repair else lambda r: order.append('repair'))), \
                patch.object(rolling, 'begin', side_effect=lambda r: order.append('begin')), \
                patch.object(rolling, 'finish_start', side_effect=lambda r: order.append('finish')):
            self.run_patched(target.start)

    def test_a_start_repairs_an_interrupted_swap_before_it_resumes_any_environment(self):
        self.build()
        order = []
        with patch.object(self.fake, 'docker', side_effect=lambda *a, **k: order.append('docker') or Fake.docker(self.fake, *a, **k)):
            self.start(order)
        tail = [step for step in order if step != 'docker']
        self.assertEqual(tail, ['repair', 'begin', 'resume', 'finish'])

    def test_a_switch_naming_anything_else_refuses_the_start_before_any_docker_call(self):
        self.build()
        order = []
        with self.assertRaisesRegex(RuntimeError, 'may be empty .rest., rest, auth'):
            with patch.object(self.fake, 'docker', side_effect=lambda *a, **k: order.append('docker')):
                self.start(order, switch='rest,storage')
        self.assertEqual(order, [])

    def test_a_switch_naming_anything_else_refuses(self):
        for value in ('rest,storage', 'yes', 'rest,'):
            with self.subTest(value), patch.dict(os.environ, {rolling.SWITCH: value}):
                with self.assertRaisesRegex(RuntimeError, 'may be empty .rest., rest, auth'):
                    rolling.chosen()
        with patch.dict(os.environ, {rolling.SWITCH: ' rest , auth '}):
            self.assertEqual(rolling.chosen(), {'auth', 'rest'})
        for environ in ({}, {rolling.SWITCH: ''}, {rolling.SWITCH: '  '}):
            with patch.dict(os.environ, environ, clear=True):
                self.assertEqual(rolling.chosen(), frozenset({'rest'}))
        for value in ('none', '0', ' none ', 'off', 'false', 'OFF', 'None', 'False'):
            with self.subTest(value), patch.dict(os.environ, {rolling.SWITCH: value}):
                self.assertEqual(rolling.chosen(), frozenset())
        for value, names in (('auth', {'auth'}), ('rest', {'rest'}), ('auth,rest', {'auth', 'rest'})):
            with patch.dict(os.environ, {rolling.SWITCH: value}):
                self.assertEqual(rolling.chosen(), names)
        for value in ('none,rest', 'none,none', '0,rest'):
            with self.subTest(value), patch.dict(os.environ, {rolling.SWITCH: value}):
                with self.assertRaisesRegex(RuntimeError, 'may be empty .rest., rest, auth'):
                    rolling.chosen()


class RollTests(Fixture):
    """The supervisor's roll, after the confirmation, with the gateway serving."""

    def test_rest_is_replaced_side_by_side_and_the_old_one_runs_through_the_watch_window(self):
        self.build()
        new = self.swap()
        fake = self.fake
        self.assertEqual(fake.named(self.name()), new)
        self.assertIsNone(fake.find(self.name() + rolling.SUFFIX))
        self.assertNotIn(self.old['rest'], fake.containers)
        self.assertEqual(self.endpoints()['rest'], self.url(new))
        self.assertIsNone(self.record())
        calls = fake.calls
        order = [calls.index('write rolling.json'), calls.index('run'), calls.index('network connect'),
                 calls.index('write endpoints.json'), calls.index('health'), calls.index('stop'), calls.index('rm'),
                 calls.index('rename')]
        self.assertEqual(order, sorted(order))
        # The old one ran until the watch window ended: REST's longest deadline and a margin, never less.
        window = max(runtime.REST_STATEMENT_SECONDS, runtime.REST_TRANSACTION_SECONDS) + rolling.DRAIN_MARGIN_SECONDS
        self.assertEqual(rolling.watch_seconds(), window)
        self.assertGreaterEqual(fake.clock, window)
        self.assertGreaterEqual(calls.count('health'), window // rolling.CHECK_SECONDS)
        self.assertLess(calls.index('write endpoints.json'), calls.index('stop'))
        # The new container's settings file now carries the canonical name; the old one's is gone.
        self.assertEqual(self.settings_of(self.name()), 'X=new\n')
        self.assertIsNone(self.settings_of(self.name() + rolling.SUFFIX))
        self.assertIn(f'ALTER ROLE {E}_rest CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};', fake.limits())
        self.assertIn(f'ALTER DATABASE {E} CONNECTION LIMIT {connection_budget.ENVIRONMENT_LIMIT};', fake.limits())
        self.assertIn(new, self.target.touched)

    def test_both_limits_are_raised_within_bounds_for_the_swap_and_given_back(self):
        self.build()
        self.swap()
        widened = next(q for q in self.fake.queries if f'CONNECTION LIMIT {2 * connection_budget.SERVICE_LIMIT};' in q)
        self.assertIn(f'ALTER DATABASE {E} CONNECTION LIMIT {connection_budget.ENVIRONMENT_LIMIT + connection_budget.SWAP_CONNECTIONS};', widened)
        for query in (widened, self.fake.limits()):
            self.assertIn("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s';", query)

    def test_a_limit_that_cannot_be_given_back_beside_the_sessions_is_given_back_after_ending_them(self):
        self.build()
        calls = []
        real = self.fake.sql

        def sql(query, *a, **k):
            if f'ALTER ROLE {E}_rest CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};' in query:
                calls.append(query)
                if len(calls) == 1:
                    raise RuntimeError('lock timeout')
            return real(query, *a, **k)
        self.target.sql = sql
        self.swap()
        self.assertNotIn('pg_terminate_backend', calls[0])
        self.assertIn(f"a.usename = '{E}_rest'", calls[1])

    def test_the_new_rest_serves_no_session_opened_before_the_settings_were_written(self):
        self.build()
        self.swap()
        queries = self.fake.queries
        reads = [i for i, q in enumerate(queries) if REST_SETTINGS in q]
        ended = next(i for i, q in enumerate(queries) if 'client_addr' in q)
        self.assertTrue(any(i < ended for i in reads) and any(i > ended for i in reads))
        self.assertFalse(any('pg_terminate_backend' in q and 'client_addr' not in q for q in queries[:ended]))

    def assert_back_on_the_old_one(self, error, pattern):
        fake = self.fake
        self.assertRegex(str(error), pattern)
        self.assertEqual(fake.named(self.name()), self.old[self.service])
        self.assertTrue(fake.containers[self.old[self.service]]['State']['Running'])
        self.assertIsNone(fake.find(self.name() + rolling.SUFFIX))
        self.assertEqual(self.endpoints()[self.service], self.url(self.old[self.service]))
        self.assertIsNone(self.record())
        self.assertEqual(self.settings_of(self.name()), 'X=old\n')
        self.assertIsNone(self.settings_of(self.name() + rolling.SUFFIX))
        if fake.limits(self.service):
            self.assertIn(f'CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};', fake.limits(self.service))

    def test_a_new_container_that_fails_after_the_switch_is_switched_back_within_the_watch_window(self):
        self.build()
        # It answers its start, its reconnect and the first checks after the switch, then stops answering.
        self.fake.unhealthy, self.fake.answers = {'172.30.0.4'}, {'172.30.0.4': 5}
        with self.assertRaises(rolling.RollError) as caught:
            self.swap()
        self.assert_back_on_the_old_one(caught.exception, 'stopped answering after the switch')
        self.assertIn('write endpoints.json', self.fake.calls)

    def test_a_new_container_that_never_answers_is_removed_and_the_old_one_keeps_serving(self):
        self.build()
        self.fake.unhealthy = {'172.30.0.4'}
        with self.assertRaises(rolling.RollError) as caught:
            self.swap()
        self.assert_back_on_the_old_one(caught.exception, 'did not become ready')

    def test_a_rest_that_does_not_reconnect_after_its_sessions_end_is_never_switched_to(self):
        self.build()
        # It answers its start, then not again after the pin ended its sessions.
        self.fake.unhealthy, self.fake.answers = {'172.30.0.4'}, {'172.30.0.4': 1}
        with self.assertRaises(rolling.RollError) as caught:
            self.swap()
        self.assert_back_on_the_old_one(caught.exception, 'settings could not be written')
        self.assertNotIn('write endpoints.json', self.fake.calls)

    def test_settings_a_tenant_changed_meanwhile_stop_the_swap_with_the_old_one_serving(self):
        self.build()
        self.fake.settings = runtime.rest_exact(E) + f',{E}:work_mem=64kB'
        with self.assertRaises(rolling.RollError) as caught:
            self.swap()
        self.assert_back_on_the_old_one(caught.exception, 'settings could not be written')

    def test_a_switch_that_fails_switches_back_to_the_old_one(self):
        self.build()
        real, attempts = rolling.point, []

        def point(e, service, url):
            attempts.append(url)
            if len(attempts) == 1:
                raise OSError('disk full')
            return real(e, service, url)
        with patch.object(rolling, 'point', side_effect=point):
            with self.assertRaises(rolling.RollError) as caught:
                self.swap()
        self.assert_back_on_the_old_one(caught.exception, 'switch to the new rest failed')

    def test_auth_is_replaced_side_by_side_without_the_rest_settings(self):
        self.service = 'auth'
        self.build()
        new = self.swap()
        self.assertEqual(self.endpoints()['auth'], self.url(new))
        self.assertNotIn(self.old['auth'], self.fake.containers)
        self.assertFalse(any(REST_SETTINGS in q or 'client_addr' in q for q in self.fake.queries))

    def test_no_room_or_a_failed_measurement_changes_nothing(self):
        for label in ('connections', 'measurement', 'memory'):
            with self.subTest(label):
                self.build()
                if label == 'connections':
                    self.fake.budget = '197|190'
                elif label == 'measurement':
                    self.fake.budget = None
                refusal = 'memory_headroom' if label == 'memory' else None
                patches = [patch.object(resource_admission, 'refusal', return_value=refusal)]
                with patches[0]:
                    real = self.patches
                    self.patches = lambda: [p for p in real() if getattr(p, 'attribute', None) != 'refusal']
                    try:
                        with self.assertRaisesRegex(rolling.RollError, 'no room'):
                            self.swap()
                    finally:
                        self.patches = real
                self.assertNotIn('write rolling.json', self.fake.calls)
                self.assertNotIn('run', self.fake.calls)
                self.assertTrue(self.fake.containers[self.old['rest']]['State']['Running'])

    def test_limits_that_cannot_be_raised_leave_no_record_behind(self):
        self.build()
        self.fake.failing_sql = (f'CONNECTION LIMIT {2 * connection_budget.SERVICE_LIMIT};',)
        with self.assertRaises(rolling.RollError) as caught:
            self.swap()
        self.assert_back_on_the_old_one(caught.exception, 'could not be raised')
        self.assertNotIn('run', self.fake.calls)

    def test_the_canary_goes_first_one_environment_per_run_and_the_first_failure_stops_the_rest(self):
        self.build(environments=(E, F, G))
        self.plan(environments=(E, F, G))
        with patch.dict(os.environ, {rolling.CANARY: G}):
            self.assertEqual(self.roll(), 0)
        plan = self.saved_plan()
        self.assertEqual(plan['canary'], G, plan)
        self.assertIsNone(plan['stopped'], plan)
        self.assertEqual({s: i['state'] for s, i in plan['environments'][G].items()}, {'auth': 'new', 'rest': 'new'})
        self.assertEqual(plan['environments'][E]['rest']['state'], 'old')
        # The next run takes the first by name; its new REST fails after the switch.
        address = f'172.30.0.{self.fake.serial + 3}'
        self.fake.unhealthy, self.fake.answers = {address}, {address: 5}
        self.assertEqual(self.roll(), 0)
        plan = self.saved_plan()
        self.assertEqual(plan['environments'][E]['auth']['state'], 'new')
        self.assertEqual(plan['environments'][E]['rest']['state'], 'failed')
        self.assertIn(f'{E} rest', plan['stopped'])
        self.assertEqual({s: i['state'] for s, i in plan['environments'][F].items()}, {'auth': 'old', 'rest': 'old'})
        self.assertFalse(self.run_patched(rolling.due))
        self.assertEqual(self.roll(), rolling.IDLE)
        self.assertEqual(self.fake.named(self.name('rest', F)), self.old[F, 'rest'])
        # The console reads which environment runs which pin.
        status = self.run_patched(rolling.status)
        self.assertEqual(status['environments'][F], {'auth': 'old', 'rest': 'old'})
        self.assertEqual(status['environments'][E]['rest'], 'failed')
        # A retry starts again from the failed one.
        self.assertTrue(self.run_patched(rolling.retry))
        self.assertEqual(self.saved_plan()['environments'][E]['rest']['state'], 'old')
        self.assertTrue(self.run_patched(rolling.due))

    def test_a_failed_auth_leaves_the_environments_rest_untouched(self):
        self.build()
        self.plan()
        self.fake.unhealthy = {f'172.30.0.{self.fake.serial + 2}'}
        self.assertEqual(self.roll(), 0)
        plan = self.saved_plan()
        self.assertEqual(plan['environments'][E]['auth']['state'], 'failed')
        self.assertIn(f'{E} auth: the new auth did not become ready', plan['stopped'])
        self.assertEqual(plan['environments'][E]['rest']['state'], 'old')
        self.assertEqual(self.fake.calls.count('run'), 1)
        self.assertEqual(self.fake.named(self.name('rest')), self.old['rest'])

    def test_the_last_environment_ends_the_plan(self):
        self.build()
        self.plan()
        self.assertEqual(self.roll(), 0)
        self.assertIsNone(self.saved_plan())
        self.assertEqual(self.roll(), rolling.IDLE)

    def test_an_environment_whose_container_is_already_on_the_new_image_is_marked_new(self):
        self.build(image=NEW_IMAGE)
        self.plan(services=('rest',))
        self.assertEqual(self.roll(), 0)
        self.assertIsNone(self.saved_plan())
        self.assertNotIn('run', self.fake.calls)

    def test_a_container_or_pin_that_is_not_the_planned_one_stops_the_roll(self):
        for label in ('container', 'pin'):
            with self.subTest(label):
                self.build(image='sha256:' + '7' * 64 if label == 'container' else OLD_IMAGE)
                self.plan(services=('rest',))
                if label == 'pin':
                    self.target.image = Mock(return_value=('x', 'sha256:' + '8' * 64))
                self.roll()
                self.assertIsNotNone(self.saved_plan()['stopped'])
                self.assertNotIn('run', self.fake.calls)

    def test_a_paused_environment_waits(self):
        self.build()
        self.plan()
        (self.state / 'write-pauses').mkdir()
        (self.state / 'write-pauses' / f'{E}.json').write_text(json.dumps({'version': 1, 'runtime': E, 'phase': 'held'}))
        self.assertEqual(self.roll(), rolling.IDLE)
        self.assertNotIn('run', self.fake.calls)

    def test_the_kept_runtime_record_stays_consistent_after_the_swap(self):
        self.build()
        new = self.swap()
        stopped = []
        self.fake.docker = Mock(side_effect=lambda *a, **k: stopped.append(a) or Fake.docker(self.fake, *a, **k))
        self.target.touched.add(self.old['auth'])
        self.run_patched(self.target.retire_unadopted)
        self.assertTrue(self.fake.containers[new]['State']['Running'])
        self.assertEqual(stopped, [])

    def test_the_swap_budget_counts_the_promised_limits_the_swap_and_the_reserve(self):
        reserve = connection_budget.OPERATIONS_RESERVE
        self.assertTrue(connection_budget.swap_fits(197, 197 - reserve - connection_budget.SWAP_CONNECTIONS))
        self.assertFalse(connection_budget.swap_fits(197, 197 - reserve - connection_budget.SWAP_CONNECTIONS + 1))
        for bad in ((-1, 0), (1, True), ('197', 0)):
            with self.assertRaises(ValueError):
                connection_budget.swap_fits(*bad)


class LockTests(Fixture):
    def test_a_roll_waits_while_the_operation_lock_or_a_restore_holds(self):
        import fcntl
        self.build()
        self.plan()
        for name in ('operation.lock', 'backup.lock'):
            with self.subTest(name), patch.object(runtime, 'STATE', self.state), \
                    patch.object(rolling, 'roll_once', side_effect=AssertionError('rolled')), \
                    (self.state / name).open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                self.assertEqual(rolling.main(['roll']), rolling.BUSY)


class CrashTests(Fixture):
    def complete_steps(self):
        self.build()
        self.swap()
        return list(self.fake.calls)

    def check_repaired(self, before):
        fake = self.fake
        self.assertIsNone(self.record())
        names = [item['Name'].lstrip('/') for item in fake.containers.values()]
        self.assertEqual(names.count(self.name()), 1)
        self.assertNotIn(self.name() + rolling.SUFFIX, names)
        current = fake.find(self.name())
        forward = before is not None and before['phase'] == 'retiring'
        self.assertEqual(current['Id'], before['new'] if forward else self.old[self.service])
        # The settings file under the canonical name is that container's, and no temporary one is left.
        self.assertEqual(self.settings_of(self.name()), 'X=new\n' if forward else 'X=old\n')
        self.assertIsNone(self.settings_of(self.name() + rolling.SUFFIX))
        if current['State']['Running']:
            self.assertEqual(self.endpoints()[self.service], self.url(current['Id']))
        if before is not None:
            self.assertIn(f'ALTER ROLE {E}_{self.service} CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};', fake.limits(self.service))
        # Limits a swap raised are always given back: the record is written before them, so a crash after the
        # raise always leaves a record for the repair.
        raised = f'ALTER ROLE {E}_{self.service} CONNECTION LIMIT {connection_budget.SERVICE_LIMIT + connection_budget.SWAP_CONNECTIONS};'
        if any(raised in q for q in fake.queries):
            self.assertIn(f'ALTER ROLE {E}_{self.service} CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};', fake.limits(self.service))
        alive = [item for item in fake.containers.values() if item['Name'].lstrip('/').startswith(self.name()) and item['State']['Running']]
        self.assertLessEqual(len(alive), 1)

    def crash_everywhere(self):
        labels = self.complete_steps()
        self.assertGreater(len(labels), 20)
        for crash in range(len(labels)):
            for stopped in (False, True):
                with self.subTest(crash=crash, before=labels[crash], stopped=stopped):
                    self.build(crash)
                    with self.assertRaises(Crash):
                        self.swap()
                    before = self.record()
                    if stopped:
                        # The leftover runtime stops every owned container after a killed supervisor.
                        for item in self.fake.containers.values():
                            self.fake.running(item, False)
                    self.fake.crash = None
                    self.run_patched(rolling.repair, self.target)
                    self.check_repaired(before)

    def test_a_crash_at_every_step_of_a_rest_replacement_is_repaired(self):
        self.crash_everywhere()

    def test_a_crash_at_every_step_of_an_auth_replacement_is_repaired(self):
        self.service = 'auth'
        self.crash_everywhere()

    def test_the_record_names_the_new_container_as_soon_as_it_exists(self):
        labels = self.complete_steps()
        # Crashed right after the new container started and was recorded: repair knows which one it is, and
        # refuses a different one under the temporary name.
        self.build(labels.index('network connect'))
        with self.assertRaises(Crash):
            self.swap()
        self.assertEqual(self.record()['new'], self.fake.named(self.name() + rolling.SUFFIX))

    def test_a_crash_between_the_rename_and_the_settings_file_is_repaired(self):
        labels = self.complete_steps()
        self.build(labels.index('settings'))
        with self.assertRaises(Crash):
            self.swap()
        self.assertEqual(self.fake.find(self.name())['Id'], self.record()['new'])
        self.assertEqual(self.settings_of(self.name()), 'X=old\n')
        before = self.record()
        self.fake.crash = None
        self.run_patched(rolling.repair, self.target)
        self.check_repaired(before)

    def test_a_crash_at_every_step_of_a_repair_is_repaired_again(self):
        labels = self.complete_steps()
        checked = 0
        for crash in range(len(labels)):
            for again in range(len(labels)):
                self.build(crash)
                try:
                    self.swap()
                except Crash:
                    pass
                before = self.record()
                if before is None:
                    break
                self.fake.crash, self.fake.steps = again, 0
                try:
                    self.run_patched(rolling.repair, self.target)
                except Crash:
                    pass
                finished = self.record() is None
                before = self.record() or before
                self.fake.crash = None
                self.run_patched(rolling.repair, self.target)
                with self.subTest(crash=crash, again=again):
                    self.check_repaired(before)
                checked += 1
                if finished:
                    break
        self.assertGreater(checked, len(labels))

    def test_containers_that_are_not_as_recorded_refuse(self):
        base = {'version': 1, 'environment': E, 'service': 'rest', 'at': 1}
        cases = {
            'starting, old gone': ('starting', lambda: self.fake.containers.pop(self.old['rest'])),
            'retiring, both gone': ('retiring', lambda: self.fake.containers.pop(self.old['rest'])),
            'foreign temporary': ('retiring', lambda: self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE)),
            'new and temporary both': ('retiring', lambda: (self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE),
                                                            self.fake.containers.__setitem__('new', None))),
        }
        for label, (phase, change) in cases.items():
            with self.subTest(label):
                self.build()
                record = {**base, 'phase': phase, 'old': self.old['rest'], 'new': 'f' * 64}
                change()
                if label == 'new and temporary both':
                    self.fake.containers.pop('new')
                    temporary = self.fake.find(self.name() + rolling.SUFFIX)
                    record['new'] = temporary['Id']
                    canonical = self.fake.containers.pop(self.old['rest'])
                    canonical['Id'] = record['new']
                    temporary['Id'] = record['new']
                    self.fake.containers = {'a': canonical, 'b': temporary}
                (self.state / rolling.NAME).write_text(json.dumps(record))
                with self.assertRaisesRegex(RuntimeError, 'explicit reconciliation'):
                    self.run_patched(rolling.repair, self.target)
                self.assertTrue((self.state / rolling.NAME).exists())

    def test_a_record_that_cannot_be_read_refuses(self):
        self.build()
        good = {'version': 1, 'environment': E, 'service': 'rest', 'phase': 'starting', 'old': self.old['rest'], 'new': None, 'at': 1}
        for text in ('{', json.dumps({**good, 'version': 2}), json.dumps({**good, 'phase': 'halfway'}),
                     json.dumps({**good, 'service': 'storage'}), json.dumps({**good, 'old': 7}), json.dumps({**good, 'new': 7}),
                     json.dumps({**good, 'environment': 'e_x'})):
            with self.subTest(text):
                (self.state / rolling.NAME).write_text(text)
                with self.assertRaisesRegex(RuntimeError, 'cannot be read'):
                    self.run_patched(rolling.repair, self.target)

    def test_a_plan_that_cannot_be_read_refuses(self):
        self.build()
        entry = {'state': 'old', 'from': OLD_IMAGE, 'to': NEW_IMAGE}
        good = {'version': 1, 'stopped': None, 'canary': None, 'environments': {E: {'rest': entry}}}
        for plan in ('{', {**good, 'version': 2}, {**good, 'environments': {E: {'storage': entry}}},
                     {**good, 'environments': {E: {'rest': {**entry, 'state': 'maybe'}}}}, {**good, 'stopped': 1},
                     {**good, 'environments': {'e_x': {'rest': entry}}}):
            with self.subTest(plan):
                (self.state / rolling.PLAN).write_text(plan if isinstance(plan, str) else json.dumps(plan))
                with self.assertRaisesRegex(RuntimeError, 'cannot be read'):
                    self.run_patched(rolling.read_plan)

    def test_a_swap_refuses_while_one_is_recorded_or_a_temporary_container_exists(self):
        self.build()
        (self.state / rolling.NAME).write_text(json.dumps(
            {'version': 1, 'environment': F, 'service': 'auth', 'phase': 'starting', 'old': 'x', 'new': None, 'at': 1}))
        with self.assertRaisesRegex(RuntimeError, 'already recorded'):
            self.swap()
        (self.state / rolling.NAME).unlink()
        self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE)
        with self.assertRaisesRegex(RuntimeError, 'without a recorded replacement'):
            self.swap()
        self.assertNotIn('run', self.fake.calls)


class SettledTests(Fixture):
    def pending(self):
        self.build()
        (self.state / rolling.NAME).write_text(json.dumps(
            {'version': 1, 'environment': E, 'service': 'rest', 'phase': 'starting', 'old': self.old['rest'], 'new': None, 'at': 1}))

    def test_backup_and_require_settled_refuse_while_a_swap_is_recorded(self):
        self.pending()
        with patch.object(runtime, 'STATE', self.state):
            with self.assertRaisesRegex(backup.BackupError, 'unfinished'):
                backup.require_service_images(E)
            with self.assertRaisesRegex(RuntimeError, 'unfinished'):
                rolling.require_settled(E)
            rolling.require_settled(F)
            (self.state / rolling.NAME).write_text('{')
            with self.assertRaisesRegex(RuntimeError, 'unfinished'):
                rolling.require_settled(F)

    def test_an_environment_on_its_previous_pin_refuses_the_commands_but_not_its_resume(self):
        self.build()
        self.plan(environments=(E,))
        with patch.object(runtime, 'STATE', self.state):
            with self.assertRaisesRegex(RuntimeError, 'previous pin'):
                rolling.require_settled(E)
            rolling.require_settled(E, plan=False)
            rolling.require_settled(F)
            (self.state / rolling.PLAN).write_text('{')
            with self.assertRaisesRegex(RuntimeError, 'previous pin'):
                rolling.require_settled(F)

    def test_a_write_pause_refuses_before_it_records_or_stops_anything(self):
        self.pending()
        writers, route = Mock(), Mock(return_value={E: {}})
        with patch.object(runtime, 'STATE', self.state):
            with self.assertRaisesRegex(write_pause.PauseError, 'unfinished'):
                write_pause.pause(E, state=self.root / 'pause', route=route, endpoints={E: {}}, writers=writers)
        writers.restart.assert_not_called()
        route.assert_not_called()
        self.assertFalse(write_pause.record_path(E, self.root / 'pause').exists())

    def test_resume_rotation_and_both_auth_reconciles_refuse_while_a_swap_is_recorded(self):
        self.pending()
        self.target.activate_services = Mock()
        self.target.values['environments'][E].update({'jwt': 'x'})
        commands = {'resume': lambda: self.target.resume(E), 'rotate': lambda: self.target.rotate_signing(E),
                    'mail': lambda: self.target.reconcile_mail(E), 'sign-in': lambda: self.target.reconcile_auth(E)}
        self.fake.add(runtime.DB, 'sha256:db')
        self.fake.add(runtime.PREFIX + '-storage', 'sha256:storage')
        for label, command in commands.items():
            with self.subTest(label):
                with self.assertRaisesRegex(RuntimeError, 'unfinished'):
                    self.run_patched(command)
        self.target.activate_services.assert_not_called()
        self.assertNotIn('stop', self.fake.calls)


class ResumeTests(Fixture):
    def resume(self, guard='t'):
        self.target.activate_services = Mock()
        self.target.data_api = Mock(side_effect=lambda *a, **k: self.target.sql('-- data api'))
        self.target.scheduled_jobs_back = Mock()
        real = self.fake.sql
        self.target.sql = lambda q, *a, **k: (SimpleNamespace(returncode=0, stdout=guard, stderr='')
                                              if 'sbarbase_guard' in q else real(q, *a, **k))
        patches = self.patches() + [patch.object(runtime.effect_receipt, 'require_settled')]
        for item in patches:
            item.start()
        try:
            self.target.resume(E)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_a_kept_rest_keeps_serving_and_its_sessions_end_after_the_settings_and_after_the_data_api(self):
        self.build()
        self.resume()
        fake = self.fake
        self.assertNotIn('stop', fake.calls)
        pinned = next(i for i, q in enumerate(fake.queries) if f"ALTER ROLE {E}_rest PASSWORD '{PASSWORD}'" in q)
        marker = fake.queries.index('-- data api')
        self.assertIn('pg_terminate_backend', fake.queries[pinned])
        self.assertIn(rolling.sessions_sql(E), fake.queries[pinned + 1:marker])
        self.assertIn(rolling.sessions_sql(E), fake.queries[marker + 1:])

    def test_a_kept_rest_whose_settings_cannot_be_written_beside_it_is_stopped_as_before(self):
        self.build()
        self.fake.failing_sql = ('PASSWORD',)
        real = self.fake.sql
        self.fake.sql = lambda q, *a, **k: (Fake.sql(self.fake, q) if 'PASSWORD' not in q or self.fake.calls.count('stop')
                                            else (_ for _ in ()).throw(RuntimeError('lock timeout')))
        self.fake.failing_sql = ()
        self.target.sql = self.fake.sql
        self.resume()
        self.assertEqual(self.fake.calls.count('stop'), 1)
        self.assertFalse(self.fake.containers[self.old['rest']]['State']['Running'])

    def test_rest_stops_as_before_without_the_switch_without_a_kept_record_or_without_the_guard(self):
        for label in ('switch off', 'not kept', 'no guard'):
            with self.subTest(label):
                self.build()
                if label == 'not kept':
                    self.target.kept = None
                with patch.dict(os.environ, {rolling.SWITCH: 'none' if label == 'switch off' else 'rest'}):
                    self.resume(guard='f' if label == 'no guard' else 't')
                self.assertEqual(self.fake.calls[0], 'stop')
                self.assertFalse(any(q == rolling.sessions_sql(E) for q in self.fake.queries))


class SupervisorTests(Fixture):
    def supervisor(self):
        supervisor = dev.Supervisor.__new__(dev.Supervisor)
        supervisor.rolling, supervisor.rolling_after, supervisor.confirm = None, 0.0, None
        supervisor.drain, supervisor.update = None, None
        supervisor.spawn = Mock(return_value='child')
        return supervisor

    def test_the_supervisor_rolls_only_after_the_confirmation_and_while_nothing_pauses_it(self):
        self.build()
        self.plan()
        with patch.object(runtime, 'STATE', self.state):
            supervisor = self.supervisor()
            supervisor.confirm = object()
            supervisor.schedule_rolling()
            supervisor.spawn.assert_not_called()
            supervisor.confirm, supervisor.drain = None, {'kind': 'apply'}
            supervisor.schedule_rolling()
            supervisor.spawn.assert_not_called()
            supervisor.drain = None
            supervisor.schedule_rolling()
            supervisor.spawn.assert_called_once_with(['/usr/bin/python3', 'lab/rolling.py', 'roll'])
            # An update waits for the child: the supervisor is not idle while it runs.
            self.assertIsNotNone(supervisor.rolling)
            with patch.object(dev, 'child_status', return_value=rolling.BUSY), patch.object(dev, 'terminate_group'):
                supervisor.schedule_rolling()
            self.assertIsNone(supervisor.rolling)
            self.assertGreater(supervisor.rolling_after, time.monotonic() + 3)
            self.assertLess(supervisor.rolling_after, time.monotonic() + 10)
            # A stopped plan is not rolled.
            self.plan(stopped='canary failed')
            supervisor.rolling_after = 0.0
            supervisor.schedule_rolling()
            self.assertEqual(supervisor.spawn.call_count, 1)

    def test_current_json_and_status_say_which_environment_runs_which_pin(self):
        self.build(environments=(E, F))
        self.plan(environments=(E, F), stopped=f'{E} rest: failed')
        plan = self.saved_plan()
        plan['environments'][E]['rest']['state'] = 'failed'
        plan['environments'][E]['auth']['state'] = 'new'
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'STATE', self.state), \
                patch.object(updates, 'UPGRADES', Path(folder)), \
                patch.object(updates, 'apply_verdict', return_value=None), patch.object(updates, 'zone', return_value=None):
            record = updates.publish_current(None, running={'version': '1', 'commit': 'c' * 40}, rollback=(False, 'x.'))
            self.assertEqual(record['rolling']['environments'][F], {'auth': 'old', 'rest': 'old'})
            self.assertEqual(record['rolling']['environments'][E], {'auth': 'new', 'rest': 'failed'})
            self.assertIn('failed', record['rolling']['stopped'])
            with patch.object(upgrade, 'load_state', return_value={'from': 'a' * 40, 'to': 'b' * 40, 'started_at': 'x', 'phase': 'confirmed'}), \
                    patch.object(upgrade, 'git', return_value='c' * 40), patch('sys.stdout', new_callable=io.StringIO) as out:
                upgrade.status()
            self.assertIn(f'{F} rest old', out.getvalue())
            self.assertIn(f'{E} rest failed', out.getvalue())


class SupervisorLoopTests(Fixture):
    """The supervisor's run loop reaches the roll after the confirmation, survives it failing, waits for it
    before an update and ends it at stop (lab/dev.py)."""

    def supervisor(self):
        supervisor = dev.Supervisor.__new__(dev.Supervisor)
        for name in ('reset_studios', 'settle_updates', 'publish_current', 'start_worker', 'descriptor', 'check',
                     'schedule_backup', 'schedule_studios', 'schedule_sign_in', 'schedule_data_api', 'schedule_toggles',
                     'schedule_scheduler', 'schedule_updates', 'schedule_rolling'):
            setattr(supervisor, name, Mock())
        supervisor.spawn = Mock(return_value='server')
        supervisor.drain_marker = Mock()
        supervisor.host_watch = Mock()
        supervisor.confirm, supervisor.drain, supervisor.update, supervisor.backup = None, None, None, None
        supervisor.scheduler, supervisor.server, supervisor.worker = None, None, None
        supervisor.studios, supervisor.sign_in, supervisor.data_api, supervisor.toggles = {}, {}, {}, {}
        supervisor.rolling, supervisor.rolling_after, supervisor.updates_after = None, 0.0, float('inf')
        return supervisor

    def run_turns(self, supervisor, turns):
        supervisor.stop_event = Mock()
        supervisor.stop_event.wait = Mock(side_effect=[False] * turns + [True])
        with patch.object(dev, 'STATE', self.state), patch.object(dev, 'terminate_group') as terminate, \
                patch('sys.stderr', new_callable=io.StringIO):
            supervisor.run()
        return terminate

    def test_each_turn_after_the_confirmation_reaches_the_roll(self):
        supervisor = self.supervisor()
        confirm = Mock()
        confirm.poll = Mock(side_effect=[False, True, True])
        supervisor.confirm = confirm
        self.run_turns(supervisor, 3)
        # Not while the confirmation waits; on the turn that confirms and the next one.
        self.assertEqual(supervisor.schedule_rolling.call_count, 2)

    def test_a_roll_scheduling_that_raises_does_not_stop_the_installation(self):
        supervisor = self.supervisor()
        supervisor.schedule_rolling = Mock(side_effect=OSError('state unreadable'))
        self.run_turns(supervisor, 3)
        self.assertEqual(supervisor.schedule_rolling.call_count, 3)
        self.assertGreater(supervisor.rolling_after, time.monotonic() + 30)
        supervisor.schedule_toggles.assert_called()

    def test_the_roll_child_is_ended_at_stop(self):
        supervisor = self.supervisor()
        child = Mock()
        supervisor.schedule_rolling = Mock(side_effect=lambda: setattr(supervisor, 'rolling', child))
        terminate = self.run_turns(supervisor, 1)
        self.assertIn(unittest.mock.call(child), terminate.call_args_list)

    def test_an_update_drain_waits_for_the_roll_child(self):
        supervisor = self.supervisor()
        with patch.object(upgrade, 'present', return_value=False):
            self.assertTrue(dev.Supervisor.idle(supervisor))
            supervisor.rolling = Mock()
            self.assertFalse(dev.Supervisor.idle(supervisor))
            supervisor.rolling = None
            self.assertTrue(dev.Supervisor.idle(supervisor))

    def test_the_child_is_asked_again_after_5_seconds_when_busy_and_a_minute_after_a_failure(self):
        self.build()
        self.plan()
        for status, low, high in ((rolling.BUSY, 3, 10), (1, 50, 70), (0, 0, 3)):
            with self.subTest(status), patch.object(runtime, 'STATE', self.state), \
                    patch.object(dev, 'child_status', return_value=status), patch.object(dev, 'terminate_group'):
                supervisor = self.supervisor()
                del supervisor.schedule_rolling
                supervisor.rolling = Mock()
                dev.Supervisor.schedule_rolling(supervisor)
                self.assertIsNone(supervisor.rolling)
                self.assertTrue(time.monotonic() + low <= supervisor.rolling_after <= time.monotonic() + high)


class RollGuardTests(Fixture):
    """The roll's own refusals: it repairs first, and never touches what it must not."""

    def test_a_record_a_failed_roll_left_is_repaired_and_the_next_roll_goes_ahead(self):
        self.build()
        self.plan(services=('rest',))
        # A roll that died before its switch: the record and the new container are left.
        temporary = self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE)
        (self.state / rolling.NAME).write_text(json.dumps({'version': 1, 'environment': E, 'service': 'rest', 'phase': 'starting',
                                                          'old': self.old['rest'], 'new': temporary, 'at': 1}))
        self.assertTrue(self.run_patched(rolling.due))
        self.assertEqual(self.roll(), 0)
        self.assertIsNone(self.record())
        self.assertIsNone(self.saved_plan())
        self.assertNotIn(temporary, self.fake.containers)
        self.assertNotEqual(self.fake.named(self.name()), self.old['rest'])

    def test_a_pending_effect_receipt_refuses_the_roll(self):
        self.build()
        self.plan()
        with patch.object(runtime, 'STATE', self.state), \
                patch.object(runtime.effect_receipt, 'require_settled', side_effect=RuntimeError('pending receipt')), \
                patch.object(rolling, 'roll_once') as rolled, patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertEqual(rolling.main(['roll']), 1)
        rolled.assert_not_called()
        self.assertIn('pending receipt', err.getvalue())

    def test_a_fenced_or_unpublished_environment_is_never_swapped(self):
        for label in ('fenced', 'unpublished'):
            with self.subTest(label):
                self.build()
                self.plan()
                if label == 'unpublished':
                    (self.state / 'endpoints.json').write_text('{}')
                    self.assertEqual(self.roll(), rolling.IDLE)
                else:
                    real = self.patches
                    self.patches = lambda: [p for p in real() if getattr(p, 'attribute', None) != 'is_fenced'] + \
                        [patch.object(runtime.source_fence, 'is_fenced', return_value=True)]
                    try:
                        # Skipped, not idle: the other environments go on in the next run.
                        self.assertEqual(self.roll(), 0)
                    finally:
                        self.patches = real
                self.assertNotIn('run', self.fake.calls)
                item = self.saved_plan()['environments'][E]['rest']
                # An unpublished environment waits as it is; a fenced one is skipped with the reason.
                self.assertEqual(item['state'], 'skipped' if label == 'fenced' else 'old')
                if label == 'fenced':
                    self.assertIn('fenced', item['reason'])

    def test_a_stopped_container_is_never_swapped(self):
        self.build(running=False)
        with self.assertRaisesRegex(rolling.RollError, 'not running'):
            self.swap()
        self.assertNotIn('run', self.fake.calls)

    def test_the_new_container_answers_to_the_canonical_name_on_the_functions_network(self):
        self.build()
        connects = []
        real = self.fake.docker
        self.fake.docker = lambda *a, **k: (connects.append(a) if a[:2] == ('network', 'connect') else None) or real(*a, **k)
        self.swap()
        self.assertEqual(connects, [('network', 'connect', '--alias', self.name(), runtime.functions_network(E),
                                     self.name() + rolling.SUFFIX)])


class DataApiRaceTests(Fixture):
    """A console Data API change and a REST swap of the same environment never overlap."""

    def test_a_roll_waits_while_a_data_api_change_holds_the_environment(self):
        self.build()
        self.plan()
        with patch.object(data_api, 'OPERATION_LOCK', self.state / 'operation.lock'):
            held = data_api.environment_lock(E)
        try:
            self.assertEqual(self.roll(), rolling.BUSY)
        finally:
            held.close()
        self.assertNotIn('run', self.fake.calls)
        self.assertEqual(self.saved_plan()['environments'][E]['rest']['state'], 'old')

    def test_a_data_api_change_during_a_swap_is_refused_until_the_swap_ends(self):
        self.build()
        self.plan(services=('rest',))
        tried = []
        real = rolling.swap

        def swap(*args, **kwargs):
            # A console change that arrives while the new REST runs beside the old one.
            command = Mock(side_effect=lambda: data_api.environment_lock(E).close())
            try:
                command()
                tried.append('written')
            except BlockingIOError:
                tried.append('busy')
            return real(*args, **kwargs)
        with patch.object(rolling, 'swap', side_effect=swap):
            self.assertEqual(self.roll(), 0)
        self.assertEqual(tried, ['busy'])
        # The command itself asks again later (exit 75) rather than writing.
        with patch.object(data_api, 'OPERATION_LOCK', self.state / 'operation.lock'):
            data_api.environment_lock(E).close()


class StudioLimitTests(Fixture):
    """Studio writes its environment's database limit with a recorded swap counted, under the limit lock."""

    def lock_held(self):
        """Whether another holder has this environment's limit lock now, probed without waiting."""
        import fcntl
        with (self.state / f'connection-limit-{E}.lock').open('a') as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def test_studio_writes_its_limit_with_the_limit_lock_held(self):
        import studio
        self.build()
        for function in (studio.open_connections, studio.close_login):
            with self.subTest(function.__name__):
                held = []
                answer = lambda q, *a: (held.append(self.lock_held()) if 'CONNECTION LIMIT' in q else None) or '197|100'
                with patch.object(studio, 'runtime_sql', side_effect=answer):
                    self.run_patched(function, E)
                self.assertEqual(held, [True])

    def test_studio_waits_for_the_limit_lock_rather_than_failing(self):
        import fcntl
        import studio
        import threading
        self.build()
        written, outcome, reached = [], [], threading.Event()
        real = fcntl.flock

        def flock(handle, flags):
            if getattr(handle, 'name', '').endswith(f'connection-limit-{E}.lock'):
                self.assertEqual(flags, fcntl.LOCK_EX)
                reached.set()
            return real(handle, flags)

        def close():
            try:
                with patch.object(studio, 'runtime_sql', side_effect=lambda q, *a: written.append(q) or ''):
                    studio.close_login(E)
                outcome.append('done')
            except BaseException as error:
                outcome.append(error)
        with patch.object(runtime, 'STATE', self.state), patch.object(rolling.fcntl, 'flock', side_effect=flock):
            holder = rolling.limit_lock(E)
            worker = threading.Thread(target=close)
            try:
                worker.start()
                self.assertTrue(reached.wait(30))
                # Blocked on the lock, not failed: still running, nothing written yet.
                worker.join(.5)
                self.assertTrue(worker.is_alive())
                self.assertFalse([q for q in written if 'CONNECTION LIMIT' in q])
            finally:
                holder.close()
            worker.join(30)
        self.assertEqual(outcome, ['done'])
        self.assertTrue([q for q in written if 'CONNECTION LIMIT' in q])

    def test_the_record_goes_and_the_limits_come_back_only_under_the_limit_lock(self):
        for label in ('swap', 'repair'):
            with self.subTest(label):
                self.build()
                held = []
                real_sql, real_settle = self.fake.sql, rolling.settle

                def sql(query, *a, **k):
                    if 'ALTER DATABASE' in query:
                        held.append(('limit', self.lock_held()))
                    return real_sql(query, *a, **k)

                def settle(target):
                    if target.name == rolling.NAME:
                        held.append(('record', self.lock_held()))
                    return real_settle(target)
                self.target.sql = sql
                with patch.object(rolling, 'settle', side_effect=settle):
                    if label == 'swap':
                        self.swap()
                    else:
                        (self.state / rolling.NAME).write_text(json.dumps(
                            {'version': 1, 'environment': E, 'service': 'rest', 'phase': 'starting', 'old': self.old['rest'],
                             'new': None, 'at': 1}))
                        self.run_patched(rolling.repair, self.target)
                self.assertTrue(held and all(on for _, on in held), held)
                self.assertEqual(held[-1][0], 'record')

    def studio(self, function):
        import studio
        written = []
        with patch.object(studio, 'runtime_sql', side_effect=lambda q, *a: written.append(q) or '197|100'):
            self.run_patched(function, E)
        return written[-1]

    def test_opening_or_closing_studio_during_a_swap_keeps_the_swap_allowance(self):
        import studio
        self.build()
        base = connection_budget.ENVIRONMENT_LIMIT
        self.assertIn(f'CONNECTION LIMIT {base + connection_budget.STUDIO_CONNECTIONS};', self.studio(studio.open_connections))
        self.assertIn(f'CONNECTION LIMIT {base}', self.studio(studio.close_login))
        (self.state / rolling.NAME).write_text(json.dumps({'version': 1, 'environment': E, 'service': 'rest', 'phase': 'switched',
                                                          'old': 'x', 'new': None, 'at': 1}))
        swap = connection_budget.SWAP_CONNECTIONS
        self.assertIn(f'CONNECTION LIMIT {base + connection_budget.STUDIO_CONNECTIONS + swap};', self.studio(studio.open_connections))
        self.assertIn(f'CONNECTION LIMIT {base + swap}', self.studio(studio.close_login))
        # Another environment's swap adds nothing; an unreadable record counts as one.
        with patch.object(runtime, 'STATE', self.state):
            self.assertEqual(rolling.allowance(F), 0)
            (self.state / rolling.NAME).write_text('{')
            self.assertEqual(rolling.allowance(F), swap)

    def test_the_swap_raises_and_gives_back_the_limits_under_the_limit_lock(self):
        import fcntl
        self.build()
        held = []
        real = self.fake.sql

        def sql(query, *a, **k):
            if 'ALTER DATABASE' in query:
                with (self.state / f'connection-limit-{E}.lock').open('a') as other:
                    try:
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held.append(False)
                    except BlockingIOError:
                        held.append(True)
            return real(query, *a, **k)
        self.target.sql = sql
        self.swap()
        self.assertEqual(held, [True, True])


class StateTests(Fixture):
    """What the plan and the record report, allow and refuse."""

    def test_an_unreadable_plan_is_reported_and_not_rolled(self):
        self.build()
        (self.state / rolling.PLAN).write_text('{')
        status = self.run_patched(rolling.status)
        self.assertIn('cannot be read', status['stopped'])
        self.assertFalse(self.run_patched(rolling.due))

    def test_a_plan_of_only_failed_or_new_entries_has_nothing_due_and_settles_the_environment(self):
        self.build()
        self.plan()
        plan = self.saved_plan()
        plan['environments'][E]['rest']['state'] = 'failed'
        plan['environments'][E]['auth']['state'] = 'new'
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        self.assertFalse(self.run_patched(rolling.due))
        plan['environments'][E]['rest']['state'] = 'new'
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        self.run_patched(rolling.require_settled, E)

    def test_a_restart_keeps_a_failed_entry_failed(self):
        self.build()
        self.plan(services=('rest',), stopped=f'{E} rest: failed')
        plan = self.saved_plan()
        plan['environments'][E]['rest']['state'] = 'failed'
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        item = self.fake.inspect('container', self.name())
        self.assertTrue(self.run_patched(rolling.defer, self.target, self.name(), 'rest', item, NEW_IMAGE, 'pin-new'))
        self.assertEqual(self.saved_plan()['environments'][E]['rest']['state'], 'failed')
        # An entry already on the new pin allows nothing more.
        plan['environments'][E]['rest']['state'] = 'new'
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        self.assertFalse(self.run_patched(rolling.planned, self.name(), 'rest', item, NEW_IMAGE))

    def test_defer_only_for_the_service_the_name_says_and_launch_never_for_a_container_with_volumes_or_a_command(self):
        self.build()
        self.intent()
        item = self.fake.inspect('container', self.name())
        self.assertFalse(self.run_patched(rolling.defer, self.target, self.name('rest'), 'auth', item, NEW_IMAGE, 'pin-new'))
        cid, created = self.run_patched(lambda: self.target.launch(self.name(), 'rest', {'X': 'old'}, '256m', .25,
                                                                   command=('x',), existing_only=True, tier='production'))
        self.assertTrue(created)
        self.assertIsNone(self.saved_plan())

    def test_begin_forgets_what_an_earlier_start_deferred_and_finish_drops_a_canary_it_no_longer_holds(self):
        self.build()
        self.target.deferred = {(F, 'rest')}
        self.run_patched(rolling.begin, self.target)
        self.assertEqual(self.target.deferred, set())
        self.plan(environments=(E, F), canary=F)
        self.target.deferred = {(E, 'rest')}
        self.run_patched(rolling.finish_start, self.target)
        self.assertIsNone(self.saved_plan()['canary'])

    def test_removing_a_record_makes_its_directory_entry_durable_and_point_skips_an_unpublished_environment(self):
        self.build()
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime.effect_receipt, 'sync_directory') as synced:
            (self.state / rolling.NAME).write_text('{}')
            rolling.settle(rolling.path())
            synced.assert_called_once_with(self.state)
        before = (self.state / 'endpoints.json').read_text()
        self.run_patched(rolling.point, F, 'rest', 'http://172.30.0.99:3000')
        self.assertEqual((self.state / 'endpoints.json').read_text(), before)
        self.assertNotIn('write endpoints.json', self.fake.calls)

    def test_the_exact_settings_are_the_platforms_three(self):
        self.assertEqual(runtime.rest_exact(E), f'{E}:search_path=pg_catalog,{E}:statement_timeout=8s,{E}:transaction_timeout=12s')

    def test_the_limit_lock_names_only_an_environment(self):
        with patch.object(runtime, 'STATE', self.state):
            for bad in ('e_x', '../e_' + 'a' * 24, E + '/x'):
                with self.subTest(bad), self.assertRaises(RuntimeError):
                    rolling.limit_lock(bad)
        self.assertEqual([p.name for p in self.state.iterdir() if p.name.startswith('connection-limit')], [])

    def test_the_sql_builders_refuse_what_they_were_not_given_to_write(self):
        for call in (lambda: rolling.limits_sql(E, 'rest', -1, 18), lambda: rolling.limits_sql(E, 'rest', 6, '18'),
                     lambda: rolling.limits_sql('e_x', 'rest', 6, 18), lambda: rolling.sessions_sql('e_x'),
                     lambda: rolling.sessions_sql(E, 'storage')):
            with self.assertRaises(RuntimeError):
                call()

    def test_restore_and_pause_refuse_for_an_environment_on_its_previous_pin(self):
        self.build()
        self.plan()
        writers, route = Mock(), Mock(return_value={E: {}})
        with patch.object(runtime, 'STATE', self.state):
            with self.assertRaisesRegex(backup.BackupError, 'previous pin'):
                backup.require_service_images(E)
            with self.assertRaisesRegex(write_pause.PauseError, 'previous pin'):
                write_pause.pause(E, state=self.root / 'pause', route=route, endpoints={E: {}}, writers=writers)
        writers.restart.assert_not_called()


class RollbackRaceTests(Fixture):
    """An operator's rollback holds the operation lock from its checks to the move, so no swap starts between."""

    def test_a_roll_that_starts_during_the_rollback_waits(self):
        self.build()
        self.plan()
        seen = []
        state = {'phase': 'confirmed', 'from': 'a' * 40, 'to': 'b' * 40}

        def move(*args, **kwargs):
            # The supervisor's next roll child arrives while the checkout moves.
            with patch.object(rolling, 'roll_once', side_effect=AssertionError('rolled')):
                seen.append(rolling.main(['roll']))
        with patch.object(upgrade, 'UPSTREAM', self.state), patch.object(runtime, 'STATE', self.state), \
                patch.object(upgrade, 'load_state', return_value=state), patch.object(upgrade, 'rollback_refusal', return_value=None), \
                patch.object(upgrade, 'way_back', return_value={}), patch.object(upgrade, 'pull'), patch.object(upgrade, 'save_state'), \
                patch.object(upgrade.upgrade_guard, 'begin_way_back'), patch.object(upgrade, 'layout'), \
                patch.object(upgrade.upgrade_guard, 'complete_way_back', side_effect=move), patch('sys.stdout', new_callable=io.StringIO):
            upgrade.go_back(False)
        self.assertEqual(seen, [rolling.BUSY])

    def test_a_rollback_waits_for_a_swap_in_progress_and_then_refuses(self):
        import fcntl
        self.build()
        state = {'phase': 'confirmed', 'from': 'a' * 40, 'to': 'b' * 40}
        checked = []
        with patch.object(upgrade, 'UPSTREAM', self.state), patch.object(upgrade, 'load_state', return_value=state), \
                patch.object(upgrade, 'OPERATION_WAIT', 0), \
                patch.object(upgrade, 'rollback_refusal', side_effect=lambda *a: checked.append(1)), \
                (self.state / 'operation.lock').open('a') as roll:
            fcntl.flock(roll, fcntl.LOCK_EX)
            with self.assertRaisesRegex(upgrade.UpgradeError, 'Another operation on the runtime'):
                upgrade.go_back(False)
        # Refused before it even looked: nothing was checked or moved.
        self.assertEqual(checked, [])

    def test_the_automatic_way_back_takes_no_operation_lock(self):
        import fcntl
        self.build()
        with patch.object(upgrade, 'UPSTREAM', self.state), patch.object(upgrade, 'way_back_now') as moved, \
                (self.state / 'operation.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            upgrade.go_back(True)
        moved.assert_called_once_with(True, None)


class SkipTests(Fixture):
    """An environment that is not running is skipped and recorded; the others go on; a paused or skipped one
    never makes the supervisor spawn a roll every minute."""

    def test_a_crashed_rest_is_skipped_with_its_reason_and_the_other_environments_are_rolled(self):
        self.build(environments=(E, F, G))
        self.plan(environments=(E, F, G), services=('rest',))
        self.fake.running(self.fake.containers[self.old[E, 'rest']], False)
        self.assertEqual(self.roll(), 0)
        plan = self.saved_plan()
        item = plan['environments'][E]['rest']
        self.assertEqual(item['state'], 'skipped')
        self.assertIn('not running', item['reason'])
        self.assertIn(self.name('rest', E), item['reason'])
        self.assertIsNone(plan['stopped'], plan)
        self.assertNotIn('run', self.fake.calls)
        # The next runs replace the others, which a stopped plan would have left on the old pin.
        self.assertEqual(self.roll(), 0)
        self.assertEqual(self.roll(), 0)
        plan = self.saved_plan()
        self.assertEqual([plan['environments'][e]['rest']['state'] for e in (E, F, G)], ['skipped', 'new', 'new'])
        self.assertIsNone(plan['stopped'])
        self.assertNotEqual(self.fake.named(self.name('rest', F)), self.old[F, 'rest'])
        self.assertNotEqual(self.fake.named(self.name('rest', G)), self.old[G, 'rest'])
        self.assertEqual(self.fake.named(self.name('rest', E)), self.old[E, 'rest'])
        # Nothing is due for the skipped one: no child every minute.
        self.assertFalse(self.run_patched(rolling.due))
        self.assertEqual(self.roll(), rolling.IDLE)
        self.assertEqual(self.run_patched(rolling.status)['environments'][E], {'rest': 'skipped'})
        # It still refuses the by-name commands, as an environment on its previous pin does.
        with self.assertRaisesRegex(RuntimeError, 'previous pin'):
            self.run_patched(rolling.require_settled, E)

    def test_a_skipped_environment_is_asked_for_again_by_retry_once_it_runs(self):
        self.build(environments=(E, F))
        self.plan(environments=(E, F), services=('rest',))
        self.fake.running(self.fake.containers[self.old[E, 'rest']], False)
        self.roll(), self.roll()
        self.assertFalse(self.run_patched(rolling.due))
        self.fake.running(self.fake.containers[self.old[E, 'rest']], True)
        self.assertTrue(self.run_patched(rolling.retry))
        item = self.saved_plan()['environments'][E]['rest']
        self.assertEqual(item, {'state': 'old', 'from': OLD_IMAGE, 'to': NEW_IMAGE})
        self.assertTrue(self.run_patched(rolling.due))
        self.assertEqual(self.roll(), 0)
        self.assertIsNone(self.saved_plan())

    def test_a_service_of_a_running_environment_is_still_replaced_when_the_other_one_is_not_running(self):
        self.build()
        self.plan()
        self.fake.running(self.fake.containers[self.old['rest']], False)
        self.assertEqual(self.roll(), 0)
        states = {s: i['state'] for s, i in self.saved_plan()['environments'][E].items()}
        self.assertEqual(states, {'auth': 'new', 'rest': 'skipped'})
        self.assertIsNone(self.saved_plan()['stopped'])

    def test_a_failure_that_is_not_a_missing_container_still_stops_the_plan(self):
        self.build(environments=(E, F))
        self.plan(environments=(E, F), services=('rest',))
        self.fake.failing_sql = (f'CONNECTION LIMIT {2 * connection_budget.SERVICE_LIMIT};',)
        self.roll()
        self.assertIsNotNone(self.saved_plan()['stopped'])

    def test_a_paused_or_unpublished_environment_does_not_make_a_roll_due(self):
        self.build(environments=(E, F))
        self.plan(environments=(E, F), services=('rest',))
        self.assertTrue(self.run_patched(rolling.due))
        pauses = self.state / 'write-pauses'
        pauses.mkdir()
        for e in (E, F):
            (pauses / f'{e}.json').write_text(json.dumps({'version': 1, 'runtime': e, 'phase': 'held'}))
        self.assertFalse(self.run_patched(rolling.due))
        (pauses / f'{F}.json').unlink()
        self.assertTrue(self.run_patched(rolling.due))
        (self.state / 'endpoints.json').write_text(json.dumps({E: {}}))
        self.assertFalse(self.run_patched(rolling.due))

    def test_the_supervisor_spawns_no_roll_child_for_a_plan_of_paused_or_skipped_environments(self):
        self.build()
        self.plan(services=('rest',))
        (self.state / 'write-pauses').mkdir()
        (self.state / 'write-pauses' / f'{E}.json').write_text(json.dumps({'version': 1, 'runtime': E, 'phase': 'held'}))
        supervisor = SupervisorLoopTests.supervisor(self)
        supervisor.paused = Mock(return_value=False)
        supervisor.rolling = None
        supervisor.rolling_after = 0.0
        supervisor.confirm = None
        supervisor.spawn = Mock()
        with patch.object(runtime, 'STATE', self.state):
            dev.Supervisor.schedule_rolling(supervisor)
        supervisor.spawn.assert_not_called()

    def test_a_plan_entry_with_a_reason_is_read_and_a_reason_that_is_not_text_is_not(self):
        self.build()
        self.plan(services=('rest',))
        plan = self.saved_plan()
        plan['environments'][E]['rest'].update(state='skipped', reason='x is not running')
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        self.assertEqual(self.run_patched(rolling.read_plan)['environments'][E]['rest']['reason'], 'x is not running')
        plan['environments'][E]['rest']['reason'] = 5
        (self.state / rolling.PLAN).write_text(json.dumps(plan))
        with self.assertRaisesRegex(RuntimeError, 'cannot be read'):
            self.run_patched(rolling.read_plan)


class ForceTests(Fixture):
    """`repair --force` and the start that holds one environment instead of refusing every start."""

    def stuck(self, phase='starting', remove_canonical=True, temporary=True):
        """A swap record in a state repair does not recognise: the canonical container was removed by hand."""
        self.build()
        new = self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE) if temporary else None
        (self.root / 'private' / (self.name() + rolling.SUFFIX + '.env')).write_text('X=new\n')
        if remove_canonical:
            del self.fake.containers[self.old['rest']]
        record = {'version': 1, 'environment': E, 'service': 'rest', 'phase': phase, 'old': self.old['rest'], 'new': new,
                  'at': 1}
        (self.state / rolling.NAME).write_text(json.dumps(record))
        return new

    def force(self, e=E):
        return self.run_patched(rolling.repair_force, self.target, e)

    def test_the_record_is_one_repair_does_not_recognise(self):
        self.stuck()
        with self.assertRaises(rolling.Unrecognised):
            self.run_patched(rolling.repair, self.target)
        self.assertIsNotNone(self.record())

    def test_force_removes_only_the_container_the_replacement_created_and_settles_the_record(self):
        new = self.stuck()
        did = self.force()
        self.assertIsNone(self.record())
        self.assertNotIn(new, self.fake.containers)
        self.assertIsNone(self.fake.find(self.name()))
        self.assertIsNone(self.settings_of(self.name() + rolling.SUFFIX))
        removed = [call for call in self.fake.calls if call == 'rm']
        self.assertEqual(removed, ['rm'])
        self.assertIn(f'ALTER ROLE {E}_rest CONNECTION LIMIT {connection_budget.SERVICE_LIMIT};', self.fake.limits())
        self.assertTrue(any('removed' in line and rolling.SUFFIX in line for line in did), did)

    def test_force_records_what_it_did_in_the_journal(self):
        self.stuck()
        patches = self.patches()
        for item in patches:
            item.start()
        try:
            did = rolling.repair_force(self.target, E)
            printed = sys.stdout.getvalue()
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertGreaterEqual(len(did), 3)
        for line in did:
            self.assertIn(f'rolling repair --force {self.name()}: {line}', printed)

    def test_force_never_touches_a_canonical_container_even_one_it_does_not_know(self):
        new = self.stuck(remove_canonical=False)
        # Someone replaced the canonical container by hand: it is not the one the record names.
        hand = self.fake.add('hand-made', OLD_IMAGE)
        del self.fake.containers[self.old['rest']]
        self.fake.containers[hand]['Name'] = '/' + self.name()
        with self.assertRaises(rolling.Unrecognised):
            self.run_patched(rolling.repair, self.target)
        self.force()
        self.assertIsNone(self.record())
        self.assertEqual(self.fake.named(self.name()), hand)
        self.assertTrue(self.fake.containers[hand]['State']['Running'])
        self.assertNotIn(new, self.fake.containers)
        self.assertEqual(self.endpoints()['rest'], self.url(hand))
        self.assertEqual([call for call in self.fake.calls if call in ('stop', 'rm', 'rename')], ['rm'])

    def test_force_keeps_the_replacements_own_container_that_already_holds_the_name(self):
        new = self.stuck(remove_canonical=True)
        self.fake.containers[new]['Name'] = '/' + self.name()
        with self.assertRaises(rolling.Unrecognised):
            self.run_patched(rolling.repair, self.target)
        self.force()
        self.assertEqual(self.fake.named(self.name()), new)
        self.assertEqual(self.settings_of(self.name()), 'X=new\n')
        self.assertIsNone(self.settings_of(self.name() + rolling.SUFFIX))
        self.assertNotIn('rm', self.fake.calls)
        self.assertIsNone(self.record())

    def test_force_refuses_a_temporary_container_it_did_not_create(self):
        new = self.stuck()
        stranger = self.fake.add('stranger', NEW_IMAGE)
        self.fake.containers[new]['Name'] = '/other'
        self.fake.containers[stranger]['Name'] = '/' + self.name() + rolling.SUFFIX
        with self.assertRaisesRegex(RuntimeError, 'not created by this replacement'):
            self.force()
        self.assertIn(stranger, self.fake.containers)
        self.assertIn(new, self.fake.containers)
        self.assertNotIn('rm', self.fake.calls)
        self.assertIsNotNone(self.record())

    def test_force_refuses_a_temporary_container_when_the_record_names_none(self):
        self.stuck(temporary=False)
        stranger = self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE)
        with self.assertRaisesRegex(RuntimeError, 'not created by this replacement'):
            self.force()
        self.assertIn(stranger, self.fake.containers)
        self.assertIsNotNone(self.record())

    def test_force_needs_a_record_for_the_environment_it_is_given(self):
        self.build(environments=(E, F))
        with self.assertRaisesRegex(RuntimeError, 'No side by side replacement'):
            self.force()
        self.stuck()
        with self.assertRaisesRegex(RuntimeError, 'No side by side replacement'):
            self.force(F)
        self.assertIsNotNone(self.record())
        self.assertEqual(self.fake.calls.count('rm'), 0)

    def test_a_record_the_ordinary_repair_handles_needs_no_force(self):
        self.build()
        temporary = self.fake.add(self.name() + rolling.SUFFIX, NEW_IMAGE)
        (self.state / rolling.NAME).write_text(json.dumps({'version': 1, 'environment': E, 'service': 'rest', 'phase': 'switched',
                                                          'old': self.old['rest'], 'new': temporary, 'at': 1}))
        did = self.force()
        self.assertIn('ordinary way', did[0])
        self.assertIsNone(self.record())
        self.assertEqual(self.fake.named(self.name()), self.old['rest'])

    def test_force_finishes_a_later_phase_by_renaming_the_container_it_created(self):
        new = self.stuck(phase='retiring')
        # The ordinary repair knows this one (the new container takes the name), so force changes nothing more.
        self.force()
        self.assertEqual(self.fake.named(self.name()), new)
        self.assertIsNone(self.record())

    def test_a_failed_step_leaves_the_record(self):
        self.stuck()
        self.fake.failing_sql = (f'ALTER ROLE {E}_rest CONNECTION LIMIT',)
        with self.assertRaises(RuntimeError):
            self.force()
        self.assertIsNotNone(self.record())

    def test_the_command_takes_the_environment_and_runs_under_the_locks(self):
        self.stuck()
        with patch.object(runtime, 'Runtime', return_value=self.target):
            self.assertEqual(self.run_patched(rolling.main, ['repair', '--force', E]), 0)
        self.assertIsNone(self.record())
        for argv in (['repair', '--force'], ['repair', '--force', 'e_nothex'], ['roll', '--force']):
            with self.subTest(argv), self.assertRaises(SystemExit):
                self.run_patched(rolling.main, argv)

    def test_the_command_without_force_still_refuses_what_it_does_not_recognise(self):
        self.stuck()
        with patch.object(runtime, 'Runtime', return_value=self.target):
            self.assertEqual(self.run_patched(rolling.main, ['repair']), 1)
        self.assertIsNotNone(self.record())

    def test_a_start_holds_the_one_environment_and_resumes_the_others(self):
        self.stuck()
        held = self.run_patched(rolling.repair_or_hold, self.target)
        self.assertEqual(list(held), [E])
        self.assertIn('not as recorded', held[E])
        self.assertIsNotNone(self.record(), 'the record stays for the operator')
        target = self.target
        target.values['environments'] = {E: {}, F: {}}
        target.held = held
        target.resume = Mock()
        (self.state / 'endpoints.json').write_text(json.dumps({E: {}, F: {}}))
        self.run_patched(lambda: runtime.Runtime.resume_published_environments(target))
        self.assertEqual([call.args for call in target.resume.call_args_list], [(F,)])

    def test_a_start_with_no_held_environment_resumes_every_one(self):
        self.build(environments=(E, F))
        target = self.target
        target.held = {}
        target.resume = Mock()
        self.run_patched(lambda: runtime.Runtime.resume_published_environments(target))
        self.assertEqual(sorted(call.args[0] for call in target.resume.call_args_list), [E, F])

    def test_runtime_start_holds_instead_of_refusing(self):
        self.stuck()
        order = []
        # The real repair runs against the record; everything else of the start is faked.
        StartTests.start(self, order, real_repair=True)
        self.assertEqual(list(self.target.held), [E])
        self.assertIn('resume', order)

    def test_only_an_unrecognised_state_holds_the_start_anything_else_refuses_it(self):
        self.build()
        (self.state / rolling.NAME).write_text('{')
        with self.assertRaisesRegex(RuntimeError, 'cannot be read'):
            self.run_patched(rolling.repair_or_hold, self.target)
        self.stuck()
        with patch.object(rolling, 'repair', side_effect=RuntimeError('Docker operation rm failed')), \
                self.assertRaisesRegex(RuntimeError, 'Docker operation'):
            self.run_patched(rolling.repair_or_hold, self.target)
        self.build()
        self.assertEqual(self.run_patched(rolling.repair_or_hold, self.target), {})


class UpgradeTests(Fixture):
    """An unfinished swap holds an update and an operator's way back."""

    def test_an_unfinished_swap_holds_an_update_and_refuses_a_rollback(self):
        self.build()
        (self.state / rolling.NAME).write_text(json.dumps({'version': 1, 'environment': E, 'service': 'rest', 'phase': 'retiring',
                                                          'old': self.old['rest'], 'new': 'f' * 64, 'at': 1}))
        self.assertIn(rolling.NAME, upgrade.UNSETTLED)
        state = {'phase': 'confirmed', 'from': 'a' * 40, 'to': 'b' * 40}
        with patch.object(upgrade, 'UPSTREAM', self.state), patch.object(upgrade, 'load_state', return_value=state), \
                patch.object(upgrade, 'held', return_value=False), patch.object(runtime, 'STATE', self.state):
            self.assertTrue(any(rolling.NAME in refusal for refusal in upgrade.transient_refusals()))
            self.assertIn('unfinished', upgrade.rollback_refusal(state))
            # The automatic way back of a failed start goes on: the version it returns to repairs the record.
            self.assertNotIn('unfinished', upgrade.rollback_refusal(state, automatic=True) or '')
            # With no plan left, the record alone makes a roll due, which repairs it first.
            self.assertTrue(rolling.due())
            # The drain does not go on either.
            supervisor = SupervisorLoopTests.supervisor(self)
            self.assertFalse(dev.Supervisor.idle(supervisor))
            (self.state / rolling.NAME).unlink()
            self.assertEqual(upgrade.transient_refusals(), [])
            self.assertTrue(dev.Supervisor.idle(supervisor))


if __name__ == '__main__':
    unittest.main()
