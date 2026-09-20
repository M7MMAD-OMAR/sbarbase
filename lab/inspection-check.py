"""Verify a stopped retained installation is observed without logical mutations."""
import hashlib
import json
from pathlib import Path
from provisioning_inspection import inspect_state

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.lab/upstream'


def snapshot():
    paths=[STATE/name for name in ('control.sqlite','cutover-operation.json','recovery-target.json','endpoints.json')]
    paths+=list((STATE/'effect-outcomes').glob('*.json'))
    return {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.exists()}


def main():
    checks=[]
    def check(name,condition):
        if not condition:raise RuntimeError(name)
        checks.append(name)
    before=snapshot()
    report=inspect_state(STATE)
    check('independent existing locks acquired',report.get('local_ownership')=='exclusive_snapshot')
    check('current fixture has no pending receipt',report.get('receipt_status')=='missing')
    check('Docker inventory observed rather than inferred',report.get('docker_status')=='observed' and bool(report.get('containers')))
    check('all observed owned containers remain stopped',all(not x['running'] for x in report['containers']))
    check('no source database query attempted',report['database']['status']=='not_queried')
    check('observation never authorizes replay',report['safe_to_replay'] is False)
    check('catalog journals endpoints and native witnesses unchanged',snapshot()==before)
    result={'scope':'Read-only inspection of current stopped retained installation with no pending receipt. Database read branch and pending-state combinations covered by isolated tests, not this live probe.',
            'owned_containers_observed':len(report['containers']),'count':len(checks),'checks':checks}
    (ROOT/'docs/evidence/provisioning-inspection-checks.json').write_text(json.dumps(result,indent=2)+'\n')
    print(str(len(checks))+' live read-only inspection checks passed')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Inspection verification failed; no replay attempted') from None
