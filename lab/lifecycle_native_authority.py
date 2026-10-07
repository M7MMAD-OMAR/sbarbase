"""Original installed lifecycle authority checked inside each native effect process."""
from contextvars import ContextVar
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import urllib.request
from lifecycle_auth_realm import realm, private_json
import durable_runtime as native

_current = ContextVar('original_lifecycle_authority', default=None)


def require(condition):
    if not condition:
        raise ValueError('Lifecycle authority revoked')


class NativeLifecycleAuthority:
    def __init__(self, catalog, request):
        self.catalog = Path(catalog).resolve(strict=True)
        require(self.catalog == native.STATE / 'control.sqlite')
        metadata = self.catalog.stat()
        require(metadata.st_uid == os.getuid() and metadata.st_nlink == 1 and not metadata.st_mode & 0o077)
        self.catalog_identity = f'{metadata.st_dev}:{metadata.st_ino}'
        self.request = request
        self.original_digest = None
        self.connection = sqlite3.connect(self.catalog, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            self.assert_current()
        except Exception:
            self.close()
            raise

    def close(self):
        self.connection.rollback()
        self.connection.close()

    def assert_current(self):
        metadata = self.catalog.stat()
        require(f'{metadata.st_dev}:{metadata.st_ino}' == self.catalog_identity and metadata.st_uid == os.getuid()
                and metadata.st_nlink == 1 and not metadata.st_mode & 0o077)
        connection = self.connection
        require(connection.in_transaction)
        try:
            request = self.request
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transfer_runtime_guards'").fetchone():
                require(connection.execute('SELECT 1 FROM transfer_runtime_guards WHERE runtime=?', (request['runtime'],)).fetchone() is None)
            row = connection.execute('SELECT * FROM environment_lifecycle WHERE operation=?', (request['operation'],)).fetchone()
            require(row and row['runtime'] == request['runtime'] and row['epoch'] == request['epoch']
                    and row['state'] in ('deleting', 'restoring', 'purging'))
            resources = json.loads(row['inventory'])
            require(request['resource'] in resources and request['action'] in
                    (('inspect', 'quarantine') if row['state'] == 'deleting' else
                     ('inspect', 'restore', 'readiness') if row['state'] == 'restoring' else ('inspect', 'inspect-quarantined', 'purge')))
            current = connection.execute('SELECT current_digest,current_identity FROM lifecycle_authorizations WHERE operation=?', (row['operation'],)).fetchone()
            require(current and re.fullmatch(r'[a-f0-9]{64}', current[0]))
            if self.original_digest is None:
                self.original_digest = current[0]
            require(current[0] == self.original_digest)
            root = native.PRIVATE / 'lifecycle-authorization'
            root_stat = root.lstat()
            require(root.resolve(strict=True) == root and stat.S_ISDIR(root_stat.st_mode) and not stat.S_ISLNK(root_stat.st_mode)
                    and root_stat.st_uid == os.getuid() and not root_stat.st_mode & 0o077)
            path = root / (row['operation'] + '.' + current[0] + '.json')
            private_stat = path.lstat()
            require(f'{private_stat.st_dev}:{private_stat.st_ino}' == current[1])
            value = private_json(path, current[1])
            digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            require(digest == current[0] and value['version'] == 1 and value['catalog'] == str(self.catalog)
                    and value['catalogIdentity'] == self.catalog_identity)
            keys = ('environment', 'runtime', 'operation', 'state', 'epoch', 'inventory', 'coverage', 'actor', 'management_epoch')
            require(value['binding'] == {key: row[key] for key in keys})
            before = realm()
            require(before == value['realm'])
            bearer = value['bearer']
            require(type(bearer) is str and len(bearer) <= 8192 and re.fullmatch(r'Bearer \S+', bearer, re.IGNORECASE))
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *_args, **_kwargs):
                    return None
            lookup = urllib.request.Request(before['auth'].rstrip('/') + '/user', headers={'authorization': bearer, 'apikey': before['key']})
            with urllib.request.build_opener(NoRedirect).open(lookup, timeout=5) as response:
                require(response.status == 200)
                raw = response.read(65537)
                require(len(raw) <= 65536)
                user = json.loads(raw)
            require(realm() == before)
            identity = value['identity']
            require(type(user) is dict and not user.get('is_anonymous') and user.get('id') == identity['actor'])
            segments = bearer[7:].split('.')
            require(len(segments) == 3)
            claims = json.loads(base64.urlsafe_b64decode(segments[1] + '=' * (-len(segments[1]) % 4)))
            require(claims['sub'] == identity['actor'] and claims['session_id'] == identity['session'] and claims['aal'] == 'aal2'
                    and claims['exp'] == identity['expiresAt'])
            def timestamp(method):
                now = int(time.time())
                return max([0] + [item['timestamp'] for item in claims['amr'] if type(item) is dict and item.get('method') == method
                                 and type(item.get('timestamp')) is int and 0 < item['timestamp'] <= now])
            require(timestamp('password') == identity['passwordAt'] and timestamp('totp') == identity['verifiedAt'] > 0)
            actor = identity['actor']
            grant = connection.execute('''SELECT g.factor,g.expires,g.epoch,COALESCE(e.epoch,0),COALESCE(e.revoked,0)
              FROM management_mfa_grant g LEFT JOIN management_mfa_epoch e ON e.actor=g.actor
              WHERE g.actor=? AND g.session=? AND g.verified=?''', (actor, identity['session'], identity['verifiedAt'])).fetchone()
            factors = {factor['id'] for factor in user.get('factors', []) if factor.get('status') == 'verified' and factor.get('factor_type') == 'totp'}
            original_factors = {factor['id'] for factor in identity['factors'] if factor['status'] == 'verified' and factor['factor_type'] == 'totp'}
            original_factor = value.get('factor')
            require(type(original_factor) is str and original_factor in original_factors and original_factor in factors)
            now = int(time.time())
            require(type(claims['exp']) is int and claims['exp'] > now and 0 < identity['passwordAt'] <= now
                    and now - identity['passwordAt'] < 43200 and grant and grant[0] == original_factor
                    and grant[1] > now and grant[2] == grant[3] == value['epoch'] and identity['verifiedAt'] > grant[4])
            require(connection.execute('''SELECT 1 FROM environments e JOIN projects p ON p.id=e.project
              JOIN memberships m ON m.organization=p.organization WHERE e.id=? AND m.actor=? AND m.role='owner' ''',
                                       (row['environment'], actor)).fetchone())
            if row['state'] == 'purging':
                require(connection.execute('''SELECT 1 FROM installation_bootstrap b JOIN memberships m ON m.organization=b.organization
                   WHERE m.actor=? AND m.role IN ('owner','admin')''', (actor,)).fetchone())
            current_row = connection.execute('SELECT * FROM environment_lifecycle WHERE operation=?', (row['operation'],)).fetchone()
            current_authorization = connection.execute('SELECT current_digest,current_identity FROM lifecycle_authorizations WHERE operation=?', (row['operation'],)).fetchone()
            require(current_row and all(current_row[key] == row[key] for key in keys) and tuple(current_authorization or ()) == tuple(current))
            fresh_grant = connection.execute('''SELECT g.factor,g.expires,g.epoch,COALESCE(e.epoch,0),COALESCE(e.revoked,0)
              FROM management_mfa_grant g LEFT JOIN management_mfa_epoch e ON e.actor=g.actor
              WHERE g.actor=? AND g.session=? AND g.verified=?''', (actor, identity['session'], identity['verifiedAt'])).fetchone()
            require(fresh_grant and tuple(fresh_grant) == tuple(grant))
            final_now = int(time.time())
            require(claims['exp'] > final_now and grant[1] > final_now and final_now - identity['passwordAt'] < 43200)
            final = self.catalog.stat()
            require(f'{final.st_dev}:{final.st_ino}' == self.catalog_identity)
        finally:
            require(connection.in_transaction)


def install(authority):
    require(isinstance(authority, NativeLifecycleAuthority))
    return _current.set(authority)


def reset(token):
    authority = _current.get()
    try:
        if authority is not None:
            authority.close()
    finally:
        _current.reset(token)


def require_current():
    authority = _current.get()
    if authority is not None:
        require(isinstance(authority, NativeLifecycleAuthority))
        authority.assert_current()
