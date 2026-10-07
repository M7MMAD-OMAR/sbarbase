import json
import pathlib
import re
import subprocess
import sys

path = pathlib.Path(sys.argv[1])
report = {'schema': 1, 'failed': 0, 'observations': []}

def call(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)

def absence(result, kind, value):
    prefix = r'(?:error response from daemon:\s*|error:\s*)?'
    message = prefix + r'no such ' + kind + r':\s*' + re.escape(value)
    return (result.returncode == 1 and result.stdout.strip() == '[]'
            and re.fullmatch(message, result.stderr.strip(), re.IGNORECASE) is not None)

try:
    if path.is_file():
        ledger = json.loads(path.read_text())
        owner = ledger['owner']
        if not re.fullmatch(r'[a-f0-9]{32}', owner):
            raise ValueError('Invalid owned ledger identity')
        expected_tag = 'sbarbase-merged-check:' + owner
        if ledger['tag'] != expected_tag:
            raise ValueError('Invalid owned image tag')
        seen = set()
        references = ledger['cids'] + ledger['names']
        for ref in references:
            if not (re.fullmatch(r'[a-f0-9]{64}', ref) or re.fullmatch('sb-merged-' + owner + r'-[a-z-]+', ref)):
                raise ValueError('Invalid exact owned reference')
            found = call(['docker', 'inspect', ref])
            if absence(found, 'object', ref):
                report['observations'].append({'reference': ref, 'absence_verified': True,
                                              'argv': ['docker', 'inspect', ref],
                                              'returncode': found.returncode, 'stdout': found.stdout, 'stderr': found.stderr})
                continue
            if found.returncode:
                raise RuntimeError('Exact owned inspection remains uncertain')
            row = json.loads(found.stdout)[0]
            cid = row['Id']
            if row['Config']['Labels'].get('sbarbase.merged-check') != owner:
                raise RuntimeError('Owned label differs; reconciliation refused')
            if row['Name'][1:] not in ledger['names']:
                raise RuntimeError('Owned name differs; reconciliation refused')
            if ledger['image_id'] and row['Image'] != ledger['image_id']:
                raise RuntimeError('Owned image differs; reconciliation refused')
            if cid in seen:
                continue
            seen.add(cid)
            if row['State']['Running']:
                killed = call(['docker', 'kill', '--signal', 'KILL', cid])
                if killed.returncode:
                    raise RuntimeError('Exact owned kill remains uncertain')
            removed = call(['docker', 'rm', cid])
            if removed.returncode:
                raise RuntimeError('Exact owned removal remains uncertain')
            absent = call(['docker', 'inspect', cid])
            if not absence(absent, 'object', cid):
                raise RuntimeError('Exact owned absence remains uncertain')
            report['observations'].append({'reference': ref, 'cid': cid, 'removed': True, 'absence_verified': True})
        image = call(['docker', 'image', 'inspect', expected_tag])
        if absence(image, 'image', expected_tag):
            report['observations'].append({'image_tag': expected_tag, 'absence_verified': True,
                                          'argv': ['docker', 'image', 'inspect', expected_tag],
                                          'returncode': image.returncode, 'stdout': image.stdout, 'stderr': image.stderr})
        else:
            if image.returncode:
                raise RuntimeError('Exact owned image inspection remains uncertain')
            row = json.loads(image.stdout)[0]
            if row['Config'].get('Labels', {}).get('sbarbase.merged-check') != owner:
                raise RuntimeError('Owned image label differs; reconciliation refused')
            if ledger['image_id'] and row['Id'] != ledger['image_id']:
                raise RuntimeError('Owned image identity differs; reconciliation refused')
            removed = call(['docker', 'image', 'rm', expected_tag])
            if removed.returncode:
                raise RuntimeError('Owned image tag removal remains uncertain')
            if not absence(call(['docker', 'image', 'inspect', expected_tag]), 'image', expected_tag):
                raise RuntimeError('Owned image tag absence remains uncertain')
            report['observations'].append({'image_tag': expected_tag, 'removed': True, 'absence_verified': True})
    else:
        report['observations'].append({'ledger_absent': True, 'meaning': 'No checker allocation recorded'})
except Exception as error:
    report['failed'] = 1
    report['error'] = str(error)
output = path.with_name(sys.argv[2] if len(sys.argv) > 2 else 'guard-result.json')
if output.exists():
    raise SystemExit('Refusing to overwrite a prior guard observation')
output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
raise SystemExit(report['failed'])
