"""Read the retained original management Auth process without launching or configuring it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import urllib.request
import durable_runtime as native
import run


def private_json(path, expected_identity=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077 or metadata.st_nlink != 1:
            raise ValueError('Retained original Auth unavailable')
        if expected_identity is not None and f'{metadata.st_dev}:{metadata.st_ino}' != expected_identity:
            raise ValueError('Retained original Auth unavailable')
        with os.fdopen(os.dup(fd)) as stream:
            return json.load(stream)
    finally:
        os.close(fd)


def realm():
    # Retain original Runtime read methods without its credential-generating constructor.
    runtime = object.__new__(native.Runtime)
    runtime.values = private_json(native.PRIVATE / 'runtime.json')
    runtime.pins = json.loads((run.ROOT / 'lab/images.lock.json').read_text())
    name = native.PREFIX + '-management-auth'
    item = native.inspect('container', name)
    if not item or not item['State']['Running'] or item['Image'] != runtime.image('auth')[1] or item['Config'].get('Labels', {}).get('io.sbarbase.owner') != native.OWNER:
        raise ValueError('Retained original Auth unavailable')
    actual = dict(entry.split('=', 1) for entry in item['Config'].get('Env', []) if '=' in entry)
    expected = run.auth_configuration('management', runtime.values['management'], native.DB)
    expected.update({'GOTRUE_DISABLE_SIGNUP': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'false', 'GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED': 'false'})
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError('Retained original Auth unavailable')
    endpoint = runtime.endpoint(name, 9999)
    projection = private_json(native.STATE / 'management.json')
    if projection != {'auth': endpoint}:
        raise ValueError('Retained original Auth unavailable')
    return {'auth': endpoint, 'key': native.token(runtime.values['management']['jwt'], 'anon'),
            'process': item['Id'] + ':' + hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()}


def original_user(material_path, digest, identity):
    path = Path(material_path)
    if path.parent != native.PRIVATE / 'lifecycle-authorization' or path.resolve(strict=True) != path:
        raise ValueError('Retained original Auth unavailable')
    value = private_json(path, identity)
    if hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() != digest:
        raise ValueError('Retained original Auth unavailable')
    factor = value.get('factor')
    if type(factor) is not str or not any(item.get('id') == factor and item.get('status') == 'verified'
                                        and item.get('factor_type') == 'totp' for item in value['identity']['factors']):
        raise ValueError('Retained original Auth unavailable')
    before = realm()
    if before != value['realm']:
        raise ValueError('Retained original Auth unavailable')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    request = urllib.request.Request(before['auth'].rstrip('/') + '/user', headers={'authorization': value['bearer'], 'apikey': before['key']})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
        if response.status != 200:
            raise ValueError('Retained original Auth unavailable')
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('Retained original Auth unavailable')
        user = json.loads(raw)
    if realm() != before or user.get('is_anonymous') or user.get('id') != value['identity']['actor']:
        raise ValueError('Retained original Auth unavailable')
    if not any(item.get('id') == factor and item.get('status') == 'verified' and item.get('factor_type') == 'totp'
               for item in user.get('factors', [])):
        raise ValueError('Retained original Auth unavailable')
    return {'factors': [{key: factor[key] for key in ('id', 'status', 'factor_type')} for factor in user.get('factors', [])]}


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--material')
        parser.add_argument('--digest')
        parser.add_argument('--identity')
        args = parser.parse_args()
        print(json.dumps(original_user(args.material, args.digest, args.identity) if args.material and args.digest else realm()))
    except Exception:
        sys.exit('Retained original Auth unavailable')
