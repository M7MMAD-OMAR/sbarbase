"""The installation manifest of each daily run, and encrypted copies of the run off this host.

The installation manifest records what a new host needs besides the environment backups: the
pinned images, the routing, the catalog's organizations, projects, environments, memberships and
jobs, the operator settings, and the names of the secret files. It is built from allow-lists, so
a column or a key that is not named here never reaches it, and it holds no secret value.

An off-host copy is one object per daily run: a tar of that run's environment backups and its
installation manifest, encrypted with AES-256-GCM under a key the operator keeps in a private
file, and uploaded to one S3-compatible bucket. The client signs requests with AWS Signature
Version 4 using the Python standard library only, so a host needs no extra tool and the tests run
the same code against a loopback server. A failed copy never changes or removes a local backup.

    {"schema": 1,
     "s3": {"endpoint": "https://s3.eu-central-1.amazonaws.com", "region": "eu-central-1",
            "bucket": "example-backups", "prefix": "sbarbase/",
            "credentialsFile": ".secrets/upstream/offsite-s3.json"},
     "keyFile": ".secrets/upstream/offsite-key.json"}

lives in ``.lab/upstream/backup-offsite.json``. Relative paths are resolved against the checkout.
"""
import contextlib
import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tarfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.parse import quote, urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import backup
from backup import BackupError

SECRETS = backup.ROOT / '.secrets'
INSTALLATION = 'installation'
PINS = ('distro-image.lock.json', 'images.lock.json', 'storage-image.lock.json', 'studio-image.lock.json')
SETTINGS = ('SBARBASE_PUBLIC_URL', 'SBARBASE_CONSOLE_PORT', 'SBARBASE_BACKUP_HOUR', 'SBARBASE_BACKUP_KEEP')
# Catalog columns copied into the manifest. Claims and receipt tokens are left out on purpose.
CATALOG_COLUMNS = {
    'organizations': ('id', 'name'),
    'projects': ('id', 'organization', 'name'),
    'environments': ('id', 'project', 'name'),
    'memberships': ('organization', 'actor', 'role'),
    'provision_jobs': ('environment', 'runtime', 'organization', 'state', 'attempt'),
    'runtime_routing': ('runtime', 'revision', 'maintenance', 'placement'),
    'auth_settings': ('runtime', 'revision', 'applied', 'state'),
}
ENVIRONMENT_FILES = ('database.dump', 'objects.tar', 'manifest.json')
INSTALLATION_FILES = ('installation.json', 'manifest.json')
MAGIC = b'SBBKUP1\n'
NONCE = 12
TAG = 16
SUFFIX = '.sbb'
MEMBER = re.compile(r'(e_[a-f0-9]{24}|installation)/(\d{8}T\d{6}Z)(?:/([a-z]+\.[a-z]+))?')
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


# ---------------------------------------------------------------- installation manifest

def origin(url):
    """Scheme, host and port of a URL only: a path or user part can itself be a credential."""
    parts = urlsplit(str(url))
    if not parts.scheme or not parts.hostname:
        return ''
    try:
        port = f':{parts.port}' if parts.port else ''
    except ValueError:
        port = ''
    return f'{parts.scheme}://{parts.hostname}{port}'


def read_json(path):
    """A settings file as JSON, or 'invalid': a broken file must not fail the daily backup."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return 'invalid'
    return value if isinstance(value, dict) else 'invalid'


def catalog_rows(path):
    if not path.is_file():
        return {}
    rows = {}
    with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as database:
        for table, wanted in CATALOG_COLUMNS.items():
            present = {row[1] for row in database.execute(f'PRAGMA table_info({table})')}
            columns = [column for column in wanted if column in present]
            if not columns:
                continue
            cursor = database.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY 1')
            rows[table] = [dict(zip(columns, row)) for row in cursor]
    return rows


def routing(endpoints):
    result = {}
    for runtime, item in sorted(endpoints.items()):
        if not backup.RUNTIME.fullmatch(runtime) or not isinstance(item, dict):
            continue
        storage = item.get('storage') if isinstance(item.get('storage'), dict) else {}
        result[runtime] = {'auth': origin(item.get('auth')), 'rest': origin(item.get('rest')),
                           'storage': {'url': origin(storage.get('url')), 'tenantHost': str(storage.get('tenantHost', ''))}}
    return result


def notification_settings(path):
    if not path.is_file() or path.is_symlink():
        return None
    config = read_json(path)
    if config == 'invalid':
        return config
    email, webhook, telegram = (config.get(name) or {} for name in ('email', 'webhook', 'telegram'))
    return {
        'email': {field: email.get(field) for field in ('enabled', 'host', 'port', 'from', 'to', 'tls')},
        'webhook': {'enabled': webhook.get('enabled'), 'origin': origin(webhook.get('url', '')),
                    'secretFile': webhook.get('secretFile')},
        'telegram': {'enabled': telegram.get('enabled'), 'chatId': telegram.get('chatId'),
                     'tokenFile': telegram.get('tokenFile')},
    }


def secret_names():
    """Names of the private files, never their contents."""
    if not SECRETS.is_dir():
        return []
    return sorted(str(path.relative_to(SECRETS)) for path in SECRETS.rglob('*') if path.is_file())


def installation_manifest(stamp, runtimes, environ=os.environ):
    state = backup.STATE
    endpoints = backup.published()
    offsite = config_path()
    offsite_settings = None
    if offsite.is_file() and not offsite.is_symlink():
        raw = read_json(offsite)
        s3 = raw.get('s3') if raw != 'invalid' and isinstance(raw.get('s3'), dict) else {}
        offsite_settings = 'invalid' if raw == 'invalid' else {
            's3': {'endpoint': origin(s3.get('endpoint', '')),
                   **{field: s3.get(field) for field in ('region', 'bucket', 'prefix', 'credentialsFile')}},
            'keyFile': raw.get('keyFile')}
    return {
        'version': 1, 'kind': 'installation', 'created_at': stamp, 'environments': sorted(runtimes),
        'pins': {name: json.loads((backup.ROOT / 'lab' / name).read_text())
                 for name in PINS if (backup.ROOT / 'lab' / name).is_file()},
        'routing': routing(endpoints),
        'catalog': catalog_rows(state / 'control.sqlite'),
        'settings': {name: environ[name] for name in SETTINGS if name in environ},
        'notifications': notification_settings(state / 'notifications.json'),
        'offsite': offsite_settings,
        'secrets': {'directory': '.secrets', 'files': secret_names()},
    }


def write_installation(stamp, runtimes, keep=backup.DEFAULT_KEEP, environ=os.environ, reason=None, protected=None):
    """Write ``installation/<stamp>/`` beside the environment backups; manifest.json last. A run
    taken for an upgrade is marked like its environment backups, so pruning keeps it too."""
    if reason is not None and reason not in backup.REASONS:
        raise BackupError('Unknown backup reason')
    target = backup.private_dir(backup.BACKUPS / INSTALLATION) / stamp
    if target.exists():
        raise BackupError('An installation manifest with this time already exists')
    backup.private_dir(target)
    body = json.dumps(installation_manifest(stamp, runtimes, environ), indent=2, sort_keys=True) + '\n'
    backup.write_private(target / 'installation.json', body)
    record = {'version': 1, 'kind': 'installation', 'created_at': stamp,
              'installation': {'file': 'installation.json', 'bytes': (target / 'installation.json').stat().st_size,
                               'sha256': backup.digest(target / 'installation.json')}}
    if reason is not None:
        record['reason'] = reason
    backup.write_private(target / 'manifest.json', json.dumps(record, indent=2) + '\n')
    backup.prune(INSTALLATION, keep, protected)
    return target


def verify_installation(path):
    manifest = json.loads((path / 'manifest.json').read_text())
    item = manifest.get('installation') or {}
    file = path / 'installation.json'
    if manifest.get('kind') != 'installation' or item.get('file') != 'installation.json' or not file.is_file() \
            or file.stat().st_size != item.get('bytes') or backup.digest(file) != item.get('sha256'):
        raise BackupError('Installation manifest does not match its digest')
    return manifest


# ---------------------------------------------------------------- configuration and key

def config_path():
    return backup.STATE / 'backup-offsite.json'


def resolve_path(value):
    path = Path(str(value))
    return path if path.is_absolute() else backup.ROOT / path


def private_json(path, what):
    """A regular file, not a symlink, readable by its owner only."""
    if path.is_symlink() or not path.is_file():
        raise BackupError(f'{what} {path} is unavailable')
    if path.stat().st_mode & 0o077:
        raise BackupError(f'{what} {path} is not private (chmod 600)')
    try:
        return json.loads(path.read_text())
    except ValueError:
        raise BackupError(f'{what} {path} is not valid JSON') from None


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_config(path=None):
    """The validated configuration, or None when no off-host copy is configured."""
    path = Path(path) if path else config_path()
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise BackupError('Off-host configuration must be a regular file')
    try:
        config = json.loads(path.read_text())
    except ValueError:
        raise BackupError('Off-host configuration is not valid JSON') from None
    s3 = config.get('s3') if isinstance(config, dict) else None
    if config.get('schema') != 1 or not isinstance(s3, dict) or 'keyFile' not in config:
        raise BackupError('Off-host configuration needs schema 1, an s3 target and a keyFile')
    endpoint = urlsplit(str(s3.get('endpoint', '')))
    if endpoint.scheme not in ('https', 'http') or not endpoint.hostname or endpoint.username \
            or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment:
        raise BackupError('Off-host endpoint must be a plain https:// origin')
    if endpoint.scheme == 'http' and not loopback(endpoint.hostname):
        raise BackupError('Off-host endpoint must use https')
    prefix = str(s3.get('prefix', 'sbarbase/'))
    if not re.fullmatch(r'[A-Za-z0-9._/-]{0,200}', prefix) or '..' in prefix or prefix.startswith('/'):
        raise BackupError('Off-host prefix may hold letters, digits, dot, dash, underscore and slash')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', str(s3.get('bucket', ''))):
        raise BackupError('Off-host bucket name is invalid')
    if not re.fullmatch(r'[a-z0-9-]{1,32}', str(s3.get('region', ''))):
        raise BackupError('Off-host region is invalid')
    if 'credentialsFile' not in s3:
        raise BackupError('Off-host configuration needs s3.credentialsFile')
    return {'endpoint': f'{endpoint.scheme}://{endpoint.netloc}', 'scheme': endpoint.scheme,
            'netloc': endpoint.netloc, 'host': endpoint.hostname, 'port': endpoint.port,
            'region': s3['region'], 'bucket': s3['bucket'], 'prefix': prefix,
            'credentialsFile': resolve_path(s3['credentialsFile']), 'keyFile': resolve_path(config['keyFile'])}


def load_key(path):
    value = private_json(Path(path), 'Off-host key file')
    key = str(value.get('key', '')) if isinstance(value, dict) else ''
    if value.get('schema') != 1 or not re.fullmatch(r'[0-9a-f]{64}', key):
        raise BackupError(f'Off-host key file {path} must hold {{"schema": 1, "key": "<64 hex>"}}')
    return bytes.fromhex(key)


def new_key(path):
    """Write a new key file on the operator's explicit request; never over an existing file.

    A relative path is resolved against the checkout, as ``keyFile`` in the configuration is.
    """
    path = resolve_path(path)
    if path.exists() or path.is_symlink():
        raise BackupError(f'{path} already exists; a key is never overwritten')
    if not path.parent.is_dir():
        raise BackupError(f'The directory of {path} does not exist')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(json.dumps({'schema': 1, 'key': secrets.token_hex(32)}) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    return path


# ---------------------------------------------------------------- encryption

def aad(name):
    """The set name is authenticated, so a set renamed on the target is refused."""
    return MAGIC + name.encode()


class Encrypting:
    """A write-only file object: tar writes plaintext, the handle receives ciphertext."""

    def __init__(self, handle, key, name):
        nonce = os.urandom(NONCE)
        self.encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        self.encryptor.authenticate_additional_data(aad(name))
        self.handle = handle
        handle.write(MAGIC + nonce)

    def write(self, data):
        self.handle.write(self.encryptor.update(bytes(data)))
        return len(data)

    def finish(self):
        self.handle.write(self.encryptor.finalize() + self.encryptor.tag)


def decrypted(path, key, name):
    """Plaintext blocks of an encrypted set. The last step raises unless the tag verifies."""
    size = path.stat().st_size
    if size < len(MAGIC) + NONCE + TAG:
        raise BackupError('Off-host set is truncated')
    with path.open('rb') as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise BackupError('Off-host set has an unknown format')
        nonce = handle.read(NONCE)
        handle.seek(size - TAG)
        tag = handle.read(TAG)
        handle.seek(len(MAGIC) + NONCE)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(aad(name))
        remaining = size - len(MAGIC) - NONCE - TAG
        while remaining:
            block = handle.read(min(1 << 20, remaining))
            if not block:
                raise BackupError('Off-host set is truncated')
            remaining -= len(block)
            yield decryptor.update(block)
        try:
            yield decryptor.finalize()
        except InvalidTag:
            raise BackupError('Off-host set failed authentication: wrong key, or it was changed') from None


class Reader:
    """A read-only file object over decrypted blocks, for a streaming tar."""

    def __init__(self, blocks):
        self.blocks = blocks
        self.buffer = b''
        self.position = 0

    def read(self, size=-1):
        parts = []
        while size != 0:
            if self.position >= len(self.buffer):
                try:
                    self.buffer, self.position = next(self.blocks), 0
                except StopIteration:
                    break
                continue
            take = len(self.buffer) - self.position if size < 0 else min(size, len(self.buffer) - self.position)
            parts.append(self.buffer[self.position:self.position + take])
            self.position += take
            if size > 0:
                size -= take
        return b''.join(parts)

    def drain(self):
        for _ in self.blocks:
            pass


def pack(stamp, runtimes, key, handle):
    """Tar one run's backups straight into the cipher; no plaintext copy is written."""
    writer = Encrypting(handle, key, stamp)
    folders = [(e, ENVIRONMENT_FILES) for e in sorted(runtimes)]
    if (backup.BACKUPS / INSTALLATION / stamp / 'manifest.json').is_file():
        folders.append((INSTALLATION, INSTALLATION_FILES))
    with tarfile.open(fileobj=writer, mode='w|', format=tarfile.PAX_FORMAT) as archive:
        for name, files in folders:
            folder = backup.BACKUPS / name / stamp
            archive.add(folder, f'{name}/{stamp}', recursive=False)
            for file in files:
                archive.add(folder / file, f'{name}/{stamp}/{file}', recursive=False)
    writer.finish()
    return [name for name, _ in folders]


def unpack(path, key, stamp, staging):
    """Authenticate the whole set first, then extract allow-listed entries into ``staging``."""
    for _ in decrypted(path, key, stamp):
        pass
    reader = Reader(decrypted(path, key, stamp))
    with tarfile.open(fileobj=reader, mode='r|') as archive:
        for member in archive:
            match = MEMBER.fullmatch(member.name)
            allowed = match and match[2] == stamp and (
                member.isdir() if match[3] is None else member.isfile() and match[3] in (
                    INSTALLATION_FILES if match[1] == INSTALLATION else ENVIRONMENT_FILES))
            if not allowed:
                raise BackupError('Off-host set holds an unexpected entry')
            archive.extract(member, staging, filter='data')
    reader.drain()


# ---------------------------------------------------------------- S3 with Signature Version 4

def canonical_query(query):
    return '&'.join(f'{quote(k, safe="-_.~")}={quote(v, safe="-_.~")}' for k, v in sorted(query))


def sign(method, path, query, headers, payload_hash, access_key, secret_key, region, amz_date):
    """Headers of one request with its AWS Signature Version 4 authorization."""
    signed = {name.lower(): str(value).strip() for name, value in headers.items()}
    signed['x-amz-date'] = amz_date
    signed['x-amz-content-sha256'] = payload_hash
    names = sorted(signed)
    request = '\n'.join([method, quote(path, safe='/-_.~'), canonical_query(query),
                         ''.join(f'{name}:{signed[name]}\n' for name in names), ';'.join(names), payload_hash])
    scope = f'{amz_date[:8]}/{region}/s3/aws4_request'
    text = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(request.encode()).hexdigest()])
    key = ('AWS4' + secret_key).encode()
    for part in (amz_date[:8], region, 's3', 'aws4_request'):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    signed['authorization'] = (f'AWS4-HMAC-SHA256 Credential={access_key}/{scope}, '
                               f'SignedHeaders={";".join(names)}, Signature={signature}')
    return signed


class S3:
    TIMEOUT = 300

    def __init__(self, config):
        self.config = config
        credentials = private_json(config['credentialsFile'], 'Off-host credentials file')
        if not isinstance(credentials, dict) or credentials.get('schema') != 1 \
                or not credentials.get('accessKeyId') or not credentials.get('secretAccessKey'):
            raise BackupError(f"Off-host credentials file {config['credentialsFile']} must hold schema, "
                              'accessKeyId and secretAccessKey')
        self.access_key = str(credentials['accessKeyId'])
        self.secret_key = str(credentials['secretAccessKey'])

    def request(self, method, key='', query=(), body=None, length=0, payload_hash=EMPTY_SHA256, out=None,
                expect=(200,)):
        config = self.config
        path = f"/{config['bucket']}" + (f'/{key}' if key else '')
        amz_date = datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')
        headers = {'host': config['netloc']}
        if method == 'PUT':
            headers['content-length'] = str(length)
        headers = sign(method, path, query, headers, payload_hash, self.access_key, self.secret_key,
                       config['region'], amz_date)
        connection_class = http.client.HTTPSConnection if config['scheme'] == 'https' else http.client.HTTPConnection
        connection = connection_class(config['host'], config['port'], timeout=self.TIMEOUT)
        try:
            target = quote(path, safe='/-_.~') + (f'?{canonical_query(query)}' if query else '')
            connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            if body is not None:
                while block := body.read(1 << 20):
                    connection.send(block)
            response = connection.getresponse()
            if response.status not in expect:
                response.read(1 << 16)
                raise BackupError(f'Off-host {method} returned HTTP {response.status}')
            if out is None:
                return response.read(1 << 24)
            while block := response.read(1 << 20):
                out.write(block)
            return b''
        except (OSError, http.client.HTTPException) as error:
            raise BackupError(f'Off-host {method} failed: {type(error).__name__}') from None
        finally:
            connection.close()

    def put(self, key, path):
        with path.open('rb') as handle:
            self.request('PUT', key, body=handle, length=path.stat().st_size, payload_hash=backup.digest(path))

    def get(self, key, path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            self.request('GET', key, out=handle)

    def delete(self, key):
        self.request('DELETE', key, expect=(200, 204))

    def list(self, prefix):
        keys, token = [], None
        while True:
            query = [('list-type', '2'), ('prefix', prefix)] + ([('continuation-token', token)] if token else [])
            try:
                root = ElementTree.fromstring(self.request('GET', query=query))
            except ElementTree.ParseError:
                raise BackupError('Off-host listing is not valid XML') from None
            values = {}
            for element in root.iter():
                tag = element.tag.rsplit('}', 1)[-1]
                if tag == 'Key':
                    keys.append(element.text or '')
                elif tag in ('IsTruncated', 'NextContinuationToken'):
                    values[tag] = element.text or ''
            if values.get('IsTruncated') != 'true' or not values.get('NextContinuationToken'):
                return keys
            token = values['NextContinuationToken']


def set_names(client, prefix):
    """Set names (UTC times) on the target, oldest first. Other objects are never listed as sets."""
    pattern = re.compile(re.escape(prefix) + r'(\d{8}T\d{6}Z)' + re.escape(SUFFIX))
    return sorted(match[1] for key in client.list(prefix) if (match := pattern.fullmatch(key)))


def prune_remote(client, prefix, keep, newest):
    """Keep the newest ``keep`` sets, and only once the set just uploaded is listed."""
    if keep < 1:
        raise BackupError('Keep at least one backup')
    names = set_names(client, prefix)
    if newest not in names:
        raise BackupError('The uploaded set is not listed on the target; nothing was pruned')
    doomed = names[:-keep]
    for name in doomed:
        client.delete(f'{prefix}{name}{SUFFIX}')
    return doomed


# ---------------------------------------------------------------- the daily step and the fetch

def upload(stamp, runtimes, keep, config):
    key = load_key(config['keyFile'])
    client = S3(config)
    work = backup.BACKUPS / f'.offsite-{stamp}{SUFFIX}'
    try:
        fd = os.open(work, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            members = pack(stamp, runtimes, key, handle)
        client.put(f"{config['prefix']}{stamp}{SUFFIX}", work)
    finally:
        work.unlink(missing_ok=True)
    pruned = prune_remote(client, config['prefix'], keep, stamp)
    return members, pruned


def after_run(stamp, runtimes, keep, catalog=None):
    """Copy a finished run off this host when configured. Never raises and never touches local backups.

    A failure prints one line and raises ``backup.failed`` through the operator channels. The
    return value is True (copied), False (failed) or None (not configured).
    """
    try:
        config = load_config()
        if config is None:
            return None
        if not runtimes:
            # A run without one environment backup would count toward the target's retention.
            # A failed local backup is already reported by the run's own exit code.
            print(f'off-host copy {stamp} skipped: no environment was backed up in this run')
            return None
        members, pruned = upload(stamp, runtimes, keep, config)
        print(f'off-host copy {stamp}: {len(members)} backup(s) encrypted and uploaded, '
              f'{len(pruned)} older set(s) removed from the target')
        return True
    except Exception as error:
        reason = str(error) if isinstance(error, BackupError) else type(error).__name__
        print(f'off-host copy {stamp} failed: {reason}; the local backups are unchanged', file=sys.stderr)
        import notification_producers
        notification_producers.emit('backup.failed', 'critical', 'backup.offsite_failed|installation', {},
                                    'system:backup', 'export_failed', {'failed': len(runtimes)}, catalog=catalog)
        return False


def list_remote():
    config = load_config()
    if config is None:
        raise BackupError('No off-host copy is configured')
    return set_names(S3(config), config['prefix'])


def fetch(stamp):
    """Download one set, authenticate it, and put each backup it holds in place, verified.

    An environment that already has a local backup with this time is left as it is.
    Returns the names placed.
    """
    if not backup.STAMP.fullmatch(stamp):
        raise BackupError('Name a set by its time, such as 20260924T030000Z')
    config = load_config()
    if config is None:
        raise BackupError('No off-host copy is configured')
    key = load_key(config['keyFile'])
    backup.private_dir(backup.BACKUPS)
    download = backup.BACKUPS / f'.fetch-{stamp}{SUFFIX}'
    staging = backup.BACKUPS / f'.fetch-{stamp}'
    if download.exists() or staging.exists():
        raise BackupError(f'A fetch of {stamp} was interrupted; remove {download.name} and {staging.name} first')
    placed = []
    try:
        S3(config).get(f"{config['prefix']}{stamp}{SUFFIX}", download)
        backup.private_dir(staging)
        unpack(download, key, stamp, staging)
        for folder in sorted(staging.iterdir()):
            source = folder / stamp
            target = backup.private_dir(backup.BACKUPS / folder.name) / stamp
            if target.exists():
                continue
            os.replace(source, target)
            try:
                if folder.name == INSTALLATION:
                    verify_installation(target)
                else:
                    backup.verify(folder.name, target)
            except Exception:
                shutil.rmtree(target)
                raise
            placed.append(folder.name)
    finally:
        download.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return placed
