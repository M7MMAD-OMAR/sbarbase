#!/usr/bin/env python3
"""Bounded disposable original Auth fixture. Never adopts existing resources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/verify'))
import fixture_subnet


def run(argv, data=None, timeout=30):
    result = subprocess.run(argv, input=data, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} {argv[1]} failed with exit {result.returncode}')
    return result.stdout


def docker(*args, data=None):
    return run(['docker', *args], data)


def wait(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError('Disposable service did not become ready within deadline')


def network_identity(record, name, owner, expected_id=None):
    if (not isinstance(record, dict) or not isinstance(record.get('Id'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', record['Id'])
            or (expected_id is not None and record['Id'] != expected_id)
            or record.get('Name') != name or record.get('Driver') != 'bridge'
            or record.get('Scope') != 'local' or record.get('Internal') is not True
            or record.get('Labels', {}).get('io.sbarbase.owner') != owner
            or record.get('Containers') != {}):
        raise RuntimeError('Owned fixture network identity differs')
    return record['Id']


def create_network(name, owner, directory, created, state, report):
    fixture = 'identity-' + name
    directory.mkdir(parents=True, exist_ok=False)
    receipt = fixture_subnet.observe(fixture, directory)
    report['network_admission'] = receipt
    if docker('network', 'ls', '--filter', 'name=^' + name + '$', '--format', '{{.ID}}'):
        raise RuntimeError('Fixture network already exists')
    receipt = fixture_subnet.admit_receipt(json.dumps(receipt), fixture)
    # Retain cleanup intent before create, including a lost create response.
    created.append(('network', name))
    identity = docker('network', 'create', '--driver', 'bridge', '--internal', '--subnet', receipt['subnet'],
                      '--label', 'io.sbarbase.owner=' + owner, name)
    if not re.fullmatch(r'[a-f0-9]{64}\n', identity):
        raise RuntimeError('Created fixture network ID differs')
    state['id'] = identity.rstrip('\n')
    records = json.loads(docker('network', 'inspect', state['id']))
    (directory / 'network-inspect.json').write_text(json.dumps(records, indent=2) + '\n')
    if not isinstance(records, list) or len(records) != 1:
        raise RuntimeError('Fixture network inspection differs')
    record = records[0]
    network_identity(record, name, owner, state['id'])
    ipam = record.get('IPAM', {})
    configurations = ipam.get('Config')
    if (ipam.get('Driver') != 'default' or not isinstance(configurations, list)
            or len(configurations) != 1 or configurations[0].get('Subnet') != receipt['subnet']):
        raise RuntimeError('Owned fixture network subnet differs')
    report['network'] = record


def cleanup_network(name, owner, state):
    identity = state.get('id', name)
    probe = subprocess.run(['docker', 'network', 'inspect', identity],
                           capture_output=True, text=True, timeout=15)
    if probe.returncode:
        expected = 'Error response from daemon: network ' + identity + ' not found\n'
        if probe.returncode == 1 and probe.stdout == '[]\n' and probe.stderr == expected:
            return
        raise RuntimeError('Fixture network cleanup inspection refused')
    records = json.loads(probe.stdout)
    if probe.stderr or not isinstance(records, list) or len(records) != 1:
        raise RuntimeError('Fixture network cleanup inspection differs')
    cid = network_identity(records[0], name, owner, state.get('id'))
    docker('network', 'rm', cid)


def browser_preflight(contract):
    if contract != 'mfa':
        return None
    missing = []
    for path, label in ((ROOT / 'lab/mfa-browser/node_modules/playwright/index.mjs', 'public Playwright package'),
                        (ROOT / '.lab/ui/index.html', 'built UI index')):
        if not path.is_file() or not os.access(path, os.R_OK):
            missing.append(label)
    browser = os.environ.get('CHROMIUM_BIN') or next(
        (path for name in ('google-chrome', 'chromium', 'chromium-browser') if (path := shutil.which(name))), None)
    if not browser or not Path(browser).is_file() or not os.access(browser, os.X_OK):
        missing.append('executable Chromium')
    if missing:
        raise RuntimeError('MFA browser prerequisites missing: ' + ', '.join(missing))
    return str(Path(browser).resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--contract', choices=['mfa', 'cli'], default='mfa')
    args = parser.parse_args()
    browser = browser_preflight(args.contract)
    driver = ROOT / ('lab/management-cli-check.ts' if args.contract == 'cli' else 'lab/management-mfa-check.ts')
    owner = 'sb06-mfa-' + uuid.uuid4().hex[:12]
    db, auth, network = owner + '-db', owner + '-auth', owner + '-net'
    pins = json.loads((ROOT / 'lab/images.lock.json').read_text())
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    if available < 2 * 1024 * 1024:
        raise RuntimeError('Fixture requires at least 2 GiB free host memory')
    before = docker('ps', '-q').split()
    created = []
    network_state = {}
    report = {'schema': 1, 'owner': owner, 'images': {}, 'limits': {}, 'checks': [], 'cleanup': False}
    try:
        for service in ('db', 'auth'):
            image = json.loads(docker('image', 'inspect', pins[service]['id']))[0]
            if image['Id'] != pins[service]['id'] or not set(image.get('RepoDigests', [])).intersection(pins[service]['digests']):
                raise RuntimeError('Pinned public image identity unavailable')
            report['images'][service] = {'id': image['Id'], 'digests': pins[service]['digests']}
        create_network(network, owner, args.output.parent / (owner + '-network'),
                       created, network_state, report)
        with tempfile.TemporaryDirectory(prefix=owner + '-') as directory:
            private = Path(directory)
            os.chmod(private, 0o700)
            password, jwt = secrets.token_hex(24), secrets.token_hex(32)
            db_env = private / 'db.env'
            db_env.write_text('POSTGRES_PASSWORD=' + password + '\n')
            os.chmod(db_env, 0o600)
            created.append(('container', db))
            docker('run', '-d', '--name', db, '--label', 'io.sbarbase.owner=' + owner, '--network', network,
                   '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5', '--pids-limit', '64',
                   '--tmpfs', '/var/lib/postgresql/data:rw,noexec,nosuid,size=256m', '--env-file', str(db_env),
                   pins['db']['id'], '-c', 'max_connections=12', '-c', 'shared_buffers=32MB')
            # The entrypoint's temporary bootstrap server accepts sockets before final startup.
            wait(lambda: bool(docker('exec', db, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres')))
            docker('exec', '-i', db, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', data=(
                'CREATE DATABASE management;\nCREATE DATABASE tenant;\n'
                'CREATE ROLE management_auth LOGIN PASSWORD \'' + password + '\';\n'
                'REVOKE ALL ON DATABASE management FROM PUBLIC;\nGRANT CONNECT ON DATABASE management TO management_auth;\n'))
            docker('exec', '-i', db, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'management',
                   data='CREATE SCHEMA auth AUTHORIZATION management_auth;\nALTER ROLE management_auth SET search_path TO auth;\n')
            config = {'GOTRUE_API_HOST': '0.0.0.0', 'GOTRUE_API_PORT': '9999',
                      'API_EXTERNAL_URL': 'http://localhost/management/auth/v1', 'GOTRUE_SITE_URL': 'http://localhost',
                      'GOTRUE_DB_DRIVER': 'postgres', 'GOTRUE_DB_DATABASE_URL': f'postgres://management_auth:{password}@{db}:5432/management',
                      'GOTRUE_DB_NAMESPACE': 'auth', 'GOTRUE_DB_MAX_POOL_SIZE': '3',
                      'GOTRUE_JWT_SECRET': jwt, 'GOTRUE_JWT_AUD': 'authenticated',
                      'GOTRUE_JWT_DEFAULT_GROUP_NAME': 'authenticated', 'GOTRUE_JWT_ADMIN_ROLES': 'service_role',
                      'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true', 'GOTRUE_DISABLE_SIGNUP': 'true',
                      'GOTRUE_MAILER_AUTOCONFIRM': 'false', 'GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED': 'false',
                      'GOTRUE_MFA_TOTP_ENROLL_ENABLED': 'true', 'GOTRUE_MFA_TOTP_VERIFY_ENABLED': 'true'}
            auth_env = private / 'auth.env'
            auth_env.write_text(''.join(f'{key}={value}\n' for key, value in config.items()))
            os.chmod(auth_env, 0o600)
            created.append(('container', auth))
            docker('run', '-d', '--name', auth, '--label', 'io.sbarbase.owner=' + owner, '--network', network,
                   '--memory', '256m', '--memory-swap', '256m', '--cpus', '.25', '--pids-limit', '64',
                   '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1', '--env-file', str(auth_env), pins['auth']['id'])
            info = json.loads(docker('inspect', auth))[0]
            endpoint = 'http://' + info['NetworkSettings']['Networks'][network]['IPAddress'] + ':9999'
            wait(lambda: urllib.request.urlopen(endpoint + '/health', timeout=2).status == 200)
            for name in (db, auth):
                current = json.loads(docker('inspect', name))[0]
                host = current['HostConfig']
                assert not host.get('PortBindings')
                report['limits'][name.rsplit('-', 1)[-1]] = {'memory': host['Memory'], 'nano_cpus': host['NanoCpus']}
            credentials = private / 'credentials.json'
            credentials.write_text(json.dumps({'endpoint': endpoint, 'jwt': jwt, 'directory': directory}))
            os.chmod(credentials, 0o600)
            result = run(['systemd-run', '--user', '--quiet', '--wait', '--pipe',
                          '--unit=' + owner + '-driver', '-p', 'MemoryMax=256M', '-p', 'CPUQuota=25%',
                          '-p', 'TasksMax=256', '-p', 'RuntimeMaxSec=100',
                          *(['--setenv=CHROMIUM_BIN=' + browser] if browser else []), '--working-directory=' + str(ROOT),
                          shutil.which('bun') or 'bun', str(driver), str(credentials)], timeout=110)
            observations = json.loads(result)
            report.update(observations)
            captures = args.output.parent / 'playwright'
            captures.mkdir(parents=True, exist_ok=True)
            report['browser_captures'] = {}
            for capture in private.glob('mfa-*.png'):
                destination = captures / (owner + '-' + capture.name)
                shutil.copyfile(capture, destination)
                report['browser_captures'][capture.name] = {'path': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}
            audit = docker('exec', db, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'management', '-At',
                           '-c', "SELECT payload->>'action',count(*) FROM auth.audit_log_entries GROUP BY 1 ORDER BY 1;")
            report['native_audit_counts'] = audit.strip().splitlines()
    except Exception as error:
        report['checks'].append({'name': 'fixture runtime completed', 'ok': False})
        report.update({'total': len(report['checks']), 'failed': 1, 'skipped': 0, 'error': str(error)})
    finally:
        for kind, name in reversed(created):
            if kind == 'network':
                try:
                    cleanup_network(name, owner, network_state)
                except Exception as error:
                    report.setdefault('cleanup_errors', []).append(str(error))
                    report['checks'].append({'name': 'fixture network cleanup admitted', 'ok': False})
                    report['total'] = len(report['checks'])
                    report['failed'] = sum(not item['ok'] for item in report['checks'])
                    report.setdefault('skipped', 0)
                    report.setdefault('error', str(error))
                continue
            probe = subprocess.run(['docker', kind, 'inspect', name], capture_output=True, text=True, timeout=15)
            if probe.returncode:
                continue
            inspected = json.loads(probe.stdout)[0]
            labels = inspected.get('Config', {}).get('Labels', {}) if kind == 'container' else inspected.get('Labels', {})
            if labels.get('io.sbarbase.owner') != owner:
                raise RuntimeError('Cleanup ownership mismatch')
            docker('rm', '-f', '-v', name) if kind == 'container' else docker('network', 'rm', name)
        after = docker('ps', '-q').split()
        remaining_containers = docker('ps', '-a', '--filter', 'label=io.sbarbase.owner=' + owner, '--format', '{{.Names}}').split()
        remaining_networks = docker('network', 'ls', '--filter', 'label=io.sbarbase.owner=' + owner, '--format', '{{.Name}}').split()
        report['cleanup'] = not remaining_containers and not remaining_networks and not report.get('cleanup_errors')
        if not report['cleanup'] and not report.get('cleanup_errors'):
            report['checks'].append({'name': 'fixture resources absent after cleanup', 'ok': False})
            report['total'] = len(report['checks'])
            report['failed'] = sum(not item['ok'] for item in report['checks'])
            report.setdefault('skipped', 0)
        report['unrelated_running_containers_preserved'] = set(before).issubset(set(after))
        report['running_container_inventory'] = {'before': before, 'after': after,
                                                'missing': sorted(set(before) - set(after))}
        report['contract'] = args.contract
        report['source'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in [Path(__file__), driver]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({key: report[key] for key in ('total', 'failed', 'skipped')}))
    return int(report['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
