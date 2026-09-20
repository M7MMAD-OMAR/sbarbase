"""Isolated authority protocol against the pinned image's real filesystem/tools.

Does not start PostgreSQL or test reload, host journals or process interruption.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import re
import uuid
import atomic_hba
import hba_authority as authority

OWNER='hba-authority-probe'


def docker(*args,data=None):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=True,timeout=30)


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
        except (RuntimeError,ValueError,subprocess.CalledProcessError) as error:
            if code is not None and getattr(error,'returncode',None)!=code:raise
            checks.append(label)
        else:raise AssertionError(label)
    try:
        cid=docker('run','-d','--pull=never','--restart=no','--cidfile',str(cidfile),'--name',name,'--label','io.sbarbase.owner='+OWNER,
                   '--network=none','--memory=128m','--cpus=0.5','--pids-limit=32','--entrypoint','sh',image,'-c','exec sleep 600').stdout.strip()
        info=json.loads(docker('inspect',cid).stdout)[0]
        check('bounded isolated pinned container',info['Image']==image and info['HostConfig']['Memory']==128*1024**2 and info['HostConfig']['NetworkMode']=='none' and not info['HostConfig']['PortBindings'])
        original=docker('exec',cid,'cat','/etc/postgresql/pg_hba.conf').stdout
        generation=str(uuid.uuid4());token=str(uuid.uuid4());identity={'kind':'startup','id':str(uuid.uuid4())}
        initial=authority.initialize(docker,cid,generation)
        prepared=atomic_hba.prepare(docker,cid,'local all all reject\n')
        binding=authority.operation_binding(prepared,identity)
        active=authority.update(docker,initial,token,binding)
        permit=authority.authorize(active,prepared,token,identity)
        revoked=authority.update(docker,active,token,binding,revoke=True)
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
