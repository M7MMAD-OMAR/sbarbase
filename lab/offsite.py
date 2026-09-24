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
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import secrets
import struct
import sys
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
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def encrypted_size(plain):
    chunks = max(1, -(-plain // CHUNK))
    return len(MAGIC) + 16 + 8 + plain + chunks * TAG


class EncryptingReader(io.RawIOBase):
    """Reads a file as `SBB1 | salt | nonce prefix | chunks`, each chunk sealed with its index and
    whether it is the last, so a reordered, dropped or truncated chunk fails to open."""

    def __init__(self, path, key, salt):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.handle = open(path, 'rb')
        self.size = os.fstat(self.handle.fileno()).st_size
        self.aead = AESGCM(key)
        self.prefix = secrets.token_bytes(8)
        self.pending = MAGIC + salt + self.prefix
        self.index = 0
        self.remaining = self.size
        self.finished = False

    def readable(self):
        return True

    def _next(self):
        data = self.handle.read(CHUNK)
        self.remaining -= len(data)
        last = self.remaining == 0
        nonce = self.prefix + struct.pack('>I', self.index)
        self.pending += self.aead.encrypt(nonce, data, struct.pack('>IB', self.index, last))
        self.index += 1
        self.finished = last

    def readinto(self, buffer):
        while len(self.pending) < len(buffer) and not self.finished:
            self._next()
        count = min(len(buffer), len(self.pending))
        buffer[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count

    def close(self):
        self.handle.close()
        super().close()


def decrypt_stream(source, target, passphrase):
    """Decrypts a stream written by EncryptingReader into the file `target`."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    header = source.read(len(MAGIC) + 16 + 8)
    if len(header) != 28 or header[:4] != MAGIC:
        raise OffsiteError('Not an encrypted Sbarbase backup file')
    aead = AESGCM(derive_key(passphrase, header[4:20]))
    prefix, index = header[20:28], 0
    block = source.read(CHUNK + TAG)
    with open(target, 'wb') as handle:
        while True:
            following = source.read(CHUNK + TAG) if len(block) == CHUNK + TAG else b''
            last = not following
            try:
                handle.write(aead.decrypt(prefix + struct.pack('>I', index), block, struct.pack('>IB', index, last)))
            except InvalidTag:
                raise OffsiteError('The file does not decrypt: a wrong passphrase, or a damaged or truncated copy') from None
            if last:
                return
            block, index = following, index + 1


# ---- S3 ---------------------------------------------------------------------------------

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
    if target.exists():
        raise OffsiteError('That backup is already on this server')
    partial = backup.private_dir(backup.BACKUPS / e) / f'.{stamp}.fetching'
    backup.private_dir(partial)
    try:
        for file in FILES:
            response = bucket.request('GET', f"{config['prefix']}/{e}/{stamp}/{file}.sbb", stream=True)
            if isinstance(response, bytes):
                raise OffsiteError(f'{file} is missing from the copy')
            decrypt_stream(response, partial / file, config['passphrase'])
            os.chmod(partial / file, 0o600)
        partial.rename(target)
        return backup.verify(e, target)
    except BaseException:
        import shutil
        shutil.rmtree(partial, ignore_errors=True)
        if target.exists() and not (target / 'manifest.json').exists():
            shutil.rmtree(target, ignore_errors=True)
        raise


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
