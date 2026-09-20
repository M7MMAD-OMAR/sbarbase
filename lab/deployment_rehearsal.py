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
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
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
    process=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=ROOT,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Supervisor exited during startup')
        try:
            supervisor=json.loads((STATE/'supervisor.json').read_text())
            server=json.loads((STATE/'server.json').read_text())
            if supervisor['pid']==process.pid and supervisor['serverPid']==server['pid']:
                return process,server
        except (OSError,ValueError,KeyError):pass
        time.sleep(.2)
    process.terminate();raise RuntimeError('Supervisor startup timed out')


def stop_supervisor(process,grace=150):
    process.terminate()
    try:
        return process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        return process.wait(timeout=30)


def rehearse(bootstrap_file,skip_install,timeout):
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
        process,server=start_supervisor(timeout)
        record('supervisor started and owns the console',True)
        record('console serves the built page',http_status(server['url']+'/')==200)
        record('management identity realm reachable through the console',
               http_status(server['url']+'/management/auth/v1/settings',{'apikey':PUBLISHABLE})==200)
        if install_server.smoke():
            record('every recorded environment route answered',True)
        else:
            record('every recorded environment route answered',False,'see smoke output above')
        if (STATE/'cutover-operation.json').exists():
            result=subprocess.run(['bun','lab/combined-gateway-check.ts'],cwd=ROOT,capture_output=True,text=True,timeout=180)
            record('combined gateway checks passed',result.returncode==0,result.stdout.strip() or result.stderr.strip())
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
    args=parser.parse_args()
    findings,_=rehearse(args.bootstrap_file,args.skip_install,args.timeout)
    passed=bool(findings) and all(item['ok'] for item in findings)
    evidence={'scope':('Deployment rehearsal: read-only preflight, installation (or an already installed host), supervisor '
                       'startup, console and management reachability, recorded environment routes, supervised shutdown and '
                       'absence of running owned containers. Not sustained load, not multi-host, not HTTPS termination.'),
              'checks':findings,'passed':passed,'count':len(findings)}
    out=ROOT/'docs'/'evidence'/'deployment-rehearsal.json'
    out.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',out)
    print('deployment rehearsal:', 'passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()