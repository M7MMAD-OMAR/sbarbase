"""FD-bound PostgreSQL password publication for owned Linux startup volumes.

The caller must separately admit the image and volume and exclude other writers.
The API opens an absolute parent path through anchored nofollow descriptors.
This primitive does not establish Vault continuity or production readiness.
"""
import json
import os
import re
import secrets
import stat

NAME = 'password'
PENDING = '.password.pending-'


class MaterialError(RuntimeError):
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


class PasswordFile:
    def __init__(self, directory):
        self.fds = []
        self.bindings = []
        self.fd = None
        if (type(directory) is not str or not directory.startswith('/')
                or len(directory) > 4096 or directory == '/'):
            raise MaterialError('PARENT_PATH_INVALID')
        parts = directory[1:].split('/')
        if any(not part or part in ('.', '..') or '\x00' in part for part in parts):
            raise MaterialError('PARENT_PATH_INVALID')
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
                    raise MaterialError('PARENT_BINDING_CHANGED')
                self.bindings.append((parent, part, child, identity(value)))
            self.fd = self.fds[-1]
            value = os.fstat(self.fd)
            if (value.st_uid != os.geteuid() or value.st_gid != os.getegid()
                    or stat.S_IMODE(value.st_mode) != 0o700):
                raise MaterialError('PARENT_POLICY_REFUSED')
            self.parent_identity = identity(value)
            self._parent()
        except (OSError, ValueError):
            self.close()
            raise MaterialError('PARENT_UNAVAILABLE') from None
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
            raise MaterialError('DESCRIPTOR_CLOSE_FAILED_RECONCILIATION_REQUIRED')

    def _parent(self):
        if self.fd is None:
            raise MaterialError('PARENT_CLOSED')
        for parent, name, child, expected in self.bindings:
            if (identity(os.fstat(child)) != expected
                    or identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected):
                raise MaterialError('PARENT_BINDING_CHANGED')
        if identity(os.fstat(self.fd)) != self.parent_identity:
            raise MaterialError('PARENT_BINDING_CHANGED')

    def _pending_absent(self):
        entries = os.listdir(self.fd)
        if len(entries) > 512 or any(name.startswith(PENDING) for name in entries):
            raise MaterialError('PENDING_RECONCILIATION_REQUIRED')

    def _read(self, fd, name):
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.geteuid() or before.st_gid != os.getegid()
                or before.st_nlink != 1 or before.st_size != 64):
            raise MaterialError('PASSWORD_METADATA_REFUSED')
        value = os.pread(fd, 65, 0)
        if re.fullmatch(b'[0-9a-f]{64}', value) is None:
            raise MaterialError('PASSWORD_FORMAT_REFUSED')
        after = os.fstat(fd)
        bound = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if (metadata(before) != metadata(after) or not stat.S_ISREG(bound.st_mode)
                or metadata(after) != metadata(bound)):
            raise MaterialError('PASSWORD_BINDING_CHANGED')
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
                    raise MaterialError('PASSWORD_WITNESS_DIFFERS')
            return result
        except (OSError, ValueError):
            raise MaterialError('PASSWORD_UNAVAILABLE') from None

    def provision(self):
        self.publication_attempted = False
        try:
            return self._provision()
        except MaterialError:
            if self.publication_attempted:
                raise MaterialError('PROVISION_FAILED_RECONCILIATION_REQUIRED') from None
            raise
        except (OSError, ValueError):
            raise MaterialError('PROVISION_FAILED_RECONCILIATION_REQUIRED') from None

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
                raise MaterialError('EXISTING_PASSWORD_REFUSED')
            temporary = PENDING + secrets.token_hex(16)
            fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
            owned = os.fstat(fd)
            value = secrets.token_bytes(32).hex().encode('ascii')
            offset = 0
            while offset < 64:
                written = os.write(fd, value[offset:])
                if type(written) is not int or not 1 <= written <= 64 - offset:
                    raise MaterialError('PASSWORD_WRITE_REFUSED')
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
                raise MaterialError('PENDING_BINDING_CHANGED')
            os.unlink(temporary, dir_fd=self.fd)
            temporary = None
            os.fsync(self.fd)
            return self.verify()
        except (OSError, ValueError):
            # Publication may have succeeded before a subsequent I/O failure.
            raise MaterialError('PROVISION_FAILED_RECONCILIATION_REQUIRED') from None
        finally:
            try:
                if temporary is not None and owned is not None:
                    self._parent()
                    bound = os.stat(temporary, dir_fd=self.fd, follow_symlinks=False)
                    if (bound.st_dev, bound.st_ino) != (owned.st_dev, owned.st_ino):
                        raise MaterialError('PENDING_BINDING_CHANGED')
                    os.unlink(temporary, dir_fd=self.fd)
                    os.fsync(self.fd)
            except (OSError, ValueError):
                raise MaterialError('PENDING_RECONCILIATION_REQUIRED') from None
            finally:
                if fd is not None:
                    os.close(fd)



def material_frame(kind, operation, directory, expected=None):
    """Complete private work and descriptor closure before returning public data."""
    from native_root_key import RootKey, RootKeyError
    if kind not in ('key', 'password') or operation not in ('provision', 'verify'):
        raise MaterialError('OPERATION_REFUSED')
    if operation == 'provision' and expected is not None:
        raise MaterialError('PROVISION_WITNESS_REFUSED')
    factory = RootKey if kind == 'key' else PasswordFile
    try:
        with factory(directory) as material:
            witness = material.provision() if operation == 'provision' else material.verify(expected)
    except RootKeyError as error:
        raise MaterialError(str(error)) from None
    if (type(witness) is not dict or len(witness) != 9
            or any(type(value) is not int or not 0 <= value < 2**64 for value in witness.values())):
        raise MaterialError('PUBLIC_WITNESS_REFUSED')
    return {'status': 'MATERIAL_VERIFIED', 'kind': kind, 'operation': operation, 'metadata': witness}


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('key', 'password'))
    parser.add_argument('operation', choices=('provision', 'verify'))
    parser.add_argument('directory')
    parser.add_argument('--expected')
    args = parser.parse_args(argv)
    try:
        if args.expected is not None and len(args.expected) > 2048:
            raise MaterialError('PUBLIC_WITNESS_REFUSED')
        expected = None if args.expected is None else json.loads(args.expected)
        frame = material_frame(args.kind, args.operation, args.directory, expected)
        print(json.dumps(frame, separators=(',', ':'), sort_keys=True))
        return 0
    except (MaterialError, ValueError, TypeError, OSError):
        # Unknown OS errors and private file contents never reach a public frame.
        print('{"status":"MATERIAL_REFUSED"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    import signal
    import time
    started = time.monotonic()
    def expired(*_):
        raise MaterialError('WORKER_DEADLINE_REFUSED')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 15)
    try:
        code = main()
        raise SystemExit(code if time.monotonic() < started + 15 else 1)
    except MaterialError:
        raise SystemExit(1) from None
