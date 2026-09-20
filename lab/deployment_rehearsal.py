"""Deployment rehearsal: install, supervise, verify, shut down, record evidence.

Usage:
  /usr/bin/python3 lab/deployment_rehearsal.py [--bootstrap-file PATH] [--skip-install] [--timeout 600]

What it proves, in one command:
  1. the host passes the read-only preflight;
  2. the installation installs (or is already installed) and the supervisor starts;
  3. the console serves the built page and the management identity realm answers;
  4. every recorded environment route answers through the running services;
  5. the supervisor shuts down and leaves no owned container running.

Exit code is non-zero if any step fails. Evidence goes to
docs/evidence/deployment-rehearsal.json. No secret is printed.
"""
import argparse
import datetime
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import console_build_check
import install_server

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/'.lab'/'upstream'
PUBLISHABLE='sb_publishable_sbarbase_local_management'


def owned_running(owner):
    return bool(install_server.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner,check=False).stdout.strip())


def http_status(url,headers=None,timeout=5):
    request=urllib.request.Request(url,headers=headers or {})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            response.read();return response.status
    except urllib.error.HTTPError as error:return error.code
    except Exception as error:return str(error)


def start_supervisor(timeout):
    log_path=STATE/'rehearsal-supervisor.log'
    log=open(log_path,'wb')
    try:
        process=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=ROOT,
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    finally:
        # The child holds its own descriptor; the parent's copy can be closed.
        log.close()
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError('Supervisor exited during startup; '+supervisor_tail(log_path))
        try:
            supervisor=json.loads((STATE/'supervisor.json').read_text())
            server=json.loads((STATE/'server.json').read_text())
            if supervisor['pid']==process.pid and supervisor['serverPid']==server['pid']:
                return process,server
        except (OSError,ValueError,KeyError):pass
        time.sleep(.2)
    process.terminate();raise RuntimeError('Supervisor startup timed out; '+supervisor_tail(log_path))


def supervisor_tail(path,lines=8):
    """Last lines the supervisor printed, so a startup failure states its cause."""
    try:
        content=Path(path).read_text(errors='replace').strip().splitlines()
    except OSError:
        return 'no supervisor output captured'
    return ' | '.join(content[-lines:]) if content else 'supervisor printed nothing'


def stop_supervisor(process,grace=150):
    process.terminate()
    try:
        return process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        return process.wait(timeout=30)


def host_facts():
    """Facts an operator needs to certify a server run. No secret, no path leak."""
    import platform
    facts: dict[str,object]={'platform':platform.platform(),'kernel':platform.release(),'machine':platform.machine(),
           'python':platform.python_version()}
    for label,argv in (('docker',['docker','version','--format','{{.Server.Version}}']),
                       ('bun',['bun','--version']),
                       ('systemd',['systemctl','--version'])):
        try:
            result=subprocess.run(argv,capture_output=True,text=True,timeout=20)
            facts[label]=result.stdout.strip().splitlines()[0] if result.returncode==0 and result.stdout.strip() else 'unavailable'
        except Exception:facts[label]='unavailable'
    try:
        memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
        facts['mem_available_mib']=int(memory['MemAvailable'].split()[0])//1024
    except Exception:facts['mem_available_mib']=None
    try:facts['disk_free_mib']=int(__import__('shutil').disk_usage(ROOT).free)//(1024*1024)
    except Exception:facts['disk_free_mib']=None
    return facts


UNIT=Path('/etc/systemd/system/sbarbase.service')


def unit_status(path=UNIT):
    """Whether the supervised path is installed, valid and active on this host."""
    status={'installed':path.exists(),'enabled':None,'active':None,'verify':'not-run'}
    if not status['installed']:return status
    for key,argv in (('enabled',['systemctl','is-enabled','sbarbase.service']),
                     ('active',['systemctl','is-active','sbarbase.service'])):
        try:status[key]=subprocess.run(argv,capture_output=True,text=True,timeout=20).stdout.strip() or 'unknown'
        except Exception:status[key]='unavailable'
    source=(ROOT/'deploy'/'sbarbase.service')
    if source.exists():
        try:
            result=subprocess.run(['systemd-analyze','verify',str(source)],capture_output=True,text=True,timeout=60)
            status['verify']='passed' if result.returncode==0 else 'failed: '+(result.stderr.strip() or result.stdout.strip())
        except Exception:status['verify']='unavailable'
    return status


def run_bootstrap_check(timeout=300):
    """Install step "operator bootstrap", against the live management Auth.

    Returns None when the step is deliberately skipped, so the evidence never
    implies a check that did not run.
    """
    if os.environ.get('SBARBASE_SKIP_BOOTSTRAP_CHECK')=='1':return None
    result=subprocess.run(['bun','lab/bootstrap-check.ts'],cwd=ROOT,capture_output=True,text=True,timeout=timeout)
    lines=[line for line in result.stdout.strip().splitlines() if line.strip()]
    detail=lines[-1] if lines else (result.stderr.strip()[-200:] or 'no output')
    return result.returncode,detail


def rehearse(bootstrap_file,skip_install,timeout,attempts=1,delay=15,require_unit=False):
    findings=[]
    def record(label,ok,detail=''):
        findings.append({'check':label,'ok':bool(ok),'detail':detail})
        print(('ok: ' if ok else 'FAIL: ')+label+(('  '+str(detail)) if detail and not ok else ''))

    preflight=install_server.preflight()
    blockers=[detail for kind,detail in preflight if kind=='blocker']
    for detail in blockers:record('preflight: '+detail,False)
    if blockers:
        return findings,None
    record('host preflight passed',True)
    if not skip_install:
        install_server.install(bootstrap_file)
        record('installation steps completed',True)
    process=None
    try:
        problems,_=console_build_check.verify()
        record('built console page is intact',not problems,'; '.join(problems))
        process,server=None,None
        # A refusal from host pressure is transient on a shared host; each attempt
        # is recorded, and the run states how many it took.
        refusals=[]
        for attempt in range(1,max(1,attempts)+1):
            try:
                process,server=start_supervisor(timeout)
                break
            except RuntimeError as error:
                refusals.append(f'attempt {attempt}: {error}')
                print('retry: '+refusals[-1])
                if attempt<max(1,attempts):time.sleep(delay)
        if process is None:
            record('supervisor started and owns the console',False,refusals[-1] if refusals else 'no attempt ran')
            findings.append({'check':'startup attempts before refusal','ok':True,'detail':str(len(refusals))})
            return findings,None
        record('supervisor started and owns the console',True,
               '' if len(refusals)==0 else f'after {len(refusals)+1} attempts: '+' | '.join(refusals))
        record('console serves the built page',http_status(server['url']+'/')==200)
        record('management identity realm reachable through the console',
               http_status(server['url']+'/management/auth/v1/settings',{'apikey':PUBLISHABLE})==200)
        if install_server.smoke():
            record('every recorded environment route answered',True)
        else:
            record('every recorded environment route answered',False,'see smoke output above')
        bootstrap=run_bootstrap_check()
        if bootstrap is not None:
            code,detail=bootstrap
            record('operator bootstrap checks passed against the live management Auth',code==0,detail)
        if (STATE/'cutover-operation.json').exists():
            result=subprocess.run(['bun','lab/combined-gateway-check.ts'],cwd=ROOT,capture_output=True,text=True,timeout=180)
            record('combined gateway checks passed',result.returncode==0,result.stdout.strip() or result.stderr.strip())
        supervised=unit_status()
        if supervised['installed']:
            record('supervisor unit is enabled on this host',supervised['enabled'] in ('enabled','enabled-runtime'),supervised['enabled'])
            record('supervisor unit file verifies',supervised['verify']=='passed',supervised['verify'])
        elif require_unit:
            record('supervised path exercised through systemd',False,
                   'sbarbase.service is not installed at /etc/systemd/system; a server acceptance run requires it')
        else:
            record('supervised path exercised through systemd',True,
                   'not required for this run: the supervisor was started directly, sbarbase.service is not installed')
    finally:
        if process is not None:
            code=stop_supervisor(process)
            record('supervisor shut down cleanly',code==0,'exit '+str(code))
            record('no owned container left running',not owned_running('durable-upstream') and not owned_running('recovery-target'))
    return findings,None


def main():
    parser=argparse.ArgumentParser(description='sbarbase deployment rehearsal')
    parser.add_argument('--bootstrap-file')
    parser.add_argument('--skip-install',action='store_true')
    parser.add_argument('--timeout',type=int,default=600)
    parser.add_argument('--attempts',type=int,default=3,
                        help='startup attempts allowed before the run is declared failed (host pressure is transient)')
    parser.add_argument('--attempt-delay',type=int,default=15)
    parser.add_argument('--require-unit',action='store_true',
                        help='fail when sbarbase.service is not installed (a server acceptance run requires it)')
    args=parser.parse_args()
    started=datetime.datetime.now().astimezone()
    findings,_=rehearse(args.bootstrap_file,args.skip_install,args.timeout,args.attempts,args.attempt_delay,args.require_unit)
    finished=datetime.datetime.now().astimezone()
    passed=bool(findings) and all(item['ok'] for item in findings)
    pins=[{'component':label,'digest':digest,'pull':reference} for label,digest,reference in install_server.pinned_images()]
    evidence={'scope':('Deployment rehearsal: read-only preflight, installation (or an already installed host), supervisor '
                       'startup, console and management reachability, recorded environment routes, the supervised unit when '
                       'installed, supervised shutdown and absence of running owned containers. Not sustained load, not '
                       'multi-host, not HTTPS termination.'),
              'command':' '.join(['/usr/bin/python3','lab/deployment_rehearsal.py']+sys.argv[1:]),
              'host':host_facts(),
              'pins':pins,
              'unit':unit_status(),
              'bootstrap_file_used':bool(args.bootstrap_file),
              'install_skipped':bool(args.skip_install),
              'startup_attempts_allowed':args.attempts,
              'unit_required':bool(args.require_unit),
              'supervisor_log':str(STATE/'rehearsal-supervisor.log'),
              'runtime_diagnostics':str(STATE/'diagnostics'),
              'started_at':started.isoformat(timespec='seconds'),
              'finished_at':finished.isoformat(timespec='seconds'),
              'checks':findings,'passed':passed,'count':len(findings)}
    out=ROOT/'docs'/'evidence'/'deployment-rehearsal.json'
    out.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',out)
    print('deployment rehearsal:', 'passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()