"""Fail-closed factory startup preparation contract and bounded executor.

An admitted profile and native receipts are supplied by the separate bounded
executor. A declaration never establishes their authority. Pure contracts never create resources or read private material. The explicit
preparation executor requires an independently admitted public profile and
creates fresh resources; it never starts the candidate or accepts startup.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re


from native_startup_contract import StartupRefusal, require, identifier, CONTAINER_PROJECTION


def image_id(value):
    return type(value) is str and re.fullmatch(r'sha256:[a-f0-9]{64}', value) is not None


def absolute(value):
    return (type(value) is str and 1 < len(value) <= 1024 and value.startswith('/')
            and value.isascii() and all(32 <= ord(c) <= 126 for c in value)
            and all(part not in ('', '.', '..') for part in value[1:].split('/')))


def pinned_factory(lock):
    require(type(lock) is dict and set(lock) == {'tag', 'id', 'digests'}, 'LOCK_REFUSED')
    require(image_id(lock['id']) and type(lock['digests']) is list, 'LOCK_REFUSED')
    require(type(lock['tag']) is str and len(lock['tag']) <= 256, 'LOCK_REFUSED')
    references = lock['digests']
    require(len(references) == 1 and type(references[0]) is str
            and re.fullmatch(r'[a-z0-9][a-z0-9./:_-]{0,255}@sha256:[a-f0-9]{64}', references[0])
            and references[0].endswith('@' + lock['id']), 'LOCK_REFUSED')
    return references[0], lock['id']


@dataclass(frozen=True)
class NativeProfile:
    """Input contract for an independently admitted source-bound image profile.

    Constructing this object is not image/provider admission. The standalone
    CLI requires an explicit independently reviewed public source packet.
    """
    image: str
    reference: str
    verifier_image: str
    uid: int
    gid: int
    custom_directory: str
    credential_directory: str
    data_directory: str
    socket_directory: str
    config_file: str
    provider_file: str
    provider_key: str
    entrypoint: tuple
    command: tuple
    disabled_health: dict
    docker_socket_source: str = '/var/run/docker.sock'

    def validate(self, lock):
        reference, expected = pinned_factory(lock)
        require(self.image == expected and self.reference == reference, 'PROFILE_IMAGE_DIFFERS')
        require(image_id(self.verifier_image), 'VERIFIER_ID_REFUSED')
        require(type(self.uid) is int and type(self.gid) is int and 0 < self.uid <= 4294967295 and 0 < self.gid <= 4294967295,
                'NATIVE_IDENTITY_REFUSED')
        require(absolute(self.docker_socket_source), 'DOCKER_SOCKET_PATH_REFUSED')
        require(not any(path == '/sbarbase-public-control' or path.startswith('/sbarbase-public-control/') or '/sbarbase-public-control'.startswith(path + '/')
                for path in (self.custom_directory, self.credential_directory, self.data_directory, self.socket_directory)), 'CONTROL_MOUNT_OVERLAP')
        paths = (self.custom_directory, self.credential_directory, self.data_directory,
                 self.socket_directory, self.config_file, self.provider_file, self.provider_key)
        require(all(absolute(path) for path in paths), 'PROFILE_PATH_REFUSED')
        directories = paths[:4]
        require(len(set(directories)) == 4 and not any(
            a.startswith(b + '/') for a in directories for b in directories if a != b), 'MOUNT_OVERLAP')
        require(self.provider_key == self.custom_directory + '/pgsodium_root.key', 'PROVIDER_KEY_DIFFERS')
        require(type(self.entrypoint) is tuple and self.entrypoint
                and type(self.command) is tuple and self.command
                and all(type(arg) is str and 0 < len(arg) <= 1024 and '\x00' not in arg
                        for arg in self.entrypoint + self.command), 'SOURCE_COMMAND_REFUSED')
        require(type(self.disabled_health) is dict and self.disabled_health.get('Test') == ['NONE']
                and set(self.disabled_health) <= {'Test', 'Interval', 'Timeout', 'StartPeriod', 'StartInterval', 'Retries'}
                and all(type(value) is int and value >= 0 for key, value in self.disabled_health.items()
                        if key != 'Test'), 'DISABLED_HEALTH_REFUSED')
        return self


def witness(value):
    names = {'device', 'inode', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns', 'ctime_ns'}
    require(type(value) is dict and set(value) == names
            and all(type(number) is int and 0 <= number < 2**64 for number in value.values()), 'WITNESS_REFUSED')
    require(value['mode'] == 0o600 and value['nlink'] == 1 and value['size'] == 64,
            'WITNESS_POLICY_REFUSED')
    return dict(value)


def guard_frame(stdout, stderr, native_code, kind, expected):
    """A zero exit and fixed frame never override stderr, malformed or stale data."""
    require(native_code == 0 and type(native_code) is int and stderr == b'', 'GUARD_NATIVE_REFUSED')
    require(type(stdout) is bytes and 0 < len(stdout) <= 2048 and stdout.endswith(b'\n')
            and stdout.count(b'\n') == 1, 'GUARD_FRAME_REFUSED')
    try:
        def unique(pairs):
            values = {}
            for key, value in pairs:
                require(key not in values, 'GUARD_DUPLICATE_FIELD')
                values[key] = value
            return values
        frame = json.loads(stdout.decode('ascii'), object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise StartupRefusal('GUARD_FRAME_REFUSED') from None
    require(type(frame) is dict and set(frame) == {'status', 'kind', 'operation', 'metadata'}
            and frame['status'] == 'MATERIAL_VERIFIED' and frame['kind'] == kind
            and frame['operation'] == 'verify', 'GUARD_FRAME_REFUSED')
    require(witness(frame['metadata']) == witness(expected), 'GUARD_WITNESS_DIFFERS')
    return frame


class CreationLineage:
    """A fresh invocation records attempt before execution, never adopts a name."""
    def __init__(self, name):
        require(type(name) is str and re.fullmatch(r'factory-[a-f0-9]{32}-[a-z-]{1,32}', name), 'NAME_REFUSED')
        self.name = name
        self.attempted = False
        self.absent_confirmed = False
        self.cid = None
        self.uncertain = False

    def precreate_absent(self, cid_stdout, cid_stderr, code):
        require(not self.attempted and self.cid is None, 'PRECREATE_ORDER_REFUSED')
        # CID-only exact-name enumeration must be empty with quiet success.
        # No arbitrary daemon absence error grants creation authority.
        require(cid_stdout == b'' and cid_stderr == b'' and type(code) is int and code == 0, 'PRECREATE_ABSENCE_REFUSED')
        self.absent_confirmed = True

    def begin(self):
        require(self.absent_confirmed and not self.attempted and self.cid is None, 'CREATE_ALREADY_ATTEMPTED')
        self.attempted = True
        self.uncertain = True

    def created(self, stdout, stderr, code):
        require(self.attempted and self.cid is None and self.uncertain, 'CREATE_LINEAGE_REFUSED')
        require(code == 0 and type(code) is int and stderr == b'' and type(stdout) is bytes
                and re.fullmatch(b'[a-f0-9]{64}\n', stdout), 'CREATE_RESULT_REFUSED')
        self.cid = stdout[:-1].decode('ascii')
        self.uncertain = False
        return self.cid

    def cleanup_identity(self):
        require(self.attempted and self.cid is not None and not self.uncertain, 'CLEANUP_LINEAGE_REFUSED')
        return self.cid


@dataclass(frozen=True)
class MaterialHandoff:
    run: str
    key_volume: str
    credential_volume: str
    data_volume: str
    runtime_volume: str
    key_witness: dict
    password_witness: dict
    removed_guard_cids: tuple

    @property
    def owner(self):
        return 'sbarbase-fixture-factory-' + self.run

    @property
    def candidate_name(self):
        return 'factory-' + self.run + '-candidate'

    def validate(self, profile):
        require(type(self.run) is str and re.fullmatch(r'[a-f0-9]{32}', self.run), 'RUN_REFUSED')
        expected = tuple('factory-' + self.run + '-' + suffix for suffix in ('custom', 'credential', 'pgdata', 'runtime'))
        require((self.key_volume, self.credential_volume, self.data_volume, self.runtime_volume) == expected,
                'VOLUME_NAMESPACE_REFUSED')
        for value in (witness(self.key_witness), witness(self.password_witness)):
            require(value['uid'] == profile.uid and value['gid'] == profile.gid, 'MATERIAL_OWNER_REFUSED')
        require(type(self.removed_guard_cids) is tuple and len(self.removed_guard_cids) == 2
                and len(set(self.removed_guard_cids)) == 2 and all(identifier(cid) for cid in self.removed_guard_cids),
                'GUARD_CLEANUP_REFUSED')
        return self


def admit_volume_projection(item, name, owner, consumers):
    require(type(item) is dict and set(item) == {'Name', 'Driver', 'Options', 'Labels'}
            and item['Name'] == name and item['Driver'] == 'local' and item['Options'] is None
            and item['Labels'] == {'io.sbarbase.owner': owner} and type(consumers) is list and consumers == [], 'VOLUME_ADMISSION_REFUSED')


def mounts(profile, handoff):
    return [
        {'Name': handoff.key_volume, 'Destination': profile.custom_directory, 'RW': False},
        {'Name': handoff.credential_volume, 'Destination': profile.credential_directory, 'RW': False},
        {'Name': handoff.data_volume, 'Destination': profile.data_directory, 'RW': True},
        {'Name': handoff.runtime_volume, 'Destination': profile.socket_directory, 'RW': True},
    ]


def create_arguments(profile, handoff, lock):
    """Construct public args after externally renewed profile/material admission."""
    profile.validate(lock)
    handoff.validate(profile)
    args = ['docker', 'create', '--pull=never', '--name', handoff.candidate_name,
            '--label', 'io.sbarbase.owner=' + handoff.owner, '--user', f'{profile.uid}:{profile.gid}',
            '--network', 'none', '--read-only', '--no-healthcheck', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', '512m', '--memory-swap', '512m',
            '--cpus', '1', '--pids-limit', '128', '--shm-size', '64m', '--log-driver', 'none',
            '--env', 'POSTGRES_PASSWORD_FILE=' + profile.credential_directory + '/password',
            '--env', 'POSTGRES_HOST=' + profile.socket_directory, '--env', 'POSTGRES_DB=postgres']
    for item in mounts(profile, handoff):
        mount = 'type=volume,source=' + item['Name'] + ',target=' + item['Destination'] + ',volume-nocopy'
        if not item['RW']:
            mount += ',readonly'
        args += ['--mount', mount]
    # Keep the admitted image entrypoint and source-bound vendor command.
    return args + [profile.reference, *profile.command]


def admit_created_candidate(item, profile, handoff, lineage):
    """Re-use immediately before removal; running/changed state refuses cleanup."""
    cid = lineage.cleanup_identity()
    require(lineage.name == handoff.candidate_name, 'CANDIDATE_LINEAGE_DIFFERS')
    fields = {'Id', 'Name', 'Image', 'State', 'User', 'Entrypoint', 'Cmd', 'Healthcheck',
              'Labels', 'Mounts', 'HostConfig', 'Networks'}
    require(type(item) is dict and set(item) == fields, 'CANDIDATE_PROJECTION_REFUSED')
    require(item['Id'] == cid and item['Name'] == '/' + handoff.candidate_name and item['Image'] == profile.image,
            'CANDIDATE_IDENTITY_DIFFERS')
    require(item['State'] == {'Status': 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0},
            'CANDIDATE_STATE_DIFFERS')
    require(item['User'] == f'{profile.uid}:{profile.gid}' and item['Entrypoint'] == list(profile.entrypoint)
            and item['Cmd'] == list(profile.command) and item['Healthcheck'] == profile.disabled_health
            and item['Labels'] == {'io.sbarbase.owner': handoff.owner}, 'CANDIDATE_SOURCE_DIFFERS')
    expected = mounts(profile, handoff)
    require(type(item['Mounts']) is list and len(item['Mounts']) == 4, 'CANDIDATE_MOUNTS_DIFFERS')
    actual = []
    for mount in item['Mounts']:
        require(type(mount) is dict and set(mount) == {'Type', 'Name', 'Destination', 'RW', 'Propagation'}
                and mount['Type'] == 'volume' and mount['Propagation'] == '', 'CANDIDATE_MOUNTS_DIFFERS')
        actual.append({key: mount[key] for key in ('Name', 'Destination', 'RW')})
    require(sorted(actual, key=lambda row: row['Name']) == sorted(expected, key=lambda row: row['Name']),
            'CANDIDATE_MOUNTS_DIFFERS')
    require(item['HostConfig'] == {'ReadonlyRootfs': True, 'NetworkMode': 'none', 'PidMode': '', 'IpcMode': 'private',
            'Privileged': False, 'CapAdd': None, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
            'Memory': 536870912, 'MemorySwap': 536870912, 'NanoCpus': 1000000000, 'PidsLimit': 128,
            'ShmSize': 67108864, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0},
            'PortBindings': {}, 'Binds': None, 'Devices': [], 'LogConfig': {'Type': 'none', 'Config': {}}},
            'CANDIDATE_RESOURCES_DIFFERS')
    require(item['Networks'] == ['none'], 'CANDIDATE_NETWORK_DIFFERS')
    return cid


def preparation_receipt(*, profile, handoff, candidate_cid, created_admitted, candidate_absent,
                        copyup_admitted, guards_admitted, volumes_renewed):
    """Called only after the executor's actual renewed source-bound evidence."""
    require(identifier(candidate_cid), 'CANDIDATE_ID_REFUSED')
    require(all(type(value) is bool and value for value in
                (created_admitted, candidate_absent, copyup_admitted, guards_admitted, volumes_renewed)),
            'PREPARATION_INCOMPLETE')
    return {'status': 'STARTUP_MATERIAL_HANDOFF_ONLY', 'startup': 'UNRUN',
            'factory_image': profile.image, 'verifier_image': profile.verifier_image,
            'run': handoff.run, 'candidate_cid': candidate_cid, 'candidate_removed': True,
            'materials_retained': True, 'production_accepted': False}


# Native executor is intentionally separate from the pure contracts above.
# The CLI entrypoint is defined below after these implementation definitions.
class BoundedDocker:
    """Finite public-only command boundary, excluding Env, logs and private bytes."""
    def __init__(self, timeout=240, output_limit=1048576):
        import time
        from native_startup_guardian import establish_subreaper
        establish_subreaper()
        self.deadline = time.monotonic() + timeout
        self.work_deadline = self.deadline - 60
        self.output_limit = output_limit
        self.total = 0
        self.commands = []

    def call(self, args, *, cleanup=False, cap=None):
        import os
        import selectors
        import subprocess
        import time
        require(type(args) is list and args and args[0] == 'docker'
                and all(type(arg) is str and len(arg) <= 131072 and '\x00' not in arg for arg in args)
                and sum(len(arg) + 1 for arg in args) <= 262144, 'COMMAND_REFUSED')
        budget = (10 if cleanup else 20) if cap is None else cap
        require(type(budget) in (int, float) and 0 < budget <= (10 if cleanup else 20), 'COMMAND_CAP_REFUSED')
        end = min(self.deadline if cleanup else self.work_deadline, time.monotonic() + budget)
        exec_end = end - .25
        require(exec_end > time.monotonic(), 'DEADLINE_REFUSED')
        selected = selectors.DefaultSelector()
        try:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except BaseException:
            selected.close()
            raise
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        record = {'args': args, 'exit_code': None, 'stdout_bytes': 0, 'stderr_bytes': 0}
        self.commands.append(record)
        try:
            for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selected.register(stream, selectors.EVENT_READ, name)
            while selected.get_map():
                require(time.monotonic() < exec_end, 'COMMAND_TIMEOUT')
                for key, _ in selected.select(max(0, min(.025, exec_end - time.monotonic()))):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selected.unregister(key.fileobj)
                        continue
                    self.total += len(data)
                    require(self.total <= self.output_limit - (0 if cleanup else 262144)
                            and len(buffers[key.data]) + len(data) <= 65536, 'OUTPUT_LIMIT')
                    buffers[key.data].extend(data)
            status = None
            while time.monotonic() < exec_end:
                status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if status is not None:
                    break
                time.sleep(.005)
            require(status is not None, 'COMMAND_TIMEOUT')
            code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
            record.update(exit_code=code, stdout_bytes=len(buffers['stdout']), stderr_bytes=len(buffers['stderr']))
            require(time.monotonic() < end and code == 0 and not buffers['stderr'], 'NATIVE_COMMAND_REFUSED')
            return bytes(buffers['stdout'])
        except (OSError, subprocess.SubprocessError):
            raise StartupRefusal('NATIVE_EXECUTION_REFUSED') from None
        finally:
            from native_startup_guardian import local_kill_reap, close_pipes
            try:
                local_kill_reap(process, end)
            finally:
                try:
                    selected.close()
                finally:
                    close_pipes(process)
            require(time.monotonic() < end, 'COMMAND_CLOSE_DEADLINE')



IMAGE_PROJECTION = ('{"Id":{{json .Id}},"Os":{{json .Os}},"Architecture":{{json .Architecture}},'
    '"RootFS":{{json .RootFS}},"User":{{json (index .Config "User")}},'
    '"Entrypoint":{{json (index .Config "Entrypoint")}},"Cmd":{{json (index .Config "Cmd")}},'
    '"Healthcheck":{{json (index .Config "Healthcheck")}},"Volumes":{{json (index .Config "Volumes")}}}')
VOLUME_PROJECTION = ('{"Name":{{json .Name}},"Driver":{{json .Driver}},'
                     '"Options":{{json .Options}},"Labels":{{json .Labels}}}')


class PreparationExecutor:
    """Explicitly admitted public profile input, fresh resources, preserved materials."""
    def __init__(self, boundary, controller=None, guardian_factory=None):
        self.controller = controller
        self.guardian_factory = guardian_factory or GuardianClient
        self.guardian = None
        self.boundary = boundary
        self.helpers = []
        self.volumes = []
        self.guard_cids = []
        self.candidate = None
        self.profile = None
        self.handoff = None
        self.receipt = {'status': 'REFUSED', 'startup': 'UNRUN', 'materials_retained': True,
                        'commands': boundary.commands, 'cleanup': [], 'observations': [], 'material_witnesses': {}}

    def call(self, args, cleanup=False, cap=None):
        return self.boundary.call(args, cleanup=cleanup, cap=cap)

    def projection(self, kind, identity, template, cleanup=False):
        raw = self.call(['docker', kind, 'inspect', '--format', template, identity], cleanup)
        require(len(raw) <= 65536, 'PROJECTION_LIMIT')
        try:
            value = json.loads(raw)
            self.receipt['observations'].append({'kind': kind, 'identity': identity, 'projection': value, 'cleanup': cleanup})
            return value
        except (ValueError, UnicodeError):
            raise StartupRefusal('PROJECTION_REFUSED') from None

    def renew_volume(self, name, owner, cleanup=False):
        item = self.projection('volume', name, VOLUME_PROJECTION, cleanup)
        consumers = self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'volume=' + name], cleanup)
        require(consumers == b'', 'VOLUME_CONSUMERS_REFUSED')
        admit_volume_projection(item, name, owner, [])

    def absence(self, name, cleanup=False):
        # Enumerate exact-name IDs only. No full unrelated container inspection.
        raw = self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'name=^/' + name + '$'], cleanup)
        require(raw == b'', 'NAME_ALREADY_EXISTS')

    def helper_args(self, lineage, image, entrypoint, command, volumes, *, seed=False):
        args = ['docker', 'create', '--pull=never', '--name', lineage.name,
                '--label', 'io.sbarbase.owner=' + self.handoff.owner,
                '--network', 'none', '--read-only', '--no-healthcheck', '--user',
                '0:0' if seed else f'{self.profile.uid}:{self.profile.gid}', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--memory', '64m', '--memory-swap', '64m',
                '--cpus', '.25', '--pids-limit', '16', '--log-driver', 'none', '--entrypoint', entrypoint]
        if seed:
            args += ['--cap-add', 'CHOWN']
        for name, path, readonly, copyup in volumes:
            value = 'type=volume,source=' + name + ',target=' + path
            if not copyup:
                value += ',volume-nocopy'
            if readonly:
                value += ',readonly'
            args += ['--mount', value]
        return args + [image, *command]

    def helper_admit(self, item, args, lineage, *, terminal=False):
        require(item['Id'] == lineage.cleanup_identity() and item['Name'] == '/' + lineage.name,
                'HELPER_IDENTITY_DIFFERS')
        expected_image = self.profile.image if args[-len(self._command)-1] == self.profile.reference else self.profile.verifier_image
        require(item['Image'] == expected_image and item['User'] == args[args.index('--user') + 1]
                and item['Entrypoint'] == [args[args.index('--entrypoint') + 1]]
                and item['Cmd'] == self._command and item['Labels'] == {'io.sbarbase.owner': self.handoff.owner},
                'HELPER_SOURCE_DIFFERS')
        state = {'Status': 'exited' if terminal else 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0}
        require(item['State'] == state and item['Networks'] == ['none'], 'HELPER_STATE_DIFFERS')
        # Full resource and mount projections are bound by the expected create args.
        expected_host = {'ReadonlyRootfs': True, 'NetworkMode': 'none', 'PidMode': '', 'IpcMode': 'private',
            'Privileged': False, 'CapAdd': ['CHOWN'] if '--cap-add' in args else None, 'CapDrop': ['ALL'],
            'SecurityOpt': ['no-new-privileges'], 'Memory': 67108864, 'MemorySwap': 67108864,
            'NanoCpus': 250000000, 'PidsLimit': 16, 'ShmSize': 67108864,
            'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}, 'PortBindings': {}, 'Binds': None,
            'Devices': [], 'LogConfig': {'Type': 'none', 'Config': {}}}
        require(item['HostConfig'] == expected_host, 'HELPER_RESOURCES_DIFFERS')
        health = self._factory_health if expected_image == self.profile.image else self._verifier_health
        require(item['Healthcheck'] == health, 'HELPER_HEALTH_DIFFERS')
        expected_mounts = []
        for index, value in enumerate(args):
            if value == '--mount':
                options = args[index + 1].split(',')
                fields = dict(part.split('=', 1) for part in options if '=' in part)
                expected_mounts.append({'Type': 'volume', 'Name': fields['source'], 'Destination': fields['target'],
                                        'RW': 'readonly' not in options, 'Propagation': ''})
        require(sorted(item['Mounts'], key=lambda row: row['Name']) == sorted(expected_mounts, key=lambda row: row['Name']),
                'HELPER_MOUNTS_DIFFERS')

    def helper_contract(self, args, lineage):
        return expected_helper_projection(self.profile, self.handoff, args, lineage, self._factory_health, self._verifier_health)

    def helper(self, suffix, image, entrypoint, command, volumes, *, seed=False):
        lineage = CreationLineage('factory-' + self.handoff.run + '-' + suffix)
        self.absence(lineage.name)
        lineage.precreate_absent(b'', b'', 0)
        args = self.helper_args(lineage, image, entrypoint, command, volumes, seed=seed)
        self._command = command
        self.guardian.exchange('intent', {'role': suffix, 'expected': self.helper_contract(args, lineage)})
        lineage.begin()
        record = {'lineage': lineage, 'args': args, 'command': command, 'terminal': False}
        self.helpers.append(record)
        cid = lineage.created(self.call(args), b'', 0)
        record['cid'] = cid
        self.receipt.setdefault('helper_creations', []).append({'name': lineage.name, 'cid': cid})
        self.helper_admit(self.projection('container', cid, CONTAINER_PROJECTION), args, lineage)
        self.guardian.exchange('register', {'cid': cid})
        self.guardian.exchange('permit', {})
        stdout = self.call(['docker', 'start', '--attach', cid])
        require(self.call(['docker', 'wait', cid]) == b'0\n', 'HELPER_WAIT_REFUSED')
        self.helper_admit(self.projection('container', cid, CONTAINER_PROJECTION), args, lineage, terminal=True)
        record['terminal'] = True
        self.remove_helper(record)
        return stdout, cid

    def remove_helper(self, record, cleanup=False):
        lineage = record['lineage']
        cid = lineage.cleanup_identity()
        self._command = record['command']
        self.helper_admit(self.projection('container', cid, CONTAINER_PROJECTION, cleanup), record['args'], lineage,
                          terminal=record['terminal'])
        if cleanup:
            raise StartupRefusal('GUARDIAN_OWNS_FAILURE_CLEANUP')
        self.guardian.exchange('removal', {})
        require(self.call(['docker', 'rm', cid], cleanup) == (cid + '\n').encode(), 'HELPER_REMOVE_REFUSED')
        self.absence(lineage.name, cleanup)
        require(self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + cid], cleanup) == b'',
                'HELPER_CID_REMAINS')
        self.guardian.exchange('absence', {})
        self.helpers.remove(record)

    def run(self, profile, lock, source):
        """source is an independently reviewed public profile, never private state."""
        import secrets
        import shlex
        self.profile = profile.validate(lock)
        require(type(source) is dict and set(source) == {'factory_projection', 'verifier_projection',
                'provider_hex', 'custom_files', 'custom_directories', 'custom_file_metadata'}, 'PROFILE_SOURCE_REFUSED')
        factory = self.projection('image', profile.reference, IMAGE_PROJECTION)
        verifier = self.projection('image', profile.verifier_image, IMAGE_PROJECTION)
        require(factory == source['factory_projection'] and verifier == source['verifier_projection']
                and factory['Id'] == profile.image and verifier['Id'] == profile.verifier_image
                and factory['Os'] == verifier['Os'] == 'linux' and factory['Architecture'] == verifier['Architecture']
                and factory['Volumes'] is None and verifier['Volumes'] is None
                and factory['Entrypoint'] == list(profile.entrypoint) and factory['Cmd'] == list(profile.command),
                'PROFILE_IMAGE_SOURCE_DIFFERS')
        def disabled(image):
            health = image['Healthcheck']
            return {**(health or {}), 'Test': ['NONE']}
        self._factory_health, self._verifier_health = disabled(factory), disabled(verifier)
        require(self._factory_health == profile.disabled_health, 'PROFILE_HEALTH_DIFFERS')
        files, directories = source['custom_files'], source['custom_directories']
        require(type(files) is dict and 1 <= len(files) <= 64 and type(directories) is dict
                and 1 <= len(directories) <= 16 and '' in directories, 'CUSTOM_MANIFEST_REFUSED')
        metadata = source['custom_file_metadata']
        require(type(metadata) is dict and set(metadata) == set(files), 'CUSTOM_FILE_METADATA_REFUSED')
        provider = source['provider_hex']
        require(type(provider) is str and re.fullmatch(r'(?:[0-9a-f]{2}){1,4096}', provider), 'PROVIDER_SOURCE_REFUSED')
        require(not profile.provider_file.startswith(profile.custom_directory + '/'), 'PROVIDER_PRIVATE_PATH_REFUSED')
        for path in list(files) + list(directories):
            require(path == '' or (re.fullmatch(r'[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*', path)
                    and all(part not in ('.', '..', 'pgsodium_root.key') for part in path.split('/'))), 'CUSTOM_PATH_REFUSED')
        require(sum(len(value) for value in files.values() if type(value) is str) <= 65536
                and all(type(value) is str and re.fullmatch(r'(?:[0-9a-f]{2})*', value) for value in files.values()),
                'CUSTOM_BYTES_REFUSED')
        run = secrets.token_hex(16)
        names = ['factory-' + run + '-' + suffix for suffix in ('custom', 'credential', 'pgdata', 'runtime')]
        self.handoff = MaterialHandoff(run, *names, {}, {}, ())
        try:
            for name in names:
                require(self.call(['docker', 'volume', 'ls', '-q', '--filter', 'name=^' + name + '$']) == b'', 'VOLUME_ALREADY_EXISTS')
                self.volumes.append({'name': name, 'attempted': True})
                require(self.call(['docker', 'volume', 'create', '--driver', 'local', '--label',
                        'io.sbarbase.owner=' + self.handoff.owner, name]) == (name + '\n').encode(), 'VOLUME_CREATE_REFUSED')
                self.renew_volume(name, self.handoff.owner)
            require(self.controller is not None, 'GUARD_CONTROLLER_REQUIRED')
            self.guardian = self.guardian_factory(self)
            self.guardian.launch()
            # Source-bound original public provider and complete custom config tree.
            check = '[ "$(/usr/bin/od -An -v -tx1 ' + shlex.quote(profile.provider_file) + ' | tr -d " \\n")" = ' + shlex.quote(provider) + ' ]\n'
            expected_names = sorted(list(files) + list(directories))
            check += '[ "$(find ' + shlex.quote(profile.custom_directory) + ' -mindepth 1 -printf "%P\\n" | LC_ALL=C sort)" = ' + shlex.quote('\n'.join(name for name in expected_names if name)) + ' ]\n'
            for path, value in files.items():
                target = shlex.quote(profile.custom_directory + '/' + path)
                require(type(metadata[path]) is str and re.fullmatch(r'[0-9]{1,8}:[0-9]{1,8}:[0-7]{3,4}:1', metadata[path]), 'CUSTOM_FILE_METADATA_REFUSED')
                check += '[ -f ' + target + ' ] && [ ! -L ' + target + ' ]\n'
                check += '[ "$(stat -c "%u:%g:%a:%h" ' + target + ')" = ' + shlex.quote(metadata[path]) + ' ]\n'
                check += '[ "$(/usr/bin/od -An -v -tx1 ' + target + ' | tr -d " \\n")" = ' + shlex.quote(value) + ' ]\n'
            for path, meta in directories.items():
                require(type(meta) is str and re.fullmatch(r'[0-9]{1,8}:[0-9]{1,8}:[0-7]{3,4}', meta), 'CUSTOM_DIRECTORY_METADATA_REFUSED')
                target = shlex.quote(profile.custom_directory + ('/' + path if path else ''))
                check += '[ -d ' + target + ' ] && [ ! -L ' + target + ' ]\n'
                check += '[ "$(stat -c "%u:%g:%a" ' + target + ')" = ' + shlex.quote(meta) + ' ]\n'
            # First baseline has no mounts, so profile matches source rather than copied state.
            stdout, _ = self.helper('source-check', profile.reference, '/bin/sh', ['-eu', '-c', check + 'printf "SOURCE_COMPLETE\\n"\n'], [])
            require(stdout == b'SOURCE_COMPLETE\n', 'SOURCE_FRAME_REFUSED')
            seed = 'chown ' + str(profile.uid) + ':' + str(profile.gid) + ' ' + shlex.quote(profile.custom_directory) + '\n' + check
            seed += 'chmod 700 ' + shlex.quote(profile.credential_directory) + '\n'
            seed += 'chown ' + str(profile.uid) + ':' + str(profile.gid) + ' ' + shlex.quote(profile.credential_directory) + '\n'
            seed += 'chmod 700 ' + shlex.quote(profile.data_directory) + '\n'
            seed += 'chmod 3775 ' + shlex.quote(profile.socket_directory) + '\n'
            for path in (profile.data_directory, profile.socket_directory):
                seed += 'chown ' + str(profile.uid) + ':' + str(profile.gid) + ' ' + shlex.quote(path) + '\n'
            seed += 'printf "COPYUP_COMPLETE\\n"\n'
            stdout, _ = self.helper('seed', profile.reference, '/bin/sh', ['-eu', '-c', seed],
                [(names[0], profile.custom_directory, False, True), (names[1], profile.credential_directory, False, False),
                 (names[2], profile.data_directory, False, False), (names[3], profile.socket_directory, False, False)], seed=True)
            require(stdout == b'COPYUP_COMPLETE\n', 'COPYUP_FRAME_REFUSED')
            witnesses = []
            for kind, volume, path in (('key', names[0], profile.custom_directory), ('password', names[1], profile.credential_directory)):
                command = ['/opt/sbarbase/lab/native_startup_materials.py', kind, 'provision', path]
                stdout, _ = self.helper(kind + '-provision', profile.verifier_image, '/usr/bin/python3', command,
                                        [(volume, path, False, False)])
                require(type(stdout) is bytes and len(stdout) <= 2048 and stdout.endswith(b'\n') and stdout.count(b'\n') == 1, 'PROVISION_FRAME_REFUSED')
                frame = json.loads(stdout)
                require(type(frame) is dict and set(frame) == {'status', 'kind', 'operation', 'metadata'} and frame['status'] == 'MATERIAL_VERIFIED'
                        and frame['kind'] == kind and frame['operation'] == 'provision', 'PROVISION_FRAME_REFUSED')
                expected = witness(frame['metadata'])
                command = ['/opt/sbarbase/lab/native_startup_materials.py', kind, 'verify', path,
                           '--expected', json.dumps(expected, sort_keys=True, separators=(',', ':'))]
                stdout, cid = self.helper(kind + '-guard', profile.verifier_image, '/usr/bin/python3', command,
                                         [(volume, path, True, False)])
                frame = guard_frame(stdout, b'', 0, kind, expected)
                self.receipt['material_witnesses'][kind] = {'metadata': expected, 'guard_frame': frame, 'guard_cid': cid}
                witnesses.append(expected)
                self.guard_cids.append(cid)
            self.handoff = MaterialHandoff(run, *names, *witnesses, tuple(self.guard_cids))
            for name in names:
                self.renew_volume(name, self.handoff.owner)
            require(self.projection('image', profile.reference, IMAGE_PROJECTION) == factory
                    and self.projection('image', profile.verifier_image, IMAGE_PROJECTION) == verifier, 'IMAGE_RENEWAL_REFUSED')
            lineage = CreationLineage(self.handoff.candidate_name)
            self.absence(lineage.name)
            lineage.precreate_absent(b'', b'', 0)
            self.candidate = lineage
            candidate_args = create_arguments(profile, self.handoff, lock)
            self.guardian.exchange('intent', {'role': 'candidate', 'expected': expected_candidate_projection(profile, self.handoff)})
            lineage.begin()
            cid = lineage.created(self.call(candidate_args), b'', 0)
            self.receipt['candidate_creation'] = {'name': lineage.name, 'cid': cid}
            self.guardian.exchange('register', {'cid': cid})
            admit_created_candidate(self.projection('container', cid, CONTAINER_PROJECTION), profile, self.handoff, lineage)
            self.remove_candidate()
            for name in names:
                self.renew_volume(name, self.handoff.owner)
            require(self.projection('image', profile.reference, IMAGE_PROJECTION) == factory
                    and self.projection('image', profile.verifier_image, IMAGE_PROJECTION) == verifier, 'IMAGE_RENEWAL_REFUSED')
            self.guardian.finish()
            self.receipt.update(preparation_receipt(profile=profile, handoff=self.handoff, candidate_cid=cid,
                created_admitted=True, candidate_absent=True, copyup_admitted=True, guards_admitted=True, volumes_renewed=True))
            return self.receipt
        finally:
            if self.receipt['status'] != 'STARTUP_MATERIAL_HANDOFF_ONLY' and self.guardian is not None:
                try:
                    self.guardian.cancel()
                except (StartupRefusal, OSError, ValueError):
                    self.receipt['cleanup'].append({'kind': 'guardian', 'status': 'UNCERTAIN'})
            for record in self.helpers:
                self.receipt['cleanup'].append({'kind': 'helper', 'name': record['lineage'].name,
                    'cid': record['lineage'].cid, 'create_attempted': record['lineage'].attempted,
                    'status': 'GUARDIAN_OR_UNRESOLVED'})
            if self.candidate is not None:
                self.receipt['cleanup'].append({'kind': 'candidate', 'name': self.candidate.name,
                    'cid': self.candidate.cid, 'create_attempted': self.candidate.attempted,
                    'status': 'GUARDIAN_OR_UNRESOLVED'})
            if self.receipt['cleanup']:
                self.receipt['status'] = 'REFUSED'

    def remove_candidate(self, cleanup=False):
        lineage = self.candidate
        cid = lineage.cleanup_identity()
        admit_created_candidate(self.projection('container', cid, CONTAINER_PROJECTION, cleanup),
                                self.profile, self.handoff, lineage)
        require(not cleanup, 'GUARDIAN_OWNS_FAILURE_CLEANUP')
        self.guardian.exchange('removal', {})
        require(self.call(['docker', 'rm', cid], cleanup) == (cid + '\n').encode(), 'CANDIDATE_REMOVE_REFUSED')
        self.absence(lineage.name, cleanup)
        require(self.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + cid], cleanup) == b'',
                'CANDIDATE_CID_REMAINS')
        self.guardian.exchange('absence', {})
        self.candidate = None



def expected_host(*, helper, seed=False, guardian=False):
    return {'ReadonlyRootfs': True, 'NetworkMode': 'none', 'PidMode': '', 'IpcMode': 'private',
        'Privileged': False, 'CapAdd': ['CHOWN'] if seed else None, 'CapDrop': ['ALL'],
        'SecurityOpt': ['no-new-privileges'], 'Memory': 67108864 if helper else 536870912,
        'MemorySwap': 67108864 if helper else 536870912, 'NanoCpus': 250000000 if helper else 1000000000,
        'PidsLimit': 32 if guardian else (16 if helper else 128), 'ShmSize': 67108864,
        'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}, 'PortBindings': {}, 'Binds': None,
        'Devices': [], 'LogConfig': {'Type': 'none', 'Config': {}}}


def expected_helper_projection(profile, handoff, args, lineage, factory_health, verifier_health):
    source_index = args.index(profile.reference) if profile.reference in args else args.index(profile.verifier_image)
    command = args[source_index + 1:]
    source_image = profile.image if args[source_index] == profile.reference else profile.verifier_image
    expected_mounts = []
    for index, value in enumerate(args):
        if value == '--mount':
            parts = args[index + 1].split(',')
            fields = dict(part.split('=', 1) for part in parts if '=' in part)
            expected_mounts.append({'Type': 'volume', 'Name': fields['source'], 'Destination': fields['target'],
                                   'RW': 'readonly' not in parts, 'Propagation': ''})
    return {'Id': None, 'Name': '/' + lineage.name, 'Image': source_image,
        'State': {'Status': 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0},
        'User': args[args.index('--user') + 1], 'Entrypoint': [args[args.index('--entrypoint') + 1]],
        'Cmd': command, 'Healthcheck': factory_health if source_image == profile.image else verifier_health,
        'Labels': {'io.sbarbase.owner': handoff.owner}, 'Mounts': expected_mounts,
        'HostConfig': expected_host(helper=True, seed='--cap-add' in args), 'Networks': ['none']}


def expected_candidate_projection(profile, handoff):
    return {'Id': None, 'Name': '/' + handoff.candidate_name, 'Image': profile.image,
        'State': {'Status': 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0},
        'User': f'{profile.uid}:{profile.gid}', 'Entrypoint': list(profile.entrypoint), 'Cmd': list(profile.command),
        'Healthcheck': profile.disabled_health, 'Labels': {'io.sbarbase.owner': handoff.owner},
        'Mounts': [{'Type': 'volume', **mount, 'Propagation': ''} for mount in mounts(profile, handoff)],
        'HostConfig': expected_host(helper=False), 'Networks': ['none']}


class GuardianClient:
    """Separate daemon guardian lifecycle; all controls contain fixed public JSON."""
    def __init__(self, executor):
        self.executor = executor
        self.sequence = 0
        self.lineage = CreationLineage('factory-' + executor.handoff.run + '-guardian')
        self.args = None
        self.expected = None
        self.ended = False

    def exchange(self, operation, data, cleanup=False):
        self.sequence += 1
        message = json.dumps({'sequence': self.sequence, 'operation': operation, 'data': data}, sort_keys=True, separators=(',', ':'))
        require(len(message) <= 131072, 'GUARD_MESSAGE_LIMIT')
        raw = self.executor.call(['docker', 'exec', self.lineage.cleanup_identity(), '/usr/bin/python3',
            '/opt/sbarbase/deploy/verify/native_startup_guardian.py', 'control', '--message', message], cleanup, cap=2)
        require(len(raw) <= 4096 and raw.count(b'\n') == 1 and raw.endswith(b'\n'), 'GUARD_REPLY_FRAME')
        reply = json.loads(raw)
        require(type(reply) is dict and set(reply) == {'status', 'data'} and reply['status'] == 'ACK', 'GUARD_REPLY_REFUSED')
        self.executor.receipt.setdefault('guardian_controls', []).append({'message': {'sequence': self.sequence, 'operation': operation}, 'reply': reply})
        return reply['data']

    def admit(self, *, state):
        projection = CONTAINER_PROJECTION.replace('"Propagation":{{json (index $m "Propagation")}}',
            '"Source":{{if eq $m.Type "bind"}}{{json $m.Source}}{{else}}null{{end}},"Propagation":{{json (index $m "Propagation")}}')
        item = self.executor.projection('container', self.lineage.cleanup_identity(), projection, cleanup=state == 'exited')
        require({key: value for key, value in item.items() if key != 'State'} ==
                {key: value for key, value in self.expected.items() if key != 'State'}, 'GUARDIAN_SOURCE_DIFFERS')
        require(item['State'] == {'Status': state, 'Running': state == 'running', 'OOMKilled': False, 'ExitCode': 0},
                'GUARDIAN_STATE_DIFFERS')

    def launch(self):
        import math
        import time
        executor, profile = self.executor, self.executor.profile
        control_volume = 'factory-' + executor.handoff.run + '-control'
        require(executor.call(['docker', 'volume', 'ls', '-q', '--filter', 'name=^' + control_volume + '$']) == b'', 'CONTROL_VOLUME_PRESENT')
        executor.volumes.append({'name': control_volume, 'attempted': True})
        require(executor.call(['docker', 'volume', 'create', '--driver', 'local', '--label', 'io.sbarbase.owner=' + executor.handoff.owner,
            control_volume]) == (control_volume + '\n').encode(), 'CONTROL_VOLUME_CREATE_REFUSED')
        executor.renew_volume(control_volume, executor.handoff.owner)
        duration = math.floor(executor.boundary.deadline - time.monotonic() - 40)
        require(40 < duration <= 240, 'GUARDIAN_LAUNCH_RESERVE')
        command = ['/opt/sbarbase/deploy/verify/native_startup_guardian.py', 'wrapper', '--run', executor.handoff.run,
            '--duration', str(duration), '--controller', json.dumps(executor.controller, separators=(',', ':'), sort_keys=True)]
        self.args = ['docker', 'create', '--pull=never', '--name', self.lineage.name,
            '--label', 'io.sbarbase.owner=' + executor.handoff.owner, '--user', '0:0', '--network', 'none', '--read-only',
            '--no-healthcheck', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '64m', '--memory-swap', '64m',
            '--cpus', '.25', '--pids-limit', '32', '--log-driver', 'none', '--entrypoint', '/usr/bin/python3',
            '--mount', 'type=bind,source=' + profile.docker_socket_source + ',target=/var/run/docker.sock',
            '--mount', 'type=volume,source=' + control_volume + ',target=/sbarbase-public-control,volume-nocopy',
            profile.verifier_image, *command]
        executor.absence(self.lineage.name)
        self.lineage.precreate_absent(b'', b'', 0)
        self.lineage.begin()
        cid = self.lineage.created(executor.call(self.args), b'', 0)
        self.expected = {'Id': cid, 'Name': '/' + self.lineage.name, 'Image': profile.verifier_image,
            'User': '0:0', 'Entrypoint': ['/usr/bin/python3'], 'Cmd': command, 'Healthcheck': executor._verifier_health,
            'Labels': {'io.sbarbase.owner': executor.handoff.owner},
            'Mounts': [{'Type': 'bind', 'Name': None, 'Source': profile.docker_socket_source, 'Destination': '/var/run/docker.sock', 'RW': True, 'Propagation': 'rprivate'},
                {'Type': 'volume', 'Name': control_volume, 'Source': None, 'Destination': '/sbarbase-public-control', 'RW': True, 'Propagation': ''}],
            'HostConfig': expected_host(helper=True, guardian=True), 'Networks': ['none']}
        self.admit(state='created')
        require(executor.call(['docker', 'start', cid]) == (cid + '\n').encode(), 'GUARDIAN_START_REFUSED')
        self.admit(state='running')
        sample = self.exchange('sample', {})
        require(type(sample) is dict and set(sample) == {'sample', 'hard_end'}
                and all(type(value) in (int, float) and math.isfinite(value) for value in sample.values()), 'GUARD_CLOCK_FRAME')
        now = time.monotonic()
        self.exchange('configure', {'work': sample['sample'] + max(0, executor.boundary.work_deadline - now),
                                   'whole': sample['sample'] + max(0, executor.boundary.deadline - now)})

    def finish(self):
        import time
        from native_startup_guardian import reserve_completion
        reserve_completion(time.monotonic(), self.executor.boundary.deadline, 3)
        self.exchange('seal', {})
        reserve_completion(time.monotonic(), self.executor.boundary.deadline, 2)
        frame = self.exchange('read', {})
        require(frame == {'status': 'GUARD_COMPLETE', 'roles': ['source-check', 'seed', 'key-provision', 'key-guard',
            'password-provision', 'password-guard', 'candidate'], 'pending': False}, 'GUARD_COMPLETION_REFUSED')
        reserve_completion(time.monotonic(), self.executor.boundary.deadline, 1)
        self.exchange('release', {})
        cid = self.lineage.cleanup_identity()
        require(self.executor.call(['docker', 'wait', cid], True) == b'0\n', 'GUARDIAN_WAIT_REFUSED')
        self.admit(state='exited')
        require(self.executor.call(['docker', 'rm', cid], True) == (cid + '\n').encode(), 'GUARDIAN_REMOVE_REFUSED')
        require(self.executor.call(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + cid], True) == b'', 'GUARDIAN_CID_PRESENT')
        self.executor.absence(self.lineage.name, True)
        self.executor.receipt['guardian_terminal'] = {'cid': cid, 'status': 'ABSENT', 'exit_code': 0}
        self.ended = True

    def cancel(self):
        require(not self.ended, 'GUARDIAN_ALREADY_ENDED')
        self.exchange('cancel', {}, cleanup=True)
        # No success/fallback: independent guardian records cleanup privately in
        # its public receipt volume, even when this controller cannot continue.


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--profile', help='Independently admitted public factory/verifier profile JSON')
    parser.add_argument('--verifier-container', help='Full CID of this baked verifier invocation')
    parser.add_argument('--evidence', default='/evidence/preparation.json')
    args = parser.parse_args(argv)
    executor = None
    try:
        root = Path(__file__).resolve().parents[2]
        require(root == Path('/opt/sbarbase'), 'BAKED_EXECUTION_REQUIRED')
        require(args.prepare_only, 'STARTUP_ACCEPTANCE_UNRUN')
        require(args.profile is not None and identifier(args.verifier_container), 'PROFILE_INPUT_REQUIRED')
        require(type(args.profile) is str and re.fullmatch(r'/public-profile/[a-zA-Z0-9_.-]+\.json', args.profile), 'PROFILE_PATH_REFUSED')
        import os
        import stat
        fd = os.open(args.profile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            value = os.fstat(fd)
            require(stat.S_ISREG(value.st_mode) and value.st_size <= 131072, 'PROFILE_LIMIT')
            public = os.read(fd, 131073)
            require(len(public) <= 131072, 'PROFILE_LIMIT')
        finally:
            os.close(fd)
        packet = json.loads(public)
        require(type(packet) is dict and set(packet) == {'profile', 'source'}, 'PROFILE_PACKET_REFUSED')
        values = packet['profile']
        require(type(values) is dict, 'PROFILE_PACKET_REFUSED')
        values['entrypoint'], values['command'] = tuple(values['entrypoint']), tuple(values['command'])
        profile = NativeProfile(**values)
        output = Path(args.evidence)
        require(output.is_absolute() and output.parent == Path('/evidence') and not output.exists() and not output.is_symlink(), 'EVIDENCE_PATH_REFUSED')
        boundary = BoundedDocker()
        executor = PreparationExecutor(boundary)
        own = executor.projection('container', args.verifier_container,
            '{"Id":{{json .Id}},"Image":{{json .Image}},"Hostname":{{json .Config.Hostname}}}')
        import socket
        require(own == {'Id': args.verifier_container, 'Image': profile.verifier_image,
                        'Hostname': socket.gethostname()}, 'CURRENT_VERIFIER_IDENTITY_DIFFERS')
        executor.controller = executor.projection('container', args.verifier_container, CONTAINER_PROJECTION)
        require(executor.controller['State']['Running'] is True and executor.controller['State']['OOMKilled'] is False, 'CURRENT_CONTROLLER_NOT_RUNNING')
        lock = json.loads((root / 'lab/distro-image.lock.json').read_text())
        report = executor.run(profile, lock, packet['source'])
        output = Path(args.evidence)
        require(output.is_absolute() and output.parent == Path('/evidence'), 'EVIDENCE_PATH_REFUSED')
        with output.open('x') as stream:
            json.dump(report, stream, sort_keys=True)
            stream.write('\n')
        print(json.dumps({'status': report['status'], 'startup': 'UNRUN', 'production_accepted': False}))
        return 0 if report['status'] == 'STARTUP_MATERIAL_HANDOFF_ONLY' else 1
    except (StartupRefusal, OSError, ValueError, TypeError, KeyError) as error:
        reason = str(error) if isinstance(error, StartupRefusal) else 'PUBLIC_INPUT_OR_NATIVE_FAILURE'
        # Retain public resource lineage; never include arbitrary exception text.
        if executor is not None:
            report = executor.receipt
            report['status'] = 'REFUSED'
            report['volume_names'] = [item['name'] for item in executor.volumes]
            report['candidate_name'] = None if executor.candidate is None else executor.candidate.name
            try:
                output = Path(args.evidence)
                if output.is_absolute() and output.parent == Path('/evidence'):
                    with output.open('x') as stream:
                        json.dump(report, stream, sort_keys=True)
                        stream.write('\n')
            except (OSError, ValueError):
                pass
        print(json.dumps({'status': 'REFUSED', 'reason': reason, 'startup': 'UNRUN', 'production_accepted': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
