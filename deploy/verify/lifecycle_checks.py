#!/usr/bin/env python3
"""Coordinate bounded isolated SB-07 checks after the root grants the verifier role.

This host process orchestrates containers only. Tests run in the baked public
source image. The actual resource stage alone receives the explicitly selected
local daemon socket and creates fresh UUID resources recorded by its fixture.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4


def command(argv, timeout=600):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def source_digest(root):
    files = []
    for folder in ('src', 'lab', 'tests', 'deploy', 'ui'):
        for path in (root / folder).rglob('*'):
            relative = path.relative_to(root)
            if any(part in ('node_modules', '__pycache__', '.lab') for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError('Public lifecycle source contains a symlink')
            if path.is_file():
                files.append(path)
    for name in ('package.json', 'bun.lock', 'tsconfig.json', 'tsconfig.control.json', 'PROJECT_GOAL.md'):
        files.append(root / name)
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('component', 'actual'))
    parser.add_argument('--output')
    parser.add_argument('--docker-host', default='unix:///var/run/docker.sock')
    args = parser.parse_args()
    if os.environ.get('SBARBASE_LIFECYCLE_ROLE') != 'assigned':
        raise SystemExit('Root coordination must assign the lifecycle verifier role first')
    if (not args.docker_host.startswith('unix:///') or args.docker_host.startswith('unix:////')
            or '..' in Path(args.docker_host[7:]).parts):
        raise SystemExit('Explicit local Unix Docker socket required')
    root = Path(__file__).resolve().parents[2]
    run = str(uuid4())
    output = Path(args.output or root / '.lab' / 'lifecycle-evidence' / run).resolve()
    output.mkdir(parents=True, exist_ok=False)
    image = 'sbarbase-lifecycle-verify:' + run
    docker = ['docker', '--host', args.docker_host]
    initial = source_digest(root)
    result = {'run': run, 'stage': args.stage, 'source_digest': initial, 'image_tag': image,
              'checks': [], 'cleanup': []}
    allocations = []
    total = failed = 0
    def save():
        temporary = output / 'result.json.pending'
        with temporary.open('w') as handle:
            json.dump(result, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output / 'result.json')
        descriptor = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    def outer_identity(item, entry):
        marks = item.get('Config', {}).get('Labels', {})
        host = item.get('HostConfig', {})
        mounts = item.get('Mounts', [])
        expected_socket = {'Type': 'bind', 'Source': args.docker_host[7:], 'Destination': '/var/run/docker.sock'}
        mounted = [{key: mount.get(key) for key in expected_socket} for mount in mounts]
        if (not re.fullmatch(r'[a-f0-9]{64}', item.get('Id', ''))
                or item.get('Name') != '/' + entry['allocation_name']
                or entry.get('container', item['Id']) != item['Id']
                or item.get('Image') != result.get('image_id')
                or marks.get('io.sbarbase.owner') != 'sbarbase-lifecycle-verifier'
                or marks.get('io.sbarbase.verifier') != run
                or marks.get('io.sbarbase.stage') != entry['name']
                or item.get('Config', {}).get('Entrypoint') != [entry['command'][0]]
                or item.get('Config', {}).get('Cmd') != entry['command'][1:]
                or host.get('Init') is not True or host.get('NetworkMode') != 'none'
                or host.get('Memory') != 512 * 1024 * 1024 or host.get('MemorySwap') != 512 * 1024 * 1024
                or host.get('NanoCpus') != 500000000 or host.get('PidsLimit') != 128
                or host.get('Privileged') or host.get('PortBindings')
                or host.get('CapDrop') != ['ALL']
                or not any(value in ('no-new-privileges', 'no-new-privileges:true', 'no-new-privileges=true') for value in host.get('SecurityOpt', []))
                or mounted != ([expected_socket] if entry['socket'] else [])):
            raise RuntimeError('Exact verifier allocation identity differs')
    def execute(name, argv, parser_name='json', socket=False, environment=None, expected_count=None):
        nonlocal total, failed
        entry = {'name': name, 'allocation_name': 'sb07-verifier-' + str(uuid4()), 'image_id': result['image_id'],
                 'command': argv, 'socket': socket, 'status': 'allocation-pending', 'execution_requested': False}
        allocations.append(entry)
        result['checks'].append(entry)
        save()
        create = [*docker, 'create', '--name', entry['allocation_name'], '--init', '--network', 'none', '--memory', '512m',
                  '--memory-swap', '512m', '--cpus', '0.5', '--pids-limit', '128',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                  '--label', 'io.sbarbase.owner=sbarbase-lifecycle-verifier',
                  '--label', 'io.sbarbase.verifier=' + run,
                  '--label', 'io.sbarbase.stage=' + name,
                  '--env', 'SBARBASE_LIFECYCLE_VERIFIER=1', '--entrypoint', argv[0]]
        for key, value in (environment or {}).items():
            create += ['--env', key + '=' + value]
        if socket:
            create += ['--user', '0', '--mount', 'type=bind,src=' + args.docker_host[7:] + ',dst=/var/run/docker.sock']
        create += [result['image_id'], *argv[1:]]
        created = command(create, 30)
        if created.returncode or not re.fullmatch(r'[a-f0-9]{64}\s*', created.stdout):
            raise RuntimeError('Verifier container creation failed')
        cid = created.stdout.strip()
        entry.update({'container': cid, 'status': 'allocated'})
        save()
        inspected = command([*docker, 'inspect', cid], 30)
        if inspected.returncode:
            raise RuntimeError('Verifier pre-start inspection failed')
        outer_identity(json.loads(inspected.stdout)[0], entry)
        entry.update({'status': 'running', 'execution_requested': True})
        save()
        try:
            started = command([*docker, 'start', '--attach', cid], 540)
        except subprocess.TimeoutExpired as error:
            # Stopping preserves the owner's writable layer and prevents new allocations.
            stopped = command([*docker, 'stop', '--time', '5', cid], 15)
            if stopped.returncode:
                raise RuntimeError('Timed-out verifier stop unproven')
            copied = command([*docker, 'cp', cid + ':/evidence/.', str(output)], 30)
            if copied.returncode:
                raise RuntimeError('Timed-out verifier ledger copy failed; preserve owner')
            started = subprocess.CompletedProcess([], 124,
                (error.stdout or b'').decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout or '',
                'Lifecycle verifier deadline exceeded')
        log = started.stdout + started.stderr
        (output / (name + '.log')).write_text(log)
        inspected = command([*docker, 'inspect', cid], 30)
        if inspected.returncode:
            raise RuntimeError('Verifier inspection failed')
        identity = json.loads(inspected.stdout)[0]
        outer_identity(identity, entry)
        (output / (name + '-container.json')).write_text(inspected.stdout)
        copied = command([*docker, 'cp', cid + ':/evidence/.', str(output)], 30)
        if copied.returncode:
            raise RuntimeError('Verifier evidence copy failed')
        count = 0
        if parser_name == 'bun':
            passes = re.findall(r'^\s*(\d+) pass\s*$', log, re.M)
            failures = re.findall(r'^\s*(\d+) fail\s*$', log, re.M)
            count = int(passes[-1]) if len(passes) == 1 else 0
            passed = count > 0 and failures == ['0'] and not re.search(r'\b[1-9][0-9]* (skip|todo)\b', log)
        elif parser_name == 'unittest':
            matches = re.findall(r'^Ran (\d+) tests? in ', log, re.M)
            count = int(matches[0]) if len(matches) == 1 else 0
            passed = count > 0 and re.search(r'^OK\s*$', log, re.M) is not None and 'skipped=' not in log
        elif parser_name == 'exit':
            passed = not re.search(r'(?:error TS\d+|warning:)', log, re.I)
        else:
            observation = json.loads(started.stdout)
            if any(type(observation.get(key)) is not int for key in ('total', 'failed', 'skipped')):
                raise RuntimeError('Fixture summary invalid')
            count = observation['total']
            passed = count > 0 and observation['failed'] == observation['skipped'] == 0
        if expected_count is not None:
            passed = passed and count == expected_count
        passed = passed and started.returncode == identity['State']['ExitCode'] == 0
        total += count if count else 0 if parser_name == 'exit' and passed else 1
        failed += 0 if passed else max(count, 1)
        result['checks'][-1].update({'status': 'passed' if passed else 'failed', 'total': count,
                                    'expected_total': expected_count, 'exit_code': started.returncode})
        save()
    def reconcile_helpers():
        """Independent exact-allocation cleanup if the fixture owner was interrupted."""
        nonlocal total, failed
        path = output / 'actual-resources.json'
        if not path.is_file():
            raise RuntimeError('Fixture allocation ledger missing; preserve owner')
        packet = json.loads(path.read_text())
        installation = packet.get('installation')
        if not isinstance(installation, str) or not re.fullmatch(r'[a-f0-9-]{36}', installation):
            raise RuntimeError('Helper cleanup ledger identity invalid')
        def owned(item, resource, volume=False):
            labels = item.get('Labels', {}) if volume else item.get('Config', {}).get('Labels', {})
            return (labels.get('io.sbarbase.owner') == 'sbarbase-lifecycle'
                    and labels.get('io.sbarbase.installation') == installation
                    and labels.get('io.sbarbase.environment') == resource['runtime']
                    and labels.get('io.sbarbase.resource') == resource['resource']
                    and labels.get('io.sbarbase.role') == 'environment'
                    and not any(key in labels for key in ('io.sbarbase.retained', 'io.sbarbase.recovery')))
        for entry in reversed(packet['containers']):
            resource = entry.get('resource_contract')
            name = resource['id'] if resource else entry['name']
            observation = command([*docker, 'inspect', name], 30)
            if observation.returncode:
                listing = command([*docker, 'ps', '-a', '--no-trunc', '--format', '{{.ID}} {{.Names}}'], 30)
                if listing.returncode or any(name in line.split() for line in listing.stdout.splitlines()):
                    raise RuntimeError('Helper absence unproven')
                result['cleanup'].append({'helper': name, 'absent': True})
                continue
            item = json.loads(observation.stdout)[0]
            if resource is None:
                if entry['name'] != 'sb07-' + entry['resource']:
                    raise RuntimeError('Planned helper identity invalid')
                resource = {'id': item['Id'], 'runtime': entry['runtime'], 'resource': entry['resource']}
            if item['Id'] != resource['id'] or not owned(item, resource):
                raise RuntimeError('Foreign helper cleanup refused')
            stopped = command([*docker, 'stop', '--time', '5', resource['id']], 15)
            removed = command([*docker, 'rm', resource['id']], 30)
            listing = command([*docker, 'ps', '-a', '--no-trunc', '--format', '{{.ID}}'], 30)
            if stopped.returncode or removed.returncode or listing.returncode or resource['id'] in listing.stdout.splitlines():
                raise RuntimeError('Helper cleanup unproven')
            result['cleanup'].append({'helper': resource['id'], 'absent': True})
        for entry in reversed(packet['volumes']):
            resource = entry.get('resource', entry['planned'])
            observation = command([*docker, 'volume', 'inspect', resource['id']], 30)
            if observation.returncode:
                listing = command([*docker, 'volume', 'ls', '--format', '{{.Name}}'], 30)
                if listing.returncode or resource['id'] in listing.stdout.splitlines():
                    raise RuntimeError('Helper volume absence unproven')
                result['cleanup'].append({'helper_volume': resource['id'], 'absent': True})
                continue
            item = json.loads(observation.stdout)[0]
            if (item['Name'] != resource['id'] or not owned(item, resource, True)
                    or resource.get('createdAt', item['CreatedAt']) != item['CreatedAt']):
                raise RuntimeError('Foreign helper volume cleanup refused')
            # Docker refuses mounted volumes; force is never supplied.
            removed = command([*docker, 'volume', 'rm', resource['id']], 30)
            listing = command([*docker, 'volume', 'ls', '--format', '{{.Name}}'], 30)
            if removed.returncode or listing.returncode or resource['id'] in listing.stdout.splitlines():
                raise RuntimeError('Helper volume cleanup unproven')
            result['cleanup'].append({'helper_volume': resource['id'], 'absent': True})
    try:
        save()
        build = command([*docker, 'build', '--file', str(root / 'deploy/verify/Dockerfile'),
                         '--label', 'io.sbarbase.owner=sbarbase-lifecycle-verifier',
                         '--label', 'io.sbarbase.verifier=' + run, '--tag', image, str(root)], 600)
        (output / 'build.log').write_text(build.stdout + build.stderr)
        if build.returncode:
            raise RuntimeError('Public source image build failed')
        inspected = command([*docker, 'image', 'inspect', image], 30)
        image_id = json.loads(inspected.stdout)[0]['Id']
        result['image_id'] = image_id
        (output / 'image.json').write_text(inspected.stdout)
        if args.stage == 'component':
            execute('bun', ['bun', 'test', 'tests', '--timeout', '10000'], 'bun')
            execute('python', ['/usr/bin/python3', '-m', 'unittest', 'discover', '-s', 'lab', '-p', 'test_lifecycle*.py', '-v'], 'unittest', expected_count=54)
            execute('control-types', ['bun', 'run', 'typecheck:control'], 'exit')
        else:
            execute('actual-journal', ['bun', 'lab/lifecycle-journal-check.ts'], expected_count=28)
            info = command([*docker, 'info', '--format', '{{.ID}}'], 30)
            if info.returncode or not info.stdout.strip():
                raise RuntimeError('Assigned daemon identity unavailable')
            execute('actual-resources', ['/usr/bin/python3', 'lab/lifecycle-resource-check.py',
                    '--docker-host', 'unix:///var/run/docker.sock', '--daemon-id', info.stdout.strip(),
                    '--image', image_id], socket=True, expected_count=40)
        if source_digest(root) != initial:
            raise RuntimeError('Source changed during lifecycle verification')
    except Exception as error:
        result['failure'] = type(error).__name__
        failed += 1
        total += 1
    finally:
        for entry in reversed(allocations):
            target = entry.get('container', entry['allocation_name'])
            try:
                inspected = command([*docker, 'inspect', target], 30)
                if inspected.returncode:
                    listing = command([*docker, 'ps', '-a', '--no-trunc', '--format', '{{.ID}} {{.Names}}'], 30)
                    if listing.returncode or any(target in row.split() for row in listing.stdout.splitlines()):
                        raise RuntimeError('Verifier allocation absence unproven')
                    if entry['socket'] and entry['execution_requested']:
                        # Losing the owner does not prove its recorded helpers are absent.
                        reconcile_helpers()
                    result['cleanup'].append({'allocation_name': entry['allocation_name'], 'absent': True})
                    save()
                    continue
                item = json.loads(inspected.stdout)[0]
                outer_identity(item, entry)
                cid = item['Id']
                entry['container'] = cid
                save()
                if item['State'].get('Running'):
                    stopped = command([*docker, 'stop', '--time', '5', cid], 15)
                    if stopped.returncode:
                        raise RuntimeError('Verifier stop unproven; preserve owner')
                if entry['execution_requested']:
                    copied = command([*docker, 'cp', cid + ':/evidence/.', str(output)], 30)
                    if copied.returncode:
                        raise RuntimeError('Verifier ledger copy failed; preserve owner')
                started_at = item['State'].get('StartedAt')
                helpers_possible = entry['socket'] and (entry['execution_requested'] or started_at and not started_at.startswith('0001-'))
                if helpers_possible:
                    if not entry['execution_requested']:
                        copied = command([*docker, 'cp', cid + ':/evidence/.', str(output)], 30)
                        if copied.returncode:
                            raise RuntimeError('Uncertain verifier ledger copy failed; preserve owner')
                    reconcile_helpers()
                    save()
                removed = command([*docker, 'rm', cid], 30)
                absent = command([*docker, 'ps', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'], 30)
                clean = removed.returncode == absent.returncode == 0 and not absent.stdout.strip()
                result['cleanup'].append({'container': cid, 'absent': clean})
                if not clean:
                    failed += 1
                    total += 1
            except Exception:
                result['cleanup'].append({'allocation_name': entry['allocation_name'], 'container': entry.get('container'),
                                          'unresolved': True, 'preserved_for_reconciliation': True})
                failed += 1
                total += 1
        try:
            if any(entry.get('preserved_for_reconciliation') for entry in result['cleanup']):
                raise RuntimeError('Preserve verifier image while allocation cleanup is unresolved')
            inspected = command([*docker, 'image', 'inspect', image], 30)
            if inspected.returncode:
                absent = command([*docker, 'image', 'ls', '--filter', 'reference=' + image,
                                  '--format', '{{.Repository}}:{{.Tag}}'], 30)
                if absent.returncode or image in absent.stdout.splitlines():
                    raise RuntimeError('Verifier image allocation absence unproven')
            else:
                observed = json.loads(inspected.stdout)[0]
                marks = observed.get('Config', {}).get('Labels', {})
                if (image not in observed.get('RepoTags', []) or marks.get('io.sbarbase.owner') != 'sbarbase-lifecycle-verifier'
                        or marks.get('io.sbarbase.verifier') != run
                        or result.get('image_id', observed['Id']) != observed['Id']):
                    raise RuntimeError('Foreign image tag cleanup refused')
                removed = command([*docker, 'image', 'rm', image], 30)
                absent = command([*docker, 'image', 'ls', '--filter', 'reference=' + image, '--format', '{{.ID}}'], 30)
                if removed.returncode or absent.returncode or absent.stdout.strip():
                    raise RuntimeError('Verifier image tag cleanup unproven')
            result['cleanup'].append({'image_tag': image, 'absent': True})
        except Exception:
            result['cleanup'].append({'image_tag': image, 'unresolved': True, 'preserved_for_reconciliation': True})
            failed += 1
            total += 1
        result.update({'total': total, 'failed': failed, 'skipped': 0})
        save()
    print(json.dumps({'total': total, 'failed': failed, 'skipped': 0}))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
