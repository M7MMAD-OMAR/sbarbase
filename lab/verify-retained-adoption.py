"""Verify the completed retained-source adoption from durable evidence only.

Reads the stopped container's HBA file through docker cp (a tar stream is never
compared), the private checkpoints and the generation pin. No container is
started and no private path or secret is written to evidence.
"""
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
import hba_adoption as adoption
import hba_authority as authority
import hba_generation

LAB=Path(__file__).resolve().parent
ROOT=LAB.parent
STATE=ROOT/'.lab'/'upstream'
NAME='sbarbase-durable-db'
OWNER='durable-upstream'


def docker(*args):
    return subprocess.run(['docker',*args],capture_output=True,text=True,check=True,timeout=120)


def read_stopped_file(reference):
    """Extract one path from a stopped container without starting it."""
    with tempfile.TemporaryDirectory() as directory:
        archive=Path(directory)/'copy.tar'
        with archive.open('wb') as output:
            subprocess.run(['docker','cp',reference,'-'],stdout=output,check=True,timeout=120)
        with tarfile.open(archive) as tar:
            member=next(item for item in tar.getmembers() if item.isfile())
            handle=tar.extractfile(member)
            return handle.read().decode()


def main():
    image=json.loads((LAB/'distro-image.lock.json').read_text())['id']
    info=json.loads(docker('inspect',NAME).stdout)[0]
    cid=info['Id']
    pin=hba_generation.load(STATE)
    completion=adoption.read_checkpoint(STATE,'hba-completed')
    outcome=completion['outcome']
    record=outcome['journal']
    text=read_stopped_file(cid+':'+adoption.HBA_PATH)
    lines=text.splitlines(keepends=True)
    markers=[line for line in lines if line.startswith('# sbarbase-hba-revision:')]
    preserved=''.join(line for line in lines if not line.startswith('# sbarbase-hba-revision:'))
    digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
    catalog=sqlite3.connect(STATE/'control.sqlite')
    environments=sorted(row[0] for row in catalog.execute('SELECT DISTINCT runtime FROM runtime_routing') if row[0])
    catalog.close()
    expected_rules=[]
    for runtime in environments:
        for role in ('auth','rest','storage'):
            expected_rules.append(f'host {runtime} {runtime}_{role} 0.0.0.0/0 scram-sha-256')
    present=set(line.strip() for line in preserved.splitlines())
    checks={
        'source still stopped by exact identity':info['State']['Running'] is False and info['Id']==cid,
        'owner and pinned image unchanged':info['Config']['Labels'].get('io.sbarbase.owner')==OWNER and info['Image']==image,
        'generation pin matches a completed target':pin['target']['container_id']==cid and pin['target']['owner']==OWNER,
        'adoption intent consumed':not (STATE/adoption.NAME).exists(),
        'every durable checkpoint exists':all((STATE/adoption.CHECKPOINTS/(phase+'.json')).exists() for phase in adoption.PHASES),
        'live file digest equals the recorded applied content':digest(text)==authority.digest(record['content']),
        'effective rules preserved byte for byte':digest(preserved)==record['expected'],
        'exactly one fresh revision marker':len(markers)==1,
        'inventory rules for every catalog environment are present':all(rule in present for rule in expected_rules),
        'completion archived as publication-witnessed':outcome['application']=='publication-witnessed' and outcome['activation']=='unknown',
        'no pending HBA journal remains':not (STATE/'hba-operation.json').exists(),
        'no pending worker effect remains':not (STATE/'worker-effect.json').exists(),
    }
    for label,ok in checks.items():
        print(('ok: ' if ok else 'FAIL: ')+label)
    evidence={'scope':('Verification of the completed retained-source adoption, read from durable evidence and the stopped container only. '
                        'The pre-adoption bytes are evidenced by the journal expected digest, so rule preservation is proven byte for byte. '
                        'Quiescence of legacy host clients remains an explicit operational assumption. No container was started.'),
              'source':{'name':NAME,'owner':OWNER,'image':image},
              'hba_before_digest':record['expected'],'hba_applied_digest':authority.digest(record['content']),
              'hba_live_digest':digest(text),'hba_preserved_digest':digest(preserved),
              'revision_marker_count':len(markers),'environment_rule_count':len(expected_rules),
              'generation':pin['generation'],'checks':checks,'passed':all(checks.values())}
    out=ROOT/'docs'/'evidence'/'retained-source-adoption.json'
    existing=json.loads(out.read_text()) if out.exists() else {}
    merged={**existing,**evidence,'checks':{**existing.get('checks',{}),**checks},
            'passed':all({**existing.get('checks',{}),**checks}.values())}
    out.write_text(json.dumps(merged,indent=1)+'\n')
    print('evidence:',out)
    if not all(checks.values()):sys.exit(1)


if __name__=='__main__':main()