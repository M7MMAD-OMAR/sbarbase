"""Exercise target up/stop/restart and replace stale placement before admission."""
import json
import subprocess
import durable_runtime as runtime
import run as lab
from target_runtime import TargetRuntime


def main():
    target=TargetRuntime();checks=[]
    def probe(running):
        result=subprocess.run(['bun','lab/target-lifecycle-check.ts'],input=json.dumps({'environment':target.environment,'running':running}),text=True,capture_output=True,timeout=30)
        if result.returncode:raise RuntimeError('Lifecycle gateway check failed')
        checks.extend(json.loads(result.stdout))
    try:
        target.start();probe(True)
        first=target.routing()['revision']
        target.stop();probe(False)
        state=target.routing()
        target.routing('stage',state['revision'],{'auth':'http://127.0.0.1:1','rest':'http://127.0.0.1:1'})
        target.start();probe(True)
        state=target.routing()
        if state['revision']<=first or ':1'==state['placement']['auth'][-2:] or 'storage' not in state['placement']:raise RuntimeError('Placement was not refreshed')
        checks.append('restart replaces stale placement and restores Storage route before admission')
        if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():raise RuntimeError('Source unexpectedly running')
        checks.append('source stayed stopped throughout target lifecycle')
    finally:target.stop()
    probe(False)
    (lab.ROOT/'docs/evidence/target-lifecycle-checks.json').write_text(json.dumps({'scope':'Retained target startup, composed gateway availability, pause-before-stop and restart replacing intentionally stale addresses. Source stopped throughout. Staged local mode only; no combined source/target capacity or multi-worker graceful drain.', 'checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' target lifecycle checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Target lifecycle check failed; inspect retained state')
