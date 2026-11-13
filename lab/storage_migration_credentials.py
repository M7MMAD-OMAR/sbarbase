"""Mint an attended, purpose-bound Storage token in existing server custody.

This operator command writes a private file, never a token on stdout. It neither
provisions privileges nor grants a bucket, and cannot mint runtime credentials.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

import durable_runtime as runtime
import runtime_files


def mint(environment, purpose, manifest_sha256, expires_at, output, *, now=None, read_secrets=None, read_endpoints=None):
    now = int(time.time()) if now is None else now
    if not re.fullmatch(r'e_[a-f0-9]{24}', environment) or purpose not in ('originals', 'evidence') \
            or not re.fullmatch(r'[a-f0-9]{64}', manifest_sha256) or type(expires_at) is not int \
            or not 60 < expires_at - now <= 3600:
        raise ValueError('Invalid attended migration identity or lifetime')
    endpoint = (read_endpoints or runtime.published_endpoints)().get(environment, {}).get('storage')
    if not isinstance(endpoint, dict) or endpoint.get('tenantHost') != environment + '.storage.internal':
        raise ValueError('Storage tenant registration differs')
    output = Path(output)
    if not output.is_absolute() or output.name in ('', '.', '..'):
        raise ValueError('Absolute private output path required')
    # Open the existing private parent by FD, without following symlinks. All
    # writes use that held descriptor; an unsafe parent is never repaired here.
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ValueError('Output parent must be owned and private')
        values = (read_secrets or (lambda: runtime_files.read_runtime_secret(runtime.PRIVATE)))()
        signing = values.get('environments', {}).get(environment, {}).get('jwt')
        if not isinstance(signing, str) or len(signing) < 32:
            raise ValueError('Environment signing custody unavailable')
        role = environment + '_fanio_migration_' + purpose
        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
        unsigned = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({
            'role': role, 'aud': 'sbarbase:storage', 'iss': 'sbarbase-storage-migration-operator',
            'iat': now, 'nbf': now - 5, 'exp': expires_at, 'jti': secrets.token_hex(16),
            'storage_migration_manifest': manifest_sha256})
        signature = base64.urlsafe_b64encode(hmac.new(signing.encode(), unsigned.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, 'w') as handle:
                handle.write(unsigned + '.' + signature + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(parent_fd)
        except Exception:
            os.unlink(output.name, dir_fd=parent_fd)
            raise
    finally:
        os.close(parent_fd)
    return {'environment': environment, 'purpose': purpose, 'role': role,
            'manifest_sha256': manifest_sha256, 'expires_at': expires_at, 'credential': 'written-private-file'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True)
    parser.add_argument('--purpose', choices=('originals', 'evidence'), required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--expires-at', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = mint(args.environment, args.purpose, args.manifest_sha256, args.expires_at, args.output)
    except Exception:
        print(json.dumps({'status': 'refused', 'reason': 'Migration credential was not published'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
