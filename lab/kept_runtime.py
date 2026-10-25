"""The owned containers a planned upgrade restart leaves running, for the next start to adopt.

An upgrade or rollback that moved the checkout ends with the supervisor exiting so the service
manager starts the version the checkout holds (lab/dev.py, RESTART_FOR_UPGRADE). Stopping every
owned container there, the shared database included, stopped every application on the server
for the whole restart, though most releases change no image and no container setting. So that
one exit leaves them running and records which ones it left (`kept-runtime.json`); the next
start adopts them through the runtime's own identity and drift checks (lab/durable_runtime.py
launch), replacing any whose image or computed settings changed, as it always has.

Everything else stays as it was. The record is honoured only while all of these hold, and is
otherwise thrown away so the runtime is stopped as before (lab/leftover_runtime.py):

- the checkout is the commit the exit moved to, and the record is younger than MAX_AGE;
- every running owned container is one the record names, with the image it had then;
- no authority state waits for reconciliation (the same list lab/leftover_runtime.py refuses on);
- the version the checkout moved to has this module, so it knows the record: an exit towards a
  version without it stops the runtime as before (lab/dev.py).

Any other exit (a stop, a failed start, a way back, a killed supervisor) records nothing. The
record is removed once the start that adopted the containers has its runtime up, so a later
crash finds no record and the leftover runtime is stopped as before. A start that adopted them
and ends before its runtime is up stops them (lab/dev.py close_runtime).

One window is left: between the exit and the next start (the service manager's restart delay, its
start checks and, under Docker, the container's start script, which may pull images) no process owns
them. A stop of the service in those seconds leaves them
running, as a killed supervisor does, until the next start adopts them within MAX_AGE or stops
them after it.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.lab' / 'upstream'
NAME = 'kept-runtime.json'
OWNED = 'label=io.sbarbase.owner=durable-upstream'
# A planned restart takes seconds to a few minutes (a way back reinstalls dependencies first);
# a record older than this belongs to a start that never came, and the runtime is stopped instead.
MAX_AGE = 1800
# The leftover settle judges a record first; the checks after it in the same start (the preflight,
# the runtime start) accept it this much longer, so a record that turns MAX_AGE between them does
# not fail a start the settle already let through.
LATER_GRACE = 300
# A full commit id in a SHA-1 or a SHA-256 repository, as lab/release_channel.py reads them.
COMMIT_ID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def path(state=STATE):
    return Path(state) / NAME


def head(root=ROOT):
    """The commit the checkout holds, or None."""
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and COMMIT_ID.fullmatch(value) else None


def running():
    """{id: {'image', 'name', 'memory'}} of every running owned container, or None when Docker
    cannot be asked. `memory` is the container's limit in bytes (0 when it has none)."""
    try:
        ids = subprocess.run(['docker', 'ps', '-q', '--no-trunc', '--filter', OWNED], capture_output=True, text=True, timeout=60)
        if ids.returncode:
            return None
        if not ids.stdout.split():
            return {}
        result = subprocess.run(['docker', 'inspect', '--format', '{{.Id}} {{.Image}} {{.Name}} {{.HostConfig.Memory}}',
                                 *ids.stdout.split()], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    found = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 4 or not parts[3].isdigit():
            return None
        found[parts[0]] = {'image': parts[1], 'name': parts[2].lstrip('/'), 'memory': int(parts[3])}
    return found


def supported(root=ROOT):
    """Whether the version the checkout holds knows the record (it ships this module)."""
    return (Path(root) / 'lab' / 'kept_runtime.py').is_file()


def started(pid):
    """A process's start time in clock ticks since boot (/proc/<pid>/stat), or None when it is gone."""
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        return int(text[text.rindex(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def leave(containers, to, state=STATE, now=None, origin=None):
    """Records the running owned containers a planned upgrade exit leaves for the next start, and
    the commit they ran with (`from`). Written through a private temporary file and a rename, so a
    reader sees all of it or none."""
    record = {'version': 1, 'to': to, 'from': origin, 'at': int(time.time() if now is None else now),
              'containers': {cid: {'image': item['image'], 'name': item['name']} for cid, item in containers.items()}}
    write(record, state)
    return record


def claim(state=STATE, pid=None):
    """Marks the record as taken by the start that adopts the containers. Only that process and its
    stages may use it from then on: a start that dies while adopting leaves a record no later start
    honours, so the next one stops what it left, as after any crash."""
    record = read(state)
    if record is None:
        return None
    pid = os.getpid() if pid is None else pid
    record['claimed'] = {'pid': pid, 'started': started(pid)}
    write(record, state)
    return record


def write(record, state=STATE):
    target = path(state)
    pending = target.with_name(NAME + '.pending')
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, target)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read(state=STATE):
    try:
        record = json.loads(path(state).read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or record.get('version') != 1 or not isinstance(record.get('to'), str)
            or type(record.get('at')) is not int or not isinstance(record.get('containers'), dict)):
        return None
    return record


def judge(record, containers, commit, waiting, now=None, grace=0):
    """None when `record` lets the running `containers` be adopted, else why not."""
    if record is None:
        return 'no readable record'
    if commit is None or record['to'] != commit:
        return 'the checkout is not the version the record was written for'
    age = (time.time() if now is None else now) - record['at']
    if age < 0 or age > MAX_AGE + grace:
        return 'the record is too old'
    if waiting:
        return ', '.join(waiting) + ' needs reconciliation'
    claimed = record.get('claimed')
    if claimed is not None:
        owner = claimed.get('pid') if isinstance(claimed, dict) else None
        if owner not in (os.getpid(), os.getppid()) or claimed.get('started') is None or started(owner) != claimed.get('started'):
            return 'an earlier start took the record and did not finish'
    if containers is None:
        return 'Docker could not be asked'
    kept = record['containers']
    for cid, item in containers.items():
        entry = kept.get(cid)
        if not isinstance(entry, dict) or entry.get('image') != item['image'] or entry.get('name') != item['name']:
            return f"{item['name']} is running and is not a container the record kept as it is"
    return None


def current(state=STATE, root=ROOT, containers=None, waiting=None, now=None, grace=0):
    """(record, None) when the running owned containers may be adopted, else (None, reason).
    Reads only; discard() removes a record. `grace` is LATER_GRACE for a check after the settle."""
    if not path(state).exists():
        return None, 'no record'
    if waiting is None:
        import leftover_runtime
        waiting = leftover_runtime.pending(Path(state))
    record = read(state)
    reason = judge(record, running() if containers is None else containers, head(root), waiting, now, grace)
    return (record, None) if reason is None else (None, reason)


def discard(state=STATE):
    path(state).unlink(missing_ok=True)
    path(state).with_name(NAME + '.pending').unlink(missing_ok=True)


# Where Docker's cgroup v2 hierarchy keeps a container's memory accounting: the systemd driver,
# then the cgroupfs driver.
CGROUP = Path('/sys/fs/cgroup')
CGROUP_PATHS = ('system.slice/docker-{id}.scope', 'docker/{id}')
# An environment's Studio pair, which the start placement does not count (lab/studio.py names).
STUDIO = ('-studio', '-meta')
# The containers the start placement counts (lab/resource_policy.py start_placement): the database,
# Storage, the management Auth and each environment's services. Only these are credited, so one of a
# kind this version no longer runs never makes its admission more lenient than a start from stopped.
COUNTED = re.compile(r'sbarbase-durable-(db|storage|management-auth|e_[a-f0-9]{24}-(auth|rest|realtime|functions))')


# The environment variable with which a handover start (lab/handover.py) tells the stages it runs beside
# the previous console process how much memory that process holds, so an admission counts it as available.
ADMISSION_CREDIT = 'SBARBASE_ADMISSION_CREDIT_MIB'


def admission_credit_mib(environ=os.environ):
    """MiB the previous console process holds that an admission may count as available: a plain number
    in the environment, 0 for anything else (a stage that was not given one counts nothing)."""
    text = environ.get(ADMISSION_CREDIT, '')
    return int(text) if re.fullmatch(r'[0-9]{1,7}', text) else 0


def credit_mib(containers, cgroup=CGROUP):
    """MiB the kept containers hold now that the host does not count as available: their anonymous
    and shared memory (cgroup v2 memory.stat `anon` and `shmem`). Their file cache is left out, since
    MemAvailable already counts it. An admission that counts their limits again adds this back. A
    container without accounting counts 0; one whose accounting cannot be read makes the whole credit 0,
    which keeps the admission at least as strict as a start from stopped containers. `containers` are
    ids, or the record's {id: {'name'}} to leave out what the start placement does not count (COUNTED)."""
    if isinstance(containers, dict):
        containers = [cid for cid, item in containers.items() if COUNTED.fullmatch(str(item.get('name', '')))]
    total = 0
    for cid in containers:
        for pattern in CGROUP_PATHS:
            path = Path(cgroup) / pattern.format(id=cid) / 'memory.stat'
            if not path.exists():
                continue
            try:
                fields = dict(line.split() for line in path.read_text().splitlines() if line.count(' ') == 1)
                total += int(fields['anon']) + int(fields['shmem'])
            except (OSError, ValueError, KeyError):
                return 0
            break
        # A container with no accounting under either driver holds nothing to count back (it has
        # exited), or is not found where expected: either way crediting it nothing is the strict side.
    return total // 1024 ** 2
