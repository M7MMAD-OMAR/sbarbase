"""Local operator setup. Passwords are read without echo or from bounded stdin."""
import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--stdin', action='store_true', help='Read JSON with email, password and organization from stdin')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
os.chdir(root)
private = root / '.secrets/upstream'
if not private.is_dir() or not (root / '.lab/upstream/management.json').exists():
    raise SystemExit('Start the durable upstream runtime before operator setup.')
if subprocess.run(['git', 'check-ignore', '-q', str(private/'bootstrap.json')]).returncode:
    raise SystemExit('Bootstrap state must be ignored by Git.')
with (private/'bootstrap.lock').open('a') as lock:
    os.chmod(private/'bootstrap.lock', 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another operator setup is active.')
    try:
        if args.stdin:
            raw = sys.stdin.buffer.read(8193)
            if len(raw) > 8192:
                raise ValueError('Too large')
            payload = json.loads(raw)
        else:
            if not sys.stdin.isatty():
                raise ValueError('Use explicit stdin mode')
            email = input('Owner email: ').strip()
            organization = input('Organization name: ').strip()
            password = getpass.getpass('Password (at least 12 characters): ')
            if password != getpass.getpass('Repeat password: '):
                raise ValueError('Passwords do not match')
            payload = {'email': email, 'organization': organization, 'password': password}
        if not isinstance(payload, dict) or set(payload) != {'email', 'organization', 'password'}:
            raise ValueError('Invalid fields')
        child_env = dict(os.environ, SBARBASE_BOOTSTRAP_LOCKED='1')
        result = subprocess.run(['bun', 'lab/bootstrap.ts'], input=json.dumps(payload), text=True,
                                env=child_env, pass_fds=(lock.fileno(),))
        raise SystemExit(result.returncode)
    except (ValueError, EOFError):
        raise SystemExit('Invalid operator setup input; no credentials were printed.')
