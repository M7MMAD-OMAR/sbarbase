"""One owned dynamic Auth/REST environment; local experiment only."""
import effect_receipt
import fcntl
import json
import os
import re
import secrets
import sys
import time
import urllib.request
import run as lab


def provision(environment):
    effect_receipt.require_permission(lab.STATE,environment)
    if not re.fullmatch(r'e_[a-f0-9]{24}', environment):
        raise RuntimeError('Invalid runtime identifier')
    if not lab.owned(lab.DB):
        raise RuntimeError('Start the component lab first')
    available = int(next(x.split()[1] for x in lab.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
    if available < 4 * 1024 * 1024:
        raise RuntimeError('Insufficient lab memory headroom')
    path = lab.PRIVATE / 'lab.json'
    values = json.loads(path.read_text())
    if environment not in values['environments']:
        if len(values['environments']) >= 5:
            raise RuntimeError('Lab admission limit reached')
        values['environments'][environment] = {k: secrets.token_hex(32) for k in ('auth', 'rest', 'jwt')}
        temp = path.with_suffix('.pending')
        lab.secure_file(temp, json.dumps(values))
        with temp.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    v = values['environments'][environment]
    lab.provision_environment(environment, v)
    hba = ['local all all trust', 'host all postgres 0.0.0.0/0 reject']
    for e in values['environments']:
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', e):
            raise RuntimeError('Invalid environment inventory')
        for role in ('auth', 'rest'):
            hba.append(f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256')
    hba += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
    lab.docker('exec', '-i', lab.DB, 'sh', '-c', 'cat > "$PGDATA/pg_hba.conf"', data='\n'.join(hba)+'\n')
    lab.sql('SELECT pg_reload_conf();')
    pins = json.loads((lab.ROOT / 'lab/images.lock.json').read_text())
    endpoint = lab.launch_services(environment, v, pins)
    for service, suffix in (('auth', '/health'), ('rest', '/')):
        for _ in range(40):
            try:
                with urllib.request.urlopen(endpoint[service] + suffix, timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.5)
        else:
            raise RuntimeError('Service readiness failed')
    path = lab.STATE / 'endpoints.json'
    endpoints = json.loads(path.read_text())
    endpoints[environment] = endpoint
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(endpoints, indent=2))
    os.replace(temp, path)


if __name__ == '__main__':
    try:
        with (lab.STATE / 'operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            provision(sys.argv[1])
            effect_receipt.native_outcome(lab.STATE,sys.argv[1],0,'component-provision-v1')
    except Exception:
        # Never expose SQL, credentials, request payloads or Docker environment.
        print('Environment provisioning failed; retained state can be reconciled.', file=sys.stderr)
        raise SystemExit(1)
