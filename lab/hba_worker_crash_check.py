"""Actual worker/guardian crash probes, only inside a private fresh fixture."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
from contextlib import closing
import hba_apply
import hba_authority
import hba_generation
import hba_journal
import hba_reconcile
import hba_settlement
import hba_target


HOOK = '''import json,os,signal,sys
from pathlib import Path
root=Path(__ROOT__)
state=root/'.lab/upstream'
phase=__PHASE__
entry=Path(sys.argv[0]).resolve()
native=entry==root/'lab/durable_runtime.py' and len(sys.argv)==3 and sys.argv[1]=='provision'
guardian=entry==root/'lab/worker_lock_exec.py'

def trace(frame,event,value):
    if event!='return':return
    file=Path(frame.f_code.co_filename).resolve()
    if native:
        expected=('hba_journal.py','begin') if phase=='after-intent' else ('hba_apply.py','execute')
        if file!=root/'lab'/expected[0] or frame.f_code.co_name!=expected[1]:return
        module=sys.modules.get(expected[0][:-3])
        if module is None or frame.f_code is not getattr(module,expected[1]).__code__:return
        if phase=='after-intent':
            if type(value).__name__!='Snapshot':return
        elif not isinstance(value,dict) or value.get('phase')!='applied-reload-acknowledged':return
        import effect_receipt,hba_journal,hba_authority,hba_apply
        original=hba_journal.read_text(state/hba_journal.NAME)
        record=hba_journal.decode(original)
        runtime,receipt,claim,attempt=effect_receipt.hba_identity(state,sys.argv[2])
        if record['identity']!={'kind':'worker','runtime':runtime,'receipt':receipt,'claim':claim,'attempt':attempt}:raise RuntimeError('Unexpected HBA crash identity')
        if phase=='after-witness' and hba_apply.read_completion(state,record,original)!=value:raise RuntimeError('Unexpected completion evidence')
        if phase=='after-intent' and (value.container_id!=record['container'] or value.generation!=record['generation']):raise RuntimeError('Unexpected registration evidence')
        sys.setprofile(None)
        effect_receipt.publish(state/'hba-crash-observed.json',{'phase':phase,'pid':os.getpid(),'identity':record['identity'],'token':record['token'],'journal_digest':hba_authority.digest(original)})
        os.kill(os.getpid(),signal.SIGKILL)
        raise RuntimeError('SIGKILL did not terminate native process')
    if guardian and file==root/'lab/worker_lock_exec.py' and frame.f_code.co_name=='main':
        if frame.f_locals.get('code')!=-signal.SIGKILL:return
        import effect_receipt
        child=frame.f_locals['child']
        if child.returncode!=-signal.SIGKILL:return
        sys.setprofile(None)
        effect_receipt.publish(state/'hba-crash-guardian.json',{'pid':child.pid,'exit_code':child.returncode,'receipt':frame.f_locals['record']['token']})

if native or guardian:sys.setprofile(trace)
'''


def run(repo,private,name,phase,command,check):
    if phase not in ('after-intent','after-witness'):raise ValueError('Unknown HBA crash phase')
    state=repo/'.lab/upstream'
    seed="import {Catalog} from './src/control/catalog';const c=new Catalog('.lab/upstream/control.sqlite');try{const o=c.createOrganization('crash-owner','Crash');const p=c.createProject('crash-owner',o,'HBA');const e=c.createEnvironment('crash-owner',p,'production');await Bun.write('.lab/upstream/hba-crash-probe.json',JSON.stringify({environment:e}));}finally{c.close();}"
    command(['bun','-e',seed],label='crash-seed')
    environment=json.loads((state/'hba-crash-probe.json').read_text())['environment']
    hook=private/'hba-crash-hook';hook.mkdir(mode=0o700)
    script=hook/'sitecustomize.py'
    script.write_text(HOOK.replace('__ROOT__',repr(str(repo.resolve()))).replace('__PHASE__',repr(phase)));script.chmod(0o600)
    command(['/usr/bin/python3','lab/worker.py','--upstream'],label='crash-worker',timeout=220,
            env=dict(os.environ,PYTHONPATH=str(hook)),expect_failure=True)
    observed=json.loads((state/'hba-crash-observed.json').read_text())
    guardian=json.loads((state/'hba-crash-guardian.json').read_text())
    check('guardian observed actual native SIGKILL at '+phase,guardian=={'pid':observed['pid'],'exit_code':-signal.SIGKILL,'receipt':observed['identity']['receipt']} and observed['phase']==phase)
    with hba_reconcile.fresh_ownership(state):
        check('fresh locks confirm prior guardian and native ownership ended',True)
    receipt_path=state/'worker-effect.json';receipt_bytes=receipt_path.read_bytes();receipt=json.loads(receipt_bytes)
    job=receipt['job'];runtime=job['runtime']
    original=hba_journal.read_text(state/hba_journal.NAME);record=hba_journal.decode(original)
    check('crash journal binds actual pending worker receipt',receipt['phase']=='pending' and receipt['hbaProtocol']==1 and job['environment']==environment and observed['journal_digest']==hba_authority.digest(original) and record['identity']==observed['identity']=={'kind':'worker','runtime':runtime,'receipt':receipt['token'],'claim':job['claim'],'attempt':job['attempt']})
    stage_path=state/'effect-stages'/(receipt['token']+'.json');stage_bytes=stage_path.read_bytes()
    check('native crash remains at services without whole-job witness',json.loads(stage_bytes)['stage']=='services' and not (state/'effect-outcomes'/(receipt['token']+'.json')).exists())
    def job_state():
        with closing(sqlite3.connect(state/'control.sqlite')) as db:
            return db.execute('SELECT runtime,claim,attempt,state FROM provision_jobs WHERE environment=?',(environment,)).fetchone()
    before_job=job_state()
    check('crashed worker claim remains running and exact',before_job==(runtime,job['claim'],job['attempt'],'running'))
    command(['/usr/bin/python3','lab/worker.py','--upstream','--settle-only'],label='crash-worker-blocked',expect_failure=True)
    check('pending HBA journal blocks worker before catalog recovery','Pending HBA operation requires reconciliation before worker startup' in (private/'crash-worker-blocked.stderr').read_text() and receipt_path.read_bytes()==receipt_bytes and job_state()==before_job and hba_journal.read_text(state/hba_journal.NAME)==original)
    command(['/usr/bin/python3','lab/installation_runtime.py','up'],label='crash-startup-blocked',expect_failure=True)
    check('unresolved HBA and worker state prevent installation startup',receipt_path.read_bytes()==receipt_bytes and hba_journal.read_text(state/hba_journal.NAME)==original)
    pin=hba_generation.load(state)
    target=hba_target.Target(**pin['target'])
    if target.name!=name+'-db':raise RuntimeError('Crash fixture target mismatch')
    calls=[]
    def docker(*args,data=None):
        calls.append(args)
        return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,timeout=30,check=True)
    snapshot=hba_authority.read(docker,target.container_id,pin['generation'])
    check('crash leaves exact HBA authority active',hba_authority.decode(snapshot.text,pin['generation'])['operations'][record['token']]=={'binding':record['binding'],'state':'active'})
    before_digest=hba_apply.file_digest(docker,target.container_id);calls.clear()
    if phase=='after-intent':
        check('intent crash has no native HBA dispatch attempt',not (state/hba_apply.ATTEMPTS/(record['token']+'.json')).exists() and not (state/hba_apply.COMPLETIONS/(record['token']+'.json')).exists() and before_digest==record['expected'])
        outcome=hba_settlement.cancel_baseline(docker,state,target=target)
        check('intent-only HBA cancellation archives baseline observation',outcome['kind']=='retired-baseline-observed')
    else:
        witness=hba_apply.read_completion(state,record,original)
        check('applied crash has exact durable reload witness',witness['content_digest']==before_digest and witness['activation']=='unknown')
        outcome=hba_settlement.complete_applied(docker,state,target=target)
        check('applied HBA completion archives exact witness',outcome['witness']==witness)
    check('HBA-only reconciliation never applies or runs SQL',not any(hba_authority.APPLY in args or 'psql' in args for args in calls))
    check('HBA-only reconciliation preserves file bytes and worker uncertainty',hba_apply.file_digest(docker,target.container_id)==before_digest and receipt_path.read_bytes()==receipt_bytes and stage_path.read_bytes()==stage_bytes and job_state()==before_job and not (state/hba_journal.NAME).exists())
    check('HBA crash outcome is retained without activation claim',hba_settlement.read(state,record['token'])==outcome and outcome['activation']=='unknown')
    command(['/usr/bin/python3','lab/worker.py','--upstream','--settle-only'],label='crash-job-still-blocked',expect_failure=True)
    check('settling HBA alone cannot replay or settle services-stage job','Preflight evidence mismatch or external effects already started' in (private/'crash-job-still-blocked.stderr').read_text() and receipt_path.read_bytes()==receipt_bytes and stage_path.read_bytes()==stage_bytes and job_state()==before_job and not (state/'effect-outcomes'/(receipt['token']+'.json')).exists())
