"""Live fault injection against disposable containers with no data volumes."""
import importlib.util
import json
import secrets
from pathlib import Path
from unittest.mock import patch
import run as lab
import durable_runtime as runtime

spec=importlib.util.spec_from_file_location('restore_cleanup_live',Path(__file__).with_name('recovery-restore-db.py'))
restore=importlib.util.module_from_spec(spec);spec.loader.exec_module(restore)


def main():
    prefix='sbarbase-cleanup-'+secrets.token_hex(6)
    helper=prefix+'-helper';db=prefix+'-db';record=runtime.STATE/(prefix+'.json')
    image=json.loads((lab.ROOT/'lab/distro-image.lock.json').read_text())['id']
    checks=[];owned={}
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def inspect(name):return json.loads(lab.docker('container','inspect',name).stdout)[0]
    try:
        for name in (helper,db):
            if lab.docker('container','inspect',name,check=False).returncode==0:raise RuntimeError('Name collision')
            owned[name]=lab.docker('run','-d','--pull','never','--name',name,'--label','io.sbarbase.owner=recovery-target','--label','io.sbarbase.cleanup-fixture='+prefix,'--network','none','--memory','64m','--memory-swap','64m','--cpus','0.25','--pids-limit','16','--entrypoint','sleep',image,'120').stdout.strip()
        original=lab.docker
        def injected(*args,**kwargs):
            if args==('rm','-f',helper):raise RuntimeError('Injected helper removal failure')
            return original(*args,**kwargs)
        descriptor={'status':'database-verified','stage':'verified'}
        with patch.object(restore.lab,'docker',injected):
            try:restore.cleanup_target(descriptor,record,helper,db)
            except RuntimeError:pass
            else:raise RuntimeError('Fault was not observed')
        check('helper removal failure records cleanup failure',json.loads(record.read_text())['status']=='cleanup-failed')
        check('database container actually stopped despite helper failure',not inspect(db)['State']['Running'])
        check('failed helper remains available for explicit cleanup',inspect(helper)['State']['Running'])
        # Reconciliation retries cleanup only, never replays database restoration.
        restore.cleanup_target(descriptor,record,helper,db)
        check('cleanup retry removes owned helper',lab.docker('container','inspect',helper,check=False).returncode!=0)
        check('cleanup retry leaves target stopped',not inspect(db)['State']['Running'])
        check('cleanup retry does not upgrade failed verification to success',json.loads(record.read_text())['status']=='failed')
    finally:
        for name,identity in owned.items():
            result=lab.docker('container','inspect',identity,check=False)
            if result.returncode==0:
                state=json.loads(result.stdout)[0]
                if state['Config']['Labels'].get('io.sbarbase.cleanup-fixture')!=prefix:raise RuntimeError('Fixture ownership changed')
                lab.docker('rm','-f',identity)
    (lab.ROOT/'docs/evidence/recovery-cleanup-checks.json').write_text(json.dumps({'scope':'Actual cleanup function with injected helper-remove failure and real disposable container shutdown, followed by cleanup retry. No database contents, SIGKILL recovery or daemon outage exercised. Retained data volumes untouched.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' live recovery cleanup checks passed')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Cleanup probe failed; inspect only its owned fixture resources') from None
