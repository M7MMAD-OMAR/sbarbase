"""Notification outbox, drain and channel behaviour, with no Docker and no network access.

The catalog side is exercised through the real TypeScript catalog by driving Bun, because the
outbox schema and the enqueue live in src/control/catalog.ts: a second declaration of the
schema in Python would drift, and a test that declared the tables itself would prove nothing
about the product path. Every fixture uses a private temporary catalog; the retained .lab/ and
.secrets/ trees are never read or written.
"""
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import notify

ROOT = Path(__file__).resolve().parents[1]
CREATE = """
import {{Catalog}} from '{root}/src/control/catalog';
const c=new Catalog({catalog});
const organization=c.createOrganization('alice','A');
const project=c.createProject('alice',organization,'P');
const environment=c.createEnvironment('alice',project,'production');
const job=c.claimProvision();
{action}
c.close();
console.log(JSON.stringify({{environment,runtime:job.runtime,claim:job.claim,attempt:job.attempt}}));
"""
SETTLE = """
import {{Catalog}} from '{root}/src/control/catalog';
const c=new Catalog({catalog});
try{{
  c.applyProvisionReceipt({environment},{runtime},{claim},{attempt},{exit});
  console.log('settled');
}}catch(error){{
  console.log('refused');
}}
c.close();
"""
FINISH = """
import {{Catalog}} from '{root}/src/control/catalog';
const c=new Catalog({catalog});
try{{
  c.finishProvision({environment},{claim},false,'capacity_exceeded');
  console.log('settled');
}}catch(error){{
  console.log('refused');
}}
c.close();
"""


def bun(source):
    if shutil.which('bun') is None:
        raise AssertionError('bun is required to exercise the real catalog')
    result = subprocess.run(['bun', '-e', source], capture_output=True, text=True, cwd=ROOT)
    if result.returncode:
        raise AssertionError('catalog driver failed: ' + result.stderr[-400:])
    return result.stdout.strip()


def create_and_claim(catalog, action=''):
    """Create the fixture through the real catalog and leave one job claimed, not settled."""
    lines = bun(CREATE.format(root=ROOT, catalog=json.dumps(str(catalog)), action=action)).splitlines()
    return json.loads(lines[-1])


def settle(catalog, identity, exit_code=75):
    return bun(SETTLE.format(root=ROOT, catalog=json.dumps(str(catalog)),
                             environment=json.dumps(identity['environment']),
                             runtime=json.dumps(identity['runtime']),
                             claim=json.dumps(identity['claim']),
                             attempt=identity['attempt'], exit=exit_code))


def finish(catalog, identity):
    """Settle through finishProvision alone, with no outer transaction to hide behind."""
    return bun(FINISH.format(root=ROOT, catalog=json.dumps(str(catalog)),
                             environment=json.dumps(identity['environment']),
                             claim=json.dumps(identity['claim'])))


class Receiver:
    """Loopback webhook receiver. One free port per run, never a fixed one."""

    def __init__(self, hold=False):
        self.requests = []
        self.hold = hold
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('content-length', 0)))
                outer.requests.append((dict(self.headers), body))
                if outer.hold:
                    # Accept the request and never answer it, so the client's own deadline is
                    # the only thing that ends the attempt.
                    outer.release.wait(30)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'ok')

            def log_message(self, *args):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def url(self, path='/sbarbase'):
        return 'http://127.0.0.1:{}{}'.format(self.port, path)


class NotificationCase(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='sbarbase-notify-'))
        self.catalog = self.directory / 'control.sqlite'
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.secret = uuid.uuid4().hex + uuid.uuid4().hex[:32]

    def capacityRefusal(self, action=''):
        """Drive the real settlement path: worker receipt exit 75 becomes capacity_exceeded."""
        identity = create_and_claim(self.catalog, action)
        self.assertEqual(settle(self.catalog, identity), 'settled')
        return identity

    def connect(self):
        database = notify.connect(self.catalog)
        self.addCleanup(database.close)
        return database

    @contextlib.contextmanager
    def database(self):
        connection = notify.connect(self.catalog)
        try:
            yield connection
        finally:
            connection.close()

    def rows(self, sql, parameters=()):
        with contextlib.closing(sqlite3.connect(self.catalog)) as database:
            return database.execute(sql, parameters).fetchall()

    def webhookConfig(self, receiver, enabled=True, url=None):
        secret_file = self.directory / 'notifier.json'
        secret_file.write_text(json.dumps({'schema': 1, 'webhookSecret': self.secret}))
        os.chmod(secret_file, 0o600)
        return {'schema': 1, 'webhook': {'enabled': enabled, 'url': url or receiver.url(),
                                         'secretFile': str(secret_file)}}

    def receiver(self, hold=False):
        receiver = Receiver(hold)
        self.addCleanup(receiver.close)
        return receiver

    def drain(self, config, channels, limit=20):
        database = notify.connect(self.catalog)
        try:
            return notify.drain(database, config, channels, self.secret, limit)
        finally:
            database.close()


class EnqueueTransactionTests(NotificationCase):
    """The two fatal orderings of the red team, from the enqueue side."""

    def test_outbox_row_commits_with_the_state_change_it_describes(self):
        identity = self.capacityRefusal()
        self.assertEqual(self.rows('SELECT state,failure FROM provision_jobs'),
                         [('failed', 'capacity_exceeded')])
        outbox = self.rows('SELECT kind,severity,reason,environment FROM notification_outbox')
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0][0], 'provision.capacity_refused')
        self.assertEqual(outbox[0][1], 'warning')
        self.assertEqual(outbox[0][2], 'unrecorded')
        self.assertEqual(outbox[0][3], identity['environment'])
        self.assertEqual(self.rows('SELECT channel,state,attempts FROM notification_delivery '
                                   'ORDER BY channel'),
                         [('email', 'pending', 0), ('webhook', 'pending', 0)])

    def test_refused_enqueue_rolls_back_the_state_change_in_the_same_transaction(self):
        """This is the test that catches an enqueue moved outside the transaction.

        The refusal is forced at the outbox insert itself, with a SQLite trigger, so nothing
        depends on a hook in the product code. finishProvision is called directly, so no outer
        transaction can mask the boundary. With the enqueue inside the transaction the state
        change rolls back with it. With the enqueue after the commit it does not, and every
        assertion below fails.
        """
        identity = create_and_claim(self.catalog)
        with self.database() as database:
            database.execute('CREATE TRIGGER refuse_outbox BEFORE INSERT ON notification_outbox '
                             "BEGIN SELECT RAISE(ABORT,'refused for the transaction test'); END;")
        self.assertEqual(finish(self.catalog, identity), 'refused')
        self.assertEqual(self.rows('SELECT state,failure FROM provision_jobs'), [('running', None)])
        self.assertEqual(self.rows('SELECT count(*) FROM notification_outbox'), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM audit_events "
                                   "WHERE action='provision.failed'"), [(0,)])

    def test_repeated_condition_is_suppressed_by_a_durable_window(self):
        action = ("const organization_b=c.createOrganization('bob','B');\n"
                  "c.setMember('bob',organization_b,'alice','owner');\n"
                  "c.setMember('bob',organization_b,'alice','owner');")
        identity = create_and_claim(self.catalog, action)
        self.assertEqual(settle(self.catalog, identity), 'settled')
        self.assertEqual(self.rows("SELECT occurrences FROM notification_outbox "
                                   "WHERE kind='membership.owner_changed'"), [(2,)])
        self.assertEqual(self.rows("SELECT count(*) FROM notification_delivery d "
                                   "JOIN notification_outbox o ON o.id=d.event "
                                   "WHERE o.kind='membership.owner_changed'"), [(2,)])


class DrainTests(NotificationCase):
    def setUp(self):
        super().setUp()
        self.identity = self.capacityRefusal()

    def test_healthy_channel_delivers_once_and_settles_delivered(self):
        receiver = self.receiver()
        result = self.drain(self.webhookConfig(receiver), ['webhook'])
        self.assertEqual((result['delivered'], result['failed'], result['deferred']), (1, 1, 0))
        self.assertEqual(len(receiver.requests), 1)
        headers, body = receiver.requests[0]
        envelope = json.loads(body)
        self.assertEqual(headers['X-Sbarbase-Schema'], '1')
        self.assertEqual(headers['X-Sbarbase-Event-Id'], envelope['id'])
        self.assertEqual(headers['X-Sbarbase-Delivery-Id'], envelope['delivery'])
        expected = 'sha256=' + hmac.new(self.secret.encode(),
                                        headers['X-Sbarbase-Timestamp'].encode() + b'.' + body,
                                        hashlib.sha256).hexdigest()
        self.assertEqual(headers['X-Sbarbase-Signature'], expected)
        self.assertEqual(envelope['kind'], 'provision.capacity_refused')
        self.assertEqual(envelope['severity'], 'warning')
        self.assertEqual(envelope['occurrences'], 1)
        self.assertIn(envelope['reason'], notify.REASONS)
        self.assertEqual(envelope['subject']['environment'], self.identity['environment'])
        self.assertEqual(self.rows('SELECT channel,state,attempts,last_error FROM notification_delivery '
                                   'ORDER BY channel'),
                         [('email', 'failed', 1, 'channel_disabled'), ('webhook', 'delivered', 1, None)])

    def test_a_broken_channel_records_a_failure_and_leaves_the_operation_unchanged(self):
        """The negative control. Silence is a failure of the probe, not a pass."""
        receiver = self.receiver()
        before = (self.rows('SELECT state,failure FROM provision_jobs'),
                  self.rows('SELECT actor,action,subject,detail FROM audit_events'),
                  self.rows('SELECT count(*) FROM provision_effect_results'))
        result = self.drain(self.webhookConfig(receiver, url='http://127.0.0.1:1/sbarbase'), ['webhook'])
        after = (self.rows('SELECT state,failure FROM provision_jobs'),
                 self.rows('SELECT actor,action,subject,detail FROM audit_events'),
                 self.rows('SELECT count(*) FROM provision_effect_results'))
        self.assertEqual(before, after)
        self.assertEqual((result['delivered'], result['deferred']), (0, 1))
        self.assertEqual(receiver.requests, [])
        row = self.rows('SELECT state,attempts,last_error,next_attempt_at,delivered_at '
                        "FROM notification_delivery WHERE channel='webhook'")[0]
        self.assertEqual(row[0], 'pending')
        self.assertGreaterEqual(row[1], 1)
        self.assertEqual(row[2], 'webhook_unreachable')
        self.assertGreater(row[3], int(time.time() * 1000) - 1000)
        self.assertIsNone(row[4])
        self.assertEqual(self.rows("SELECT kind FROM notification_outbox "
                                   "WHERE kind='notifier.channel_failed'"), [])

    def test_webhook_timeout_is_a_timeout_and_not_a_hang(self):
        receiver = self.receiver(hold=True)
        config = self.webhookConfig(receiver)
        notify.ATTEMPT_TIMEOUT_SECONDS = 1
        try:
            started = time.monotonic()
            result = self.drain(config, ['webhook'])
            elapsed = time.monotonic() - started
        finally:
            notify.ATTEMPT_TIMEOUT_SECONDS = 10
        self.assertLess(elapsed, 5)
        self.assertEqual(result['deferred'], 1)
        self.assertEqual(len(receiver.requests), 1)
        self.assertEqual(self.rows("SELECT last_error FROM notification_delivery "
                                   "WHERE channel='webhook'")[0][0], 'webhook_timeout')

    def test_settlement_rejects_a_stale_claim_and_nothing_is_delivered_twice(self):
        receiver = self.receiver()
        self.drain(self.webhookConfig(receiver), ['webhook'])
        database = self.connect()
        self.assertEqual(notify.claim(database, 20), [])
        item = {'event': self.rows('SELECT event FROM notification_delivery')[0][0],
                'channel': 'webhook', 'claim': str(uuid.uuid4())}
        with self.assertRaises(RuntimeError):
            notify.settle(database, item, 'delivered')
        self.assertEqual(len(receiver.requests), 1)

    def test_retry_backoff_is_bounded_and_exhaustion_settles_failed(self):
        receiver = self.receiver()
        config = self.webhookConfig(receiver, url='http://127.0.0.1:1/sbarbase')
        config['email'] = {'enabled': True, 'host': '127.0.0.1', 'port': 1,
                           'from': 'sbarbase@installation.invalid', 'to': 'operator@example.invalid',
                           'tls': 'none'}
        database = self.connect()
        event = self.rows('SELECT event FROM notification_delivery '
                          "WHERE channel='webhook'")[0][0]
        for attempt in range(1, notify.MAX_ATTEMPTS + 1):
            database.execute("UPDATE notification_delivery SET state='pending',next_attempt_at=0 "
                             "WHERE event=? AND channel='webhook'", (event,))
            notify.drain(database, config, ['email', 'webhook'], self.secret)
            row = self.rows("SELECT state,attempts,next_attempt_at FROM notification_delivery "
                            "WHERE channel='webhook'")[0]
            self.assertEqual(row[1], attempt)
            if attempt < notify.MAX_ATTEMPTS:
                self.assertEqual(row[0], 'pending')
                expected = notify.BACKOFF_SECONDS[min(attempt, len(notify.BACKOFF_SECONDS)) - 1] * 1000
                self.assertAlmostEqual(row[2] - int(time.time() * 1000), expected, delta=2000)
            else:
                self.assertEqual(row[0], 'failed')
                # The failed event stays visible and escalates once, on the healthy channel.
                escalation = self.rows("SELECT kind,reason FROM notification_outbox "
                                       "WHERE kind='notifier.channel_failed'")
                self.assertEqual(escalation, [('notifier.channel_failed', 'webhook_unreachable')])
                self.assertEqual(self.rows("SELECT channel FROM notification_delivery d "
                                           "JOIN notification_outbox o ON o.id=d.event "
                                           "WHERE o.kind='notifier.channel_failed'"), [('email',)])

    def test_a_channel_failure_escalates_once_per_six_hours(self):
        """docs/engineering/OPERATOR-NOTIFICATIONS.md section 4: one escalation per channel per six hours.

        The escalation window was the critical severity window (300 seconds), so a channel
        that stayed broken produced roughly 72 escalation messages per six hours instead of
        one, and two broken channels escalated each other.
        """
        receiver = self.receiver()
        config = self.webhookConfig(receiver, url='http://127.0.0.1:1/sbarbase')
        config['email'] = {'enabled': True, 'host': '127.0.0.1', 'port': 1,
                           'from': 'sbarbase@installation.invalid', 'to': 'operator@example.invalid',
                           'tls': 'none'}
        database = self.connect()
        event = self.rows('SELECT id FROM notification_outbox')[0][0]
        for _ in range(notify.MAX_ATTEMPTS + 4):
            database.execute("UPDATE notification_delivery SET state='pending',next_attempt_at=0 "
                             'WHERE event=?', (event,))
            notify.drain(database, config, ['email', 'webhook'], self.secret)
        escalations = self.rows('SELECT dedupe_key,window_until-at FROM notification_outbox '
                                "WHERE kind='notifier.channel_failed' ORDER BY dedupe_key")
        self.assertEqual(notify.ESCALATION_WINDOW_SECONDS, 6 * 3600)
        self.assertEqual([row[0] for row in escalations],
                         ['notifier.channel_failed|email', 'notifier.channel_failed|webhook'])
        self.assertEqual([row[1] for row in escalations], [21600 * 1000, 21600 * 1000])

    def test_redaction_gate_refuses_before_any_byte_leaves(self):
        """A credential reaching a rendered field settles failed and sends nothing."""
        receiver = self.receiver()
        database = self.connect()
        identity = self.rows('SELECT id FROM notification_outbox')[0][0]
        database.execute('UPDATE notification_outbox SET actor=? WHERE id=?',
                         ('postgres://operator:secret@database.internal:5432/management', identity))
        result = notify.drain(database, self.webhookConfig(receiver), ['email', 'webhook'], self.secret)
        self.assertEqual((result['refused'], result['delivered']), (2, 0))
        self.assertEqual(receiver.requests, [])
        self.assertEqual(self.rows('SELECT state,last_error FROM notification_delivery d '
                                   "JOIN notification_outbox o ON o.id=d.event "
                                   'WHERE o.kind=? ORDER BY d.channel',
                                   ('provision.capacity_refused',)),
                         [('failed', 'redaction_refused'), ('failed', 'redaction_refused')])
        # One refusal event per refused event, delivered on every enabled channel.
        self.assertEqual(self.rows("SELECT o.kind,o.reason,d.channel,d.state FROM notification_delivery d "
                                   "JOIN notification_outbox o ON o.id=d.event "
                                   "WHERE o.kind='notifier.redaction_refused' ORDER BY d.channel"),
                         [('notifier.redaction_refused', 'redaction_refused', 'email', 'pending'),
                          ('notifier.redaction_refused', 'redaction_refused', 'webhook', 'pending')])

    def test_a_credential_that_never_reaches_a_message_is_also_refused(self):
        envelope = {'id': str(uuid.uuid4()), 'subject': {}, 'actor': 'system', 'summary': 'x',
                    'action': 'y', 'reason': 'runtime_failed', 'kind': 'provision.failed'}
        self.assertEqual(notify.refusal_shapes(
            envelope, {'failure': 'postgres://operator:secret@database.internal:5432/x',
                       'attempt': 1}), ['detail.failure'])
        self.assertEqual(notify.refusal_shapes(envelope, {'failure': 'capacity_exceeded', 'attempt': 1}), [])
        # A field the envelope does not declare cannot carry anything into a message.
        envelope['connection_string'] = 'postgres://leak'
        self.assertEqual(notify.refusal_shapes(envelope), [])

    def test_prune_never_removes_an_undelivered_event(self):
        database = self.connect()
        event = self.rows('SELECT id FROM notification_outbox')[0][0]
        database.execute('UPDATE notification_outbox SET expires_at=0 WHERE id=?', (event,))
        self.assertEqual(notify.prune(database), 0)
        result = notify.drain(database, self.webhookConfig(self.receiver()), ['webhook'], self.secret)
        # The same bounded retention applies inside the drain: the event was delivered and
        # expired, so the drain's own prune pass removes it and its orphan delivery rows.
        self.assertEqual(self.rows('SELECT count(*) FROM notification_outbox'), [(0,)])
        self.assertEqual(self.rows('SELECT count(*) FROM notification_delivery'), [(0,)])
        self.assertGreaterEqual(result['pruned'], 1)

    def test_the_fine_refusal_reason_is_recorded_from_its_producer(self):
        database = self.connect()
        item = notify.claim(database)[0]
        self.assertEqual(item['row']['reason'], 'unrecorded')
        original = notify.produced_refusal_reason
        notify.produced_refusal_reason = lambda: 'memory_headroom'
        try:
            self.assertEqual(notify.record_refusal_reason(database, item), 'memory_headroom')
        finally:
            notify.produced_refusal_reason = original
        stored = self.rows('SELECT reason,detail FROM notification_outbox')[0]
        self.assertEqual(stored[0], 'memory_headroom')
        self.assertEqual(json.loads(stored[1])['reason_source'], 'admission_gate')
        self.assertEqual(item['row']['reason'], 'memory_headroom')
        self.assertIn(notify.produced_refusal_reason(), notify.REASONS)

    def test_a_coarse_refusal_that_stays_unrecorded_says_so(self):
        database = self.connect()
        item = notify.claim(database)[0]
        original = notify.produced_refusal_reason
        notify.produced_refusal_reason = lambda: 'unrecorded'
        try:
            self.assertIsNone(notify.record_refusal_reason(database, item))
        finally:
            notify.produced_refusal_reason = original
        self.assertEqual(self.rows('SELECT reason FROM notification_outbox'), [('unrecorded',)])


class VocabularyTests(unittest.TestCase):
    def test_every_declared_kind_renders_a_closed_envelope(self):
        for kind in sorted(notify.KINDS):
            detail = {field: (1 if field in ('attempt', 'revision') else 'value')
                      for field in notify.DETAIL_KEYS[kind]}
            item = {'event': str(uuid.uuid4()), 'claim': str(uuid.uuid4()),
                    'row': {'id': str(uuid.uuid4()), 'kind': kind, 'severity': 'warning',
                            'organization': str(uuid.uuid4()), 'project': None, 'environment': None,
                            'runtime': 'e_' + 'a' * 24, 'actor': 'system', 'reason': 'unrecorded',
                            'detail': json.dumps(detail), 'at': 1, 'last_at': 2,
                            'occurrences': 1, 'window_until': 0}}
            envelope = notify.render(item, item['claim'])
            self.assertEqual(sorted(envelope), sorted(notify.ENVELOPE_FIELDS), kind)
            self.assertEqual(notify.refusal_shapes(envelope, detail), [], kind)
            self.assertTrue(notify.mail_subject(envelope).startswith('[sbarbase] '), kind)
            self.assertIn(envelope['id'], notify.mail_body(envelope))
            self.assertEqual(len(notify.webhook_body(envelope)) > 0, True)

    def test_reason_class_covers_every_reason_of_the_vocabulary(self):
        self.assertEqual(sorted(notify.REASON_CLASS), sorted(notify.REASONS))

    def test_detail_keys_cover_every_kind(self):
        self.assertEqual(sorted(notify.DETAIL_KEYS), sorted(notify.KINDS))

    def test_identifier_shapes_are_not_mistaken_for_credentials(self):
        self.assertTrue(notify.credential_shape('postgres://x:y@z/db'))
        self.assertTrue(notify.credential_shape('a' * 64))
        self.assertTrue(notify.credential_shape('sb_secret_abcdefghijklmnopqrstuv'))
        self.assertFalse(notify.credential_shape('e_' + 'a' * 24))
        self.assertFalse(notify.credential_shape(str(uuid.uuid4())))
        self.assertFalse(notify.credential_shape('provision.capacity_refused'))
        self.assertFalse(notify.credential_shape('measurement_unavailable'))

    def test_two_renderings_of_one_row_agree_on_everything_but_the_waiting_seconds(self):
        """render() recomputes the suppression seconds, so two renderings can differ by one.

        The drain renders the email, sends it, and only then renders the webhook, so the
        probe's comparison of the webhook's summary against the email body must compare the
        stable part. Comparing the whole string is a coin flip, and its failure text cannot
        distinguish a moved second from a credential in the body.
        """
        row = {'id': str(uuid.uuid4()), 'kind': 'provision.capacity_refused', 'severity': 'warning',
               'organization': None, 'project': None, 'environment': None, 'runtime': 'e_' + 'a' * 24,
               'actor': 'system', 'reason': 'memory_headroom',
               'detail': json.dumps({'failure': 'capacity_exceeded', 'attempt': 1,
                                     'reason_source': 'admission_gate'}),
               'at': 1, 'last_at': 2, 'occurrences': 1, 'window_until': 1_300_000}
        with patch.object(notify.time, 'time', return_value=1000.0):
            email = notify.render({'event': 'event', 'claim': 'claim', 'row': dict(row)}, 'email')
        with patch.object(notify.time, 'time', return_value=1001.0):
            webhook = notify.render({'event': 'event', 'claim': 'claim', 'row': dict(row)}, 'webhook')
        self.assertIn('suppressed for 300 seconds', email['summary'])
        self.assertIn('suppressed for 299 seconds', webhook['summary'])
        self.assertNotEqual(email['summary'], webhook['summary'])
        self.assertEqual(notify.stable_summary(email['summary']), notify.stable_summary(webhook['summary']))
        self.assertIn('summary: ' + email['summary'], notify.mail_body(email))
        self.assertIn('summary: ' + notify.stable_summary(email['summary']),
                      notify.mail_body(email).replace(notify.SUPPRESSION_TEMPLATE.format(window=300), ''))


class ConfigurationTests(NotificationCase):
    def write(self, name, payload, mode=0o600):
        path = self.directory / name
        path.write_text(json.dumps(payload))
        os.chmod(path, mode)
        return path

    def test_unknown_schema_and_non_private_secret_are_refused(self):
        with self.assertRaises(notify.ConfigurationError):
            notify.load_config(self.write('a.json', {'schema': 2}))
        secret = self.write('secret.json', {'schema': 1, 'webhookSecret': 'b' * 64}, 0o644)
        config = self.write('b.json', {'schema': 1, 'webhook': {
            'enabled': True, 'url': 'http://127.0.0.1:1/x', 'secretFile': str(secret)}})
        with self.assertRaises(notify.ConfigurationError):
            notify.load_config(config)
        os.chmod(secret, 0o600)
        loaded, channels, value = notify.load_config(config)
        self.assertEqual(channels, ['webhook'])
        self.assertEqual(value, 'b' * 64)
        with self.assertRaises(notify.ConfigurationError):
            notify.load_config(self.write('c.json', {'schema': 1, 'email': {'enabled': True}}))

    def test_missing_secret_file_and_unknown_transport_are_refused(self):
        config = self.write('d.json', {'schema': 1, 'webhook': {
            'enabled': True, 'url': 'http://127.0.0.1:1/x',
            'secretFile': str(self.directory / 'absent.json')}})
        with self.assertRaises(notify.ConfigurationError):
            notify.load_config(config)
        mail = self.write('e.json', {'schema': 1, 'email': {
            'enabled': True, 'host': '127.0.0.1', 'port': 1025,
            'from': 'a@b.invalid', 'to': 'c@d.invalid', 'tls': 'plaintext'}})
        with self.assertRaises(notify.ConfigurationError):
            notify.load_config(mail)


class WorkerLockTests(NotificationCase):
    """The drain opens no lock; it can only run under the worker's existing one."""

    def test_absent_or_mismatched_descriptor_is_refused(self):
        state = self.directory / 'state'
        state.mkdir()
        lock = state / 'worker.lock'
        lock.write_text('')
        saved = os.environ.pop('SBARBASE_WORKER_FD', None)
        self.addCleanup(lambda: os.environ.pop('SBARBASE_WORKER_FD', None))
        try:
            self.assertFalse(notify.worker_lock_held(state))
            descriptor = os.open(lock, os.O_RDWR)
            self.addCleanup(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.environ['SBARBASE_WORKER_FD'] = str(descriptor)
            self.assertTrue(notify.worker_lock_held(state))
            self.assertFalse(notify.worker_lock_held(state.parent))
        finally:
            if saved is not None:
                os.environ['SBARBASE_WORKER_FD'] = saved

    def test_a_drain_without_the_worker_lock_refuses_before_it_writes(self):
        state = self.directory / 'state'
        state.mkdir()
        (state / 'notifications.json').write_text('{}')
        script = ('import sys,notify\n'
                  'sys.exit(notify.main(["--catalog",sys.argv[1],"--config",sys.argv[2],'
                  '"--once","--state",sys.argv[3],"--require-worker-lock"]))')
        result = subprocess.run(['/usr/bin/python3', '-c', script, str(self.catalog),
                                 str(state / 'notifications.json'), str(state)],
                                capture_output=True, text=True, cwd=ROOT / 'lab')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires the existing worker lock', result.stderr)


if __name__ == '__main__':
    unittest.main()