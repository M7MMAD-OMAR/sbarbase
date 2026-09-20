"""Isolated authority protocol against the pinned image's real filesystem/tools.

Includes helper SIGKILL around registry rename and host SIGKILL before/after registration. Does not start PostgreSQL or test
reload, host lease recovery, power loss or whole-operation recovery.
A private immutable host journal precedes initial registration and supports read-only inspection.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import re
import uuid
import atomic_hba
import hba_authority as authority
import hba_journal as journal
import hba_target
import hba_journal_crash_check as host_crash

OWNER='hba-authority-probe'


def docker(*args,data=None):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=True,timeout=30)


def interrupt_update(cid,snapshot,token,binding,after_rename):
    """Kill the exact stopped helper; outer fixture owns failure cleanup."""
    record=authority.decode(snapshot.text,snapshot.generation)
    record['operations'][token]={'binding':binding,'state':'revoked'}
    record['revision']=str(uuid.uuid4())
    text=authority.encode(record)
    marker='/tmp/authority-stop-'+uuid.uuid4().hex
    stop=f'printf "%s" "$$" > {marker}; kill -STOP "$$"\n'
    boundary='sync "$directory"' if after_rename else 'mv -f --'
    script=authority.UPDATE.replace(boundary,stop+boundary,1)
    child=subprocess.Popen(['docker','exec','-i',cid,'sh','-c',script,'authority-interrupt',
                            str(len(text.encode())),authority.digest(text),authority.digest(snapshot.text)],
                           stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
    try:
        child.stdin.write(text);child.stdin.close();child.stdin=None
        deadline=time.monotonic()+10
        stamp=None
        while time.monotonic()<deadline:
            observed=subprocess.run(['docker','exec',cid,'cat',marker],capture_output=True,text=True,timeout=5)
            if observed.returncode==0 and observed.stdout.isdigit():
                pid=observed.stdout
                fields=docker('exec',cid,'cat','/proc/'+pid+'/stat').stdout.split()
                if fields[2]=='T':stamp=fields[21];break
            time.sleep(.05)
        if stamp is None:raise RuntimeError('Authority helper did not stop at checkpoint')
        # Read only while the writer is demonstrably stopped holding the lock.
        before_kill=authority.read(docker,cid,snapshot.generation)
        docker('exec',cid,'sh','-c',f"[ \"$(awk '{{print $22}}' /proc/{pid}/stat)\" = \"{stamp}\" ] && kill -KILL {pid}")
        code=child.wait(timeout=10)
        docker('exec',cid,'rm',marker)
        after_kill=authority.read(docker,cid,snapshot.generation)
        expected=authority.Snapshot(cid,snapshot.generation,text) if after_rename else snapshot
        if code==0 or before_kill!=expected or after_kill!=expected:
            raise AssertionError('Interrupted registry did not preserve expected complete version')
        return after_kill
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)


def main():
    image=json.loads((Path(__file__).parent/'distro-image.lock.json').read_text())['id']
    name='sbar-hba-authority-'+uuid.uuid4().hex
    cid=None
    private=tempfile.TemporaryDirectory(prefix='sbar-hba-authority-')
    cidfile=Path(private.name)/'container.id'
    checks=[]
    def check(label,condition):
        if not condition:raise AssertionError(label)
        checks.append(label)
    def refused(label,call,code=None):
        try:call()
        except (RuntimeError,ValueError,FileExistsError,subprocess.CalledProcessError) as error:
            if code is not None and getattr(error,'returncode',None)!=code:raise
            checks.append(label)
        else:raise AssertionError(label)
    try:
        cid=docker('run','-d','--pull=never','--restart=no','--cidfile',str(cidfile),'--name',name,'--label','io.sbarbase.owner='+OWNER,
                   '--network=none','--memory=128m','--cpus=0.5','--pids-limit=32','--entrypoint','sh',image,'-c','exec sleep 600').stdout.strip()
        info=json.loads(docker('inspect',cid).stdout)[0]
        check('bounded isolated pinned container',info['Image']==image and info['HostConfig']['Memory']==128*1024**2 and info['HostConfig']['NetworkMode']=='none' and not info['HostConfig']['PortBindings'])
        original=docker('exec',cid,'cat','/etc/postgresql/pg_hba.conf').stdout
        generation=str(uuid.uuid4());token=str(uuid.uuid4());identity={'kind':'startup','startup':str(uuid.uuid4())}
        initial=authority.initialize(docker,cid,generation)
        prepared=atomic_hba.prepare(docker,cid,'local all all reject\n')
        captured=hba_target.capture(docker,name,OWNER,image)
        hba_target.require(docker,captured,initial,prepared)
        check('configured database target captured and rechecked by exact ID',captured.container_id==cid)
        for field,value in (('name','foreign-db'),('owner','foreign-owner'),('image','sha256:'+'0'*64)):
            expected={'container_id':cid,'name':name,'owner':OWNER,'image':image}
            expected[field]=value
            refused('live container validation rejects wrong configured '+field,lambda expected=expected:hba_target.require(docker,hba_target.Target(**expected),initial,prepared))
        binding=authority.operation_binding(prepared,identity)
        journal_file=Path(private.name)/journal.NAME
        active=journal.begin(docker,journal_file,initial,prepared,token,identity)
        check('durable host journal binds registered operation',journal.load(journal_file)['binding']==binding and journal.inspect(docker,journal_file)['authority']=='active')
        permit=authority.authorize(active,prepared,token,identity)
        revoked=authority.update(docker,active,token,binding,revoke=True)
        observed_journal=journal.inspect(docker,journal_file)
        check('read-only journal inspection observes revocation without activation claim',observed_journal['authority']=='revoked' and observed_journal['application']=='unknown' and observed_journal['activation']=='unknown')
        refused('same journal cannot create replacement intent',lambda:journal.begin(docker,journal_file,revoked,prepared,str(uuid.uuid4()),identity))
        refused('old permit rejected after revocation',lambda:authority.apply(docker,permit),75)
        check('rejected permit leaves HBA unchanged',docker('exec',cid,'cat','/etc/postgresql/pg_hba.conf').stdout==original)
        refused('stale snapshot cannot erase tombstone',lambda:authority.update(docker,initial,token,binding),74)
        refused('revoked token cannot register again',lambda:authority.update(docker,revoked,token,binding))
        new_prepared=atomic_hba.prepare(docker,cid,'local all all trust\n')
        refused('revoked operation cannot freshly prepare authority',lambda:authority.authorize(revoked,new_prepared,token,identity))
        token2=str(uuid.uuid4());binding2=authority.operation_binding(new_prepared,identity)
        second=authority.update(docker,revoked,token2,binding2)
        refused('competing active token refused',lambda:authority.update(docker,second,str(uuid.uuid4()),binding2))
        authority.apply(docker,authority.authorize(second,new_prepared,token2,identity))
        check('new active operation publishes exact content',docker('exec',cid,'cat','/etc/postgresql/pg_hba.conf').stdout==new_prepared.content)
        truncated=second.text[:20]
        refused('truncated registry update rejected',lambda:docker('exec','-i',cid,'sh','-c',authority.UPDATE,'probe',str(len(second.text.encode())),authority.digest(second.text),authority.digest(second.text),data=truncated))
        check('truncation preserves complete registry',authority.read(docker,cid,generation)==second)
        interrupted=interrupt_update(cid,second,token2,binding2,False)
        check('SIGKILL before registry rename preserves active version',interrupted==second)
        finished=interrupt_update(cid,second,token2,binding2,True)
        check('SIGKILL after registry rename preserves revoked version',authority.decode(finished.text,generation)['operations'][token2]['state']=='revoked')
        refused('post-crash tombstone refuses registration',lambda:authority.update(docker,finished,token2,binding2))
        token3=str(uuid.uuid4())
        resumed=authority.update(docker,finished,token3,binding2)
        check('dead helper releases lock for a new exact operation',authority.decode(resumed.text,generation)['operations'][token3]['state']=='active')
        idle=authority.update(docker,resumed,token3,binding2,revoke=True)
        host_crash.run(cid,idle,check)
        docker('exec',cid,'rm',authority.PATH)
        refused('missing registry refuses read',lambda:authority.read(docker,cid,generation))
        refused('retained marker prevents silent reinitialization',lambda:authority.initialize(docker,cid,generation))
    finally:
        if cid is None and cidfile.is_file():cid=cidfile.read_text().strip()
        if cid:
            if not re.fullmatch(r'[a-f0-9]{64}',cid):raise RuntimeError('Invalid cleanup container identity')
            info=json.loads(docker('inspect',cid).stdout)[0]
            if info['Image']!=image or info['Id']!=cid or info['Name']!='/'+name or info['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:raise RuntimeError('Cleanup identity mismatch')
            docker('rm','-fv',cid)
            check('exact disposable container removed',cid not in docker('ps','-aq','--no-trunc').stdout.split())
        private.cleanup()
    evidence={'scope':__doc__.strip(),'image':image,'count':len(checks),'checks':checks}
    destination=Path(__file__).parent.parent/'docs/evidence/hba-authority.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
