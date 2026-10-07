"""Authenticated streaming installation package, without native admission.

Collectors must supply already fenced, complete material. Packaging cannot prove
native contents or authorize publication, restoration, reopening or retention.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from recovery_inventory import (Refused, HASH, MAX_HEADER, canonical, integer,
                                recovery_set_digest, validate_manifest)
from recovery_receipt import artifact, strict_json, require_identity

MAGIC = b'SBRCV1\r\n'
CHUNK = 1 << 20
TAG = 16
FRAME = struct.Struct('>BI')
DOMAIN = b'sbarbase-installation-recovery-v1\x00'
FORMAT = 'sbarbase-recovery-stream-v1'
SALT = 32


def key_bytes(key):
    if type(key) is not bytes or len(key) != 32:
        raise Refused('Independent 32-byte recovery key required')


def manifest_header(manifest):
    # Catalog JSON.stringify identity includes resource property order. Preserve
    # it on the wire; sorted canonical JSON is only the recovery-set hash codec.
    raw = json.dumps(manifest, separators=(',', ':'), ensure_ascii=True,
                     allow_nan=False).encode('ascii')
    if len(raw) > MAX_HEADER:
        raise Refused('Recovery manifest exceeds header budget')
    return raw


def open_path(path, flags):
    """Open through directory FDs, refusing symlinks in every component."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise Refused('Absolute nontraversing recovery path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = following
        return os.open(path.name, flags | os.O_NOFOLLOW, dir_fd=fd)
    finally:
        os.close(fd)


def identity(fd):
    value = os.fstat(fd)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)


def private_regular(fd):
    value = os.fstat(fd)
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid()
            or value.st_mode & 0o077 or value.st_nlink != 1):
        raise Refused('Owned private regular recovery file required')


@contextmanager
def private_root(path):
    fd = open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        value = os.fstat(fd)
        if value.st_uid != os.getuid() or value.st_mode & 0o077:
            raise Refused('Owned private recovery directory required')
        yield fd
    finally:
        os.close(fd)


def package_size(manifest):
    plaintext = 4 + len(canonical(manifest)) + sum(m['bytes'] for m in manifest['materials'])
    frames = (plaintext + CHUNK - 1) // CHUNK
    if frames >= 1 << 32:
        raise Refused('Recovery package exhausts authenticated frame sequence')
    return len(MAGIC) + SALT + 8 + plaintext + (frames + 1) * (FRAME.size + TAG)


def package_cipher(key, salt, digest):
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                   info=DOMAIN + bytes.fromhex(digest)).derive(key)
    return AESGCM(derived)


class Encrypting:
    """Bounded AEAD frames followed by an authenticated terminal frame."""
    def __init__(self, handle, key, digest):
        self.handle = handle
        salt = secrets.token_bytes(SALT)
        self.cipher = package_cipher(key, salt, digest)
        self.prefix = secrets.token_bytes(8)
        self.aad = DOMAIN + MAGIC + bytes.fromhex(digest) + salt + self.prefix
        self.index = 0
        self.buffer = bytearray()
        self.output_hash = hashlib.sha256()
        self.output_bytes = 0
        self.output(MAGIC + salt + self.prefix)

    def output(self, data):
        self.handle.write(data)
        self.output_hash.update(data); self.output_bytes += len(data)

    def frame(self, data, final):
        if self.index >= 1 << 32:
            raise Refused('Recovery package exhausts authenticated frame sequence')
        counter = struct.pack('>I', self.index)
        payload = self.cipher.encrypt(self.prefix + counter, data, self.aad + counter + bytes([final]))
        self.output(FRAME.pack(final, len(payload)) + payload)
        self.index += 1

    def write(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= CHUNK:
            self.frame(bytes(self.buffer[:CHUNK]), 0)
            del self.buffer[:CHUNK]

    def finish(self):
        if self.buffer:
            self.frame(bytes(self.buffer), 0); self.buffer.clear()
        self.frame(b'', 1)
        return {'format': FORMAT, 'bytes': self.output_bytes, 'sha256': self.output_hash.hexdigest()}


def seal_recovery(manifest, members, key, destination):
    """Publish an exclusive ciphertext file, never an incomplete package."""
    try:
        key_bytes(key); validate_manifest(manifest)
        manifest = strict_json(manifest_header(manifest))
        digest = recovery_set_digest(manifest)
        planned_size = package_size(manifest)
        if type(members) is not dict or set(members) != {m['name'] for m in manifest['materials']}:
            raise Refused('Exact complete recovery material paths required')
        destination = Path(destination)
        with private_root(destination.parent) as parent:
            name = '.recovery-cipher-' + secrets.token_hex(16)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    stream = Encrypting(handle, key, digest)
                    header = manifest_header(manifest)
                    stream.write(struct.pack('>I', len(header))); stream.write(header)
                    for member in manifest['materials']:
                        source = open_path(members[member['name']], os.O_RDONLY | os.O_NONBLOCK)
                        try:
                            private_regular(source); before = identity(source)
                            if before[2] != member['bytes']:
                                raise Refused('Recovery member size differs from inventory')
                            actual = hashlib.sha256(); remaining = member['bytes']
                            while remaining:
                                block = os.read(source, min(CHUNK, remaining))
                                if not block:
                                    raise Refused('Recovery member changed during packaging')
                                remaining -= len(block); actual.update(block); stream.write(block)
                            if os.read(source, 1) or before != identity(source) or actual.hexdigest() != member['sha256']:
                                raise Refused('Recovery member changed or differs from inventory')
                        finally:
                            os.close(source)
                    result = stream.finish()
                    if result['bytes'] != planned_size:
                        raise Refused('Encrypted recovery size differs from complete inventory')
                    handle.flush(); os.fsync(handle.fileno())
                # Hard-link publication cannot replace an existing destination.
                os.link(name, destination.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
                return result
            finally:
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
    except Refused:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise Refused('Recovery package unavailable or publication refused') from None


def read_exact(handle, size):
    result = bytearray()
    while len(result) < size:
        block = handle.read(size - len(result))
        if not block:
            raise Refused('Authenticated recovery stream is truncated')
        result.extend(block)
    return bytes(result)


def decrypted_blocks(handle, key, digest):
    handle.seek(0)
    if read_exact(handle, len(MAGIC)) != MAGIC:
        raise Refused('Unknown authenticated recovery stream format')
    salt = read_exact(handle, SALT); prefix = read_exact(handle, 8)
    aad = DOMAIN + MAGIC + bytes.fromhex(digest) + salt + prefix
    cipher = package_cipher(key, salt, digest); index = 0
    while index < 1 << 32:
        final, size = FRAME.unpack(read_exact(handle, FRAME.size))
        if final not in (0, 1) or not TAG <= size <= CHUNK + TAG or (final and size != TAG) or (not final and size == TAG):
            raise Refused('Invalid authenticated recovery frame')
        payload = read_exact(handle, size); counter = struct.pack('>I', index)
        try:
            plaintext = cipher.decrypt(prefix + counter, payload, aad + counter + bytes([final]))
        except InvalidTag:
            raise Refused('Recovery stream authentication failed') from None
        if final:
            if handle.read(1):
                raise Refused('Recovery stream has trailing data')
            return
        yield plaintext
        index += 1
    raise Refused('Recovery stream has no authenticated terminal frame')


class PlainReader:
    def __init__(self, blocks):
        self.blocks = iter(blocks); self.buffer = bytearray()

    def read(self, count):
        while len(self.buffer) < count:
            block = next(self.blocks, None)
            if block is None:
                break
            self.buffer.extend(block)
        result = bytes(self.buffer[:count]); del self.buffer[:count]
        return result

    def exact(self, count):
        value = self.read(count)
        if len(value) != count:
            raise Refused('Recovery plaintext omits required material')
        return value


@dataclass(frozen=True)
class StagedRecovery:
    """Validated private bytes only; no native effect or lifecycle authority."""
    manifest: dict
    members: dict
    directory: Path


@contextmanager
def stage_recovery(source, encrypted_artifact, key, set_digest, workdir, *, max_bytes):
    """Authenticate all ciphertext before writing private plaintext staging.

    The unlinked owned ciphertext spool separates both passes from replacement of
    the caller's input path. Full-write same-UID actors remain outside this boundary.
    """
    try:
        key_bytes(key); artifact(encrypted_artifact)
        if not isinstance(set_digest, str) or not HASH.fullmatch(set_digest) or not integer(max_bytes, 1):
            raise Refused('Explicit recovery digest and transport byte budget required')
        if encrypted_artifact['bytes'] > max_bytes:
            raise Refused('Encrypted recovery exceeds admitted byte budget')
        with private_root(workdir) as root:
            temporary = '.recovery-spool-' + secrets.token_hex(16)
            writable = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
            spool = None
            try:
                with os.fdopen(writable, 'wb') as output:
                    source_fd = open_path(source, os.O_RDONLY | os.O_NONBLOCK)
                    try:
                        private_regular(source_fd); before = identity(source_fd)
                        if before[2] != encrypted_artifact['bytes']:
                            raise Refused('Encrypted recovery size differs from admitted artifact')
                        actual = hashlib.sha256(); remaining = encrypted_artifact['bytes']
                        while remaining:
                            block = os.read(source_fd, min(CHUNK, remaining))
                            if not block:
                                raise Refused('Encrypted recovery input changed during capture')
                            remaining -= len(block); actual.update(block); output.write(block)
                        if os.read(source_fd, 1) or before != identity(source_fd) or actual.hexdigest() != encrypted_artifact['sha256']:
                            raise Refused('Encrypted recovery input differs from admitted artifact')
                    finally:
                        os.close(source_fd)
                    output.flush(); os.fsync(output.fileno())
                spool = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
            finally:
                os.unlink(temporary, dir_fd=root)
            with os.fdopen(spool, 'rb') as handle:
                # Discard RAM-only plaintext from the complete authentication pass.
                for _ in decrypted_blocks(handle, key, set_digest):
                    pass
                reader = PlainReader(decrypted_blocks(handle, key, set_digest))
                length = struct.unpack('>I', reader.exact(4))[0]
                if not 0 < length <= MAX_HEADER:
                    raise Refused('Recovery manifest exceeds header budget')
                manifest = strict_json(reader.exact(length)); validate_manifest(manifest)
                if recovery_set_digest(manifest) != set_digest or package_size(manifest) != encrypted_artifact['bytes']:
                    raise Refused('Authenticated recovery inventory differs from admitted set')
                directory = '.recovery-plain-' + secrets.token_hex(16)
                os.mkdir(directory, 0o700, dir_fd=root)
                folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                created = []
                try:
                    paths = {}
                    for index, member in enumerate(manifest['materials']):
                        # Flat internal names avoid archive path and prefix collisions.
                        name = str(index) + '.material'
                        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                        file_stat = os.fstat(fd)
                        created.append((name, file_stat.st_dev, file_stat.st_ino))
                        with os.fdopen(fd, 'wb') as output:
                            actual = hashlib.sha256(); remaining = member['bytes']
                            while remaining:
                                block = reader.exact(min(CHUNK, remaining))
                                remaining -= len(block); actual.update(block); output.write(block)
                            if actual.hexdigest() != member['sha256']:
                                raise Refused('Authenticated member differs from recovery inventory')
                            output.flush(); os.fsync(output.fileno())
                        paths[member['name']] = Path(workdir) / directory / name
                    if reader.read(1):
                        raise Refused('Authenticated recovery contains unclassified plaintext')
                    os.fsync(folder)
                    yield StagedRecovery(manifest, paths, Path(workdir) / directory)
                finally:
                    try:
                        observed = os.stat(directory, dir_fd=root, follow_symlinks=False)
                        held = os.fstat(folder)
                        if ((observed.st_dev, observed.st_ino) != (held.st_dev, held.st_ino)
                                or set(os.listdir(folder)) != {name for name, _, _ in created}):
                            raise Refused('Recovery staging ownership changed; cleanup refused')
                        for name, device, inode in created:
                            observed = os.stat(name, dir_fd=folder, follow_symlinks=False)
                            if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.getuid()
                                    or observed.st_nlink != 1 or observed.st_mode & 0o077
                                    or (observed.st_dev, observed.st_ino) != (device, inode)):
                                raise Refused('Recovery member ownership changed; cleanup refused')
                        for name, _, _ in created:
                            os.unlink(name, dir_fd=folder)
                        os.rmdir(directory, dir_fd=root); os.fsync(root)
                    finally:
                        os.close(folder)
    except Refused:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise Refused('Recovery input unavailable or staging refused') from None
