"""Unexecuted proposed public FIFO regressions, no launcher or private input.

Exact temporary-object metadata mapping is inherited from the accepted component
test fixture. It is a model of admitted UID/GID, never native ownership evidence.
The actor below is a bounded public protocol model, never Bash or a reaping proof.
"""
import errno
import os
from pathlib import Path
import select
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy' / 'verify'))

import private_psql_producer as transport
import private_request_pump as subject
import test_private_psql_producer as component_fixture


class PublicPumpTests(unittest.TestCase):
    setUp = component_fixture.NamespaceTests.setUp

    def make(self, kind='CLOSED_OBJECT_STATE', payload=b'SELECT true;\n'):
        origin = time.monotonic()
        namespace = transport.Namespace(self.root)
        mailbox = subject.Mailbox()
        selector = transport.Selector(7, 1, kind, payload)
        pump = subject.Pump(namespace, (selector,), subject.Handoff(*([True] * 6)),
                            mailbox, float(origin + 10), float(origin))
        self.addCleanup(pump.abort)
        return pump, mailbox

    def actor(self, pump, mailbox, *, result=b't\n', stderr=b'', done_first=False,
              eof_delay=0, done=b'DONE 000001 000\n', proof=True, stop=False,
              close_final_writer=False, stop_frames=b'STOPPED\nDRIVER_EXIT 000\n',
              driver_proof=True, driver_delay=0, driver_status=0):
        errors, transferred = [], bytearray()
        finished = threading.Event()

        def run():
            owned = []
            try:
                def endpoint(name, flags):
                    fd = os.open(name, flags | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=self.root)
                    owned.append(fd)
                    return fd
                s = endpoint('S', os.O_WRONLY)
                c = endpoint('C', os.O_RDWR)
                os.write(s, b'READY V2\n')
                end, frame = time.monotonic() + 3, bytearray()
                while b'\n' not in frame:
                    if time.monotonic() >= end or finished.is_set():
                        return
                    if select.select([c], [], [], .01)[0]:
                        frame.extend(os.read(c, 128))
                if bytes(frame) != b'REQ 000001 007 01\n':
                    raise AssertionError('PUBLIC_MODEL_WRONG_REQUEST')
                q = endpoint('Q', os.O_RDONLY)
                r = endpoint('R', os.O_WRONLY)
                e = endpoint('E', os.O_WRONLY)
                os.write(s, b'START 000001\n')
                # EAGAIN means Q has a writer but no data. Initial zero does not.
                while time.monotonic() < end and not finished.is_set():
                    try:
                        self.assertEqual(os.read(q, 1), b'')
                    except BlockingIOError:
                        break
                    finished.wait(.001)
                else:
                    return
                os.write(s, b'OPENED 000001\n')
                # Wait for producer data, not an initial FIFO zero before Q opens.
                seen = False
                while time.monotonic() < end and not finished.is_set():
                    if select.select([q], [], [], .01)[0]:
                        block = os.read(q, 1024)
                        if block:
                            transferred.extend(block)
                            seen = True
                        elif seen:
                            break
                if not seen:
                    return
                if result:
                    os.write(r, result)
                if stderr:
                    os.write(e, stderr)
                if proof:
                    mailbox.completion = subject.Completion(1, 7, 1, 0, True, True, True, True)
                if done_first:
                    os.write(s, done)
                if eof_delay:
                    finished.wait(eof_delay)
                for fd in (r, e):
                    owned.remove(fd)
                    os.close(fd)
                if not done_first:
                    os.write(s, done)
                # Both surviving-writer and final-writer-close schedules are models.
                while time.monotonic() < end and not finished.is_set():
                    if select.select([c], [], [], .01)[0]:
                        if os.read(c, 128) == b'STOP\n' and stop:
                            os.write(s, stop_frames)
                            if close_final_writer:
                                owned.remove(s)
                                os.close(s)
                            if driver_delay:
                                finished.wait(driver_delay)
                            if driver_proof:
                                mailbox.driver = subject.DriverCompletion(driver_status, True, True, True)
                            if close_final_writer:
                                return
            except BaseException as error:
                errors.append(type(error).__name__)
            finally:
                for fd in owned:
                    os.close(fd)

        worker = threading.Thread(target=run)
        worker.start()
        def cleanup():
            finished.set()
            worker.join(3.5)
            self.assertFalse(worker.is_alive())
        self.addCleanup(cleanup)
        return errors, transferred

    def exercise_order(self, done_first):
        pump, mailbox = self.make(payload=b'x' * 8192)
        errors, transferred = self.actor(pump, mailbox, done_first=done_first, stop=True)
        pump.ready()
        self.assertEqual(pump.request(7), subject.Outcome(1, 'VERIFIED'))
        self.assertEqual(bytes(transferred), b'x' * 8192)
        self.assertEqual(pump.stop(), 'CONTROLS_CLOSED_NATIVE_TERMINAL_UNPROVED')
        self.assertFalse(errors)

    def test_real_fifo_eof_before_done_and_partial_q_chunks(self):
        self.exercise_order(False)

    def test_real_fifo_done_before_eof_and_partial_q_chunks(self):
        self.exercise_order(True)

    def stop_setup(self, **options):
        pump, mailbox = self.make()
        errors, _ = self.actor(pump, mailbox, stop=True, **options)
        pump.ready()
        self.assertEqual(pump.request(7).classification, 'VERIFIED')
        return pump, mailbox, errors

    def test_real_final_s_writer_close_with_exact_frames_and_separate_driver_proof(self):
        pump, _, errors = self.stop_setup(close_final_writer=True)
        self.assertEqual(pump.stop(), 'CONTROLS_CLOSED_NATIVE_TERMINAL_UNPROVED')
        self.assertTrue(pump.terminal_s_eof)
        self.assertTrue(pump.closed)
        self.assertFalse(errors)

    def test_terminal_eof_waits_for_delayed_proof_without_busy_eof_loop(self):
        pump, _, errors = self.stop_setup(close_final_writer=True, driver_delay=.08)
        real_factory, selections = subject.selectors.DefaultSelector, []
        def factory():
            poller = real_factory()
            original = poller.select
            def counted(timeout=None):
                selections.append(timeout)
                return original(timeout)
            poller.select = counted
            return poller
        start = time.monotonic()
        with patch.object(subject.selectors, 'DefaultSelector', side_effect=factory):
            self.assertEqual(pump.stop(), 'CONTROLS_CLOSED_NATIVE_TERMINAL_UNPROVED')
        self.assertTrue(pump.terminal_s_eof)
        self.assertGreaterEqual(time.monotonic() - start, .07)
        self.assertLess(len(selections), 30)
        self.assertFalse(errors)

    def test_early_s_eof_refuses_even_with_mock_driver_proof(self):
        pump, _, _ = self.stop_setup(close_final_writer=True, stop_frames=b'')
        with self.assertRaisesRegex(transport.TransportRefusal, '^PUMP_STOP_FAILED_UNRECONCILED$'):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertFalse(pump.terminal_s_eof)

    def test_s_eof_after_only_stopped_refuses(self):
        pump, _, _ = self.stop_setup(close_final_writer=True, stop_frames=b'STOPPED\n')
        with self.assertRaises(transport.TransportRefusal):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertFalse(pump.control.driver_exit)

    def test_partial_terminal_frame_eof_refuses(self):
        pump, _, _ = self.stop_setup(close_final_writer=True,
                                     stop_frames=b'STOPPED\nDRIVER_EXIT 000')
        with self.assertRaises(transport.TransportRefusal):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertFalse(pump.terminal_s_eof)

    def test_terminal_eof_without_driver_proof_expires_under_same_whole_deadline(self):
        pump, _, _ = self.stop_setup(close_final_writer=True, driver_proof=False)
        pump.whole = time.monotonic() + .15
        with self.assertRaises(transport.TransportRefusal):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertTrue(pump.terminal_s_eof)
        self.assertFalse(pump.closed)

    def test_terminal_eof_with_boolean_driver_status_refuses(self):
        pump, _, _ = self.stop_setup(close_final_writer=True, driver_status=False)
        with self.assertRaises(transport.TransportRefusal):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertFalse(pump.closed)

    def test_terminal_eof_with_boolean_driver_snapshot_refuses(self):
        pump, mailbox, _ = self.stop_setup(close_final_writer=True, driver_proof=False)
        mailbox.driver = True
        with self.assertRaises(transport.TransportRefusal):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertFalse(pump.closed)

    def test_terminal_eof_with_unreaped_driver_proof_refuses(self):
        pump, mailbox, _ = self.stop_setup(close_final_writer=True, driver_proof=False)
        mailbox.driver = subject.DriverCompletion(0, False, True, True)
        with self.assertRaises(transport.TransportRefusal):
            pump.stop()
        self.assertTrue(pump.failed)
        self.assertFalse(pump.closed)

    def test_done_and_proof_do_not_replace_delayed_actual_response_eof(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, done_first=True, eof_delay=.08)
        pump.ready()
        start = time.monotonic()
        self.assertEqual(pump.request(7).classification, 'VERIFIED')
        self.assertGreaterEqual(time.monotonic() - start, .07)

    def test_false_is_absent_only_for_closed_object_state(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, result=b'f\n')
        pump.ready()
        self.assertEqual(pump.request(7).classification, 'ABSENT')

    def test_false_invariant_and_first_diagnostic_byte_latch_all_future_requests(self):
        pump, mailbox = self.make(kind='MUST_TRUE_INVARIANT')
        self.actor(pump, mailbox, result=b'f\n', stderr=b'!')
        pump.ready()
        with self.assertRaisesRegex(transport.TransportRefusal, '^PUMP_REQUEST_FAILED_UNRECONCILED$'):
            pump.request(7)
        self.assertTrue(pump.failed)
        self.assertTrue(pump.namespace.failed)
        self.assertEqual(pump.request_handles, {})
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)

    def test_nonzero_done_refuses_even_with_scalar_eof_and_mock_proof(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, done=b'DONE 000001 124\n')
        pump.ready()
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)

    def test_false_invariant_without_diagnostics_is_refused(self):
        pump, mailbox = self.make(kind='MUST_TRUE_INVARIANT')
        self.actor(pump, mailbox, result=b'f\n')
        pump.ready()
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)

    def test_scalar_requires_exact_one_lf_and_closed_grammar(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, result=b't\n\n')
        pump.ready()
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)

    def test_result_overflow_is_refused_without_returning_raw_bytes(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, result=b't' * 17)
        pump.ready()
        with self.assertRaisesRegex(transport.TransportRefusal, '^PUMP_REQUEST_FAILED_UNRECONCILED$'):
            pump.request(7)

    def test_done_and_eof_without_reaping_witness_expires(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, proof=False)
        pump.ready()
        pump.whole = time.monotonic() + .15
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)
        self.assertTrue(pump.failed)

    def test_done_and_reaping_witness_without_eof_expires(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, done_first=True, eof_delay=.4)
        pump.ready()
        pump.whole = time.monotonic() + .15
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)
        self.assertTrue(pump.failed)

    def test_queued_duplicate_done_is_not_hidden_by_completion(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox, done=b'DONE 000001 000\nDONE 000001 000\n')
        pump.ready()
        with self.assertRaises(transport.TransportRefusal):
            pump.request(7)

    def test_enxio_four_retries_then_fifth_open_succeeds(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox)
        pump.ready()
        original, attempts = pump.namespace.open, []
        def intermittent(name, flags):
            if name == 'Q':
                attempts.append(name)
                if len(attempts) <= 4:
                    raise OSError(errno.ENXIO, 'PUBLIC_MODEL_NO_READER')
            return original(name, flags)
        with patch.object(pump.namespace, 'open', side_effect=intermittent):
            self.assertEqual(pump.request(7).classification, 'VERIFIED')
        self.assertEqual(len(attempts), 5)

    def test_enxio_is_finite_and_other_open_errors_never_retry(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox)
        pump.ready()
        original, attempts = pump.namespace.open, []
        def refused(name, flags):
            if name == 'Q':
                attempts.append(name)
                raise OSError(errno.EACCES, 'PUBLIC_MODEL_DENIED')
            return original(name, flags)
        with patch.object(pump.namespace, 'open', side_effect=refused):
            with self.assertRaises(transport.TransportRefusal):
                pump.request(7)
        self.assertEqual(len(attempts), 1)

    def test_enxio_exhausts_five_attempts_without_adoption(self):
        pump, mailbox = self.make()
        self.actor(pump, mailbox)
        pump.ready()
        original, attempts = pump.namespace.open, []
        def absent(name, flags):
            if name == 'Q':
                attempts.append(name)
                raise OSError(errno.ENXIO, 'PUBLIC_MODEL_NO_READER')
            return original(name, flags)
        with patch.object(pump.namespace, 'open', side_effect=absent):
            with self.assertRaises(transport.TransportRefusal):
                pump.request(7)
        self.assertEqual(len(attempts), 5)
        self.assertTrue(pump.failed)

    def test_changed_namespace_refuses_before_encoding_or_private_endpoint_open(self):
        pump, _ = self.make()
        pump.control.feed(b'READY V2\n')
        os.chmod('Q', 0o644, dir_fd=self.root)
        with patch.object(transport.Selector, 'encode') as encode, \
                patch.object(pump.namespace, 'open') as opened:
            with self.assertRaises(transport.TransportRefusal):
                pump.request(7)
        encode.assert_not_called()
        opened.assert_not_called()

    def test_origin_precedes_encoding_and_expired_encoding_opens_no_request_endpoint(self):
        pump, _ = self.make()
        pump.control.feed(b'READY V2\n')
        now = [time.monotonic()]
        pump.clock = lambda: now[0]
        original = transport.Selector.encode
        def slow_encode(selector, value):
            now[0] += 5
            return original(selector, value)
        with patch.object(transport.Selector, 'encode', slow_encode), \
                patch.object(pump.namespace, 'open') as opened:
            with self.assertRaises(transport.TransportRefusal):
                pump.request(7)
        opened.assert_not_called()

    def test_reaping_status_boolean_and_stale_or_missing_witness_do_not_pass(self):
        pump, mailbox = self.make()
        selected = pump.manifest[7]
        self.assertFalse(pump.completion_ok(selected, 1))
        mailbox.completion = subject.Completion(0, 7, 1, 0, True, True, True, True)
        self.assertFalse(pump.completion_ok(selected, 1))
        mailbox.completion = subject.Completion(1, 7, 1, False, True, True, True, True)
        with self.assertRaises(transport.TransportRefusal):
            pump.completion_ok(selected, 1)

    def test_atomic_control_short_write_never_retries_tail(self):
        pump, _ = self.make()
        with patch.object(subject.os, 'write', return_value=1) as write:
            with self.assertRaises(transport.TransportRefusal):
                pump.publish_control(b'STOP\n', pump.whole, lifecycle=True)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(pump.budget.lifecycle_bytes, 1)

    def test_uncertain_close_latches_and_never_retries_descriptor(self):
        pump, _ = self.make()
        fd = pump.handles['C']
        real_close, attempted = os.close, []
        def uncertain(value):
            attempted.append(value)
            real_close(value)
            if value == fd:
                raise OSError(errno.EINTR, 'PUBLIC_MODEL_UNCERTAIN_CLOSE')
        with patch.object(subject.os, 'close', side_effect=uncertain):
            with self.assertRaises(transport.TransportRefusal):
                pump.close_one(pump.handles, 'C')
            pump.abort()
            pump.abort()
        self.assertEqual(attempted.count(fd), 1)
        self.assertIn(fd, pump.uncertain_fds)
        self.assertTrue(pump.failed)

    def test_bad_handoff_is_fixed_public_refusal_even_without_namespace(self):
        now = float(time.monotonic())
        with self.assertRaisesRegex(transport.TransportRefusal, '^PUMP_HANDOFF_REFUSED$'):
            subject.Pump(None, (), None, None, now + 10, now)


if __name__ == '__main__':
    unittest.main()
