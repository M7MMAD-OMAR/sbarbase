"""Prove the supervised path under systemd, without installing a system unit.

Usage: /usr/bin/python3 lab/supervised_run_check.py [--timeout 600]

Writes a temporary *user* unit that mirrors the shipped system unit's directives
(same ExecStartPre gate, same ExecStart, same PATH), starts it with
`systemctl --user`, proves the console and the management realm answer, stops it
with `systemctl`, and proves the shutdown was clean and left no owned container
running. The unit file is always removed.

Honest scope: this proves systemd supervision mechanics and the preflight gate as
systemd runs them. It is not the shipped unit installed at
/etc/systemd/system/sbarbase.service, which a server acceptance run covers.
Evidence goes to docs/evidence/supervised-run.json. No secret is printed.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
UNIT_NAME='sbar-supervised-check.service'
USER_UNIT_DIR=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'systemd'/'user'
UNIT_PATH=USER_UNIT_DIR/UNIT_NAME
EVIDENCE=ROOT/'docs'/'evidence'/'supervised-run.json'
STATE=ROOT/'.lab'/'upstream'
PUBLISHABLE='sb_publishable_sbarbase_local_management'


def docker_environment():
    """Docker settings a service must inherit on a host whose CLI config differs.

    The shipped unit relies on the daemon socket the host's Docker context resolves
    to. Where a shell reaches the daemon through DOCKER_HOST (a non-default context
    such as Docker Desktop), a unit without it cannot reach the daemon at all, so
    the check forwards it and says so in the evidence.
    """
    forwarded={}
    for name in ('DOCKER_HOST','DOCKER_CONTEXT'):
        value=os.environ.get(name)
        if value:forwarded[name]=value
    return forwarded


def unit_text():
    docker_lines=''.join(f'Environment={name}={value}\n' for name,value in docker_environment().items())
    return f"""[Unit]
Description=sbarbase supervised-path check (temporary)

[Service]
Type=simple
WorkingDirectory={ROOT}
Environment=HOME={Path.home()}
Environment=PATH={os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')}
{docker_lines}ExecStartPre=/usr/bin/python3 {ROOT}/lab/install_server.py check
ExecStart=/usr/bin/python3 {ROOT}/lab/dev.py
Restart=no
TimeoutStopSec=220

[Install]
WantedBy=default.target
"""


def systemctl(*args,check=True,timeout=240):
    return subprocess.run(['systemctl','--user',*args],capture_output=True,text=True,timeout=timeout,check=check)


def journal(lines=60):
    try:
        return subprocess.run(['journalctl','--user','-u',UNIT_NAME,'--no-pager','-n',str(lines)],
                              capture_output=True,text=True,timeout=60).stdout
    except Exception:
        return ''


def http_status(url,headers=None,timeout=5):
    request=urllib.request.Request(url,headers=headers or {})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            response.read();return response.status
    except urllib.error.HTTPError as error:return error.code
    except Exception as error:return str(error)


def owned_running(owner):
    result=subprocess.run(['docker','ps','-q','--filter','label=io.sbarbase.owner='+owner],
                          capture_output=True,text=True,timeout=60)
    return bool(result.stdout.strip())


def wait_for_console(timeout,started_after):
    """The supervisor the unit just started, not state left by an earlier run."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            supervisor_path=STATE/'supervisor.json';server_path=STATE/'server.json'
            if supervisor_path.stat().st_mtime>started_after and server_path.stat().st_mtime>started_after:
                supervisor=json.loads(supervisor_path.read_text())
                server=json.loads(server_path.read_text())
                if supervisor.get('pid') and server.get('url') and alive(supervisor['pid']) and alive(server['pid']):
                    return supervisor,server
        except (OSError,ValueError,KeyError):pass
        time.sleep(.5)
    return None,None


def alive(pid):
    """A pid is only evidence if the process is still there."""
    try:
        os.kill(int(pid),0);return True
    except (ProcessLookupError,ValueError,TypeError):return False
    except PermissionError:return True


def main():
    parser=argparse.ArgumentParser(description='supervised-path check')
    parser.add_argument('--timeout',type=int,default=420)
    args=parser.parse_args()
    checks=[]
    def record(label,ok,detail=''):
        checks.append({'check':label,'ok':bool(ok),'detail':detail})
        print(('ok: ' if ok else 'FAIL: ')+label+(('  '+str(detail)) if detail and not ok else ''))

    if systemctl('show-environment',check=False).returncode:
        raise SystemExit('No systemd user session available; supervised check skipped')

    USER_UNIT_DIR.mkdir(parents=True,exist_ok=True)
    UNIT_PATH.write_text(unit_text())
    started=False
    try:
        systemctl('daemon-reload')
        loaded=systemctl('show','-p','LoadState','--value',UNIT_NAME,check=False).stdout.strip()
        record('temporary unit loaded by systemd',loaded=='loaded',loaded)

        start=systemctl('start',UNIT_NAME,check=False)
        started=start.returncode==0
        started_after=time.time()
        record('systemctl start accepted the unit',started,(start.stderr or start.stdout).strip()[:200])

        supervisor,server=wait_for_console(args.timeout,started_after)
        if supervisor and server:
            record('systemd started the supervisor and the console',True)
            record('console serves the built page',http_status(server['url']+'/')==200)
            record('management identity realm reachable through the console',
                   http_status(server['url']+'/management/auth/v1/settings',{'apikey':PUBLISHABLE})==200)
            record('owned containers were started by the unit',owned_running('durable-upstream'))
        else:
            record('systemd started the supervisor and the console',False,'no server.json and supervisor.json within the timeout')

        gate=journal()
        # The gate must have admitted the host, not merely run: the summary line is
        # present for a refusal too.
        record('the preflight gate admitted the host under systemd','Preflight: 0 blocker(s)' in gate,
               gate.strip().splitlines()[-1][:160] if gate.strip() else 'no journal output')
    finally:
        if started:
            stop=systemctl('stop',UNIT_NAME,check=False,timeout=300)
            record('systemctl stop returned cleanly',stop.returncode==0,(stop.stderr or '').strip()[:200])
        UNIT_PATH.unlink(missing_ok=True)
        systemctl('daemon-reload',check=False)
        record('temporary unit file removed',not UNIT_PATH.exists())
        record('no owned container left running',not owned_running('durable-upstream') and not owned_running('recovery-target'))

    passed=bool(checks) and all(item['ok'] for item in checks)
    evidence={'scope':('Supervised-path check: a temporary systemd user unit mirroring the shipped unit directives starts '
                       'the supervisor, the preflight gate runs as systemd runs it, the console and management realm answer, '
                       'systemctl stop is clean and no owned container is left running. Not the shipped system unit '
                       'installed at /etc/systemd/system, not HTTPS, not an empty-host install.'),
              'unit':'user unit '+UNIT_NAME+' (temporary, mirrors deploy/sbarbase.service)',
              'docker_forwarded':docker_environment(),
              'docker_note':('Forwarded because this host reaches the daemon through DOCKER_HOST while its docker context '
                             'points elsewhere; a server whose context resolves to the native socket needs neither.'),
              'run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'checks':checks,'count':len(checks),'passed':passed}
    EVIDENCE.parent.mkdir(parents=True,exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',EVIDENCE)
    print('supervised run check:','passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()