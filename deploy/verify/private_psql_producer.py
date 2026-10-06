"""Selected V2 helper-side FIFO/protocol component; no producer success or launcher.

Callers must independently admit immutable runtime, whole-fixture guardian,
selector templates, READY lineage, diagnostics and publication before use.
No object or control frame here supplies native child/container authority.
"""
import os
import stat
from dataclasses import dataclass

NAMES = ('Q', 'R', 'E', 'C', 'S')
CLIENT = '/nix/var/nix/profiles/default/bin/psql'


class TransportRefusal(Exception):
    """Fixed public reason only; private bytes never enter exception text."""


def require(value, reason):
    if not value:
        raise TransportRefusal(reason)


def signature(info):
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid,
            info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)


def bind_ascii(value):
    """Encode one printable ASCII bind value; no unencoded metacharacter remains."""
    require(type(value) is bytes and 1 <= len(value) <= 512
            and all(32 <= byte <= 126 for byte in value), 'PRIVATE_VALUE_REFUSED')
    return b"'" + b''.join(bytes((92, 120)) + format(byte, '02x').encode('ascii')
                          for byte in value) + b"'"


@dataclass(frozen=True)
class Selector:
    """Source-admitted template supplied by a separately reviewed closed caller.

    This type does not admit SQL, role, client argv or connection provenance.
    prefix/suffix must be immutable public selected SELECT/bind/execute source.
    """
    identifier: int
    profile: int
    kind: str
    prefix: bytes
    suffix: bytes = b''
    parameter: bool = False

    def encode(self, value):
        require(type(self.identifier) is int and 0 <= self.identifier <= 999
                and type(self.profile) is int and self.profile in (1, 2)
                and self.kind in ('MUST_TRUE_INVARIANT', 'CLOSED_OBJECT_STATE')
                and type(self.prefix) is bytes and type(self.suffix) is bytes
                and type(self.parameter) is bool, 'SELECTOR_SHAPE_REFUSED')
        if self.parameter:
            payload = self.prefix + bind_ascii(value) + self.suffix
        else:
            require(value is None, 'UNEXPECTED_PRIVATE_PARAMETER')
            payload = self.prefix + self.suffix
        require(0 < len(payload) <= 8192, 'REQUEST_BYTES_REFUSED')
        return payload


class Namespace:
    """An already owned root fd; caller retains and closes that root descriptor."""

    def __init__(self, root_fd):
        self.root = root_fd
        self.failed = False
        self.uncertain_close_fd = None
        self.witness = None
        try:
            require(type(root_fd) is int and root_fd >= 0, 'ROOT_DESCRIPTOR_REFUSED')
            info = os.fstat(root_fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 100 and info.st_gid == 101
                    and stat.S_IMODE(info.st_mode) == 0o700, 'ADAPTER_ROOT_REFUSED')
            require(os.listdir(root_fd) == [], 'ADAPTER_NOT_FRESH')
            for name in NAMES:
                os.mkfifo(name, 0o600, dir_fd=root_fd)
            self.witness = self.snapshot()
        except BaseException:
            self.failed = True
            raise TransportRefusal('NAMESPACE_PREPARATION_REFUSED') from None

    def snapshot(self):
        require(not self.failed and set(os.listdir(self.root)) == set(NAMES), 'NAMESPACE_MEMBERSHIP_REFUSED')
        root = os.fstat(self.root)
        require(stat.S_ISDIR(root.st_mode) and root.st_uid == 100 and root.st_gid == 101
                and stat.S_IMODE(root.st_mode) == 0o700, 'ADAPTER_ROOT_REFUSED')
        result = {'': signature(root)}
        for name in NAMES:
            info = os.stat(name, dir_fd=self.root, follow_symlinks=False)
            require(stat.S_ISFIFO(info.st_mode) and info.st_uid == 100 and info.st_gid == 101
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'ADAPTER_FIFO_REFUSED')
            result[name] = signature(info)
        return result

    def renew(self):
        try:
            require(self.snapshot() == self.witness, 'NAMESPACE_CHANGED')
        except BaseException:
            self.failed = True
            raise TransportRefusal('NAMESPACE_RENEWAL_REFUSED') from None

    def open(self, name, flags):
        allowed = {'Q': (os.O_WRONLY,), 'R': (os.O_RDONLY, os.O_WRONLY),
                   'E': (os.O_RDONLY, os.O_WRONLY), 'C': (os.O_RDWR,),
                   'S': (os.O_RDONLY, os.O_WRONLY)}
        require(name in allowed and type(flags) is int and flags in allowed[name]
                and not self.failed, 'ENDPOINT_REFUSED')
        fd = os.open(name, flags | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.root)
        try:
            require(signature(os.fstat(fd)) == self.witness[name], 'ENDPOINT_IDENTITY_REFUSED')
            require(not os.get_inheritable(fd), 'ENDPOINT_INHERITANCE_REFUSED')
            return fd
        except BaseException:
            self.failed = True
            try:
                os.close(fd)
            except BaseException:
                # Evidence only: closure is uncertain; never retry or adopt this fd.
                self.uncertain_close_fd = fd
            raise TransportRefusal('ENDPOINT_ADMISSION_REFUSED') from None


class Control:
    """S wire state only; READY/DONE text grants no process or SQL authority."""

    def __init__(self):
        self.buffer = bytearray()
        self.ready = self.started = self.opened = self.done = False
        self.stopped = self.driver_exit = self.failed = False
        self.sequence = 0

    def fail(self):
        self.failed = True
        raise TransportRefusal('CONTROL_REFUSED')

    def begin(self, sequence):
        try:
            require(not self.failed and self.ready and not self.stopped and not self.buffer
                    and type(sequence) is int and sequence == self.sequence + 1 and 1 <= sequence <= 256
                    and (self.sequence == 0 or self.done), 'CONTROL_REQUEST_REFUSED')
            self.sequence = sequence
            self.started = self.opened = self.done = False
        except BaseException:
            self.failed = True
            raise TransportRefusal('CONTROL_REQUEST_REFUSED') from None

    def feed(self, block, *, stopping=False):
        try:
            require(type(block) is bytes and not self.failed, 'CONTROL_REFUSED')
            for byte in block:
                require(byte == 10 or 32 <= byte <= 126, 'CONTROL_ENCODING_REFUSED')
                require(len(self.buffer) < 128, 'CONTROL_FRAME_OVERFLOW')
                self.buffer.append(byte)
                if byte != 10:
                    continue
                frame = bytes(self.buffer)
                self.buffer.clear()
                if frame == b'READY V2\n':
                    require(not self.ready and self.sequence == 0 and not stopping, 'READY_ORDER_REFUSED')
                    self.ready = True
                elif frame == ('START %06d\n' % self.sequence).encode('ascii'):
                    require(self.sequence > 0 and not stopping and not self.started, 'START_ORDER_REFUSED')
                    self.started = True
                elif frame == ('OPENED %06d\n' % self.sequence).encode('ascii'):
                    require(not stopping and self.started and not self.opened and not self.done, 'OPENED_ORDER_REFUSED')
                    self.opened = True
                elif frame == ('DONE %06d 000\n' % self.sequence).encode('ascii'):
                    require(not stopping and self.opened and not self.done, 'DONE_ORDER_REFUSED')
                    self.done = True
                elif frame == b'STOPPED\n':
                    require(stopping and self.ready and (self.sequence == 0 or self.done)
                            and not self.stopped, 'STOP_ORDER_REFUSED')
                    self.stopped = True
                elif frame == b'DRIVER_EXIT 000\n':
                    require(stopping and self.stopped and not self.driver_exit, 'EXIT_ORDER_REFUSED')
                    self.driver_exit = True
                else:
                    self.fail()
        except BaseException:
            self.failed = True
            raise TransportRefusal('CONTROL_REFUSED') from None


def close_all(handles):
    """Attempt every owned close once, even when another close fails."""
    failed = False
    for name, fd in list(handles.items()):
        handles.pop(name)
        try:
            os.close(fd)
        except OSError:
            failed = True
    require(not failed, 'DESCRIPTOR_CLOSURE_REFUSED')



class TransferBudget:
    """Count actual transferred bytes once before retaining them, no EOF authority."""

    def __init__(self):
        self.failed = False
        self.sequence = 0
        self.run_bytes = self.lifecycle_bytes = 0
        self.channels = {}

    def begin(self, sequence):
        try:
            require(not self.failed and type(sequence) is int
                    and sequence == self.sequence + 1 and 1 <= sequence <= 256, 'BUDGET_SEQUENCE_REFUSED')
            self.sequence = sequence
            self.channels = {name: 0 for name in NAMES}
        except BaseException:
            self.failed = True
            raise TransportRefusal('TRANSFER_BUDGET_REFUSED') from None

    def record(self, channel, count, *, lifecycle=False):
        try:
            require(not self.failed and type(count) is int and count >= 0
                    and channel in NAMES and type(lifecycle) is bool, 'TRANSFER_COUNT_REFUSED')
            if lifecycle:
                require(channel in ('C', 'S'), 'PRIVATE_LIFECYCLE_REFUSED')
                self.lifecycle_bytes += count
                require(self.lifecycle_bytes <= 1024, 'LIFECYCLE_OVERFLOW')
            else:
                require(self.sequence > 0, 'TRANSFER_BEFORE_REQUEST')
                self.channels[channel] += count
                self.run_bytes += count
                require(self.channels[channel] <= {'Q': 8192, 'R': 16, 'E': 4096, 'C': 128, 'S': 512}[channel]
                        and sum(self.channels.values()) <= 16384 and self.run_bytes <= 262144,
                        'TRANSFER_OVERFLOW')
                require(channel != 'E' or count == 0, 'PRIVATE_STDERR_REFUSED')
        except BaseException:
            self.failed = True
            raise TransportRefusal('TRANSFER_BUDGET_REFUSED') from None
