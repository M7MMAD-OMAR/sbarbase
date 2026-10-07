"""Read-only validation of the local Linux Docker installation profile."""
from dataclasses import dataclass
import ast
import json
import os
import posixpath
import re
from pathlib import Path
import subprocess

DEFAULT_ROOT = '/var/lib/docker'
DEFAULT_SOCKET = '/var/run/docker.sock'
CONTROLLER_ENDPOINT = 'unix:///var/run/docker.sock'


class ProfileError(RuntimeError):
    def __init__(self, reason, detail=None):
        super().__init__(detail or ('Docker profile refused: ' + reason +
                         '. Action: check the declared local-v1 inputs with deploy/compose.sh check; '
                         'use the candidate described in docs/engineering/HOST-PREFLIGHT.md.'))
        self.reason = reason


@dataclass(frozen=True)
class DockerProfile:
    data_root: str
    socket: str
    container: bool
    project: str = ''


def normalized_path(value, reason='invalid_absolute_path'):
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//') or any(ord(c) < 32 for c in value):
        raise ProfileError(reason)
    result = posixpath.normpath(value)
    if result == '/':
        raise ProfileError(reason)
    return result


def validate_endpoint(endpoint, env=None):
    env = os.environ if env is None else env
    if env.get('DOCKER_CONTEXT') or env.get('DOCKER_TLS') or env.get('DOCKER_TLS_VERIFY'):
        raise ProfileError('docker_endpoint_override')
    if not isinstance(endpoint, str) or not endpoint.startswith('unix:///'):
        raise ProfileError('local_unix_socket_required')
    return 'unix://' + normalized_path(endpoint[7:], 'invalid_docker_socket')


def configured(env=None):
    env = os.environ if env is None else env
    return any(env.get(name) for name in ('SBARBASE_DOCKER_PROFILE', 'SBARBASE_CONTAINER',
                                         'SBARBASE_DOCKER_DATA_ROOT', 'SBARBASE_DOCKER_SOCKET'))


def from_environment(env=None):
    env = os.environ if env is None else env
    if env.get('SBARBASE_DOCKER_PROFILE') not in (None, '', 'local-v1'):
        raise ProfileError('unknown_docker_profile')
    if env.get('SBARBASE_CONTAINER') not in (None, '', '1'):
        raise ProfileError('invalid_container_marker')
    container = env.get('SBARBASE_CONTAINER') == '1'
    if container and env.get('SBARBASE_DOCKER_PROFILE') != 'local-v1':
        raise ProfileError('container_profile_required')
    root = normalized_path(env.get('SBARBASE_DOCKER_DATA_ROOT') or DEFAULT_ROOT, 'invalid_docker_data_root')
    socket = normalized_path(env.get('SBARBASE_DOCKER_SOCKET') or DEFAULT_SOCKET, 'invalid_docker_socket')
    if container:
        endpoint = validate_endpoint(env.get('DOCKER_HOST', ''), env)
        if endpoint != CONTROLLER_ENDPOINT:
            raise ProfileError('controller_endpoint_mismatch')
    else:
        endpoint = validate_endpoint(env.get('DOCKER_HOST') or 'unix://' + DEFAULT_SOCKET, env)
        if endpoint != 'unix://' + socket:
            raise ProfileError('host_endpoint_mismatch')
    return DockerProfile(root, socket, container, env.get('SBARBASE_COMPOSE_PROJECT', ''))


def validate_daemon(profile, info):
    if not isinstance(info, dict):
        raise ProfileError('daemon_info_invalid')
    if info.get('OSType') != 'linux':
        raise ProfileError('linux_daemon_required')
    if 'docker desktop' in str(info.get('OperatingSystem', '')).lower():
        raise ProfileError('docker_desktop_unvalidated')
    options = info.get('SecurityOptions', [])
    if not isinstance(options, list) or any(not isinstance(value, str) for value in options):
        raise ProfileError('daemon_security_options_invalid')
    if any('rootless' in value.lower() for value in options):
        raise ProfileError('rootless_daemon_unvalidated')
    if normalized_path(info.get('DockerRootDir'), 'daemon_data_root_missing') != profile.data_root:
        raise ProfileError('docker_data_root_mismatch')
    daemon_id = info.get('ID')
    if not isinstance(daemon_id, str) or not daemon_id.strip():
        raise ProfileError('daemon_identity_missing')
    return daemon_id


def controller_mounts(profile, info, expected_id):
    if not isinstance(info, dict):
        raise ProfileError('controller_inspection_invalid')
    config = info.get('Config')
    state = info.get('State')
    if not isinstance(config, dict) or not isinstance(state, dict):
        raise ProfileError('controller_inspection_invalid')
    labels = config.get('Labels')
    mounts = info.get('Mounts')
    if not isinstance(labels, dict) or not isinstance(mounts, list) or any(not isinstance(item, dict) for item in mounts):
        raise ProfileError('controller_inspection_invalid')
    if (info.get('Id') != expected_id or not isinstance(expected_id, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_id)
            or not profile.project or labels.get('com.docker.compose.project') != profile.project
            or labels.get('com.docker.compose.service') != 'sbarbase'
            or state.get('Running') is not True):
        raise ProfileError('controller_identity_mismatch')
    for source, target, readonly in ((profile.data_root, profile.data_root, True),
                                     (profile.socket, DEFAULT_SOCKET, False)):
        found = [item for item in mounts if item.get('Destination') == target]
        if len(found) != 1:
            raise ProfileError('controller_mount_missing_or_ambiguous')
        mount = found[0]
        if mount.get('Type') != 'bind' or normalized_path(mount.get('Source')) != source:
            raise ProfileError('controller_mount_source_mismatch')
        if mount.get('RW') is not (not readonly):
            raise ProfileError('controller_mount_access_mismatch')


def controller_security(info):
    """Refuse a controller started with an alternative security configuration."""
    host = info.get('HostConfig')
    if not isinstance(host, dict) or type(host.get('Privileged')) is not bool:
        raise ProfileError('controller_security_inspection_invalid')
    if (host['Privileged'] or host.get('SecurityOpt') not in (None, [])
            or host.get('CapAdd') not in (None, []) or host.get('CapDrop') not in (None, [])
            or host.get('Runtime') != 'runc' or host.get('UsernsMode') not in (None, '')
            or host.get('NetworkMode') != 'host' or host.get('CgroupnsMode') != 'host'
            or info.get('AppArmorProfile') not in ('', 'docker-default')):
        raise ProfileError('controller_security_profile_unvalidated')


def controller_checkout(info, checkout):
    checkout = normalized_path(checkout, 'checkout_path_invalid')
    mounts = info.get('Mounts', [])
    found = [item for item in mounts if item.get('Destination') == checkout]
    if (len(found) != 1 or found[0].get('Type') != 'bind'
            or normalized_path(found[0].get('Source')) != checkout
            or found[0].get('RW') is not True
            or info.get('Config', {}).get('WorkingDir') != checkout):
        raise ProfileError('controller_checkout_mismatch')


def controller_id(cgroup_text=None):
    text = Path('/proc/self/cgroup').read_text() if cgroup_text is None else cgroup_text
    identities = set()
    for line in text.splitlines():
        parts = line.split(':', 2)
        if len(parts) != 3:
            raise ProfileError('controller_cgroup_invalid')
        identities.update(re.findall(r'(?=(?:^|/)(?:docker/|docker-)([a-f0-9]{64})(?:\.scope)?(?:/|$))', parts[2]))
    if len(identities) != 1:
        raise ProfileError('controller_identity_ambiguous')
    return identities.pop()


def docker_command(*args):
    selected = from_environment()
    endpoint = CONTROLLER_ENDPOINT if selected.container else 'unix://' + selected.socket
    return ['docker', '--host', endpoint, *args]


def docker_json(*args):
    try:
        result = subprocess.run(docker_command(*args), capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProfileError('docker_probe_unavailable') from error
    if result.returncode:
        raise ProfileError('docker_probe_failed')
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise ProfileError('docker_probe_invalid') from error


def capability_script():
    """Use the baked admission contract after an update, or the checkout copy."""
    module = Path(__file__).resolve()
    if module.parent == Path('/usr/local/lib/sbarbase'):
        return module.with_name('host-preflight.sh')
    return module.parent.parent / 'deploy' / 'host-preflight.sh'


def validate_capabilities(profile):
    """The host launcher and runtime use the same read-only capability checks."""
    environment = dict(os.environ, SBARBASE_DOCKER_PROFILE='local-v1',
                       SBARBASE_DOCKER_DATA_ROOT=profile.data_root,
                       SBARBASE_DOCKER_SOCKET=profile.socket,
                       SBARBASE_CONTAINER='1' if profile.container else '',
                       DOCKER_HOST=CONTROLLER_ENDPOINT if profile.container else 'unix://' + profile.socket)
    try:
        result = subprocess.run(['/bin/sh', str(capability_script()), '--runtime'],
                                env=environment, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProfileError('host_capability_probe_unavailable') from error
    if result.returncode:
        # The shell emits its own safe diagnostic, never daemon stderr or user secrets.
        diagnostic = result.stderr.strip()
        match = re.fullmatch(r'Docker profile refused \[([a-z0-9_]+)\]: [^\n]+', diagnostic)
        if not match:
            raise ProfileError('host_capability_probe_invalid')
        raise ProfileError(match.group(1), diagnostic)
    if result.stdout.strip() != 'local-v1 preflight passed; supported-profile acceptance unproven; production unproven':
        raise ProfileError('host_capability_probe_invalid')


def require_or_exit():
    try:
        return require_supported()
    except ProfileError as error:
        raise SystemExit(str(error)) from None


def require_supported():
    """Reject before mutation, then pin subsequent Docker calls to this endpoint."""
    selected = from_environment()
    daemon_id = validate(selected)
    os.environ['DOCKER_HOST'] = CONTROLLER_ENDPOINT if selected.container else 'unix://' + selected.socket
    return daemon_id


def validated_identity(profile=None):
    """Return the daemon identity only after validating the mounted container profile."""
    profile = from_environment() if profile is None else profile
    validate_capabilities(profile)
    daemon_id = validate_daemon(profile, docker_json('info', '--format', '{{json .}}'))
    if profile.container:
        identity = controller_id()
        records = docker_json('inspect', identity)
        if not isinstance(records, list) or len(records) != 1:
            raise ProfileError('controller_identity_ambiguous')
        controller_mounts(profile, records[0], identity)
        controller_security(records[0])
        controller_checkout(records[0], os.environ.get('SBARBASE_ROOT') or str(Path.cwd()))
    if not Path(profile.data_root).is_dir():
        raise ProfileError('docker_data_root_unavailable')
    return daemon_id


def validate(profile=None):
    return validated_identity(profile)


def runtime_profile(profile, checkout):
    """Read a checkout capability declaration without importing its code.

    This marker declares compatibility; it cannot prove the runtime obeys the
    profile. The built image validator still verifies the actual daemon mounts.
    """
    target = Path(checkout) / 'lab' / 'resource_policy.py'
    try:
        tree = ast.parse(target.read_text(), filename=str(target))
    except (OSError, UnicodeError, SyntaxError, ValueError) as error:
        raise ProfileError('unsupported_runtime_profile') from error
    if profile.data_root == DEFAULT_ROOT:
        return
    declarations = [statement for statement in tree.body if isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)
                    and statement.targets[0].id == 'DOCKER_PROFILE_VERSION']
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
              and node.id == 'DOCKER_PROFILE_VERSION' and isinstance(node.ctx, (ast.Store, ast.Del))]
    if (len(declarations) != 1 or len(stores) != 1
            or not isinstance(declarations[0].value, ast.Constant)
            or declarations[0].value.value != 'local-v1'):
        raise ProfileError('unsupported_runtime_profile')


def main(argv=None):
    import sys
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ['check'] and not (len(arguments) == 2 and arguments[0] == 'runtime'):
        print('usage: docker_profile.py check | runtime <checkout>', file=sys.stderr)
        return 1
    try:
        selected = from_environment()
        if arguments[0] == 'check':
            validated_identity(selected)
        else:
            runtime_profile(selected, arguments[1])
    except (ProfileError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
