"""Server preflight and installation driver for sbarbase.

Commands:
  check   read-only preflight; prints findings and exits non-zero on blockers
  plan    print the exact steps install would run, without running them
  install perform the steps below, stopping at the first failure
  smoke   verify a running installation (management Auth, console, gateway)

Rules:
- Never print or accept secrets in arguments. The operator identity is supplied
  through a private 0600 JSON file read on stdin.
- Never modify retained containers or volumes; adopt them explicitly instead.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/'.lab'/'upstream'
PRIVATE=ROOT/'.secrets'/'upstream'
PLANNED_MIB=5888
RESERVE_MIB=2560
PLANNED_CPUS=5.75
CPU_SPARE=2
MIN_FREE_BYTES=12*1024**3
LOCKS=('distro-image.lock.json','images.lock.json','storage-image.lock.json')


def run(command,*,check=True,stdin=None,env=None,cwd=None):
    return subprocess.run(command,input=stdin,text=True,capture_output=True,check=check,timeout=600,env=env,cwd=cwd)


def docker(*args,**kwargs):
    return run(['docker',*args],**kwargs)


def versions():
    findings=[]
    if sys.platform!='linux':findings.append(('blocker','Host must be Linux'))
    if not shutil.which('docker'):findings.append(('blocker','docker CLI not found'))
    if not shutil.which('bun'):findings.append(('blocker','bun not found (package manager and console build)'))
    python=Path('/usr/bin/python3')
    if not python.exists():findings.append(('blocker','/usr/bin/python3 not found'))
    else:
        result=run([str(python),'-c','import sys;print("%d.%d"%sys.version_info[:2])'])
        if tuple(int(part) for part in result.stdout.strip().split('.'))<(3,14):
            findings.append(('blocker','/usr/bin/python3 must be 3.14 or newer, found '+result.stdout.strip()))
    return findings


def daemon():
    findings=[]
    result=docker('info','--format','{{json .}}',check=False)
    if result.returncode:
        findings.append(('blocker','Docker daemon unreachable'));return findings
    info=json.loads(result.stdout)
    if info.get('OSType')!='linux':findings.append(('blocker','Native Linux containers required'))
    if info.get('Name')!=os.uname().nodename:
        findings.append(('warning','Docker daemon host differs from this host: remote daemons are not supported'))
    return findings


def images():
    findings=[]
    pins={}
    for name in LOCKS:
        pins[name]=json.loads((ROOT/'lab'/name).read_text())
    pinned=[]
    for name,lock in pins.items():
        if 'id' in lock:pinned.append((name,lock['id']))
        for key,value in lock.items():
            if isinstance(value,dict) and 'id' in value:pinned.append((name+':'+key,value['id']))
    for name,digest in pinned:
        if docker('image','inspect',digest,check=False).returncode:
            findings.append(('action','Pinned image '+name+' is not local; install will pull '+digest[:24]+'...'))
    return findings


def capacity():
    findings=[]
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    cpus=os.cpu_count() or 0
    needed=PLANNED_MIB+RESERVE_MIB
    if memory/1024<needed:
        findings.append(('blocker',f'Host headroom insufficient: {memory//1024} MiB available, plan needs {needed} MiB'))
    if cpus<PLANNED_CPUS+CPU_SPARE:
        findings.append(('blocker',f'CPU count insufficient: {cpus} available, plan needs {int(PLANNED_CPUS)+CPU_SPARE}'))
    usage=shutil.disk_usage('/')
    if usage.free<MIN_FREE_BYTES:
        findings.append(('blocker',f'Disk free {usage.free//1024**3} GiB below the {MIN_FREE_BYTES//1024**3} GiB minimum'))
    return findings


def target_findings(target_names,state,current_prefix):
    """Only the current target is a blocker; other retained targets are history."""
    findings=[]
    prefixes=sorted({name.split('-db')[0] for name in target_names if name.endswith('-db')})
    for prefix in prefixes:
        pinned=(state/'targets'/prefix/'hba-generation.json').exists()
        if prefix==current_prefix:
            if not pinned:findings.append(('blocker','Current recovery target '+prefix+' has no generation pin: adopt it with lab/adopt-retained.py target'))
            else:findings.append(('action','Current recovery target '+prefix+' carries a generation pin'))
        elif not pinned:
            findings.append(('info','Historical recovery target '+prefix+' has no generation pin; the runtime does not start it'))
    return findings


def current_prefix():
    record=STATE/'recovery-target.json'
    if not record.exists():return None
    return json.loads(record.read_text()).get('prefix')


def state():
    findings=[]
    ignored=run(['git','check-ignore','-q',str(PRIVATE/'runtime.json')],cwd=ROOT,check=False)
    if ignored.returncode:
        findings.append(('blocker','.secrets/upstream must be git-ignored before credentials are written'))
    containers=docker('ps','-a','--filter','label=io.sbarbase.owner=durable-upstream','--format','{{.Names}}',check=False).stdout.split()
    targets=docker('ps','-a','--filter','label=io.sbarbase.owner=recovery-target','--format','{{.Names}}',check=False).stdout.split()
    running=docker('ps','--filter','label=io.sbarbase.owner=durable-upstream','-q',check=False).stdout.strip()
    if running:findings.append(('blocker','Owned containers are already running; stop or supervise them instead of installing'))
    for name in ('hba-operation.json','worker-effect.json'):
        if (STATE/name).exists():findings.append(('blocker','Pending authority state '+name+' requires reconciliation before install'))
    if containers or targets:
        findings.append(('action',f'Retained installation detected ({len(containers)} source, {len(targets)} target containers)'))
        if not (STATE/'hba-generation.json').exists():
            findings.append(('blocker','Retained source has no generation pin: adopt it with lab/adopt-retained.py source'))
        findings.extend(target_findings(targets,STATE,current_prefix()))
    else:
        findings.append(('action','No installation containers: this is a fresh install'))
    return findings


def preflight():
    checks=versions()+daemon()+images()+capacity()+state()
    return checks


def report(checks,title='Preflight'):
    blockers=[detail for kind,detail in checks if kind=='blocker']
    for kind,detail in checks:
        print(f'{kind:>8}  {detail}')
    print(f'{title}: {len(blockers)} blocker(s), {len([1 for kind,_ in checks if kind=="action"])} action(s)')
    return not blockers


def npm_install():
    if not (ROOT/'node_modules').exists():
        run(['bun','install'],cwd=ROOT)


def install(bootstrap_file):
    checks=preflight()
    if not report(checks):raise SystemExit('Preflight failed; nothing was installed')
    STATE.mkdir(parents=True,exist_ok=True)
    PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(PRIVATE,0o700)
    print('step 1/5  state and secret directories prepared')
    for lock in LOCKS:
        entry=json.loads((ROOT/'lab'/lock).read_text())
        digest=entry['id']
        if docker('image','inspect',digest,check=False).returncode:
            if docker('pull',digest,check=False).returncode:raise SystemExit('Pinned image pull failed for '+lock)
    print('step 2/5  pinned images present')
    npm_install()
    run(['bun','run','build:ui'],cwd=ROOT)
    print('step 3/5  console built')
    env={**os.environ}
    result=run(['/usr/bin/python3','lab/installation_runtime.py','up'],cwd=ROOT,check=False,env=env)
    if result.returncode:raise SystemExit('Runtime startup failed: '+result.stderr.strip())
    print('step 4/5  owned runtime started')
    if bootstrap_file is not None:
        payload=Path(bootstrap_file).read_text()
        boot=run(['/usr/bin/python3','lab/bootstrap.py','--stdin'],cwd=ROOT,check=False,stdin=payload)
        if boot.returncode:raise SystemExit('Operator bootstrap failed: '+boot.stderr.strip())
        print('step 5/5  operator identity bootstrapped')
    else:
        print('step 5/5  operator bootstrap skipped; run: /usr/bin/python3 lab/bootstrap.py')
    print('Installation ready. Supervise it with deploy/sbarbase.service or run the foreground supervisor:')
    print('  bun lab/upstream-server.ts   # after /usr/bin/python3 lab/installation_runtime.py up')
    print('Smoke test: /usr/bin/python3 lab/install_server.py smoke')


def smoke():
    checks=[]
    envs=json.loads((STATE/'endpoints.json').read_text()) if (STATE/'endpoints.json').exists() else {}
    management=json.loads((STATE/'management.json').read_text()) if (STATE/'management.json').exists() else {}
    routes={'management-auth':management.get('auth')}
    for environment,endpoints in envs.items():
        routes[environment+':auth']=endpoints.get('auth')
        routes[environment+':rest']=endpoints.get('rest')
    import urllib.request
    for name,base in routes.items():
        if not base:checks.append((name,'missing endpoint'));continue
        url=base+('/health' if name.endswith('auth') or name=='management-auth' else '/')
        try:
            with urllib.request.urlopen(url,timeout=5) as response:code=response.status
        except Exception as error:code=str(error)
        checks.append((name,code))
    server=STATE/'server.json'
    console='missing'
    if server.exists():
        pid=json.loads(server.read_text()).get('pid')
        console=f'server pid {pid}' if pid else 'no pid'
    for name,value in checks:print(f'{name:>22}  {value}')
    print('console:',console)
    ok=all(value==200 for name,value in checks if name.endswith('auth') or name.endswith('rest'))
    return ok


def main():
    parser=argparse.ArgumentParser(description='sbarbase server preflight and installation')
    parser.add_argument('command',choices=('check','plan','install','smoke'))
    parser.add_argument('--bootstrap-file',help='private 0600 JSON with email, password and organization')
    args=parser.parse_args()
    if args.command=='check':
        raise SystemExit(0 if report(preflight()) else 1)
    if args.command=='plan':
        print('1. preflight (docker, bun, /usr/bin/python3 3.14+, pinned images, headroom, disk, state)')
        print('2. create private state and secret directories (0700)')
        print('3. pull each pinned image by digest when it is not local')
        print('4. bun install when node_modules is absent')
        print('5. bun run build:ui')
        print('6. /usr/bin/python3 lab/installation_runtime.py up')
        print('7. /usr/bin/python3 lab/bootstrap.py --stdin  (from the private JSON file)')
        print('8. supervise: deploy/sbarbase.service, or the foreground supervisor')
        return
    if args.command=='install':
        install(args.bootstrap_file);return
    raise SystemExit(0 if smoke() else 1)


if __name__=='__main__':main()