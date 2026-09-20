"""Producer EOF and helper interruption against pinned upstream HBA files."""
import hashlib
import subprocess
import time
import uuid
import atomic_hba


def run(container,admin,check,docker,sql):
    path='/etc/postgresql/pg_hba.conf'
    old=docker('exec',container,'cat',path).stdout
    new='local all all trust\n# complete replacement fixture\nhost all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n'
    def read():return docker('exec',container,'cat',path).stdout
    def launch(script,content,expected=new):
        payload=expected.encode()
        return docker('exec','-i',container,'sh','-c',script,'sbarbase-hba',str(len(payload)),hashlib.sha256(payload).hexdigest(),data=content,check=False)
    metadata=docker('exec',container,'stat','-c','%a:%u:%g',path).stdout.strip()
    legacy=launch('cat > /etc/postgresql/pg_hba.conf',new[:17])
    check('legacy producer EOF publishes truncated active file',legacy.returncode==0 and read()==new[:17])
    if launch(atomic_hba.SCRIPT,old,expected=old).returncode:raise RuntimeError('Atomic baseline restoration failed')
    partial=launch(atomic_hba.SCRIPT,new[:17])
    check('atomic writer rejects producer EOF before expected length',partial.returncode!=0 and read()==old)
    wrong=new.replace('complete','incorrect',1)
    wrong=wrong[:len(new)]
    if len(wrong)!=len(new):raise RuntimeError('Equal-length checksum fixture unavailable')
    corrupt=launch(atomic_hba.SCRIPT,wrong)
    check('atomic writer rejects same-length wrong content',corrupt.returncode!=0 and read()==old)
    marker='/tmp/sbar-hba-'+uuid.uuid4().hex
    stop=f'printf \'%s\' "$$" > {marker}; kill -STOP "$$"\n'
    def interrupted(script,callback=lambda:None):
        payload=new.encode()
        child=subprocess.Popen(['docker','exec','-i',container,'sh','-c',script,'sbarbase-hba',str(len(payload)),hashlib.sha256(payload).hexdigest()],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
        pid=None;stamp=None
        try:
            child.stdin.write(new);child.stdin.close();child.stdin=None
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                value=docker('exec',container,'cat',marker,check=False)
                if value.returncode==0 and value.stdout.isdigit():
                    pid=value.stdout
                    fields=docker('exec',container,'cat','/proc/'+pid+'/stat').stdout.split()
                    if fields[2]=='T':stamp=fields[21];break
                time.sleep(.05)
            if stamp is None:raise RuntimeError('HBA writer did not reach stopped checkpoint')
            callback()
            observed=read()
            # Stopped process plus exact kernel start time pins the owned helper.
            docker('exec',container,'sh','-c',f'[ "$(awk \'{{print $22}}\' /proc/{pid}/stat)" = "{stamp}" ] && kill -KILL {pid}')
            child.wait(timeout=10)
            docker('exec',container,'rm','-f',marker)
            return observed,child.returncode
        finally:
            if child.poll() is None:
                # The outer fixture destroys this exact container on any failure.
                child.kill();child.wait(timeout=5)
    before=atomic_hba.SCRIPT.replace('mv -f --',stop+'mv -f --',1)
    observed,code=interrupted(before)
    check('helper SIGKILL before rename preserves complete old file',code!=0 and observed==old and read()==old)
    after=atomic_hba.SCRIPT.replace('sync "$directory"',stop+'sync "$directory"',1)
    observed,code=interrupted(after)
    check('helper SIGKILL after rename leaves complete new file',code!=0 and observed==new and read()==new)
    check('replacement preserves mode uid and gid',docker('exec',container,'stat','-c','%a:%u:%g',path).stdout.strip()==metadata)
    check('new complete HBA parses without errors',sql(container,'SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;').stdout.strip()=='0')
    check('explicit reload acknowledges signal',sql(container,'SELECT pg_reload_conf();').stdout.strip()=='t')
    if launch(atomic_hba.SCRIPT,old,expected=old).returncode:raise RuntimeError('Atomic baseline restoration failed')
    check('normal complete replacement returns original content',read()==old)
    sql(container,'SELECT pg_reload_conf();')

    def attempt(prepared):
        results=[]
        def capture(*args,**kwargs):
            result=docker(*args,**kwargs,check=False);results.append(result);return result
        atomic_hba.apply(capture,prepared)
        return results[0]
    first=atomic_hba.prepare(docker,container,new)
    second=atomic_hba.prepare(docker,container,old)
    check('prepared request captures exact container identity',first.container_id==container)
    check('first prepared write succeeds',attempt(first).returncode==0 and read()==first.content)
    held=read()
    check('competing stale prepared write is rejected',attempt(second).returncode==74 and read()==held)
    check('successful prepared request cannot replay',attempt(first).returncode==74 and read()==held)
    repeated=atomic_hba.prepare(docker,container,new)
    check('same rules receive a different revision header',repeated.content!=first.content)
    check('same rules new revision succeeds',attempt(repeated).returncode==0 and read()==repeated.content)
    check('old expected revision does not revive after same rules',attempt(second).returncode==74 and read()==repeated.content)
    waiting=atomic_hba.prepare(docker,container,old)
    def while_locked():
        check('busy writer lock refuses without changing target',attempt(waiting).returncode==73 and read()==repeated.content)
    paused=atomic_hba.CAS_SCRIPT.replace('current=$(sha256sum',stop+'current=$(sha256sum',1)
    interrupted(paused,while_locked)
    check('writer lock releases after stopped helper death',attempt(waiting).returncode==0 and read()==waiting.content)
    check('versioned HBA remains valid configuration',sql(container,'SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;').stdout.strip()=='0')
