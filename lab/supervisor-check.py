"""Owned local runner smoke check. Stops its runtime and preserves volumes."""
import json, os, signal, subprocess, time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
state=root/'.lab/upstream'
checks=[]
def check(name, condition):
    if not condition: raise RuntimeError(name)
    checks.append(name)
with (state/'supervisor-smoke.log').open('w') as log:
    child=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=root,stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+180
        descriptor=None
        while time.monotonic()<deadline:
            if child.poll() is not None: raise RuntimeError('runner exited during startup')
            path=state/'supervisor.json'
            if path.exists():
                candidate=json.loads(path.read_text())
                if candidate['pid']==child.pid:
                    descriptor=candidate
                    break
            time.sleep(.2)
        check('runner started owned server and worker',descriptor is not None)
        duplicate=subprocess.run(['/usr/bin/python3','lab/dev.py'],cwd=root,capture_output=True,timeout=10)
        check('duplicate runner rejected',duplicate.returncode!=0)
        manual=subprocess.run(['/usr/bin/python3','lab/worker.py','--upstream'],cwd=root,capture_output=True,timeout=10)
        check('manual worker excluded',manual.returncode!=0)
        pid=descriptor['workerPid']
        status=Path(f'/proc/{pid}/status').read_text()
        check('worker is direct owned child',f'PPid:\t{child.pid}\n' in status)
        os.kill(pid,signal.SIGKILL)
        deadline=time.monotonic()+15
        recovered=None
        while time.monotonic()<deadline:
            record=json.loads((state/'supervisor.json').read_text())
            if record['workerPid']!=pid:
                recovered=record
                break
            time.sleep(.1)
        check('idle worker restarted after forced exit',recovered is not None)
        check('server retained across worker restart',recovered['serverPid']==descriptor['serverPid'])
        child.terminate()
        check('graceful runner stop',child.wait(timeout=60)==0)
        check('supervisor descriptor removed',not (state/'supervisor.json').exists())
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=60)
result={'scope':'Local supervisor smoke test; idle worker interruption only, not in-flight provisioning recovery','checks':checks,'count':len(checks)}
(root/'docs/evidence/supervisor-smoke-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
