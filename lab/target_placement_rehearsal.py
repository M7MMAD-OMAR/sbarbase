"""Repeatable rehearsal of the adopted recovery-target placement.

Starts the retained target through its normal lifecycle (which health-checks
Auth, REST and Storage and re-stages routing), probes the routes independently,
then stops it and verifies that everything is stopped again and routing is paused.
The source placement is never started. Evidence: docs/evidence/target-placement-rehearsal.json.

Usage: /usr/bin/python3 lab/target_placement_rehearsal.py [--skip-start]
"""
import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
import run as lab
import hba_generation

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/'.lab'/'upstream'


def docker(*args):
    return subprocess.run(['docker',*args],capture_output=True,text=True,check=False,timeout=120)


def descriptor():
    return json.loads((STATE/'recovery-target.json').read_text())


def running(prefix):
    return [kind for kind in ('db','auth','rest','storage')
            if json.loads(docker('inspect',prefix+'-'+kind).stdout)[0]['State']['Running']]


def address(prefix,network,kind):
    info=json.loads(docker('inspect',prefix+'-'+kind).stdout)[0]
    return info['NetworkSettings']['Networks'][network]['IPAddress']


def probe(url,timeout=5):
    try:
        with urllib.request.urlopen(url,timeout=timeout) as response:
            response.read();return response.status
    except urllib.error.HTTPError as error:return error.code
    except Exception as error:return str(error)


def routing(environment):
    result=subprocess.run(['bun','lab/routing-operator.ts'],input=json.dumps({'action':'read','runtimes':[environment]}),
                          cwd=ROOT,text=True,capture_output=True,timeout=60)
    if result.returncode:raise RuntimeError('Routing read failed')
    return json.loads(result.stdout)[environment]


def lifecycle(command):
    result=subprocess.run(['/usr/bin/python3','lab/target_runtime.py',command],cwd=ROOT,text=True,capture_output=True,timeout=300)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--skip-start',action='store_true');args=parser.parse_args()
    checks=[]
    def check(label,condition,detail=''):
        checks.append({'check':label,'ok':bool(condition),'detail':str(detail)})
        print(('ok: ' if condition else 'FAIL: ')+label+(('  '+str(detail)) if detail and not condition else ''))
    info=descriptor();prefix=info['prefix'];environment=info['environment'];network=info['network']
    check('source placement stays stopped',not docker('ps','-q','--filter','label=io.sbarbase.owner=durable-upstream').stdout.strip())
    already=running(prefix)
    if already and not args.skip_start:raise SystemExit('Target already running ('+', '.join(already)+'); stop it first')
    try:
        if not args.skip_start:
            started=lifecycle('up')
            check('target started through its own lifecycle',started.returncode==0,started.stderr.strip() or started.stdout.strip())
            state=routing(environment)
            check('target lifecycle reports running and a new routing revision','"running": true' in started.stdout)
        check('all four target containers are running',len(running(prefix))==4,', '.join(running(prefix)))
        check('target Auth answers',probe('http://'+address(prefix,network,'auth')+':9999/health')==200)
        check('target REST answers',probe('http://'+address(prefix,network,'rest')+':3000/')==200)
        state=routing(environment)
        check('routing is resumed with a persisted placement',state['maintenance'] is False and bool(state['placement']))
        pin=hba_generation.load(STATE/'targets'/prefix)
        check('target carries the adopted generation pin',pin['target']['container_id']==json.loads(docker('inspect',prefix+'-db').stdout)[0]['Id'])
    finally:
        stopped=lifecycle('stop')
        check('target stopped through its own lifecycle',stopped.returncode==0,stopped.stderr.strip() or stopped.stdout.strip())
        check('no target container left running',not running(prefix),', '.join(running(prefix)))
        state=routing(environment)
        check('routing returned to paused maintenance',state['maintenance'] is True)
        check('source placement still stopped',not docker('ps','-q','--filter','label=io.sbarbase.owner=durable-upstream').stdout.strip())
    passed=all(item['ok'] for item in checks)
    evidence={'scope':('Rehearsal of the retained recovery-target placement after HBA adoption: target lifecycle start with its '
                       'own readiness probes, independent Auth and REST probes, resumed routing with a persisted placement, '
                       'lifecycle stop, paused maintenance afterwards and an untouched stopped source. Not sustained load, not '
                       'HTTPS, not multi-host.'),
              'environment':environment,'checks':checks,'count':len(checks),'passed':passed}
    out=ROOT/'docs'/'evidence'/'target-placement-rehearsal.json'
    out.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',out)
    print('target placement rehearsal:','passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()