"""Baked guardian integration and adversarial deadline/remote-state tests."""
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy/verify'))
import native_factory_startup_inspect as startup
import native_startup_guardian as guardian
from test_native_factory_startup import FakeDocker, FakeGuardDocker, profile, source, LOCK


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class FullCapDocker(FakeGuardDocker):
    def __init__(self, docker, clock):
        super().__init__(docker)
        self.clock = clock

    def step(self, end):
        startup.require(self.clock.now + 2 <= end, 'TEST_COMMAND_DEADLINE')
        self.clock.now += 2

    def call(self, args, end):
        self.step(end)
        return super().call(args, end)

    def inspect(self, cid, end):
        self.step(end)
        return super().inspect(cid, end)

    def absent(self, cid, name, end):
        self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + cid], end)
        self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'name=^/' + name + '$'], end)
        return super().absent(cid, name, end)


def registered_guard(role='source-check', now=10, cleanup=190.2):
    clock = Clock(now)
    docker = FakeDocker()
    cid = 'a' * 64
    run = 'b' * 32
    handoff = startup.MaterialHandoff(run, *['factory-' + run + '-' + suffix for suffix in ('custom', 'credential', 'pgdata', 'runtime')], {}, {}, ())
    lineage = startup.CreationLineage('factory-' + run + '-' + role)
    args = ['docker', 'create', '--user', '100:101', '--name', lineage.name, '--entrypoint', '/bin/sh', '--label', 'io.sbarbase.owner=' + handoff.owner, profile().reference, '-eu', '-c', 'public-only']
    expected = startup.expected_helper_projection(profile(), handoff, args, lineage, profile().disabled_health, {'Test': ['NONE']})
    expected['Id'] = cid
    docker.containers[cid] = copy.deepcopy(expected)
    docker.containers[cid]['State'].update(Status='running', Running=True)
    boundary = FullCapDocker(docker, clock)
    state = guardian.Guardian(run, 200.2, docker.controller, boundary, clock)
    state.work_end, state.cleanup_end = cleanup - 30, cleanup
    state.slot = {'role': role, 'name': lineage.name, 'expected': expected, 'cid': cid, 'start_end': now + 20, 'removing': False}
    return state, docker, clock, cid


class GuardianTests(unittest.TestCase):
    def test_fast_launch_counterexample_composes_inside_wrapper(self):
        self.assertEqual(guardian.compose_deadlines(200.2, 179.95, 239.95, .3), (160.2, 190.2))
        with self.assertRaises(startup.StartupRefusal):
            guardian.compose_deadlines(40, 180, 240, 1)

    def test_insufficient_start_reserve_refuses(self):
        state, _, clock, _ = registered_guard(now=159)
        with self.assertRaises(startup.StartupRefusal):
            state.healthy_reserve(20)
        clock.now = 139
        state.healthy_reserve(20)

    def test_full_cap_running_cleanup_uses_seven_commands_fourteen_seconds(self):
        state, docker, clock, cid = registered_guard(now=160.2)
        report = state.cleanup()
        self.assertEqual(report['status'], 'REFUSED')
        self.assertEqual(report['remote'], 'ABSENT')
        self.assertAlmostEqual(clock.now, 174.2)
        self.assertNotIn(cid, docker.containers)
        self.assertLessEqual(clock.now + 16, state.cleanup_end)
        self.assertLessEqual(state.cleanup_end + 10, state.hard_end)

    def test_cleanup_exhaustion_stays_unresolved(self):
        state, docker, _, cid = registered_guard(now=187)
        report = state.cleanup()
        self.assertEqual(report['remote'], 'UNRESOLVED')
        self.assertIn(cid, docker.containers)
        self.assertTrue(state.failed)

    def test_actual_executor_attach_timeout_leaves_running_helper_for_independent_guard(self):
        class TimeoutDocker(FakeDocker):
            def call(self, args, **kwargs):
                if args[1:3] == ['start', '--attach']:
                    item = self.containers[args[-1]]
                    item['State'].update(Status='running', Running=True)
                    raise startup.StartupRefusal('LOCAL_ATTACH_TIMEOUT')
                return super().call(args, **kwargs)
        docker = TimeoutDocker()
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        with self.assertRaises(startup.StartupRefusal):
            executor.run(profile(), LOCK, source())
        cid = executor.helpers[0]['lineage'].cid
        self.assertTrue(docker.containers[cid]['State']['Running'])
        self.assertEqual(executor.receipt['status'], 'REFUSED')
        outcome = docker.guard.cleanup()
        self.assertEqual(outcome['remote'], 'ABSENT')
        self.assertNotIn(cid, docker.containers)
        self.assertTrue(any(args[1] == 'kill' and args[-1] == cid for args in docker.guard.docker.commands))
        self.assertEqual(executor.receipt['status'], 'REFUSED')

    def test_late_absence_ack_freezes_actual_executor_before_next_role(self):
        class LostAckDocker(FakeDocker):
            def call(self, args, **kwargs):
                if args[1] == 'exec':
                    message = json.loads(args[args.index('--message') + 1])
                    if message['operation'] == 'absence':
                        super().call(args, **kwargs)
                        raise startup.StartupRefusal('CONTROL_TWO_SECOND_CAP')
                return super().call(args, **kwargs)
        docker = LostAckDocker()
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        with self.assertRaises(startup.StartupRefusal):
            executor.run(profile(), LOCK, source())
        names = [item['args'][item['args'].index('--name') + 1] for item in docker.commands if item['args'][1] == 'create']
        self.assertFalse(any(name.endswith('-seed') for name in names))
        self.assertEqual(executor.receipt['status'], 'REFUSED')

    def test_controller_death_cleanup_does_not_need_controller_finally(self):
        state, docker, clock, cid = registered_guard()
        docker.controller['State'].update(Status='exited', Running=False)
        state.tick()
        self.assertTrue(state.failed)
        self.assertTrue(docker.containers[cid]['State']['Running'])
        self.assertEqual(state.cleanup()['remote'], 'ABSENT')
        self.assertNotIn(cid, docker.containers)

    def test_unknown_create_never_adopts_name_or_starts(self):
        state, docker, _, cid = registered_guard()
        state.slot['cid'] = None
        docker.containers[cid]['State'].update(Status='created', Running=False)
        report = state.cleanup()
        self.assertEqual(report['remote'], 'UNRESOLVED_CREATE')
        self.assertEqual(docker.containers[cid]['State']['Status'], 'created')
        self.assertFalse(state.docker.commands)

    def test_authority_drift_refuses_signal(self):
        state, docker, _, cid = registered_guard()
        docker.containers[cid]['Labels'] = {'io.sbarbase.owner': 'foreign'}
        self.assertEqual(state.cleanup()['remote'], 'UNRESOLVED')
        self.assertTrue(docker.containers[cid]['State']['Running'])
        self.assertFalse(any(args[1] == 'kill' for args in state.docker.commands))

    def test_remote_kill_failure_never_proves_lifetime_or_handoff(self):
        state, docker, _, cid = registered_guard()
        original = state.docker.call
        def refusal(args, end):
            if args[1] == 'kill':
                raise startup.StartupRefusal('DAEMON_UNAVAILABLE')
            return original(args, end)
        state.docker.call = refusal
        self.assertEqual(state.cleanup()['remote'], 'UNRESOLVED')
        self.assertTrue(docker.containers[cid]['State']['Running'])
        self.assertTrue(state.failed)

    def test_candidate_running_is_never_helper_stop_authority(self):
        state, docker, _, cid = registered_guard('candidate')
        self.assertEqual(state.cleanup()['remote'], 'UNRESOLVED')
        self.assertTrue(docker.containers[cid]['State']['Running'])
        self.assertFalse(any(args[1] == 'kill' for args in state.docker.commands))

    def test_pending_role_blocks_next_intent(self):
        state, _, _, _ = registered_guard()
        with self.assertRaises(startup.StartupRefusal):
            state.intent('seed', {})
        state.slot['cid'] = None
        with self.assertRaises(startup.StartupRefusal):
            state.intent('seed', {})

    def test_immutable_configuration_cannot_extend_deadlines(self):
        state, _, clock, _ = registered_guard()
        original = (state.work_end, state.cleanup_end, state.hard_end)
        with self.assertRaises(startup.StartupRefusal):
            state.configure(1000, 2000)
        self.assertEqual(original, (state.work_end, state.cleanup_end, state.hard_end))

    def test_seal_reader_release_reserve_all_remaining_controller_stages(self):
        for stages in (3, 2, 1):
            last = 240 - 50 - stages * 2
            guardian.reserve_completion(last, 240, stages)
            with self.assertRaises(startup.StartupRefusal):
                guardian.reserve_completion(last + .001, 240, stages)
        with self.assertRaises(startup.StartupRefusal):
            guardian.reserve_completion(190.2, 240, 1)

    def test_script_and_import_share_fixed_exception_identity(self):
        import native_startup_contract as shared
        self.assertIs(startup.StartupRefusal, shared.StartupRefusal)
        self.assertIs(guardian.StartupRefusal, shared.StartupRefusal)
        # --help does not create Docker resources; catches script-mode import errors.
        script = Path(startup.__file__)
        result = subprocess.run([sys.executable, str(script), '--help'], stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b'')


class ProcessBoundaryTests(unittest.TestCase):
    """Baked tests exercise real production functions and owned process sessions.

    Public fixture programs replace Docker command payloads, never daemon state
    admission. No fixture outcome is material or native preparation acceptance.
    """
    def setUp(self):
        import ctypes
        self._subreaper_prctl = ctypes.CDLL(None, use_errno=True).prctl
        self._subreaper_integer = ctypes.c_int
        self._subreaper_byref = ctypes.byref
        self._subreaper_original = self._read_subreaper()
        self.addCleanup(self._restore_subreaper, self._subreaper_original)
        guardian.establish_subreaper()
        self.assertEqual(self._read_subreaper(), 1)

    def _read_subreaper(self):
        value = self._subreaper_integer(-1)
        self.assertEqual(self._subreaper_prctl(37, self._subreaper_byref(value), 0, 0, 0), 0,
                         'required caller subreaper getter unavailable')
        self.assertIn(value.value, (0, 1))
        return value.value

    def _restore_subreaper(self, original):
        self.assertIs(type(original), int)
        self.assertIn(original, (0, 1))
        self.assertEqual(self._subreaper_prctl(36, original, 0, 0, 0), 0,
                         'required caller subreaper restoration unavailable')
        self.assertEqual(self._read_subreaper(), original)

    def test_nested_failure_cleanup_restores_both_actual_subreaper_states(self):
        original = self._read_subreaper()
        self.addCleanup(self._restore_subreaper, original)
        class FailingCase(ProcessBoundaryTests):
            def runTest(self):
                self.assertEqual(self._read_subreaper(), 1)
                self.fail('deliberate nested fixture failure')
        try:
            for state in (0, 1):
                self._restore_subreaper(state)
                case = FailingCase(methodName='runTest')
                result = unittest.TestResult()
                unittest.TestSuite([case]).run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(len(result.failures), 1)
                self.assertIn('deliberate nested fixture failure', result.failures[0][1])
                self.assertFalse(result.errors or result.skipped or result.expectedFailures
                                 or result.unexpectedSuccesses)
                self.assertEqual(self._read_subreaper(), state)
        finally:
            self._restore_subreaper(original)

    def owner(self):
        return guardian.SessionOwner(guardian.time.monotonic() + 60)

    def test_exited_unreaped_leader_signals_before_reap_without_later_signal(self):
        from unittest.mock import Mock, patch
        events = []
        process = Mock(pid=4321, returncode=None)
        process.wait.side_effect = lambda **kwargs: events.append('reap')
        def waitid(kind, pid, flags):
            if kind == guardian.os.P_PGID:
                raise ChildProcessError()
            events.append('WNOWAIT')
            return Mock(si_status=0, si_code=guardian.os.CLD_EXITED)
        with patch.object(guardian.os, 'waitid', side_effect=waitid), patch.object(guardian.os, 'killpg', side_effect=lambda *args: events.append(('signal', args))):
            guardian.local_kill_reap(process, guardian.time.monotonic() + 1)
        self.assertEqual(events, ['WNOWAIT', ('signal', (4321, guardian.signal.SIGKILL)), 'reap'])

    def wrapper_fixture(self, program, *, reader_program=None, virtual=False):
        import stat
        import tempfile
        from unittest.mock import patch
        original_stat = guardian.os.stat
        original_spawn = guardian.SessionOwner.spawn
        original_shutdown = guardian.SessionOwner.shutdown
        owners = []
        def spawn(owner, argv, **kwargs):
            if argv[0] == 'fixture':
                return original_spawn(owner, [sys.executable, '-c', argv[1]], **kwargs)
            source = reader_program if argv[-1] == 'reader' else program
            return original_spawn(owner, [sys.executable, '-c', source, *argv[3:]], **kwargs)
        def shutdown(owner):
            owners.append(owner)
            return original_shutdown(owner)
        def stat_socket(path, *args, **kwargs):
            if str(path) == '/var/run/docker.sock':
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=stat.S_IFSOCK)
            actual = original_stat(path, *args, **kwargs)
            if str(path) == str(guardian.CONTROL):
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=actual.st_mode, st_uid=0, st_gid=0)
            return actual
        with tempfile.TemporaryDirectory() as directory:
            control_path = Path(directory)
            # Baked tests use UID10001. Only this public fixture's ownership
            # metadata is injected; production requires real root ownership.
            with patch.object(guardian, 'CONTROL', control_path), patch.object(guardian.os, 'stat', side_effect=stat_socket), patch.object(guardian.SessionOwner, 'spawn', spawn), patch.object(guardian.SessionOwner, 'shutdown', shutdown), patch.object(guardian, 'broker_command', return_value=True):
                if virtual:
                    clock = Clock(100)
                    original_select = guardian.selectors.DefaultSelector.select
                    def select(selected, timeout=None):
                        clock.now += .25
                        return original_select(selected, 0)
                    with patch.object(guardian.time, 'monotonic', clock), patch.object(guardian.selectors.DefaultSelector, 'select', select):
                        code = guardian.wrapper('b' * 32, 41, {})
                else:
                    try:
                        code = guardian.wrapper('b' * 32, 41, {})
                    except startup.StartupRefusal:
                        code = 1
        return code, owners

    def test_actual_wrapper_owns_live_distinct_reader_when_serve_dies(self):
        serve = """import json, os, socket, sys, time
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
sock = socket.socket(fileno=int(args['--broker-descriptor']))
reader = 'import os,time; os.kill(%d,9); time.sleep(60)' % os.getpid()
sock.sendall((json.dumps({'argv':['fixture', reader], 'end':time.monotonic()+2,'limit':4096})+'\\n').encode())
time.sleep(60)
"""
        # fixture dispatcher belongs to the wrapper, including the distinct reader.
        code, owners = self.wrapper_fixture(serve, reader_program='')
        self.assertEqual(code, 1)
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].children, {})
        self.assertEqual(len(owners[0].closed), 2)
        self.assertLess(owners[0].shutdown_started, owners[0].hard_end - guardian.SHUTDOWN)

    def test_actual_wrapper_starts_shutdown_within_reserve_under_full_clock_caps(self):
        code, owners = self.wrapper_fixture('import time; time.sleep(60)', virtual=True)
        self.assertEqual(code, 1)
        self.assertEqual(owners[0].children, {})
        self.assertLessEqual(owners[0].shutdown_started, owners[0].hard_end - guardian.SHUTDOWN)

    def test_actual_owned_reader_incrementally_refuses_oversized_stdout(self):
        from unittest.mock import patch
        owner = self.owner()
        original_spawn = owner.spawn
        with patch.object(owner, 'spawn', side_effect=lambda argv, **kwargs: original_spawn([sys.executable, '-c', 'import os; os.write(1,b"x"*4097)'], **kwargs)):
            with self.assertRaises(startup.StartupRefusal):
                guardian.owned_reader(guardian.time.monotonic() + 2, guardian.LocalBroker(owner))
        self.assertEqual(owner.children, {})
        self.assertEqual(len(owner.closed), 1)

    def test_actual_owned_reader_timeout_closes_all_streams_and_reaps(self):
        from unittest.mock import patch
        owner = self.owner()
        original_spawn = owner.spawn
        handles = []
        def spawn(argv, **kwargs):
            process = original_spawn([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            handles.append(process)
            return process
        with patch.object(owner, 'spawn', side_effect=spawn):
            with self.assertRaises(startup.StartupRefusal):
                guardian.owned_reader(guardian.time.monotonic() + .4, guardian.LocalBroker(owner))
        self.assertEqual(owner.children, {})
        self.assertTrue(handles[0].stdout.closed and handles[0].stderr.closed)
        self.assertIsNotNone(handles[0].returncode)

    def test_actual_reader_close_failure_cannot_return_complete_capture(self):
        from unittest.mock import patch
        owner = self.owner()
        original_close = guardian.close_pipes
        def uncertain(process):
            original_close(process)
            raise startup.StartupRefusal('INJECTED_PIPE_CLOSE_UNCERTAIN')
        with patch.object(guardian, 'close_pipes', side_effect=uncertain):
            with self.assertRaises(startup.StartupRefusal):
                owner.capture([sys.executable, '-c', 'print("public")'], guardian.time.monotonic() + 2, 4096)
        self.assertEqual(len(owner.children), 1)
        owner.shutdown()
        self.assertFalse(owner.children)

    def test_actual_serve_reader_failure_publishes_refusal_without_release(self):
        import os
        import socket
        import tempfile
        from unittest.mock import patch
        from types import SimpleNamespace
        read, write = os.pipe()
        os.write(write, b'{"hard_end":200,"controller":{}}\n')
        os.close(write)
        parent, child = socket.socketpair()
        state = SimpleNamespace(hard_end=200, cleanup_end=190, sequence=0, released=False, failed=False,
            completed=[], observations=[], docker=SimpleNamespace(commands=[]), clock=lambda: 1,
            tick=lambda: None, message=lambda message: {'status': 'GUARD_COMPLETE'},
            cleanup=lambda: {'status': 'REFUSED', 'remote': 'ABSENT'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            guardian.public_write(path / 'message-1.json', {'sequence': 1, 'operation': 'read', 'data': {}})
            with patch.object(guardian, 'CONTROL', path), patch.object(guardian, 'Guardian', return_value=state), patch.object(guardian, 'owned_reader', side_effect=startup.StartupRefusal('READER_UNCERTAIN')):
                self.assertEqual(guardian.serve('b' * 32, read, child.detach()), 1)
            self.assertEqual(guardian.public_read(path / 'reply-1.json'), {'status': 'REFUSED'})
            self.assertEqual(guardian.public_read(path / 'outcome.json')['status'], 'REFUSED')
            self.assertFalse(state.released)
        parent.close()

    def test_actual_control_publication_ack_and_timeout_preserve_uncertainty(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(guardian, 'CONTROL', path), patch('builtins.print') as printed:
                guardian.public_write(path / 'reply-1.json', {'status': 'ACK', 'data': {'status': 'READY'}})
                self.assertEqual(guardian.control({'sequence': 1, 'operation': 'sample'}), 0)
                self.assertEqual(printed.call_count, 1)
                clock = Clock(10)
                def advance(_):
                    clock.now += .25
                with patch.object(guardian.time, 'monotonic', clock), patch.object(guardian.time, 'sleep', side_effect=advance):
                    with self.assertRaises(startup.StartupRefusal):
                        guardian.control({'sequence': 2, 'operation': 'intent'})
                self.assertEqual(printed.call_count, 1)
                self.assertTrue((path / 'message-2.json').exists())
                self.assertFalse((path / 'message-3.json').exists())

    def test_actual_guarddocker_broker_closes_and_reaps_before_result(self):
        from unittest.mock import patch
        owner = self.owner()
        original_spawn = owner.spawn
        handles = []
        def spawn(argv, **kwargs):
            process = original_spawn([sys.executable, '-c', 'print("public")'], **kwargs)
            handles.append(process)
            return process
        with patch.object(owner, 'spawn', side_effect=spawn):
            docker = guardian.GuardDocker(broker=guardian.LocalBroker(owner))
            self.assertEqual(docker.call(['docker', 'ps', '-aq'], guardian.time.monotonic() + 2), b'public\n')
        self.assertFalse(owner.children)
        self.assertTrue(handles[0].stdout.closed and handles[0].stderr.closed)
        self.assertEqual(handles[0].returncode, 0)

    def test_actual_boundeddocker_timeout_terminates_and_closes_session(self):
        from unittest.mock import patch
        original_popen = subprocess.Popen
        handles = []
        def popen(argv, **kwargs):
            process = original_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            handles.append(process)
            return process
        docker = startup.BoundedDocker()
        with patch.object(subprocess, 'Popen', side_effect=popen):
            with self.assertRaises(startup.StartupRefusal):
                docker.call(['docker', 'ps', '-aq'], cap=.4)
        self.assertIsNotNone(handles[0].returncode)
        self.assertTrue(handles[0].stdout.closed and handles[0].stderr.closed)

    def test_actual_exited_leader_with_live_group_descendant_is_owned_and_reaped(self):
        import os
        import time
        owner = self.owner()
        program = 'import os,time; child=os.fork(); os._exit(0) if child else time.sleep(60)'
        process = owner.spawn([sys.executable, '-c', program], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        end = time.monotonic() + 2
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            self.assertLess(time.monotonic(), end)
            time.sleep(.005)
        self.assertIsNone(process.returncode)
        owner.cleanup(process, end)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(process._sbarbase_group_reaped)
        self.assertTrue(process.stdout.closed and process.stderr.closed)
        self.assertFalse(owner.children)
        with self.assertRaises(ChildProcessError):
            os.waitid(os.P_PGID, process.pid, os.WEXITED | os.WNOHANG)

    def test_descendant_failure_after_leader_reap_irreversibly_refuses_retry(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        clock = Clock(10)
        process = SimpleNamespace(pid=4321, returncode=None)
        def wait(**kwargs):
            process.returncode = 0
        process.wait = wait
        def waitid(kind, pid, flags):
            if kind == guardian.os.P_PGID:
                return None  # An owned adopted descendant has not terminated.
            return SimpleNamespace(si_status=0, si_code=guardian.os.CLD_EXITED)
        def advance(_):
            clock.now += 1
        with patch.object(guardian.time, 'monotonic', clock), patch.object(guardian.time, 'sleep', side_effect=advance), patch.object(guardian.os, 'waitid', side_effect=waitid), patch.object(guardian.os, 'killpg') as signal_group:
            with self.assertRaisesRegex(startup.StartupRefusal, 'GUARD_LOCAL_GROUP_UNRESOLVED'):
                guardian.local_kill_reap(process, 11)
            self.assertEqual(process.returncode, 0)
            self.assertFalse(getattr(process, '_sbarbase_group_reaped', False))
            with self.assertRaisesRegex(startup.StartupRefusal, 'GUARD_LOCAL_AUTHORITY_REAPED'):
                guardian.local_kill_reap(process, 20)
            self.assertEqual(signal_group.call_count, 1)

    def test_boundeddocker_selector_close_failure_still_closes_every_pipe(self):
        from unittest.mock import patch
        original_popen = subprocess.Popen
        original_selector = guardian.selectors.DefaultSelector
        handles = []
        def popen(argv, **kwargs):
            process = original_popen([sys.executable, '-c', 'print("public")'], **kwargs)
            handles.append(process)
            return process
        class FailingCloseSelector:
            def __init__(self):
                self.inner = original_selector()
            def register(self, *args):
                return self.inner.register(*args)
            def unregister(self, *args):
                return self.inner.unregister(*args)
            def get_map(self):
                return self.inner.get_map()
            def select(self, *args):
                return self.inner.select(*args)
            def close(self):
                self.inner.close()
                raise OSError('injected selector closure failure')
        docker = startup.BoundedDocker()
        with patch.object(subprocess, 'Popen', side_effect=popen), patch.object(guardian.selectors, 'DefaultSelector', FailingCloseSelector):
            with self.assertRaises(OSError):
                docker.call(['docker', 'ps', '-aq'], cap=2)
        self.assertEqual(handles[0].returncode, 0)
        self.assertTrue(handles[0]._sbarbase_group_reaped)
        self.assertTrue(handles[0].stdout.closed and handles[0].stderr.closed)
