"""Server preflight and installation driver for sbarbase.

Commands:
  check   read-only preflight; prints findings and exits non-zero on blockers
  plan    print the exact steps install would run, without running them
  install perform the steps below, stopping at the first failure
  smoke   verify a running installation (console, management Auth, environments)

Rules:
- Never print or accept secrets in arguments. The operator identity is supplied
  through a private 0600 JSON file read on stdin.
- Never modify retained containers or volumes; adopt them explicitly instead.
- Every mutating command holds the installation operation lock.
"""
import argparse
import datetime
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import console_build_check
import pinned_images_check
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


def pinned_images():
    """Every pinned component with a pullable 'repository@sha256:...' reference."""
    result=[]
    for lock in LOCKS:
        entry=json.loads((ROOT/'lab'/lock).read_text())
        for key,value in entry.items():
            if isinstance(value,dict) and isinstance(value.get('id'),str):
                result.append((lock+':'+key,value))
            elif key=='id' and isinstance(value,str):
                result.append((lock+':default',entry))
    references=[]
    for label,value in result:
        digests=[item for item in value.get('digests') or [] if isinstance(item,str) and '@sha256:' in item]
        references.append((label,value['id'],digests[0] if digests else value['id']))
    return references


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


def resolved_endpoint():
    """The socket the docker context resolves to, readable even without a daemon."""
    result=docker('context','inspect','--format','{{.Endpoints.docker.Host}}',check=False)
    return result.stdout.strip() or 'the docker context endpoint (unresolved)'


def daemon():
    findings=[]
    result=docker('info','--format','{{json .}}',check=False)
    if result.returncode:
        # A service does not inherit the caller's shell; name what was tried.
        endpoint=os.environ.get('DOCKER_HOST') or resolved_endpoint()
        findings.append(('blocker',f'Docker daemon unreachable from this process (tried {endpoint}); a system service must reach the socket its docker context resolves to'))
        return findings
    info=json.loads(result.stdout)
    if info.get('OSType')!='linux':findings.append(('blocker','Native Linux containers required'))
    if info.get('Name')!=os.uname().nodename:
        findings.append(('warning','Docker daemon host differs from this host: remote daemons are not supported'))
    return findings


def images():
    findings=[]
    for label,digest,reference in pinned_images():
        if docker('image','inspect',digest,check=False).returncode:
            findings.append(('action','Pinned image '+label+' is not local; install will pull '+reference[:60]))
    return findings


def combined_stage_measured_mib():
    """Measured cost of the already-running source stage, when it has been sampled.

    The combined admission measures the host while the source stage is up, so the
    preflight must add what that stage actually uses or it understates the
    requirement and the start dies halfway with containers already created.
    """
    record=ROOT/'docs'/'evidence'/'source-stage-footprint.json'
    try:
        value=json.loads(record.read_text()).get('total_mib')
    except Exception:
        return None
    return value if isinstance(value,int) and value>0 else None


def headroom_requirement(moved,measured):
    """The headroom a start needs, with its composition stated."""
    needed=PLANNED_MIB+RESERVE_MIB
    composition=f'{PLANNED_MIB} MiB placement + {RESERVE_MIB} MiB reserve'
    if moved and measured:
        needed+=measured
        composition+=f' + {measured} MiB measured for the running source stage'
    return needed,composition


def capacity():
    findings=[]
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    cpus=os.cpu_count() or 0
    moved=(STATE/'cutover-operation.json').exists()
    measured=combined_stage_measured_mib()
    if moved and not measured:
        findings.append(('warning','Combined headroom cannot be stated precisely yet: no source-stage footprint measurement exists, so the requirement is the placement and reserve only'))
    needed,composition=headroom_requirement(moved,measured)
    if memory/1024<needed:
        findings.append(('blocker',f'Host headroom insufficient: {memory//1024} MiB available, plan needs {needed} MiB ({composition})'))
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
        pending=(state/'targets'/prefix/'hba-operation.json').exists()
        if prefix==current_prefix:
            if pending:findings.append(('blocker','Current recovery target '+prefix+' has a pending HBA operation: reconcile it before install'))
            elif not pinned:findings.append(('blocker','Current recovery target '+prefix+' has no generation pin: adopt it with lab/adopt-retained.py target'))
            else:findings.append(('action','Current recovery target '+prefix+' carries a generation pin'))
        elif pending:
            findings.append(('blocker','Historical recovery target '+prefix+' has a pending HBA operation: reconcile it explicitly'))
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
    return versions()+daemon()+images()+capacity()+state()


def report(checks,title='Preflight'):
    blockers=[detail for kind,detail in checks if kind=='blocker']
    for kind,detail in checks:
        print(f'{kind:>8}  {detail}')
    print(f'{title}: {len(blockers)} blocker(s), {len([1 for kind,_ in checks if kind=="action"])} action(s)')
    return not blockers


def operation_lock():
    """One installation mutation at a time; a held lock is a clean refusal."""
    STATE.mkdir(parents=True,exist_ok=True)
    handle=(STATE/'operation.lock').open('a')
    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close();raise SystemExit('Another installation operation holds the operation lock')
    return handle


def bootstrap_payload(path):
    """Refuse anything but the operator's own private 0600 regular file."""
    metadata=os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):raise SystemExit('Bootstrap file must be a regular file')
    if metadata.st_uid!=os.getuid():raise SystemExit('Bootstrap file must be owned by the running user')
    if stat.S_IMODE(metadata.st_mode)!=0o600:raise SystemExit('Bootstrap file must be mode 0600')
    return Path(path).read_text()


def npm_install():
    if not (ROOT/'node_modules').exists():
        run(['bun','install'],cwd=ROOT)


def install(bootstrap_file):
    checks=preflight()
    if not report(checks):raise SystemExit('Preflight failed; nothing was installed')
    lock=operation_lock()
    try:
        PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(PRIVATE,0o700)
        print('step 1/5  state and secret directories prepared')
        for label,digest,reference in pinned_images():
            if docker('image','inspect',digest,check=False).returncode:
                if docker('pull',reference,check=False).returncode:raise SystemExit('Pinned image pull failed for '+label)
        for label,digest,reference in pinned_images():
            record,error=pinned_images_check.inspect_image(reference)
            ok,detail=pinned_images_check.evaluate(digest,record)
            if not ok:raise SystemExit('Pinned image '+label+' did not verify: '+detail)
        print('step 2/5  pinned images present and verified')
        npm_install()
        run(['bun','run','build:ui'],cwd=ROOT)
        problems,_=console_build_check.verify()
        if problems:raise SystemExit('Console build produced an unusable page: '+'; '.join(problems))
        print('step 3/5  console built and verified')
        result=run(['/usr/bin/python3','lab/installation_runtime.py','up'],cwd=ROOT,check=False,env={**os.environ})
        if result.returncode:raise SystemExit('Runtime startup failed: '+result.stderr.strip())
        print('step 4/5  owned runtime started')
        if bootstrap_file is not None:
            payload=bootstrap_payload(bootstrap_file)
            boot=run(['/usr/bin/python3','lab/bootstrap.py','--stdin'],cwd=ROOT,check=False,stdin=payload)
            if boot.returncode:raise SystemExit('Operator bootstrap failed: '+boot.stderr.strip())
            print('step 5/5  operator identity bootstrapped')
        else:
            print('step 5/5  operator bootstrap skipped; run: /usr/bin/python3 lab/bootstrap.py')
        print('Installation ready. Supervise it with deploy/sbarbase.service or the foreground supervisor')
        print('(bun lab/upstream-server.ts starts the console API beside the owned runtime).')
        print('Smoke test (console running): /usr/bin/python3 lab/install_server.py smoke')
    finally:
        lock.close()


def console_status():
    """Live console check: a missing or dead server.json is a failure."""
    server=STATE/'server.json'
    if not server.exists():return False,'server.json missing (console not started)'
    try:pid=json.loads(server.read_text()).get('pid')
    except (OSError,ValueError):return False,'server.json unreadable'
    if not isinstance(pid,int) or pid<=0:return False,'server.json has no usable pid'
    try:os.kill(pid,0)
    except ProcessLookupError:return False,f'console pid {pid} is not running'
    except PermissionError:return True,f'console pid {pid} exists (owned by another user)'
    return True,f'console pid {pid} running'


def smoke():
    import urllib.request
    checks=[]
    routes={}
    if (STATE/'management.json').exists():
        routes['management-auth']=json.loads((STATE/'management.json').read_text()).get('auth')
    else:
        routes['management-auth']=None
    if (STATE/'endpoints.json').exists():
        for environment,endpoints in json.loads((STATE/'endpoints.json').read_text()).items():
            routes[environment+':auth']=endpoints.get('auth')
            routes[environment+':rest']=endpoints.get('rest')
    for name,base in routes.items():
        if not base:checks.append((name,'missing endpoint'));continue
        url=base+('/health' if name.endswith('auth') else '/')
        try:
            with urllib.request.urlopen(url,timeout=5) as response:code=response.status
        except Exception as error:code=str(error)
        checks.append((name,code))
    alive,detail=console_status()
    for name,value in checks:print(f'{name:>22}  {value}')
    print(f'{">":>22}  console: {detail}')
    endpoints_ok=all(value==200 for _,value in checks)
    return endpoints_ok and alive


SERVICE_UNIT=ROOT/'deploy'/'sbarbase.service'
SERVICE_UNIT_PATH=Path('/etc/systemd/system/sbarbase.service')
UNIT_ANCHORS=('WorkingDirectory=/opt/sbarbase','User=sbarbase','Group=sbarbase',
              'Environment=HOME=/home/sbarbase','ExecStart=/usr/bin/python3 /opt/sbarbase/lab/dev.py',
              'ExecStartPre=/usr/bin/python3 /opt/sbarbase/lab/install_server.py check',
              'ReadWritePaths=/opt/sbarbase /home/sbarbase/.secrets','Documentation=file:/opt/sbarbase/docs/SERVER-DEPLOYMENT.md')


def validate_service_identity(user,home,bun_dir):
    """Reject anything that could inject a directive into the unit or a path we cannot reason about."""
    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',str(user)):
        raise SystemExit('--service-user must be a plain account name, not '+repr(user))
    for label,value in (('--home',home),('--bun-dir',bun_dir)):
        text=str(value)
        if not text.startswith('/'):
            raise SystemExit(label+' must be an absolute path, not '+repr(text))
        if any(character in text for character in ('\n','\r','\t',' ','%','\\','"',"'")):
            raise SystemExit(label+' must not contain whitespace, quotes, percent or backslash: '+repr(text))
    return True


def rendered_unit(root,home,user,bun_dir,text=None):
    """Rewrite the shipped unit for an installation. Refuses if its shape changed.

    The shipped file carries a server layout such as /opt/sbarbase. A deployment
    elsewhere must not be hand-edited, so the substitution is explicit and the
    anchors are checked first: a unit whose directives moved is not rewritten
    blindly.
    """
    source=text if text is not None else SERVICE_UNIT.read_text()
    if not bun_dir:raise SystemExit('Bun directory is required: the service needs bun on PATH')
    validate_service_identity(user,home,bun_dir)
    for anchor in UNIT_ANCHORS:
        if anchor not in source:raise SystemExit('Shipped unit no longer contains '+repr(anchor)+'; refusing to render it blindly')
    rendered=(source
        .replace('Documentation=file:/opt/sbarbase/','Documentation=file:'+str(root)+'/')
        .replace('WorkingDirectory=/opt/sbarbase','WorkingDirectory='+str(root))
        .replace('User=sbarbase','User='+user)
        .replace('Group=sbarbase','Group='+user)
        .replace('Environment=HOME=/home/sbarbase','Environment=HOME='+str(home))
        .replace(':/home/sbarbase/.bun/bin',':'+str(bun_dir))
        .replace('ExecStartPre=/usr/bin/python3 /opt/sbarbase/','ExecStartPre=/usr/bin/python3 '+str(root)+'/')
        .replace('ExecStart=/usr/bin/python3 /opt/sbarbase/','ExecStart=/usr/bin/python3 '+str(root)+'/')
        .replace('ReadWritePaths=/opt/sbarbase /home/sbarbase/.secrets','ReadWritePaths='+str(root)+' '+str(Path(home)/'.secrets')))
    if '/opt/sbarbase' in rendered:
        raise SystemExit('Rendered unit still refers to the shipped default path; refusing it')
    return rendered


def unit_commands(rendered_path):
    """The exact commands an operator runs to install and start the unit."""
    return ['sudo install -m 0644 '+str(rendered_path)+' '+str(SERVICE_UNIT_PATH),
            'sudo systemctl daemon-reload',
            'sudo systemctl enable --now sbarbase.service',
            'systemctl is-active sbarbase.service']


def supervise(apply=False,service_user='sbarbase',home=None,bun_dir=None,evidence_path=None):
    """Render, verify and optionally install the supervisor unit."""
    home=home or Path('/home')/service_user
    if bun_dir is None:
        found=shutil.which('bun')
        if not found:
            raise SystemExit('bun is not on PATH: pass --bun-dir with the directory holding it '
                             '(a service does not inherit your shell PATH)')
        bun_dir=str(Path(found).parent)
    rendered=rendered_unit(ROOT,home,service_user,bun_dir)
    temporary=ROOT/'.lab'/'rendered-sbarbase.service'
    temporary.parent.mkdir(parents=True,exist_ok=True)
    temporary.write_text(rendered)
    verify=run(['systemd-analyze','verify',str(temporary)],check=False)
    verified=verify.returncode==0
    root_user=os.geteuid()==0
    applied=False
    if apply:
        if not root_user:raise SystemExit('Installing the unit requires root (run with sudo)')
        if not verified:raise SystemExit('Rendered unit did not verify; refusing to install it')
        for command in (['install','-m','0644',str(temporary),str(SERVICE_UNIT_PATH)],
                        ['systemctl','daemon-reload'],
                        ['systemctl','enable','--now','sbarbase.service']):
            if run(command,check=False).returncode:
                raise SystemExit('Unit installation step failed: '+' '.join(command))
        applied=run(['systemctl','is-active','sbarbase.service'],check=False).stdout.strip()=='active'
        if not applied:
            raise SystemExit('The unit was installed but did not become active; inspect systemctl status sbarbase.service')
    evidence={'scope':('Supervisor unit: the shipped unit is rendered for this installation (paths, service user and Bun '
                       'directory), verified with systemd-analyze, and the exact install commands are recorded. With '
                       '--apply and root the unit is installed, reloaded, enabled and started, and the run fails unless it '
                       'becomes active. Not a substitute for the server acceptance run, which requires the unit to be '
                       'installed.'),
              'installation_root':str(ROOT),'service_user':service_user,'home':str(home),'bun_dir':bun_dir,
              'rendered':rendered,'rendered_path':str(temporary),
              'verify':'passed' if verified else ('failed: '+(verify.stderr or verify.stdout).strip()),
              'running_as_root':root_user,'applied':applied,'install_commands':unit_commands(temporary),
              'run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'passed':bool(verified) and (not apply or applied)}
    out=Path(evidence_path) if evidence_path else ROOT/'docs'/'evidence'/'supervisor-unit.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(evidence,indent=1)+'\n')
    print('rendered unit verified' if verified else 'rendered unit FAILED verification')
    print('evidence:',out)
    if apply:print('unit installed and started' if applied else 'unit installed but not active')
    else:print('dry run: install it with  sudo /usr/bin/python3 lab/install_server.py supervise --apply')
    return evidence['passed']


def main():
    parser=argparse.ArgumentParser(description='sbarbase server preflight and installation')
    parser.add_argument('command',choices=('check','plan','install','smoke','supervise'))
    parser.add_argument('--bootstrap-file',help='private 0600 JSON with email, password and organization')
    parser.add_argument('--apply',action='store_true',help='supervise: install, enable and start the unit (requires root)')
    parser.add_argument('--service-user',default='sbarbase',help='supervise: the account the service runs as')
    parser.add_argument('--home',help='supervise: the service account home directory')
    parser.add_argument('--bun-dir',help='supervise: directory holding the bun binary')
    args=parser.parse_args()
    if args.command=='supervise':
        raise SystemExit(0 if supervise(args.apply,args.service_user,args.home,args.bun_dir) else 1)
    if args.command=='check':
        raise SystemExit(0 if report(preflight()) else 1)
    if args.command=='plan':
        print('1. preflight (docker, bun, /usr/bin/python3 3.14+, pinned images, headroom, disk, state)')
        print('2. take the installation operation lock')
        print('3. create the private secret directory (0700)')
        print('4. pull each pinned image by its repository@digest reference when it is not local')
        print('5. bun install when node_modules is absent')
        print('6. bun run build:ui')
        print('7. /usr/bin/python3 lab/installation_runtime.py up')
        print('8. /usr/bin/python3 lab/bootstrap.py --stdin  (from the private 0600 JSON file)')
        print('9. supervise: /usr/bin/python3 lab/install_server.py supervise --apply  (root: renders, verifies, enables, starts)')
        return
    if args.command=='install':
        install(args.bootstrap_file);return
    raise SystemExit(0 if smoke() else 1)


if __name__=='__main__':main()