"""Durable operator notification drain.

Placement: this runs inside the worker's existing exclusive installation lock. It opens no
lock of its own, it is launched by the worker process, and it is never a separate service or
a supervisor stage. One claimant is therefore still the rule, and the catalog has no second
installation writer.

Ordering: the outbox row is written by src/control/catalog.ts inside the same SQLite
transaction as the state change that produced it. Delivery happens here, strictly after that
commit, in a separate bounded step. A channel failure can therefore only ever move a
notification_delivery row: nothing in this file writes provision_jobs, audit_events or any
operation state.

Redaction: a message is built from an allow-list of fields. A caller cannot add a field, and
the fully rendered envelope (subject, body, headers, url) is scanned for credential shapes
before any byte leaves the process. A match settles the delivery failed with
`redaction_refused` and no bytes are sent. The direction of error is deliberate: an
over-zealous match costs a message, never a credential.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import signal
import smtplib
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing, contextmanager
from email.message import EmailMessage
from pathlib import Path

MAX_ATTEMPTS = 8
LEASE_MS = 30000
BACKOFF_SECONDS = (15, 60, 300, 1800, 7200)
RETENTION_MS = 30 * 24 * 60 * 60 * 1000
WINDOW_SECONDS = {'info': 3600, 'warning': 1800, 'critical': 300}
ESCALATION_WINDOW_SECONDS = 6 * 3600
ATTEMPT_TIMEOUT_SECONDS = 10

KINDS = frozenset((
    'provision.failed', 'provision.capacity_refused', 'provision.retry_limit',
    'provision.retried', 'routing.paused', 'routing.resumed',
    'membership.owner_changed', 'project.ownership_changed',
    'notifier.channel_failed', 'notifier.redaction_refused'))
REASONS = frozenset((
    'runtime_failed', 'retry_limit', 'retry_requested', 'owner_changed', 'ownership_changed',
    'routing_paused', 'routing_resumed', 'installation_limit', 'memory_headroom',
    'disk_headroom', 'inode_headroom', 'measurement_unavailable', 'cpu_some10',
    'io_full10', 'memory_full10', 'connection_budget', 'unrecorded',
    'webhook_unreachable', 'webhook_timeout', 'webhook_status',
    'smtp_refused', 'smtp_temporary_failure', 'channel_disabled', 'redaction_refused'))
CHANNELS = ('email', 'webhook')
SEVERITIES = ('info', 'warning', 'critical')
DETAIL_KEYS = {
    'provision.failed': ('failure', 'attempt'),
    'provision.capacity_refused': ('failure', 'attempt', 'reason_source'),
    'provision.retry_limit': ('attempt', 'reason_source'),
    'provision.retried': ('attempt', 'reason_source'),
    'routing.paused': ('revision',),
    'routing.resumed': ('revision',),
    'membership.owner_changed': ('target', 'role'),
    'project.ownership_changed': ('from', 'to'),
    'notifier.channel_failed': ('channel', 'last_error'),
    'notifier.redaction_refused': ('refused_event', 'refused_kind'),
}
REASON_CLASS = {
    'runtime_failed': 'provisioning_outcome', 'retry_limit': 'provisioning_outcome',
    'retry_requested': 'provisioning_outcome',
    'installation_limit': 'admission_refusal', 'memory_headroom': 'admission_refusal',
    'disk_headroom': 'admission_refusal', 'inode_headroom': 'admission_refusal',
    'measurement_unavailable': 'admission_refusal', 'cpu_some10': 'admission_refusal',
    'io_full10': 'admission_refusal', 'memory_full10': 'admission_refusal',
    'connection_budget': 'admission_refusal', 'unrecorded': 'admission_refusal',
    'owner_changed': 'authority_change', 'ownership_changed': 'authority_change',
    'routing_paused': 'routing_change', 'routing_resumed': 'routing_change',
    'webhook_unreachable': 'channel_outcome', 'webhook_timeout': 'channel_outcome',
    'webhook_status': 'channel_outcome', 'smtp_refused': 'channel_outcome',
    'smtp_temporary_failure': 'channel_outcome', 'channel_disabled': 'channel_outcome',
    'redaction_refused': 'channel_outcome',
}
# Fixed renderer. summary and action are looked up here and interpolate only safe
# identifiers, a closed reason name and a number. No caller supplies either string.
SUMMARY = {
    'provision.failed': ('Environment provisioning failed and retained state needs inspection.',
                         'Inspect the retained operation, then retry from the console.'),
    'provision.capacity_refused': (
        'Environment provisioning was refused before any mutation; the recorded reason is {reason}.',
        'Free the named resource or move the environment, then retry from the console.'),
    'provision.retry_limit': ('Preflight recovery reached its retry limit and retained state needs inspection.',
                              'Inspect the retained operation and reconcile it before another attempt.'),
    'provision.retried': ('A failed provisioning operation was queued again by an operator.',
                          'No action required; this records the retry.'),
    'routing.paused': ('Runtime routing was paused for maintenance.',
                       'Resume routing from the console when maintenance ends.'),
    'routing.resumed': ('Runtime routing was resumed.', 'No action required.'),
    'membership.owner_changed': ('Organization ownership changed, so who can act in this organization changed.',
                                 'Review the organization membership if this was not intended.'),
    'project.ownership_changed': ('Project ownership moved between organizations.',
                                  'Review the destination organization if this was not intended.'),
    'notifier.channel_failed': ('A notification channel failed permanently while other events wait.',
                                'Repair the recorded channel; the failed event stays visible.'),
    'notifier.redaction_refused': ('A notification was refused by the redaction gate before any bytes left.',
                                   'Inspect the refused event: it carries a credential shape.'),
}
# Allow-list. The rendered envelope contains exactly these keys, in these positions.
ENVELOPE_FIELDS = ('schema', 'id', 'delivery', 'kind', 'severity', 'at', 'last_at', 'occurrences',
                   'window_seconds', 'subject', 'actor', 'reason', 'reason_class', 'summary', 'action')
SUBJECT_FIELDS = ('organization', 'project', 'environment', 'runtime')

IDENTIFIER_SHAPES = re.compile(
    r'e_[a-f0-9]{24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
CREDENTIAL_SHAPES = (
    re.compile(r'postgres(ql)?://', re.I),
    re.compile(r'sb_publishable_', re.I),
    re.compile(r'sb_secret_', re.I),
    re.compile(r'password=', re.I),
    re.compile(r'apikey', re.I),
    re.compile(r'authorization:', re.I),
    re.compile(r'BEGIN PRIVATE KEY'),
    re.compile(r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'),
    re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])'),
    re.compile(r'[A-Za-z0-9_-]{32,}'),
)


def credential_shape(value):
    """Scan one string after removing exactly the shapes a catalog identifier has.

    Without that removal a uuid or a runtime identifier would match a key pattern and a
    legitimate message would be silenced. Nothing else is exempted.
    """
    return any(shape.search(IDENTIFIER_SHAPES.sub('id', value)) for shape in CREDENTIAL_SHAPES)


def refusal_shapes(envelope, detail=None):
    """Every string that could leave the process, flattened, with the field that carries it.

    The detail is scanned as well: it is never interpolated into a message today, and the
    gate refuses it anyway, so a future template cannot turn it into a leak.
    """
    found = []
    for field in ENVELOPE_FIELDS:
        value = envelope.get(field)
        values = value.values() if field == 'subject' and isinstance(value, dict) else [value]
        for item in values:
            if isinstance(item, str) and credential_shape(item):
                found.append(field)
    for field, value in (detail or {}).items():
        if isinstance(value, str) and credential_shape(value):
            found.append('detail.' + field)
    return found


# ---------------------------------------------------------------- catalog access

def connect(path):
    database = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    database.row_factory = sqlite3.Row
    database.execute('PRAGMA busy_timeout=5000')
    database.execute('PRAGMA foreign_keys=ON')
    return database


@contextmanager
def immediate(database):
    """One immediate transaction, the same boundary src/control/catalog.ts uses."""
    database.execute('BEGIN IMMEDIATE')
    try:
        yield database
    except BaseException:
        database.execute('ROLLBACK')
        raise
    database.execute('COMMIT')


def claim(database, limit=20, lease_ms=LEASE_MS):
    """Claim due deliveries exactly once, mirroring claimProvision.

    A row moves to `claimed` only when the guard matches, and `attempts` increments on every
    claim, so a replay of an old attempt is a stale claim and cannot settle a newer one.
    """
    now = int(time.time() * 1000)
    claimed = []
    with immediate(database):
        rows = database.execute('''SELECT d.event event,d.channel channel,d.attempts attempts,o.id id,o.kind kind,
            o.severity severity,o.organization organization,o.project project,o.environment environment,
            o.runtime runtime,o.actor actor,o.reason reason,o.detail detail,o.at at,o.last_at last_at,
            o.occurrences occurrences,o.window_until window_until
            FROM notification_delivery d JOIN notification_outbox o ON o.id=d.event
            WHERE (d.state='pending' AND d.next_attempt_at<=?)
               OR (d.state='claimed' AND d.claim_at<=?)
            ORDER BY o.at LIMIT ?''', (now, now - lease_ms, limit)).fetchall()
        for row in rows:
            token = str(uuid.uuid4())
            result = database.execute('''UPDATE notification_delivery
                SET state='claimed',claim=?,claim_at=?,attempts=attempts+1
                WHERE event=? AND channel=? AND state IN ('pending','claimed')''',
                (token, now, row['event'], row['channel']))
            if result.rowcount != 1:
                continue
            claimed.append({'event': row['event'], 'channel': row['channel'], 'claim': token,
                            'attempts': row['attempts'] + 1, 'row': dict(row)})
    return claimed


def settle(database, item, outcome, error=None):
    """Settle one claimed delivery. Mirrors the stale claim guard of finishProvision."""
    if outcome not in ('delivered', 'transient', 'failed'):
        raise ValueError('Invalid notification outcome')
    if error is not None and not re.fullmatch(r'[a-z0-9_]{1,60}', error):
        raise ValueError('Invalid notification error token')
    now = int(time.time() * 1000)
    with immediate(database):
        if outcome == 'delivered':
            result = database.execute('''UPDATE notification_delivery
                SET state='delivered',delivered_at=?,claim=NULL,last_error=NULL,next_attempt_at=?
                WHERE event=? AND channel=? AND claim=? AND state='claimed' ''',
                (now, now, item['event'], item['channel'], item['claim']))
            if result.rowcount != 1:
                raise RuntimeError('Stale notification claim')
            return 'delivered'
        row = database.execute('SELECT attempts FROM notification_delivery WHERE event=? AND channel=?',
                               (item['event'], item['channel'])).fetchone()
        if row is None:
            raise RuntimeError('Stale notification claim')
        attempts = row['attempts']
        permanent = outcome == 'failed' or attempts >= MAX_ATTEMPTS
        backoff = BACKOFF_SECONDS[min(max(attempts, 1), len(BACKOFF_SECONDS)) - 1] * 1000
        next_attempt_at = now if permanent else now + backoff
        result = database.execute('''UPDATE notification_delivery
            SET state=?,claim=NULL,last_error=?,next_attempt_at=?
            WHERE event=? AND channel=? AND claim=? AND state='claimed' ''',
            ('failed' if permanent else 'pending', error, next_attempt_at,
             item['event'], item['channel'], item['claim']))
        if result.rowcount != 1:
            raise RuntimeError('Stale notification claim')
        return 'failed' if permanent else 'pending'


def prune(database, now=None, limit=500):
    """Bounded retention. A row with any pending or claimed delivery is never removed."""
    now = int(time.time() * 1000) if now is None else now
    with immediate(database):
        rows = database.execute('''SELECT o.id id FROM notification_outbox o
            WHERE o.expires_at < ? AND NOT EXISTS(SELECT 1 FROM notification_delivery d
              WHERE d.event=o.id AND d.state IN ('pending','claimed'))
            ORDER BY o.expires_at LIMIT ?''', (now, limit)).fetchall()
        for row in rows:
            database.execute('DELETE FROM notification_delivery WHERE event=?', (row['id'],))
            database.execute('DELETE FROM notification_outbox WHERE id=?', (row['id'],))
    return len(rows)


def enqueue(database, kind, severity, dedupe_key, subject, actor, reason, detail, channels=CHANNELS):
    """Enqueue an event in the caller's transaction, with the same durable window as notify().

    This is the escalation writer. It is still the same single catalog writer: the caller is
    the drain step inside the worker, and the row commits with the settlement that caused it.
    """
    if kind not in KINDS or severity not in SEVERITIES or reason not in REASONS:
        raise ValueError('Notification vocabulary violation')
    for field, value in detail.items():
        if field not in DETAIL_KEYS[kind]:
            raise ValueError('Unexpected notification detail field')
        if not isinstance(value, (str, int, bool)) or (isinstance(value, str) and len(value) > 200):
            raise ValueError('Invalid notification detail value')
    for field in DETAIL_KEYS[kind]:
        if field not in detail:
            raise ValueError('Missing notification detail field')
    if credential_shape(dedupe_key) or credential_shape(json.dumps(detail)):
        raise ValueError('Notification content refused')
    now = int(time.time() * 1000)
    open_row = database.execute('''SELECT id FROM notification_outbox
        WHERE dedupe_key=? AND window_until>? ORDER BY at DESC LIMIT 1''', (dedupe_key, now)).fetchone()
    if open_row:
        database.execute('UPDATE notification_outbox SET occurrences=occurrences+1,last_at=? WHERE id=?',
                         (now, open_row['id']))
        return open_row['id']
    identity = str(uuid.uuid4())
    database.execute('''INSERT INTO notification_outbox(id,at,last_at,window_until,occurrences,
        digest_sent,kind,severity,dedupe_key,organization,project,environment,runtime,actor,reason,
        detail,expires_at) VALUES (?,?,?,?,1,0,?,?,?,?,?,?,?,?,?,?,?)''',
        (identity, now, now, now + WINDOW_SECONDS[severity] * 1000, kind, severity, dedupe_key,
         subject.get('organization'), subject.get('project'), subject.get('environment'),
         subject.get('runtime'), actor, reason, json.dumps(detail), now + RETENTION_MS))
    for channel in channels:
        database.execute('''INSERT INTO notification_delivery(event,channel,state,attempts,next_attempt_at)
            VALUES (?,?,'pending',0,?)''', (identity, channel, now))
    return identity


# ---------------------------------------------------------------- the fine reason

def produced_refusal_reason():
    """Record the fine refusal reason from the gate that produces it.

    Exit code 75 is the whole protocol a refused child may publish, so the fine admission
    reason cannot travel with the receipt. It is asked from its own producer instead of being
    invented here. A gate that cannot measure yields `measurement_unavailable`, which is the
    same fail-closed answer the gate itself gives.
    """
    try:
        import resource_admission
        reason = resource_admission.refusal(resource_admission.snapshot())
        if reason:
            return reason
    except Exception:
        return 'measurement_unavailable'
    try:
        import pressure_admission
        metric = pressure_admission.refusal(pressure_admission.snapshot())
        if metric:
            return metric
    except Exception:
        return 'measurement_unavailable'
    return 'unrecorded'


def record_refusal_reason(database, item):
    """Persist the fine reason on a coarse capacity refusal before the message is rendered."""
    if item['row']['kind'] != 'provision.capacity_refused' or item['row']['reason'] != 'unrecorded':
        return None
    reason = produced_refusal_reason()
    if reason == 'unrecorded' or reason not in REASONS:
        return None
    detail = json.loads(item['row']['detail'])
    detail['reason_source'] = 'admission_gate'
    with immediate(database):
        database.execute('UPDATE notification_outbox SET reason=?,detail=? WHERE id=?',
                         (reason, json.dumps(detail), item['event']))
    item['row']['reason'] = reason
    item['row']['detail'] = json.dumps(detail)
    return reason


# ---------------------------------------------------------------- rendering

def render(item, delivery_id):
    """Build the envelope from the allow-list. Unknown keys are dropped, never copied."""
    row = item['row']
    kind, reason, severity = row['kind'], row['reason'], row['severity']
    if kind not in KINDS:
        raise ValueError('Unknown notification kind')
    if reason not in REASONS:
        raise ValueError('Unknown notification reason')
    if severity not in SEVERITIES:
        raise ValueError('Unknown notification severity')
    detail = json.loads(row['detail'])
    for field, value in detail.items():
        if field not in DETAIL_KEYS[kind] or not isinstance(value, (str, int, bool)):
            raise ValueError('Invalid notification detail')
    summary, action = SUMMARY[kind]
    summary = summary.format(reason=reason)
    window = max(0, (row['window_until'] - int(time.time() * 1000)) // 1000)
    if window:
        summary = (summary + ' This condition is suppressed for {window} seconds; repeats inside '
                   'that window are summarised in one message.').format(window=window)
    subject = {field: row[field] for field in SUBJECT_FIELDS if row[field] is not None}
    return {
        'schema': 1,
        'id': item['event'],
        'delivery': delivery_id,
        'kind': kind,
        'severity': severity,
        'at': iso(row['at']),
        'last_at': iso(row['last_at']),
        'occurrences': row['occurrences'],
        'window_seconds': window,
        'subject': subject,
        'actor': row['actor'],
        'reason': reason,
        'reason_class': REASON_CLASS[reason],
        'summary': summary,
        'action': action,
    }


def iso(milliseconds):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(milliseconds / 1000)) + \
        '.{:03d}Z'.format(milliseconds % 1000)


def mail_subject(envelope):
    return '[sbarbase] {severity} {kind} {id}'.format(**envelope)


def mail_body(envelope):
    lines = ['kind: ' + envelope['kind'], 'severity: ' + envelope['severity'],
             'event: ' + envelope['id'], 'delivery: ' + envelope['delivery'],
             'at: ' + envelope['at'], 'occurrences: ' + str(envelope['occurrences']),
             'reason: ' + envelope['reason'], 'reason_class: ' + envelope['reason_class'],
             'actor: ' + envelope['actor'], 'summary: ' + envelope['summary'],
             'action: ' + envelope['action']]
    for field in SUBJECT_FIELDS:
        if field in envelope['subject']:
            lines.append(field + ': ' + envelope['subject'][field])
    return '\n'.join(lines) + '\n'


def webhook_body(envelope):
    return json.dumps(envelope, sort_keys=True, separators=(',', ':')).encode('utf-8')


def webhook_signature(secret, timestamp, body):
    message = str(timestamp).encode('ascii') + b'.' + body
    return 'sha256=' + hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- channels

def deliver_email(config, envelope, timeout=None):
    """One SMTP submission. Returns (outcome, error token). Never raises past this point."""
    mail = config.get('email') or {}
    budget = ATTEMPT_TIMEOUT_SECONDS if timeout is None else timeout
    message = EmailMessage()
    message['Subject'] = mail_subject(envelope)
    message['From'] = mail['from']
    message['To'] = mail['to']
    message['X-Sbarbase-Event-Id'] = envelope['id']
    message['X-Sbarbase-Delivery-Id'] = envelope['delivery']
    message.set_content(mail_body(envelope))
    try:
        if mail.get('tls') == 'tls':
            server = smtplib.SMTP_SSL(mail['host'], int(mail['port']), timeout=budget)
        else:
            server = smtplib.SMTP(mail['host'], int(mail['port']), timeout=budget)
        with closing(server):
            server.ehlo()
            if mail.get('tls') == 'starttls':
                server.starttls()
                server.ehlo()
            server.send_message(message)
        return 'delivered', None
    except smtplib.SMTPResponseException as error:
        code = getattr(error, 'smtp_code', 0)
        token = 'smtp_temporary_failure' if 400 <= code < 500 else 'smtp_refused'
        return ('transient' if token == 'smtp_temporary_failure' else 'failed'), token
    except (socket.timeout, TimeoutError):
        return 'transient', 'smtp_timeout'
    except (ConnectionError, OSError, smtplib.SMTPException):
        return 'transient', 'smtp_unreachable'


def deliver_webhook(config, envelope, secret, timeout=None):
    """One signed POST. Returns (outcome, error token). Never raises past this point."""
    budget = ATTEMPT_TIMEOUT_SECONDS if timeout is None else timeout
    body = webhook_body(envelope)
    timestamp = int(time.time())
    request = urllib.request.Request(config['webhook']['url'], data=body, method='POST')
    request.add_header('content-type', 'application/json')
    request.add_header('X-Sbarbase-Schema', '1')
    request.add_header('X-Sbarbase-Event-Id', envelope['id'])
    request.add_header('X-Sbarbase-Delivery-Id', envelope['delivery'])
    request.add_header('X-Sbarbase-Timestamp', str(timestamp))
    request.add_header('X-Sbarbase-Signature', webhook_signature(secret, timestamp, body))
    try:
        with urllib.request.urlopen(request, timeout=budget) as response:
            response.read(1024)
            if 200 <= response.status < 300:
                return 'delivered', None
            return 'transient', 'webhook_status_' + str(response.status)
    except urllib.error.HTTPError as error:
        token = 'webhook_status_' + str(error.code)
        if 400 <= error.code < 500:
            return 'failed', token
        return 'transient', token
    except urllib.error.URLError as error:
        # urlopen wraps a socket timeout in URLError, so the reason is what classifies it.
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            return 'transient', 'webhook_timeout'
        return 'transient', 'webhook_unreachable'
    except (socket.timeout, TimeoutError):
        return 'transient', 'webhook_timeout'
    except (ConnectionError, OSError):
        return 'transient', 'webhook_unreachable'


# ---------------------------------------------------------------- configuration

class ConfigurationError(RuntimeError):
    pass


def load_config(path):
    """Read and validate the channel configuration. Refuses an unknown schema and a path that
    is not a regular, private file. The file mode is defence in depth only: the parent note
    records that a container environment and a 0600 file are both readable by anything that can
    reach the Docker daemon, so no credential here is protected by its mode."""
    config_path = Path(path)
    if config_path.is_symlink():
        raise ConfigurationError('Notification configuration must not be a symlink')
    if not config_path.is_file():
        raise ConfigurationError('Notification configuration is missing')
    config = json.loads(config_path.read_text())
    if config.get('schema') != 1:
        raise ConfigurationError('Unknown notification configuration schema')
    channels = []
    secret = None
    if config.get('email', {}).get('enabled'):
        mail = config['email']
        for field in ('host', 'port', 'from', 'to', 'tls'):
            if field not in mail:
                raise ConfigurationError('Notification email configuration is incomplete')
        if mail['tls'] not in ('none', 'starttls', 'tls'):
            raise ConfigurationError('Unknown notification email transport security')
        channels.append('email')
    if config.get('webhook', {}).get('enabled'):
        if 'url' not in config['webhook'] or 'secretFile' not in config['webhook']:
            raise ConfigurationError('Notification webhook configuration is incomplete')
        secret_path = Path(config['webhook']['secretFile'])
        if not secret_path.is_absolute():
            secret_path = Path(os.getcwd()) / secret_path
        if secret_path.is_symlink() or not secret_path.is_file():
            raise ConfigurationError('Notification webhook secret is unavailable')
        mode = secret_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ConfigurationError('Notification webhook secret is not private')
        secret = json.loads(secret_path.read_text())
        if secret.get('schema') != 1 or not re.fullmatch(r'[0-9a-f]{64}', str(secret.get('webhookSecret', ''))):
            raise ConfigurationError('Invalid notification webhook secret')
        webhook_secret = secret['webhookSecret']
        channels.append('webhook')
    else:
        webhook_secret = None
    if not channels:
        raise ConfigurationError('No notification channel is enabled')
    return config, channels, webhook_secret


# ---------------------------------------------------------------- the drain step

def drain(database, config, channels, secret, limit=20, lease_ms=LEASE_MS, prune_limit=500):
    """One bounded drain step: claim, render, gate, send, settle, escalate, age out."""
    outcome = {'claimed': 0, 'delivered': 0, 'deferred': 0, 'failed': 0, 'refused': 0,
               'escalated': 0, 'pruned': 0, 'errors': []}
    for item in claim(database, limit, lease_ms):
        outcome['claimed'] += 1
        channel = item['channel']
        try:
            record_refusal_reason(database, item)
            envelope = render(item, item['claim'])
            if channel not in channels:
                settle(database, item, 'failed', 'channel_disabled')
                outcome['failed'] += 1
                continue
            detail = json.loads(item['row']['detail'])
            if refusal_shapes(envelope, detail):
                # Fail closed: nothing is sent, and the refusal is itself an operator event.
                settle(database, item, 'failed', 'redaction_refused')
                enqueue_redaction_refused(database, item, channels)
                outcome['refused'] += 1
                continue
            if channel == 'email':
                result, error = deliver_email(config, envelope)
            else:
                result, error = deliver_webhook(config, envelope, secret)
            state = settle(database, item, result, error)
            if state == 'delivered':
                outcome['delivered'] += 1
            elif state == 'pending':
                outcome['deferred'] += 1
            else:
                outcome['failed'] += 1
                if error != 'channel_disabled':
                    escalate(database, item, channels, error)
        except Exception as error:
            # The drain's own exceptions never reach the operation that produced the event.
            outcome['errors'].append(type(error).__name__)
    outcome['pruned'] = prune(database, limit=prune_limit)
    return outcome


def failure_reason(error):
    """Map a short error token to a closed reason of the vocabulary."""
    if error is None:
        return 'unrecorded'
    if error.startswith('webhook_status'):
        return 'webhook_status'
    if error in ('smtp_timeout', 'smtp_unreachable'):
        return 'smtp_temporary_failure'
    return error if error in REASONS else 'unrecorded'


def escalate(database, item, channels, error):
    """One `notifier.channel_failed` event per channel per six hour window, on the channels
    that are not the failed one. The failed delivery row stays visible."""
    others = tuple(channel for channel in channels if channel != item['channel'])
    if not others:
        return False
    with immediate(database):
        enqueue(database, 'notifier.channel_failed', 'critical',
                'notifier.channel_failed|' + item['channel'],
                {'environment': item['row']['environment'], 'runtime': item['row']['runtime']},
                'system', failure_reason(error),
                {'channel': item['channel'], 'last_error': error}, others)
    return True


def enqueue_redaction_refused(database, item, channels):
    """One refusal event per refused event, on every enabled channel."""
    with immediate(database):
        enqueue(database, 'notifier.redaction_refused', 'critical',
                'notifier.redaction_refused|' + item['event'],
                {'environment': item['row']['environment'], 'runtime': item['row']['runtime']},
                'system', 'redaction_refused',
                {'refused_event': item['event'], 'refused_kind': item['row']['kind']}, channels)


# ---------------------------------------------------------------- worker lock

def worker_lock_held(state):
    """Prove this step runs under the worker's existing exclusive lock.

    The worker exports the descriptor it already holds; this opens no lock. A missing or
    mismatched descriptor is a refusal, never a silent second claimer.
    """
    inherited = os.environ.get('SBARBASE_WORKER_FD')
    if not inherited or not inherited.isdigit():
        return False
    try:
        held, expected = os.fstat(int(inherited)), os.stat(Path(state) / 'worker.lock')
    except (OSError, ValueError):
        return False
    return (held.st_dev, held.st_ino) == (expected.st_dev, expected.st_ino)


def follow_stop():
    stopping = []
    def stop(*_):
        stopping.append(True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return stopping


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description='Durable operator notification drain; runs inside the worker lock.')
    parser.add_argument('--catalog', required=True, help='Control catalog holding the outbox')
    parser.add_argument('--config', required=True, help='Notification channel configuration')
    parser.add_argument('--once', action='store_true', help='Run one bounded step and exit')
    parser.add_argument('--follow', action='store_true', help='Repeat the step until stopped')
    parser.add_argument('--limit', type=int, default=20, help='Deliveries per step, at most 200')
    parser.add_argument('--lease-ms', type=int, default=LEASE_MS)
    parser.add_argument('--state', help='Worker state directory; requires the existing worker lock')
    parser.add_argument('--require-worker-lock', action='store_true',
                        help='Refuse to run unless the worker lock descriptor is inherited')
    parser.add_argument('--json', action='store_true', help='Print one evidence object per step')
    args = parser.parse_args(argv)
    if not args.once and not args.follow:
        parser.error('Choose --once or --follow')
    if args.follow and args.once:
        parser.error('Choose either --once or --follow')
    if not 1 <= args.limit <= 200:
        parser.error('Invalid notification limit')
    if args.state and not worker_lock_held(args.state):
        raise SystemExit('Notification drain requires the existing worker lock')
    if args.require_worker_lock and not worker_lock_held(args.state or (root / '.lab' / 'upstream')):
        raise SystemExit('Notification drain requires the existing worker lock')
    try:
        config, channels, secret = load_config(args.config)
    except ConfigurationError as error:
        raise SystemExit(str(error)) from None
    catalog = os.path.abspath(args.catalog)
    if not os.path.exists(catalog):
        raise SystemExit('Notification catalog is missing')
    stopping = follow_stop()
    parent = os.getppid()
    steps = 0
    while not stopping:
        try:
            with closing(connect(catalog)) as database:
                result = drain(database, config, channels, secret, args.limit, args.lease_ms)
        except sqlite3.OperationalError as error:
            # A busy catalog is not an operation failure: the next step retries, and the rows
            # stay pending and visible meanwhile. Anything else is a real defect and surfaces.
            if 'locked' not in str(error) and 'busy' not in str(error):
                raise
            result = {'claimed': 0, 'delivered': 0, 'deferred': 0, 'failed': 0, 'refused': 0,
                      'escalated': 0, 'pruned': 0, 'errors': [], 'catalog': 'busy'}
        steps += 1
        if args.json and (args.once or result['claimed']):
            print(json.dumps({'step': steps, 'channels': list(channels), **result}, sort_keys=True),
                  flush=True)
        if args.once:
            break
        if os.getppid() != parent:
            # The worker that owned the lock is gone; this drain must not outlive it.
            break
        time.sleep(0.5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())