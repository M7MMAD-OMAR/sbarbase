"""Stops an owned runtime that a supervisor which did not stop cleanly left running.

A supervisor killed outright (SIGKILL, the OOM killer) never runs its own stop, and the owned
containers keep running: Docker owns them, not the supervisor's process group or the service's
cgroup. The next start then refused: the preflight (lab/install_server.py check) finds owned
containers running, and its headroom check, like the runtime's own start check, reads memory
those containers still hold. Under systemd the service stayed down after any unclean stop, a
kill during an upgrade's health-gated confirmation included.

This runs before that preflight (deploy/sbarbase.service) and at the start of lab/dev.py, and
stops them exactly as the supervisor's own stop does (lab/installation_runtime.py stop:
`docker stop`, so every container and volume is kept and nothing is removed or recreated), but
only when both hold:

- no live owner holds its lock: supervisor.lock (a supervisor), worker.lock (a worker, including
  one a killed supervisor left behind, which inherits that lock), effect.lock (an effect owner).
  These are held for the whole stop, so no owner can start in between;
- no pending authority state needs reconciliation: a provisioning receipt, an HBA journal, a
  generation migration record or a recovery target's HBA journal. Those keep blocking, and the
  runtime stays as the crash left it for whoever reconciles them.

    /usr/bin/python3 lab/leftover_runtime.py

Exit 0: nothing owned was running, Docker could not be asked (the preflight names that), or the
leftover runtime was stopped. Exit 1: it declined or the stop failed; the reason is on standard
error. Only the containers labelled io.sbarbase.owner=durable-upstream count as running, the
same query as the preflight's.
"""
import contextlib
import fcntl
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.lab' / 'upstream'
# Held by a live supervisor, a worker and an effect owner (lab/dev.py, lab/worker.py, hba_startup).
LOCKS = ('supervisor.lock', 'worker.lock', 'effect.lock')
# Authority state that needs reconciliation before any start (effect_receipt.require_settled,
# hba_startup.require_clear, lab/install_server.py state and target_findings).
PENDING = ('worker-effect.json', 'hba-operation.json', 'hba-migration')
OWNED = 'label=io.sbarbase.owner=durable-upstream'
# The supervisor's own stop stage gets the same (lab/dev.py).
STOP_SECONDS = 90


class StopFailed(RuntimeError):
    pass


def present(path):
    """Only a missing entry is absent; a path that cannot be read counts as present."""
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def pending(state):
    """Pending authority state, by its path under the state directory."""
    found = [name for name in PENDING if present(state / name)]
    targets = state / 'targets'
    if targets.is_dir():
        found += [str(path.relative_to(state)) for path in sorted(targets.glob('*/hba-operation.json'))]
    return found


def docker_ps():
    """Running owned container ids, or None when Docker cannot be asked."""
    try:
        result = subprocess.run(['docker', 'ps', '-q', '--filter', OWNED], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.split() if result.returncode == 0 else None


def installation_stop():
    """The supervisor's own stop stage (lab/dev.py): keeps containers and volumes, removes nothing."""
    try:
        return subprocess.run(['/usr/bin/python3', 'lab/installation_runtime.py', 'stop'], cwd=ROOT,
                              timeout=STOP_SECONDS).returncode
    except subprocess.TimeoutExpired:
        return 'timed out'


@contextlib.contextmanager
def probe(state, names):
    """Takes each named lock that is free and holds it until the block ends; yields the names
    of the ones someone else holds."""
    state.mkdir(parents=True, exist_ok=True)
    descriptors, held = [], []
    try:
        for name in names:
            descriptor = os.open(state / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            descriptors.append(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held.append(name)
        yield held
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def decide(running, waiting, held):
    """('nothing' | 'stop' | 'decline', reason) from what runs, what is pending and which lock is held."""
    if not running:
        return 'nothing', None
    if held:
        return 'decline', (f'Owned containers are running and {", ".join(held)} is held: a live supervisor or '
                           'worker still owns them. Stop it first (sudo systemctl stop sbarbase, or Ctrl+C in the '
                           'terminal that runs lab/dev.py); the next start then begins from a stopped runtime')
    if waiting:
        return 'decline', ('Owned containers were left running by a start that did not stop cleanly, and '
                           f'{", ".join(waiting)} needs reconciliation first, so they are left as they are. '
                           'Reconcile it (docs/engineering/PROVISIONING-RECEIPTS.md), then start again')
    return 'stop', None


def settle(state=STATE, *, caller_holds=(), running=docker_ps, stop=installation_stop, say=print):
    """Stops a leftover owned runtime when decide() allows it. Returns (outcome, reason), outcome
    one of 'nothing', 'unknown' (Docker could not be asked), 'stopped' or 'decline'. Raises
    StopFailed when the stop ran and did not succeed.

    `caller_holds` names the locks the caller already holds (lab/dev.py holds supervisor.lock and
    worker.lock): flock refuses a second open file of the same lock even in the same process, so
    those are not probed again."""
    ids = running()
    if ids is None:
        return 'unknown', None
    if not ids:
        return 'nothing', None
    with probe(state, [name for name in LOCKS if name not in caller_holds]) as held:
        outcome, reason = decide(ids, pending(state), held)
        if outcome != 'stop':
            return outcome, reason
        say(f'{len(ids)} owned container(s) were left running by a supervisor that did not stop cleanly; '
            'stopping them as its own stop does (containers and volumes are kept).', flush=True)
        result = stop()
        if result != 0:
            raise StopFailed(f'The leftover owned runtime could not be stopped (installation runtime stop: {result}); '
                             'inspect its current container state')
        left = running()
        if left:
            raise StopFailed(f'{len(left)} owned container(s) still run after the stop; inspect their state')
    return 'stopped', None


def main():
    os.chdir(ROOT)
    try:
        outcome, reason = settle()
    except StopFailed as error:
        print(str(error), file=sys.stderr)
        return 1
    if outcome == 'decline':
        print(reason, file=sys.stderr)
        return 1
    if outcome == 'stopped':
        print('The leftover owned runtime is stopped; the start goes on.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
