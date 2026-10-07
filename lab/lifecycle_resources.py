"""Exact-identity resource effects for the retained environment lifecycle.

This adapter never discovers ownership from names and never removes shared
engines or volumes. Exact shared SQL effects delegate to the OID-bound driver.
The caller must persist its pending effect before invoking this adapter, which
holds fresh installation effect and operation locks. Resource enrollment and
purge admission belong to the control plane, not this low-level effect boundary.
"""
from lifecycle_native_authority import require_current
import argparse
import ctypes
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
from urllib.parse import quote


OWNER = 'sbarbase-lifecycle'
UUID = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
RUNTIME = re.compile(r'^e_[a-f0-9]{24}$')
RESOURCE_KEYS = {'kind', 'id', 'resource', 'installation', 'runtime',
                 'createdAt', 'device', 'inode', 'marker', 'identity', 'service', 'legacy'}
MAX_RESPONSE = 8 * 1024 * 1024


class Refused(RuntimeError):
    """Closed nonsecret refusal reason."""


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=15)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Docker:
    """Local Unix daemon only; every effect checks its enrolled daemon ID."""
    def __init__(self, endpoint, daemon_id):
        if (not isinstance(endpoint, str) or not endpoint.startswith('unix:///')
                or endpoint.startswith('unix:////') or '..' in Path(endpoint[7:]).parts
                or not daemon_id):
            raise Refused('docker_identity_required')
        if any(os.environ.get(key) for key in ('DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY')):
            raise Refused('docker_override_forbidden')
        self.path = endpoint[7:]
        self.daemon_id = daemon_id
        self.api = ''
        version = self.request('GET', '/version')[1]
        def parts(value):
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+', value):
                raise Refused('docker_api_version_unavailable')
            return tuple(map(int, value.split('.')))
        if not parts(version.get('MinAPIVersion', '1.24')) <= (1, 44) <= parts(version.get('ApiVersion')):
            raise Refused('docker_api_version_unsupported')
        self.api = '/v1.44'
        info = self.request('GET', '/info')[1]
        if info.get('ID') != daemon_id or info.get('OSType') != 'linux':
            raise Refused('daemon_identity_mismatch')

    def request(self, method, path, allowed=(200,), body=None, raw=False):
        if method not in ('GET', 'HEAD'):
            require_current()
        connection = UnixConnection(self.path)
        try:
            encoded = json.dumps(body).encode() if body is not None else None
            connection.request(method, self.api + path, body=encoded,
                               headers={'Content-Type': 'application/json'} if encoded else {})
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE or response.status not in allowed:
                raise Refused('docker_effect_refused')
            value = data if raw else json.loads(data) if data else None
            return response.status, value
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise Refused('docker_observation_unavailable') from error
        finally:
            connection.close()

    def container(self, resource, state=None):
        code, value = self.request('GET', '/containers/' + resource['id'] + '/json?size=true', (200, 404))
        if code == 404:
            # A successful inventory distinguishes absence from an inspect transport failure.
            _, items = self.request('GET', '/containers/json?all=true')
            if not isinstance(items, list) or any(item.get('Id') == resource['id'] for item in items):
                raise Refused('ambiguous_container_absence')
            return None
        if value.get('Id') != resource['id']:
            raise Refused('container_identity_mismatch')
        if 'legacy' in resource:
            import lifecycle_shared
            lifecycle_shared.inspect_legacy_service(self, state, resource, value)
        else:
            labels(value.get('Config', {}).get('Labels'), resource)
            if resource.get('service'):
                if value['Config']['Labels'].get('io.sbarbase.service') != resource['service']:
                    raise Refused('container_service_identity_mismatch')
                if not (value.get('Config', {}).get('Healthcheck') or {}).get('Test'):
                    raise Refused('container_readiness_contract_missing')
        return value

    def volume(self, resource):
        code, value = self.request('GET', '/volumes/' + quote(resource['id'], safe=''), (200, 404))
        if code == 404:
            _, inventory = self.request('GET', '/volumes')
            items = inventory.get('Volumes')
            if not isinstance(items, list) or any(item.get('Name') == resource['id'] for item in items):
                raise Refused('ambiguous_volume_absence')
            return None
        if value.get('Name') != resource['id'] or value.get('CreatedAt') != resource['createdAt']:
            raise Refused('volume_identity_mismatch')
        labels(value.get('Labels'), resource)
        if value.get('Driver') != 'local' or value.get('Options'):
            raise Refused('volume_driver_unsupported')
        return value

    def volume_users(self, resource):
        _, items = self.request('GET', '/containers/json?all=true')
        if not isinstance(items, list):
            raise Refused('container_inventory_unavailable')
        users = []
        for item in items:
            if not isinstance(item.get('Mounts'), list):
                raise Refused('mount_inventory_unavailable')
            if any(mount.get('Type') == 'volume' and mount.get('Name') == resource['id'] for mount in item['Mounts']):
                labels(item.get('Labels'), resource, same_resource=False)
                users.append(item)
        return users

    def volume_bytes(self, resource):
        _, inventory = self.request('GET', '/system/df')
        items = inventory.get('Volumes')
        if not isinstance(items, list):
            raise Refused('volume_measurement_unavailable')
        found = [item for item in items if item.get('Name') == resource['id']]
        if len(found) != 1:
            raise Refused('volume_measurement_unavailable')
        labels(found[0].get('Labels'), resource)
        size = found[0].get('UsageData', {}).get('Size')
        if type(size) is not int or size < 0:
            raise Refused('volume_measurement_unavailable')
        return size


def labels(value, resource, same_resource=True):
    expected = {'io.sbarbase.owner': OWNER,
                'io.sbarbase.installation': resource['installation'],
                'io.sbarbase.environment': resource['runtime'],
                'io.sbarbase.role': 'environment'}
    if same_resource:
        expected['io.sbarbase.resource'] = resource['resource']
    if (not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items())
            or any(key in value for key in ('io.sbarbase.retained', 'io.sbarbase.recovery'))):
        raise Refused('resource_ownership_mismatch')


def validate(resource, runtime, installation):
    if (not isinstance(resource, dict) or set(resource) - RESOURCE_KEYS
            or resource.get('kind') not in ('container', 'volume', 'directory', 'shared-database')
            or not isinstance(resource.get('resource'), str) or not UUID.fullmatch(resource['resource'])
            or resource.get('installation') != installation or resource.get('runtime') != runtime
            or not UUID.fullmatch(installation) or not RUNTIME.fullmatch(runtime)
            or not isinstance(resource.get('id'), str)):
        raise Refused('invalid_resource_contract')
    common = {'kind', 'id', 'resource', 'installation', 'runtime'}
    if resource['kind'] == 'shared-database':
        if set(resource) != common | {'identity'} or resource['id'] != runtime:
            raise Refused('shared_ownership_identity_required')
        import lifecycle_shared
        lifecycle_shared.validate(resource)
    elif resource['kind'] == 'container':
        allowed = common | ({'service'} if 'service' in resource else set()) | ({'legacy'} if 'legacy' in resource else set())
        if set(resource) != allowed or not re.fullmatch(r'[a-f0-9]{64}', resource['id']):
            raise Refused('immutable_container_id_required')
        if 'service' in resource and resource['service'] not in ('auth', 'rest', 'database', 'storage'):
            raise Refused('invalid_service_identity')
        if 'legacy' in resource:
            import lifecycle_shared
            lifecycle_shared.validate_legacy_service(resource)
    elif resource['kind'] == 'volume':
        if (set(resource) != common | {'createdAt'}
                or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', resource['id'])
                or not isinstance(resource['createdAt'], str) or not resource['createdAt']):
            raise Refused('immutable_volume_identity_required')
    elif (set(resource) != common | {'device', 'inode', 'marker'}
          or resource['marker'] != '.sbarbase-lifecycle-owner.json'
          or any(type(resource[key]) is not int or resource[key] < 1 for key in ('device', 'inode'))):
        raise Refused('immutable_directory_identity_required')
    return resource


def safe_root(value):
    path = Path(value)
    if (not path.is_absolute() or path == Path('/') or '..' in path.parts
            or str(path) != value or any(ord(c) < 32 for c in value)):
        raise Refused('invalid_owned_root')
    for parent in reversed((path, *path.parents[:-1])):
        if parent.is_symlink():
            raise Refused('owned_root_symlink_forbidden')
    if not path.is_dir():
        raise Refused('owned_root_missing')
    return path


def mount_id(fd):
    """Linux exposes the VFS mount identity, including same-device bind mounts."""
    try:
        fields = [line.split(':', 1)[1].strip() for line in Path('/proc/self/fdinfo/' + str(fd)).read_text().splitlines()
                  if line.startswith('mnt_id:')]
        if len(fields) != 1 or not fields[0].isdigit() or int(fields[0]) <= 0:
            raise ValueError('Missing mount identity')
        return int(fields[0])
    except (OSError, ValueError):
        raise Refused('directory_mount_identity_unavailable') from None


def owned_root_fd(root):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(root).parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        mount_id(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def rename_exact(source_fd, source, target_fd, target):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise Refused('exclusive_directory_rename_unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    require_current()
    if rename(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def open_directory(root, resource, root_fd=None):
    allocated = root_fd is None
    if allocated:
        root_fd = owned_root_fd(root)
    try:
        return open_pinned_directory(root, resource, root_fd)
    finally:
        if allocated:
            os.close(root_fd)


def open_pinned_directory(root, resource, root_fd):
    path = Path(resource['id'])
    if (not path.is_absolute() or str(path) != resource['id'] or '..' in path.parts
            or not ((path.parent == root / resource['runtime'] and path.name == resource['resource'])
                    or (path.parent == root and path.name == resource['runtime']))):
        raise Refused('directory_outside_owned_root')
    grave = root / (resource['runtime'] + '-' + resource['resource'] + '.purging')
    def exists_at(parent, name):
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    root_mount = mount_id(root_fd)
    nested = path.parent != root
    parent_fd = None
    if nested:
        try:
            parent_fd = os.open(resource['runtime'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            if mount_id(parent_fd) != root_mount:
                raise Refused('directory_mount_boundary')
        except FileNotFoundError:
            pass
        except OSError as error:
            if parent_fd is not None:
                os.close(parent_fd)
            raise Refused('directory_parent_identity_unavailable') from error
        except BaseException:
            if parent_fd is not None:
                os.close(parent_fd)
            raise
    else:
        parent_fd = os.dup(root_fd)
    original = parent_fd is not None and exists_at(parent_fd, path.name)
    retired = exists_at(root_fd, grave.name)
    if original and retired:
        os.close(parent_fd)
        raise Refused('directory_graveyard_collision')
    if retired:
        path = grave
        if parent_fd is not None:
            os.close(parent_fd)
        parent_fd = os.dup(root_fd)
    if parent_fd is None:
        return None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise Refused('directory_identity_unavailable') from error
        try:
            identity = os.fstat(fd)
            if mount_id(fd) != root_mount:
                raise Refused('directory_mount_boundary')
            if (identity.st_dev, identity.st_ino) != (resource['device'], resource['inode']):
                raise Refused('directory_identity_mismatch')
            try:
                marker_fd = os.open(resource['marker'], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if retired and not os.listdir(fd):
                    return fd, path, True
                raise Refused('directory_marker_missing') from None
            with os.fdopen(marker_fd, 'rb') as handle:
                marker_stat = os.fstat(handle.fileno())
                if mount_id(handle.fileno()) != root_mount:
                    raise Refused('directory_mount_boundary')
                if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_nlink != 1 or marker_stat.st_size > 4096:
                    raise Refused('directory_marker_invalid')
                marker = json.load(handle)
            if marker != {'installation': resource['installation'], 'runtime': resource['runtime'], 'resource': resource['resource']}:
                raise Refused('directory_ownership_mismatch')
            return fd, path, retired
        except Exception:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def tree(fd, device, remove=False, expected_mount=None, keep=None):
    """Operate relative to pinned directory FDs, rejecting links and mount crossings."""
    expected_mount = mount_id(fd) if expected_mount is None else expected_mount
    if mount_id(fd) != expected_mount:
        raise Refused('directory_mount_boundary')
    total = 0
    for name in os.listdir(fd):
        if name == keep:
            continue
        item = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if item.st_dev != device:
            raise Refused('directory_mount_boundary')
        if stat.S_ISDIR(item.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if mount_id(child) != expected_mount:
                    raise Refused('directory_mount_boundary')
                if (os.fstat(child).st_dev, os.fstat(child).st_ino) != (item.st_dev, item.st_ino):
                    raise Refused('directory_changed')
                total += tree(child, device, remove, expected_mount)
            finally:
                os.close(child)
            if remove:
                require_current()
                os.rmdir(name, dir_fd=fd)
        elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                current = os.fstat(child)
                if mount_id(child) != expected_mount:
                    raise Refused('directory_mount_boundary')
                if (current.st_dev, current.st_ino, current.st_mode, current.st_nlink) != (item.st_dev, item.st_ino, item.st_mode, item.st_nlink):
                    raise Refused('directory_changed')
            finally:
                os.close(child)
            total += item.st_size
            if remove:
                require_current()
                os.unlink(name, dir_fd=fd)
        else:
            raise Refused('directory_links_or_special_files_forbidden')
    return total


class Adapter:
    def __init__(self, root, installation, docker=None, state=None):
        self.root = safe_root(str(root))
        self.installation = installation
        self.docker = docker
        self.state = state

    def apply(self, request):
        if (not isinstance(request, dict) or set(request) != ({'action','resource','runtime','epoch','operation','shared','runtimeSecrets'} if request.get('action')=='migrate-service' else {'action', 'resource', 'runtime', 'epoch', 'operation','files'} if request.get('action')=='migrate-shared' else {'action', 'resource', 'runtime', 'epoch', 'operation'})
                or request.get('action') not in ('inspect', 'quarantine', 'restore', 'purge','migrate-shared','migrate-service','readiness','inspect-quarantined')
                or type(request.get('epoch')) is not int or request['epoch'] < 0
                or not isinstance(request.get('operation'), str)
                or not (UUID.fullmatch(request['operation']) or
                        request['action'] == 'inspect' and request['operation'] == 'enrollment')
                or request['action'] not in ('inspect','migrate-shared','migrate-service','readiness','inspect-quarantined') and request['epoch'] < 1):
            raise Refused('invalid_effect_request')
        resource = validate(request['resource'], request['runtime'], self.installation)
        action = request['action']
        if action=='migrate-service':
            if resource['kind']!='container' or not resource.get('legacy') or self.docker is None:
                raise Refused('invalid_legacy_service_migration')
            import lifecycle_shared
            shared=validate(request['shared'],request['runtime'],self.installation)
            return lifecycle_shared.migrate_legacy_service(resource,shared,self.docker,self.state,request['runtimeSecrets'])
        if action=='migrate-shared':
            if resource['kind']!='shared-database' or self.docker is None:
                raise Refused('invalid_shared_migration')
            import lifecycle_shared
            files=validate(request['files'],request['runtime'],self.installation)
            return lifecycle_shared.migrate(resource,self.docker,self.root,self.state,request['operation'],request['epoch'],files)
        if resource['kind'] == 'directory':
            result=self.directory(resource,'inspect' if action=='readiness' else action)
            if action=='readiness':
                if result['outcome']!='present':raise Refused('directory_readiness_unproven')
                result['outcome']='restored'
            return result
        if self.docker is None:
            raise Refused('docker_identity_required')
        if resource['kind'] == 'container':
            return self.container(resource, action)
        if resource['kind'] == 'shared-database':
            import lifecycle_shared
            return lifecycle_shared.execute(resource, action, self.docker, self.root,
                                            self.state, request['operation'], request['epoch'])
        result=self.volume(resource,'inspect' if action=='readiness' else action)
        if action=='readiness':
            if result['outcome']!='present':raise Refused('volume_readiness_unproven')
            result['outcome']='restored'
        return result

    def directory(self, resource, action):
        root_fd = owned_root_fd(self.root)
        try:
            return self.pinned_directory(resource, action, root_fd)
        finally:
            os.close(root_fd)

    def pinned_directory(self, resource, action, root_fd):
        opened = open_directory(self.root, resource, root_fd)
        if opened is None:
            if action in ('restore', 'quarantine'):
                raise Refused('retained_resource_missing')
            return {'outcome': 'absent', 'reclaimedBytes': 0}
        fd, path, retired = opened
        try:
            expected_mount = mount_id(root_fd)
            if retired and action not in ('inspect', 'purge'):
                raise Refused('directory_purge_already_started')
            size = tree(fd, resource['device'], expected_mount=expected_mount)
            if action == 'purge':
                if not retired:
                    grave = self.root / (resource['runtime'] + '-' + resource['resource'] + '.purging')
                    # Rename before deleting any entry, keeping its ownership marker.
                    # A replay discovers exactly this inode at the graveyard path.
                    original_parent = (os.dup(root_fd) if path.parent == self.root else
                        os.open(resource['runtime'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd))
                    try:
                        if mount_id(original_parent) != expected_mount:
                            raise Refused('directory_mount_boundary')
                        observed = os.stat(path.name, dir_fd=original_parent, follow_symlinks=False)
                        if (observed.st_dev, observed.st_ino) != (resource['device'], resource['inode']):
                            raise Refused('directory_identity_mismatch')
                        try:
                            rename_exact(original_parent, path.name, root_fd, grave.name)
                        except FileExistsError:
                            raise Refused('directory_graveyard_collision') from None
                        os.fsync(root_fd)
                        os.fsync(original_parent)
                    finally:
                        os.close(original_parent)
                    path = grave
                grave_fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    observed = os.fstat(grave_fd)
                    if (observed.st_dev, observed.st_ino) != (resource['device'], resource['inode']) or mount_id(grave_fd) != expected_mount:
                        raise Refused('directory_identity_mismatch')
                finally:
                    os.close(grave_fd)
                # Keep the identity marker until all other effects have settled. A killed
                # process can reconcile partial removal using the same pinned directory.
                tree(fd, resource['device'], True, expected_mount, resource['marker'])
                os.fsync(fd)
                if resource['marker'] in os.listdir(fd):
                    require_current()
                    os.unlink(resource['marker'], dir_fd=fd)
                os.fsync(fd)
                observed = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
                if (observed.st_dev, observed.st_ino) != (resource['device'], resource['inode']):
                    raise Refused('directory_identity_mismatch')
                require_current()
                os.rmdir(path.name, dir_fd=root_fd)
                os.fsync(root_fd)
                return {'outcome': 'purged', 'reclaimedBytes': size}
            return {'outcome': {'inspect': 'present', 'quarantine': 'quarantined', 'restore': 'restored'}[action],
                    'reclaimedBytes': 0, 'observedBytes': size}
        finally:
            os.close(fd)

    def container(self, resource, action):
        item = self.docker.container(resource, self.state)
        if item is None:
            if action in ('restore', 'quarantine'):
                raise Refused('retained_resource_missing')
            return {'outcome': 'absent', 'reclaimedBytes': 0}
        if action == 'inspect':
            size = item.get('SizeRw')
            if type(size) is not int or size < 0:
                raise Refused('container_measurement_unavailable')
            return {'outcome': 'present', 'reclaimedBytes': 0, 'observedBytes': size}
        if action == 'quarantine':
            self.docker.request('POST', '/containers/' + resource['id'] + '/stop?t=5', (204, 304))
            if self.docker.container(resource, self.state)['State'].get('Running') is not False:
                raise Refused('container_stop_unproven')
            return {'outcome': 'quarantined', 'reclaimedBytes': 0}
        if action=='readiness':
            if not self.container_ready(resource,item):raise Refused('container_readiness_unproven')
            return {'outcome':'restored','reclaimedBytes':0}
        if action == 'restore':
            if not (item.get('Config', {}).get('Healthcheck') or {}).get('Test') and 'legacy' not in resource:
                raise Refused('container_readiness_contract_missing')
            self.docker.request('POST', '/containers/' + resource['id'] + '/start', (204, 304))
            deadline = time.monotonic() + 15
            while True:
                current = self.docker.container(resource, self.state)
                if current and self.container_ready(resource,current):
                    return {'outcome': 'restored', 'reclaimedBytes': 0}
                if time.monotonic() >= deadline:
                    raise Refused('container_readiness_unproven')
                time.sleep(.2)
        if item.get('State', {}).get('Running') is not False:
            raise Refused('purge_requires_quarantined_container')
        size = item.get('SizeRw')
        if type(size) is not int or size < 0:
            raise Refused('container_measurement_unavailable')
        self.docker.request('DELETE', '/containers/' + resource['id'] + '?force=false&v=false', (204,))
        if self.docker.container(resource, self.state) is not None:
            raise Refused('container_reclaim_unproven')
        return {'outcome': 'purged', 'reclaimedBytes': size}

    def container_ready(self,resource,item):
        if item.get('State',{}).get('Running') is not True:
            return False
        if (item.get('Config',{}).get('Healthcheck') or {}).get('Test'):
            return item['State'].get('Health',{}).get('Status')=='healthy'
        if 'legacy' in resource:
            import lifecycle_shared
            return lifecycle_shared.legacy_service_ready(self.docker,self.state,resource,item)
        return False

    def volume(self, resource, action):
        item = self.docker.volume(resource)
        if item is None:
            if action in ('restore', 'quarantine'):
                raise Refused('retained_resource_missing')
            return {'outcome': 'absent', 'reclaimedBytes': 0}
        users = self.docker.volume_users(resource)
        if action == 'purge':
            if users:
                raise Refused('volume_still_mounted')
            size = self.docker.volume_bytes(resource)
            self.docker.request('DELETE', '/volumes/' + quote(resource['id'], safe='') + '?force=false', (204,))
            if self.docker.volume(resource) is not None:
                raise Refused('volume_reclaim_unproven')
            return {'outcome': 'purged', 'reclaimedBytes': size}
        if action == 'quarantine' and any(item.get('State') != 'exited' for item in users):
            raise Refused('volume_writer_not_quarantined')
        result = {'outcome': {'inspect': 'present', 'quarantine': 'quarantined', 'restore': 'restored'}[action],
                  'reclaimedBytes': 0}
        if action == 'inspect':
            result['observedBytes'] = self.docker.volume_bytes(resource)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--installation', required=True)
    parser.add_argument('--state-root', required=True)
    parser.add_argument('--docker-host')
    parser.add_argument('--daemon-id')
    parser.add_argument('--catalog')
    args = parser.parse_args()
    try:
        if not UUID.fullmatch(args.installation):
            raise Refused('invalid_installation_identity')
        root = safe_root(args.root)
        state = safe_root(args.state_root)
        lock_fds = []
        try:
            # Fresh per-effect locks survive a parent crash until this command exits.
            # The bridge owns worker.lock, and does not own either lock acquired here.
            for name in ('effect.lock', 'operation.lock'):
                lock_fd = os.open(state / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                lock_fds.append(lock_fd)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = sys.stdin.buffer.read(65537)
            if len(data) > 65536:
                raise Refused('effect_request_too_large')
            request = json.loads(data)
            authority_token = None
            if request.get('operation') != 'enrollment' and request.get('action') not in ('migrate-shared', 'migrate-service'):
                if args.catalog:
                    from lifecycle_native_authority import NativeLifecycleAuthority, install
                    authority_token = install(NativeLifecycleAuthority(args.catalog, request))
                elif os.environ.get('SBARBASE_LIFECYCLE_VERIFIER') != '1':
                    raise Refused('original_lifecycle_authority_required')
            try:
                require_current()
                docker = Docker(args.docker_host, args.daemon_id) if args.docker_host else None
                result = Adapter(root, args.installation, docker, state).apply(request)
                require_current()
                print(json.dumps(result, sort_keys=True))
            finally:
                if authority_token is not None:
                    from lifecycle_native_authority import reset
                    reset(authority_token)
        finally:
            for lock_fd in reversed(lock_fds):
                os.close(lock_fd)
    except Exception as error:
        reason = str(error) if isinstance(error, Refused) else 'resource_effect_unavailable'
        print(json.dumps({'outcome': 'refused', 'reason': reason}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
