"""In-place handover of the console process (docs/engineering/HANDOVER.md), behind SBARBASE_HANDOVER=1.

Off, nothing here runs: lab/dev.py keeps today's planned restart (exit 42, kept runtime). On, a
code only upgrade does not stop the Bun process that serves every application. The old
supervisor replaces its own program with the new version's (`execve`, same process id), the old
Bun keeps serving, and the new version's Bun (the candidate) joins the shared ports, steers
every new connection to itself with a one instruction reuseport program, and only then does the
old Bun retire.

This module holds what both supervisors share: the protocol number and the format hash, the
qualification (Q1 to Q12), the record the old supervisor leaves (`handover.json`), the adoption
and ending of the old Bun (`Retiring`), the probe, promote, steer, retire and confirm sequence
(`Promotion`), and the /proc and socket helpers. Imported at the top of lab/dev.py, so the old
supervisor uses the running version's copy. Nothing here is production ready.
"""
import contextlib
import ctypes
import hashlib
import http.client
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import console_build_check
import kept_runtime
# This version's own copy, loaded when the supervisor starts: Q4 judges the move with the code that is running,
# not with the target's, which the checkout holds by the time the question is asked.
import release_channel

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab/upstream'
UPGRADE_STATE = ROOT / '.lab/upgrades/state.json'
FORMATS_FILE = 'src/control/handover-formats.json'

# The handover protocol number (the signals, records and headers named in the design, and the
# format of every file both Bun processes read while they overlap). The old supervisor reads the
# target's from `git show <to>:lab/handover.py` as text, one match of the next line.
PROTOCOL = 1
# Append only: the sha256 of the canonical JSON of the required keys that FORMATS_FILE declares
# (formats_hash). One protocol per line; a hash never changes once a release carries it.
FORMATS = {
    1: '4335f6e38f924c9edbcc7065a420970f32a84e323bade583342d67f2cc93e3e5',
}

SWITCH = 'SBARBASE_HANDOVER'
EXEC_MARKER = 'SBARBASE_HANDOVER_EXEC'
CANDIDATE_MARKER = 'SBARBASE_CANDIDATE'
CREDIT = kept_runtime.ADMISSION_CREDIT
END_GRACE_SECONDS = 20
PROMOTE_SECONDS = 10
VERIFY_CONNECTIONS = 4
VERIFY_TIMEOUT = 1
HANDOVER_SECONDS = 90
MEMORY_MARGIN_MIB = 256
ACCEPT_GRACE_SECONDS = .3
SOL_SOCKET = 1
SO_ATTACH_REUSEPORT_CBPF = 51
SO_DETACH_REUSEPORT_BPF = 68
CANDIDATE_EXIT = 75
RECORD_SECONDS = 2
# Connections the steering trial (Q8) makes: with the program ignored they spread over both sockets by hash, and
# all of them landing on one is 1 in 2 ** (n - 1).
STEERING_TRIAL_CONNECTIONS = 32
# How long the trial waits for a connection to be made and for the second socket to have one to accept; a loaded
# host can stall a loopback connect for seconds (SYN retransmits), and a slow answer is not a refused program.
STEERING_WAIT_SECONDS = 10
STEERING_ATTEMPTS = 2
# The old console process rewrites its listener record with `frozen` at the next tick of its 2 s timers
# after handover.json names it; H2 waits this long for that before it calls the handover failed.
FREEZE_WAIT_SECONDS = 5


class HandoverFailed(Exception):
    """A handover failed at `step` although the version passed its round: no way back (W3, W5)."""

    def __init__(self, step, reason):
        super().__init__(f'The handover failed at {step}: {reason}')
        self.step, self.reason = step, reason


class Refused(Exception):
    """A qualification condition that does not hold; the text names it."""


def enabled(environ=os.environ):
    return environ.get(SWITCH) == '1'


def retire_wait_seconds(environ=os.environ):
    """SBARBASE_DRAIN_MS / 1000 + 0.3 + 5 (20.3 s by default, at most 24.3 s)."""
    text = environ.get('SBARBASE_DRAIN_MS', '')
    milliseconds = int(text) if re.fullmatch(r'\d{1,5}', text) and int(text) <= 19000 else 15000
    return milliseconds / 1000 + ACCEPT_GRACE_SECONDS + 5


# The memory the old console process holds, which the stages that run beside it count as available
# (lab/kept_runtime.py admission_credit_mib, read by each admission).
credit_mib = kept_runtime.admission_credit_mib


# Files -------------------------------------------------------------------------------------------

def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def write_json(path, value):
    """A private temporary file, fsync, rename, directory fsync, mode 0600."""
    path = Path(path)
    temporary = path.with_name(path.name + '.pending')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(value, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def record_path(state=STATE):
    return Path(state) / 'handover.json'


def listeners_path(pid, state=STATE):
    return Path(state) / f'listeners-{pid}.json'


def failed_path(state=STATE):
    return Path(state) / 'handover-failed.json'


def write_failed(to, step, reason, state=STATE, now=None):
    write_json(failed_path(state), {'to': to, 'step': step, 'reason': str(reason), 'at': int(time.time() if now is None else now)})


def failed_for(to, state=STATE):
    """Whether a handover to this commit failed once: it is never tried again for it (Q11)."""
    record = read_json(failed_path(state))
    return isinstance(record, dict) and record.get('to') == to


# The format rule ---------------------------------------------------------------------------------

def required_part(declared):
    return {name: entry.get('required', {}) for name, entry in declared.items()}


def formats_hash(declared):
    """The sha256 of the canonical JSON of the required keys, their types and meanings."""
    return hashlib.sha256(json.dumps(required_part(declared), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def git_show(root, commit, path):
    result = subprocess.run(['git', 'show', f'{commit}:{path}'], cwd=root, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise Refused(f'{path} does not exist at {commit[:12]}')
    return result.stdout


def text_protocol(text):
    """The PROTOCOL number of a lab/handover.py read as text: exactly one line `PROTOCOL = <n>`."""
    found = re.findall(r'^PROTOCOL = (\d+)$', text, re.M)
    if len(found) != 1:
        raise Refused('lab/handover.py does not declare exactly one PROTOCOL line')
    return int(found[0])


def text_format_hash(text, protocol):
    """The hash on `protocol`'s line of FORMATS in a lab/handover.py read as text."""
    found = re.findall(rf"^    {protocol}: '([0-9a-f]{{64}})',$", text, re.M)
    if len(found) != 1:
        raise Refused(f'lab/handover.py does not declare exactly one format hash for protocol {protocol}')
    return found[0]


def declares_all(current, target):
    """Whether the target declares every key `current` declares, required and optional, with the
    identical declaration. It may only add optional keys."""
    if not isinstance(target, dict):
        return False
    for name, entry in current.items():
        other = target.get(name)
        if not isinstance(other, dict):
            return False
        for section in ('required', 'optional'):
            before, after = entry.get(section, {}), other.get(section, {})
            if any(key not in after or after[key] != value for key, value in before.items()):
                return False
        if set(other.get('required', {})) - set(entry.get('required', {})):
            return False
    return True


def load_declared(root=ROOT):
    try:
        return json.loads((Path(root) / FORMATS_FILE).read_text())
    except (OSError, ValueError):
        return None


# This version's declaration, read when the supervisor started: the checkout has moved to the target by
# the time Q3 compares, so the file there is the target's.
DECLARED = load_declared()


def current_declared():
    if DECLARED is None:
        raise Refused(f'{FORMATS_FILE} was not readable when this version started')
    return DECLARED


# /proc -------------------------------------------------------------------------------------------

def stat_fields(pid, proc='/proc'):
    """(parent, start time in clock ticks) of a process, or None when it is gone."""
    try:
        text = (Path(proc) / str(pid) / 'stat').read_text()
        fields = text[text.rindex(')') + 2:].split()
        return int(fields[1]), int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def vm_rss_mib(pid, proc='/proc'):
    try:
        for line in (Path(proc) / str(pid) / 'status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return -(-int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    raise Refused(f'the memory of process {pid} cannot be read')


def hex_address(host):
    """(proc file, address as /proc/net prints it) of a listener's bind address."""
    # `localhost` is 127.0.0.1, as src/http/handover.ts procAddress reads it; any other name is not an address.
    host = '127.0.0.1' if host == 'localhost' else host
    try:
        packed = socket.inet_pton(socket.AF_INET, host)
        return 'tcp', packed[::-1].hex().upper()
    except (OSError, TypeError):
        pass
    try:
        packed = socket.inet_pton(socket.AF_INET6, host)
    except (OSError, TypeError):
        raise Refused(f'{host} is not an IP address') from None
    return 'tcp6', b''.join(packed[at:at + 4][::-1] for at in range(0, 16, 4)).hex().upper()


def listening(host, port, proc='/proc'):
    """The inodes of every socket in state 0A (listen) at host:port, from /proc/net/tcp or tcp6."""
    name, address = hex_address(host)
    found = []
    try:
        lines = (Path(proc) / 'net' / name).read_text().splitlines()[1:]
    except OSError as error:
        raise Refused(f'/proc/net/{name} cannot be read ({error.__class__.__name__})') from None
    for line in lines:
        fields = line.split()
        if len(fields) > 9 and fields[3] == '0A' and fields[1] == f'{address}:{port:04X}':
            found.append(int(fields[9]))
    return found


def descriptors(pid, proc='/proc'):
    """The inodes of the socket descriptors of a process (/proc/<pid>/fd/* reading socket:[inode])."""
    found = set()
    try:
        names = os.listdir(Path(proc) / str(pid) / 'fd')
    except OSError as error:
        raise Refused(f'/proc/{pid}/fd cannot be read ({error.__class__.__name__})') from None
    for name in names:
        try:
            match = re.fullmatch(r'socket:\[(\d+)\]', os.readlink(Path(proc) / str(pid) / 'fd' / name))
        except OSError:
            continue
        if match:
            found.add(int(match.group(1)))
    return found


def clean_group(record, pid, proc='/proc'):
    """{name: inode} for a process's fixed listeners (Q7, H2): at each address and port exactly one
    socket in state 0A, and its inode is one of the process's own descriptors."""
    owned = descriptors(pid, proc)
    found = {}
    for item in listener_list(record):
        inodes = listening(item['host'], item['port'], proc)
        if len(inodes) != 1 or inodes[0] not in owned:
            raise Refused(f"{item['name']} on {item['host']}:{item['port']} is not held by process {pid} alone")
        found[item['name']] = inodes[0]
    return found


def listener_list(record):
    """The listeners of a record, or raises: name, host and port of each, names unique."""
    items = record.get('listeners') if isinstance(record, dict) else None
    if not isinstance(items, list):
        raise Refused('the listener record has no list')
    names = set()
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('host'), str)
                or type(item.get('port')) is not int or item['name'] in names):
            raise Refused('the listener record is malformed')
        names.add(item['name'])
    return items


def group_state(old, new, inode_old, old_pid, candidate_pid, proc='/proc'):
    """H4: the sockets of every listener in either record. Shared: exactly two, `inode_old` (still a
    descriptor of the old Bun) and one more that the candidate holds. Only the candidate's: exactly one,
    its own. Only the old Bun's is left to close with it. Raises Refused on any other count or owner,
    a third member included, and on a listener whose address changed."""
    old_owned, new_owned = descriptors(old_pid, proc), descriptors(candidate_pid, proc)
    wanted = {item['name']: item for item in listener_list(new)}
    seen = {}
    for item in listener_list(old):
        other = wanted.get(item['name'])
        if other is None:
            continue
        if (other['host'], other['port']) != (item['host'], item['port']):
            raise Refused(f"{item['name']} moved from {item['host']}:{item['port']} to {other['host']}:{other['port']}")
        inodes = listening(item['host'], item['port'], proc)
        mine = inode_old.get(item['name'])
        extra = [inode for inode in inodes if inode != mine]
        if len(inodes) != 2 or mine not in inodes or mine not in old_owned or len(extra) != 1 or extra[0] not in new_owned:
            raise Refused(f"{item['name']} on {item['host']}:{item['port']} is not shared by exactly the two Bun processes")
        seen[item['name']] = (mine, extra[0])
    for name, item in wanted.items():
        if name in seen:
            continue
        inodes = listening(item['host'], item['port'], proc)
        if len(inodes) != 1 or inodes[0] not in new_owned:
            raise Refused(f"{name} on {item['host']}:{item['port']} is not held by the candidate alone")
        seen[name] = (None, inodes[0])
    return seen


# Steering ----------------------------------------------------------------------------------------

class _Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint16), ('jt', ctypes.c_uint8), ('jf', ctypes.c_uint8), ('k', ctypes.c_uint32)]


def steering_program():
    """(sock_fprog bytes, the filter that must stay alive while they are used): one instruction,
    BPF_RET|BPF_K 1, which sends every new connection of the group to its second member."""
    code = _Filter(0x06, 0, 0, 1)
    return struct.pack('=HxxxxxxQ', 1, ctypes.addressof(code)), code


def steering_works(wait=None, attempts=None):
    """Q8: two SO_REUSEPORT listeners on a loopback port, the program attached to the second one through
    setsockopt on its own descriptor, and every one of several connections lands on it. Proves that the kernel
    and the seccomp profile allow steering where the candidate will. A failure is tried again (`attempts`): a
    stalled loopback connect on a loaded host is not a refused program, and a program the kernel ignores fails
    every attempt."""
    wait = STEERING_WAIT_SECONDS if wait is None else wait
    return any(steering_trial(wait) for _ in range(STEERING_ATTEMPTS if attempts is None else attempts))


def steering_trial(wait):
    first = second = None
    clients = []
    try:
        # A port nobody listens on, found without SO_REUSEPORT: an ephemeral bind with it may be given a port that
        # another process's reuseport listener already shares, and the group would have a third member.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        first = socket.socket()
        first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        first.bind(('127.0.0.1', port))
        second = socket.socket()
        second.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        second.bind(('127.0.0.1', port))
        first.listen(STEERING_TRIAL_CONNECTIONS * 2)
        second.listen(STEERING_TRIAL_CONNECTIONS * 2)
        program, keep = steering_program()
        second.setsockopt(SOL_SOCKET, SO_ATTACH_REUSEPORT_CBPF, program)
        for _ in range(STEERING_TRIAL_CONNECTIONS):
            clients.append(socket.create_connection(('127.0.0.1', port), timeout=wait))
        del keep
        landed = 0
        for _ in range(STEERING_TRIAL_CONNECTIONS):
            if not select.select([second], [], [], wait)[0]:
                return False
            second.accept()[0].close()
            landed += 1
        return landed == STEERING_TRIAL_CONNECTIONS and not select.select([first], [], [], 0)[0]
    except OSError:
        return False
    finally:
        for item in (*clients, second, first):
            if item is not None:
                with contextlib.suppress(OSError):
                    item.close()


def detach_works(fd_socket):
    """The detach form: option 68 with a 4 byte int 0 (an option length below sizeof(int) is EINVAL)."""
    fd_socket.setsockopt(SOL_SOCKET, SO_DETACH_REUSEPORT_BPF, struct.pack('=i', 0))


# Memory ------------------------------------------------------------------------------------------

def memory_refusal(rss_mib, meminfo='/proc/meminfo', cgroup_file='/proc/self/cgroup', cgroup_root='/sys/fs/cgroup'):
    """Q9: room for two Bun processes: MemAvailable, and where this process's cgroup has a memory.max
    other than `max` also memory.max minus memory.current, are each at least the old Bun's VmRSS plus
    MEMORY_MARGIN_MIB. The reason, or None."""
    need = rss_mib + MEMORY_MARGIN_MIB
    for line in Path(meminfo).read_text().splitlines():
        if line.startswith('MemAvailable:'):
            if int(line.split()[1]) // 1024 < need:
                return f'MemAvailable is below {need} MiB'
            break
    else:
        return 'MemAvailable cannot be read'
    try:
        path = next(line.split(':', 2)[2].strip() for line in Path(cgroup_file).read_text().splitlines() if line.startswith('0::'))
        folder = Path(cgroup_root) / path.lstrip('/')
        limit = (folder / 'memory.max').read_text().strip()
        if limit != 'max':
            free = (int(limit) - int((folder / 'memory.current').read_text())) // 1024 ** 2
            if free < need:
                return f'the cgroup has {free} MiB left, below {need} MiB'
    except (OSError, StopIteration, ValueError, IndexError):
        pass
    return None


# Qualification (Q1 to Q12) -----------------------------------------------------------------------

class Plan:
    """What the old supervisor takes through the exec: the old Bun (`server`), the commit the exec
    starts (`to`) and the record that names them."""

    def __init__(self, server, to, record):
        self.server, self.to, self.record = server, to, record


def listeners_ok(record, pid):
    return (isinstance(record, dict) and record.get('pid') == pid and record.get('protocol') == PROTOCOL
            and record.get('role') == 'serving')


def refusal(supervisor, kind, completed, asked, status, head, environ=os.environ, root=ROOT, state=STATE,
            upgrade_state=UPGRADE_STATE, proc='/proc', meminfo='/proc/meminfo', cgroup=None, trial=None):
    """(reason, None) for the first condition that does not hold (Q1 to Q9, Q11 and Q12; Q10 is
    checked in lab/dev.py when the runtime is left), else (None, plan)."""
    # Q1
    if not enabled(environ):
        return 'Q1: SBARBASE_HANDOVER is not 1', None
    if kind != 'apply' or not completed:
        return 'Q1: not a completed upgrade', None
    # Q2
    if environ.get('SBARBASE_GUARDED') != '1':
        return 'Q2: no service manager runs this supervisor', None
    if asked is not None and asked.is_set():
        return 'Q2: a stop was asked for', None
    if (Path(state) / 'cutover-operation.json').exists():
        return 'Q2: a moved environment has a recovery target beside the source', None
    # Q11
    to = head
    if not isinstance(to, str) or failed_for(to, state):
        return 'Q11: a handover to this commit failed before', None
    # Q3
    target_text = git_show(root, to, 'lab/handover.py')
    if text_protocol(target_text) != PROTOCOL:
        return 'Q3: the target declares another protocol', None
    if text_format_hash(target_text, PROTOCOL) != FORMATS[PROTOCOL]:
        return 'Q3: the target declares other file formats', None
    if not declares_all(current_declared(), json.loads(git_show(root, to, FORMATS_FILE))):
        return 'Q3: the target changes a declared file format', None
    # Q4
    record = read_json(upgrade_state)
    if (not isinstance(record, dict) or record.get('phase') != 'applied' or record.get('moved') is not True
            or record.get('to') != to or record.get('from') != supervisor.head or record.get('changes') != []):
        return 'Q4: the upgrade is not a code only move to this commit', None
    if list(release_channel.pin_diffs(supervisor.head, to)):
        return 'Q4: a pinned image changes', None
    if release_channel.classify(supervisor.head, to)[0] != 'safe':
        return 'Q4: the release is not class safe', None
    if release_channel.stores_changed(supervisor.head, to):
        return 'Q4: a control store schema changes', None
    if isinstance(record.get('release'), dict) and 'class' in record['release'] and record['release']['class'] != 'safe':
        return 'Q4: the release record is not class safe', None
    # Q5
    port = environ.get('SBARBASE_CONSOLE_PORT', '')
    if not re.fullmatch(r'\d{1,5}', port) or int(port) == 0:
        return 'Q5: the console port is not pinned', None
    # Q6
    server = supervisor.server
    if server is None or status(server) is not None:
        return 'Q6: the console process is not running', None
    published = read_json(Path(state) / 'server.json')
    owned = read_json(listeners_path(server.pid, state))
    if (not isinstance(published, dict) or published.get('pid') != server.pid or published.get('protocol') != PROTOCOL
            or not listeners_ok(owned, server.pid)):
        return 'Q6: the running version does not declare the protocol', None
    # Q7
    try:
        clean_group(owned, server.pid, proc)
    except Refused as error:
        return f'Q7: {error}', None
    # Q8
    if not (trial or steering_works)():
        return 'Q8: steering does not work on this host', None
    # Q9
    reason = memory_refusal(vm_rss_mib(server.pid, proc), meminfo=meminfo, **(cgroup or {}))
    if reason:
        return f'Q9: {reason}', None
    # Q12
    if not (console_build_check.is_fresh(out=console_build_check.STAGED)[0] or console_build_check.is_fresh()[0]):
        return 'Q12: the start would build the console', None
    plan = Plan(server, to, {'protocol': PROTOCOL, 'from': supervisor.head, 'to': to, 'pid': server.pid,
                             'console_port': int(port), 'listeners': owned['listeners']})
    return None, plan


def qualifies(supervisor, kind, completed, asked, status, head=None, **options):
    """A Plan when every condition holds, else None (today's exit 42). Any exception inside counts as a
    condition that does not hold. Q10 is checked later, in main's finally."""
    environ = options.get('environ', os.environ)
    if not enabled(environ):
        return None
    try:
        why, plan = refusal(supervisor, kind, completed, asked, status, head if head is not None else supervisor_head(), **options)
    except Exception as error:
        print(f'The handover is not used: {error.__class__.__name__}: {error}', flush=True)
        return None
    if why:
        print(f'The handover is not used ({why}); the console restarts as it always has.', flush=True)
        return None
    return plan


def supervisor_head(root=ROOT):
    return kept_runtime.head(root)


# The old supervisor: A4 to A9 --------------------------------------------------------------------

def start_time(pid):
    return kept_runtime.started(pid)


def leave_for_exec(plan, asked, status, terminate, execve=os.execve, sigmask=signal.pthread_sigmask, state=STATE,
                   environ=os.environ, flush=lambda: (sys.stdout.flush(), sys.stderr.flush()), program=None):
    """A5 to A9. Returns only when the exec did not happen (the caller then exits 42, as a planned
    restart does today): a stop that arrived before the mask, the old Bun gone, or any error. It
    never returns when `execve` succeeded."""
    masked = False
    try:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('the exec must be called from the main thread')
        sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        masked = True
        if asked.is_set():
            raise _Cancelled
        if status(plan.server) is not None:
            raise _Cancelled
        write_json(record_path(state), {**plan.record, 'started': start_time(plan.server.pid), 'at': int(time.time())})
        flush()
        program = program or ['/usr/bin/python3', str(ROOT / 'lab/dev.py')]
        execve(program[0], program, {**environ, EXEC_MARKER: '1'})
    except _Cancelled:
        pass
    except Exception as error:
        print(f'The handover could not start ({error.__class__.__name__}: {error}); the console restarts as it always has.',
              file=sys.stderr, flush=True)
    # Not exec'd: the mask is lifted, the record removed and the old Bun stopped (A6, A9).
    if masked:
        sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})
    record_path(state).unlink(missing_ok=True)
    terminate(plan.server)
    return None


class _Cancelled(Exception):
    pass


# The new program: B1 to B3 and B13 ---------------------------------------------------------------

def enter(environ, stop_event, asked, install, sigmask=signal.pthread_sigmask):
    """B1. True when this program was started by a handover (the marker, removed from the
    environment so no child and no later exec inherits it). Then the SIGTERM and SIGINT handlers are
    installed first and only after them unblocked, so a stop that was pending across the exec reaches the
    handler and every child starts with an empty mask."""
    marked = environ.pop(EXEC_MARKER, None) == '1'
    if marked:
        install(stop_event, asked)
        sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})
    return marked


def unreaped(pid):
    """'running', 'exited' (a child not yet reaped) or 'gone' (not a child of ours, or reaped)."""
    try:
        result = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return 'gone'
    return 'running' if result is None else 'exited'


class Retiring:
    """The old Bun, which the new program adopts as its own child (B2). Inert without a pid."""

    def __init__(self, pid=None, started=None, state=STATE):
        self.pid, self.started, self.state = pid, started, Path(state)
        self.reaped = pid is None

    def __bool__(self):
        return self.pid is not None

    def exited(self):
        """True once the old Bun is reaped; reaps it when it exited on its own (H9)."""
        if self.reaped:
            return True
        try:
            result = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG)
        except ChildProcessError:
            result = True
        if result is None:
            return False
        self.reaped = True
        return True

    def kill_group(self):
        """SIGKILL to the old Bun's session, then reap it (H9 at RETIRE_WAIT_SECONDS)."""
        if self.reaped:
            return
        if unreaped(self.pid) == 'running':
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError):
            os.waitid(os.P_PID, self.pid, os.WEXITED)
        self.reaped = True

    def remove_records(self, also=None):
        record_path(self.state).unlink(missing_ok=True)
        for pid in (self.pid, getattr(also, 'pid', None)):
            if pid is not None:
                listeners_path(pid, self.state).unlink(missing_ok=True)
        if also is not None:
            (self.state / 'candidate.json').unlink(missing_ok=True)

    def end(self, also=None, grace=END_GRACE_SECONDS, sleep=time.sleep, clock=time.monotonic):
        """B13. SIGTERM to the old Bun and to `also` (the candidate's Popen) at the same moment, one
        grace for both, SIGKILL to the group of each still running, both reaped, the records removed.
        Idempotent: once reaped, an id is never signalled again."""
        if self.pid is None and also is None:
            return
        members = []
        if self.pid is not None and not self.reaped and unreaped(self.pid) != 'gone':
            members.append(self)
        if also is not None and also.returncode is None and unreaped(also.pid) != 'gone':
            members.append(also)
        for member in members:
            if unreaped(member.pid) == 'running':
                with contextlib.suppress(ProcessLookupError):
                    os.kill(member.pid, signal.SIGTERM)
        deadline = clock() + grace
        while clock() < deadline and any(unreaped(member.pid) == 'running' for member in members):
            sleep(.02)
        for member in members:
            if unreaped(member.pid) == 'running':
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(member.pid, signal.SIGKILL)
        for member in members:
            if member is self:
                with contextlib.suppress(ChildProcessError):
                    os.waitid(os.P_PID, self.pid, os.WEXITED)
                self.reaped = True
            else:
                member.wait()
        self.remove_records(also)


def container(environ=os.environ):
    return environ.get('SBARBASE_CONTAINER') == '1'


def host_admission(environ=os.environ, root=ROOT):
    """B4: the commands of the read-only host admission that the exec skipped, in the order the service
    manager runs them before the guard: under systemd the preflight, in the container the profile checks."""
    if container(environ):
        script = '/usr/local/lib/sbarbase/docker_profile.py'
        return [['/usr/bin/python3', script, 'check'], ['/usr/bin/python3', script, 'runtime', str(root)]]
    return [['/bin/sh', str(Path(root) / 'deploy/host-preflight.sh'), '--runtime']]


def start_gates(environ=os.environ):
    """B7: the start gates after the guard that the exec skipped: the unit's install check, and in the
    container the image check its start script runs (dependencies were installed by the upgrade child)."""
    return [['/usr/bin/python3', 'lab/install_server.py', 'images' if container(environ) else 'check']]


def adopt(head, marked, state=STATE, parent=None, proc='/proc'):
    """B2. The old Bun as a Retiring, from handover.json. Valid only with the exec marker, an equal
    protocol, `to` equal to HEAD, and a pid whose parent is this process and whose start time equals the
    record's. A record that is not valid: its pid, when it is a live child of this process with that
    start time, is stopped and reaped; the record is removed either way. A live process with the recorded
    start time that is not our child means a handover is in progress under another process: this start
    refuses before it takes any lock."""
    path = record_path(state)
    if not path.exists():
        return Retiring(state=state)
    parent = os.getpid() if parent is None else parent
    record = read_json(path)
    pid = record.get('pid') if isinstance(record, dict) else None
    started = record.get('started') if isinstance(record, dict) else None
    found = stat_fields(pid, proc) if type(pid) is int else None
    alive = found is not None and found[1] == started
    ours = alive and found[0] == parent
    valid = (marked and isinstance(record, dict) and record.get('protocol') == PROTOCOL and record.get('to') == head and ours)
    if valid:
        return Retiring(pid, started, state)
    if alive and not ours:
        raise SystemExit('A handover of the console process is in progress')
    if ours:
        Retiring(pid, started, state).end()
    path.unlink(missing_ok=True)
    return Retiring(state=state)


# Probe, promote, steer, retire, confirm (H1 to H11) ----------------------------------------------

class Rounds:
    """Health rounds off the supervisor's loop, one at a time, every `interval`: poll() is None while
    waiting and (healthy, detail) once a round answered."""

    def __init__(self, probe, clock, interval, submit):
        self.probe, self.clock, self.interval, self.submit = probe, clock, interval, submit
        self.pending = None
        self.next = None

    def poll(self):
        if self.next is None:
            self.next = self.clock()
        if self.pending is not None and self.pending.done():
            try:
                result = self.pending.result()
            except Exception as error:
                result = (False, f'health probe failed ({error.__class__.__name__})')
            self.pending = None
            self.next = self.clock() + self.interval
            return result
        if self.pending is None and self.clock() >= self.next:
            self.pending = self.submit(self.probe)
        return None


def verify_console(port, pid, count=VERIFY_CONNECTIONS, timeout=VERIFY_TIMEOUT, host='127.0.0.1'):
    """H7. (ok, detail): `count` new connections to the console port, each GET /health, every
    answer 200 from process `pid`."""
    for _ in range(count):
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            connection.request('GET', '/health', headers={'connection': 'close'})
            answer = connection.getresponse()
            answer.read()
            seen = answer.getheader('x-sbarbase-pid')
        except (OSError, http.client.HTTPException) as error:
            return False, f'a fresh connection to the console port failed ({error.__class__.__name__})'
        finally:
            connection.close()
        if answer.status != 200 or seen != str(pid):
            return False, f'a fresh connection to the console port was answered by process {seen} with HTTP {answer.status}'
    return True, f'{count} fresh connection(s) answered from the candidate'


class Promotion:
    """Replaces upgrade_health.Confirmation in a handover start. poll() runs once per supervisor turn
    and never blocks: probe rounds and the verification run in threads, /proc reads take microseconds,
    and every wait is a deadline checked on the next turn. True once the confirmation is saved;
    RuntimeError (W2) when the new version fails; HandoverFailed (W3) when the version passed its
    round and the handover failed."""

    def __init__(self, supervisor, retiring, confirmed, state=STATE, clock=time.monotonic, submit=None, probe=None,
                 verify=None, status=None, kill=os.kill, proc='/proc', environ=os.environ, log=print, later=None,
                 promote_seconds=PROMOTE_SECONDS, handover_seconds=HANDOVER_SECONDS):
        import upgrade_health
        self.supervisor, self.retiring, self.confirmed = supervisor, retiring, confirmed
        self.state, self.clock, self.kill, self.proc, self.log = Path(state), clock, kill, proc, log
        self.submit = submit or upgrade_health.background
        self.status = status
        self.verify = verify or verify_console
        self.retire_wait = retire_wait_seconds(environ)
        self.probe = probe or self.round
        self.phase = 'probe'
        self.rounds = Rounds(self.probe, clock, upgrade_health.INTERVAL, self.submit)
        # The design's PROMOTE_SECONDS and HANDOVER_SECONDS, which a test on a starved host may stretch.
        self.promote_seconds, self.handover_seconds = promote_seconds, handover_seconds
        self.later = later or self.start_timer
        self.deadline_at = None
        self.detail = 'no health probe has finished yet'
        self.limit = None
        self.inode_old = {}
        self.freeze_until = None
        self.promote_until = None
        self.verifying = None
        self.retired_at = None
        self.sent = False

    @staticmethod
    def start_timer(seconds, function):
        timer = threading.Timer(seconds, function)
        timer.daemon = True
        timer.start()

    def round(self):
        import upgrade_health
        return upgrade_health.check(self.state, self.supervisor.server.pid if self.supervisor.server else None,
                                    server_file='candidate.json', expect_pid=self.supervisor.server.pid)

    def owns_exit(self):
        """True from the moment H3 sends SIGUSR2 until the promoted server.json was seen: a candidate
        exit in it is the handover's to judge (W3), not the loop's (Supervisor.check)."""
        return self.sent and self.phase == 'promoting'

    def candidate(self):
        return self.supervisor.server.pid

    def fail(self, step, reason):
        raise HandoverFailed(step, reason)

    def poll(self):
        import upgrade_health
        now = self.clock()
        if self.deadline_at is None:
            self.deadline_at = now + upgrade_health.DEADLINE
        if self.limit is not None and now > self.limit:
            raise RuntimeError(f'The handover did not finish within {self.handover_seconds} s: {self.detail}')
        method = getattr(self, 'step_' + self.phase)
        return method(now)

    def step_probe(self, now):
        import upgrade_health
        result = self.rounds.poll()
        if result is not None:
            healthy, self.detail = result
            if healthy:
                self.limit = now + self.handover_seconds
                self.phase = 'freeze'
                self.freeze_until = now + FREEZE_WAIT_SECONDS
                return self.step_freeze(now)
        if now >= self.deadline_at:
            raise RuntimeError(f'The new version did not become healthy within {upgrade_health.DEADLINE} s: {self.detail}')
        return False

    def step_freeze(self, now):
        """H2 and H3. The old process's record must say `frozen` (it does at its next timer tick after
        A7, which a passing round normally outlasts); the supervisor gives it FREEZE_WAIT_SECONDS."""
        old = read_json(listeners_path(self.retiring.pid, self.state))
        if not isinstance(old, dict) or old.get('frozen') is not True:
            if now >= self.freeze_until:
                self.fail('H2', 'the old console process did not freeze its listeners')
            return False
        try:
            self.inode_old = clean_group(old, self.retiring.pid, self.proc)
        except Refused as error:
            self.fail('H2', str(error))
        self.old_record = old
        self.sent = True
        self.phase = 'promoting'
        self.promote_until = now + self.promote_seconds
        try:
            self.kill(self.candidate(), signal.SIGUSR2)
        except OSError as error:
            self.fail('H3', f'the promote signal could not be sent ({error.__class__.__name__})')
        return False

    def step_promoting(self, now):
        code = self.status(self.supervisor.server)
        if code is not None:
            self.fail('H3', f'the candidate exited with status {code}')
        published = read_json(self.state / 'server.json')
        if isinstance(published, dict) and published.get('pid') == self.candidate() and published.get('promoted') is True:
            self.published = published
            self.phase = 'check'
            self.sent = True
            return False
        if now >= self.promote_until:
            self.fail('H3', f'the candidate published nothing in {self.promote_seconds} s')
        return False

    def groups(self):
        new = read_json(listeners_path(self.candidate(), self.state))
        if not isinstance(new, dict) or new.get('role') != 'candidate' or new.get('protocol') != PROTOCOL:
            raise Refused('the candidate has no listener record')
        self.new_record = new
        return group_state(self.old_record, new, self.inode_old, self.retiring.pid, self.candidate(), self.proc)

    def step_check(self, now):
        """H4."""
        try:
            self.first = self.groups()
        except Refused as error:
            self.fail('H4', str(error))
        self.phase = 'recheck'
        return False

    def step_recheck(self, now):
        """H5 and H6."""
        shared = {item['name'] for item in listener_list(self.old_record)} & {item['name'] for item in listener_list(self.new_record)}
        by_name = {item['name']: item for item in listener_list(self.new_record)}
        for name in sorted(shared):
            if by_name[name].get('steered') is not True:
                self.fail('H5', f'{name} was not steered to the candidate')
        try:
            again = self.groups()
        except Refused as error:
            self.fail('H6', str(error))
        if again != self.first:
            self.fail('H6', 'a listener was rebound between the checks')
        port = self.published['url'].rsplit(':', 1)[-1]
        self.verifying = self.submit(lambda: self.verify(int(port), self.candidate()))
        self.phase = 'verify'
        return False

    def step_verify(self, now):
        """H7, then H8: the old Bun retires."""
        if not self.verifying.done():
            return False
        try:
            ok, detail = self.verifying.result()
        except Exception as error:
            ok, detail = False, f'the verification failed ({error.__class__.__name__})'
        if not ok:
            self.fail('H7', detail)
        if not self.retiring.reaped:
            # An id that was reaped (Supervisor.check does it when the old process dies on its own) is never
            # signalled again (B13): it is as retired as one that exits at once.
            with contextlib.suppress(ProcessLookupError):
                self.kill(self.retiring.pid, signal.SIGTERM)
        self.retired_at = now
        self.phase = 'retire'
        return False

    def step_retire(self, now):
        """H9."""
        if self.retiring.exited():
            pass
        elif now - self.retired_at >= self.retire_wait:
            self.retiring.kill_group()
        else:
            return False
        self.retiring.remove_records()
        done = getattr(self.supervisor, 'retired', None)
        if done:
            done()
        # H9a: the candidate lets go of the steering; the supervisor looks at its record a little later.
        with contextlib.suppress(ProcessLookupError):
            # A candidate that is gone is found by the loop's own check of it.
            self.kill(self.candidate(), signal.SIGUSR2)
        self.later(RECORD_SECONDS, self.note_steering)
        self.phase = 'confirm'
        self.rounds = None
        return False

    def step_confirm(self, now):
        """H10. Confirmation is tried at once; when it was not saved, the next round (on the
        candidate's private address, the pid checked) runs after the interval and it is tried again."""
        import upgrade_health
        if self.rounds is None:
            self.rounds = Rounds(self.probe, self.clock, upgrade_health.INTERVAL, self.submit)
            self.rounds.next = now
            if self.try_confirm():
                self.phase = 'done'
                return True
            self.rounds.next = now + upgrade_health.INTERVAL
            return False
        result = self.rounds.poll()
        if result is not None:
            healthy, self.detail = result
            if healthy and self.try_confirm():
                self.phase = 'done'
                return True
        return False

    def try_confirm(self):
        try:
            saved = self.confirmed()
        except Exception as error:
            self.detail = f'the health checks passed, but the confirmation was not saved ({error.__class__.__name__})'
            return False
        if not saved:
            self.detail = 'the health checks passed, but the confirmation was not saved'
        return bool(saved)

    def step_done(self, now):
        return True

    def note_steering(self):
        """The record of the promoted candidate after H9a: logged when it still says steered."""
        record = read_json(listeners_path(self.candidate(), self.state))
        if isinstance(record, dict) and any(item.get('steered') for item in record.get('listeners', [])):
            self.log('The candidate still steers a listener after the old process left; the detach failed (see its log).')
