"""FD-bound root key publication for exclusively owned Linux local fixtures.

The caller must separately admit the image and volume and exclude other writers.
The API opens an absolute parent path through anchored nofollow descriptors.
This primitive does not establish Vault continuity or production readiness.
"""
import argparse
import json
import os
import re
import secrets
import stat
import sys

NAME = 'pgsodium_root.key'
PENDING = '.pgsodium_root.key.pending-'


class RootKeyError(RuntimeError):
    """A fixed reason code, never private file contents or an OS exception."""


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_gid, value.st_nlink)


def metadata(value):
    return {'device': value.st_dev, 'inode': value.st_ino,
            'mode': stat.S_IMODE(value.st_mode), 'uid': value.st_uid,
            'gid': value.st_gid, 'nlink': value.st_nlink,
            'size': value.st_size, 'mtime_ns': value.st_mtime_ns,
            'ctime_ns': value.st_ctime_ns}


class RootKey:
    def __init__(self, directory):
        self.fds = []
        self.bindings = []
        self.fd = None
        if (type(directory) is not str or not directory.startswith('/')
                or len(directory) > 4096 or directory == '/'):
            raise RootKeyError('PARENT_PATH_INVALID')
        parts = directory[1:].split('/')
        if any(not part or part in ('.', '..') or '\x00' in part for part in parts):
            raise RootKeyError('PARENT_PATH_INVALID')
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            root = os.open('/', flags)
            self.fds.append(root)
            for part in parts:
                parent = self.fds[-1]
                child = os.open(part, flags, dir_fd=parent)
                self.fds.append(child)
                value = os.fstat(child)
                if identity(value) != identity(os.stat(part, dir_fd=parent, follow_symlinks=False)):
                    raise RootKeyError('PARENT_BINDING_CHANGED')
                self.bindings.append((parent, part, child, identity(value)))
            self.fd = self.fds[-1]
            value = os.fstat(self.fd)
            if (value.st_uid != os.geteuid() or value.st_gid != os.getegid()
                    or stat.S_IMODE(value.st_mode) not in (0o700, 0o750, 0o755)):
                raise RootKeyError('PARENT_POLICY_REFUSED')
            self.parent_identity = identity(value)
            self._parent()
        except (OSError, ValueError):
            self.close()
            raise RootKeyError('PARENT_UNAVAILABLE') from None
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def close(self):
        failed = False
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except OSError:
                failed = True
        self.fds = []
        self.fd = None
        if failed:
            raise RootKeyError('DESCRIPTOR_CLOSE_FAILED_RECONCILIATION_REQUIRED')

    def _parent(self):
        if self.fd is None:
            raise RootKeyError('PARENT_CLOSED')
        for parent, name, child, expected in self.bindings:
            if (identity(os.fstat(child)) != expected
                    or identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected):
                raise RootKeyError('PARENT_BINDING_CHANGED')
        if identity(os.fstat(self.fd)) != self.parent_identity:
            raise RootKeyError('PARENT_BINDING_CHANGED')

    def _pending_absent(self):
        entries = os.listdir(self.fd)
        if len(entries) > 512 or any(name.startswith(PENDING) for name in entries):
            raise RootKeyError('PENDING_RECONCILIATION_REQUIRED')

    def _read(self, fd, name):
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.geteuid() or before.st_gid != os.getegid()
                or before.st_nlink != 1 or before.st_size != 64):
            raise RootKeyError('KEY_METADATA_REFUSED')
        value = os.pread(fd, 65, 0)
        if re.fullmatch(b'[0-9a-f]{64}', value) is None:
            raise RootKeyError('KEY_FORMAT_REFUSED')
        after = os.fstat(fd)
        bound = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if (metadata(before) != metadata(after) or not stat.S_ISREG(bound.st_mode)
                or metadata(after) != metadata(bound)):
            raise RootKeyError('KEY_BINDING_CHANGED')
        self._parent()
        return metadata(after)

    def verify(self, expected=None):
        try:
            self._parent()
            self._pending_absent()
            fd = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self.fd)
            try:
                result = self._read(fd, NAME)
            finally:
                os.close(fd)
            if expected is not None:
                if (type(expected) is not dict or expected.keys() != result.keys()
                        or any(type(value) is not int for value in expected.values())
                        or expected != result):
                    raise RootKeyError('KEY_WITNESS_DIFFERS')
            return result
        except (OSError, ValueError):
            raise RootKeyError('KEY_UNAVAILABLE') from None

    def provision(self):
        self.publication_attempted = False
        try:
            return self._provision()
        except RootKeyError:
            if self.publication_attempted:
                raise RootKeyError('PROVISION_FAILED_RECONCILIATION_REQUIRED') from None
            raise
        except (OSError, ValueError):
            raise RootKeyError('PROVISION_FAILED_RECONCILIATION_REQUIRED') from None

    def _provision(self):
        fd = None
        temporary = None
        owned = None
        try:
            self._parent()
            self._pending_absent()
            try:
                os.stat(NAME, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise RootKeyError('EXISTING_KEY_REFUSED')
            temporary = PENDING + secrets.token_hex(16)
            fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
            owned = os.fstat(fd)
            value = secrets.token_bytes(32).hex().encode('ascii')
            offset = 0
            while offset < 64:
                written = os.write(fd, value[offset:])
                if type(written) is not int or not 1 <= written <= 64 - offset:
                    raise RootKeyError('KEY_WRITE_REFUSED')
                offset += written
            self._read(fd, temporary)
            os.fsync(fd)
            self._parent()
            self._read(fd, temporary)
            self.publication_attempted = True
            os.link(temporary, NAME, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                    follow_symlinks=False)
            os.fsync(self.fd)
            bound = os.stat(temporary, dir_fd=self.fd, follow_symlinks=False)
            if (bound.st_dev, bound.st_ino) != (owned.st_dev, owned.st_ino):
                raise RootKeyError('PENDING_BINDING_CHANGED')
            os.unlink(temporary, dir_fd=self.fd)
            temporary = None
            os.fsync(self.fd)
            return self.verify()
        except (OSError, ValueError):
            # Publication may have succeeded before a subsequent I/O failure.
            raise RootKeyError('PROVISION_FAILED_RECONCILIATION_REQUIRED') from None
        finally:
            try:
                if temporary is not None and owned is not None:
                    self._parent()
                    bound = os.stat(temporary, dir_fd=self.fd, follow_symlinks=False)
                    if (bound.st_dev, bound.st_ino) != (owned.st_dev, owned.st_ino):
                        raise RootKeyError('PENDING_BINDING_CHANGED')
                    os.unlink(temporary, dir_fd=self.fd)
                    os.fsync(self.fd)
            except (OSError, ValueError):
                raise RootKeyError('PENDING_RECONCILIATION_REQUIRED') from None
            finally:
                if fd is not None:
                    os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('provision', 'verify'))
    parser.add_argument('directory')
    args = parser.parse_args()
    try:
        with RootKey(args.directory) as key:
            value = key.provision() if args.operation == 'provision' else key.verify()
        print(json.dumps({'passed': True, 'metadata': value}, sort_keys=True))
        return 0
    except RootKeyError as error:
        print(json.dumps({'passed': False, 'reason': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
