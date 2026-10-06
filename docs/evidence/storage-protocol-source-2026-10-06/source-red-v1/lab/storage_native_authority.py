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
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile

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

    def stop(self):
        _installed_launch_guard(self.spec)
        with self.ledger.locked() as ledger:
            require(not ledger.records, 'Stop intent already exists; observe instead of retrying')
            current = self.observe()
            ledger.append(self.spec, 'intent', current['observation_sha256'])
            self.commands.stop()
            ledger.append(self.spec, 'ack', current['observation_sha256'])
            ended = self.observe()
            require(all(entry['stopped'] for entry in ended['writers']), 'Original writer exit unobserved')
            ledger.append(self.spec, 'observed', ended['observation_sha256'])
            return {'native_admitted': False, 'pending': list(PENDING)}

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
