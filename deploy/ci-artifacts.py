#!/usr/bin/env python3
"""Collect changed CI reports without uploading checked-in historical results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


REPORTS = {
    'first_project': 'docker-first-project.json',
    'backup_restore': 'docker-backup-restore.json',
    'offsite': 'docker-offsite-checks.json',
    'studio': 'docker-studio-checks.json',
    'sign_in': 'docker-sign-in-checks.json',
    'realtime': 'docker-realtime-checks.json',
    'functions': 'docker-functions-checks.json',
    'database': 'docker-database-checks.json',
    'import_project': 'docker-import-checks.json',
    'signing': 'docker-signing-checks.json',
    'observe': 'docker-observe-checks.json',
    'upload': 'docker-upload-checks.json',
    'upgrade': 'docker-upgrade-checks.json',
}
HISTORICAL = 'docker-install-checks.json'
LIMIT = 65536
EMPTY_HOST_REPORTS = {
    'console-build.json': 'console-build',
    'console-serve.json': 'checks',
    'tls-termination.json': 'checks',
    'supervisor-unit.json': 'supervisor-unit',
    'server-acceptance-rehearsal.json': 'checks',
    'server-acceptance-latest.json': 'checks',
}


def contracts(name):
    if name == 'docker':
        return [(step, filename, 'checks') for step, filename in REPORTS.items()]
    if name == 'empty-host':
        return [('empty_host', filename, schema) for filename, schema in EMPTY_HOST_REPORTS.items()]
    raise ValueError('Unknown evidence contract')


def report_state(data, schema):
    if not isinstance(data, dict) or type(data.get('passed')) is not bool:
        raise ValueError('Evidence schema refused')
    if schema == 'checks':
        checks = data.get('checks')
        if (not isinstance(checks, list) or not checks or len(checks) > 1000
                or type(data.get('count')) is not int or data['count'] != len(checks)):
            raise ValueError('Evidence schema refused')
        all_true = all(isinstance(c, dict) and c.get('ok', c.get('passed')) is True for c in checks)
        if data['passed'] and not all_true:
            raise ValueError('Evidence outcome contradiction refused')
        return {'count': len(checks), 'report_passed': data['passed'], 'all_checks_true': all_true}
    if schema == 'console-build':
        problems = data.get('problems'); build = data.get('build')
        if (not isinstance(problems, list) or not isinstance(data.get('page'), dict)
                or (build is not None and (not isinstance(build, dict) or type(build.get('exit')) is not int))):
            raise ValueError('Console build evidence schema refused')
        if data['passed'] != (not problems and (build is None or build['exit'] == 0)):
            raise ValueError('Console build outcome contradiction refused')
        return {'report_passed': data['passed'], 'problem_count': len(problems)}
    if schema == 'supervisor-unit':
        if (type(data.get('applied')) is not bool or type(data.get('running_as_root')) is not bool
                or type(data.get('start_deferred')) is not bool or not isinstance(data.get('verify'), str)):
            raise ValueError('Supervisor evidence schema refused')
        if data['passed'] and (data['verify'] != 'passed' or not data['applied'] or not data['running_as_root']):
            raise ValueError('Acceptance supervisor outcome refused')
        return {'report_passed': data['passed'], 'applied': data['applied'],
                'start_deferred': data['start_deferred']}
    raise ValueError('Unknown evidence schema')


def read_report(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError('Evidence file type or size refused')
        raw = handle.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('Evidence file size refused')
    return raw


def digest(raw):
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def prepare(root, directory, run, attempt, head, contract='docker'):
    if not directory.is_absolute() or not root.is_absolute():
        raise ValueError('Evidence paths must be absolute')
    directory.mkdir(mode=0o700)
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValueError('Evidence directory mode refused')
    hashes = {name: digest(read_report(root / 'docs/evidence' / name))
              for name in [*(item[1] for item in contracts(contract)), HISTORICAL]}
    baseline = {'schema': 1, 'workflow_run': run, 'run_attempt': attempt,
                'workflow_head': head, 'baseline_sha256': hashes, 'contract': contract}
    (directory / 'baseline.json').write_text(json.dumps(baseline, indent=2) + '\n')
    # Keep the collector outside the checkout that the native upgrade replaces.
    (directory / 'collector.py').write_bytes(Path(__file__).read_bytes())
    (directory / 'staging').mkdir(mode=0o700)


def collect(root, directory, outcomes):
    baseline = json.loads((directory / 'baseline.json').read_text())
    rows = []
    errors = []
    for step, name, schema in contracts(baseline['contract']):
        outcome = outcomes.get(step, {}).get('outcome', 'unavailable')
        row = {'file': name, 'step': step, 'step_outcome': outcome,
               'baseline_sha256': baseline['baseline_sha256'][name], 'included': False, 'report_schema': schema}
        if outcome not in ('success', 'failure'):
            row['classification'] = 'unreached_or_cancelled'
        else:
            try:
                raw = read_report(root / 'docs/evidence' / name)
                current_hash = digest(raw)
                row['observed_sha256'] = current_hash
                if raw is None or current_hash == row['baseline_sha256']:
                    row['classification'] = 'missing_or_unchanged'
                    if outcome == 'success': errors.append(name)
                else:
                    data = json.loads(raw)
                    state = report_state(data, schema)
                    row.update(state, classification='current_changed_report', included=True, bytes=len(raw))
                    if outcome == 'success' and not state['report_passed']: errors.append(name)
                    temporary = directory / 'staging' / ('.' + name)
                    with temporary.open('xb') as target:
                        target.write(raw)
                    temporary.rename(directory / 'staging' / name)
            except (OSError, ValueError, TypeError, AttributeError):
                row['classification'] = 'unreadable_or_invalid'
                row['included'] = False
                if outcome == 'success': errors.append(name)
        rows.append(row)
    if baseline['contract'] == 'empty-host' and outcomes.get('empty_host', {}).get('outcome') == 'success':
        copies = [row for row in rows if row['file'] in (
            'server-acceptance-rehearsal.json', 'server-acceptance-latest.json')]
        if (len(copies) != 2 or not all(row['included'] for row in copies)
                or copies[0].get('observed_sha256') != copies[1].get('observed_sha256')):
            errors.append('acceptance_copy_mismatch')
    manifest = {k: baseline[k] for k in ('schema', 'workflow_run', 'run_attempt', 'workflow_head', 'contract')}
    manifest.update(reports=rows, excluded_historical=[{
        'file': HISTORICAL, 'baseline_sha256': baseline['baseline_sha256'][HISTORICAL],
        'reason': 'Checked-in historical transcription; never a current probe output'}],
        packaging_valid=not errors,
        scope='Workflow head identifies the run input, not derived upgrade release commits. '
              'Included reports retain their own scope and passed state. No production acceptance implied.')
    (directory / 'staging' / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Publish the complete manifest and selected reports as one directory.
    (directory / 'staging').rename(directory / 'reports')
    return not errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'collect'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--run'); parser.add_argument('--attempt'); parser.add_argument('--head')
    parser.add_argument('--contract', choices=['docker', 'empty-host'], default='docker')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.root, args.directory, args.run, args.attempt, args.head, args.contract)
    else:
        if not collect(args.root, args.directory, json.loads(os.environ['CI_EVIDENCE_STEP_OUTCOMES'])):
            raise SystemExit('Successful step lacked valid changed evidence; see run-manifest.json')


if __name__ == '__main__': main()
