"""Disposable operator notification probe: one real event, two real channels, one fault.

What it proves, in order:

1. The outbox row and the operation's terminal state commit together. The probe creates a
   private catalog, drives a real capacity refusal through the real settlement path
   (`applyProvisionReceipt` with exit code 75), and reads both rows back from the same file.
2. Both channels really receive a message. Email goes to Mailpit by its pinned image
   reference, the webhook goes to a loopback receiver that answers by itself, and the
   signature is recomputed over `timestamp + "." + raw body`.
3. The negative control. In every fault mode the probe requires a recorded delivery failure
   and a still-correct operation: a silence is a failed probe, not a pass.

Everything it touches is disposable and owned by it alone: one private container, one private
network, one private catalog, one private configuration, all named with a per-run suffix so a
concurrent run cannot collide. The retained .lab/ and .secrets/ trees are never read or
written, and the probe refuses to run against a catalog inside .lab/.
"""
import argparse
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
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = 'io.sbarbase.owner=notification-check'
MAILER_REFERENCE = 'public.ecr.aws/supabase/mailpit:v1.30.2'
# The mailer is a test fixture, so it deliberately does not enter lab/images.lock.json: the
# installer walks every entry of that table and would then require a test image on every
# server. The reference and its expected local image id are held here instead, and the probe
# refuses to run when the local id differs, which keeps the anti-drift property without the
# install dependency.
MAILER_IMAGE_ID = '37a38e48e933'
DRIVE = """
import {{Catalog}} from '{root}/src/control/catalog';
const catalog=new Catalog({catalog});
const organization=catalog.createOrganization('alice','A');
const project=catalog.createProject('alice',organization,'P');
const environment=catalog.createEnvironment('alice',project,'production');
const job=catalog.claimProvision();
catalog.applyProvisionReceipt(environment,job.runtime,job.claim,job.attempt,75);
catalog.close();
console.log(JSON.stringify({{environment,runtime:job.runtime}}));
"""
ENVELOPE_FIELDS = ('action', 'actor', 'at', 'delivery', 'id', 'kind', 'last_at', 'occurrences',
                   'reason', 'reason_class', 'schema', 'severity', 'subject', 'summary',
                   'window_seconds')
BODY_DENYLIST = ('postgres://', 'postgresql://', 'sb_secret_', 'sb_publishable_', 'password=',
                 'apikey', 'authorization:', 'BEGIN PRIVATE KEY')


class Checks:
    def __init__(self):
        self.rows = []

    def record(self, name, ok, detail):
        self.rows.append({'check': name, 'ok': bool(ok), 'detail': detail})
        print('{} {}: {}'.format('PASS' if ok else 'FAIL', name, detail), flush=True)
        return bool(ok)

    def failed(self):
        return [row['check'] for row in self.rows if not row['ok']]


class Receiver:
    """Loopback webhook receiver on a port chosen at run time. Answers 200, or never answers."""

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

    def url(self):
        return 'http://127.0.0.1:{}/sbarbase'.format(self.port)


def command(*arguments, check=True, cwd=None):
    result = subprocess.run(list(arguments), capture_output=True, text=True, cwd=cwd)
    if check and result.returncode:
        raise RuntimeError('command failed: {} {}'.format(arguments[0], result.stderr[-300:]))
    return result


def docker(*arguments, check=True):
    return command('docker', *arguments, check=check)


def local_image_id(reference):
    result = docker('images', '--format', '{{.ID}}', reference, check=False)
    lines = result.stdout.strip().splitlines()
    return lines[0] if lines else ''


def repo_digests(reference):
    result = docker('image', 'inspect', '--format', '{{json .RepoDigests}}', reference, check=False)
    try:
        return json.loads(result.stdout.strip() or '[]')
    except ValueError:
        return []


def container_ip(name):
    return docker('inspect', '--format',
                  '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', name).stdout.strip()


def labels_of(resource_id, kind):
    field = '.Labels' if kind == 'network' else '.Config.Labels'
    return docker(kind, 'inspect', '--format', '{{json ' + field + '}}', resource_id,
                  check=False).stdout.strip()


def create_disposable_network(network, suffix):
    """A private internal network on a subnet outside Docker's default pools.

    The default pools are fully subnetted on this host, and a 192.168/16 range can collide
    with the workstation's own routes, so the subnet is requested explicitly and rotated
    until one is accepted. Nothing outside this probe uses the result.
    """
    offset = int(suffix, 16) % 240
    for step in range(1, 200):
        third = 1 + (offset + step) % 240
        subnet = '10.214.{}.0/24'.format(third)
        result = docker('network', 'create', '--internal', '--subnet', subnet, '--label', OWNER,
                        network, check=False)
        if result.returncode == 0:
            return subnet
    raise RuntimeError('could not allocate a disposable internal network')


def mailpit_ready(ip, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen('http://{}:8025/api/v1/info'.format(ip), timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def mailpit_messages(ip, attempts=3):
    """Read the mailer's own record. Mailpit serves its API on 8025 and SMTP on 1025."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen('http://{}:8025/api/v1/messages'.format(ip),
                                        timeout=5) as response:
                listing = json.load(response)
            break
        except Exception:
            if attempt == attempts - 1:
                return None
            time.sleep(1)
    messages = []
    for summary in listing.get('messages', []):
        with urllib.request.urlopen('http://{}:8025/api/v1/message/{}'.format(ip, summary['ID']),
                                    timeout=5) as response:
            detail = json.load(response)
        messages.append({'id': summary['ID'], 'subject': detail.get('Subject', ''),
                         'text': detail.get('Text', ''),
                         'to': [address.get('Address') for address in detail.get('To', [])]})
    return messages


def body_shapes(text):
    return [shape for shape in BODY_DENYLIST if shape in text]


def drive_capacity_refusal(catalog, directory, name='drive.ts'):
    """The real path: a real catalog, a real claim, the real receipt settlement of exit 75."""
    driver = directory / name
    driver.write_text(DRIVE.format(root=ROOT, catalog=json.dumps(str(catalog))))
    result = command('bun', str(driver))
    return json.loads(result.stdout.strip().splitlines()[-1])


def write_notification_files(directory, webhook_url, mail_host, healthy_mail=True):
    secret = uuid.uuid4().hex + uuid.uuid4().hex[:32]
    secret_file = directory / 'notifier.json'
    secret_file.write_text(json.dumps({'schema': 1, 'webhookSecret': secret}))
    os.chmod(secret_file, 0o600)
    config = {
        'schema': 1,
        'email': {'enabled': True, 'host': mail_host, 'port': 1025 if healthy_mail else 1,
                  'from': 'sbarbase@installation.invalid', 'to': 'operator@example.invalid',
                  'tls': 'none'},
        'webhook': {'enabled': True, 'url': webhook_url, 'secretFile': str(secret_file)},
    }
    config_file = directory / 'notifications.json'
    config_file.write_text(json.dumps(config))
    os.chmod(config_file, 0o600)
    return config_file, secret


def deliveries(catalog):
    with sqlite3.connect(catalog) as database:
        rows = database.execute('''SELECT d.channel,d.state,d.attempts,d.last_error,d.next_attempt_at,
            o.id,o.kind,o.severity,o.reason,o.occurrences,o.detail
            FROM notification_delivery d JOIN notification_outbox o ON o.id=d.event
            ORDER BY o.at,d.channel''').fetchall()
    return [{'channel': row[0], 'state': row[1], 'attempts': row[2], 'last_error': row[3],
             'next_attempt_at': row[4], 'event': row[5], 'kind': row[6], 'severity': row[7],
             'reason': row[8], 'occurrences': row[9], 'detail': json.loads(row[10])}
            for row in rows]


def operation_state(catalog):
    """The operation's own columns, read from the catalog, never from the notifier."""
    with sqlite3.connect(catalog) as database:
        return {
            'job': database.execute('SELECT state,failure,attempt FROM provision_jobs').fetchall(),
            'audit': database.execute('SELECT action,detail FROM audit_events ORDER BY sequence').fetchall(),
            'effects': database.execute('SELECT attempt,exit_code FROM provision_effect_results').fetchall(),
        }


def operation_signature(state):
    """Compare the operation without comparing the identities two fixtures never share."""
    return {'job': state['job'],
            'actions': [action for action, _ in state['audit']],
            'effects': state['effects']}


def run(args, mode, checks, evidence, state):
    image_id = local_image_id(MAILER_REFERENCE)
    evidence['mailer'] = {'reference': MAILER_REFERENCE, 'expected_image_id': MAILER_IMAGE_ID,
                          'local_image_id': image_id, 'repo_digests': repo_digests(MAILER_REFERENCE)}
    if image_id != MAILER_IMAGE_ID:
        checks.record('the mailer is exactly the pinned local image', False,
                      'local {} expected {}'.format(image_id or 'absent', MAILER_IMAGE_ID))
        return
    checks.record('the mailer is exactly the pinned local image', True,
                  '{} local id {}'.format(MAILER_REFERENCE, image_id))

    # 1. The fixture, through the real settlement path, on a private catalog.
    identity = drive_capacity_refusal(state['catalog'], state['directory'])
    settled = operation_state(state['catalog'])
    checks.record('the capacity refusal reached its terminal state',
                  settled['job'] == [('failed', 'capacity_exceeded', 1)]
                  and settled['effects'] == [(1, 75)],
                  'job {} effects {}'.format(settled['job'], settled['effects']))
    rows = deliveries(state['catalog'])
    checks.record('the outbox row committed with the state change it describes',
                  len(rows) == 2 and rows[0]['kind'] == 'provision.capacity_refused'
                  and rows[0]['severity'] == 'warning' and rows[0]['occurrences'] == 1,
                  '{} delivery rows, event {}'.format(len(rows), rows[0]['event']))
    evidence['event'] = {'id': rows[0]['event'], 'kind': rows[0]['kind'],
                         'severity': rows[0]['severity'], 'reason': rows[0]['reason'],
                         'detail': rows[0]['detail'], 'environment': identity['environment']}

    # 2. Disposable resources: private network, private mailpit, loopback receiver.
    evidence['mailer']['subnet'] = create_disposable_network(state['network'], state['suffix'])
    docker('run', '-d', '--name', state['container'], '--label', OWNER,
           '--network', state['network'], '--memory', '128m', '--memory-swap', '128m',
           '--cpus', '.25', '--pids-limit', '64', '--log-opt', 'max-size=5m',
           '--log-opt', 'max-file=2', MAILER_REFERENCE)
    state['started'] = True
    identifier = docker('ps', '-aq', '--filter', 'name=' + state['container']).stdout.strip()
    evidence['mailer']['labels'] = labels_of(identifier, 'container')
    ip = container_ip(state['container'])
    evidence['mailer']['container_ip'] = ip
    checks.record('the disposable mailer answers on its own API',
                  mailpit_ready(ip, 60),
                  'http://{}:8025/api/v1/info'.format(ip))

    def mailbox():
        messages = mailpit_messages(ip)
        if messages is None:
            checks.record('the mailer API is readable', False, 'mailpit did not answer')
            return []
        return messages

    checks.record('the mailer API lists an empty mailbox before the run',
                  mailbox() == [], 'mailpit message count 0')
    receiver = Receiver(hold=(mode == 'timeout_webhook'))
    state['receiver'] = receiver
    webhook_url = receiver.url() if mode != 'broken_webhook' else 'http://127.0.0.1:1/sbarbase'
    config_file, secret = write_notification_files(state['directory'], webhook_url, ip,
                                                  healthy_mail=(mode != 'broken_email'))
    if mode == 'redaction_refused':
        # Injected through the internal path: a credential written straight into a rendered
        # field of the outbox row, past the enqueue gate that would normally refuse it.
        with sqlite3.connect(state['catalog']) as database:
            database.execute('UPDATE notification_outbox SET actor=?',
                             ('postgres://operator:secret@database.internal:5432/management',))

    # 3. The drain, exactly as the worker invokes it: one bounded step under no new lock.
    started_at = time.monotonic()
    result = command('/usr/bin/python3', str(ROOT / 'lab/notify.py'), '--catalog',
                     str(state['catalog']), '--config', str(config_file), '--once', '--json')
    elapsed = time.monotonic() - started_at
    step = json.loads(result.stdout.strip().splitlines()[-1])
    evidence['drain'] = {'seconds': round(elapsed, 3), 'result': step}
    checks.record('the drain step is bounded, not a hang', elapsed < 15,
                  '{:.2f} seconds'.format(elapsed))
    checks.record('the drain claims each delivery exactly once', step['claimed'] == 2,
                  'claimed {} delivered {} deferred {} failed {} refused {}'.format(
                      step['claimed'], step['delivered'], step['deferred'], step['failed'],
                      step['refused']))

    # 4. The operation is identical to a healthy control built by the same code path.
    control_catalog = state['directory'] / 'control-run.sqlite'
    drive_capacity_refusal(control_catalog, state['directory'], name='control.ts')
    control = operation_state(control_catalog)
    checks.record('a channel failure cannot change the operation it reports',
                  operation_signature(control) == operation_signature(settled),
                  'healthy {} versus this run {}'.format(operation_signature(control),
                                                         operation_signature(settled)))

    rows = deliveries(state['catalog'])
    evidence['deliveries'] = rows
    email = next(row for row in rows if row['channel'] == 'email')
    webhook = next(row for row in rows if row['channel'] == 'webhook')

    if mode == 'redaction_refused':
        refused = [row for row in rows if row['kind'] == 'provision.capacity_refused']
        checks.record('the fail-closed gate refused both channels',
                      len(refused) == 2 and all(row['state'] == 'failed'
                                                and row['last_error'] == 'redaction_refused'
                                                for row in refused),
                      ' '.join('{}={}'.format(row['channel'], row['last_error']) for row in refused))
        checks.record('no byte reached the receiver', receiver.requests == [],
                      'receiver saw {} request(s)'.format(len(receiver.requests)))
        checks.record('no byte reached the mailer', mailbox() == [],
                      'mailpit message count 0')
        checks.record('the refusal is itself an operator event',
                      len([row for row in rows if row['kind'] == 'notifier.redaction_refused']) == 2,
                      'one refusal event per enabled channel')
        return

    if mode == 'broken_email':
        checks.record('a broken email channel produced a recorded failure, not a silence',
                      email['attempts'] >= 1 and bool(email['last_error']),
                      'email {} {} attempts {}'.format(email['state'], email['last_error'],
                                                       email['attempts']))
        checks.record('the broken email channel sent nothing', mailbox() == [],
                      'mailpit message count 0')
        checks.record('the healthy webhook channel still delivered',
                      webhook['state'] == 'delivered' and len(receiver.requests) == 1,
                      'webhook {} requests {}'.format(webhook['state'], len(receiver.requests)))
        return

    if mode in ('broken_webhook', 'timeout_webhook'):
        expected = 'webhook_timeout' if mode == 'timeout_webhook' else 'webhook_unreachable'
        checks.record('a broken webhook channel produced a recorded failure, not a silence',
                      webhook['attempts'] >= 1 and webhook['last_error'] == expected
                      and webhook['state'] == 'pending'
                      and webhook['next_attempt_at'] > int(time.time() * 1000) - 1000,
                      'webhook {} {} attempts {}'.format(webhook['state'], webhook['last_error'],
                                                         webhook['attempts']))
        checks.record('the closed port received no answerable delivery',
                      len(receiver.requests) == (1 if mode == 'timeout_webhook' else 0),
                      'receiver saw {} request(s)'.format(len(receiver.requests)))
        mail = mailbox()
        checks.record('the healthy email channel still delivered',
                      email['state'] == 'delivered' and len(mail) == 1,
                      'email {} mailpit {}'.format(email['state'], len(mail)))
        return

    # 5. Healthy run: read both channels back and verify the signature.
    checks.record('the webhook delivered and the receiver answered 200',
                  webhook['state'] == 'delivered' and len(receiver.requests) == 1,
                  'webhook {} requests {}'.format(webhook['state'], len(receiver.requests)))
    headers, body = receiver.requests[0]
    envelope = json.loads(body)
    signature = headers.get('X-Sbarbase-Signature', '')
    expected = 'sha256=' + hmac.new(secret.encode(),
                                    headers.get('X-Sbarbase-Timestamp', '').encode() + b'.' + body,
                                    hashlib.sha256).hexdigest()
    checks.record('the webhook signature verifies over timestamp and raw body',
                  hmac.compare_digest(signature, expected),
                  'header {} recomputed {}'.format(signature[:24], expected[:24]))
    checks.record('the event identity and the closed header set are correct',
                  headers.get('X-Sbarbase-Event-Id') == envelope['id'] == rows[0]['event']
                  and headers.get('X-Sbarbase-Delivery-Id') == envelope['delivery']
                  and headers.get('X-Sbarbase-Schema') == '1',
                  'event {} delivery {}'.format(envelope['id'][:8], envelope['delivery'][:8]))
    checks.record('the payload is the closed field set',
                  sorted(envelope) == sorted(ENVELOPE_FIELDS), ', '.join(sorted(envelope)))
    checks.record('the payload carries the recorded kind, severity and reason',
                  envelope['kind'] == 'provision.capacity_refused'
                  and envelope['severity'] == 'warning' and envelope['occurrences'] == 1
                  and envelope['reason'] in ('unrecorded', 'memory_headroom', 'disk_headroom',
                                             'inode_headroom', 'measurement_unavailable',
                                             'cpu_some10', 'io_full10', 'memory_full10',
                                             'installation_limit', 'connection_budget'),
                  'kind {} reason {}'.format(envelope['kind'], envelope['reason']))
    mail = mailbox()
    if mail:
        checks.record('the mail carries the event id, the severity and the kind',
                      envelope['id'] in mail[0]['subject']
                      and 'warning' in mail[0]['subject']
                      and 'provision.capacity_refused' in mail[0]['subject'],
                      'subject {}'.format(mail[0]['subject']))
        shapes = body_shapes(mail[0]['text'])
        checks.record('the mail carries the same summary and no credential shape',
                      envelope['summary'] in mail[0]['text'] and not shapes,
                      'body {} characters, shapes {}'.format(len(mail[0]['text']),
                                                             shapes or 'none'))
        checks.record('the mail carries the event identity for receiver-side deduplication',
                      envelope['id'] in mail[0]['text'],
                      'event id present in the body')
    else:
        checks.record('the mail carries the event id, the severity and the kind', False,
                      'mailpit received no message')
        checks.record('the mail carries the same summary and no credential shape', False,
                      'mailpit received no message')
        checks.record('the mail carries the event identity for receiver-side deduplication', False,
                      'mailpit received no message')
    checks.record('the operator recipient never reaches the catalog',
                  'operator@example.invalid' not in json.dumps(evidence['deliveries']),
                  'no recipient address in any delivery row')


def cleanup(checks, evidence, state):
    """Remove only what this run created, after verifying the ownership label."""
    removed = {'container': False, 'network': False, 'temporary_directory': False}
    if state['started']:
        identifier = docker('ps', '-aq', '--filter', 'name=' + state['container']).stdout.strip()
        if identifier:
            labels = labels_of(identifier, 'container')
            if 'io.sbarbase.owner' in labels and 'notification-check' in labels:
                docker('rm', '-f', state['container'])
                removed['container'] = True
    if state['network'] in docker('network', 'ls', '--format', '{{.Name}}').stdout.split():
        identifier = docker('network', 'ls', '--filter', 'name=' + state['network'],
                            '--format', '{{.ID}}').stdout.strip()
        labels = labels_of(identifier, 'network') if identifier else ''
        if 'io.sbarbase.owner' in labels and 'notification-check' in labels:
            docker('network', 'rm', state['network'])
            removed['network'] = True
    shutil.rmtree(state['directory'], ignore_errors=True)
    removed['temporary_directory'] = not state['directory'].exists()
    evidence['cleanup'] = {
        **removed,
        'containers_still_labelled': docker('ps', '-a', '--filter', 'label=' + OWNER,
                                            '--format', '{{.Names}}').stdout.strip(),
        'networks_still_labelled': docker('network', 'ls', '--filter', 'label=' + OWNER,
                                          '--format', '{{.Name}}').stdout.strip(),
    }
    print('cleanup: container {} network {} temporary directory {}'.format(
        removed['container'], removed['network'], removed['temporary_directory']), flush=True)
    print('cleanup evidence: ' + json.dumps(evidence['cleanup'], sort_keys=True), flush=True)


def report(checks, evidence):
    evidence['checks'] = checks.rows
    evidence['failed_checks'] = checks.failed()
    evidence['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    print('evidence: ' + json.dumps(evidence, sort_keys=True, default=str), flush=True)
    print('result: {} failed check(s)'.format(len(evidence['failed_checks'])), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Disposable operator notification probe.')
    parser.add_argument('--local', action='store_true',
                        help='Run against local disposable resources (the only mode)')
    parser.add_argument('--broken-webhook', action='store_true',
                        help='Negative control: the webhook url is on a closed loopback port')
    parser.add_argument('--broken-email', action='store_true',
                        help='Negative control: the mail host has no listener')
    parser.add_argument('--timeout-webhook', action='store_true',
                        help='Negative control: the receiver accepts and never answers')
    parser.add_argument('--redaction-refused', action='store_true',
                        help='Negative control: a credential shape reaches a rendered field')
    parser.add_argument('--keep', action='store_true',
                        help='Keep the disposable container and network for inspection')
    args = parser.parse_args(argv)
    modes = [name for name in ('broken_webhook', 'broken_email', 'timeout_webhook', 'redaction_refused')
             if getattr(args, name)]
    if len(modes) > 1:
        raise SystemExit('Choose one fault mode at most')
    mode = modes[0] if modes else 'healthy'
    suffix = uuid.uuid4().hex[:8]
    directory = Path(tempfile.mkdtemp(prefix='sbarbase-notification-check-'))
    catalog = directory / 'control.sqlite'
    if '.lab' in catalog.resolve().parts:
        shutil.rmtree(directory, ignore_errors=True)
        raise SystemExit('Refusing to run against a catalog inside .lab/')
    checks = Checks()
    evidence = {'schema': 1, 'mode': mode, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'catalog': str(catalog), 'container': 'sbarbase-notify-mailpit-' + suffix,
                'network': 'sbarbase-notify-net-' + suffix}
    state = {'directory': directory, 'catalog': catalog, 'container': evidence['container'],
             'network': evidence['network'], 'suffix': suffix, 'receiver': None, 'started': False}
    try:
        run(args, mode, checks, evidence, state)
    except Exception as error:
        checks.record('the probe ran to completion without an unexpected error', False,
                      '{}: {}'.format(type(error).__name__, str(error)[:300]))
    finally:
        if state['receiver'] is not None:
            state['receiver'].close()
        if not args.keep:
            cleanup(checks, evidence, state)
    report(checks, evidence)
    return 1 if checks.failed() else 0


if __name__ == '__main__':
    raise SystemExit(main())