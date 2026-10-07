"""Complete private SQLite material codec, without native effect authority.

The source driver must quiesce all writers and checkpoint SQLite before capture.
The target driver supplies a schema contract from the same pinned original
constructors, not from an operator's claimed schema hash. All bytes stay private.
"""
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import time


class Refused(RuntimeError):
    pass


def clock(deadline):
    if type(deadline) not in (int, float):
        raise Refused('Finite absolute SQLite material deadline required')
    try:
        finite = math.isfinite(deadline)
    except OverflowError:
        finite = False
    if not finite:
        raise Refused('Finite absolute SQLite material deadline required')
    if time.monotonic() >= deadline:
        raise Refused('SQLite material deadline expired')


def witness(fd):
    s = os.fstat(fd)
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
            s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


@dataclass(frozen=True)
class SQLiteMaterial:
    kind: str
    payload: bytes = field(repr=False)
    schema_sha256: str
    sha256: str


def decode(kind, payload, deadline, *, max_bytes):
    clock(deadline)
    if kind not in ('catalog', 'managed-keys') or type(payload) is not bytes:
        raise Refused('Unknown SQLite material')
    if type(max_bytes) is not int or not 0 < len(payload) <= max_bytes:
        raise Refused('SQLite material byte limit')
    # WAL databases require a separately checkpointed snapshot. Never silently
    # treat the main file as a complete source database while WAL is active.
    if len(payload) < 100 or payload[:16] != b'SQLite format 3\0' or payload[18:20] != b'\x01\x01':
        raise Refused('Checkpointed SQLite database required')
    db = sqlite3.connect(':memory:')
    try:
        db.deserialize(payload)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise Refused('SQLite integrity refused')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise Refused('SQLite foreign keys refused')
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = {row[1] for row in schema if row[0] == 'table'}
        required = ({'organizations','memberships','projects','environments','installation_bootstrap',
                     'management_mfa_epoch','management_mfa_grant','environment_lifecycle'}
                    if kind == 'catalog' else {'api_keys'})
        if not required <= tables or (kind == 'managed-keys' and tables != {'api_keys'}):
            raise Refused('SQLite material role differs')
        schema.append(('user_version', db.execute('PRAGMA user_version').fetchone()[0]))
        digest = hashlib.sha256(json.dumps(schema, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        payload_digest = hashlib.sha256(payload).hexdigest()
        clock(deadline)
        return SQLiteMaterial(kind, payload, digest, payload_digest)
    except sqlite3.Error as error:
        raise Refused('SQLite decoding refused') from error
    finally:
        db.close()


def capture(kind, fd, deadline, *, max_bytes):
    clock(deadline)
    owned = os.dup(fd)
    try:
        os.set_inheritable(owned, False)
        return _capture(kind, owned, deadline, max_bytes=max_bytes)
    finally:
        os.close(owned)


def _capture(kind, fd, deadline, *, max_bytes):
    clock(deadline)
    before = witness(fd)
    s = os.fstat(fd)
    if (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY or
            not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or
            stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1):
        raise Refused('Private read-only SQLite descriptor required')
    if type(max_bytes) is not int or not 0 < s.st_size <= max_bytes:
        raise Refused('SQLite material byte limit')
    parts = []
    offset = 0
    while offset < s.st_size:
        clock(deadline)
        part = os.pread(fd, min(65536, s.st_size-offset), offset)
        if not part:
            raise Refused('SQLite material truncated')
        parts.append(part)
        offset += len(part)
    if before != witness(fd) or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
        raise Refused('SQLite material changed')
    clock(deadline)
    return decode(kind, b''.join(parts), deadline, max_bytes=max_bytes)


def reconstruct(material, parent_fd, schema_template, deadline):
    """Write one exclusively owned prepared file, return its owned read-only FD.

    This prepares private bytes only. The caller retains native target admission
    and must not start Catalog/KeyStore until secret policies invalidate old
    management sessions and stale execution leases without reviving authority.
    """
    clock(deadline)
    owned = os.dup(parent_fd)
    try:
        os.set_inheritable(owned, False)
        return _reconstruct(material, owned, schema_template, deadline)
    finally:
        os.close(owned)


def _reconstruct(material, parent_fd, schema_template, deadline):
    clock(deadline)
    if type(material) is not SQLiteMaterial or type(schema_template) is not SQLiteMaterial:
        raise Refused('Decoded SQLite material required')
    checked = decode(material.kind, material.payload, deadline, max_bytes=len(material.payload))
    template = decode(schema_template.kind, schema_template.payload, deadline, max_bytes=len(schema_template.payload))
    if checked != material or template != schema_template or material.kind != schema_template.kind or material.schema_sha256 != schema_template.schema_sha256:
        raise Refused('Pinned original SQLite schema differs')
    parent = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise Refused('Owned private target directory required')
    name = {'catalog': 'control.sqlite', 'managed-keys': 'managed-keys.sqlite'}[material.kind]
    fd = None
    opened = None
    result = None
    try:
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
        opened = os.fstat(fd)
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(material.payload):
            clock(deadline)
            count = os.write(fd, material.payload[offset:offset+65536])
            if count <= 0:
                raise Refused('SQLite reconstruction write refused')
            offset += count
        os.fsync(fd)
        clock(deadline)
        result = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        rs = os.fstat(result)
        if (rs.st_dev,rs.st_ino) != (opened.st_dev,opened.st_ino):
            raise Refused('SQLite target ownership changed')
        reread = capture(material.kind, result, deadline, max_bytes=len(material.payload))
        if reread != material:
            raise Refused('SQLite reconstructed bytes differ')
        os.fsync(parent_fd)
        clock(deadline)
        returned, result = result, None
        return returned
    except BaseException:
        if result is not None:
            os.close(result)
        if opened is not None:
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev,current.st_ino) == (opened.st_dev,opened.st_ino):
                    os.unlink(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if fd is not None:
            os.close(fd)
