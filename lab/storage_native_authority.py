"""Source implementation for an original local Storage authority.

Parsing and private-file protocols are executable without original services.
Docker observation, process control and SQL observation require an installed
launch/publication guard. That integration is pending and this module never
mints native admission. A receipt, callback or caller-supplied JSON cannot
replace the missing installed guard.
"""
import contextlib
import copy
import fcntl
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

from storage_write_settlement import (IMAGE, SOURCE, HEX, UUID, Refused,
    canonical, closed, digest, exact, integer, require, text, validate_manifest,
    Journal, _pairs)


MAX_OUTPUT = 2 * 1024 * 1024
MAX_FILES = 10000
MAX_BYTES = 64 * 1024 * 1024
PENDING = ('installed-launch-publication-authority', 'compiled-image-provenance',
           'direct-database-and-filesystem-writer-fence', 'enabled-effect-barriers')


def native_integer(value, minimum=0):
    require(type(value) is int and minimum <= value <= 9223372036854775807,
            'Invalid native signed integer')
    return value


def json_material(data):
    require(type(data) is bytes and len(data) <= MAX_OUTPUT, 'Observation bound exceeded')
    try:
        return json.loads(data, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('Malformed command observation') from None


def config_digest(inspect):
    """Effective Docker configuration, distinct from image config identity."""
    require(type(inspect) is dict, 'Malformed Docker inspect object')
    require(type(inspect.get('Config')) is dict and type(inspect.get('HostConfig')) is dict and
            type(inspect.get('Mounts')) is list, 'Missing effective configuration')
    return digest({key: inspect[key] for key in ('Config', 'HostConfig', 'Mounts')})


def validate_spec(value):
    closed(value, ('version', 'manifest', 'image_config_id', 'internal_bucket',
                   'version_separator', 'processes', 'protected_containers', 'database_incarnation'))
    require(type(value['version']) is int and value['version'] == 1, 'Unknown native spec version')
    manifest = validate_manifest(value['manifest'])
    require(manifest['backend'] == 'file', 'Remote provider authority remains pending')
    require(manifest['binding']['coverage'] == 'dedicated-resources',
            'Local controller cannot enroll shared or fixture resources')
    text(value['image_config_id'], r'sha256:' + HEX)
    text(value['internal_bucket'], r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}')
    require(value['version_separator'] in ('/', '-$v-'), 'Unknown original version separator')
    processes = value['processes']
    require(type(processes) is dict and set(processes) == {w['container'] for w in manifest['writers']},
            'Process enrollment differs')
    for entries in processes.values():
        require(type(entries) is list and entries, 'Original writer process enrollment is empty')
        seen = set()
        for entry in entries:
            closed(entry, ('pid', 'start_ticks', 'mount_namespace', 'pid_namespace'))
            for key in entry:
                integer(entry[key], 1)
            require(entry['pid'] not in seen, 'Duplicate process enrollment')
            seen.add(entry['pid'])
    protected = value['protected_containers']
    require(type(protected) is dict and not (set(protected) & set(processes)), 'Protected writer overlap')
    require(set(protected.values()) == set(manifest['neighbors']) and
            len(protected) == len(manifest['neighbors']), 'Protected neighbor enrollment differs')
    for cid, fingerprint in protected.items():
        text(cid, HEX)
        text(fingerprint, HEX)
    db = closed(value['database_incarnation'], ('container', 'image_config_id', 'started_at',
                                               'init_process', 'config_sha256'))
    require(db['container'] == manifest['database']['container'] and db['container'] in protected,
            'Original database must be enrolled in protected inventory')
    text(db['image_config_id'], r'sha256:' + HEX)
    text(db['config_sha256'], HEX)
    text(db['started_at'], r'[0-9T:.Z+-]{1,64}')
    closed(db['init_process'], ('pid', 'start_ticks', 'mount_namespace', 'pid_namespace'))
    for item in db['init_process'].values():
        integer(item, 1)
    return copy.deepcopy(value)


def process_identity(pid, stat_bytes, mount_namespace, pid_namespace):
    """Decode Linux /proc stat without splitting a parenthesized process name."""
    integer(pid, 1)
    require(type(stat_bytes) is bytes and len(stat_bytes) <= 16384, 'Invalid process stat')
    try:
        prefix, rest = stat_bytes.decode('ascii').rsplit(') ', 1)
        observed_pid, name = prefix.split(' (', 1)
        fields = rest.split()
        require(int(observed_pid) == pid and len(fields) >= 20 and name, 'Process stat identity differs')
        ticks = int(fields[19])
    except (ValueError, UnicodeError):
        raise Refused('Malformed process stat') from None
    return {'pid': pid, 'start_ticks': integer(ticks, 1),
            'mount_namespace': integer(mount_namespace, 1),
            'pid_namespace': integer(pid_namespace, 1)}


def decode_inventory(spec, material, stopped=False):
    """Validate observations only. A positive parse is never native proof."""
    spec = validate_spec(spec)
    manifest = spec['manifest']
    closed(material, ('daemon', 'namespace', 'generation', 'management_epoch',
                      'operation', 'containers_before', 'containers_after', 'image',
                      'processes', 'volume', 'database_image', 'database_process'))
    for key in ('daemon', 'namespace', 'generation', 'management_epoch', 'operation', 'volume'):
        require(exact(material[key], manifest[key]), 'Local authority binding differs: ' + key)
    before, after = material['containers_before'], material['containers_after']
    require(type(before) is list and type(after) is list and len(before) <= 1000,
            'Incomplete daemon inventory')
    require(exact(before, after), 'Container inventory changed during observation')
    require(type(material['image']) is dict, 'Image association missing')
    image = material['image']
    require(image.get('Id') == spec['image_config_id'] and type(image.get('RepoDigests')) is list,
            'Docker image config association differs')
    require(any(type(entry) is str and entry.endswith('@' + IMAGE) for entry in image['RepoDigests']),
            'Original registry digest association missing')
    require(type(material['processes']) is dict and set(material['processes']) == set(spec['processes']),
            'Current process inventory differs')
    known = {writer['container']: writer for writer in manifest['writers']}
    protected = spec['protected_containers']
    require(len(before) == len(known) + len(protected), 'Unlisted daemon resource owner')
    ids, result = set(), []
    for raw in before:
        require(type(raw) is dict, 'Malformed Docker inspect')
        cid = text(raw.get('Id'), HEX)
        require(cid not in ids, 'Duplicate daemon container')
        ids.add(cid)
        if cid in protected:
            require(digest(raw) == protected[cid], 'Protected neighbor changed')
            continue
        require(cid in known, 'Unlisted writer or helper')
        writer = known[cid]
        require(raw.get('Image') == spec['image_config_id'], 'Writer image config differs')
        require(config_digest(raw) == writer['config_sha256'], 'Effective writer configuration differs')
        state = raw.get('State')
        require(type(state) is dict and type(state.get('Running')) is bool,
                'Incomplete writer state')
        require(state.get('StartedAt') == writer['started_at'], 'Writer incarnation changed')
        require(state.get('Paused') is False and state.get('Restarting') is False and
                state.get('Dead') is False and state.get('OOMKilled') is False,
                'Ambiguous process shutdown state')
        host = raw['HostConfig']
        require(type(host) is dict and host.get('RestartPolicy') == {'Name': 'no', 'MaximumRetryCount': 0},
                'Writer restart policy remains enabled')
        require(host.get('Privileged') is False and host.get('PidMode') == '' and
                host.get('VolumesFrom') in (None, []) and host.get('NetworkMode') != 'host' and
                host.get('CapAdd') in (None, []), 'Global process or inherited volume reach')
        env = raw['Config'].get('Env')
        require(type(env) is list and all(type(item) is str and '=' in item for item in env),
                'Effective writer environment unavailable')
        environment = {}
        for item in env:
            key, value = item.split('=', 1)
            require(key not in environment, 'Duplicate effective environment key')
            environment[key] = value
        require(environment.get('MULTI_TENANT', 'false') == 'false' and
                environment.get('IS_MULTITENANT', 'false') == 'false' and
                environment.get('STORAGE_BACKEND') == 'file', 'Shared or unknown effective backend reach')
        labels = raw['Config'].get('Labels')
        require(type(labels) is dict and labels.get('sbarbase.owner') == writer['owner'] and
                labels.get('sbarbase.runtime') == writer['scope'] and
                labels.get('sbarbase.operation') == manifest['operation'], 'Original owner labels differ')
        mounts = raw['Mounts']
        require(type(mounts) is list and len(mounts) == 1, 'Unlisted writable or external mounts')
        mount = mounts[0]
        require(type(mount) is dict and mount.get('Type') == 'volume' and
                mount.get('Name') == writer['mount_source'] and mount.get('Source') == manifest['volume']['root'] and
                mount.get('Destination') == writer['mount_destination'] and mount.get('RW') is True,
                'Original writer mount identity differs')
        entries = material['processes'][cid]
        require(exact(entries, [] if stopped else spec['processes'][cid]), 'Process exit or start identity differs')
        require(state['Running'] is (not stopped), 'Writer process has not reached required state')
        require(type(state.get('Pid')) is int and (state['Pid'] == 0 if stopped else
                state['Pid'] in [entry['pid'] for entry in entries]), 'Container init PID differs')
        require(state.get('Status') == ('exited' if stopped else 'running'), 'Unknown container state')
        if stopped:
            require(type(state.get('ExitCode')) is int and state['ExitCode'] == 0,
                    'Forced or failed exit needs separate original-state reconciliation')
        result.append({'container': cid, 'processes': copy.deepcopy(entries), 'stopped': stopped})
    require(ids == set(known) | set(protected), 'Complete daemon inventory differs')
    # Docker enumerates CIDs independently of the durable enrollment order.
    # Every observation was checked above before restoring that exact order.
    by_cid = {entry['container']: entry for entry in result}
    result = [by_cid[writer['container']] for writer in manifest['writers']]
    enrolled_db = spec['database_incarnation']
    database = next(raw for raw in before if raw['Id'] == enrolled_db['container'])
    require(database.get('Image') == enrolled_db['image_config_id'] and
            config_digest(database) == enrolled_db['config_sha256'], 'Original database configuration differs')
    db_image = material['database_image']
    require(type(db_image) is dict and db_image.get('Id') == enrolled_db['image_config_id'] and
            type(db_image.get('RepoDigests')) is list and any(type(entry) is str and
            entry.endswith('@' + manifest['database']['image']) for entry in db_image['RepoDigests']),
            'Original database image config and registry pin association differs')
    db_state = database.get('State')
    require(type(db_state) is dict and db_state.get('Running') is True and db_state.get('Status') == 'running' and
            db_state.get('Paused') is False and db_state.get('Restarting') is False and
            db_state.get('Dead') is False and db_state.get('OOMKilled') is False and
            db_state.get('StartedAt') == enrolled_db['started_at'], 'Original database incarnation differs')
    require(type(db_state.get('Pid')) is int and db_state['Pid'] == enrolled_db['init_process']['pid'] and
            exact(material['database_process'], enrolled_db['init_process']), 'Original database process identity differs')
    return {'writers': result, 'database': copy.deepcopy(enrolled_db),
            'native_admitted': False, 'pending': list(PENDING),
            'observation_sha256': digest(material)}


def stopped_observation(spec, value):
    """Validate a detached source observation, never grant native authority."""
    spec = validate_spec(spec)
    closed(value, ('writers', 'database', 'native_admitted', 'pending', 'observation_sha256'))
    require(value['native_admitted'] is False and exact(value['pending'], list(PENDING)),
            'Source observation cannot grant native admission')
    text(value['observation_sha256'], HEX)
    require(exact(value['database'], spec['database_incarnation']), 'Database observation enrollment differs')
    expected = [writer['container'] for writer in spec['manifest']['writers']]
    require(type(value['writers']) is list and len(value['writers']) == len(expected), 'Stopped writer set differs')
    for cid, entry in zip(expected, value['writers']):
        closed(entry, ('container', 'processes', 'stopped'))
        require(entry['container'] == cid and entry['stopped'] is True and exact(entry['processes'], []),
                'Every original writer must be observed stopped before capture')
    return copy.deepcopy(value)


class OwnerLedger:
    """Fsynced private intent ownership, separate from native settlement receipts."""
    def __init__(self, root):
        self.root = Path(root)

    @contextlib.contextmanager
    def locked(self):
        require(self.root.is_absolute(), 'Explicit owner ledger root required')
        fd = os.open('/', os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.root.parts[1:]:
                require(part not in ('', '.', '..'), 'Unsafe owner ledger root')
                child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                    'Owner ledger directory must be private and owned')
            lock = os.open('owner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                Journal._private(lock)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                data = os.open('owner.jsonl', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    Journal._private(data)
                    os.fsync(fd)
                    yield _LockedOwnerLedger(data)
                finally:
                    os.close(data)
            finally:
                os.close(lock)
        except (OSError, ValueError) as error:
            raise Refused('Owner ledger unavailable or already owned') from error
        finally:
            os.close(fd)


class _LockedOwnerLedger:
    def __init__(self, fd):
        self.fd, self.records = fd, []
        size = os.fstat(fd).st_size
        require(size <= MAX_OUTPUT, 'Owner ledger bound exceeded')
        data = os.pread(fd, size, 0)
        require(not data or data.endswith(b'\n'), 'Ambiguous interrupted owner record')
        for line in data.splitlines():
            record = json_material(line)
            self._validate(record)
            self.records.append(record)

    def _validate(self, record):
        closed(record, ('sequence', 'previous', 'spec_sha256', 'operation', 'generation',
                        'management_epoch', 'action', 'phase', 'containers', 'observation_sha256', 'sha256'))
        require(type(record['sequence']) is int and record['sequence'] == len(self.records) + 1,
                'Owner ledger sequence differs')
        previous = self.records[-1] if self.records else None
        require(record['previous'] == (previous['sha256'] if previous else '0' * 64), 'Owner ledger chain differs')
        text(record['operation'], UUID)
        for key in ('spec_sha256', 'observation_sha256', 'sha256'):
            text(record[key], HEX)
        integer(record['generation'], 1)
        integer(record['management_epoch'])
        require(record['action'] == 'stop' and record['phase'] in ('intent', 'ack', 'observed'),
                'Unknown owner effect')
        require(type(record['containers']) is list and record['containers'] and
                all(type(cid) is str and re.fullmatch(HEX, cid) for cid in record['containers']) and
                len(set(record['containers'])) == len(record['containers']), 'Invalid owned container set')
        if previous:
            require(all(exact(record[key], previous[key]) for key in
                        ('spec_sha256', 'operation', 'generation', 'management_epoch', 'action', 'containers')),
                    'Owner effect identity changed')
            require((previous['phase'], record['phase']) in (('intent', 'ack'), ('intent', 'observed'),
                                                             ('ack', 'observed')), 'Owner effect replay forbidden')
        else:
            require(record['phase'] == 'intent', 'Owner effect lacks before-effect intent')
        require(digest({key: value for key, value in record.items() if key != 'sha256'}) == record['sha256'],
                'Owner record checksum differs')

    def append(self, spec, phase, observation_sha256):
        spec = validate_spec(spec)
        manifest = spec['manifest']
        record = {'sequence': len(self.records) + 1,
                  'previous': self.records[-1]['sha256'] if self.records else '0' * 64,
                  'spec_sha256': digest(spec), 'operation': manifest['operation'],
                  'generation': manifest['generation'], 'management_epoch': manifest['management_epoch'],
                  'action': 'stop', 'phase': phase,
                  'containers': [writer['container'] for writer in manifest['writers']],
                  'observation_sha256': observation_sha256}
        record['sha256'] = digest(record)
        self._validate(record)
        encoded = canonical(record) + b'\n'
        require(os.fstat(self.fd).st_size + len(encoded) <= MAX_OUTPUT, 'Owner ledger bound exceeded')
        os.lseek(self.fd, 0, os.SEEK_END)
        require(os.write(self.fd, encoded) == len(encoded), 'Interrupted owner ledger write')
        os.fsync(self.fd)
        self.records.append(record)
        return copy.deepcopy(record)


def _installed_launch_guard(spec):
    validate_spec(spec)
    raise Refused('Installed launch/publication and original writer reach authority pending')


class DockerLocalCommands:
    """Real command source. All execution currently refuses before subprocess."""
    def __init__(self, spec, executable='/usr/bin/docker', socket='/var/run/docker.sock'):
        self.spec = validate_spec(spec)
        require(executable == '/usr/bin/docker' and socket == '/var/run/docker.sock',
                'Unbound executable or Docker daemon socket')
        self.executable, self.socket = executable, socket

    def _run(self, arguments):
        _installed_launch_guard(self.spec)
        require(type(arguments) is tuple and all(type(item) is str for item in arguments), 'Invalid command argv')
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run((self.executable, '--host', 'unix://' + self.socket, *arguments),
                    stdin=subprocess.DEVNULL, stdout=output, stderr=errors, timeout=8,
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}, check=False,
                    preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT)))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise Refused('Original command outcome unavailable; never replay its intent') from error
            require(result.returncode == 0 and output.tell() <= MAX_OUTPUT and errors.tell() <= MAX_OUTPUT,
                    'Original command failed or observation bound exceeded')
            output.seek(0)
            return output.read(MAX_OUTPUT + 1)

    def containers(self):
        ids = self._run(('ps', '--all', '--no-trunc', '--format', '{{.ID}}')).decode('ascii').splitlines()
        require(ids and len(ids) <= 1000 and len(ids) == len(set(ids)), 'Incomplete Docker daemon inventory')
        for cid in ids:
            text(cid, HEX)
        return json_material(self._run(('inspect', *sorted(ids))))

    def image(self):
        return self.image_config(self.spec['image_config_id'])

    def image_config(self, image_config_id):
        text(image_config_id, r'sha256:' + HEX)
        result = json_material(self._run(('image', 'inspect', image_config_id)))
        require(type(result) is list and len(result) == 1, 'Original image association unavailable')
        return result[0]

    def stop(self):
        cids = tuple(writer['container'] for writer in self.spec['manifest']['writers'])
        return self._run(('stop', '--timeout', '5', *cids))

    def material(self, context):
        _installed_launch_guard(self.spec)
        closed(context, ('daemon', 'namespace', 'operation', 'generation', 'management_epoch'))
        context = copy.deepcopy(context)
        before = self.containers()
        info = json_material(self._run(('info', '--format', '{{json .}}')))
        require(type(info) is dict and type(info.get('ID')) is str, 'Docker daemon identity unavailable')
        socket = os.stat(self.socket, follow_symlinks=False)
        require(stat.S_ISSOCK(socket.st_mode), 'Docker daemon endpoint identity differs')
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
        text(boot_id, UUID)
        namespaces = {name: os.stat('/proc/self/ns/' + name).st_ino for name in ('mnt', 'pid')}
        actual_daemon = digest({'id': info['ID'], 'socket_device': socket.st_dev,
                                'socket_inode': socket.st_ino, 'boot': boot_id, 'namespaces': namespaces})
        require(context['daemon'] == actual_daemon and
                context['namespace'] == 'mnt:' + str(namespaces['mnt']) + '/pid:' + str(namespaces['pid']),
                'Installed daemon or host namespace binding differs')
        processes = {}
        selected_states = {item.get('Id'): item.get('State') for item in before if type(item) is dict}
        for cid in self.spec['processes']:
            state = selected_states.get(cid)
            require(type(state) is dict and type(state.get('Running')) is bool, 'Writer state unavailable')
            if state['Running'] is False:
                processes[cid] = []
                continue
            raw = self._run(('top', cid, '-eo', 'pid')).decode('ascii').splitlines()
            require(raw and raw[0].strip() == 'PID', 'Original Docker process inventory unavailable')
            pids = [int(item.strip()) for item in raw[1:] if re.fullmatch(r'\s*[0-9]+\s*', item)]
            require(len(pids) == len(raw) - 1 and len(set(pids)) == len(pids), 'Incomplete process table')
            entries = []
            for pid in sorted(pids):
                proc = Path('/proc/' + str(integer(pid, 1)))
                first = (proc / 'stat').read_bytes()
                mount = os.stat(proc / 'ns/mnt').st_ino
                namespace = os.stat(proc / 'ns/pid').st_ino
                second = (proc / 'stat').read_bytes()
                require(os.stat(proc / 'ns/mnt').st_ino == mount and
                        os.stat(proc / 'ns/pid').st_ino == namespace, 'Process namespace changed during observation')
                one = process_identity(pid, first, mount, namespace)
                require(exact(one, process_identity(pid, second, mount, namespace)), 'Process replaced during observation')
                entries.append(one)
            again = self._run(('top', cid, '-eo', 'pid')).decode('ascii').splitlines()
            require(raw == again, 'Original process set changed during observation')
            processes[cid] = entries
        volume = self.spec['manifest']['volume']
        # Descriptor identity is observed without traversing or rewriting files.
        fd = _open_root(volume['root'])
        try:
            current = os.fstat(fd)
            actual_volume = copy.deepcopy(volume)
            actual_volume.update(device=current.st_dev, inode=current.st_ino, mount_id=_mount_id(fd))
        finally:
            os.close(fd)
        image = self.image()
        enrolled_db = self.spec['database_incarnation']
        database = next((raw for raw in before if raw.get('Id') == enrolled_db['container']), None)
        require(type(database) is dict and type(database.get('State')) is dict and
                type(database['State'].get('Pid')) is int, 'Original database init process unavailable')
        db_pid = integer(database['State']['Pid'], 1)
        proc = Path('/proc/' + str(db_pid))
        first = (proc / 'stat').read_bytes()
        db_mount_ns = os.stat(proc / 'ns/mnt').st_ino
        db_pid_ns = os.stat(proc / 'ns/pid').st_ino
        db_process = process_identity(db_pid, first, db_mount_ns, db_pid_ns)
        require(exact(db_process, process_identity(db_pid, (proc / 'stat').read_bytes(),
                os.stat(proc / 'ns/mnt').st_ino, os.stat(proc / 'ns/pid').st_ino)),
                'Original database init process changed during observation')
        db_image = self.image_config(enrolled_db['image_config_id'])
        after = self.containers()
        return {**context, 'containers_before': before, 'containers_after': after,
                'image': image, 'processes': processes, 'volume': actual_volume,
                'database_image': db_image, 'database_process': db_process}

    def storage_rows(self):
        context = _installed_launch_guard(self.spec)
        before = stopped_observation(self.spec, decode_inventory(self.spec, self.material(context), stopped=True))
        db = self.spec['manifest']['database']
        # The original tenant database is bound by CID, image, database name and
        # OID. SQL results remain observations, never a database admission gate.
        query = """SELECT json_build_object(
          'database_oid',(SELECT oid FROM pg_database WHERE datname=current_database()),
          'sessions',(SELECT coalesce(json_agg(row_to_json(a)), '[]'::json)
            FROM pg_stat_activity a WHERE datname=current_database() AND pid<>pg_backend_pid()),
          'prepared',(SELECT coalesce(json_agg(row_to_json(p)), '[]'::json)
            FROM pg_prepared_xacts p WHERE database=current_database()),
          'objects',(SELECT coalesce(json_agg(row_to_json(o) ORDER BY bucket_id,name,id),'[]'::json)
            FROM storage.objects o));"""
        rows = json_material(self._run(('exec', db['container'], 'psql', '-X', '-A', '-t',
            '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', db['name'], '-c', query)))
        after = stopped_observation(self.spec, decode_inventory(self.spec,
            self.material(_installed_launch_guard(self.spec)), stopped=True))
        require(exact(before, after), 'Original writer/database observation changed across SQL capture')
        closed(rows, ('database_oid', 'sessions', 'prepared', 'objects'))
        require(exact(rows['database_oid'], db['oid']), 'Original database OID differs')
        return rows


def _mount_id(fd):
    data = Path('/proc/self/fdinfo/' + str(fd)).read_text(encoding='ascii')
    matches = re.findall(r'^mnt_id:\s+([0-9]+)$', data, re.MULTILINE)
    require(len(matches) == 1, 'Descriptor mount identity unavailable')
    return integer(int(matches[0]), 1)


def _open_root(root):
    require(type(root) is str and root.startswith('/'), 'Explicit file root required')
    fd = os.open('/', os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(root).parts[1:]:
            require(part not in ('', '.', '..'), 'Unsafe original file root')
            child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def snapshot_files(root, identity, max_bytes=MAX_BYTES):
    """Read exact bytes, native nanosecond mtime and xattrs using directory FDs.

    This parser can inspect private test files. It is not a settled-state proof.
    The installed writer guard is required by the authority before calling it
    against original resources. No metadata, mtime, row or ETag is rewritten.
    """
    require(type(root) is str and root.startswith('/'), 'Explicit file root required')
    closed(identity, ('device', 'inode', 'mount_id'))
    for key in identity:
        integer(identity[key], 1)
    integer(max_bytes, 1)
    require(max_bytes <= MAX_BYTES, 'File observation byte limit exceeds role')
    fd = _open_root(root)
    files, consumed = [], [0]
    try:
        def check_root():
            info = os.fstat(fd)
            require(exact({'device': info.st_dev, 'inode': info.st_ino, 'mount_id': _mount_id(fd)}, identity),
                    'Original root descriptor identity changed')
        check_root()
        def walk(parent, prefix='', depth=0):
            require(depth <= 64, 'File observation nesting exceeds bound')
            parent_before = os.fstat(parent)
            for name in sorted(os.listdir(parent)):
                require(len(files) < MAX_FILES and name not in ('.', '..') and '/' not in name,
                        'File inventory bound or path differs')
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                require(info.st_dev == identity['device'], 'Unlisted filesystem mount')
                opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    require(_mount_id(opened) == identity['mount_id'], 'Unlisted bind mount')
                    actual = os.fstat(opened)
                    require((actual.st_dev, actual.st_ino) == (info.st_dev, info.st_ino), 'File replaced during open')
                    path = prefix + name
                    if stat.S_ISDIR(actual.st_mode):
                        walk(opened, path + '/', depth + 1)
                    else:
                        require(stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1, 'Nonregular or shared original file')
                        consumed[0] += actual.st_size
                        require(consumed[0] <= max_bytes, 'File observation byte bound exceeded')
                        sha = hashlib.sha256()
                        read = 0
                        while True:
                            block = os.read(opened, min(65536, max_bytes - read + 1))
                            if not block:
                                break
                            read += len(block)
                            require(read <= actual.st_size, 'Original file grew during observation')
                            sha.update(block)
                        attrs = {key: os.getxattr(opened, key).hex() for key in sorted(os.listxattr(opened))}
                        require(sum(len(key.encode()) + len(value) for key, value in attrs.items()) <= 65536,
                                'Native metadata observation exceeds bound')
                        after = os.fstat(opened)
                        require(read == actual.st_size and
                            (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_nlink) ==
                            (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns, actual.st_ino, actual.st_nlink),
                            'Original file changed during observation')
                        files.append({'path': path, 'size': read, 'sha256': sha.hexdigest(),
                                      'mtime_ns': actual.st_mtime_ns, 'mode': stat.S_IMODE(actual.st_mode),
                                      'uid': actual.st_uid, 'gid': actual.st_gid,
                                      'xattrs': attrs})
                finally:
                    os.close(opened)
            parent_after = os.fstat(parent)
            require((parent_before.st_mtime_ns, parent_before.st_ctime_ns) ==
                    (parent_after.st_mtime_ns, parent_after.st_ctime_ns), 'Directory changed during inventory')
        walk(fd)
        check_root()
    except OSError as error:
        raise Refused('Original file observation unavailable') from error
    finally:
        os.close(fd)
    return files


def reconcile_material(spec, database, files):
    """Compare original row/version paths without editing original semantics."""
    spec = validate_spec(spec)
    manifest = spec['manifest']
    closed(database, ('database_oid', 'sessions', 'prepared', 'objects'))
    require(exact(database['database_oid'], manifest['database']['oid']), 'Original database OID differs')
    require(exact(database['sessions'], []) and exact(database['prepared'], []), 'Original database writers unresolved')
    require(type(database['objects']) is list and type(files) is list and len(files) <= MAX_FILES,
            'Malformed reconciliation inventories')
    by_path = {}
    for file in files:
        closed(file, ('path', 'size', 'sha256', 'mtime_ns', 'mode', 'uid', 'gid', 'xattrs'))
        require(type(file['path']) is str and file['path'] not in by_path, 'Duplicate original file path')
        integer(file['size'])
        native_integer(file['mtime_ns'])
        integer(file['mode'])
        integer(file['uid'])
        integer(file['gid'])
        text(file['sha256'], HEX)
        require(type(file['xattrs']) is dict and all(type(k) is str and type(v) is str and
                re.fullmatch(r'(?:[a-f0-9]{2})*', v) for k, v in file['xattrs'].items()), 'Invalid native xattrs')
        by_path[file['path']] = file
    reachable, unresolved, names = set(), [], set()
    for row in database['objects']:
        require(type(row) is dict and all(key in row for key in ('id', 'bucket_id', 'name', 'version', 'metadata')),
                'Original row version inventory incomplete')
        require(type(row['bucket_id']) is str and type(row['name']) is str and
                type(row['version']) in (str, type(None)), 'Invalid original object path')
        components = [spec['internal_bucket'], manifest['binding']['runtime'], row['bucket_id'], *row['name'].split('/')]
        require(all(part and part not in ('.', '..') and '/' not in part and '\x00' not in part for part in components),
                'Unsafe original row path')
        require((row['bucket_id'], row['name']) not in names, 'Duplicate original object row')
        names.add((row['bucket_id'], row['name']))
        path = '/'.join(components)
        if row['version']:
            require('/' not in row['version'] and row['version'] not in ('.', '..') and '\x00' not in row['version'],
                    'Unsafe original object version')
            path += spec['version_separator'] + row['version']
        require(path not in reachable, 'Original version paths collide')
        reachable.add(path)
        metadata = row['metadata']
        require(type(metadata) is dict and type(metadata.get('size')) is int, 'Original row size missing')
        if path not in by_path or by_path[path]['size'] != metadata['size']:
            unresolved.append('row-file:' + path)
        if path + '.info' in by_path:
            reachable.add(path + '.info')
    unresolved.extend('unclassified-file:' + path for path in sorted(set(by_path) - reachable))
    # Queue, Redis, TUS and multipart state needs actual original barriers and
    # schema-specific observations. No empty supplied map is a substitute.
    unresolved.extend('pending-effect-barrier:' + name for name, enabled in manifest['features'].items()
                      if enabled and name in ('tus', 'multipart', 'queue', 'redis', 'remote_s3'))
    return {'manifest_sha256': digest(manifest), 'rows_sha256': digest(database['objects']),
            'files_sha256': digest(files), 'metadata_sha256': digest([
                {key: file[key] for key in ('path', 'mtime_ns', 'mode', 'uid', 'gid', 'xattrs')} for file in files]),
            'effects_sha256': digest({'pending': unresolved}), 'unresolved': unresolved,
            'native_admitted': False}


class LocalNativeAuthority:
    """Original commands and durable effect protocol; installed authority pending."""
    def __init__(self, spec, ledger_root):
        self.spec = validate_spec(spec)
        self.ledger = OwnerLedger(ledger_root)
        self.commands = DockerLocalCommands(self.spec)

    def observe(self):
        context = _installed_launch_guard(self.spec)
        material = self.commands.material(context)
        states = material['containers_before']
        selected = {writer['container'] for writer in self.spec['manifest']['writers']}
        stopped = all(item['State']['Running'] is False for item in states if item.get('Id') in selected)
        return decode_inventory(self.spec, material, stopped=stopped)

    def stop(self, fault_boundary=None, _fault_notice_fd=None):
        _installed_launch_guard(self.spec)
        require(fault_boundary in (None, 'after-intent', 'after-stop-before-ack'),
                'Unknown owning controller interruption boundary')
        with self.ledger.locked() as ledger:
            require(not ledger.records, 'Stop intent already exists; observe instead of retrying')
            current = self.observe()
            if fault_boundary:
                require(all(not row['stopped'] for row in current['writers']),
                        'Fault injection requires actual live original writers')
            ledger.append(self.spec, 'intent', current['observation_sha256'])
            if fault_boundary == 'after-intent':
                if _fault_notice_fd is not None:
                    os.write(_fault_notice_fd, b'after-intent')
                os.kill(os.getpid(), signal.SIGKILL)
                raise Refused('Owning controller interruption did not occur')
            self.commands.stop()
            if fault_boundary == 'after-stop-before-ack':
                if _fault_notice_fd is not None:
                    os.write(_fault_notice_fd, b'after-stop-before-ack')
                os.kill(os.getpid(), signal.SIGKILL)
                raise Refused('Owning controller interruption did not occur')
            ledger.append(self.spec, 'ack', current['observation_sha256'])
            ended = self.observe()
            require(all(entry['stopped'] for entry in ended['writers']), 'Original writer exit unobserved')
            ledger.append(self.spec, 'observed', ended['observation_sha256'])
            return {'native_admitted': False, 'pending': list(PENDING)}

    def run_stop_fault(self, boundary):
        """Kill the actual owned controller at a precise durable stop boundary.

        The parent reaps only its own fork. It never repeats an ambiguous stop.
        Neither the signal nor this result proves original writer settlement.
        """
        _installed_launch_guard(self.spec)
        require(boundary in ('after-intent', 'after-stop-before-ack'), 'Unknown stop fault')
        read_fd, write_fd = os.pipe()
        try:
            child = os.fork()
        except BaseException:
            os.close(read_fd); os.close(write_fd)
            raise
        if child == 0:
            try:
                os.close(read_fd)
                os.setsid()
                self.stop(fault_boundary=boundary, _fault_notice_fd=write_fd)
            except BaseException:
                os._exit(2)
            os._exit(3)
        os.close(write_fd)
        deadline = time.monotonic() + 30
        reaped = False
        try:
            while time.monotonic() < deadline:
                pid, status = os.waitpid(child, os.WNOHANG)
                if pid == child:
                    reaped = True
                    require(os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL,
                            'Actual controller did not reach the requested interruption')
                    require(os.read(read_fd, 64) == boundary.encode(),
                            'Actual controller interruption boundary was not reached')
                    with self.ledger.locked() as ledger:
                        require(len(ledger.records) == 1 and ledger.records[0]['phase'] == 'intent' and
                                ledger.records[0]['spec_sha256'] == digest(self.spec),
                                'Interrupted controller lacks exact durable owned intent')
                    return {'boundary': boundary, 'controller_signal': signal.SIGKILL,
                            'native_admitted': False, 'pending': list(PENDING)}
                time.sleep(.02)
            raise Refused('Owning controller interruption deadline exceeded')
        finally:
            os.close(read_fd)
            if not reaped:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)

    def recover_stop(self):
        _installed_launch_guard(self.spec)
        with self.ledger.locked() as ledger:
            require(ledger.records and ledger.records[-1]['phase'] in ('intent', 'ack'),
                    'No ambiguous owned stop intent to observe')
            ended = self.observe()
            require(all(entry['stopped'] for entry in ended['writers']), 'Stop remains unresolved; no replay allowed')
            ledger.append(self.spec, 'observed', ended['observation_sha256'])
            return {'native_admitted': False, 'pending': list(PENDING)}

    def reconcile(self):
        _installed_launch_guard(self.spec)
        with self.ledger.locked() as ledger:
            require(ledger.records and ledger.records[-1]['phase'] == 'observed',
                    'Original stopped writers lack a completed owned stop record')
            owned = ledger.records[-1]
            require(owned['spec_sha256'] == digest(self.spec), 'Observed stop belongs to another source operation')
            before = stopped_observation(self.spec, self.observe())
            require(before['observation_sha256'] == owned['observation_sha256'],
                    'Current observation differs from the owned observed stop')
            volume = self.spec['manifest']['volume']
            files = snapshot_files(volume['root'], {key: volume[key] for key in ('device', 'inode', 'mount_id')})
            result = reconcile_material(self.spec, self.commands.storage_rows(), files)
            after = stopped_observation(self.spec, self.observe())
            require(exact(before, after), 'Original writer/database observation changed across file/SQL capture')
            require(result['unresolved'] == [], 'Original native reconciliation remains unresolved')
            return result


def s3_query(pairs):
    """AWS encoding and ordering, with duplicate query keys retained."""
    require(type(pairs) in (list, tuple) and len(pairs) <= 32, 'Invalid original S3 query')
    encoded = []
    for pair in pairs:
        require(type(pair) in (list, tuple) and len(pair) == 2 and
                all(type(value) is str and len(value) <= 4096 for value in pair), 'Invalid S3 query pair')
        encoded.append(tuple(quote(value, safe='-_.~') for value in pair))
    return '&'.join(key + '=' + value for key, value in sorted(encoded))


def sign_s3_request(method, path, pairs, headers, body, access_key, secret_key, region, stamp):
    """Pure SigV4, checked against AWS's public synthetic S3 GET vector.

    This return value contains private authorization. It must never be retained
    as an evidence artifact or included in an exception or process argument.
    """
    require(method in ('GET', 'PUT', 'POST', 'DELETE', 'HEAD') and type(body) is bytes and
            len(body) <= 8 * 1024 * 1024, 'Invalid bounded original S3 payload')
    text(path, r'/[A-Za-z0-9/_.~%\-]{1,4095}')
    require(type(headers) is dict and 'host' in headers and len(headers) <= 16, 'Missing signed host')
    for key, value in headers.items():
        text(key, r'[a-z0-9-]{1,64}')
        require(type(value) is str and len(value) <= 4096 and value.isascii() and
                all(32 <= ord(char) < 127 for char in value), 'Unsafe signed header')
    for value in (access_key, secret_key):
        require(type(value) is str and 1 <= len(value) <= 2048 and value.isascii() and
                all(33 <= ord(char) < 127 for char in value), 'Invalid private signing input')
    text(access_key, r'[A-Za-z0-9_-]{1,256}')
    text(region, r'[A-Za-z0-9_-]{1,64}')
    text(stamp, r'[0-9]{8}T[0-9]{6}Z')
    require(not (set(headers) & {'authorization', 'x-amz-date', 'x-amz-content-sha256'}),
            'Signing headers cannot be overridden')
    signed = {name: ' '.join(value.split()) for name, value in headers.items()}
    payload_hash = hashlib.sha256(body).hexdigest()
    signed.update({'x-amz-date': stamp, 'x-amz-content-sha256': payload_hash})
    names = ';'.join(sorted(signed))
    canonical_headers = ''.join(name + ':' + signed[name] + '\n' for name in sorted(signed))
    request = '\n'.join((method, path, s3_query(pairs), canonical_headers, names, payload_hash))
    scope = stamp[:8] + '/' + region + '/s3/aws4_request'
    message = '\n'.join(('AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(request.encode()).hexdigest()))
    key = ('AWS4' + secret_key).encode()
    for value in (stamp[:8], region, 's3', 'aws4_request'):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    signed['authorization'] = ('AWS4-HMAC-SHA256 Credential=' + access_key + '/' + scope +
                               ',SignedHeaders=' + names + ',Signature=' + signature)
    return signed


def s3_xml(data, expected_root):
    require(type(data) is bytes and len(data) <= MAX_OUTPUT and
            b'<!' not in data and b'\x00' not in data, 'Invalid bounded original S3 XML')
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError):
        raise Refused('Malformed original S3 response') from None
    prefix = '{http://s3.amazonaws.com/doc/2006-03-01/}'
    require(root.tag in (expected_root, prefix + expected_root), 'Original S3 root differs')
    for element in root.iter():
        require(not element.attrib and type(element.tag) is str and
                (not element.tag.startswith('{') or element.tag.startswith(prefix)),
                'Unexpected original S3 XML namespace or attribute')
        if element.tag.startswith(prefix):
            element.tag = element.tag[len(prefix):]
    return root


def _xml_one(root, name):
    values = root.findall(name)
    require(len(values) == 1 and len(values[0]) == 0 and values[0].text is not None,
            'Missing or duplicate original S3 field')
    return values[0].text


def verify_s3_parts(data, bucket, key, upload_id, expected):
    root = s3_xml(data, 'ListPartsResult')
    require(_xml_one(root, 'Bucket') == bucket and _xml_one(root, 'Key') == key and
            _xml_one(root, 'UploadId') == upload_id and _xml_one(root, 'IsTruncated') == 'false',
            'Original multipart listing is incomplete or belongs elsewhere')
    actual = []
    for part in root.findall('Part'):
        number = _xml_one(part, 'PartNumber')
        text(number, r'[1-9][0-9]{0,3}')
        actual.append({'number': int(number), 'etag': _xml_one(part, 'ETag')})
    require(exact(actual, expected), 'Original multipart part inventory differs')
    return {'parts_sha256': digest(actual), 'native_admitted': False}


def read_protocol_credentials(path):
    """Read a private credential file via descriptors, without retaining bytes."""
    require(type(path) is str and Path(path).is_absolute(), 'Explicit private credential path required')
    fd = _open_root(str(Path(path).parent))
    file_fd = None
    try:
        file_fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(file_fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size <= 8192,
                'Original protocol credential file must be private and owned')
        result = json_material(os.read(file_fd, 8193))
        closed(result, ('service_key', 'access_key', 'secret_key', 'region'))
        for value in result.values():
            require(type(value) is str and 1 <= len(value) <= 2048 and value.isascii() and
                    all(33 <= ord(char) < 127 for char in value), 'Invalid private credential format')
        return result
    except (OSError, ValueError):
        raise Refused('Private protocol credentials unavailable') from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(fd)


@contextlib.contextmanager
def _protocol_deadline():
    """Wall deadline for a later privately owned main-thread native probe."""
    require(threading.current_thread() is threading.main_thread() and
            signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
            'Protocol probe requires an owned main thread with no active timer')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise Refused('Original protocol wall deadline exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 45)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class OriginalHttpTransport:
    """Concrete bounded original HTTP source, no installed admission yet.

    Assignment/ingress binding and request intents need the parent's installed
    integration. The existing guard refuses before credential or socket access.
    Redirects are never followed. Supplied endpoints or credentials are not
    evidence that this transport owns an original writer or bucket.
    """
    def __init__(self, spec, endpoint, tenant_host, credentials_path):
        self.spec = validate_spec(spec)
        require(type(endpoint) is str, 'Original endpoint required')
        root = urlsplit(endpoint)
        require(root.scheme == 'http' and root.hostname in ('127.0.0.1', '::1') and
                root.port is not None and 1 <= root.port <= 65535 and
                root.path in ('', '/') and not root.query and not root.fragment and
                not root.username and not root.password, 'Unbound original HTTP endpoint')
        text(tenant_host, r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}')
        require(type(credentials_path) is str and Path(credentials_path).is_absolute(),
                'Private credential file path required')
        self.host, self.port = root.hostname, root.port
        self.tenant_host, self.credentials_path = tenant_host, credentials_path
        self.host_header = ('[' + self.host + ']' if ':' in self.host else self.host) + ':' + str(self.port)

    @staticmethod
    def _path(bucket, key):
        text(bucket, r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]')
        require(type(key) is str and 1 <= len(key) <= 512 and
                all(part and part not in ('.', '..') for part in key.split('/')), 'Unsafe original object key')
        return quote(bucket, safe='-_.~') + '/' + quote(key, safe='/-_.~')

    def s3(self, method, bucket, key, pairs, body, max_response=MAX_OUTPUT,
           content_type='application/octet-stream'):
        _installed_launch_guard(self.spec)
        path = '/s3/' + self._path(bucket, key)
        credentials = read_protocol_credentials(self.credentials_path)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        headers = sign_s3_request(method, path, pairs, {
            'host': self.host_header, 'x-forwarded-host': self.tenant_host,
            'x-forwarded-port': str(self.port), 'content-type': content_type},
            body, credentials['access_key'], credentials['secret_key'], credentials['region'], stamp)
        require(type(max_response) is int and 0 <= max_response <= MAX_BYTES, 'Invalid response bound')
        connection = http.client.HTTPConnection(self.host, self.port, timeout=45)
        deadline = time.monotonic() + 45
        try:
            with _protocol_deadline():
                connection.request(method, path + ('?' + s3_query(pairs) if pairs else ''), body=body, headers=headers)
                response = connection.getresponse()
                chunks, size = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'Original S3 response deadline exceeded')
                    if connection.sock:
                        connection.sock.settimeout(remaining)
                    chunk = response.read1(min(65536, max_response + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk); size += len(chunk)
                    require(size <= max_response, 'Original S3 response bound exceeded')
                data = b''.join(chunks)
                require(200 <= response.status < 300, 'Original S3 operation failed')
                return data, response.getheader('etag')
        except (OSError, http.client.HTTPException):
            raise Refused('Original S3 transport outcome unavailable; no automatic retry') from None
        finally:
            connection.close()

    def disconnect(self, bucket, key):
        """RST after a partial original body, never a request abort substitute.

        sendall proves local socket delivery only. A later actual server/file/row
        barrier must corroborate server consumption and residual effects.
        """
        _installed_launch_guard(self.spec)
        path = '/object/' + self._path(bucket, key)
        credentials = read_protocol_credentials(self.credentials_path)
        payload = b'\x39' * 65536
        headers = ('POST ' + path + ' HTTP/1.1\r\nHost: ' + self.host_header +
                   '\r\nX-Forwarded-Host: ' + self.tenant_host + '\r\nX-Forwarded-Port: ' + str(self.port) +
                   '\r\nAuthorization: Bearer ' + credentials['service_key'] +
                   '\r\nContent-Type: application/octet-stream\r\nContent-Length: 8388608' +
                   '\r\nX-Upsert: false\r\nConnection: close\r\n\r\n').encode('ascii')
        with _protocol_deadline():
            connection = None
            try:
                connection = socket.create_connection((self.host, self.port), timeout=45)
                connection.sendall(headers + payload)
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            except OSError:
                raise Refused('Original partial-body delivery unavailable') from None
            finally:
                if connection is not None:
                    connection.close()
        return {'sent_bytes': len(payload), 'declared_bytes': 8388608,
                'outcome': 'local-partial-body-sent', 'native_admitted': False,
                'pending': ['original-server-body-consumption', 'actual-row-file-version-effects']}


def exercise_multipart(transport, bucket, key):
    """Original create/upload/list/complete/download protocol source.

    It never claims the full multipart native case. Original database parts,
    versioned backend files, asynchronous cleanup and all writers need separate
    actual observations. Failed requests retain their ambiguity; no retry or
    inferred cleanup is issued here.
    """
    require(type(transport) is OriginalHttpTransport, 'Concrete original transport required')
    created, _ = transport.s3('POST', bucket, key, [('uploads', '')], b'')
    root = s3_xml(created, 'InitiateMultipartUploadResult')
    require(_xml_one(root, 'Bucket') == bucket and _xml_one(root, 'Key') == key,
            'Original multipart creation belongs elsewhere')
    upload_id = _xml_one(root, 'UploadId')
    text(upload_id, r'[A-Za-z0-9_+/=.-]{1,2048}')
    first, last = b'\x25' * (5 * 1024 * 1024), b'\x5b' * 65536
    expected = []
    for number, payload in enumerate((first, last), 1):
        _, etag = transport.s3('PUT', bucket, key,
            [('uploadId', upload_id), ('partNumber', str(number))], payload)
        text(etag, r'"?[a-fA-F0-9]{32}"?')
        require(etag.strip('"').lower() == hashlib.md5(payload, usedforsecurity=False).hexdigest(),
                'Original multipart uploaded part checksum differs')
        expected.append({'number': number, 'etag': etag})
    listing, _ = transport.s3('GET', bucket, key, [('uploadId', upload_id)], b'')
    parts = verify_s3_parts(listing, bucket, key, upload_id, expected)
    complete = ElementTree.Element('CompleteMultipartUpload')
    for part in expected:
        element = ElementTree.SubElement(complete, 'Part')
        ElementTree.SubElement(element, 'PartNumber').text = str(part['number'])
        ElementTree.SubElement(element, 'ETag').text = part['etag']
    completed, _ = transport.s3('POST', bucket, key, [('uploadId', upload_id)],
                               ElementTree.tostring(complete), content_type='application/xml')
    root = s3_xml(completed, 'CompleteMultipartUploadResult')
    require(_xml_one(root, 'Bucket') == bucket and _xml_one(root, 'Key') == key,
            'Original multipart completion belongs elsewhere')
    downloaded, _ = transport.s3('GET', bucket, key, [], b'', max_response=len(first) + len(last))
    require(downloaded == first + last, 'Original multipart assembled bytes differ')
    return {'outcome': 'original-multipart-protocol-observed', 'parts_sha256': parts['parts_sha256'],
            'data_sha256': hashlib.sha256(downloaded).hexdigest(), 'native_admitted': False,
            'pending': ['original-database-parts-and-versions', 'actual-versioned-files',
                        'original-async-cleanup', 'all-original-writer-settlement']}
