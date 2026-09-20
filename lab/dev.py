"""Foreground local installation runner. Owns only its children and labelled lab."""
import collections
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab/upstream'


def child_status(process):
    """Observe our child without releasing its PID reservation by reaping it."""
    if process.returncode is not None:
        return process.returncode
    result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if result is None:
        return None
    return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status


def terminate_group(process, grace=20):
    """Clean an owned session before reaping its leader. Never use saved PIDs.

    Caller exclusively owns child waiting. Already reaped leaders no longer
    authorize group signals; their descendants require separate containment.
    """
    if process.returncode is not None:
        return
    try:
        status = child_status(process)
    except ChildProcessError:
        return
    if status is None:
        # Popen.terminate() polls internally, which could reap the leader.
        os.kill(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while child_status(process) is None and time.monotonic() < deadline:
            time.sleep(.02)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            deadline = time.monotonic() + 2
            while child_status(process) is None and time.monotonic() < deadline:
                time.sleep(.02)
    process.wait()


class Supervisor:
    def __init__(self, stop_event=None, worker_fd=None):
        self.stop_event = stop_event or threading.Event()
        self.server = None
        self.worker = None
        self.restarts = collections.deque()
        self.worker_fd = worker_fd

    def spawn(self, command):
        return subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),*command], cwd=ROOT, start_new_session=True)

    def descriptor(self):
        record = {'pid': os.getpid(), 'serverPid': self.server.pid if self.server else None,
                  'workerPid': self.worker.pid if self.worker else None,
                  'workerRestarts': len(self.restarts)}
        temporary = STATE/'supervisor.pending'
        temporary.write_text(json.dumps(record))
        temporary.replace(STATE/'supervisor.json')

    def start_worker(self):
        if self.worker_fd is None:
            raise RuntimeError('Supervisor requires an exclusive worker lock')
        self.worker = subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),'/usr/bin/python3', 'lab/worker.py', '--upstream', '--watch'],
                                       cwd=ROOT, start_new_session=True, pass_fds=(self.worker_fd,),
                                       env=dict(os.environ, SBARBASE_WORKER_FD=str(self.worker_fd)))
        self.descriptor()

    def check(self):
        if child_status(self.server) is not None:
            raise RuntimeError('Local API exited; stopping the installation')
        if child_status(self.worker) is not None:
            terminate_group(self.worker, grace=0)
            now = time.monotonic()
            while self.restarts and now-self.restarts[0] > 60:
                self.restarts.popleft()
            if len(self.restarts) >= 3:
                raise RuntimeError('Worker restart limit reached; inspect retained state')
            self.restarts.append(now)
            print('Provisioning worker exited; reconciling retained operations.', flush=True)
            self.start_worker()

    def run(self):
        try:
            self.server = self.spawn(['bun', 'lab/upstream-server.ts'])
            self.start_worker()
            while not self.stop_event.wait(.25):
                self.check()
        finally:
            # Stop new HTTP mutations first, then drain the active worker effect.
            if self.server:
                terminate_group(self.server)
            if self.worker:
                terminate_group(self.worker)
            path = STATE/'supervisor.json'
            if path.exists() and json.loads(path.read_text()).get('pid') == os.getpid():
                path.unlink()


def run_stage(command, stop_event, timeout=180, pass_fds=(), env=None):
    process = subprocess.Popen(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),*command], cwd=ROOT, start_new_session=True, pass_fds=pass_fds, env=env)
    deadline = time.monotonic()+timeout
    try:
        while child_status(process) is None:
            if stop_event.wait(.1):
                raise InterruptedError('Local installation startup cancelled')
            if time.monotonic() >= deadline:
                raise RuntimeError('Local installation stage timed out')
        return child_status(process)
    finally:
        # A failed stage leader may leave a Docker CLI child holding a lock.
        terminate_group(process, grace=2)


def main():
    if sys.argv[1:]:
        if sys.argv[1:] in (['--help'], ['-h']):
            print(__doc__+'\nRun from a terminal; Ctrl+C stops the console, worker and owned runtime while preserving volumes.')
            return
        raise SystemExit('Usage: /usr/bin/python3 lab/dev.py')
    os.chdir(ROOT)
    STATE.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    with (STATE/'supervisor.lock').open('a') as lock, (STATE/'worker.lock').open('a') as worker_lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another local installation runner is active.')
        # A one-shot/manual worker belongs to its caller, not this supervisor.
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Stop the existing manual worker before starting the runner.')
        started = False
        try:
            if run_stage(['/usr/bin/python3','lab/worker.py','--upstream','--settle-only'],stop_event,
                         pass_fds=(worker_lock.fileno(),),env=dict(os.environ,SBARBASE_WORKER_FD=str(worker_lock.fileno()))):
                raise RuntimeError('Provisioning receipt requires reconciliation before startup')
            if run_stage(['bun', 'run', 'build:ui'], stop_event):
                raise RuntimeError('Console build failed')
            if stop_event.is_set():
                return
            started = True
            if run_stage(['/usr/bin/python3', 'lab/installation_runtime.py', 'up'], stop_event,
                         pass_fds=(worker_lock.fileno(),),env=dict(os.environ,SBARBASE_WORKER_FD=str(worker_lock.fileno()))):
                raise RuntimeError('Runtime startup failed; the installation runtime reported its own reason above')
            if not stop_event.is_set():
                Supervisor(stop_event, worker_lock.fileno()).run()
        except InterruptedError:
            print('Local installation startup cancelled.', file=sys.stderr)
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            raise SystemExit(1)
        finally:
            if started:
                result = run_stage(['/usr/bin/python3', 'lab/installation_runtime.py', 'stop'], threading.Event(), timeout=90)
                if result:
                    print('Owned runtime stop failed; inspect its current container state.', file=sys.stderr)


if __name__ == '__main__':
    main()
