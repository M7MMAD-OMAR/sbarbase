"""Verify existing restored target without modifying its database contents."""
import base64
import json
import time
from pathlib import Path
import durable_runtime as runtime
import run as lab
from recovery_bundle import open_bundle
from recovery_boundaries import verify


def main():
    descriptor=json.loads((runtime.STATE/'recovery-target.json').read_text())
    db=descriptor['database']
    state=json.loads(lab.docker('container','inspect',db).stdout)[0]
    if not state or state['Config']['Labels'].get('io.sbarbase.owner')!='recovery-target':
        raise RuntimeError('Target ownership unavailable')
    if state['State']['Running'] or lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():
        raise RuntimeError('Source and target must be stopped')
    available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
    if available<6*1024*1024:raise RuntimeError('Memory headroom unavailable')
    payload=open_bundle(json.loads(Path(descriptor['archive']).read_text()),base64.b64decode(Path(descriptor['key']).read_text(),validate=True))
    def rows(query):
        result=lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres',data="SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM ("+query+') t;')
        return json.loads(result.stdout)
    try:
        lab.docker('start',db)
        for _ in range(60):
            if lab.docker('exec',db,'pg_isready',check=False).returncode==0:break
            time.sleep(.5)
        checks=verify(payload,rows)
    finally:
        lab.docker('stop',db)
        if json.loads(lab.docker('container','inspect',db).stdout)[0]['State']['Running']:raise RuntimeError('Target failed to stop')
    checks.append('target stopped after boundary verification')
    (lab.ROOT/'docs/evidence/independent-boundary-checks.json').write_text(json.dumps({'scope':'Readback of complete exported scoped role metadata, memberships, database ACL and settings on retained independent target. No application service verification.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' independent boundary checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Boundary verification failed; sensitive output withheld')
