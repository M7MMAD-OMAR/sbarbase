"""Inspect only public locked images on a selected local daemon; never pull."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import image_identity
import install_server
from run_checks import source_digest


def docker(*args):
    return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=60)


def main():
    report = {'scope': 'local-public-immutable-image-admission',
              'source_sha256': source_digest(), 'images': [], 'errors': [],
              'limitations': ['Read-only socket bind does not restrict Docker API permissions.',
                              'No services started or pulled; no runtime, classic-store, architecture or G0 acceptance.']}
    status = 1
    try:
        pins = install_server.pinned_images()
        template = '{"id":{{json .ID}},"version":{{json .ServerVersion}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}},"os":{{json .OSType}},"architecture":{{json .Architecture}}}'
        info = docker('info', '--format', template)
        if info.returncode:
            raise RuntimeError('Daemon info failed: ' + info.stderr.strip())
        daemon = json.loads(info.stdout)
        if not isinstance(daemon, dict) or daemon.get('os') != 'linux' or not daemon.get('id'):
            raise RuntimeError('Linux daemon identity unavailable')
        daemon['driver_status'] = [item for item in daemon.get('driver_status') or []
                                   if isinstance(item, list) and item and item[0] == 'driver-type']
        report['daemon'] = daemon
        for number, (label, digest, reference) in enumerate(pins, 1):
            entry = {'component': label, 'logical_digest': digest, 'reference': reference, 'passed': False}
            report['images'].append(entry)
            result = docker('image', 'inspect', reference)
            native_file = f'image-{number}.json'
            Path('/evidence', native_file).write_text(result.stdout)
            entry['native_output_file'] = native_file
            if result.returncode:
                entry['error'] = result.stderr.strip() or 'Exact immutable reference unavailable'
                continue
            try:
                record = image_identity.record(result.stdout)
                entry['daemon_id'] = image_identity.resolved_id(reference, record)
                entry['os'] = record.get('Os')
                entry['architecture'] = record.get('Architecture')
                entry['native_output_sha256'] = hashlib.sha256(result.stdout.encode()).hexdigest()
                entry['repo_digests'] = record['RepoDigests']
                entry['passed'] = True
            except image_identity.IdentityError as error:
                entry['error'] = str(error)
        # This exact unused public reference is inspected locally, never pulled.
        missing_ref = 'docker.io/library/sbarbase-identity-missing@sha256:' + 'a' * 64
        missing = docker('image', 'inspect', missing_ref)
        report['native_absence'] = {'reference': missing_ref, 'exit_code': missing.returncode,
                                    'stdout': missing.stdout, 'stderr': missing.stderr,
                                    'recognized': missing.returncode != 0 and image_identity.is_missing(missing_ref, missing.stdout, missing.stderr)}
        # Actual resolver refusal, using deliberate replacements of public proof.
        reference = 'docker.io/library/example@sha256:' + 'a' * 64
        replacements = [
            {'Id': 'sha256:' + 'b' * 64, 'RepoDigests': ['attacker.invalid/replacement@sha256:' + 'a' * 64]},
            {'Id': 'xsha256:' + 'a' * 64 + 'y', 'RepoDigests': [reference]},
        ]
        report['refusal_cases'] = []
        for record in replacements:
            try:
                image_identity.resolved_id(reference, record)
            except image_identity.IdentityError as error:
                report['refusal_cases'].append({'refused': True, 'detail': str(error)})
            else:
                report['refusal_cases'].append({'refused': False})
        report['passed'] = bool(report['images']) and all(e['passed'] for e in report['images']) and all(e['refused'] for e in report['refusal_cases']) and report['native_absence']['recognized']
        status = 0 if report['passed'] else 1
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        report['errors'].append(str(error))
        report['passed'] = False
    Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
