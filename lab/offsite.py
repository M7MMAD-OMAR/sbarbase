"""Encrypted copies of the backups in S3-compatible storage off this server.

    offsite.py configure           read settings from stdin as JSON, never from arguments
    offsite.py push [<environment>|all]
    offsite.py list [<environment>]
    offsite.py fetch <environment> <backup>

Works with any S3-compatible storage: Cloudflare R2, Backblaze B2, AWS S3, MinIO, Wasabi.
`configure` takes {"endpoint", "bucket", "region"?, "prefix"?, "access_key_id",
"secret_access_key", "passphrase", "keep"?}, writes them to `.secrets/offsite.json`
(mode 600) and proves them by writing, reading and deleting a test object.

Every file is encrypted here before it leaves the server (AES-256-GCM in 4 MiB chunks, the
key derived from the passphrase with scrypt), so the storage provider never sees the data.
Keep the passphrase somewhere other than this server: without it the copies cannot be read.
The daily backup pushes new backups by itself once this is configured, and keeps the newest
`keep` (30 by default) per environment in the bucket. `fetch` brings one back, checks it
against its manifest, and leaves it where `backup.py restore` finds it.
"""
import argparse
import datetime
import ctypes
import errno
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import secrets
import shutil
import stat
import struct
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import backup

CONFIG = backup.ROOT / '.secrets' / 'offsite.json'
RECORD = backup.STATE / 'offsite.json'
MAGIC = b'SBB1'
CHUNK = 4 * 1024 * 1024
TAG = 16
FILES = ('database.dump', 'objects.tar', 'manifest.json')
DEFAULT_KEEP = 30
PREFIX = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}(/[A-Za-z0-9][A-Za-z0-9_.-]{0,62}){0,4}')


class OffsiteError(RuntimeError):
    pass


# ---- encryption -------------------------------------------------------------------------

def derive_key(passphrase, salt):
    if type(salt) is not bytes or len(salt) != 16:
        raise OffsiteError('Key derivation requires a 16-byte salt')
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def _size(value, name):
    if type(value) is not int or value < 0 or value > CHUNK * 2**32:
        raise OffsiteError(f'{name} must be a nonnegative integer within the SBB1 size ceiling')
    return value


def encrypted_size(plain):
    plain = _size(plain, 'Plaintext size')
    chunks = max(1, -(-plain // CHUNK))
    return len(MAGIC) + 16 + 8 + plain + chunks * TAG


class EncryptingReader(io.RawIOBase):
    """Reads SBB1 with at most one sealed chunk pending, including for huge requests."""

    def __init__(self, path, key, salt):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if type(key) is not bytes or len(key) != 32 or type(salt) is not bytes or len(salt) != 16:
            raise OffsiteError('Encryption requires a 32-byte key and 16-byte salt')
        self.handle = open(path, 'rb')
        try:
            self.snapshot = os.fstat(self.handle.fileno())
            if not stat.S_ISREG(self.snapshot.st_mode):
                raise OffsiteError('Encryption source must be a regular file')
            self.size = _size(self.snapshot.st_size, 'Plaintext size')
            self.aead = AESGCM(key)
            self.prefix = secrets.token_bytes(8)
            self.pending = MAGIC + salt + self.prefix
            self.index = 0
            self.remaining = self.size
            self.finished = False
            self.failed = False
        except BaseException:
            self.handle.close()
            raise

    def readable(self):
        return True

    def _next(self):
        if self.failed:
            raise OffsiteError('Encryption source previously failed admission')
        try:
            if self.index >= 2**32:
                raise OffsiteError('SBB1 chunk index exhausted')
            wanted = min(CHUNK, self.remaining)
            data = _read_block(self.handle, wanted)
            if len(data) != wanted:
                raise OffsiteError('Encryption source shrank while reading')
            self.remaining -= len(data)
            last = self.remaining == 0
            if last:
                extra = self.handle.read(1)
                current = os.fstat(self.handle.fileno())
                if extra or any(getattr(current, field) != getattr(self.snapshot, field)
                                            for field in ('st_size', 'st_mtime_ns', 'st_ctime_ns')):
                    raise OffsiteError('Encryption source changed while reading')
            nonce = self.prefix + struct.pack('>I', self.index)
            self.pending = self.aead.encrypt(nonce, data, struct.pack('>IB', self.index, last))
            self.index += 1
            self.finished = last
        except BaseException:
            self.failed = True
            raise

    def readinto(self, buffer):
        if self.closed:
            raise ValueError('read of closed encryption source')
        if not len(buffer):
            return 0
        if not self.pending and not self.finished:
            self._next()
        count = min(len(buffer), len(self.pending))
        buffer[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count

    def close(self):
        if hasattr(self, 'handle'):
            self.handle.close()
        super().close()


def _read_block(source, limit):
    """Accumulate legal short reads, never beyond one requested bounded block."""
    result = bytearray()
    while len(result) < limit:
        part = source.read(limit - len(result))
        if not isinstance(part, bytes) or len(part) > limit - len(result):
            raise OffsiteError('Ciphertext source returned an invalid read')
        if not part:
            break
        result.extend(part)
    return bytes(result)


def _destination(target):
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise OffsiteError('Decryption destination must be absent or a regular file')


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _cleanup(path):
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise OffsiteError(f'Private staging cleanup failed at {path}: {error}') from error


def decrypt_stream(source, target, passphrase, *, expected_bytes=None, expected_sha256=None, max_bytes=None):
    """Authenticate into private staging and publish only complete, validated plaintext.

    Returns a plaintext bytes/sha256 receipt. Limits are explicitly supplied by callers.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    for name, value in (('Expected bytes', expected_bytes), ('Maximum bytes', max_bytes)):
        if value is not None:
            _size(value, name)
    if expected_sha256 is not None and (type(expected_sha256) is not str
                                       or re.fullmatch(r'[0-9a-f]{64}', expected_sha256) is None):
        raise OffsiteError('Expected SHA-256 must be 64 lowercase hexadecimal characters')
    if expected_bytes is not None and max_bytes is not None and expected_bytes > max_bytes:
        raise OffsiteError('Expected bytes exceed maximum bytes')
    target = Path(target)
    _destination(target)
    header = _read_block(source, 28)
    if len(header) != 28 or header[:4] != MAGIC:
        raise OffsiteError('Not an encrypted Sbarbase backup file')
    aead = AESGCM(derive_key(passphrase, header[4:20]))
    prefix, index = header[20:28], 0
    block = _read_block(source, CHUNK + TAG)
    staging = Path(tempfile.mkdtemp(prefix=f'.{target.name}.decrypt-', dir=target.parent))
    temporary = staging / 'plaintext'
    total, digest = 0, hashlib.sha256()
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            while True:
                if index >= 2**32 or len(block) < TAG:
                    raise OffsiteError('Invalid or exhausted SBB1 ciphertext chunk')
                following = _read_block(source, CHUNK + TAG) if len(block) == CHUNK + TAG else b''
                last = not following
                try:
                    data = aead.decrypt(prefix + struct.pack('>I', index), block, struct.pack('>IB', index, last))
                except InvalidTag:
                    raise OffsiteError('The file does not decrypt: a wrong passphrase, or a damaged or truncated copy') from None
                total += len(data)
                if any(limit is not None and total > limit for limit in (expected_bytes, max_bytes)):
                    raise OffsiteError('Decrypted plaintext exceeds the admitted byte limit')
                handle.write(data)
                digest.update(data)
                if last:
                    break
                block, index = following, index + 1
            actual_hash = digest.hexdigest()
            if expected_bytes is not None and total != expected_bytes:
                raise OffsiteError('Decrypted plaintext does not match expected bytes')
            if expected_sha256 is not None and not hmac.compare_digest(actual_hash, expected_sha256):
                raise OffsiteError('Decrypted plaintext does not match expected SHA-256')
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(staging)
        _destination(target)
        os.replace(temporary, target)
        _sync_directory(target.parent)
        return {'bytes': total, 'sha256': actual_hash}
    finally:
        _cleanup(staging)


# ---- request signing --------------------------------------------------------------------


def signed_headers(method, url, region, key_id, secret, headers=None, payload='UNSIGNED-PAYLOAD', now=None):
    """AWS Signature Version 4 for one request; returns the headers to send."""
    parsed = urllib.parse.urlsplit(url)
    now = now or datetime.datetime.now(datetime.UTC)
    stamp, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    headers = {name.lower(): str(value).strip() for name, value in (headers or {}).items()}
    headers.update({'host': parsed.netloc, 'x-amz-date': stamp, 'x-amz-content-sha256': payload})
    query = '&'.join(f'{urllib.parse.quote(k, safe="-_.~")}={urllib.parse.quote(v, safe="-_.~")}'
                     for k, v in sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
    names = sorted(headers)
    canonical = '\n'.join([method, urllib.parse.quote(parsed.path or '/', safe='/-_.~'), query,
                           ''.join(f'{name}:{headers[name]}\n' for name in names), ';'.join(names), payload])
    scope = f'{day}/{region}/s3/aws4_request'
    text = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ('AWS4' + secret).encode()
    for part in (day, region, 's3', 'aws4_request'):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    headers['authorization'] = f'AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={";".join(names)}, Signature={signature}'
    return headers


class Bucket:
    def __init__(self, config):
        self.config = config
        endpoint = urllib.parse.urlsplit(config['endpoint'])
        self.secure = endpoint.scheme == 'https'
        self.host = endpoint.netloc
        self.base = f"{endpoint.scheme}://{endpoint.netloc}/{config['bucket']}"

    def request(self, method, key='', query='', body=None, length=None, stream=False):
        url = f"{self.base}/{key}" + (f'?{query}' if query else '')
        extra = {'content-length': str(length if length is not None else len(body or b''))} if method == 'PUT' else {}
        headers = signed_headers(method, url, self.config.get('region') or 'auto', self.config['access_key_id'],
                                 self.config['secret_access_key'], extra)
        connection = (http.client.HTTPSConnection if self.secure else http.client.HTTPConnection)(self.host, timeout=120)
        path = urllib.parse.urlsplit(url)
        connection.request(method, path.path + (f'?{path.query}' if path.query else ''), body=body, headers=headers)
        response = connection.getresponse()
        if stream and response.status == 200:
            return response
        data = response.read()
        connection.close()
        if response.status >= 300 and not (method == 'DELETE' and response.status == 404):
            code = re.search(rb'<Code>([^<]+)</Code>', data)
            raise OffsiteError(f'{method} {key or "bucket"} answered {response.status} {code.group(1).decode() if code else ""}'.strip())
        return data

    def put_file(self, key, path, passphrase):
        salt = secrets.token_bytes(16)
        reader = EncryptingReader(path, derive_key(passphrase, salt), salt)
        try:
            self.request('PUT', key, body=io.BufferedReader(reader, CHUNK), length=encrypted_size(reader.size))
        finally:
            reader.close()

    def put_bytes(self, key, data):
        self.request('PUT', key, body=data)

    def keys(self, prefix):
        found, token = [], ''
        while True:
            query = urllib.parse.urlencode({'list-type': '2', 'prefix': prefix, **({'continuation-token': token} if token else {})})
            root = ElementTree.fromstring(self.request('GET', query=query))
            space = root.tag.split('}')[0] + '}' if root.tag.startswith('{') else ''
            found += [item.findtext(f'{space}Key') for item in root.findall(f'{space}Contents')]
            if root.findtext(f'{space}IsTruncated') != 'true':
                return found
            token = root.findtext(f'{space}NextContinuationToken') or ''


# ---- commands -----------------------------------------------------------------------------

def load_config():
    try:
        config = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return None
    return config if isinstance(config, dict) and config.get('bucket') else None


def validate(config):
    required = ('endpoint', 'bucket', 'access_key_id', 'secret_access_key', 'passphrase')
    if not isinstance(config, dict) or any(not isinstance(config.get(name), str) or not config[name] for name in required):
        raise OffsiteError('Give endpoint, bucket, access_key_id, secret_access_key and passphrase')
    endpoint = urllib.parse.urlsplit(config['endpoint'])
    if endpoint.scheme not in ('https', 'http') or not endpoint.netloc or endpoint.path not in ('', '/') or endpoint.query:
        raise OffsiteError('The endpoint is a bare https:// address, such as https://<account>.r2.cloudflarestorage.com')
    if endpoint.scheme == 'http' and endpoint.hostname not in ('127.0.0.1', 'localhost'):
        raise OffsiteError('Use https for storage that is not on this server')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', config['bucket']):
        raise OffsiteError('The bucket name is not valid')
    if len(config['passphrase']) < 12:
        raise OffsiteError('Use a passphrase of at least 12 characters')
    prefix = config.get('prefix', 'sbarbase')
    if not isinstance(prefix, str) or not PREFIX.fullmatch(prefix):
        raise OffsiteError('The prefix is letters, digits, dots, dashes and slashes')
    keep = config.get('keep', DEFAULT_KEEP)
    if type(keep) is not int or not 1 <= keep <= 1000:
        raise OffsiteError('keep is a whole number from 1 to 1000')
    region = config.get('region', 'auto')
    if not isinstance(region, str) or not re.fullmatch(r'[a-z0-9-]{1,32}', region):
        raise OffsiteError('The region is not valid')
    return {**{name: config[name] for name in required}, 'endpoint': config['endpoint'].rstrip('/'), 'prefix': prefix,
            'keep': keep, 'region': region}


def configure(stream=sys.stdin):
    try:
        config = validate(json.loads(stream.read()))
    except ValueError:
        raise OffsiteError('Send the settings as JSON on stdin') from None
    bucket = Bucket(config)
    probe = f"{config['prefix']}/.sbarbase-probe-{secrets.token_hex(4)}"
    bucket.put_bytes(probe, b'sbarbase')
    if bucket.request('GET', probe) != b'sbarbase':
        raise OffsiteError('The storage did not return what was written')
    bucket.request('DELETE', probe)
    backup.private_dir(CONFIG.parent)
    backup.write_private(CONFIG, json.dumps(config) + '\n')
    return config


def uploaded():
    try:
        return set(json.loads(RECORD.read_text()).get('uploaded', []))
    except (OSError, ValueError):
        return set()


def remember(names):
    backup.write_private(RECORD, json.dumps({'uploaded': sorted(names)}) + '\n')


def remote_backups(bucket, config, e):
    """Complete backups in the bucket for one environment, oldest first."""
    stamps = {}
    for key in bucket.keys(f"{config['prefix']}/{e}/"):
        parts = key.split('/')
        if len(parts) >= 2 and backup.STAMP.fullmatch(parts[-2]):
            stamps.setdefault(parts[-2], set()).add(parts[-1])
    return sorted(stamp for stamp, names in stamps.items() if 'complete' in names)


def push(targets, config=None):
    config = config or load_config()
    if not config:
        raise OffsiteError('Off-site copies are not configured; run offsite.py configure')
    bucket, done = Bucket(config), uploaded()
    copied = []
    for e in targets:
        for path in backup.complete_backups(e):
            name = f'{e}/{path.name}'
            if name in done:
                continue
            backup.verify(e, path)
            base = f"{config['prefix']}/{e}/{path.name}"
            for file in FILES:
                bucket.put_file(f'{base}/{file}.sbb', path / file, config['passphrase'])
            # Written last: a copy without it is an interrupted upload and is never fetched.
            bucket.put_bytes(f'{base}/complete', b'')
            done.add(name)
            remember(done)
            copied.append(name)
        for stamp in remote_backups(bucket, config, e)[:-config['keep']]:
            for key in bucket.keys(f"{config['prefix']}/{e}/{stamp}/"):
                bucket.request('DELETE', key)
    return copied


def _promote_directory(source, target):
    """Linux atomic directory publication with an absent destination, fail closed elsewhere."""
    if not sys.platform.startswith('linux'):
        raise OffsiteError('Atomic absent-destination promotion requires Linux renameat2')
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        raise OffsiteError('Atomic absent-destination promotion requires renameat2 support')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise OffsiteError('That backup is already on this server')
        raise OffsiteError(f'Atomic backup promotion failed: {os.strerror(code)}')


def fetch(e, stamp, config=None):
    config = config or load_config()
    if not config:
        raise OffsiteError('Off-site copies are not configured; run offsite.py configure')
    if not backup.STAMP.fullmatch(stamp):
        raise OffsiteError('A backup name is its UTC time, such as 20260924T030000Z')
    bucket = Bucket(config)
    if stamp not in remote_backups(bucket, config, e):
        raise OffsiteError('No complete copy of that backup in the bucket')
    target = backup.BACKUPS / e / stamp
    if os.path.lexists(target):
        raise OffsiteError('That backup is already on this server')
    parent = backup.private_dir(backup.BACKUPS / e)
    partial = parent / f'.{stamp}.fetching-{secrets.token_hex(16)}'
    partial.mkdir(mode=0o700)
    promoted = False
    try:
        for file in FILES:
            response = bucket.request('GET', f"{config['prefix']}/{e}/{stamp}/{file}.sbb", stream=True)
            if isinstance(response, bytes):
                raise OffsiteError(f'{file} is missing from the copy')
            try:
                decrypt_stream(response, partial / file, config['passphrase'])
            finally:
                response.close()
        manifest = backup.verify(e, partial, staged_stamp=stamp)
        if type(manifest.get('created_at')) is not str or manifest['created_at'] != stamp:
            raise OffsiteError('Backup creation time does not match the requested backup name')
        _sync_directory(partial)
        _promote_directory(partial, target)
        promoted = True
        _sync_directory(parent)
        return manifest
    finally:
        if not promoted:
            _cleanup(partial)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Encrypted off-site copies of the backups')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('configure')
    pushing = sub.add_parser('push')
    pushing.add_argument('environment', nargs='?', default='all')
    listing = sub.add_parser('list')
    listing.add_argument('environment', nargs='?')
    fetching = sub.add_parser('fetch')
    fetching.add_argument('environment')
    fetching.add_argument('backup')
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            config = configure()
            print(f"off-site copies go to {config['bucket']}/{config['prefix']} and keep {config['keep']} per environment")
            print('Keep the passphrase somewhere other than this server: the copies cannot be read without it.')
            return 0
        config = load_config()
        if not config:
            raise OffsiteError('Off-site copies are not configured; run offsite.py configure')
        if args.command == 'list':
            bucket = Bucket(config)
            for e in ([backup.resolve(args.environment)] if args.environment else backup.environments()):
                for stamp in remote_backups(bucket, config, e):
                    print(f'{e}  {stamp}')
            return 0
        if args.command == 'push':
            targets = backup.environments() if args.environment == 'all' else [backup.resolve(args.environment)]
            copied = push(targets, config)
            print(f'copied {len(copied)} backup(s) off the server' + (': ' + ', '.join(copied) if copied else ''))
            return 0
        e = backup.resolve(args.environment)
        manifest = fetch(e, args.backup, config)
        print(f"fetched {e} {args.backup}: database {manifest['database']['bytes']} B, {manifest['objects']['files']} file(s); "
              f'restore it with: backup.py restore {e} {args.backup}')
        return 0
    except (OffsiteError, backup.BackupError, OSError, http.client.HTTPException) as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
