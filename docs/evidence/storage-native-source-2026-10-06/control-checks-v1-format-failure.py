"""Bounded private supervisor regressions, never native writer observations."""
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time

resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
resource.setrlimit(resource.RLIMIT_FSIZE, (16777216, 16777216))
OWNED = Path(__file__).resolve().parent
profile = json.loads((OWNED / 'profile.json').read_text())
for item in profile['bindings']:
    if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
        raise SystemExit('Frozen source/helper binding differs')
# Adopt only this private probe's orphaned children so the broken runner cannot
# leave them behind. This is process supervision testing, not a Storage actor.
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
    raise SystemExit('Private child subreaper unavailable')
fd = os.open(OWNED / 'budget.json', os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
with os.fdopen(os.dup(fd)) as stream:
    budget = json.load(stream)
if budget['active_seconds'] >= 165 or budget['output_bytes'] + 200000 >= 16777216:
    raise SystemExit('Insufficient remaining frozen source role budget')
start = time.monotonic()
observations = []
capture_bytes = 0
template = (OWNED / 'bounded-checks.py').read_text()
python = profile['python_executable']
child_source = '''import json,os,time
from pathlib import Path
pid=os.getpid()
ticks=int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19])
Path(__file__).with_suffix('.identity').write_text(json.dumps({'pid':pid,'start_ticks':ticks}))
if {closed!r}:
 os.close(1);os.close(2)
else:
 os.write(1,b'x'*32768)
time.sleep(120)
'''
for mode in ('output-cap', 'closed-pipes'):
    with tempfile.TemporaryDirectory(prefix='private-supervisor-') as temporary:
        directory = Path(temporary)
        child = directory / 'child.py'
        child.write_text(child_source.format(closed=mode == 'closed-pipes'))
        runner = directory / 'bounded-checks.py'
        source = template.replace('MAX_OUTPUT=16777216', 'MAX_OUTPUT=4096')
        source = source.replace('CHECK_SECONDS=120', 'CHECK_SECONDS=.2')
        begin = source.index('argv=')
        end = source.index('\nenv=', begin)
        source = source[:begin] + 'argv=' + repr([python, str(child)]) + source[end:]
        runner.write_text(source)
        (directory / 'profile.json').write_text(json.dumps({'bindings': [], 'python_executable': python}))
        (directory / 'budget.json').write_text(json.dumps({'active_seconds': 0, 'output_bytes': 0, 'checks': []}))
        result = subprocess.run([python, str(runner), '--check', 'python'],
                                capture_output=True, timeout=8)
        capture_bytes += len(result.stdout) + len(result.stderr)
        live = None
        marker = child.with_suffix('.identity')
        if marker.exists():
            identity = json.loads(marker.read_text())
            pid = identity['pid']
            try:
                fields = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(') ', 1)[1].split()
                if int(fields[19]) == identity['start_ticks'] and fields[0] != 'Z':
                    live = pid
            except FileNotFoundError:
                pass
            finally:
                # Cleanup targets the recorded private PID with its Linux start
                # identity. Never signal a recycled PID or another process group.
                if live is not None:
                    os.kill(live, signal.SIGKILL)
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
        local = json.loads((directory / 'budget.json').read_text())
        logs = list(directory.glob('check-*.log'))
        retained = sum(path.stat().st_size for path in logs)
        capture_bytes += retained
        if mode == 'output-cap':
            passed = bool(local['checks']) and retained <= 4096 and local['output_bytes'] <= 4096
        else:
            passed = live is None and bool(local['checks']) and local['active_seconds'] <= 2
        observations.append({'id': mode, 'passed': passed, 'runner_returncode': result.returncode,
                             'retained_output_bytes': retained, 'budget': local,
                             'private_child_survived_runner': live is not None,
                             'runner_stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
                             'runner_stderr_sha256': hashlib.sha256(result.stderr).hexdigest()})
elapsed = time.monotonic() - start
failed = sum(not row['passed'] for row in observations)
payload = {'total': 2, 'failed': failed, 'skipped': 0, 'native_accepted': False,
           'seconds': elapsed, 'observations': observations}
log = OWNED / ('control-check-' + str(len(budget['checks']) + 1) + '.json')
log.write_text(json.dumps(payload, indent=2) + '\n')
capture_bytes += log.stat().st_size
budget['active_seconds'] += elapsed
budget['output_bytes'] += capture_bytes + 4096
budget['checks'].append({'phase': 'private-supervisor-control', 'seconds': elapsed,
                        'output_bytes': capture_bytes + 4096, 'log': log.name,
                        'returncode': 1 if failed else 0,
                        'reason': 'Two reduced-limit private supervisor regressions, no native proof'})
os.lseek(fd, 0, os.SEEK_SET)
os.ftruncate(fd, 0)
os.write(fd, json.dumps(budget, sort_keys=True).encode())
os.fsync(fd)
os.close(fd)
print(json.dumps({'total': 2, 'failed': failed, 'skipped': 0, 'native_accepted': False,
                  'raw_log': str(log), 'raw_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}))
raise SystemExit(1 if failed else 0)
