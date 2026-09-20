"""Host SIGKILL checkpoints for isolated HBA journal registration only."""
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
import hba_authority as authority
import hba_journal as journal
import hba_reconcile
import hba_generation


def docker(*args,data=None):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=True,timeout=30)


def child(config_path):
    config=json.loads(Path(config_path).read_text())
    snapshot=authority.Snapshot(**config['snapshot'])
    prepared=atomic_hba.Prepared(**config['prepared'])
    checkpoint=journal.publish.__code__ if config['checkpoint']=='before-register' else authority.update.__code__
    def profile(frame,event,arg):
        if event=='return' and frame.f_code is checkpoint:
            # Parent observes kernel-stopped status of its own unreaped child.
            os.kill(os.getpid(),signal.SIGSTOP)
    sys.setprofile(profile)
    try:journal.begin(docker,Path(config['journal']),snapshot,prepared,config['token'],config['identity'])
    finally:sys.setprofile(None)
    raise RuntimeError('Expected checkpoint was not interrupted')


def kill_at_checkpoint(config_path):
    process=subprocess.Popen(['/usr/bin/python3',str(Path(__file__).resolve()),'--child',str(config_path)],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            state=os.waitid(os.P_PID,process.pid,os.WSTOPPED|os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if state is not None:
                if state.si_code!=os.CLD_STOPPED or state.si_status!=signal.SIGSTOP:
                    raise RuntimeError('Journal child exited before checkpoint')
                # waitid WNOWAIT retains PID ownership until signal and reap.
                os.kill(process.pid,signal.SIGKILL)
                if process.wait(timeout=10)!=-signal.SIGKILL:raise RuntimeError('Journal child did not die by SIGKILL')
                return
            time.sleep(.05)
        raise RuntimeError('Journal child did not reach stopped checkpoint')
    finally:
        # Never signal a reaped process group. On unexpected failure, terminate
        # descendants while the owned session leader is still unreaped.
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=10)


def run(container,snapshot,check,target):
    for checkpoint,expected in (('before-register','absent'),('after-register','active')):
        # Separate disposable host fixtures, not alternative production slots.
        with tempfile.TemporaryDirectory(prefix='sbar-hba-host-crash-') as directory:
            root=Path(directory)
            path=root/journal.NAME
            hba_generation.publish(root,target,snapshot.generation)
            for name in hba_reconcile.NAMES:(root/name).touch(mode=0o600)
            prepared=atomic_hba.prepare(docker,container,'local all all reject\n')
            token=str(uuid.uuid4());identity={'kind':'startup','startup':str(uuid.uuid4())}
            config={'checkpoint':checkpoint,'snapshot':asdict(snapshot),'prepared':asdict(prepared),
                    'journal':str(path),'token':token,'identity':identity}
            config_path=root/'input.json';config_path.write_text(json.dumps(config));config_path.chmod(0o600)
            before=docker('exec',container,'cat','/etc/postgresql/pg_hba.conf').stdout
            kill_at_checkpoint(config_path)
            saved=journal.load(path)
            check(checkpoint+': SIGKILL leaves exact immutable host intent',saved['token']==token and saved['registry']==authority.digest(snapshot.text) and saved['content']==prepared.content)
            observed=journal.inspect(docker,path)
            check(checkpoint+': fresh inspection observes '+expected+' authority',observed['authority']==expected and observed['application']=='unknown' and observed['activation']=='unknown')
            calls=[]
            def forbidden(*args,**kwargs):calls.append(args);raise AssertionError('Unexpected replacement dispatch')
            try:journal.begin(forbidden,path,snapshot,prepared,str(uuid.uuid4()),identity)
            except FileExistsError:pass
            else:raise AssertionError('Interrupted journal permitted replacement')
            check(checkpoint+': interrupted intent blocks replacement before dispatch',not calls)
            check(checkpoint+': registration-only crash does not change HBA',docker('exec',container,'cat','/etc/postgresql/pg_hba.conf').stdout==before)
            # Isolated exact-token retirement leaves startup blocked by its journal.
            before_journal=path.read_bytes()
            prior=snapshot
            retired=hba_reconcile.retire(docker,root,target=target)
            snapshot=authority.read(docker,container,snapshot.generation)
            check(checkpoint+': retirement preserves journal and revoked authority',retired['authority']=='revoked' and path.read_bytes()==before_journal)
            try:authority.update(docker,prior,token,saved['binding'])
            except subprocess.CalledProcessError as error:
                if error.returncode!=74:raise
            else:raise AssertionError('Delayed registration survived retirement')
            check(checkpoint+': delayed old registration rejected after retirement',True)
    return snapshot


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--child':raise SystemExit('Internal fixture child only')
    child(sys.argv[2])
