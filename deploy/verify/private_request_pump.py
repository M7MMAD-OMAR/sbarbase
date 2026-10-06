"""Source proposal: finite H-side pump, never a launcher or native admission.

The caller must independently source-bind selector/runtime/lineage/collector
admission and publish genuine completion witnesses. These typed inputs are not
proofs by construction. No production caller, installed binary or SQL route is
admitted by this module. Public errors contain fixed reasons only.
"""
import errno
import math
import os
import selectors
import time
from dataclasses import dataclass

from private_psql_producer import Control, Namespace, Selector, TransferBudget, TransportRefusal


@dataclass(frozen=True)
class Handoff:
    """Explicit independently proved exclusive fresh namespace handoff.

    Never populated from a control frame. All six predicates require a future
    reviewed caller, source gate and actual native evidence before private use.
    """
    runtime_bound: bool
    namespace_exclusive: bool
    selector_manifest_bound: bool
    lineage_bound: bool
    collector_bound: bool
    outer_guardian_bound: bool


@dataclass(frozen=True)
class Completion:
    sequence: int
    selector: int
    profile: int
    monitor_status: int
    monitor_reaped: bool
    client_reaped: bool
    exec_only_lineage: bool
    diagnostics_quiet: bool


@dataclass(frozen=True)
class DriverCompletion:
    status: int
    driver_reaped: bool
    exact_lineage: bool
    diagnostics_quiet: bool


@dataclass(frozen=True)
class Outcome:
    sequence: int
    classification: str


class Mailbox:
    """A source-reviewed actor replaces immutable public evidence snapshots.

    This avoids an unbounded callback in the pump's deadline loop. Publication
    and evidence truth remain external native/source admission obligations.
    No private bytes, raw errors, child names or argv belong in this mailbox.
    """
    def __init__(self):
        self.completion = None
        self.driver = None


class Pump:
    """Only ready/request/stop are operational acceptance entry points.

    Their exception boundaries abort and latch all transport state. Low-level
    helpers are internal obligations, not independently latched public APIs.
    """
    def __init__(self, namespace, manifest, handoff, mailbox, whole_deadline, setup_started):
        self.failed = False
        self.closed = False
        self.uncertain_fds = []
        self.handles = {}
        self.request_handles = {}
        self.poller = None
        self.terminal_s_eof = False
        self.namespace = namespace
        self.budget = TransferBudget()
        self.control = Control()
        self.clock = time.monotonic
        try:
            self.require(type(namespace) is Namespace and not namespace.failed and namespace.witness is not None)
            self.require(type(handoff) is Handoff and all(type(v) is bool and v is True
                         for v in vars(handoff).values()))
            self.require(type(mailbox) is Mailbox and type(manifest) is tuple and 0 < len(manifest) <= 256)
            self.require(type(whole_deadline) is float and math.isfinite(whole_deadline)
                         and type(setup_started) is float and math.isfinite(setup_started)
                         and 0 < setup_started <= self.clock() < whole_deadline)
            self.whole = whole_deadline
            self.setup_deadline = min(setup_started + 2.0, whole_deadline)
            self.manifest = {}
            for item in manifest:
                self.require(type(item) is Selector and type(item.identifier) is int
                             and item.identifier not in self.manifest)
                self.require(type(item.profile) is int and item.profile in (1, 2)
                             and 0 <= item.identifier <= 999
                             and item.kind in ('MUST_TRUE_INVARIANT', 'CLOSED_OBJECT_STATE')
                             and type(item.prefix) is bytes and type(item.suffix) is bytes
                             and type(item.parameter) is bool)
                self.manifest[item.identifier] = item
            self.mailbox = mailbox
            self.namespace.renew()
            self.check(self.setup_deadline)
            self.handles['S'] = namespace.open('S', os.O_RDONLY)
            self.handles['S_boot'] = namespace.open('S', os.O_WRONLY)
            self.handles['C'] = namespace.open('C', os.O_RDWR)
            self.check(self.setup_deadline)
        except BaseException:
            self.abort()
            raise TransportRefusal('PUMP_HANDOFF_REFUSED') from None

    def require(self, value):
        if not value:
            raise TransportRefusal('PUMP_REFUSED')

    def check(self, deadline):
        self.require(not self.failed and not self.closed and self.clock() < min(deadline, self.whole))

    def close_one(self, handles, key):
        fd = handles.pop(key)
        try:
            os.close(fd)
        except BaseException:
            # Once removed, an uncertain descriptor is never retried or adopted.
            self.uncertain_fds.append(fd)
            self.failed = True
            raise TransportRefusal('PUMP_CLOSE_UNCERTAIN') from None

    def close_handles(self, handles):
        failed = False
        for key in list(handles):
            try:
                self.close_one(handles, key)
            except BaseException:
                failed = True
        self.require(not failed)

    def close_poller(self):
        poller, self.poller = self.poller, None
        if poller is not None:
            poller.close()

    def abort(self):
        self.failed = True
        if type(self.namespace) is Namespace:
            self.namespace.failed = True
        self.control.failed = True
        self.budget.failed = True
        try:
            self.close_poller()
        except BaseException:
            pass
        for handles in (self.request_handles, self.handles):
            try:
                self.close_handles(handles)
            except BaseException:
                pass
        # Namespace's uncertain admission fd is evidence only, not owned retry authority.
        uncertain = self.namespace.uncertain_close_fd if type(self.namespace) is Namespace else None
        if uncertain is not None and uncertain not in self.uncertain_fds:
            self.uncertain_fds.append(uncertain)

    def remaining(self, channel, lifecycle=False):
        if lifecycle:
            return min(128, max(0, 1024 - self.budget.lifecycle_bytes) + 1)
        caps = {'Q': 8192, 'R': 16, 'E': 4096, 'C': 128, 'S': 512}
        return min(1024, max(0, min(caps[channel] - self.budget.channels[channel],
                                   16384 - sum(self.budget.channels.values()),
                                   262144 - self.budget.run_bytes)) + 1)

    def control_read(self, *, lifecycle=False, stopping=False):
        if self.terminal_s_eof:
            self.require(stopping)
            return False
        try:
            block = os.read(self.handles['S'], self.remaining('S', lifecycle))
        except BlockingIOError:
            return False
        if block == b'':
            # Stream closure never sets frame, identity, reap or terminal proof.
            self.require(stopping and self.control.stopped and self.control.driver_exit
                         and not self.control.buffer)
            self.terminal_s_eof = True
            if self.poller is not None:
                # A closed FIFO stays readable. Remove it while awaiting proof.
                self.poller.unregister(self.handles['S'])
            return False
        self.budget.record('S', len(block), lifecycle=lifecycle)
        self.control.feed(block, stopping=stopping)
        return True

    def drain_control(self, deadline, *, lifecycle=False, stopping=False):
        # Consume already queued bytes before publication; never treat EAGAIN as EOF.
        while True:
            self.check(deadline)
            if not self.control_read(lifecycle=lifecycle, stopping=stopping):
                return

    def ready(self):
        try:
            self.check(self.setup_deadline)
            self.poller = selectors.DefaultSelector()
            self.poller.register(self.handles['S'], selectors.EVENT_READ)
            while not self.control.ready:
                self.check(self.setup_deadline)
                for _, _ in self.poller.select(min(0.02, self.setup_deadline - self.clock())):
                    self.control_read(lifecycle=True)
            self.drain_control(self.setup_deadline, lifecycle=True)
            self.require(not self.control.buffer)
            self.close_one(self.handles, 'S_boot')
            self.close_poller()
            self.namespace.renew()
            self.check(self.setup_deadline)
        except BaseException:
            self.abort()
            raise TransportRefusal('PUMP_READY_REFUSED') from None

    def publish_control(self, raw, deadline, *, lifecycle=False):
        self.check(deadline)
        self.require(type(raw) is bytes and 0 < len(raw) <= 128
                     and os.fpathconf(self.handles['C'], 'PC_PIPE_BUF') >= len(raw))
        count = os.write(self.handles['C'], raw)
        self.budget.record('C', count, lifecycle=lifecycle)
        self.require(count == len(raw))  # Atomic single frame, never retransmit a tail.
        self.check(deadline)

    def completion_ok(self, selected, sequence):
        proof = self.mailbox.completion
        if proof is None:
            return False
        self.require(type(proof) is Completion and type(proof.sequence) is int
                     and type(proof.selector) is int and type(proof.profile) is int)
        if proof.sequence < sequence:
            return False
        self.require(proof.sequence == sequence and proof.selector == selected.identifier
                     and proof.profile == selected.profile and type(proof.monitor_status) is int
                     and proof.monitor_status == 0)
        self.require(all(type(v) is bool and v is True for v in
                         (proof.monitor_reaped, proof.client_reaped, proof.exec_only_lineage, proof.diagnostics_quiet)))
        return True

    def request(self, identifier, value=None):
        origin = self.clock()  # Before namespace renewal, encoding or private endpoint opens.
        deadline = min(origin + 5.0, self.whole)
        scalar = bytearray()
        try:
            self.check(deadline)
            self.require(self.control.ready and not self.request_handles and not self.control.stopped
                         and type(identifier) is int and identifier in self.manifest)
            selected = self.manifest[identifier]
            sequence = self.control.sequence + 1
            self.drain_control(deadline, lifecycle=(self.control.sequence == 0))
            self.control.begin(sequence)
            self.budget.begin(sequence)
            self.namespace.renew()
            self.check(deadline)
            payload = selected.encode(value)
            self.check(deadline)
            for channel in ('R', 'E'):
                self.request_handles[channel] = self.namespace.open(channel, os.O_RDONLY)
                self.request_handles[channel + '_dummy'] = self.namespace.open(channel, os.O_WRONLY)
                self.check(deadline)
            self.poller = selectors.DefaultSelector()
            self.poller.register(self.handles['S'], selectors.EVENT_READ, 'S')
            for channel in ('R', 'E'):
                self.poller.register(self.request_handles[channel], selectors.EVENT_READ, channel)
            self.publish_control(('REQ %06d %03d %02d\n' % (sequence, identifier, selected.profile)).encode(), deadline)
            offset, attempts, rendezvous, next_attempt = 0, 0, None, None
            opened_applied = input_closed = False
            eof = set()
            while True:
                self.check(deadline)
                now = self.clock()
                if self.control.started and 'Q' not in self.request_handles and not input_closed:
                    if rendezvous is None:
                        rendezvous, next_attempt = min(now + 0.25, deadline), now
                    self.require(now < rendezvous)
                    if now >= next_attempt:
                        self.require(attempts < 5)
                        attempts += 1
                        try:
                            self.request_handles['Q'] = self.namespace.open('Q', os.O_WRONLY)
                        except OSError as error:
                            self.require(error.errno == errno.ENXIO)
                            next_attempt = now + 0.05
                        if 'Q' in self.request_handles:
                            self.check(deadline)
                if self.control.opened and not opened_applied:
                    self.require('Q' in self.request_handles)
                    self.close_one(self.request_handles, 'R_dummy')
                    self.close_one(self.request_handles, 'E_dummy')
                    self.poller.register(self.request_handles['Q'], selectors.EVENT_WRITE, 'Q')
                    opened_applied = True
                if input_closed and eof == {'R', 'E'} and self.control.done and not self.control.buffer:
                    self.drain_control(deadline)
                    if self.completion_ok(selected, sequence):
                        self.require(not self.control.buffer)
                        self.require(bytes(scalar) in (b't\n', b'f\n'))
                        self.require(selected.kind != 'MUST_TRUE_INVARIANT' or scalar == b't\n')
                        self.close_poller()
                        self.close_handles(self.request_handles)
                        self.namespace.renew()
                        outcome = Outcome(sequence, 'ABSENT' if scalar == b'f\n' else 'VERIFIED')
                        self.check(deadline)
                        return outcome
                wait = min(0.02, max(0.0, deadline - self.clock()))
                for key, _ in self.poller.select(wait):
                    self.check(deadline)
                    channel = key.data
                    if channel == 'S':
                        self.control_read()
                    elif channel == 'Q':
                        try:
                            count = os.write(key.fd, payload[offset:offset + 1024])
                        except BlockingIOError:
                            continue
                        self.require(type(count) is int and 0 < count <= min(1024, len(payload) - offset))
                        self.budget.record('Q', count)
                        offset += count
                        if offset == len(payload):
                            self.poller.unregister(key.fd)
                            self.close_one(self.request_handles, 'Q')
                            input_closed = True
                    else:
                        try:
                            block = os.read(key.fd, self.remaining(channel))
                        except BlockingIOError:
                            continue
                        if block == b'':
                            self.require(opened_applied)
                            self.poller.unregister(key.fd)
                            eof.add(channel)
                        else:
                            self.budget.record(channel, len(block))
                            if channel == 'R':
                                scalar.extend(block)
                self.check(deadline)
        except BaseException:
            self.abort()
            raise TransportRefusal('PUMP_REQUEST_FAILED_UNRECONCILED') from None
        finally:
            # Clearing references is not a memory erasure guarantee.
            scalar.clear()

    def stop(self):
        deadline = min(self.clock() + 2.0, self.whole)
        try:
            self.check(deadline)
            self.require(not self.request_handles and self.control.ready and not self.control.buffer
                         and (self.control.sequence == 0 or self.control.done))
            self.publish_control(b'STOP\n', deadline, lifecycle=True)
            self.poller = selectors.DefaultSelector()
            self.poller.register(self.handles['S'], selectors.EVENT_READ)
            while True:
                self.check(deadline)
                proof = self.mailbox.driver
                if self.control.stopped and self.control.driver_exit and not self.control.buffer and proof is not None:
                    self.drain_control(deadline, lifecycle=True, stopping=True)
                    self.require(not self.control.buffer)
                    self.require(type(proof) is DriverCompletion and type(proof.status) is int and proof.status == 0
                                 and all(type(v) is bool and v is True for v in
                                         (proof.driver_reaped, proof.exact_lineage, proof.diagnostics_quiet)))
                    self.close_poller()
                    self.close_handles(self.handles)
                    self.namespace.renew()
                    self.check(deadline)
                    self.closed = True
                    return 'CONTROLS_CLOSED_NATIVE_TERMINAL_UNPROVED'
                for _, _ in self.poller.select(min(0.02, max(0.0, deadline - self.clock()))):
                    self.control_read(lifecycle=True, stopping=True)
        except BaseException:
            self.abort()
            raise TransportRefusal('PUMP_STOP_FAILED_UNRECONCILED') from None
