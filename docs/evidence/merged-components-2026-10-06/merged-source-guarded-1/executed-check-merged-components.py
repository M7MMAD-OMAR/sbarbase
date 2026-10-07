import hashlib
import json
import pathlib
import re
import secrets
import subprocess
import sys

EXPECTED = sys.argv[1]
if hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest() != EXPECTED:
    raise SystemExit('Checker source changed')
out = pathlib.Path(sys.argv[2])
out.mkdir(exist_ok=True)
owner = secrets.token_hex(16)
tag = 'sbarbase-merged-check:' + owner
owned = []
names = []
image_id = None
report = {'total': 0, 'failed': 0, 'skipped': 0, 'evidence': str(out),
          'checker_sha256': EXPECTED, 'observations': [], 'cleanup': []}

def ledger():
    temporary = out / 'ledger.pending'
    temporary.write_text(json.dumps({'schema': 1, 'owner': owner, 'tag': tag,
                                    'cids': owned, 'names': names, 'image_id': image_id}) + '\n')
    temporary.replace(out / 'ledger.json')

ledger()

def call(argv, timeout=15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

def inspect(cid):
    result = call(['docker', 'inspect', cid])
    if result.returncode:
        raise RuntimeError('Exact owned container inspection failed')
    row = json.loads(result.stdout)[0]
    if row['Id'] != cid or row['Config']['Labels'].get('sbarbase.merged-check') != owner:
        raise RuntimeError('Exact owned container identity changed')
    if row['Image'] != image_id:
        raise RuntimeError('Exact owned container image changed')
    return row

def check(name, argv, parser, minimum, timeout):
    names.append('sb-merged-' + owner + '-' + name)
    ledger()
    create = call(['docker', 'create', '--name', names[-1],
                  '--label', 'sbarbase.merged-check=' + owner, '--init',
                  '--network', 'none', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--pids-limit', '128',
                  '--memory', '512m', '--memory-swap', '512m', '--cpus', '.5',
                  '--env', 'PYTHONDONTWRITEBYTECODE=1',
                  '--env', 'PYTHONPATH=/opt/sbarbase/lab',
                  '--entrypoint', argv[0], tag, *argv[1:]])
    if create.returncode or not re.fullmatch(r'[a-f0-9]{64}', create.stdout.strip()):
        raise RuntimeError('Owned container creation failed: ' + create.stderr)
    cid = create.stdout.strip()
    owned.append(cid)
    ledger()
    row = inspect(cid)
    (out / (name + '-admission.json')).write_text(json.dumps(row, indent=2) + '\n')
    result = call(['docker', 'start', '--attach', cid], timeout)
    log = result.stdout + result.stderr
    (out / (name + '.log')).write_text(log)
    row = inspect(cid)
    (out / (name + '-terminal.json')).write_text(json.dumps(row, indent=2) + '\n')
    counts = {'total': 0, 'failed': 0, 'skipped': 0}
    valid = not row['State']['Running'] and not row['State']['OOMKilled']
    valid = valid and result.returncode == 0 and row['State']['ExitCode'] == 0
    if parser == 'bun':
        passes = re.findall(r'(?m)^\s*(\d+) pass$', log)
        fails = re.findall(r'(?m)^\s*(\d+) fail$', log)
        skips = re.findall(r'(?m)^\s*(\d+) (?:skip|todo)$', log)
        valid = valid and len(passes) == 1 and len(fails) == 1
        if len(passes) == 1 and len(fails) == 1:
            counts = {'total': int(passes[0]) + int(fails[0]) + sum(map(int, skips)),
                      'failed': int(fails[0]), 'skipped': sum(map(int, skips))}
    elif parser == 'unittest':
        summaries = re.findall(r'Ran (\d+) tests? in', log)
        endings = re.findall(r'(?m)^(OK(?: \([^\n]*\))?|FAILED(?: \([^\n]*\))?)$', log)
        valid = valid and len(summaries) == 1 and len(endings) == 1
        if len(summaries) == 1:
            counts['total'] = int(summaries[0])
            counts['failed'] = sum(int(n) for n in re.findall(r'(?:failures|errors)=(\d+)', log))
            counts['skipped'] = sum(int(n) for n in re.findall(r'skipped=(\d+)', log))
        valid = valid and endings == ['OK']
    valid = valid and counts['total'] - counts['skipped'] >= minimum
    valid = valid and counts['failed'] == 0 and counts['skipped'] == 0
    observation = {'name': name, 'argv': argv, 'parser': parser, 'counts': counts,
                   'returncode': result.returncode, 'passed': valid, 'cid': cid}
    report['observations'].append(observation)
    for key in counts:
        report[key] += counts[key]
    if not valid:
        report['failed'] = max(1, report['failed'])
        raise RuntimeError('Merged source gate failed: ' + name)

try:
    with (out / 'build.log').open('w') as log:
        subprocess.run(['docker', 'build', '--iidfile', str(out / 'image-id'),
                        '--label', 'sbarbase.merged-check=' + owner,
                        '--file', 'deploy/verify/Dockerfile', '--tag', tag, '.'],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    image_id = (out / 'image-id').read_text().strip()
    ledger()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
        raise RuntimeError('Build image identity is invalid')
    report['image_id'] = image_id
    check('bun', ['bun', 'test', 'tests', '--timeout', '10000'], 'bun', 420, 90)
    check('types', ['bun', 'run', 'typecheck:control'], 'exit', 0, 60)
    check('backup', ['python3', '-m', 'unittest', 'discover', '-s', 'lab', '-p', 'test_backup*.py'], 'unittest', 72, 45)
    check('restore', ['python3', '-m', 'unittest', 'discover', '-s', 'lab', '-p', 'test_restore*.py'], 'unittest', 65, 45)
    check('fixtures-registry', ['python3', '-m', 'unittest', '-v',
          'lab.test_disposable_recovery_drill', 'lab.test_disposable_storage_backup_drill',
          'lab.test_capability_registry', 'lab.test_plan_acceptance'], 'unittest', 60, 45)
    check('registry', ['python3', 'deploy/verify/capability_registry.py', 'validate'], 'exit', 0, 30)
    check('plan', ['python3', 'deploy/check_plan.py'], 'exit', 0, 30)
except Exception as error:
    report['failed'] = max(1, report['failed'])
    report['error'] = str(error)
finally:
    for cid in owned:
        try:
            row = inspect(cid)
            if row['State']['Running']:
                killed = call(['docker', 'kill', '--signal', 'KILL', cid], 20)
                if killed.returncode:
                    raise RuntimeError('Exact owned container kill failed')
            removed = call(['docker', 'rm', cid], 20)
            if removed.returncode:
                raise RuntimeError('Exact owned container removal failed')
            absent = call(['docker', 'inspect', cid], 10)
            verified = absent.returncode == 1 and ('No such object: ' + cid) in absent.stderr
            report['cleanup'].append({'cid': cid, 'removed': True, 'absence_verified': verified})
            if not verified:
                raise RuntimeError('Exact owned absence remains unproven')
        except Exception as error:
            report['cleanup'].append({'cid': cid, 'error': str(error)})
            report['failed'] = max(1, report['failed'])
    try:
        if image_id:
            current = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])
            if current.returncode or current.stdout.strip() != image_id:
                raise RuntimeError('Owned image tag identity changed')
            removed = call(['docker', 'image', 'rm', tag], 20)
            if removed.returncode:
                raise RuntimeError('Owned image tag removal failed')
            absent = call(['docker', 'image', 'inspect', tag])
            verified = absent.returncode == 1 and 'No such image:' in absent.stderr
            report['cleanup'].append({'image_tag': tag, 'removed': True, 'absence_verified': verified})
            if not verified:
                raise RuntimeError('Owned image tag absence remains unproven')
    except Exception as error:
        report['cleanup'].append({'image_tag': tag, 'error': str(error)})
        report['failed'] = max(1, report['failed'])
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
raise SystemExit(int(report['failed'] != 0 or report['skipped'] != 0 or report['total'] == 0))
