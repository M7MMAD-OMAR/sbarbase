"""SIGKILL the real idle supervisor, then restart the retained combined runtime."""
import fcntl
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import run as lab


def main():
    checks=[];first=None;second=None
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def start():return subprocess.Popen(['/usr/bin/python3','lab/dev.py'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    def ready(process):
        deadline=time.monotonic()+70
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Supervisor exited during startup')
            try:
                owner=json.loads(Path('.lab/upstream/supervisor.json').read_text());server=json.loads(Path('.lab/upstream/server.json').read_text())
                if owner['pid']==process.pid and owner['serverPid']==server['pid'] and request(server['url']+'/')==200:return server
            except (OSError,ValueError,KeyError):pass
            time.sleep(.1)
        raise RuntimeError('Supervisor startup deadline exceeded')
    def request(url):
        try:
            with urllib.request.urlopen(url,timeout=1) as response:return response.status
        except OSError:return None
    d=json.loads(Path('.lab/upstream/recovery-target.json').read_text())
    def digest():
        values=[]
        for table in ('public.durable_items','storage.objects'):
            query="SELECT md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]')) FROM "+table+' t;'
            values.append(lab.docker('exec','-i',d['database'],'psql','-X','-At','-U','supabase_admin','-d',d['environment'],data=query).stdout.strip())
        return values
    try:
        first=start();server=ready(first)
        check('combined supervisor available before crash',request(server['url']+'/')==200)
        before=digest()
        first.kill();check('supervisor terminated by SIGKILL',first.wait(timeout=10)==-9)
        unavailable=False
        for _ in range(100):
            if request(server['url']+'/') is None:unavailable=True;break
            time.sleep(.05)
        check('parent-bound gateway stops accepting after supervisor death',unavailable)
        released=False
        for _ in range(100):
            with Path('.lab/upstream/worker.lock').open('a') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);released=True
                except BlockingIOError:pass
            if released:break
            time.sleep(.05)
        check('idle worker exits and releases inherited lock after parent death',released)
        check('runtime containers remain for explicit startup reconciliation',bool(lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip()))
        second=start();ready(second)
        result=subprocess.run(['bun','lab/combined-gateway-check.ts'],capture_output=True,text=True,timeout=45)
        check('restarted supervisor serves combined console and four environments',result.returncode==0)
        check('target application and Storage metadata match before crash',digest()==before)
        fence=lab.docker('exec','-i','sbarbase-durable-db','psql','-X','-At','-U','supabase_admin','-d','postgres',data="SELECT NOT datallowconn FROM pg_database WHERE datname='"+d['environment']+"';").stdout.strip()
        check('moved source fence survives supervisor crash and restart',fence=='t')
    finally:
        for process in (second,first):
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=100)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        result=subprocess.run(['/usr/bin/python3','lab/installation_runtime.py','stop'],capture_output=True,timeout=90)
        if result.returncode:raise RuntimeError('Combined cleanup failed')
    check('all owned runtimes stopped after crash rehearsal',all(not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner).stdout.strip() for owner in ('durable-upstream','recovery-target')))
    (lab.ROOT/'docs/evidence/supervisor-crash-checks.json').write_text(json.dumps({'scope':'SIGKILL of real combined foreground supervisor with idle worker, parent-bound API/worker exit, restart and metadata digest preservation. Docker runtime intentionally survives crash until reconciliation. No active provisioning, in-flight writes, daemon failure or power-loss test.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' supervisor crash/restart checks passed')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Supervisor crash rehearsal failed; inspect only owned runtime state') from None
