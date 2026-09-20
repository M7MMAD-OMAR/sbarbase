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


def sample_usage(names):
    """Actual resident usage of a placement, sampled from the daemon."""
    result=lab.docker('stats','--no-stream','--format','{{.Name}}\t{{.MemUsage}}',*names)
    usage={}
    for line in result.stdout.strip().splitlines():
        if '\t' not in line:continue
        name,memory=line.split('\t',1)
        value=memory.split('/')[0].strip()
        try:
            unit=value[-3:].lower()
            if unit in ('kib','mib','gib'):
                number=float(value[:-3]);factor={'kib':1024,'mib':1024**2,'gib':1024**3}[unit]
            elif value[-2:].lower()=='kb':
                number=float(value[:-2]);factor=1024
            elif value[-1:].lower()=='b':
                number=float(value[:-1]);factor=1
            else:
                continue
        except ValueError:
            continue
        if not number:continue
        usage[name.strip()]=int(number*factor)
    return usage


def record_source_stage_usage():
    """Sample what the running source stage actually costs, before the combined check.

    The combined admission measures the host while this stage is already up, so the
    preflight figure alone understates the headroom a combined start needs. Writing
    the measurement down turns that into a number instead of a guess.
    """
    try:
        names=[name for name in lab.docker('ps','--format','{{.Names}}').stdout.split()
               if name.startswith(runtime.PREFIX+'-') or name==runtime.DB]
        if not names:return None
        usage=sample_usage(names)
        if not usage:return None
        record={'sampled_containers':len(usage),'total_mib':sum(usage.values())//1024**2,
                'per_container_mib':{name:value//1024**2 for name,value in sorted(usage.items())},
                'note':'Actual usage of the source stage, sampled immediately before the combined admission check.'}
        (lab.ROOT/'docs/evidence/source-stage-footprint.json').write_text(json.dumps(record,indent=2)+'\n')
        return record
    except Exception:
        return None


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
        record_source_stage_usage()
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
