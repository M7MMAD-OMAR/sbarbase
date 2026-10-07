#!/usr/bin/env python3
"""Actual fresh-resource reclaim drill, run only inside the assigned verifier.

No service ports, existing resources, shared database or production recovery
admission are used. The two helper containers use the exact baked verifier image.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
from uuid import uuid4

from lifecycle_resources import Adapter, Docker, OWNER, Refused, labels


def atomic(path, value):
    temporary = path.with_suffix('.new')
    with temporary.open('w') as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-host', required=True)
    parser.add_argument('--daemon-id', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--evidence', default='/evidence')
    args = parser.parse_args()
    if os.environ.get('SBARBASE_LIFECYCLE_VERIFIER') != '1' or not re.fullmatch(r'sha256:[a-f0-9]{64}', args.image):
        raise SystemExit('Assigned immutable lifecycle verifier required')
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    installation = str(uuid4())
    operation = str(uuid4())
    docker = Docker(args.docker_host, args.daemon_id)
    ledger = {'installation': installation, 'operation': operation, 'daemon': args.daemon_id,
              'image': args.image, 'containers': [], 'volumes': [], 'checks': [], 'cleanup': []}
    ledger_path = evidence / 'actual-resources.json'
    atomic(ledger_path, ledger)
    def check(name, passed, **detail):
        ledger['checks'].append({'id': name, 'passed': bool(passed), **detail})
        atomic(ledger_path, ledger)
        if not passed:
            raise RuntimeError(name)

    def request(action, resource):
        return adapter.apply({'action': action, 'resource': resource, 'runtime': resource['runtime'],
                              'epoch': 1, 'operation': operation})

    def content(cid):
        _, raw = docker.request('GET', '/containers/' + cid + '/archive?path=/payload/sentinel', raw=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile():
                raise RuntimeError('Unexpected fixture payload archive')
            return archive.extractfile(members[0]).read()

    def create(runtime, word, mount_negative=False):
        volume_id, container_id = str(uuid4()), str(uuid4())
        marks = {'io.sbarbase.owner': OWNER, 'io.sbarbase.installation': installation,
                 'io.sbarbase.environment': runtime, 'io.sbarbase.role': 'environment'}
        name = 'sb07-' + volume_id
        planned = {'kind': 'volume', 'id': name, 'resource': volume_id,
                   'installation': installation, 'runtime': runtime}
        ledger['volumes'].append({'planned': planned, 'created': False})
        atomic(ledger_path, ledger)
        _, created = docker.request('POST', '/volumes/create', (201,),
                                    body={'Name': name, 'Driver': 'local',
                                          'Labels': {**marks, 'io.sbarbase.resource': volume_id}})
        volume = {**planned, 'createdAt': created['CreatedAt']}
        ledger['volumes'][-1].update({'resource': volume, 'created': True})
        atomic(ledger_path, ledger)
        name = 'sb07-' + container_id
        pending = {'name': name, 'resource': container_id, 'runtime': runtime}
        ledger['containers'].append(pending)
        atomic(ledger_path, ledger)
        mounts = [{'Type': 'volume', 'Source': volume['id'], 'Target': '/payload'}]
        directory = None
        setup = ''
        if mount_negative:
            directory_id = str(uuid4())
            directory = '/payload/' + runtime + '/' + directory_id
            pending['mount_negative'] = {'directory': directory, 'resource': directory_id, 'volume': volume['id']}
            atomic(ledger_path, ledger)
            # Alias this same fresh volume within its own disposable fixture tree.
            mounts.append({'Type': 'volume', 'Source': volume['id'], 'Target': directory + '/mounted'})
            marker = json.dumps({'installation': installation, 'runtime': runtime, 'resource': directory_id})
            setup = ("Path(" + repr(directory) + ").mkdir(parents=True,exist_ok=True);"
                     "Path(" + repr(directory + '/.sbarbase-lifecycle-owner.json') + ").write_text(" + repr(marker) + ");"
                     "Path(" + repr(directory + '/local-payload') + ").write_bytes(b'preserved mount fixture');")
        pending['planned_mounts'] = mounts
        atomic(ledger_path, ledger)
        script = "from pathlib import Path;import time;Path('/payload/sentinel').write_bytes(" + repr(word) + "*4096);" + setup + "time.sleep(600)"
        _, created = docker.request('POST', '/containers/create?name=' + name, (201,), body={
            'Image': args.image, 'User': '0', 'Entrypoint': ['/usr/bin/python3'], 'Cmd': ['-c', script],
            'Labels': {**marks, 'io.sbarbase.resource': container_id},
            'Healthcheck': {'Test': ['CMD', '/usr/bin/test', '-s', '/payload/sentinel'],
                            'Interval': 1000000000, 'Timeout': 1000000000, 'Retries': 3},
            'HostConfig': {'NetworkMode': 'none', 'Memory': 32 * 1024 * 1024,
                           'MemorySwap': 32 * 1024 * 1024, 'NanoCpus': 250000000,
                           'PidsLimit': 32, 'CapDrop': ['ALL'], 'ReadonlyRootfs': True,
                           'SecurityOpt': ['no-new-privileges'],
                           'Mounts': mounts,
                           'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '1m', 'max-file': '1'}}}})
        resource = {'kind': 'container', 'id': created['Id'], 'resource': container_id,
                    'installation': installation, 'runtime': runtime}
        pending['resource_contract'] = resource
        atomic(ledger_path, ledger)
        docker.request('POST', '/containers/' + resource['id'] + '/start', (204,))
        deadline = time.monotonic() + 15
        while True:
            current = docker.container(resource)
            actual_mounts = sorted((mount['Type'], mount.get('Name'), mount['Destination']) for mount in current.get('Mounts', []))
            expected_mounts = sorted((mount['Type'], mount['Source'], mount['Target']) for mount in mounts)
            if actual_mounts != expected_mounts:
                raise RuntimeError('Exact planned helper mount identities differ')
            if current['State'].get('Health', {}).get('Status') == 'healthy':
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Fixture readiness timed out')
            time.sleep(.2)
        return resource, volume, pending.get('mount_negative')

    def mount_refusal(container, declaration):
        # Runs only known baked source in the exact already allocated helper namespace.
        data = {'installation': installation, 'runtime': container['runtime'], **declaration}
        program = """import hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,'/opt/sbarbase/lab')
from lifecycle_resources import Adapter,Refused,mount_id
data=json.loads(sys.argv[1]); path=Path(data['directory']); identity=path.stat()
payload=path/'local-payload'; before=hashlib.sha256(payload.read_bytes()).hexdigest()
resource={'kind':'directory','id':str(path),'resource':data['resource'],'installation':data['installation'],
 'runtime':data['runtime'],'device':identity.st_dev,'inode':identity.st_ino,'marker':'.sbarbase-lifecycle-owner.json'}
root_fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY); child_fd=os.open(path/'mounted',os.O_RDONLY|os.O_DIRECTORY)
try:
 root_mount=mount_id(root_fd); child_mount=mount_id(child_fd)
 same_device=os.fstat(root_fd).st_dev==os.fstat(child_fd).st_dev
finally:
 os.close(root_fd);os.close(child_fd)
if not same_device or root_mount==child_mount: raise RuntimeError('Expected same-device mount fixture is absent')
outcome='unexpected-acceptance'
try: Adapter('/payload',data['installation']).apply({'action':'purge','resource':resource,'runtime':data['runtime'],
 'epoch':1,'operation':sys.argv[2]})
except Refused as error: outcome=str(error)
after=hashlib.sha256(payload.read_bytes()).hexdigest()
print(json.dumps({'sameDevice':same_device,'rootMount':root_mount,'childMount':child_mount,'outcome':outcome,
 'payloadBefore':before,'payloadAfter':after,'directoryPresent':path.is_dir()}))
"""
        docker.container(container)
        _, created = docker.request('POST', '/containers/' + container['id'] + '/exec', (201,), body={
            'AttachStdout': True, 'AttachStderr': True, 'Tty': True, 'User': '0',
            'Cmd': ['/usr/bin/python3', '-c', program, json.dumps(data), operation]})
        _, output = docker.request('POST', '/exec/' + created['Id'] + '/start', (200,),
                                   body={'Detach': False, 'Tty': True}, raw=True)
        _, observed = docker.request('GET', '/exec/' + created['Id'] + '/json')
        if observed.get('ContainerID') != container['id'] or observed.get('ExitCode') != 0 or observed.get('Running') is not False:
            raise RuntimeError('Mount fixture execution identity or exit differs')
        result = json.loads(output)
        atomic(evidence / 'actual-mount.json', {'container': container['id'], 'declaration': declaration, 'observation': result})
        check('UUID.same-device-bind-mount-refused', result['sameDevice'] and result['rootMount'] != result['childMount']
              and result['outcome'] == 'directory_mount_boundary' and result['directoryPresent']
              and result['payloadBefore'] == result['payloadAfter'], **result)

    status = 1
    try:
        with tempfile.TemporaryDirectory(prefix='sb07-') as temporary:
            adapter = Adapter(temporary, installation, docker)
            target_runtime, neighbor_runtime = ('e_' + uuid4().hex[:24] for _ in range(2))
            target, target_volume, _ = create(target_runtime, b'target')
            neighbor, neighbor_volume, mount_declaration = create(neighbor_runtime, b'neighbor', mount_negative=True)
            neighbor_hash = hashlib.sha256(content(neighbor['id'])).hexdigest()
            target_hash = hashlib.sha256(content(target['id'])).hexdigest()
            check('UUID.fixture-health', bool(target_hash and neighbor_hash))
            mount_refusal(neighbor, mount_declaration)
            try:
                request('purge', {**target, 'id': neighbor['id']})
            except Refused:
                check('UUID.foreign-CID-refused', True)
            else:
                check('UUID.foreign-CID-refused', False)
            check('UUID.quarantine', request('quarantine', target)['outcome'] == 'quarantined')
            check('UUID.retained-volume', request('inspect', target_volume)['outcome'] == 'present')
            check('UUID.restore-health', request('restore', target)['outcome'] == 'restored')
            check('UUID.restore-bytes', hashlib.sha256(content(target['id'])).hexdigest() == target_hash)
            request('quarantine', target)
            observed = request('inspect', target_volume)['observedBytes']
            check('UUID.volume-measured', observed >= 4096 * len(b'target'), observedBytes=observed)
            try:
                request('purge', target_volume)
            except Refused:
                check('UUID.mounted-volume-refused', True)
            else:
                check('UUID.mounted-volume-refused', False)
            request('restore', target)
            root, state = Path(temporary) / 'resources', Path(temporary) / 'state'
            root.mkdir()
            state.mkdir()
            config = Path(temporary) / 'docker-journal-fixture.json'
            atomic(config, {'catalog': str(Path(temporary) / 'catalog.sqlite'), 'root': str(root),
                            'state': str(state), 'installation': installation, 'runtime': target_runtime,
                            'resources': [target, target_volume], 'dockerHost': args.docker_host, 'daemon': args.daemon_id,
                            'volumeBytes': observed})
            child = subprocess.Popen(['bun', 'lab/lifecycle-docker-journal-check.ts', 'run', str(config)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, stderr = child.communicate(timeout=180)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise RuntimeError('Docker catalog journal deadline exceeded')
            (evidence / 'actual-docker-journal.log').write_text(stdout + stderr)
            summary = json.loads(stdout)
            journal = json.loads((evidence / 'actual-docker-journal.json').read_text())
            for observation in journal['observations']:
                check(observation['id'], observation['passed'], detail=observation.get('detail'))
            check('UUID.catalog-journal-complete', child.returncode == 0 and summary['failed'] == summary['skipped'] == 0
                  and summary['total'] == len(journal['observations']))
            check('UUID.container-reclaimed', request('purge', target)['outcome'] == 'absent')
            check('UUID.volume-reclaimed', request('purge', target_volume)['outcome'] == 'absent',
                  measuredBeforePurge=observed)
            check('UUID.neighbor-bytes-preserved', hashlib.sha256(content(neighbor['id'])).hexdigest() == neighbor_hash,
                  before=neighbor_hash, after=hashlib.sha256(content(neighbor['id'])).hexdigest())
            check('UUID.neighbor-volume-preserved', request('inspect', neighbor_volume)['outcome'] == 'present')
            check('UUID.neighbor-running', docker.container(neighbor)['State']['Running'] is True)
            ledger['limitations'] = ['Disposable exact-resource effects only; complete SB-05 and application purge are unaccepted.',
                                     'Actual worker SIGKILL and durable Catalog replay are observed; physical host crash is unmeasured.',
                                     'Reclaimed bytes are Docker reported logical writable and volume bytes, not host disk free space.']
            atomic(ledger_path, ledger)
            status = 0
    except Exception:
        ledger['failure'] = 'Actual lifecycle fixture failed; inspect individual retained checks.'
        atomic(ledger_path, ledger)
    finally:
        # Reconcile planned exact UUID names too, covering creation before CID journaling.
        for entry in reversed(ledger['containers']):
            try:
                resource = entry.get('resource_contract')
                if resource is None:
                    code, item = docker.request('GET', '/containers/' + entry['name'] + '/json', (200, 404))
                    if code == 404:
                        continue
                    resource = {'kind': 'container', 'id': item['Id'], 'resource': entry['resource'],
                                'runtime': entry['runtime'], 'installation': installation}
                item = docker.container(resource)
                if item is not None:
                    labels(item['Config']['Labels'], resource)
                    docker.request('POST', '/containers/' + resource['id'] + '/stop?t=5', (204, 304))
                    docker.request('DELETE', '/containers/' + resource['id'] + '?force=false&v=false', (204,))
                absent = docker.container(resource) is None
                ledger['cleanup'].append({'kind': 'container', 'id': resource['id'], 'absent': absent})
                if not absent:
                    status = 1
            except Exception:
                ledger['cleanup'].append({'kind': 'container', 'name': entry['name'], 'unresolved': True})
                status = 1
            atomic(ledger_path, ledger)
        for entry in reversed(ledger['volumes']):
            try:
                resource = entry.get('resource')
                if resource is None:
                    code, item = docker.request('GET', '/volumes/' + entry['planned']['id'], (200, 404))
                    if code == 404:
                        continue
                    resource = {**entry['planned'], 'createdAt': item['CreatedAt']}
                if docker.volume(resource) is not None:
                    if docker.volume_users(resource):
                        raise Refused('fixture_volume_still_mounted')
                    docker.request('DELETE', '/volumes/' + resource['id'] + '?force=false', (204,))
                absent = docker.volume(resource) is None
                ledger['cleanup'].append({'kind': 'volume', 'id': resource['id'], 'absent': absent})
                if not absent:
                    status = 1
            except Exception:
                ledger['cleanup'].append({'kind': 'volume', 'id': entry['planned']['id'], 'unresolved': True})
                status = 1
            atomic(ledger_path, ledger)
    passed = sum(check['passed'] for check in ledger['checks'])
    if status and passed == len(ledger['checks']):
        ledger['checks'].append({'id': 'UUID.fixture-or-cleanup', 'passed': False})
        atomic(ledger_path, ledger)
    print(json.dumps({'total': len(ledger['checks']), 'failed': len(ledger['checks']) - passed,
                      'skipped': 0}))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
