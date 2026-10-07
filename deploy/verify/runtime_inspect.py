"""Inspect an owned controller image offline, without executing its startup."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import ssl
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

import cryptography
from cryptography.hazmat.primitives import hashes


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: ' + (result.stdout + result.stderr).strip())
    return (result.stdout + result.stderr).strip()


def inspect():
    if sys.version_info < (3, 12):
        raise ValueError('runtime Python below supported floor')
    settings = command('apt-config', 'dump')
    if 'APT::Snapshot "20261002T000000Z";' not in settings:
        raise ValueError('runtime snapshot missing or different')
    if 'Acquire::https::CaInfo "/etc/ssl/certs/ca-certificates.crt";' not in settings:
        raise ValueError('runtime HTTPS trust setting missing')
    if not ssl.create_default_context().get_ca_certs():
        raise ValueError('runtime HTTPS trust empty')
    hasher = hashes.Hash(hashes.SHA256())
    hasher.update(b'public runtime verification')
    if hasher.finalize().hex() != hashlib.sha256(b'public runtime verification').hexdigest():
        raise ValueError('cryptography digest disagrees')
    tools = {name: command(*args) for name, args in (
        ('bun', ('bun', '--version')), ('docker', ('docker', '--version')),
        ('git', ('git', '--version')), ('ssh', ('ssh', '-V')),
        ('findmnt', ('findmnt', '--version')), ('ps', ('ps', '--version')),
        ('timeout', ('timeout', '--version')), ('readlink', ('readlink', '--version')),
        ('stat', ('stat', '--version')), ('tr', ('tr', '--version')),
        ('sed', ('sed', '--version')), ('awk', ('awk', '-W', 'version')))}
    if tools['bun'] != '1.3.14' or not tools['docker'].startswith('Docker version 29.8.2,'):
        raise ValueError('runtime copied tool version differs from pinned image')
    files = {}
    for path in ('/usr/local/bin/sbarbase-start', '/usr/local/lib/sbarbase/docker_profile.py',
                 '/usr/local/lib/sbarbase/host-preflight.sh'):
        target = Path(path)
        files[path] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                       'mode': oct(target.stat().st_mode & 0o777)}
    if not Path('/usr/local/bin/sbarbase-start').stat().st_mode & 0o111:
        raise ValueError('runtime startup not executable')
    # Exercise the SSH signature machinery used to verify release tags.
    with tempfile.TemporaryDirectory(prefix='runtime-signature-') as folder:
        root = Path(folder)
        key = root / 'key'
        message = root / 'message'
        signers = root / 'signers'
        command('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        signers.write_text('runtime-probe ' + key.with_suffix('.pub').read_text())
        message.write_text('public release signature fixture\n')
        command('ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', 'git', str(message))
        with message.open('rb') as payload:
            result = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(signers), '-I', 'runtime-probe',
                                     '-n', 'git', '-s', str(message) + '.sig'], stdin=payload,
                                    capture_output=True, timeout=15, check=True)
        if b'Good "git" signature' not in result.stdout:
            raise ValueError('runtime SSH signature not verified')
    packages = command('dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\n')
    return {'scope': 'offline-controller-input-and-tool-inspection', 'passed': True,
            'probe_user': {'uid': os.getuid(), 'account': pwd.getpwuid(os.getuid()).pw_name,
                           'uid_10001_registered': any(entry.pw_uid == 10001 for entry in pwd.getpwall())},
            'python': sys.version, 'cryptography': cryptography.__version__, 'tools': tools,
            'timezone': str(ZoneInfo('Etc/UTC')), 'release_signature_roundtrip': True,
            'files': files, 'packages': packages.splitlines(),
            'packages_sha256': hashlib.sha256(packages.encode()).hexdigest(),
            'limitations': ['No startup, Docker socket, Supabase service, architecture matrix or vulnerability acceptance.']}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(), sort_keys=True, indent=2))
    except Exception as error:
        print(json.dumps({'scope': 'offline-controller-input-and-tool-inspection', 'passed': False,
                          'error': str(error)}, sort_keys=True))
        raise SystemExit(1)
