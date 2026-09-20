"""Adopt a retained database container into owned HBA authority, with evidence.

Usage: /usr/bin/python3 lab/adopt-retained.py [source|target]

- source: the retained installation database (installation state root).
- target: the retained recovery-target database (per-target authority state
  below <installation state>/targets/<prefix>).

Operational assumptions, stated explicitly:
- No legacy host client or queued Docker request may hold HBA intent. Check that
  every container of that placement is stopped, the relevant locks are free and
  no worker-effect receipt or HBA journal exists before running this.
- Only the captured database container is started, and it is stopped again by
  the same operation. Application services are untouched.

This is a real state change on the target database: a private adoption intent,
immutable checkpoints, a generation pin and a freshly revised but otherwise
preserved pg_hba.conf. Byte-level verification of the preserved rules is done by
lab/verify-retained.py from durable evidence. No secrets or private container
paths are written to evidence.
"""
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
import hba_adoption as adoption
import hba_generation
import hba_runtime
import hba_target

LAB=Path(__file__).resolve().parent
ROOT=LAB.parent
STATE=ROOT/'.lab'/'upstream'


def docker(*args,data=None,check=True):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=check,timeout=180)


def placement(role):
    """Trusted placement description: names, owners and pinned images."""
    image=json.loads((LAB/'distro-image.lock.json').read_text())['id']
    if role=='source':
        return {'role':'source','state':STATE,'name':'sbarbase-durable-db','owner':'durable-upstream','image':image}
    descriptor=json.loads((STATE/'recovery-target.json').read_text())
    prefix=descriptor['prefix']
    state=hba_runtime.prepare_target_state(STATE,prefix)
    return {'role':'target','state':state,'prefix':prefix,'name':prefix+'-db','owner':'recovery-target','image':image}


def pending(state):
    """Locks and journals that must be absent before any adoption starts."""
    found=[]
    for name in ('hba-operation.json','worker-effect.json','hba-adoption.json'):
        if (state/name).exists():found.append(name)
    return found


def main(role):
    placement_info=placement(role)
    state=placement_info['state']
    name=placement_info['name'];owner=placement_info['owner'];image=placement_info['image']
    info=json.loads(docker('inspect',name).stdout)[0]
    cid=info['Id']
    print('role',role,'name',name,'running',info['State']['Running'],'cid',cid[:12],'owner',info['Config']['Labels'].get('io.sbarbase.owner'))
    if info['State']['Running']:raise SystemExit('Refusing: the retained database is not stopped')
    blocked=pending(state)
    if blocked:raise SystemExit('Refusing: pending authority state exists: '+', '.join(blocked))
    target=hba_target.Target(cid,name,owner,image)
    intent=adoption.publish_intent(docker,state,name=name,owner=owner,image=image)
    print('intent published: adoption',intent['adoption'],'generation',intent['generation'],'volume',intent['volume'])
    completed=adoption.execute(docker,state,target=target)
    pin=hba_generation.load(state)
    after=json.loads(docker('inspect',name).stdout)[0]
    checks={
        'completed checkpoint reached':completed['phase']=='completed',
        'database is stopped again by exact identity':after['Id']==cid and after['State']['Running'] is False,
        'generation pin equals the intent generation':pin['generation']==intent['generation'] and pin['target']==asdict(target),
        'every durable checkpoint exists':all((state/adoption.CHECKPOINTS/(phase+'.json')).exists() for phase in adoption.PHASES),
        'adoption intent consumed':not (state/adoption.NAME).exists(),
        'owner and pinned image unchanged':after['Config']['Labels'].get('io.sbarbase.owner')==owner and after['Image']==image,
    }
    for label,ok in checks.items():
        print(('ok: ' if ok else 'FAIL: ')+label)
    evidence={'scope':('Retained '+role+' database adoption on the actual stopped container, with only that container started and '
                        'stopped again. Quiescence of legacy host clients is an explicit operational assumption. No application '
                        'service was started. Rule preservation is proven byte for byte by lab/verify-retained.py.'),
              'role':role,'source':{'name':name,'owner':owner,'image':image},
              'authority_state':'installation root' if role=='source' else 'per-target state',
              'adoption':intent['adoption'],'generation':intent['generation'],'volume':intent['volume'],
              'checkpoints':list(adoption.PHASES),'checks':checks,'passed':all(checks.values())}
    out=ROOT/'docs'/'evidence'/('retained-'+role+'-adoption.json')
    existing=json.loads(out.read_text()) if out.exists() else {}
    merged={**existing,**evidence,'checks':{**existing.get('checks',{}),**checks},
            'passed':all({**existing.get('checks',{}),**checks}.values())}
    out.write_text(json.dumps(merged,indent=1)+'\n')
    print('evidence:',out)
    if not all(checks.values()):sys.exit(1)


if __name__=='__main__':
    if len(sys.argv)>2 or (len(sys.argv)==2 and sys.argv[1] not in ('source','target')):
        raise SystemExit('usage: adopt-retained.py [source|target]')
    main(sys.argv[1] if len(sys.argv)==2 else 'source')