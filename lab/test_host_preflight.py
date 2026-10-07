"""Exercise the canonical shell through owned fake executables, never a daemon."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/python3
import json, os, pathlib, re, sys
BASE = pathlib.Path(__BASE__)
config = json.loads((BASE / 'fixture.json').read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (BASE / 'queries.jsonl').open('a') as file:
    file.write(json.dumps({'tool': name, 'args': args}) + '\n')
def output(value):
    print(value)
    sys.exit(0)
def real():
    os.execv('/usr/bin/' + name, [name] + args)
if name == 'docker':
    if args[:1] != ['--host'] or args[1] != ('unix:///var/run/docker.sock' if config.get('runtime') else 'unix://' + config['socket']):
        sys.exit(88)
    args = args[2:]
    if args[:2] == ['info', '--format']:
        if config.get('docker_fail'): sys.exit(1)
        if config.get('raw_info') is not None: output(config['raw_info'])
        text = args[2]
        text = text.replace('{{range .SecurityOptions}}{{printf "%s|" .}}{{end}}', '|'.join(config['info']['SecurityOptions']) + '|')
        text = text.replace('{{range .DriverStatus}}{{index . 0}}={{index . 1}}{{printf "|"}}{{end}}', ''.join(k + '=' + v + '|' for k, v in config['info']['DriverStatus']))
        text = text.replace('{{println}}', '\n')
        def scalar(match):
            if match[1] in ('CpuCfsPeriod', 'CpuCfsQuota'): raise ValueError('JSON tags are not Go template field names')
            go_to_json = {'CPUCfsPeriod': 'CpuCfsPeriod', 'CPUCfsQuota': 'CpuCfsQuota'}
            value = config['info'][go_to_json.get(match[1], match[1])]
            return str(value).lower() if type(value) is bool else str(value)
        text = re.sub(r'{{\.([A-Za-z0-9]+)}}', scalar, text)
        if '{{' in text: sys.exit(89)
        output(text)
    if args == ['compose', 'version', '--short']:
        if config.get('compose_fail'): sys.exit(1)
        output(config.get('compose_version', '2.29.2'))
    if args[:1] == ['compose']:
        with (BASE / 'mutations.jsonl').open('a') as file:
            file.write(json.dumps({'args': args, 'env': dict(os.environ)}) + '\n')
        sys.exit(0)
    with (BASE / 'denied.jsonl').open('a') as file:
        file.write(json.dumps(args) + '\n')
    sys.exit(90)
if name == 'uname':
    output(config.get({'-s': 'host_os', '-m': 'host_arch', '-n': 'host_name'}[args[0]], {'-s': 'Linux', '-m': 'x86_64', '-n': 'fixture-node'}[args[0]]))
if name == 'findmnt':
    if config.get('findmnt_fail'): sys.exit(1)
    field = args[args.index('-o') + 1] if '-o' in args else args[args.index('-rno' if '-rno' in args else '-no') + 1]
    value = config.get({'FSTYPE': 'filesystem', 'MAJ:MIN': 'numbers', 'SOURCE': 'source', 'OPTIONS': 'mount_options'}[field], {'FSTYPE': 'ext4', 'MAJ:MIN': '8:0', 'SOURCE': '/dev/root', 'OPTIONS': 'rw,relatime'}[field])
    if field == 'MAJ:MIN' and not any(flag in args for flag in ('-r', '--raw', '-rno')):
        value = '  ' + value + ' '
    output(value)
if name == 'cat' and args == ['/sys/fs/cgroup/cgroup.controllers']:
    if config.get('cgroup_fail'): sys.exit(1)
    output(config.get('controllers', 'cpuset cpu io memory hugetlb pids rdma misc'))
if name == 'readlink' and args[-1].startswith('/sys/dev/block/'):
    if config.get('device_fail'): sys.exit(1)
    output('/sys/devices/fixture/block/sda/sda1' if config.get('partition') else '/sys/devices/fixture/block/sda')
if name == 'readlink' and args[-1].startswith('/dev/'):
    output(args[-1])
if name == 'readlink' and args[-1].startswith('/sys/class/block/'):
    if config.get('whole_disk_fail') or args[-1] == '/sys/class/block/root': sys.exit(1)
    output('/sys/devices/fixture/block/sda/sda1' if config.get('partition') and args[-1] == '/sys/class/block/sda1' else '/sys/devices/fixture/block/sda')
if name == 'stat' and args[-1].endswith('/partition'):
    if config.get('partition'): output('regular file')
    sys.exit(1)
if config.get('runtime') and name == 'stat' and args[-1] == '/var/run/docker.sock':
    output('socket')
if config.get('runtime') and name == 'readlink' and args[-1] == '/var/run/docker.sock':
    output(config['socket'])
if name == 'stat' and args[-1] in ('/sys/devices/fixture/block/sda', '/sys/devices/fixture/block/sda/sda1'):
    output('directory')
real()
'''


class ShellAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sb02-shell-fixture-')
        self.base = Path(self.temp.name)
        self.checkout = self.base / 'checkout with space'
        (self.checkout / 'deploy').mkdir(parents=True)
        for name in ('host-preflight.sh', 'compose.sh'):
            shutil.copy2(SOURCE / 'deploy' / name, self.checkout / 'deploy' / name)
        shutil.copy2(SOURCE / 'compose.yaml', self.checkout / 'compose.yaml')
        self.data = self.base / 'docker data'
        self.data.mkdir()
        (self.data / 'unchanged').write_text('fixture')
        self.sock = socket.socket(socket.AF_UNIX)
        self.socket_path = self.base / 'docker.sock'
        self.sock.bind(str(self.socket_path))
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name in ('docker', 'uname', 'findmnt', 'cat', 'readlink', 'stat'):
            target = self.bin / name
            target.write_text(FAKE.replace('__BASE__', repr(str(self.base))))
            target.chmod(0o755)
        self.config = {'socket': str(self.socket_path), 'info': {
            'OSType': 'linux', 'Architecture': 'x86_64', 'OperatingSystem': 'Ubuntu 24.04',
            'Name': 'fixture-node', 'ID': 'daemon-fixture', 'DockerRootDir': str(self.data),
            'CgroupVersion': '2', 'CgroupDriver': 'systemd', 'Driver': 'overlay2',
            'SecurityOptions': ['name=apparmor', 'name=seccomp,profile=builtin', 'name=cgroupns'],
            'DriverStatus': [['Backing Filesystem', 'extfs'], ['Supports d_type', 'true']],
            'ServerVersion': '29.8.2', 'DefaultRuntime': 'runc',
            **{name: True for name in ('MemoryLimit', 'SwapLimit', 'CpuCfsPeriod', 'CpuCfsQuota',
                                      'CPUShares', 'PidsLimit')}}}
        self.env = {'PATH': str(self.bin) + ':/usr/bin:/bin', 'LC_ALL': 'C',
                    'SBARBASE_DOCKER_DATA_ROOT': str(self.data),
                    'SBARBASE_DOCKER_SOCKET': str(self.socket_path),
                    'SBARBASE_ROOT': str(self.checkout), 'SBARBASE_COMPOSE_PROJECT': 'fixture'}
        self.before = self.tree()

    def tearDown(self):
        self.sock.close()
        self.temp.cleanup()

    def tree(self):
        return sorted((str(path.relative_to(self.base)), path.read_bytes() if path.is_file() else None)
                      for root in (self.checkout, self.data) for path in root.rglob('*'))

    def run_shell(self, command=None, changes=None, info=None, env=None, runtime=False):
        self.config.update(changes or {})
        self.config['info'].update(info or {})
        (self.base / 'fixture.json').write_text(json.dumps(self.config))
        arguments = [str(self.checkout / 'deploy' / ('compose.sh' if command else 'host-preflight.sh'))]
        if command:
            arguments += command
        if runtime:
            arguments += ['--runtime']
        result = subprocess.run(arguments, env=dict(self.env, **(env or {})), capture_output=True,
                                text=True, timeout=20, cwd=self.checkout)
        self.assertEqual(self.before, self.tree(), 'preflight or launcher changed checkout/data tree')
        self.assertFalse((self.base / 'denied.jsonl').exists(), 'unexpected Docker mutation attempted')
        return result

    def journal(self, name='queries.jsonl'):
        path = self.base / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def refusal(self, code, **kwargs):
        result = self.run_shell(['up'], **kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('[' + code + ']', result.stderr)
        self.assertIn('Action:', result.stderr)
        self.assertEqual(self.journal('mutations.jsonl'), [])

    def test_success_and_projection_before_compose_mutation(self):
        result = self.run_shell(['up'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('preflight passed; supported-profile acceptance unproven; production unproven', result.stdout)
        queries = [row['args'][2:] for row in self.journal() if row['tool'] == 'docker']
        self.assertEqual(queries[0][:2], ['info', '--format'])
        self.assertEqual(queries[1], ['compose', 'version', '--short'])
        mutation = self.journal('mutations.jsonl')[0]
        args = mutation['args']
        self.assertIn('/dev/null', args)
        self.assertEqual(args[args.index('-f') + 1], str(self.checkout / 'compose.yaml'))
        self.assertEqual(args[-3:], ['up', '--detach', '--build'])
        self.assertEqual(mutation['env']['SBARBASE_DOCKER_SOCKET'], str(self.socket_path))
        self.assertEqual(mutation['env']['SBARBASE_DOCKER_DATA_ROOT'], str(self.data))
        self.assertNotIn('HOME', mutation['env'])

    def test_missing_data_root_is_not_created(self):
        missing = self.base / 'missing' / 'docker'
        self.refusal('configured_path_unavailable', env={'SBARBASE_DOCKER_DATA_ROOT': str(missing)})
        self.assertFalse(missing.parent.exists())
        self.assertFalse(any(row['tool'] == 'docker' for row in self.journal()))

    def test_architecture_and_os_refuse_before_daemon_query(self):
        self.refusal('architecture_unvalidated', changes={'host_arch': 'aarch64'})
        self.assertFalse(any(row['tool'] == 'docker' for row in self.journal()))

    def test_remote_endpoint_refuses_before_daemon_query(self):
        self.refusal('local_unix_socket_required', env={'DOCKER_HOST': 'ssh://remote'})
        self.assertFalse(any(row['tool'] == 'docker' for row in self.journal()))

    def test_context_override_refuses_before_path_reads(self):
        self.refusal('docker_endpoint_override', env={'DOCKER_CONTEXT': 'remote'})
        self.assertEqual(self.journal(), [])

    def test_daemon_scope_negatives(self):
        cases = [('linux_daemon_required', {'OSType': 'windows'}),
                 ('daemon_architecture_unvalidated', {'Architecture': 'aarch64'}),
                 ('docker_desktop_unvalidated', {'OperatingSystem': 'Docker Desktop'}),
                 ('daemon_host_mismatch', {'Name': 'another-host'}),
                 ('daemon_identity_missing', {'ID': ''}),
                 ('docker_data_root_mismatch', {'DockerRootDir': str(self.checkout)}),
                 ('cgroup_v2_required', {'CgroupVersion': '1'}),
                 ('cgroup_driver_unvalidated', {'CgroupDriver': '<no value>'}),
                 ('admission_unproven', {'Driver': 'new-driver'}),
                 ('admission_unproven', {'DefaultRuntime': 'custom'}),
                 ('daemon_info_invalid', {'DefaultRuntime': ''}),
                 ('daemon_version_unvalidated', {'ServerVersion': '24.0.1'})]
        for code, change in cases:
            with self.subTest(code=code):
                original = dict(self.config['info'])
                self.refusal(code, info=change)
                self.config['info'] = original

    def test_daemon_identity_controls_and_malformed_tokens_refuse_before_mutation(self):
        cases = ('\t', '\r', '\x7f', '   ', 'daemon\tidentity', 'daemon identity',
                 '<no value>', '/daemon', '---', ':', 'daemon\ridentity')
        for identity in cases:
            with self.subTest(identity=repr(identity)):
                self.refusal('daemon_identity_invalid', info={'ID': identity})
                result = self.run_shell(info={'ID': identity})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[daemon_identity_invalid]', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])
        for identity in ('\n', 'daemon\njunk'):
            with self.subTest(identity=repr(identity)):
                self.refusal('daemon_info_invalid', info={'ID': identity})
                result = self.run_shell(info={'ID': identity})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[daemon_info_invalid]', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])

    def test_other_projection_text_controls_refuse_before_mutation(self):
        for field, value in (('OperatingSystem', 'Ubuntu\t24.04'), ('Name', 'fixture-node\r'),
                             ('Driver', 'overlayfs\x7f'), ('CgroupDriver', 'systemd\t'),
                             ('DefaultRuntime', 'runc\t'),
                             ('SecurityOptions', ['name=seccomp,profile=builtin', 'name=cgroupns\t']),
                             ('DriverStatus', [['Supports d_type', 'true\t']])):
            with self.subTest(field=field):
                original = dict(self.config['info'])
                self.refusal('daemon_info_invalid', info={field: value})
                result = self.run_shell(info={field: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[daemon_info_invalid]', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])
                self.config['info'] = original

    def test_missing_and_placeholder_os_refuse_in_host_and_runtime_modes(self):
        for value in ('','   ','<no value>','<nil>','null'):
            with self.subTest(value=repr(value)):
                self.refusal('daemon_info_invalid', info={'OperatingSystem':value})
                for runtime in (False,True):
                    result=self.run_shell(runtime=runtime,info={'OperatingSystem':value})
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('[daemon_info_invalid]',result.stderr)
                    self.assertEqual(self.journal('mutations.jsonl'),[])

    def test_default_security_is_exact(self):
        for options in ([], ['name=rootless'], ['name=selinux'], ['name=userns'],
                        ['name=apparmor', 'name=seccomp,profile=custom', 'name=cgroupns'],
                        ['name=apparmor', 'name=seccomp,profile=builtin'],
                        ['name=apparmor', 'name=seccomp,profile=builtin', 'name=cgroupns', 'unknown']):
            with self.subTest(options=options):
                self.refusal('security_profile_unvalidated', info={'SecurityOptions': options})

    def test_resource_features_fail_closed(self):
        for name in ('MemoryLimit', 'SwapLimit', 'CpuCfsPeriod', 'CpuCfsQuota', 'CPUShares', 'PidsLimit'):
            with self.subTest(name=name):
                self.refusal('resource_feature_unavailable', info={name: False})
                self.config['info'][name] = True
        self.refusal('daemon_info_invalid', info={'PidsLimit': '<no value>'})

    def test_controller_and_io_negatives(self):
        self.refusal('cgroup_controller_missing', changes={'controllers': 'cpu memory pids'})
        self.config.pop('controllers')
        self.refusal('io_device_unavailable', changes={'numbers': '0:41'})
        self.refusal('io_device_unavailable', changes={'numbers': '8:0', 'device_fail': True})

    def test_mount_device_numbers_request_raw_output_before_strict_validation(self):
        result = self.run_shell(changes={'numbers': '8:0'}, runtime=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        probes = [row['args'] for row in self.journal() if row['tool'] == 'findmnt' and 'MAJ:MIN' in row['args']]
        self.assertEqual(probes, [['-rno', 'MAJ:MIN', '--target', str(self.data)]])

    def test_raw_mount_numbers_still_refuse_zero_and_ambiguous_identity(self):
        for numbers in ('0:41', '8:0\n8:1', '0:41\njunk', '0:123456789012345678901', '', '  8:0 '):
            with self.subTest(numbers=numbers):
                result = self.run_shell(['up'], changes={'numbers': numbers})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[io_device_unavailable]', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])
                if numbers == '0:41':
                    self.assertIn('local-v1 mount block-device numbers: 0:41\n', result.stderr)
                else:
                    self.assertNotIn('local-v1 mount block-device numbers:', result.stderr)
                    self.assertNotIn(numbers or 'junk', result.stderr)

    def test_partition_requires_the_runtime_whole_disk_mapping(self):
        result = self.run_shell(changes={'partition': True})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.refusal('io_device_unavailable', changes={'whole_disk_fail': True})

    def test_canonical_aliases_and_root_directory_refusal(self):
        alias = self.base / 'data-alias'
        alias.symlink_to(self.data, target_is_directory=True)
        result = self.run_shell(['up'], env={'SBARBASE_DOCKER_DATA_ROOT': str(alias)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.journal('mutations.jsonl')[0]['env']['SBARBASE_DOCKER_DATA_ROOT'], str(self.data))
        (self.base / 'mutations.jsonl').unlink()
        self.refusal('invalid_absolute_path', env={'SBARBASE_DOCKER_DATA_ROOT': '/srv/..'})

    def test_modern_snapshotter_and_btrfs_candidate_is_not_a_support_claim(self):
        result = self.run_shell(changes={'filesystem': 'btrfs', 'numbers': '0:41',
                                         'source': '/dev/sda[/docker]'},
                                info={'Driver': 'overlayfs', 'DriverStatus': [['driver-type', 'io.containerd.snapshotter.v1']],
                                      'SecurityOptions': ['name=seccomp,profile=builtin', 'name=cgroupns']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'local-v1 preflight passed; supported-profile acceptance unproven; production unproven\n')
        self.assertFalse(any(row['tool'] == 'findmnt' and 'MAJ:MIN' in row['args'] for row in self.journal()))

    def test_unknown_filesystem_differs_from_missing_or_unavailable_capability(self):
        self.refusal('admission_unproven', changes={'filesystem': 'newfs'})
        self.refusal('filesystem_probe_invalid', changes={'filesystem': ''})
        self.refusal('filesystem_readonly', changes={'filesystem': 'ext4', 'mount_options': 'ro,relatime'})
        self.refusal('filesystem_probe_invalid', changes={'mount_options': 'relatime'})

    def test_modern_compose_major_is_candidate(self):
        result = self.run_shell(changes={'compose_version': '5.1.4'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_runtime_uses_the_declared_host_socket(self):
        result = self.run_shell(runtime=True, info={'Name': 'daemon-host'})
        self.assertEqual(result.returncode, 0, result.stderr)
        docker_calls = [row for row in self.journal() if row['tool'] == 'docker']
        self.assertEqual(docker_calls[0]['args'][1], 'unix://' + str(self.socket_path))

    def test_filesystem_and_xfs_dtype(self):
        self.refusal('filesystem_capability_unavailable', changes={'filesystem': 'nfs'})
        self.refusal('xfs_dtype_unavailable', changes={'filesystem': 'xfs'},
                     info={'DriverStatus': [['Supports d_type', 'false']]})
        self.config['info']['DriverStatus'] = [['Supports d_type', 'true']]
        result = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unavailable_and_malformed_daemon_diagnostics(self):
        self.refusal('docker_probe_unavailable', changes={'docker_fail': True})
        self.config.pop('docker_fail')
        for raw in ('', 'null', 'linux\nx86_64', 'garbage\n' * 23):
            with self.subTest(raw=raw):
                self.refusal('daemon_info_invalid', changes={'raw_info': raw})

    def test_compose_unavailable_and_invalid_version(self):
        self.refusal('compose_probe_unavailable', changes={'compose_fail': True})
        self.config.pop('compose_fail')
        for version in ('', 'garbage', '1.29.2', '2.19.0'):
            with self.subTest(version=version):
                self.refusal('compose_version_unvalidated', changes={'compose_version': version})

    def test_declared_inputs_and_fixed_arguments(self):
        for code, change in (('compose_override', {'COMPOSE_FILE': 'other.yml'}),
                             ('port_invalid', {'SBARBASE_CONSOLE_PORT': 'bad'}),
                             ('port_conflict', {'SBARBASE_DATABASE_PORT': '8790'}),
                             ('public_url_invalid', {'SBARBASE_PUBLIC_URL': 'https://user:password@host'}),
                             ('public_url_invalid', {'SBARBASE_PUBLIC_URL': 'https://host:bad'}),
                             ('public_url_invalid', {'SBARBASE_PUBLIC_URL': 'https://host:65536'}),
                             ('database_bind_invalid', {'SBARBASE_DATABASE_BIND': 'otherhost'}),
                             ('compose_project_invalid', {'SBARBASE_COMPOSE_PROJECT': '../other'})):
            with self.subTest(code=code): self.refusal(code, env=change)
        result = self.run_shell(['up', '--file', 'other.yml'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.journal('mutations.jsonl'), [])

    def test_env_file_and_ambient_inputs_cannot_cross_into_compose(self):
        (self.checkout / '.env').write_text('COMPOSE_FILE=other.yml\nSBARBASE_CONSOLE_PORT=invalid\n')
        self.before = self.tree()
        result = self.run_shell(['up'], env={'UNDECLARED_SECRET': 'fixture-secret'})
        self.assertEqual(result.returncode, 0, result.stderr)
        mutation = self.journal('mutations.jsonl')[0]
        self.assertNotIn('UNDECLARED_SECRET', mutation['env'])
        self.assertEqual(mutation['env']['SBARBASE_CONSOLE_PORT'], '8790')

    def test_all_startup_commands_are_gated(self):
        for command in ('check', 'up', 'build', 'pull', 'start', 'restart', 'bootstrap', 'smoke', 'upgrade'):
            with self.subTest(command=command):
                result = self.run_shell([command, 'v1.2.3'] if command == 'upgrade' else [command], changes={'host_arch': 'aarch64'})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.journal('mutations.jsonl'), [])

    def test_all_declared_scalars_reject_control_bytes_before_queries(self):
        names = ('SBARBASE_DOCKER_PROFILE', 'SBARBASE_CONTAINER', 'SBARBASE_DOCKER_DATA_ROOT',
                 'SBARBASE_DOCKER_SOCKET', 'SBARBASE_ROOT', 'SBARBASE_COMPOSE_PROJECT',
                 'COMPOSE_PROJECT_NAME', 'SBARBASE_CONSOLE_PORT', 'SBARBASE_DATABASE_PORT',
                 'SBARBASE_DATABASE_BIND', 'SBARBASE_PUBLIC_URL', 'SBARBASE_RELEASE_SOURCE',
                 'SBARBASE_BACKUP_HOUR', 'SBARBASE_BACKUP_KEEP', 'SBARBASE_UPLOAD_LIMIT_MB', 'TZ',
                 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY',
                 'DOCKER_CERT_PATH', 'DOCKER_API_VERSION', 'COMPOSE_FILE', 'COMPOSE_PROFILES',
                 'COMPOSE_ENV_FILES', 'COMPOSE_DISABLE_ENV_FILE')
        valid = dict(self.env, SBARBASE_DOCKER_PROFILE='local-v1', SBARBASE_CONSOLE_PORT='8790',
                     SBARBASE_DATABASE_PORT='6543', SBARBASE_DATABASE_BIND='127.0.0.1',
                     SBARBASE_PUBLIC_URL='https://example.com', SBARBASE_RELEASE_SOURCE='https://example.com/releases',
                     SBARBASE_BACKUP_HOUR='3', SBARBASE_BACKUP_KEEP='7', SBARBASE_UPLOAD_LIMIT_MB='50', TZ='UTC')
        for name in names:
            for control in ('\n', '\r', '\t'):
                for value in (valid.get(name, '') + control + 'junk', control + valid.get(name, ''),
                              valid.get(name, '') + control):
                    with self.subTest(name=name, control=repr(control), value=repr(value)):
                        self.refusal('deployment_scalar_invalid', env={name: value})
                        result = self.run_shell(env={name: value})
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('[deployment_scalar_invalid]', result.stderr)
        self.assertEqual(self.journal(), [], 'Malformed scalars reached a path or Docker query')

    def test_numeric_port_aliases_conflict_before_mutation(self):
        self.refusal('port_conflict', env={'SBARBASE_DATABASE_PORT': '08790'})

    def test_multiline_probe_records_refuse_before_mutation(self):
        for version in ('2.29.2\njunk', 'junk\n2.29.2', '2.29.2\n2.30.0'):
            with self.subTest(version=version):
                self.refusal('compose_version_unvalidated', changes={'compose_version': version})
        self.config.pop('compose_version')
        self.refusal('daemon_info_invalid', info={'ServerVersion': '29.8.2\njunk'})
        self.config['info']['ServerVersion'] = '29.8.2'
        self.refusal('io_device_unavailable', changes={'numbers': '8:0\njunk'})
        self.config.pop('numbers')
        self.refusal('filesystem_probe_invalid', changes={'source': '/dev/sda\njunk'})
        self.config.pop('source')
        self.refusal('filesystem_probe_invalid', changes={'mount_options': 'rw\njunk'})

    def test_bootstrap_smoke_upgrade_use_explicit_project_and_pinned_config(self):
        (self.checkout / '.env').write_text('COMPOSE_PROJECT_NAME=other\nCOMPOSE_FILE=other.yml\n')
        self.before = self.tree()
        operations = [(['bootstrap'], ['exec', 'sbarbase', 'python3', 'lab/bootstrap.py']),
                      (['smoke'], ['exec', '-T', 'sbarbase', 'python3', 'lab/install_server.py', 'smoke']),
                      (['upgrade', 'v1.2.3-rc.1'], ['exec', '-T', 'sbarbase', 'python3', 'lab/upgrade.py',
                                                    'start', '--release', 'v1.2.3-rc.1', '--allow-class', 'rebuild'])]
        for command, suffix in operations:
            with self.subTest(command=command):
                result = self.run_shell(command, env={'SBARBASE_COMPOSE_PROJECT': 'custom_project'})
                self.assertEqual(result.returncode, 0, result.stderr)
                mutation = self.journal('mutations.jsonl')[-1]
                args = mutation['args']
                self.assertEqual(args[-len(suffix):], suffix)
                self.assertEqual(args[args.index('--project-name') + 1], 'custom_project')
                self.assertEqual(args[args.index('--env-file') + 1], '/dev/null')
                self.assertEqual(args[args.index('-f') + 1], str(self.checkout / 'compose.yaml'))
                self.assertEqual(mutation['env']['COMPOSE_PROJECT_NAME'], 'custom_project')
                self.assertEqual(mutation['env']['DOCKER_HOST'], 'unix://' + str(self.socket_path))
                self.assertEqual(mutation['env']['SBARBASE_ROOT'], str(self.checkout))
                self.assertNotIn('COMPOSE_FILE', mutation['env'])

    def test_bootstrap_smoke_upgrade_reject_overrides_and_arbitrary_arguments(self):
        for command in (['bootstrap'], ['smoke'], ['upgrade', 'v1.2.3']):
            with self.subTest(command=command):
                result = self.run_shell(command, env={'COMPOSE_FILE': 'other.yml'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[compose_override]', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])
                result = self.run_shell(command + ['--file', 'other.yml'])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[invalid_arguments]', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])
        for tag in ('--file', 'v1.2', '1.2.3', 'v1.2.3 --other', 'v1.2.3\njunk', 'v1.2.3\r', 'v1.2.3\t'):
            with self.subTest(tag=repr(tag)):
                result = self.run_shell(['upgrade', tag])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Action:', result.stderr)
                self.assertEqual(self.journal('mutations.jsonl'), [])

    def test_runtime_skips_deployment_checks_and_compose_query(self):
        result = self.run_shell(runtime=True, changes={'runtime': True, 'compose_fail': True, 'mount_options': 'ro,relatime'},
                                info={'Name': 'daemon-host'}, env={'SBARBASE_CONTAINER': '1', 'SBARBASE_CONSOLE_PORT': 'bad'})
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [row['args'][2:] for row in self.journal() if row['tool'] == 'docker']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ['info', '--format'])
        self.assertEqual(self.journal('mutations.jsonl'), [])

    def test_cleanup_remains_possible_after_capability_failure(self):
        result = self.run_shell(['down'], changes={'host_arch': 'aarch64', 'docker_fail': True},
                                env={'SBARBASE_DOCKER_DATA_ROOT': str(self.base / 'absent')})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.journal('mutations.jsonl')[0]['args'][-1], 'down')
        self.assertFalse((self.base / 'absent').exists())


if __name__ == '__main__':
    unittest.main()
