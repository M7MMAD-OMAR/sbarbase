"""Restore unaffected local source environments while preserving moved source fence."""
import fcntl
import json
import subprocess
import durable_runtime as runtime
import source_fence
import run as lab


def main():
    record=runtime.STATE/'cutover-operation.json';op=json.loads(record.read_text())
    if op['phase']!='managed-target-verified-routing-paused':raise RuntimeError('Unexpected cutover phase')
    d=json.loads((runtime.STATE/'recovery-target.json').read_text());e=d['environment']
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip():raise RuntimeError('Recovery targets must remain stopped')
    target=runtime.Runtime();neighbors=[name for name in op['paused_revisions'] if name!=e]
    if len(neighbors)!=3:raise RuntimeError('Unexpected neighbor inventory')
    try:
        op['phase']='neighbors-starting';runtime.atomic(record,op)
        target.start()
        if not source_fence.is_fenced(target.sql,e):raise RuntimeError('Source fence missing after restart')
        if target.sql(f"SELECT NOT datallowconn FROM pg_database WHERE datname='{e}';").stdout.strip()!='t':raise RuntimeError('Full database fence missing')
        for kind in ('auth','rest'):
            state=runtime.inspect('container',runtime.PREFIX+'-'+e+'-'+kind)
            if state and state['State']['Running']:raise RuntimeError('Moved source service unexpectedly running')
        if target.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{e}';").stdout.strip()!='0':raise RuntimeError('Moved source has active sessions')
        value={'moved':e,'neighbors':neighbors,'expected':op['paused_revisions']}
        result=subprocess.run(['bun','lab/cutover-neighbors-check.ts'],input=json.dumps(value),text=True,capture_output=True,timeout=90)
        if result.returncode:raise RuntimeError('Neighbor gateway verification failed')
        op['phase']='neighbors-restored-target-paused';op['neighbor_revisions']=json.loads((lab.ROOT/'docs/evidence/cutover-neighbor-checks.json').read_text())['resumedRevisions'];runtime.atomic(record,op)
        print(result.stdout.strip())
    except BaseException:
        op['failed_phase']=op['phase'];op['phase']='needs-reconciliation';runtime.atomic(record,op);raise
    finally:
        # Foreground probe does not leave daemons running; normal source startup
        # now serves resumed neighbors while the moved environment remains paused.
        try:runtime.stop()
        except BaseException:
            op['phase']='cleanup-failed';runtime.atomic(record,op);raise


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main()
    except Exception:raise SystemExit('Neighbor restoration incomplete; inspect retained operation') from None
