"""Source plus retained moved target lifecycle for the local foreground supervisor."""
import argparse
import fcntl
import json
import durable_runtime as runtime
import run as lab
from target_runtime import TargetRuntime
from combined_admission import CombinedAdmission
import source_fence


def main(command):
    moved=(runtime.STATE/'cutover-operation.json').exists()
    if not moved:
        if command=='up':runtime.Runtime().start()
        else:runtime.stop()
        return
    if command=='stop':
        try:TargetRuntime(stop_only=True).stop()
        finally:runtime.stop()
        return
    target=TargetRuntime()
    source=runtime.Runtime()
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
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main(args.command)
        print('Installation runtime '+args.command+' completed')
    except Exception:raise SystemExit('Installation runtime refused or incomplete; inspect retained state') from None
