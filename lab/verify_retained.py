"""Verify a completed retained adoption from durable evidence only.

Usage: /usr/bin/python3 lab/verify_retained.py [source|target]

Reads the stopped container's HBA file through docker cp (a tar stream is never
compared), the private checkpoints and the generation pin of that database's
authority state. No container is started, the installation catalog is opened
read-only and nothing is written outside the evidence file.

Preservation method: the published file is one fresh revision-marker line
followed by the exact pre-adoption bytes, so removing only the first marker line
and hashing the remainder must reproduce the journal's expected digest. Older
marker lines inside the preserved region stay part of the comparison.
"""
import datetime
import hashlib
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
import hba_adoption as adoption
import hba_authority as authority
import hba_generation
import hba_runtime

LAB=Path(__file__).resolve().parent
ROOT=LAB.parent
STATE=ROOT/'.lab'/'upstream'
MARKER='# sbarbase-hba-revision:'


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
            if handle is None:raise RuntimeError('Archive member is not readable')
            return handle.read().decode()


def placement(role):
    image=json.loads((LAB/'distro-image.lock.json').read_text())['id']
    if role=='source':
        return {'state':STATE,'name':'sbarbase-durable-db','owner':'durable-upstream','image':image}
    descriptor=json.loads((STATE/'recovery-target.json').read_text())
    prefix=descriptor['prefix']
    return {'state':hba_runtime.target_state(STATE,prefix),'name':prefix+'-db','owner':'recovery-target','image':image}


def source_environments():
    """Read the shared catalog read-only; never create or write it."""
    uri=(STATE/'control.sqlite').as_uri()+'?mode=ro'
    catalog=sqlite3.connect(uri,uri=True)
    try:return sorted(row[0] for row in catalog.execute('SELECT DISTINCT runtime FROM runtime_routing') if row[0])
    finally:catalog.close()


def preserved_bytes(text):
    """Remove exactly the first published marker line, nothing else."""
    lines=text.splitlines(keepends=True)
    for index,line in enumerate(lines):
        if line.startswith(MARKER):return ''.join(lines[:index]+lines[index+1:])
    return ''.join(lines)


def script_digest():
    return hashlib.sha256((LAB/'verify_retained.py').read_bytes()+(LAB/'hba_adoption.py').read_bytes()).hexdigest()


def main(role):
    info_placement=placement(role)
    state=info_placement['state'];name=info_placement['name']
    owner=info_placement['owner'];image=info_placement['image']
    info=json.loads(docker('inspect',name).stdout)[0]
    cid=info['Id']
    pin=hba_generation.load(state)
    outcome=adoption.read_checkpoint(state,'hba-completed')['outcome']
    record=outcome['journal']
    text=read_stopped_file(cid+':'+adoption.HBA_PATH)
    lines=text.splitlines(keepends=True)
    markers=[line for line in lines if line.startswith(MARKER)]
    preserved=preserved_bytes(text)
    digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
    if role=='target':
        environment=json.loads((STATE/'recovery-target.json').read_text())['environment']
        environments=[environment] if environment else []
    else:
        environments=source_environments()
    expected_rules=[f'host {runtime_name} {runtime_name}_{role_name} 0.0.0.0/0 scram-sha-256'
                    for runtime_name in environments for role_name in ('auth','rest','storage')]
    present=set(line.strip() for line in preserved.splitlines())
    checks={
        'database still stopped by exact identity':info['State']['Running'] is False and info['Id']==cid,
        'owner and pinned image unchanged':info['Config']['Labels'].get('io.sbarbase.owner')==owner and info['Image']==image,
        'generation pin matches a completed target':pin['target']['container_id']==cid and pin['target']['owner']==owner,
        'adoption intent consumed':not (state/adoption.NAME).exists(),
        'every durable checkpoint exists':all((state/adoption.CHECKPOINTS/(phase+'.json')).exists() for phase in adoption.PHASES),
        'live file digest equals the recorded applied content':digest(text)==authority.digest(record['content']),
        'effective rules preserved byte for byte':digest(preserved)==record['expected'],
        'exactly one fresh revision marker':len(markers)==1,
        'environment rules for this placement are present':all(rule in present for rule in expected_rules),
        'completion archived as publication-witnessed':outcome['application']=='publication-witnessed' and outcome['activation']=='unknown',
        'no pending HBA journal remains':not (state/'hba-operation.json').exists(),
        'no pending worker effect remains':not (state/'worker-effect.json').exists(),
    }
    for label,ok in checks.items():
        print(('ok: ' if ok else 'FAIL: ')+label)
    out=ROOT/'docs'/'evidence'/('retained-'+role+'-adoption.json')
    existing=json.loads(out.read_text()) if out.exists() else {}
    # The verification section is replaced wholesale: a check produced by an
    # older code revision never survives into a new claim.
    evidence={**{key:value for key,value in existing.items() if key not in
                ('verification_checks','verification_passed','verification_script_digest','verification_run_at',
                 'checks','passed')},
              'role':role,
              'verification_scope':('Verification of the completed retained '+role+' adoption, read from durable evidence and the stopped '
                                    'container only. The pre-adoption bytes are evidenced by the journal expected digest, so rule preservation '
                                    'is proven byte for byte. Quiescence of legacy host clients remains an explicit operational assumption. '
                                    'No container was started and the catalog was read read-only.'),
              'hba_before_digest':record['expected'],'hba_applied_digest':authority.digest(record['content']),
              'hba_live_digest':digest(text),'hba_preserved_digest':digest(preserved),
              'revision_marker_count':len(markers),'environment_rule_count':len(expected_rules),
              'generation':pin['generation'],
              'verification_checks':checks,'verification_passed':all(checks.values()),
              'verification_script_digest':script_digest(),
              'verification_run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds')}
    out.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',out)
    if not all(checks.values()):sys.exit(1)


if __name__=='__main__':
    if len(sys.argv)>2 or (len(sys.argv)==2 and sys.argv[1] not in ('source','target')):
        raise SystemExit('usage: verify_retained.py [source|target]')
    main(sys.argv[1] if len(sys.argv)==2 else 'source')