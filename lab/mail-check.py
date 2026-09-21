"""Prove per environment application mail against a real SMTP server, and prove the disabled posture.

Usage:
    /usr/bin/python3 lab/mail-check.py
    /usr/bin/python3 lab/mail-check.py --negative-control dead-relay
    /usr/bin/python3 lab/mail-check.py --negative-control wrong-password

Everything this probe touches is disposable and owned by its own label:
one private internal network, one pinned Supabase PostgreSQL container, two
pinned Auth containers and two mailboxes from the pinned Mailpit image. It
removes exactly what it created, in a `finally` block, and it never reads, stops
or removes anything owned by the retained durable runtime, whose containers it
snapshots before and after.

The mailer image is deliberately not a lab pin. `lab/install_server.py` walks
every entry of every lock file and requires each image to be present, so adding a
test only mailbox there would make a production install depend on a test image.
Instead the reference lives in MAILER below with the expected local image id, and
the probe refuses to run when the local image has moved under that tag.

What a green run proves: the pinned Auth really submits over SMTP with AUTH and a
Reply-To header; the signup confirmation link is built; the switch is per
environment and a neighbour with no configuration neither sends nor is affected;
a dead relay and a wrong password each produce a recorded failure instead of
silence; and the disabled posture is byte for byte the builder's old output.

The relay speaks STARTTLS under a probe generated certificate authority, which
the Auth containers trust through `SSL_CERT_FILE`. That is not decoration: the
pinned client is Go's `net/smtp`, whose PLAIN authentication refuses an
unencrypted connection unless the relay name resolves to loopback, so without a
verified STARTTLS upgrade the credential path cannot be exercised at all, and the
wrong password control would only ever see `unencrypted connection`.

What it does not prove: delivery through a real provider, a public certificate,
port 465, a template override, or provider quota behaviour. One host, one private
network, one delivery path.

The `--negative-control` modes deliberately break the relay so that the probe
must go red. A probe that cannot fail is not evidence, so its ability to fail is
exercised on purpose and the failure it records is printed and written to the
artifact.
"""
import argparse
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_server
import mail_config
import pinned_images_check
import run as lab

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'lab' / 'mail_config.py'
LABEL = 'io.sbarbase.owner=mail-probe'
RETAINED_LABEL = 'io.sbarbase.owner=durable-upstream'
DB_PIN = 'distro-image.lock.json:default'
AUTH_PIN = 'images.lock.json:auth'

# Pinned test mailbox. The tag is not enough: a moved tag must fail closed, so
# the expected local image id is part of the constant.
MAILER = {'tag': 'public.ecr.aws/supabase/mailpit:v1.30.2', 'id_prefix': 'sha256:37a38e48e933'}
MAILER_ARGS = ('--max', '200')
TLS_ARGS = ('--smtp-tls-cert', '/certs/server.pem', '--smtp-tls-key', '/certs/server-key.pem')
SMTP_PORT = 1025
MAILER_API_PORT = 8025
MIN_HEADROOM_KIB = 6 * 1024 * 1024
# Fallbacks for a host whose Docker predefined address pools are exhausted. All
# inside 10.213.0.0/16, which is outside Docker's pools and outside the LAN this
# workstation sits on.
SUBNET_CANDIDATES = ('10.213.64.0/24', '10.213.65.0/24', '10.213.66.0/24', '10.213.67.0/24',
                     '10.213.68.0/24', '10.213.69.0/24')
MAIL_KEYS = ('GOTRUE_SMTP_HOST', 'GOTRUE_SMTP_PORT', 'GOTRUE_SMTP_USER', 'GOTRUE_SMTP_PASS',
             'GOTRUE_SMTP_ADMIN_EMAIL', 'GOTRUE_SMTP_SENDER_NAME', 'GOTRUE_SMTP_MAX_FREQUENCY',
             'GOTRUE_SMTP_LOGGING_ENABLED', 'GOTRUE_SMTP_HEADERS')
checks = []
cleanup_report = {}
network_name = ''
database_name = ''
mailer_id = ''


def check(name, passed, detail=None):
    entry = {'check': name, 'passed': bool(passed)}
    if detail is not None:
        entry['observed'] = detail
    checks.append(entry)
    if not passed:
        raise RuntimeError(name)


def docker(*args, data=None, check_result=True):
    return lab.docker(*args, data=data, check=check_result)


def inspect(name):
    return json.loads(docker('inspect', name).stdout)[0]


def endpoint(name, port):
    info = inspect(name)
    return f"http://{info['NetworkSettings']['Networks'][network_name]['IPAddress']}:{port}"


def http(url, method='GET', body=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, (json.loads(raw) if raw else {})


def wait_healthy(name, seconds=60):
    for _ in range(seconds * 2):
        info = inspect(name)
        if info['State'].get('Health', {}).get('Status') == 'healthy':
            return
        if info['State']['Running'] is not True:
            raise RuntimeError(name + ' exited during startup')
        time.sleep(.5)
    raise RuntimeError(name + ' readiness timed out')


def wait_auth(name, url, seconds=60):
    for _ in range(seconds * 2):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        if inspect(name)['State']['Running'] is not True:
            raise RuntimeError(name + ' exited during startup')
        time.sleep(.5)
    raise RuntimeError(name + ' readiness timed out')


def pinned(label):
    for name, image_id, reference in install_server.pinned_images():
        if name == label:
            return image_id, reference
    raise RuntimeError('pin not found: ' + label)


def confirmed_pin(label):
    """The local image id must be the pin. A tag that moved is a failure, not a warning."""
    image_id, reference = pinned(label)
    result = subprocess.run(['docker', 'image', 'inspect', reference], capture_output=True, text=True)
    record = json.loads(result.stdout)[0] if result.returncode == 0 else None
    passed, detail = pinned_images_check.evaluate(image_id, record)
    return image_id, reference, passed, detail


def assert_mailer_image():
    """Refuse to run when the mailer tag no longer resolves to the pinned image id."""
    result = subprocess.run(['docker', 'image', 'inspect', MAILER['tag']], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('the pinned test mailbox image is not present locally: ' + MAILER['tag'])
    record = json.loads(result.stdout)[0]
    if not record['Id'].startswith(MAILER['id_prefix']):
        raise RuntimeError('the test mailbox tag resolves to a different image than the pin expects')
    return record['Id'], record.get('RepoDigests', [])


def mail_summary(base):
    status, body = http(base + '/api/v1/messages')
    if status != 200:
        raise RuntimeError('the mailbox API is not answering')
    return body


def message_for(base, address):
    for message in mail_summary(base).get('messages', []):
        if any(item.get('Address') == address for item in message.get('To', [])):
            return message
    return None


def sql(query, database='postgres', expect=True):
    return docker('exec', '-i', database_name, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                  '-U', 'supabase_admin', '-d', database, '-At', data=query, check_result=expect)


def auth_log(name, secret):
    """The lines a diagnostic needs, never any line carrying the credential value."""
    result = docker('logs', '--tail', '60', name, check_result=False)
    lines = []
    for line in (result.stdout + result.stderr).splitlines():
        if secret and secret in line:
            continue
        if re.search(r'mail|smtp|error|fail|panic', line, re.I):
            lines.append(line.strip()[:300])
    return lines


def retained_snapshot():
    """The retained runtime's containers, read only, so its state can be shown unchanged."""
    return docker('ps', '-a', '--filter', 'label=' + RETAINED_LABEL,
                  '--format', '{{.Names}} {{.Id}} {{.State}}', check_result=False).stdout.strip()


class Probe:
    """One disposable run. Every resource it creates is named after its own prefix."""

    def __init__(self, mode, artifact):
        self.mode = mode
        self.artifact = Path(artifact)
        self.containers = []
        self.env_files = []
        self.mail_files = []
        self.environment_a = 'e_' + secrets.token_hex(12)
        self.environment_b = 'e_' + secrets.token_hex(12)
        self.prefix = 'sbarbase-mailprobe-' + secrets.token_hex(5)
        self.names = {'mail_a': self.prefix + '-mail-a', 'mail_b': self.prefix + '-mail-b',
                      'auth_a': self.prefix + '-auth-a', 'auth_b': self.prefix + '-auth-b'}
        self.values = {e: {k: secrets.token_hex(32) for k in ('auth', 'rest', 'jwt')}
                       for e in (self.environment_a, self.environment_b)}
        self.user_password = 'probe-user-password-' + secrets.token_hex(8)
        self.mail_secret = 'probe-mail-secret-' + secrets.token_hex(8)
        self.identity_a = 'mailprobe-' + secrets.token_hex(6) + '@example.test'
        self.identity_b = 'mailprobe-' + secrets.token_hex(6) + '@example.test'
        self.sender = {'admin_email': 'noreply-' + self.environment_a[2:10] + '@mailprobe.example.test',
                       'sender_name': 'Sbarbase probe A',
                       'reply_to': 'support-' + self.environment_a[2:10] + '@mailprobe.example.test'}
        self.db_image = ''
        self.auth_image = ''
        self.diagnostics = {}
        self.certificates = {}
        self.subnet = ''
        self.certificate_dir = None

    # resource lifetime
    def create_network(self):
        """A private internal network of this probe's own.

        This host's Docker predefined address pools are fully subnetted, so a
        plain `docker network create` is refused before the network exists. The
        probe therefore asks for one small subnet outside Docker's pools and
        outside this workstation's own LAN, and it refuses to run rather than
        share a network it does not own.
        """
        args = ['network', 'create', '--internal', '--label', LABEL]
        attempts = []
        first = subprocess.run(['docker', *args, network_name], capture_output=True)
        if first.returncode == 0:
            self.subnet = 'docker default pool'
            return
        attempts.append(first.stderr.decode().strip().splitlines()[-1:])
        for subnet in SUBNET_CANDIDATES:
            result = subprocess.run(['docker', *args, '--subnet', subnet, network_name], capture_output=True)
            if result.returncode == 0:
                self.subnet = subnet
                return
            attempts.append(result.stderr.decode().strip().splitlines()[-1:])
        raise RuntimeError('the probe could not create its own private network after '
                           + str(len(attempts)) + ' attempts')

    def launch(self, name, image, environment, memory, cpus, command=(), mounts=()):
        path = lab.PRIVATE / (name + '.env')
        lab.secure_file(path, ''.join(f'{key}={value}\n' for key, value in environment.items()))
        self.env_files.append(path)
        volumes = []
        for host_path, container_path in mounts:
            volumes += ['-v', str(host_path) + ':' + container_path + ':ro']
        docker('run', '-d', '--name', name, '--label', LABEL, '--network', network_name,
               '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus), '--pids-limit', '128',
               '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', *volumes,
               '--env-file', str(path), image, *command)
        self.containers.append(name)

    def launch_mailbox(self, name):
        args = ['run', '-d', '--name', name, '--label', LABEL, '--network', network_name, '--memory', '128m',
                '--memory-swap', '128m', '--cpus', '0.1', '--pids-limit', '64',
                '-v', str(self.certificates['server']) + ':/certs:ro', '-v', str(self.certificates['relay']) + ':/auth:ro',
                mailer_id]
        # The relay authenticates its clients against a credentials file. It is
        # never told to accept anything, so the credential path is real and the
        # wrong password control has something to reject.
        args += list(MAILER_ARGS) + list(TLS_ARGS) + ['--smtp-auth-file', '/auth/smtp-auth.txt']
        docker(*args)
        self.containers.append(name)
        wait_healthy(name)

    def make_certificates(self):
        """A probe generated authority the Auth containers trust, so AUTH can be exercised.

        Go's `net/smtp` refuses PLAIN authentication on an unencrypted connection
        unless the relay name resolves to loopback, and the pinned client offers no
        knob to skip verification. The relay therefore speaks STARTTLS under a
        certificate for the mailbox container names, signed by an authority the
        Auth containers read through SSL_CERT_FILE. Nothing here is a public
        certificate and nothing is reused outside this run.
        """
        root = lab.STATE / ('mail-certs-' + secrets.token_hex(4))
        ca_dir, server_dir, relay_dir = root / 'ca', root / 'server', root / 'relay'
        for directory in (ca_dir, server_dir, relay_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.certificate_dir = root
        # The relay's own credentials file: the mailbox accepts exactly this pair,
        # which is what makes a wrong password a refusal instead of a silence.
        (relay_dir / 'smtp-auth.txt').write_text('mailprobe:' + self.mail_secret + '\n')
        names = [self.names['mail_a'], self.names['mail_b']]
        commands = [
            ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
             '-keyout', str(ca_dir / 'ca-key.pem'), '-out', str(ca_dir / 'ca.pem'),
             '-subj', '/CN=sbarbase mail probe authority', '-addext', 'basicConstraints=critical,CA:TRUE'],
            ['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
             '-keyout', str(server_dir / 'server-key.pem'), '-out', str(server_dir / 'server.csr'),
             '-subj', '/CN=' + names[0], '-addext', 'subjectAltName=' + ','.join('DNS:' + n for n in names)],
            ['openssl', 'x509', '-req', '-days', '2', '-in', str(server_dir / 'server.csr'),
             '-CA', str(ca_dir / 'ca.pem'), '-CAkey', str(ca_dir / 'ca-key.pem'), '-CAcreateserial',
             '-copy_extensions', 'copy', '-out', str(server_dir / 'server.pem')],
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True)
            if result.returncode:
                raise RuntimeError('the probe could not generate its test certificates')
        for path in (ca_dir / 'ca.pem', server_dir / 'server.pem'):
            os.chmod(path, 0o644)
        for path in (ca_dir / 'ca-key.pem', server_dir / 'server-key.pem', server_dir / 'server.csr',
                     relay_dir / 'smtp-auth.txt'):
            os.chmod(path, 0o600)
        self.certificates = {'ca': ca_dir, 'server': server_dir, 'relay': relay_dir}
        return root

    def stop(self, name):
        docker('stop', name)
        self.containers.remove(name)

    def remove(self, name):
        docker('rm', '-f', name, check_result=False)
        if name in self.containers:
            self.containers.remove(name)

    def cleanup(self):
        for name in list(self.containers):
            self.remove(name)
        for path in list(self.env_files) + list(self.mail_files):
            try:
                path.unlink()
            except OSError:
                pass
        if self.certificate_dir and self.certificate_dir.is_dir():
            shutil.rmtree(self.certificate_dir, ignore_errors=True)
        if network_name:
            docker('network', 'rm', network_name, check_result=False)
        cleanup_report['containers_removed'] = not docker(
            'ps', '-a', '--filter', 'label=' + LABEL, '-q', check_result=False).stdout.strip()
        cleanup_report['subnet'] = self.subnet
        cleanup_report['network_removed'] = docker(
            'network', 'inspect', network_name, check_result=False).returncode != 0
        cleanup_report['mail_files_removed'] = all(not path.exists() for path in self.mail_files)
        cleanup_report['mail_files'] = [str(path) for path in self.mail_files]

    # configuration and services
    def payload(self, host, secret):
        return {'host': host, 'port': SMTP_PORT, 'user': 'mailprobe', 'pass': secret,
                'admin_email': self.sender['admin_email'], 'sender_name': self.sender['sender_name'],
                'reply_to': self.sender['reply_to'], 'max_frequency': '1s', 'otp_exp': 300, 'otp_length': 6,
                'secure_email_change': True, 'autoconfirm': False, 'rate_limit_email_sent': '30',
                'rate_limit_otp': 30, 'rate_limit_verify': 30, 'rate_limit_header': ''}

    def write_mail_file(self, environment, payload):
        path = mail_config.path_for(environment)
        self.mail_files.append(path)
        command = [sys.executable, str(TOOL), 'write', str(path), '--stdin', '--force']
        result = subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True)
        if result.returncode:
            raise RuntimeError('the mail configuration tool refused to write: ' + result.stderr.decode().strip())
        return path

    def build_auth(self, name, environment, mail=None):
        config = dict(lab.auth_configuration(environment, self.values[environment], database_name, mail))
        # Probe only, and only the trust anchor: the pinned client verifies the
        # relay certificate against the host name and offers no skip. This is the
        # one key the probe adds on top of the builder's output.
        config['SSL_CERT_FILE'] = '/certs/ca.pem'
        self.launch(name, self.auth_image, config, '256m', .25,
                    mounts=((self.certificates['ca'], '/certs'),))
        wait_auth(name, endpoint(name, 9999) + '/health')
        return config

    def rebuild_auth(self, name, environment, mail):
        self.remove(name)
        return self.build_auth(name, environment, mail)

    def container_environment(self, name):
        entries = inspect(name)['Config'].get('Env', [])
        return dict(entry.split('=', 1) for entry in entries if '=' in entry)

    # the run itself
    def run(self):
        global mailer_id
        for label in (DB_PIN, AUTH_PIN):
            image_id, reference, passed, detail = confirmed_pin(label)
            check('pinned image ' + label + ' is present and matches its pin', passed, detail)
            if label == DB_PIN:
                self.db_image = image_id
            else:
                self.auth_image = image_id
        mailer_id, digests = assert_mailer_image()
        check('the test mailbox image id matches the pinned prefix', mailer_id.startswith(MAILER['id_prefix']),
              mailer_id)

        self.create_network()
        check('the probe created its own private internal network', bool(self.subnet), self.subnet)
        self.launch(database_name, self.db_image,
                    {'POSTGRES_PASSWORD': secrets.token_hex(32), 'POSTGRES_HOST': '/var/run/postgresql',
                     'POSTGRES_DB': 'postgres'},
                    '1024m', 1, ('postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf',
                                 '-c', 'log_statement=none'))
        for _ in range(240):
            probe = sql("SELECT to_regrole('supabase_privileged_role') IS NOT NULL;", expect=False)
            if probe.returncode == 0 and probe.stdout.strip() == 't' and \
                    docker('exec', database_name, 'pg_isready', '-h', '127.0.0.1',
                           check_result=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('pinned database readiness timed out')
        check('the pinned database initialized', True)

        hba = ['local all supabase_admin trust']
        for environment in (self.environment_a, self.environment_b):
            lab.provision_environment(environment, self.values[environment], executor=sql)
            sql('CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto '
                'WITH SCHEMA extensions; CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions; '
                'GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;', environment)
            for role in ('auth', 'rest'):
                hba.append(f'host {environment} {environment}_{role} 0.0.0.0/0 scram-sha-256')
        hba += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
        docker('exec', '-i', database_name, 'sh', '-c', 'cat > /etc/postgresql/pg_hba.conf',
               data='\n'.join(hba) + '\n')
        sql('SELECT pg_reload_conf();')
        check('two disposable environment databases provisioned', True)

        self.make_certificates()
        check('the probe generated the test certificate authority and relay certificate',
              (self.certificates['ca'] / 'ca.pem').is_file() and (self.certificates['server'] / 'server.pem').is_file())

        # One mailbox per environment. B's mailbox is the outside the platform
        # witness that B sends nothing, which makes "no message" a server side fact.
        self.launch_mailbox(self.names['mail_a'])
        self.launch_mailbox(self.names['mail_b'])
        base_a = endpoint(self.names['mail_a'], MAILER_API_PORT)
        base_b = endpoint(self.names['mail_b'], MAILER_API_PORT)
        check('both mailboxes are healthy and empty',
              mail_summary(base_a)['total'] == 0 and mail_summary(base_b)['total'] == 0)

        host_a = network_name + '-absent' if self.mode == 'dead-relay' else self.names['mail_a']
        secret_a = 'wrong-' + self.mail_secret if self.mode == 'wrong-password' else self.mail_secret
        path = self.write_mail_file(self.environment_a, self.payload(host_a, secret_a))
        mode_of_file = path.stat().st_mode & 0o777
        check('the configuration tool wrote a 0600 file for one environment only', mode_of_file == 0o600,
              oct(mode_of_file))
        shown = subprocess.run([sys.executable, str(TOOL), 'show', str(path)], capture_output=True)
        check('show prints pass set and never the password value',
              shown.returncode == 0 and b'pass set' in shown.stdout and self.mail_secret.encode() not in shown.stdout,
              shown.stdout.decode().strip().splitlines()[:4])
        check('no mail configuration exists for the neighbouring environment',
              mail_config.load(self.environment_b) is None)

        configured = mail_config.load(self.environment_a)
        self.build_auth(self.names['auth_a'], self.environment_a, configured)
        unconfigured = self.build_auth(self.names['auth_b'], self.environment_b)
        environment_b = self.container_environment(self.names['auth_b'])
        check('the unconfigured environment has no SMTP key in its container environment',
              [key for key in environment_b if key.startswith('GOTRUE_SMTP_')] == [])
        check('the unconfigured environment builder output equals the three argument call',
              {key: value for key, value in unconfigured.items() if key != 'SSL_CERT_FILE'} ==
              lab.auth_configuration(self.environment_b, self.values[self.environment_b], database_name, None))
        probe_only = [key for key in unconfigured if key != 'SSL_CERT_FILE' and key not in
                      lab.auth_configuration(self.environment_b, self.values[self.environment_b], database_name)]
        check('the only key the probe adds to an Auth environment is its trust anchor', probe_only == [])
        environment_a = self.container_environment(self.names['auth_a'])
        check('the configured environment carries exactly the designed mail keys',
              sorted(key for key in environment_a if key.startswith('GOTRUE_SMTP_')) == sorted(MAIL_KEYS))
        check('the configured environment keeps recipient address logging off',
              environment_a['GOTRUE_SMTP_LOGGING_ENABLED'] == 'false')
        check('the configured environment does not autoconfirm',
              environment_a['GOTRUE_MAILER_AUTOCONFIRM'] == 'false')

        auth_a, auth_b = endpoint(self.names['auth_a'], 9999), endpoint(self.names['auth_b'], 9999)
        status, body = http(auth_a + '/signup', 'POST',
                            {'email': self.identity_a, 'password': self.user_password})
        if self.mode:
            check(self.mode + ' negative control: the deliberately broken relay fails the mail configured signup',
                  status == 500 and body.get('error_code') == 'unexpected_failure',
                  {'status': status, 'error_code': body.get('error_code')})
        else:
            if status != 200:
                self.diagnostics['auth_a_log'] = auth_log(self.names['auth_a'], self.mail_secret)
            check('a mail configured signup returns 200 and no session, because confirmation is required',
                  status == 200 and 'access_token' not in body,
                  {'status': status, 'error_code': body.get('error_code')})
        delivered = mail_summary(base_a)
        check('a mail configured signup delivers exactly one message', delivered['total'] == 1,
              {'total': delivered['total']})
        message = message_for(base_a, self.identity_a)
        check('the delivered message is addressed to the signed up identity', message is not None)
        check('the envelope sender address is the configured admin address',
              message['From']['Address'] == self.sender['admin_email'], message['From'])
        check('the sender display name is the configured one',
              message['From'].get('Name', '') == self.sender['sender_name'], message['From'])
        # The body and the headers are read decoded: the wire form is quoted
        # printable, where `type=signup` reads `type=3Dsignup`.
        status, detail = http(base_a + '/api/v1/message/' + message['ID'])
        body_text = (detail.get('Text') or '') + (detail.get('HTML') or '')
        check('the confirmation link carries type=signup and a non-empty token',
              status == 200 and 'type=signup' in body_text and
              re.search(r'token=[^&\s"<>]+', body_text) is not None, {'status': status})
        status, headers = http(base_a + '/api/v1/message/' + message['ID'] + '/headers')
        reply_sent = [value for name, values in headers.items() if name.lower() == 'reply-to'
                      for value in (values if isinstance(values, list) else [values])]
        check('the reply to header is the configured reply to address',
              status == 200 and reply_sent == [self.sender['reply_to']], reply_sent)
        check('the confirmation send was recorded against the identity',
              sql("SELECT confirmation_sent_at IS NOT NULL FROM auth.users WHERE email='"
                  + self.identity_a + "';", self.environment_a).stdout.strip() == 't')

        status_b, body_b = http(auth_b + '/signup', 'POST',
                                {'email': self.identity_b, 'password': self.user_password})
        check('an unconfigured signup still succeeds with a session',
              status_b == 200 and bool(body_b.get('access_token')), {'status': status_b})
        check('the unconfigured environment delivered no message', mail_summary(base_b)['total'] == 0)
        check('the configured mailbox received no mail for the neighbouring environment',
              mail_summary(base_a)['total'] == 1)
        check('the unconfigured environment recorded no confirmation send',
              sql("SELECT confirmation_sent_at IS NULL AND email_confirmed_at IS NOT NULL FROM auth.users "
                  "WHERE email='" + self.identity_b + "';", self.environment_b).stdout.strip() == 't')

        self.stop(self.names['mail_a'])
        status, body = http(auth_a + '/recover', 'POST', {'email': self.identity_a})
        check('a dead relay fails the mail dependent operation and is recorded, not silent',
              status == 500 and body.get('error_code') == 'unexpected_failure',
              {'status': status, 'error_code': body.get('error_code')})
        check('the dead relay did not make the environment unhealthy', http(auth_a + '/health')[0] == 200)
        status_b, body_b = http(auth_b + '/signup', 'POST',
                                {'email': 'neighbour-' + self.identity_b, 'password': self.user_password})
        check('the neighbour still signs up while the configured environment has a dead relay',
              status_b == 200 and bool(body_b.get('access_token')) and mail_summary(base_b)['total'] == 0,
              {'status': status_b})

        # The credential is really transmitted and really honoured. The mailbox is
        # fresh here, so every message it holds was caused by the calls below.
        self.remove(self.names['mail_a'])
        self.launch_mailbox(self.names['mail_a'])
        base_a = endpoint(self.names['mail_a'], MAILER_API_PORT)
        check('the relay authenticates its clients against its own credentials file',
              '--smtp-auth-file' in (inspect(self.names['mail_a'])['Config'].get('Cmd') or []))

        self.relay_credential('wrong-' + self.mail_secret)
        status, body = http(auth_a + '/recover', 'POST', {'email': self.identity_a})
        self.diagnostics['auth_a_log_wrong_credential'] = auth_log(self.names['auth_a'], self.mail_secret)
        self.diagnostics['wrong_credential'] = {
            'status': status, 'error_code': body.get('error_code'),
            'configured_pass_is_the_wrong_one':
                self.container_environment(self.names['auth_a'])['GOTRUE_SMTP_PASS'] == 'wrong-' + self.mail_secret,
            'messages_after': mail_summary(base_a)['total'],
        }
        check('a wrong credential is refused and recorded, not swallowed',
              status == 500 and body.get('error_code') == 'unexpected_failure',
              {'status': status, 'error_code': body.get('error_code')})
        check('the refused credential delivered nothing', mail_summary(base_a)['total'] == 0)

        self.relay_credential(self.mail_secret)
        status, body = http(auth_a + '/recover', 'POST', {'email': self.identity_a})
        check('the corrected credential delivers a second message',
              status == 200 and mail_summary(base_a)['total'] == 1, {'status': status})
        return {'signup_messages': 1, 'recovery_messages': 1, 'dead_relay_messages': 0,
                'wrong_credential_messages': 0}

    def relay_credential(self, secret):
        """Write the credential the relay will see, then recreate the Auth container."""
        self.write_mail_file(self.environment_a, self.payload(self.names['mail_a'], secret))
        self.rebuild_auth(self.names['auth_a'], self.environment_a, mail_config.load(self.environment_a))


def main(argv=None):
    global network_name, database_name
    parser = argparse.ArgumentParser(description='Disposable per environment mail probe.')
    parser.add_argument('--negative-control', choices=('dead-relay', 'wrong-password'),
                        help='deliberately break the relay so the probe must go red and record it')
    parser.add_argument('--artifact', default=str(ROOT / '.lab' / 'mail-checks.json'))
    args = parser.parse_args(argv)
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines()
                         if x.startswith('MemAvailable:')))
    if available < MIN_HEADROOM_KIB:
        raise RuntimeError('Insufficient memory headroom for the mail probe')
    lock_path = ROOT / '.lab' / 'mail-check.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another mail probe is active')

    probe = Probe(args.negative_control, args.artifact)
    network_name = probe.prefix + '-net'
    database_name = probe.prefix + '-db'
    # The generated env files and the mail configuration live in the ignored
    # secrets tree, the same tree the runtime uses. Both are created here and
    # removed with the rest of the probe's resources.
    lab.PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(lab.PRIVATE, 0o700)
    mail_config.MAIL_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(mail_config.MAIL_DIR, 0o700)
    before = retained_snapshot()
    evidence = {
        'scope': ('One host, one private internal network, one delivery path over STARTTLS under a probe generated '
                  'certificate authority. The pinned Auth submits to the pinned Mailpit. No public provider, no '
                  'public certificate, no port 465 path, no template override, no provider quota behaviour. No '
                  'credential value and no message body is retained.'),
        'mailer': {'tag': MAILER['tag'], 'id_prefix': MAILER['id_prefix']},
        'environments': {'mail_configured': probe.environment_a, 'unconfigured': probe.environment_b},
        'negative_control': args.negative_control,
        'checks': checks,
    }
    try:
        evidence['delivery'] = probe.run()
    finally:
        probe.cleanup()
        evidence['checks'] = checks
        evidence['diagnostics'] = probe.diagnostics
        evidence['mailer']['id'] = mailer_id or 'not read'
        evidence['passed'] = bool(checks) and all(entry['passed'] for entry in checks)
        evidence['cleanup'] = cleanup_report
        evidence['retained_unchanged'] = retained_snapshot() == before
        probe.artifact.parent.mkdir(parents=True, exist_ok=True)
        probe.artifact.write_text(json.dumps(evidence, indent=2) + '\n')
        failed = [entry['check'] for entry in checks if not entry['passed']]
        print('checks: ' + str(len(checks) - len(failed)) + ' passed, ' + str(len(failed)) + ' failed')
        for name in failed:
            print('FAILED: ' + name)
            for entry in checks:
                if entry['check'] == name and 'observed' in entry:
                    print('observed: ' + json.dumps(entry['observed']))
        print('mailer image: ' + str(evidence['mailer'].get('id', 'not read')) + ' (' + MAILER['tag'] + ')')
        print('cleanup: ' + json.dumps(cleanup_report))
        print('retained runtime unchanged: ' + str(evidence['retained_unchanged']))
        print('evidence: ' + str(probe.artifact))
        lock.close()
        if failed:
            if args.negative_control:
                print('negative control: the probe recorded the deliberate failure above, which is the point of '
                      'this run; a green run here would mean the probe cannot fail')
            raise SystemExit(1)
    print('Mail probe completed: ' + str(len(checks)) + ' checks, disposable resources removed.')


if __name__ == '__main__':
    main()