"""Baked tests exercise real orchestration using public-only native boundary doubles."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy/verify'))
import native_factory_startup_inspect as startup


FACTORY = 'sha256:' + '1' * 64
VERIFIER = 'sha256:' + '2' * 64
REFERENCE = 'docker.io/supabase/postgres@' + FACTORY
LOCK = {'tag': 'docker.io/supabase/postgres:test', 'id': FACTORY, 'digests': [REFERENCE]}
HEALTH = {'Test': ['NONE'], 'Interval': 2000000000, 'Retries': 10, 'Timeout': 2000000000}
WITNESS = {'device': 1, 'inode': 2, 'mode': 0o600, 'uid': 100, 'gid': 101,
           'nlink': 1, 'size': 64, 'mtime_ns': 3, 'ctime_ns': 4}


def profile():
    return startup.NativeProfile(FACTORY, REFERENCE, VERIFIER, 100, 101, '/etc/postgresql-custom',
        '/credential', '/var/lib/postgresql/data', '/var/run/postgresql', '/etc/postgresql/postgresql.conf',
        '/usr/lib/postgresql/bin/pgsodium_getkey.sh', '/etc/postgresql-custom/pgsodium_root.key',
        ('docker-entrypoint.sh',), ('postgres', '-D', '/etc/postgresql'), HEALTH)


def image(identity):
    return {'Id': identity, 'Os': 'linux', 'Architecture': 'amd64', 'RootFS': {'Type': 'layers', 'Layers': []},
        'User': None, 'Entrypoint': ['docker-entrypoint.sh'] if identity == FACTORY else ['/usr/bin/python3'],
        'Cmd': ['postgres', '-D', '/etc/postgresql'] if identity == FACTORY else [],
        'Healthcheck': {'Test': ['CMD', 'pg_isready'], **{key: value for key, value in HEALTH.items() if key != 'Test'}} if identity == FACTORY else None,
        'Volumes': None}


def source():
    return {'factory_projection': image(FACTORY), 'verifier_projection': image(VERIFIER),
            'provider_hex': '7075626c6963', 'custom_files': {'pg_net.conf': '7075626c6963'},
            'custom_directories': {'': '100:101:755'}, 'custom_file_metadata': {'pg_net.conf': '100:101:644:1'}}


class FakeDocker:
    """Models create/start/wait/inspect/remove, so tests use actual executor flow."""
    def __init__(self, fail=None):
        self.commands = []
        self.deadline = time.monotonic() + 240
        self.work_deadline = self.deadline - 60
        self.controller = {'Id': 'f' * 64, 'Name': '/test-controller', 'Image': VERIFIER,
            'State': {'Status': 'running', 'Running': True, 'OOMKilled': False, 'ExitCode': 0}}
        self.guard = None
        self.create_args = {}
        self.containers = {}
        self.volumes = {}
        self.next = 1
        self.fail = fail
        self.triggered = False

    def call(self, args, *, cleanup=False, cap=None):
        self.commands.append({'args': args, 'cleanup': cleanup})
        if self.fail is not None and not self.triggered and self.fail(args):
            self.triggered = True
            raise startup.StartupRefusal('INJECTED_NATIVE_FAILURE')
        operation = args[1:3]
        if operation == ['image', 'inspect']:
            return json.dumps(image(FACTORY if args[-1] == REFERENCE else VERIFIER)).encode() + b'\n'
        if operation == ['volume', 'ls']:
            return b''
        if operation == ['volume', 'create']:
            name = args[-1]
            self.volumes[name] = {'Name': name, 'Driver': 'local', 'Options': None,
                                  'Labels': {'io.sbarbase.owner': args[args.index('--label') + 1].split('=', 1)[1]}}
            return (name + '\n').encode()
        if operation == ['volume', 'inspect']:
            return json.dumps(self.volumes[args[-1]]).encode() + b'\n'
        if args[1] == 'ps':
            query = args[args.index('--filter') + 1]
            if query.startswith('id='):
                return ((query[3:] + '\n').encode() if query[3:] in self.containers else b'')
            if query.startswith('name='):
                target = query[5:].removeprefix('^').removesuffix('$')
                return b''.join((cid + '\n').encode() for cid, item in self.containers.items() if item['Name'] == target)
            if query.startswith('volume='):
                return b''.join((cid + '\n').encode() for cid, item in self.containers.items() if any(m['Name'] == query[7:] for m in item['Mounts']))
            return b''
        if args[1] == 'create':
            cid = format(self.next, '064x')
            self.next += 1
            source_index = args.index(REFERENCE) if REFERENCE in args else args.index(VERIFIER)
            helper = '--entrypoint' in args
            command = args[source_index + 1:]
            mounts = []
            for index, value in enumerate(args):
                if value == '--mount':
                    parts = args[index + 1].split(',')
                    fields = dict(part.split('=', 1) for part in parts if '=' in part)
                    mounts.append({'Type': fields['type'], 'Name': fields['source'] if fields['type'] == 'volume' else None, 'Destination': fields['target'],
                                   'RW': 'readonly' not in parts, 'Propagation': '' if fields['type'] == 'volume' else 'rprivate'})
            item = {'Id': cid, 'Name': '/' + args[args.index('--name') + 1],
                'Image': FACTORY if args[source_index] == REFERENCE else VERIFIER,
                'State': {'Status': 'created', 'Running': False, 'OOMKilled': False, 'ExitCode': 0},
                'User': args[args.index('--user') + 1],
                'Entrypoint': [args[args.index('--entrypoint') + 1]] if helper else ['docker-entrypoint.sh'],
                'Cmd': command, 'Healthcheck': HEALTH if args[source_index] == REFERENCE else {'Test': ['NONE']},
                'Labels': {'io.sbarbase.owner': args[args.index('--label') + 1].split('=', 1)[1]},
                'Mounts': mounts, 'Networks': ['none'],
                'HostConfig': {'ReadonlyRootfs': True, 'NetworkMode': 'none', 'PidMode': '', 'IpcMode': 'private',
                    'Privileged': False, 'CapAdd': ['CHOWN'] if '--cap-add' in args else None, 'CapDrop': ['ALL'],
                    'SecurityOpt': ['no-new-privileges'], 'Memory': 67108864 if helper else 536870912,
                    'MemorySwap': 67108864 if helper else 536870912, 'NanoCpus': 250000000 if helper else 1000000000,
                    'PidsLimit': (32 if args[args.index('--name') + 1].endswith('-guardian') else 16) if helper else 128, 'ShmSize': 67108864,
                    'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}, 'PortBindings': {}, 'Binds': None,
                    'Devices': [], 'LogConfig': {'Type': 'none', 'Config': {}}}}
            self.containers[cid] = item
            self.create_args[cid] = args
            return (cid + '\n').encode()
        if operation == ['container', 'inspect']:
            if args[-1] == self.controller['Id']:
                return json.dumps(self.controller).encode() + b'\n'
            item = copy.deepcopy(self.containers[args[-1]])
            if '"Source"' in args[args.index('--format') + 1]:
                for mount in item['Mounts']:
                    mount['Source'] = '/var/run/docker.sock' if mount['Type'] == 'bind' else None
            return json.dumps(item).encode() + b'\n'
        if args[1] == 'start':
            item = self.containers[args[-1]]
            command = item['Cmd']
            if item['Name'].endswith('-guardian'):
                import native_startup_guardian as guardian
                run = command[command.index('--run') + 1]
                duration = int(command[command.index('--duration') + 1])
                self.guard = guardian.Guardian(run, time.monotonic() + duration, self.controller, FakeGuardDocker(self))
                item['State']['Status'], item['State']['Running'] = 'running', True
                return (args[-1] + '\n').encode()
            item['State']['Status'] = 'exited'
            if 'source-check' in item['Name']:
                return b'SOURCE_COMPLETE\n'
            if 'seed' in item['Name']:
                return b'COPYUP_COMPLETE\n'
            kind, operation = command[1:3]
            return json.dumps({'status': 'MATERIAL_VERIFIED', 'kind': kind, 'operation': operation,
                               'metadata': WITNESS}).encode() + b'\n'
        if args[1] == 'exec':
            message = json.loads(args[args.index('--message') + 1])
            reply = self.guard.message(message)
            if message['operation'] == 'release':
                self.containers[args[2]]['State']['Status'] = 'exited'
                self.containers[args[2]]['State']['Running'] = False
            return json.dumps({'status': 'ACK', 'data': reply}).encode() + b'\n'
        if args[1] == 'wait':
            return b'0\n'
        if args[1] == 'rm':
            del self.containers[args[-1]]
            return (args[-1] + '\n').encode()
        raise AssertionError('Unmodeled public boundary command')


class FakeGuardDocker:
    def __init__(self, docker):
        self.docker = docker
        self.commands = []

    def call(self, args, end):
        self.commands.append(args)
        if args[1] == 'kill':
            item = self.docker.containers[args[-1]]
            item['State'] = {'Status': 'exited', 'Running': False, 'OOMKilled': False, 'ExitCode': 137}
            return (args[-1] + '\n').encode()
        if args[1] == 'wait':
            return str(self.docker.containers[args[-1]]['State']['ExitCode']).encode() + b'\n'
        return self.docker.call(args)

    def inspect(self, cid, end):
        if cid == self.docker.controller['Id']:
            return copy.deepcopy(self.docker.controller)
        return copy.deepcopy(self.docker.containers[cid])

    def absent(self, cid, name, end):
        startup.require(cid not in self.docker.containers and not any(item['Name'] == '/' + name for item in self.docker.containers.values()), 'GUARD_ABSENCE_REFUSED')


class StartupTests(unittest.TestCase):
    def test_complete_executor_flow_retains_materials_without_candidate_start(self):
        docker = FakeDocker()
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        receipt = executor.run(profile(), LOCK, source())
        self.assertEqual(receipt['status'], 'STARTUP_MATERIAL_HANDOFF_ONLY')
        self.assertEqual(receipt['startup'], 'UNRUN')
        self.assertEqual(docker.containers, {})
        self.assertEqual(len(docker.volumes), 5)
        starts = [row['args'] for row in docker.commands if row['args'][1] == 'start']
        self.assertEqual(len(starts), 7)
        self.assertFalse(any(receipt['candidate_cid'] in args for args in starts))
        self.assertFalse(any(row['args'][1:3] == ['volume', 'rm'] for row in docker.commands))

    def test_unknown_create_preserves_uncertainty_and_never_adopts_or_removes(self):
        docker = FakeDocker(fail=lambda args: args[1] == 'create')
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        with self.assertRaises(startup.StartupRefusal):
            executor.run(profile(), LOCK, source())
        self.assertEqual(len(executor.helpers), 0)
        self.assertTrue(executor.guardian.lineage.uncertain)
        self.assertFalse(any(row['args'][1] == 'rm' for row in docker.commands))
        self.assertEqual(executor.receipt['status'], 'REFUSED')

    def test_candidate_creation_failure_cannot_be_success_or_trigger_name_cleanup(self):
        docker = FakeDocker(fail=lambda args: args[1] == 'create' and args[args.index('--name') + 1].endswith('-candidate'))
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        with self.assertRaises(startup.StartupRefusal):
            executor.run(profile(), LOCK, source())
        self.assertTrue(executor.candidate.uncertain)
        self.assertFalse(any(row['args'][1] == 'rm' and row['args'][-1] == executor.candidate.name for row in docker.commands))
        self.assertEqual(executor.receipt['status'], 'REFUSED')

    def test_source_profile_drift_refuses_before_resources(self):
        packet = source()
        packet['factory_projection']['RootFS']['Layers'] = ['foreign']
        docker = FakeDocker()
        with self.assertRaises(startup.StartupRefusal):
            startup.PreparationExecutor(docker, controller=docker.controller).run(profile(), LOCK, packet)
        self.assertFalse(docker.volumes)
        self.assertFalse(docker.containers)

    def test_cleanup_readmission_refuses_running_candidate(self):
        class ChangedDocker(FakeDocker):
            def call(self, args, **kwargs):
                if args[1:3] == ['container', 'inspect'] and self.containers.get(args[-1], {}).get('Name', '').endswith('-candidate'):
                    item = self.containers[args[-1]]
                    item['State']['Running'] = True
                    item['State']['Status'] = 'running'
                return super().call(args, **kwargs)
        docker = ChangedDocker()
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        with self.assertRaises(startup.StartupRefusal):
            executor.run(profile(), LOCK, source())
        self.assertTrue(executor.candidate)
        self.assertTrue(any(item['Name'].endswith('-candidate') for item in docker.containers.values()))

    def test_helper_wait_failure_cannot_pass_material_handoff(self):
        docker = FakeDocker(fail=lambda args: args[1] == 'wait')
        executor = startup.PreparationExecutor(docker, controller=docker.controller)
        with self.assertRaises(startup.StartupRefusal):
            executor.run(profile(), LOCK, source())
        self.assertEqual(executor.receipt['status'], 'REFUSED')

    def test_guard_success_stderr_and_duplicate_frame_refuse(self):
        frame = json.dumps({'status': 'MATERIAL_VERIFIED', 'kind': 'password', 'operation': 'verify', 'metadata': WITNESS}).encode() + b'\n'
        with self.assertRaises(startup.StartupRefusal):
            startup.guard_frame(frame, b'warning', 0, 'password', WITNESS)
        with self.assertRaises(startup.StartupRefusal):
            startup.guard_frame(frame.replace(b'"status":', b'"status":"MATERIAL_VERIFIED","status":'), b'', 0, 'password', WITNESS)

    def test_lineage_requires_absence_before_create_and_exact_cid_before_cleanup(self):
        lineage = startup.CreationLineage('factory-' + 'a' * 32 + '-candidate')
        with self.assertRaises(startup.StartupRefusal):
            lineage.begin()
        lineage.precreate_absent(b'', b'', 0)
        lineage.begin()
        with self.assertRaises(startup.StartupRefusal):
            lineage.cleanup_identity()
        with self.assertRaises(startup.StartupRefusal):
            lineage.created(b'x\n', b'', 0)
        self.assertTrue(lineage.uncertain)

    def test_false_incomplete_preparation_receipt_refuses(self):
        handoff = startup.MaterialHandoff('a' * 32, *['factory-' + 'a' * 32 + '-' + suffix for suffix in ('custom', 'credential', 'pgdata', 'runtime')], WITNESS, WITNESS, ('3' * 64, '4' * 64))
        with self.assertRaises(startup.StartupRefusal):
            startup.preparation_receipt(profile=profile(), handoff=handoff, candidate_cid='5' * 64,
                created_admitted=True, candidate_absent=True, copyup_admitted=False, guards_admitted=True, volumes_renewed=True)
