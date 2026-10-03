"""Owned lightweight fixture for actual Studio image admission and launch."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import studio
import image_identity
from run_checks import source_digest


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):raise ValueError('Invalid fresh fixture identity')
    name, network = fixture + '-studio-service', fixture + '-studio-net'
    owner = 'sbarbase-fixture-' + fixture
    report = {'scope': 'owned-studio-image-admission-and-launch', 'source_sha256': source_digest(),
              'commands': [], 'attempted_runtime_commands': [], 'cases': [], 'passed': False,
              'limitations': ['Public image admission and Ubuntu sleep fixture, no Studio/meta/database service.',
                              'Fixture owner/network/private directory and omitted IO flags; no IO enforcement.',
                              'No full up/browser workflow, classic store, independent host or G0 acceptance.']}
    ids = set()
    temporary = tempfile.TemporaryDirectory(prefix='sbarbase-studio-identity-')
    stage = 'prepare'

    def native(*args, check=True, data=None):
        result = subprocess.run(['docker', *args], input=data, text=True, capture_output=True, timeout=60)
        report['commands'].append({'args': list(args), 'exit_code': result.returncode,
                                   'stdout': result.stdout, 'stderr': result.stderr})
        if 'warning' in result.stderr.lower():raise RuntimeError('Native Docker warning: ' + result.stderr.strip())
        if check and result.returncode:raise RuntimeError('Native Docker failed: ' + result.stderr.strip())
        return result

    def transport(*args, **kwargs):
        report['attempted_runtime_commands'].append(list(args))
        if args[0] == 'ps':
            args = (*args, '--filter', 'label=io.sbarbase.owner=' + owner)
        elif args[0] == 'image' and args[1] == 'inspect':
            pass
        elif args[0] == 'inspect':
            if args[-1] not in ids | {name}:raise RuntimeError('Unexpected fixture inspection')
        elif args[0] == 'run':
            if stage != 'create' or args[args.index('--name') + 1] != name:
                raise RuntimeError('Unexpected fixture creation')
            args = (args[0], '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--entrypoint', '/usr/bin/sleep', *args[1:], '120')
        else:
            raise RuntimeError('Unexpected fixture Docker operation')
        result = native(*args, **kwargs)
        if args[0] == 'run' and not result.returncode:ids.add(result.stdout.strip())
        return result

    try:
        studio.runtime.OWNER, studio.runtime.NETWORK = owner, network
        studio.runtime.PRIVATE = Path(temporary.name)
        studio.lab.docker = transport
        studio.resource_policy.io_flags = lambda tier: []
        admitted = studio.ensure_images()
        report['cases'].append({'case': 'real-studio-lock-admission', 'passed': True, 'references': admitted})
        native('network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network)
        reference = 'docker.io/library/ubuntu@sha256:3595d7fc4286a33fad0fd853a4063e654287a9c3787437d7937c94ca3f7a804e'
        stage = 'create'
        address = studio.launch(name, 'operator.meta', {'SBARBASE_PUBLIC_FIXTURE': 'yes'}, reference)
        if len(ids) != 1:raise RuntimeError('Unexpected fixture creation count')
        cid = next(iter(ids))
        actual = json.loads(native('inspect', cid).stdout)[0]
        if actual['Config']['Image'] != reference or actual['NetworkSettings']['Networks'][network]['IPAddress'] != address:
            raise RuntimeError('Created fixture binding differs')
        Path('/evidence/service.json').write_text(json.dumps(actual, indent=2) + '\n')
        report['cases'].append({'case': 'qualified-create', 'passed': True, 'container_id': cid,
                                'reference': reference, 'daemon_id': actual['Image'], 'address': address})
        stage = 'refusal'
        missing = 'docker.io/library/sbarbase-studio-missing@sha256:' + 'a' * 64
        before = len(report['attempted_runtime_commands'])
        try:
            studio.launch(name, 'operator.meta', {'SBARBASE_PUBLIC_FIXTURE': 'yes'}, missing)
        except image_identity.IdentityError as error:
            attempts = report['attempted_runtime_commands'][before:]
            if any(c[0] in ('run', 'start', 'rm', 'pull') for c in attempts):
                raise RuntimeError('Refusal attempted a lifecycle mutation')
            native_failure = report['commands'][-1]
            if not image_identity.is_missing(missing, native_failure['stdout'], native_failure['stderr']) or not str(error).startswith('Image inspection refused for ' + missing + ':'):
                raise RuntimeError('Unexpected refusal reason: ' + str(error))
            after = json.loads(native('inspect', cid).stdout)[0]
            if after['Image'] != actual['Image'] or after['Config']['Image'] != reference:
                raise RuntimeError('Refused fixture identity changed')
            report['cases'].append({'case': 'missing-proof-retains-fixture', 'passed': True, 'detail': str(error)})
        else:
            raise RuntimeError('Missing immutable proof was accepted')
        report['passed'] = True
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
    finally:
        for cid in ids:
            try:native('rm', '-f', cid)
            except Exception as error:report['passed'] = False;report['cleanup_error'] = str(error)
        # Removal of an uncreated network is an explicit preparation failure.
        try:native('network', 'rm', network)
        except Exception as error:report['passed'] = False;report['cleanup_error'] = str(error)
        temporary.cleanup()
        Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':raise SystemExit(main())
