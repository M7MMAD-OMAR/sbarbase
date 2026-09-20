"""Run the real foreground supervisor and verify the combined installation."""
import json
import subprocess
import time
from pathlib import Path
import run as lab


def main():
    process=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        ready=False
        for _ in range(600):
            if process.poll() is not None:raise RuntimeError('Foreground supervisor exited')
            try:
                supervisor=json.loads(Path('.lab/upstream/supervisor.json').read_text())
                server=json.loads(Path('.lab/upstream/server.json').read_text())
                if supervisor['pid']==process.pid and supervisor['serverPid']==server['pid']:ready=True;break
            except (OSError,ValueError,KeyError):pass
            time.sleep(.1)
        if not ready:raise RuntimeError('Combined startup timed out')
        result=subprocess.run(['bun','lab/combined-gateway-check.ts'],capture_output=True,text=True,timeout=45)
        if result.returncode:raise RuntimeError('Combined gateway checks failed')
        print(result.stdout.strip())
    finally:
        process.terminate()
        try:process.wait(timeout=100)
        except subprocess.TimeoutExpired:
            import os,signal
            os.killpg(process.pid,signal.SIGKILL);process.wait()
            raise RuntimeError('Supervisor shutdown exceeded deadline')
    if process.returncode!=0:raise RuntimeError('Supervisor shutdown failed')
    for owner in ('durable-upstream','recovery-target'):
        if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner).stdout.strip():raise RuntimeError('Owned containers remain running')
    print('Combined supervisor shutdown verified; all owned containers stopped')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Combined supervisor verification failed; inspect owned resources') from None
