"""Owned lightweight Docker fixture for the actual durable launch function."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import durable_runtime as runtime
from run_checks import source_digest


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fresh fixture identity')
    name, network = fixture + '-durable-service', fixture + '-durable-net'
    owner = 'sbarbase-fixture-' + fixture
    report = {'scope': 'owned-durable-launch-immutable-identity', 'source_sha256': source_digest(),
              'commands': [], 'attempted_runtime_commands': [], 'cases': [], 'passed': False,
              'limitations': ['Lightweight Ubuntu fixture, no Supabase/database service.',
                              'Temporary fixture owner/network/private path and omitted IO flags; no IO enforcement proof.',
                              'HBA initialization, full startup, classic store and independent host are not exercised.']}
    ids = set()
    temporary = tempfile.TemporaryDirectory(prefix='sbarbase-identity-')
    stage = 'prepare'

    def native(*args, check=True, data=None):
        result = subprocess.run(['docker', *args], text=True, input=data, capture_output=True, timeout=60)
        report['commands'].append({'args': list(args), 'exit_code': result.returncode,
                                   'stdout': result.stdout, 'stderr': result.stderr})
        if 'warning' in result.stderr.lower():
            raise RuntimeError('Native Docker warning: ' + result.stderr.strip())
        if check and result.returncode:
            raise RuntimeError('Native Docker command failed: ' + result.stderr.strip())
        return result

    def transport(*args, **kwargs):
        report['attempted_runtime_commands'].append(list(args))
        if args[0] == 'ps':
            args = (*args, '--filter', 'label=io.sbarbase.owner=' + owner)
        if args[0] == 'run':
            if stage != 'create' or args[args.index('--name') + 1] != name:
                raise RuntimeError('Unexpected fixture creation')
            args = (args[0], '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', *args[1:])
        if args[0] in ('start', 'rm') and (args[-1] not in ids or stage != 'reuse'):
            raise RuntimeError('Unexpected fixture lifecycle mutation')
        result = native(*args, **kwargs)
        if args[0] == 'run' and not result.returncode:
            ids.add(result.stdout.strip())
        return result

    try:
        runtime.OWNER, runtime.NETWORK = owner, network
        runtime.PRIVATE = Path(temporary.name)
        runtime.UPGRADE_INTENT = runtime.PRIVATE / 'absent-upgrade-intent.json'
        runtime.lab.docker = transport
        runtime.resource_policy.io_flags = lambda tier: []
        digest = 'sha256:3595d7fc4286a33fad0fd853a4063e654287a9c3787437d7937c94ca3f7a804e'
        pin = {'tag': 'ubuntu:26.04', 'id': digest, 'digests': ['ubuntu@' + digest]}
        service = runtime.Runtime.__new__(runtime.Runtime)
        service.pins = {'auth': pin, 'db': json.loads((ROOT / 'lab/images.lock.json').read_text())['db']}
        native('network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + owner, network)
        stage = 'create'
        cid, created = service.launch(name, 'auth', {'SBARBASE_PUBLIC_FIXTURE': 'yes'}, '256m', .25,
                                      command=('/usr/bin/true',), tier='production')
        actual = json.loads(native('inspect', cid).stdout)[0]
        reference, expected = service.image('auth')
        if not created or actual['Config']['Image'] != reference or actual['Image'] != expected:
            raise RuntimeError('Created fixture image binding differs')
        Path('/evidence/service.json').write_text(json.dumps(actual, indent=2) + '\n')
        report['cases'].append({'case': 'qualified-create', 'passed': True, 'container_id': cid,
                                'reference': reference, 'daemon_id': expected})
        stage = 'reuse'
        reused, created = service.launch(name, 'auth', {'SBARBASE_PUBLIC_FIXTURE': 'yes'}, '256m', .25, tier='production')
        if reused != cid or created:
            raise RuntimeError('Retained fixture was replaced')
        report['cases'].append({'case': 'retained-reuse', 'passed': True})
        stage = 'refusal'
        for case, component in [('database-image-drift', 'db'), ('owner-collision', 'auth')]:
            if case == 'owner-collision':runtime.OWNER = owner + '-wrong'
            before = len(report['attempted_runtime_commands'])
            try:
                service.launch(name, component, {'SBARBASE_PUBLIC_FIXTURE': 'yes'}, '256m', .25, tier='production')
            except RuntimeError as error:
                commands = report['attempted_runtime_commands'][before:]
                if any(c[0] in ('run', 'start', 'rm') for c in commands):
                    raise RuntimeError('Refusal dispatched a lifecycle mutation')
                expected_error = 'Runtime resource ownership collision' if case == 'owner-collision' else 'Runtime drift requires explicit reconciliation'
                if str(error) != expected_error:
                    raise RuntimeError('Unexpected refusal reason: ' + str(error))
                report['cases'].append({'case': case, 'passed': True, 'detail': str(error)})
            else:
                raise RuntimeError('Expected retained fixture refusal')
            finally:runtime.OWNER = owner
        report['passed'] = True
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
    finally:
        for cid in ids:
            try:native('rm', '-f', cid)
            except Exception as error:report['passed'] = False;report['cleanup_error'] = str(error)
        try:native('network', 'rm', network)
        except Exception as error:report['passed'] = False;report['cleanup_error'] = str(error)
        temporary.cleanup()
        Path('/evidence/report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
