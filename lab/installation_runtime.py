"""Source plus retained moved target lifecycle for the local foreground supervisor."""
import argparse
import datetime
import fcntl
import json
import os
import traceback
import hba_startup
import durable_runtime as runtime
import run as lab
from target_runtime import TargetRuntime
from combined_admission import CombinedAdmission
import source_fence


def main(command,*,startup=None):
    if command=='up':
        runtime.effect_receipt.require_settled(runtime.STATE)
        if not isinstance(startup,hba_startup.Startup):raise RuntimeError('Explicit installation startup ownership required')
        startup.verify()
    moved=(runtime.STATE/'cutover-operation.json').exists()
    if not moved:
        if command=='up':runtime.Runtime(startup=startup).start()
        else:runtime.stop()
        return
    if command=='stop':
        try:TargetRuntime(stop_only=True).stop()
        finally:runtime.stop()
        return
    source=runtime.Runtime(startup=startup)
    target=TargetRuntime()
    admission=CombinedAdmission(source,target)
    snapshot=admission.check_current()
    try:
        # Pause and reconcile a prior target run before source startup.
        target.stop();source.start()
        e=target.environment
        if source.sql(f"SELECT NOT datallowconn FROM pg_database WHERE datname='{e}';").stdout.strip()!='t' or not source_fence.is_fenced(source.sql,e):raise RuntimeError('Source fence not retained')
        for kind in ('auth','rest'):
            state=runtime.inspect('container',runtime.PREFIX+'-'+e+'-'+kind)
            if state and state['State']['Running']:raise RuntimeError('Old source service running')
        target.start(combined_admission=admission)
        snapshot['target_started']=True
        (lab.ROOT/'docs/evidence/combined-runtime-admission.json').write_text(json.dumps(snapshot,indent=2)+'\n')
    except BaseException:
        try:target.stop()
        finally:runtime.stop()
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('up','stop'));args=parser.parse_args()
    try:
        runtime.STATE.mkdir(parents=True,exist_ok=True)
        if args.command=='up':
            inherited=os.environ.get('SBARBASE_WORKER_FD')
            with hba_startup.acquire(runtime.STATE,worker_fd=int(inherited) if inherited else None) as startup:
                main(args.command,startup=startup)
        else:
            with (runtime.STATE/'operation.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main(args.command)
        print('Installation runtime '+args.command+' completed')
    except Exception:
        # The public message stays fixed; the cause goes to a private diagnostic.
        diagnostics=runtime.STATE/'diagnostics'
        diagnostics.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(diagnostics,0o700)
        path=diagnostics/('installation-runtime-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S')+'.log')
        path.write_text(traceback.format_exc())
        os.chmod(path,0o600)
        raise SystemExit('Installation runtime refused or incomplete; diagnostics: '+str(path)) from None
