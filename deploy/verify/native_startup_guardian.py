"""Independent daemon-side guardian for one serialized preparation role.

Only public contracts/control records and selected Docker metadata are read.
The guardian never mounts or reads material volumes or private logs.
"""
import argparse
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from native_startup_contract import (
    CONTAINER_PROJECTION, StartupRefusal, identifier, require,
)

CONTROL = Path('/sbarbase-public-control')
ROLES = ('source-check', 'seed', 'key-provision', 'key-guard', 'password-provision', 'password-guard', 'candidate')
SHUTDOWN = 10.0
CLEANUP = 30.0
COMMAND = 2.0
RUNTIME = 20.0


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def compose_deadlines(hard_end, mapped_work, mapped_whole, now):
    require(all(finite(value) for value in (hard_end, mapped_work, mapped_whole, now)), 'GUARD_CLOCK_REFUSED')
    cleanup_end = min(mapped_whole, hard_end - SHUTDOWN)
    work_end = min(mapped_work, cleanup_end - CLEANUP)
    require(now < work_end < cleanup_end < hard_end, 'GUARD_RESERVE_REFUSED')
    return work_end, cleanup_end


def reserve_completion(controller_now, controller_end, stages):
    # Stages are the remaining seal/read/release calls before five10s commands.
    require(type(stages) is int and 0 <= stages <= 3 and finite(controller_now) and finite(controller_end)
            and controller_now + stages * COMMAND + 50 <= controller_end, 'CONTROLLER_COMPLETION_RESERVE')


def public_write(path, value):
    """Exclusive fsynced public publication; partial records preserve uncertainty."""
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    require(len(data) <= 131072, 'GUARD_RECORD_LIMIT')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:])
            require(type(count) is int and 0 < count <= len(data) - offset, 'GUARD_RECORD_WRITE')
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def public_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        import stat
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_size <= 131072 and meta.st_nlink == 1, 'GUARD_RECORD_REFUSED')
        data = os.read(fd, 131073)
    finally:
        os.close(fd)
    require(0 < len(data) <= 131072 and data.endswith(b'\n') and data.count(b'\n') == 1, 'GUARD_RECORD_REFUSED')
    def unique(pairs):
        record = {}
        for key, value in pairs:
            require(key not in record, 'GUARD_DUPLICATE_FIELD')
            record[key] = value
        return record
    return json.loads(data.decode('ascii'), object_pairs_hook=unique)


def local_kill_reap(process, end):
    """Signal the owned session before reaping even an already exited leader."""
    if process.returncode is not None:
        require(getattr(process, '_sbarbase_group_reaped', False) is True, 'GUARD_LOCAL_AUTHORITY_REAPED')
        return
    require(time.monotonic() < end, 'GUARD_LOCAL_REAP_DEADLINE')
    # WNOWAIT preserves the owned leader's PID reservation. An exited leader
    # can still have descendants in its session/process group.
    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=max(.001, end - time.monotonic()))
    # A wrapper subreaper can now reap adopted same-group descendants. No
    # further signal is sent after the reserved leader has been reaped.
    while time.monotonic() < end:
        try:
            orphan = os.waitid(os.P_PGID, process.pid, os.WEXITED | os.WNOHANG)
        except ChildProcessError:
            break
        if orphan is None:
            time.sleep(.005)
    else:
        raise StartupRefusal('GUARD_LOCAL_GROUP_UNRESOLVED')
    require(time.monotonic() < end, 'GUARD_LOCAL_REAP_DEADLINE')
    process._sbarbase_group_reaped = True


def close_pipes(process):
    failed = False
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                failed = True
    require(not failed, 'GUARD_PIPE_CLOSE_UNCERTAIN')


class SessionOwner:
    """Only the wrapper spawns detached sessions and retains every leader handle."""
    def __init__(self, hard_end, stop=lambda: False):
        self.hard_end = hard_end
        self.stop = stop
        self.children = {}
        self.closed = []
        self.shutdown_started = None

    def spawn(self, argv, **kwargs):
        require(time.monotonic() < self.hard_end - SHUTDOWN, 'WRAPPER_SPAWN_RESERVE')
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True, **kwargs)
        self.children[process.pid] = process
        return process

    def cleanup(self, process, end):
        require(self.children.get(process.pid) is process, 'WRAPPER_CHILD_AUTHORITY')
        # Do not drop the retained handle on termination, reap or close failure.
        try:
            local_kill_reap(process, min(end, self.hard_end))
        finally:
            close_pipes(process)
        require(time.monotonic() < min(end, self.hard_end), 'WRAPPER_CLOSE_DEADLINE')
        self.closed.append(process.pid)
        del self.children[process.pid]

    def capture(self, argv, end, limit):
        require(type(argv) is list and argv and all(type(arg) is str and len(arg) <= 131072 for arg in argv)
                and type(limit) is int and 0 < limit <= 131072, 'BROKER_CAPTURE_REFUSED')
        whole_end = min(end, self.hard_end - SHUTDOWN, time.monotonic() + COMMAND)
        exec_end = whole_end - .25
        require(time.monotonic() < exec_end, 'BROKER_CAPTURE_RESERVE')
        selected = selectors.DefaultSelector()
        try:
            process = self.spawn(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except BaseException:
            selected.close()
            raise
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        code = None
        try:
            for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selected.register(stream, selectors.EVENT_READ, name)
            while selected.get_map():
                require(time.monotonic() < exec_end and not self.stop(), 'BROKER_CAPTURE_INTERRUPTED')
                for key, _ in selected.select(max(0, min(.025, exec_end - time.monotonic()))):
                    value = os.read(key.fileobj.fileno(), 8192)
                    if not value:
                        selected.unregister(key.fileobj)
                        continue
                    require(len(buffers[key.data]) + len(value) <= limit, 'BROKER_STREAM_LIMIT')
                    buffers[key.data].extend(value)
            while time.monotonic() < exec_end and not self.stop():
                status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if status is not None:
                    code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
                    break
                time.sleep(.005)
            require(code is not None and time.monotonic() < exec_end and not self.stop(), 'BROKER_CAPTURE_INTERRUPTED')
        finally:
            try:
                selected.close()
            finally:
                self.cleanup(process, whole_end)
        # Every pipe is explicitly closed, and every owned group signaled and
        # leader reaped before any completed result frame leaves the wrapper.
        return code, bytes(buffers['stdout']), bytes(buffers['stderr'])

    def shutdown(self):
        self.shutdown_started = time.monotonic()
        late = self.shutdown_started > self.hard_end - SHUTDOWN
        failures = []
        # Signal every retained unreaped group before waiting on any one child.
        # One slow reap must not postpone another owned reader's stop request.
        for process in list(self.children.values()):
            try:
                if process.returncode is None:
                    require(time.monotonic() < self.hard_end, 'WRAPPER_SIGNAL_DEADLINE')
                    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            except (StartupRefusal, OSError):
                failures.append(process.pid)
        for process in list(self.children.values()):
            try:
                self.cleanup(process, self.hard_end)
            except (StartupRefusal, OSError, subprocess.SubprocessError):
                failures.append(process.pid)
        require(not late and not failures and not self.children and time.monotonic() < self.hard_end, 'WRAPPER_SHUTDOWN_UNRESOLVED')


class SocketBroker:
    """Serve requests execution; the wrapper owns all resulting process sessions."""
    def __init__(self, channel):
        self.channel = channel

    def capture(self, argv, end, limit):
        payload = json.dumps({'argv': argv, 'end': end, 'limit': limit}, separators=(',', ':')).encode('ascii') + b'\n'
        require(len(payload) <= 262144 and time.monotonic() < end, 'BROKER_REQUEST_LIMIT')
        self.channel.settimeout(max(.001, end - time.monotonic()))
        self.channel.sendall(payload)
        result = bytearray()
        while not result.endswith(b'\n'):
            require(time.monotonic() < end and len(result) <= 524288, 'BROKER_REPLY_LIMIT')
            value = self.channel.recv(min(8192, 524289 - len(result)))
            require(value, 'BROKER_DISCONNECTED')
            result.extend(value)
        require(result.count(b'\n') == 1 and len(result) <= 524288, 'BROKER_REPLY_FRAME')
        reply = json.loads(result)
        require(type(reply) is dict and set(reply) == {'status', 'code', 'stdout', 'stderr'}
                and reply['status'] == 'CAPTURED' and type(reply['code']) is int, 'BROKER_EXECUTION_REFUSED')
        stdout, stderr = bytes.fromhex(reply['stdout']), bytes.fromhex(reply['stderr'])
        require(len(stdout) <= limit and len(stderr) <= limit, 'BROKER_STREAM_LIMIT')
        return reply['code'], stdout, stderr


class LocalBroker:
    """A dependency-injected owner for baked production-boundary tests."""
    def __init__(self, owner):
        self.owner = owner

    def capture(self, argv, end, limit):
        return self.owner.capture(argv, end, limit)


class GuardDocker:
    """The two-second cap includes local CLI output/kill/reap, with no inherited stdin."""
    def __init__(self, clock=time.monotonic, broker=None):
        self.clock = clock
        self.broker = broker
        self.commands = []
        self.bytes = 0

    def call(self, args, end):
        start = self.clock()
        whole_end = min(end, start + COMMAND)
        require(self.broker is not None and start + .25 < whole_end, 'GUARD_BROKER_REQUIRED')
        self.commands.append({'operation': args[1], 'identity': args[-1], 'started': start})
        code, stdout, stderr = self.broker.capture(args, whole_end, 131072)
        self.bytes += len(stdout) + len(stderr)
        require(self.bytes <= 2097152 and code == 0 and stderr == b'' and self.clock() < whole_end,
                'GUARD_NATIVE_REFUSED')
        return stdout

    def inspect(self, cid, end):
        require(identifier(cid), 'GUARD_CID_REFUSED')
        return json.loads(self.call(['docker', 'container', 'inspect', '--format', CONTAINER_PROJECTION, cid], end))

    def absent(self, cid, name, end):
        require(self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + cid], end) == b'', 'GUARD_CID_PRESENT')
        require(self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'name=^/' + name + '$'], end) == b'', 'GUARD_NAME_PRESENT')


class Guardian:
    """One unsettled role; exact registered-CID authority survives controller loss."""
    def __init__(self, run, hard_end, controller, docker, clock=time.monotonic):
        require(type(run) is str and len(run) == 32 and all(c in '0123456789abcdef' for c in run), 'GUARD_RUN_REFUSED')
        require(type(controller) is dict and identifier(controller.get('Id')), 'GUARD_CONTROLLER_REFUSED')
        self.run, self.hard_end, self.controller = run, hard_end, controller
        self.docker, self.clock = docker, clock
        self.work_end = self.cleanup_end = None
        self.slot = None
        self.completed = []
        self.failed = False
        self.sealed = self.read = self.released = False
        self.ready_end = min(clock() + 20, hard_end - SHUTDOWN - CLEANUP)
        self.observations = []
        self.record_sink = None
        self.sequence = 0
        self.last_message = clock()

    def configure(self, work, whole):
        require(self.work_end is None and self.clock() < self.ready_end, 'GUARD_CONFIG_ORDER')
        self.work_end, self.cleanup_end = compose_deadlines(self.hard_end, work, whole, self.clock())

    def healthy_reserve(self, operation):
        require(not self.failed and self.work_end is not None and self.clock() < self.work_end
                and self.clock() + operation + CLEANUP <= self.cleanup_end, 'GUARD_START_RESERVE')

    def same_source(self, item, expected):
        require(type(item) is dict and set(item) == set(expected), 'GUARD_PROJECTION_REFUSED')
        require({key: value for key, value in item.items() if key != 'State'} ==
                {key: value for key, value in expected.items() if key != 'State'}, 'GUARD_SOURCE_DIFFERS')
        state = item['State']
        require(type(state) is dict and set(state) == {'Status', 'Running', 'OOMKilled', 'ExitCode'}
                and state['OOMKilled'] is False and type(state['ExitCode']) is int and 0 <= state['ExitCode'] <= 255, 'GUARD_STATE_REFUSED')
        ordinal = len(self.observations) + 1
        if self.record_sink is not None:
            self.record_sink(ordinal, item)
        self.observations.append({'ordinal': ordinal, 'cid': item['Id'], 'state': state})
        return state

    def intent(self, role, expected):
        self.healthy_reserve(20)
        require(self.slot is None and len(self.completed) < len(ROLES) and role == ROLES[len(self.completed)],
                'GUARD_SERIAL_ROLE_REFUSED')
        name = 'factory-' + self.run + '-' + role
        require(type(expected) is dict and set(expected) == {'Id', 'Name', 'Image', 'State', 'User', 'Entrypoint', 'Cmd', 'Healthcheck', 'Labels', 'Mounts', 'HostConfig', 'Networks'}
                and type(expected['Image']) is str and expected['Image'].startswith('sha256:') and identifier(expected['Image'][7:])
                and type(expected['Cmd']) is list and expected['Cmd'] and type(expected['Entrypoint']) is list and expected['Entrypoint']
                and type(expected['Mounts']) is list and type(expected['HostConfig']) is dict
                and expected.get('Name') == '/' + name
                and expected.get('Labels') == {'io.sbarbase.owner': 'sbarbase-fixture-factory-' + self.run}
                and expected.get('Id') is None and expected.get('State') ==
                    {'Status': 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0}, 'GUARD_INTENT_REFUSED')
        require(self.docker.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'name=^/' + name + '$'], self.work_end) == b'',
                'GUARD_PRECREATE_PRESENT')
        self.slot = {'role': role, 'name': name, 'expected': expected, 'cid': None, 'start_end': None, 'removing': False}

    def register(self, cid):
        self.healthy_reserve(2)
        require(self.slot is not None and self.slot['cid'] is None and identifier(cid), 'GUARD_REGISTER_ORDER')
        expected = {**self.slot['expected'], 'Id': cid}
        state = self.same_source(self.docker.inspect(cid, self.work_end), expected)
        require(state == {'Status': 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0}, 'GUARD_REGISTER_STATE')
        self.slot['cid'], self.slot['expected'] = cid, expected

    def permit(self):
        self.healthy_reserve(RUNTIME)
        require(self.slot is not None and self.slot['cid'] is not None and self.slot['role'] != 'candidate'
                and self.slot['start_end'] is None, 'GUARD_START_ORDER')
        state = self.same_source(self.docker.inspect(self.slot['cid'], self.work_end), self.slot['expected'])
        require(state['Status'] == 'created' and state['Running'] is False, 'GUARD_START_STATE')
        self.slot['start_end'] = min(self.clock() + RUNTIME, self.work_end)
        return self.slot['start_end']

    def removal(self):
        require(not self.failed and self.slot is not None and self.slot['cid'] is not None, 'GUARD_REMOVE_ORDER')
        state = self.same_source(self.docker.inspect(self.slot['cid'], self.cleanup_end), self.slot['expected'])
        require(state['Running'] is False and state['Status'] in ('created', 'exited') and state['ExitCode'] == 0,
                'GUARD_NORMAL_TERMINAL_REFUSED')
        if self.slot['role'] == 'candidate':
            require(state['Status'] == 'created', 'GUARD_CANDIDATE_STARTED')
        self.slot['removing'] = True

    def absence(self):
        require(not self.failed and self.slot is not None and self.slot['removing'], 'GUARD_ABSENCE_ORDER')
        self.docker.absent(self.slot['cid'], self.slot['name'], self.cleanup_end)
        role = self.slot['role']
        self.completed.append(role)
        self.slot = None
        return role

    def controller_alive(self):
        end = self.work_end if self.work_end is not None else self.ready_end
        item = self.docker.inspect(self.controller['Id'], end)
        state = self.same_source(item, self.controller)
        require(state['Status'] == 'running' and state['Running'] is True, 'GUARD_CONTROLLER_GONE')

    def tick(self):
        if self.failed:
            return
        try:
            self.controller_alive()
            require(self.clock() < (self.work_end if self.work_end is not None else self.ready_end)
                    and self.clock() < self.last_message + 25, 'GUARD_WORK_EXPIRED')
            if self.slot is not None and self.slot['start_end'] is not None and not self.slot['removing']:
                require(self.clock() < self.slot['start_end'], 'GUARD_HELPER_EXPIRED')
        except (StartupRefusal, OSError, ValueError, subprocess.SubprocessError):
            self.failed = True

    def cleanup(self):
        """Independent failure path, never converts forced stop to verification."""
        self.failed = True
        slot = self.slot
        if slot is None:
            return {'status': 'REFUSED', 'remote': 'ABSENT'}
        if slot['cid'] is None:
            return {'status': 'REFUSED', 'remote': 'UNRESOLVED_CREATE', 'name': slot['name']}
        end = self.cleanup_end if self.cleanup_end is not None else self.hard_end - SHUTDOWN
        try:
            cid = slot['cid']
            state = self.same_source(self.docker.inspect(cid, end), slot['expected'])
            if slot['role'] == 'candidate':
                require(state['Status'] == 'created' and state['Running'] is False and state['ExitCode'] == 0, 'GUARD_CANDIDATE_STARTED')
            elif state['Running'] is True:
                require(state['Status'] == 'running', 'GUARD_UNEXPECTED_RUNNING')
                require(self.docker.call(['docker', 'kill', '--signal=KILL', cid], end) == (cid + '\n').encode(), 'GUARD_KILL_REFUSED')
                raw = self.docker.call(['docker', 'wait', cid], end)
                require(raw == b'137\n', 'GUARD_KILL_WAIT_REFUSED')
                state = self.same_source(self.docker.inspect(cid, end), slot['expected'])
                require(state['Status'] == 'exited' and state['Running'] is False, 'GUARD_KILL_TERMINAL_REFUSED')
            else:
                require(state['Status'] in ('created', 'exited'), 'GUARD_TERMINAL_REFUSED')
            require(self.docker.call(['docker', 'rm', cid], end) == (cid + '\n').encode(), 'GUARD_REMOVE_REFUSED')
            self.docker.absent(cid, slot['name'], end)
            self.slot = None
            return {'status': 'REFUSED', 'remote': 'ABSENT', 'cid': cid}
        except (StartupRefusal, OSError, ValueError, subprocess.SubprocessError):
            return {'status': 'REFUSED', 'remote': 'UNRESOLVED', 'cid': slot['cid']}

    def message(self, message):
        require(type(message) is dict and set(message) == {'sequence', 'operation', 'data'}
                and type(message['sequence']) is int and message['sequence'] == self.sequence + 1, 'GUARD_SEQUENCE_REFUSED')
        operation, data = message['operation'], message['data']
        self.sequence += 1
        self.last_message = self.clock()
        require(type(data) is dict, 'GUARD_MESSAGE_REFUSED')
        if operation == 'sample':
            require(not data and self.work_end is None, 'GUARD_SAMPLE_ORDER')
            return {'sample': self.clock(), 'hard_end': self.hard_end}
        if operation == 'configure':
            require(set(data) == {'work', 'whole'}, 'GUARD_CONFIG_FIELDS')
            self.configure(data['work'], data['whole'])
            return {'work_end': self.work_end, 'cleanup_end': self.cleanup_end}
        if operation == 'intent':
            require(set(data) == {'role', 'expected'}, 'GUARD_INTENT_FIELDS')
            self.intent(data['role'], data['expected'])
        elif operation == 'register':
            require(set(data) == {'cid'}, 'GUARD_REGISTER_FIELDS')
            self.register(data['cid'])
        elif operation == 'permit':
            require(not data, 'GUARD_PERMIT_FIELDS')
            return {'start_end': self.permit()}
        elif operation == 'removal':
            require(not data, 'GUARD_REMOVAL_FIELDS')
            self.removal()
        elif operation == 'absence':
            require(not data, 'GUARD_ABSENCE_FIELDS')
            return {'absent_role': self.absence()}
        elif operation == 'seal':
            require(not data and not self.failed and self.slot is None and self.completed == list(ROLES)
                    and self.clock() + 6 <= self.cleanup_end, 'GUARD_SEAL_REFUSED')
            self.sealed = True
        elif operation == 'read':
            require(not data and self.sealed and not self.failed and self.clock() + 4 <= self.cleanup_end, 'GUARD_READER_REFUSED')
            self.read = True
            return {'status': 'GUARD_COMPLETE', 'roles': self.completed, 'pending': False}
        elif operation == 'release':
            require(not data and self.read and not self.failed and self.clock() + 2 <= self.cleanup_end, 'GUARD_RELEASE_REFUSED')
            self.released = True
        elif operation == 'cancel':
            require(not data, 'GUARD_CANCEL_FIELDS')
            self.failed = True
        else:
            raise StartupRefusal('GUARD_OPERATION_REFUSED')
        return {'status': 'ACK', 'operation': operation}


def control(message):
    """Public control writer/reader runs inside guardian containment, never materials."""
    require(type(message) is dict and type(message.get('sequence')) is int and 1 <= message['sequence'] <= 64,
            'GUARD_CONTROL_REFUSED')
    ordinal = message['sequence']
    public_write(CONTROL / f'message-{ordinal}.json', message)
    end = time.monotonic() + 1.0
    reply = CONTROL / f'reply-{ordinal}.json'
    while not reply.exists():
        require(time.monotonic() < end, 'GUARD_CONTROL_TIMEOUT')
        time.sleep(.01)
    value = public_read(reply)
    require(value.get('status') == 'ACK', 'GUARD_CONTROL_REJECTED')
    # Private/public descriptors are closed before stdout framing.
    print(json.dumps(value, sort_keys=True, separators=(',', ':')))
    return 0



def owned_reader(end, broker):
    """Only the wrapper owns the reader's distinct session and bounded pipes."""
    require(broker is not None, 'GUARD_READER_BROKER_REQUIRED')
    finish = min(end, time.monotonic() + COMMAND)
    code, stdout, stderr = broker.capture(['/usr/bin/python3', str(Path(__file__).resolve()), 'reader'], finish, 4096)
    require(code == 0 and stderr == b'' and 0 < len(stdout) <= 4096
            and stdout.count(b'\n') == 1 and stdout.endswith(b'\n'), 'GUARD_OWNED_READER_REFUSED')
    return json.loads(stdout)


def reader():
    value = public_read(CONTROL / 'sealed-frame.json')
    require(value == {'status': 'GUARD_COMPLETE', 'roles': list(ROLES), 'pending': False}, 'GUARD_SEAL_FRAME_REFUSED')
    # Public file descriptors close in public_read before the final frame.
    print(json.dumps(value, sort_keys=True, separators=(',', ':')))
    return 0

def serve(run, descriptor, broker_descriptor):
    require(type(descriptor) is int and descriptor >= 3, 'GUARD_WRAPPER_DESCRIPTOR')
    try:
        value = os.read(descriptor, 131073)
    finally:
        os.close(descriptor)
    require(value.endswith(b'\n') and len(value) <= 131072, 'GUARD_WRAPPER_FRAME')
    initial = json.loads(value)
    require(set(initial) == {'hard_end', 'controller'} and finite(initial['hard_end']), 'GUARD_WRAPPER_FRAME')
    import socket
    require(type(broker_descriptor) is int and broker_descriptor >= 3 and broker_descriptor != descriptor, 'GUARD_BROKER_DESCRIPTOR')
    channel = socket.socket(fileno=broker_descriptor)
    broker = SocketBroker(channel)
    try:
        guard = Guardian(run, initial['hard_end'], initial['controller'], GuardDocker(broker=broker))
        public_write(CONTROL / 'ready.json', {'status': 'READY', 'hard_end': guard.hard_end})
        written = [0]
        def save_projection(ordinal, item):
            written[0] += len(json.dumps(item, separators=(',', ':')).encode('ascii'))
            require(written[0] <= 2097152, 'GUARD_EVIDENCE_LIMIT')
            public_write(CONTROL / f'observation-{ordinal}.json', item)
        guard.record_sink = save_projection
        next_controller = guard.clock()
        while not guard.released and not guard.failed:
            if guard.clock() >= next_controller:
                guard.tick()
                next_controller = guard.clock() + .25
            path = CONTROL / f'message-{guard.sequence + 1}.json'
            if path.exists():
                sequence = guard.sequence + 1
                try:
                    message = public_read(path)
                    reply = guard.message(message)
                    if message['operation'] == 'read':
                        public_write(CONTROL / 'sealed-frame.json', reply)
                        reply = owned_reader(guard.cleanup_end, broker)
                    public_write(CONTROL / f'reply-{sequence}.json', {'status': 'ACK', 'data': reply})
                except (StartupRefusal, OSError, ValueError, TypeError):
                    guard.failed = True
                    public_write(CONTROL / f'reply-{sequence}.json', {'status': 'REFUSED'})
            time.sleep(.01)
        outcome = {'status': 'GUARD_COMPLETE', 'roles': guard.completed} if guard.released else guard.cleanup()
        public_write(CONTROL / 'outcome.json', {**outcome, 'observations': guard.observations, 'commands': guard.docker.commands})
        return 0 if guard.released and not guard.failed else 1
    finally:
        channel.close()


def broker_command(argv, run):
    reader_command = ['/usr/bin/python3', str(Path(__file__).resolve()), 'reader']
    if argv == reader_command:
        return True
    if type(argv) is not list or len(argv) < 3 or argv[0] != 'docker':
        return False
    if argv[1] in ('wait', 'rm'):
        return len(argv) == 3 and identifier(argv[2])
    if argv[1] == 'kill':
        return len(argv) == 4 and argv[2] == '--signal=KILL' and identifier(argv[3])
    if argv[1:3] == ['container', 'inspect']:
        return len(argv) == 6 and argv[3:5] == ['--format', CONTAINER_PROJECTION] and identifier(argv[5])
    if argv[1:5] == ['ps', '-aq', '--no-trunc', '--filter'] and len(argv) == 6:
        query = argv[5]
        if query.startswith('id='):
            return identifier(query[3:])
        return query in ('name=^/factory-' + run + '-' + role + '$' for role in ROLES)
    return False


def establish_subreaper():
    import ctypes
    # Linux PR_SET_CHILD_SUBREAPER, limited to this daemon guardian process.
    require(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0, 'WRAPPER_SUBREAPER_UNAVAILABLE')


def wrapper(run, duration, controller):
    import socket
    import stat
    require(type(duration) is int and 40 < duration <= 240, 'GUARD_WRAPPER_DURATION')
    require(stat.S_ISSOCK(os.stat('/var/run/docker.sock', follow_symlinks=False).st_mode), 'GUARD_SOCKET_TYPE_REFUSED')
    meta = os.stat(CONTROL, follow_symlinks=False)
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and meta.st_gid == 0, 'GUARD_PUBLIC_DIRECTORY_REFUSED')
    os.chmod(CONTROL, 0o700, follow_symlinks=False)
    establish_subreaper()
    hard_end = time.monotonic() + duration
    owner = SessionOwner(hard_end)
    read, write = os.pipe2(os.O_CLOEXEC)
    parent, child_channel = socket.socketpair()
    child = None
    selected = selectors.DefaultSelector()
    request = bytearray()
    code = 1
    try:
        child = owner.spawn(['/usr/bin/python3', str(Path(__file__).resolve()), 'serve', '--run', run,
            '--descriptor', str(read), '--broker-descriptor', str(child_channel.fileno())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(read, child_channel.fileno()))
        os.close(read)
        read = None
        child_channel.close()
        frame = json.dumps({'hard_end': hard_end, 'controller': controller}, separators=(',', ':')).encode() + b'\n'
        require(len(frame) < 4096 and os.write(write, frame) == len(frame), 'GUARD_WRAPPER_PIPE_WRITE')
        os.close(write)
        write = None
        parent.setblocking(False)
        selected.register(parent, selectors.EVENT_READ)
        def serve_ended():
            return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        owner.stop = lambda: serve_ended() or time.monotonic() >= hard_end - SHUTDOWN - .25
        while time.monotonic() < hard_end - SHUTDOWN - .25:
            status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if status is not None:
                code = status.si_status if status.si_code == os.CLD_EXITED else 1
                break
            for key, _ in selected.select(.01):
                value = parent.recv(8192)
                require(value, 'BROKER_SERVE_DISCONNECTED')
                request.extend(value)
                require(len(request) <= 262144, 'BROKER_REQUEST_LIMIT')
                if request.endswith(b'\n'):
                    require(request.count(b'\n') == 1, 'BROKER_REQUEST_FRAME')
                    packet = json.loads(request)
                    require(type(packet) is dict and set(packet) == {'argv', 'end', 'limit'}
                            and finite(packet['end']), 'BROKER_REQUEST_FRAME')
                    argv = packet['argv']
                    require(broker_command(argv, run), 'BROKER_SOURCE_COMMAND_REFUSED')
                    native_code, stdout, stderr = owner.capture(argv, packet['end'], packet['limit'])
                    reply = json.dumps({'status': 'CAPTURED', 'code': native_code, 'stdout': stdout.hex(), 'stderr': stderr.hex()}, separators=(',', ':')).encode() + b'\n'
                    require(len(reply) <= 524288 and not serve_ended(), 'BROKER_REPLY_REFUSED')
                    parent.settimeout(max(.001, min(packet['end'], hard_end - SHUTDOWN) - time.monotonic()))
                    parent.sendall(reply)
                    require(time.monotonic() < min(packet['end'], hard_end - SHUTDOWN), 'BROKER_REPLY_DEADLINE')
                    parent.setblocking(False)
                    request.clear()
    finally:
        # Shutdown starts within the reserved window, never at hard_end+.25.
        # Every serve/reader/CLI Popen handle is owned here, including an exited
        # leader with descendants in its still-reserved process group.
        try:
            owner.shutdown()
        finally:
            close_failed = False
            for resource in (selected, parent, child_channel):
                try:
                    resource.close()
                except OSError:
                    close_failed = True
            for fd in (read, write):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        close_failed = True
            require(not close_failed and time.monotonic() < hard_end, 'WRAPPER_CLOSE_DEADLINE')
        require(time.monotonic() < hard_end, 'WRAPPER_CLOSE_DEADLINE')
    return 0 if code == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('wrapper', 'serve', 'control', 'reader'))
    parser.add_argument('--run')
    parser.add_argument('--duration', type=int)
    parser.add_argument('--controller')
    parser.add_argument('--descriptor', type=int)
    parser.add_argument('--broker-descriptor', type=int)
    parser.add_argument('--message')
    args = parser.parse_args(argv)
    def expired(*_):
        raise StartupRefusal('GUARD_CONTROL_DEADLINE')
    if args.mode == 'control':
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, 1.5)
    try:
        if args.mode == 'reader':
            return reader()
        if args.mode == 'control':
            require(args.message is not None and len(args.message) <= 131072, 'GUARD_MESSAGE_LIMIT')
            return control(json.loads(args.message))
        if args.mode == 'serve':
            return serve(args.run, args.descriptor, args.broker_descriptor)
        require(args.controller is not None and len(args.controller) <= 32768, 'GUARD_CONTROLLER_LIMIT')
        return wrapper(args.run, args.duration, json.loads(args.controller))
    except (StartupRefusal, OSError, ValueError, TypeError, subprocess.SubprocessError):
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
