"""Actual host death after successful durable HBA completion publication."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import atomic_hba
import hba_apply
import hba_authority as authority
import hba_generation
import hba_journal as journal
import hba_settlement
import hba_startup
import hba_target


def docker(*args,data=None):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=True,timeout=30)


def child(state):
    config=json.loads((state/'input.json').read_text())
    target=hba_target.Target(**config['target'])
    with hba_startup.acquire(state) as lease:
        snapshot=hba_generation.read_existing(docker,state,target=target)
        prepared=atomic_hba.prepare(docker,target.container_id,config['content'])
        lease.begin(docker,snapshot,prepared,config['token'],target=target)
        hba_apply.execute(docker,state,lease.descriptors,target=target,startup=lease)
        # This line is reached only after normal return, including both fsyncs.
        os.kill(os.getpid(),signal.SIGSTOP)
    raise RuntimeError('Expected completed child to be killed')


def run(container,target,generation,content,check):
    with tempfile.TemporaryDirectory(prefix='sbar-hba-completed-crash-') as directory:
        state=Path(directory);token=str(uuid.uuid4())
        hba_generation.publish(state,target,generation)
        config={'target':asdict(target),'token':token,'content':content}
        (state/'input.json').write_text(json.dumps(config));(state/'input.json').chmod(0o600)
        with (state/'child.log').open('wb') as log:
            process=subprocess.Popen(['/usr/bin/python3',str(Path(__file__).resolve()),'--child',str(state)],stdout=log,stderr=log,start_new_session=True)
            try:
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    observed=os.waitid(os.P_PID,process.pid,os.WSTOPPED|os.WEXITED|os.WNOHANG|os.WNOWAIT)
                    if observed is not None:
                        if observed.si_code!=os.CLD_STOPPED or observed.si_status!=signal.SIGSTOP:raise RuntimeError('Applied child exited before durable completion checkpoint')
                        break
                    time.sleep(.05)
                else:raise RuntimeError('Applied child checkpoint timed out')
                os.kill(process.pid,signal.SIGKILL)
                check('actual host SIGKILL after successful completion persistence',process.wait(timeout=10)==-signal.SIGKILL)
            finally:
                if process.returncode is None:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait(timeout=10)
        original=journal.read_text(state/journal.NAME);record=journal.decode(original)
        witness=hba_apply.read_completion(state,record,original)
        before=hba_apply.file_digest(docker,container)
        calls=[]
        def traced(*args,**kwargs):calls.append(args);return docker(*args,**kwargs)
        outcome=hba_settlement.complete_applied(traced,state,target=target)
        check('fresh ownership settles exact durable completion after host death',outcome['witness']==witness and outcome['activation']=='unknown' and not (state/journal.NAME).exists())
        check('success settlement makes no apply or PostgreSQL SQL call',not any(authority.APPLY in args or 'psql' in args for args in calls))
        check('success settlement leaves applied HBA bytes unchanged',hba_apply.file_digest(docker,container)==before)
        check('successful historical outcome remains readable after journal removal',hba_settlement.read(state,token)==outcome)


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--child':raise SystemExit('Internal applied fixture child only')
    child(Path(sys.argv[2]))
