"""Keep an OS lock alive in the worker process, including after wrapper exit."""
import fcntl
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
(root / '.lab').mkdir(exist_ok=True)
lock = os.open(root / '.lab/worker.lock', os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Another provisioning worker is active')
os.set_inheritable(lock, True)
os.environ['SBARBASE_WORKER_LOCKED'] = '1'
os.execvp('bun', ['bun', 'lab/worker.ts'])
