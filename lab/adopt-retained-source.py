"""Adopt a retained legacy source into HBA authority, with sanitized evidence.

Operational assumptions, stated explicitly:
- No legacy host client or queued Docker request may hold HBA intent. Check the
  locks are free, every source container is stopped, and no worker-effect
  receipt or HBA journal exists before running this.
- Only the captured database container is started, and it is stopped again by
  the same operation. Application services and recovery targets are untouched.

This is a real state change on the target source: a private adoption intent,
immutable checkpoints, a generation pin and a freshly revised but otherwise
preserved pg_hba.conf. Byte-level verification of the preserved rules is done
by lab/verify-retained-adoption.py from durable evidence. No secrets or private
container paths are written to evidence.
"""
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
import hba_adoption as adoption
import hba_generation
import hba_target

LAB=Path(__file__).resolve().parent
ROOT=LAB.parent
STATE=ROOT/'.lab'/'upstream'
NAME='sbarbase-durable-db'
OWNER='durable-upstream'


def docker(*args,data=None,check=True):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=check,timeout=180)


def prepared_state(state,expect_absent):
    """Refuse when another run already holds or has consumed this operation."""
    for path in (expect_absent,):
        if (state/path).exists():raise SystemExit('Refusing: '+str(path)+' already exists')


def main():
    image=json.loads((LAB/'distro-image.lock.json').read_text())['id']
    observed=json.loads(docker('inspect',NAME).stdout)[0]
    cid=observed['Id']
    print('source',NAME,'running=',observed['State']['Running'],'cid=',cid[:12],'owner=',observed['Config']['Labels'].get('io.sbarbase.owner'))
    if observed['State']['Running']:raise SystemExit('Refusing: the retained source is not stopped')
    prepared_state(STATE,adoption.NAME)
    target=hba_target.Target(cid,NAME,OWNER,image)
    intent=adoption.publish_intent(docker,STATE,name=NAME,owner=OWNER,image=image)
    print('intent published: adoption',intent['adoption'],'generation',intent['generation'],'volume',intent['volume'])
    completed=adoption.execute(docker,STATE,target=target)
    pin=hba_generation.load(STATE)
    info=json.loads(docker('inspect',NAME).stdout)[0]
    checks={
        'completed checkpoint reached':completed['phase']=='completed',
        'source is stopped again by exact identity':info['Id']==cid and info['State']['Running'] is False,
        'generation pin equals the intent generation':pin['generation']==intent['generation'] and pin['target']==asdict(target),
        'every durable checkpoint exists':all((STATE/adoption.CHECKPOINTS/(phase+'.json')).exists() for phase in adoption.PHASES),
        'adoption intent consumed':not (STATE/adoption.NAME).exists(),
    }
    for label,ok in checks.items():
        print(('ok: ' if ok else 'FAIL: ')+label)
    evidence={'scope':('Retained legacy source adoption on the actual stopped source database container, with only that container '
                        'started and stopped again. Quiescence of legacy host clients is an explicit operational assumption. '
                        'No application service or recovery target was started. Rule preservation is proven byte for byte by '
                        'lab/verify-retained-adoption.py from the journal expected digest.'),
              'source':{'name':NAME,'owner':OWNER,'image':image},
              'adoption':intent['adoption'],'generation':intent['generation'],'volume':intent['volume'],
              'checkpoints':list(adoption.PHASES),'checks':checks,'passed':all(checks.values())}
    out=ROOT/'docs'/'evidence'/'retained-source-adoption.json'
    existing=json.loads(out.read_text()) if out.exists() else {}
    merged={**existing,**evidence,'checks':{**existing.get('checks',{}),**checks},
            'passed':all({**existing.get('checks',{}),**checks}.values())}
    out.write_text(json.dumps(merged,indent=1)+'\n')
    print('evidence:',out)
    if not all(checks.values()):sys.exit(1)


if __name__=='__main__':main()