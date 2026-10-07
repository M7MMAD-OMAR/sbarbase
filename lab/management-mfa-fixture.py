#!/usr/bin/env python3
"""Bounded disposable original Auth fixture. Never adopts existing resources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--contract', choices=['mfa', 'cli'], default='mfa')
    args = parser.parse_args()
    driver = ROOT / ('lab/management-cli-check.ts' if args.contract == 'cli' else 'lab/management-mfa-check.ts')
    owner = 'sb06-mfa-' + uuid.uuid4().hex[:12]
    db, auth, network = owner + '-db', owner + '-auth', owner + '-net'
    pins = json.loads((ROOT / 'lab/images.lock.json').read_text())
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    if available < 2 * 1024 * 1024:
        raise RuntimeError('Fixture requires at least 2 GiB free host memory')
    before = docker('ps', '-q').split()
    created = []
    report = {'schema': 1, 'owner': owner, 'images': {}, 'limits': {}, 'checks': [], 'cleanup': False}
    try:
        for service in ('db', 'auth'):
            image = json.loads(docker('image', 'inspect', pins[service]['id']))[0]
            if image['Id'] != pins[service]['id'] or not set(image.get('RepoDigests', [])).intersection(pins[service]['digests']):
                raise RuntimeError('Pinned public image identity unavailable')
            report['images'][service] = {'id': image['Id'], 'digests': pins[service]['digests']}
        created.append(('network', network))
        docker('network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network)
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
                          '-p', 'TasksMax=256', '-p', 'RuntimeMaxSec=100', '--working-directory=' + str(ROOT),
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
        report['cleanup'] = not remaining_containers and not remaining_networks
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
