"""Bounded public-image configuration preservation, without database startup."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import image_identity
from run_checks import source_digest

REFERENCE = 'public.ecr.aws/supabase/postgres@sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86'
TARGET = '/etc/postgresql/postgresql.conf.d'
LINK = '/etc/postgresql-custom/conf.d'
ORIGINALS = ('auto_explain.conf', 'custom_overrides.conf', 'generated_optimizations.conf', 'pg_cron.conf', 'pg_net.conf')
OVERRIDE = 'zz-sbarbase-bootstrap.conf'
OVERRIDE_BYTES = b"pg_net.username='supabase_admin'\n"
DIRECTORIES = ('/etc', '/etc/postgresql', '/etc/postgresql-custom', TARGET)
STDOUT_LIMIT = 256 * 1024
STDERR_LIMIT = 64 * 1024
METADATA_LIMIT = 64 * 1024
DIAGNOSTICS = re.compile(rb'(?im)(?:\b(?:WARNING|ERROR|FATAL|PANIC):|^[\t ]*(?:WARNING|ERROR|FATAL|PANIC)\b)')
META_FORMAT = '%F|%a|%u|%g|%s|%i|%d|%h'

# Every pathname below is compiled from this closed manifest, never runtime input.
SHELL_COMMON = r'''set -eu
export LC_ALL=C
meta() { stat -c '%F|%a|%u|%g|%s|%i|%d|%h' -- "$1"; }
reg() {
    [ ! -L "$1" ]
    kind=$(stat -c '%F|%a|%u|%g|%h' -- "$1")
    case "$kind" in 'regular file|644|100|101|1'|'regular empty file|644|100|101|1') ;; *) exit 41;; esac
    size=$(stat -c '%s' -- "$1")
    [ "$size" -le 16384 ]
}
layout() {
    phase=$1
    present=$2
    for dir in /etc /etc/postgresql /etc/postgresql-custom /etc/postgresql/postgresql.conf.d; do
        [ ! -L "$dir" ]
        wanted='directory|755|100|101'
        [ "$dir" != /etc ] || wanted='directory|755|0|0'
        [ "$(stat -c '%F|%a|%u|%g' -- "$dir")" = "$wanted" ]
        printf 'D|%s|%s|' "$phase" "$dir"; meta "$dir"
    done
    [ -L /etc/postgresql-custom/conf.d ]
    [ "$(stat -c '%F|%a|%u|%g' -- /etc/postgresql-custom/conf.d)" = 'symbolic link|777|0|0' ]
    [ "$(readlink -- /etc/postgresql-custom/conf.d)" = /etc/postgresql/postgresql.conf.d ]
    printf 'L|%s|' "$phase"; meta /etc/postgresql-custom/conf.d
    printf 'T|%s|/etc/postgresql/postgresql.conf.d\n' "$phase"
    inventory=$(for item in /etc/postgresql/postgresql.conf.d/* /etc/postgresql/postgresql.conf.d/.[!.]* /etc/postgresql/postgresql.conf.d/..?*; do
        if [ -e "$item" ] || [ -L "$item" ]; then printf '%s\n' "${item##*/}"; fi
    done | sort)
    expected='auto_explain.conf
custom_overrides.conf
generated_optimizations.conf
pg_cron.conf
pg_net.conf'
    if [ "$present" = yes ]; then expected="$expected
zz-sbarbase-bootstrap.conf"; fi
    [ "$inventory" = "$expected" ]
    printf 'I|%s|%s\n' "$phase" "$(printf '%s' "$inventory" | tr '\n' ',')"
    total=0
    for base in auto_explain.conf custom_overrides.conf generated_optimizations.conf pg_cron.conf pg_net.conf; do
        reg "/etc/postgresql/postgresql.conf.d/$base"
        reg "/etc/postgresql-custom/conf.d/$base"
        size=$(stat -c '%s' -- "/etc/postgresql/postgresql.conf.d/$base")
        total=$((total + size))
        case "$base" in custom_overrides.conf|generated_optimizations.conf) [ "$size" = 0 ];; esac
        [ "$(meta "/etc/postgresql/postgresql.conf.d/$base")" = "$(meta "/etc/postgresql-custom/conf.d/$base")" ]
    done
    [ "$total" -le 81920 ]
    if [ "$present" = yes ]; then
        reg /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf
        reg /etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf
        [ "$(stat -c '%s' -- /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf)" = 33 ]
        [ "$(meta /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf)" = "$(meta /etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf)" ]
    else
        [ ! -e /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf ]
        [ ! -L /etc/postgresql/postgresql.conf.d/zz-sbarbase-bootstrap.conf ]
        [ ! -e /etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf ]
        [ ! -L /etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf ]
    fi
}
frame() {
    phase=$1; ordinal=$2; view=$3; path=$4
    reg "$path"
    before=$(meta "$path")
    size=$(stat -c '%s' -- "$path")
    printf 'B|%s|%s|%s|%s\n' "$phase" "$ordinal" "$view" "$before"
    head -c "$((size + 1))" -- "$path"
    printf '\nA|%s|%s|%s|%s\n' "$phase" "$ordinal" "$view" "$(meta "$path")"
    [ "$before" = "$(meta "$path")" ]
}
'''


def frame_commands(phase, present):
    names = ORIGINALS + ((OVERRIDE,) if present else ())
    lines = [f'layout {phase} {"yes" if present else "no"}']
    for ordinal, base in enumerate(names):
        for view, directory in (('target', TARGET), ('link', LINK)):
            lines.append(f'frame {phase} {ordinal} {view} {directory}/{base}')
    lines.append(f'layout {phase} {"yes" if present else "no"}')
    lines.append(f"printf 'E|{phase}\\n'")
    return '\n'.join(lines) + '\n'


SCRIPTS = {
    'baseline': SHELL_COMMON + r'''
# Native-tool compatibility is established on synthetic stdin before file reads.
probe=$(printf '\000\001\012\377' | od -An -v -tx1 -w1 -N 5)
[ "$probe" = ' 00
 01
 0a
 ff' ]
printf 'C|od-canonical-v1\n'
''' + frame_commands('baseline', False),
    'seed': SHELL_COMMON + frame_commands('before', False) + r'''
# Re-admit absence and every original immediately before exclusive creation.
layout before no
[ "$#" = 5 ]
for original in auto_explain.conf custom_overrides.conf generated_optimizations.conf pg_cron.conf pg_net.conf; do
    path="/etc/postgresql/postgresql.conf.d/$original"
    reg "$path"
    before=$(meta "$path")
    size=$(stat -c '%s' -- "$path")
    encoded=$(od -An -v -tx1 -w1 -N "$((size + 1))" -- "$path")
    [ "$encoded" = "$1" ]
    [ "$before" = "$(meta "$path")" ]
    shift
done
layout before no
umask 022
set -C
printf "pg_net.username='supabase_admin'\n" > /etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf
set +C
''' + frame_commands('after', True),
    'reader': SHELL_COMMON + frame_commands('reader', True),
}


def parse_meta(parts, *, regular=False):
    if len(parts) != 8:
        raise ValueError('Malformed metadata')
    kind, mode, uid, gid, size, inode, device, links = parts
    if any(not re.fullmatch(r'[0-9]+', value) for value in parts[1:]):
        raise ValueError('Nonnumeric metadata')
    value = dict(native_type=kind, mode=mode, uid=int(uid), gid=int(gid), size=int(size),
                 inode=int(inode), device=int(device), nlink=int(links))
    if regular and (kind not in ('regular file', 'regular empty file') or mode != '644'
                    or value['uid'] != 100 or value['gid'] != 101 or value['nlink'] != 1
                    or value['size'] > 16384 or value['inode'] <= 0):
        raise ValueError('Original regular metadata differs')
    if kind == 'regular empty file' and value['size'] != 0:
        raise ValueError('Empty native type has nonzero size')
    return value


def decode_frames(raw, phase, present, *, start_offset=0, allow_trailing=False):
    """Strict closed-manifest framing; consumes exactly one phase, no trailing data."""
    names = ORIGINALS + ((OVERRIDE,) if present else ())
    if not isinstance(start_offset, int) or not 0 <= start_offset <= len(raw):
        raise ValueError('Invalid structural cursor')
    offset = start_offset
    metadata_bytes = 0
    frames = {}
    layouts = []

    def line():
        nonlocal offset, metadata_bytes
        end = offset
        limit = min(len(raw), offset + 4097)
        while end < limit and raw[end] != 10:
            end += 1
        if end == limit or end - offset > 4096:
            raise ValueError('Missing or oversized frame line')
        value = raw[offset:end].decode('ascii', errors='strict')
        metadata_bytes += end + 1 - offset
        if metadata_bytes > METADATA_LIMIT:
            raise ValueError('Metadata ceiling exceeded')
        offset = end + 1
        return value.split('|')

    def layout():
        dirs = {}
        for path in DIRECTORIES:
            fields = line()
            if fields[:3] != ['D', phase, path]:
                raise ValueError('Directory order differs')
            meta = parse_meta(fields[3:])
            if (meta['native_type'] != 'directory' or meta['mode'] != '755'
                    or (meta['uid'], meta['gid']) != ((0, 0) if path == '/etc' else (100, 101))):
                raise ValueError('Parent directory differs')
            dirs[path] = meta
        fields = line()
        if fields[:2] != ['L', phase]:
            raise ValueError('Link metadata missing')
        link = parse_meta(fields[2:])
        if (link['native_type'], link['mode'], link['uid'], link['gid']) != ('symbolic link', '777', 0, 0):
            raise ValueError('Literal link differs')
        if line() != ['T', phase, TARGET] or line() != ['I', phase, ','.join(names)]:
            raise ValueError('Target or complete inventory differs')
        layouts.append({'directories': dirs, 'link': link, 'inventory': list(names)})

    layout()
    initial_layout_end = offset
    total = 0
    for ordinal, base in enumerate(names):
        for view in ('target', 'link'):
            fields = line()
            prefix = ['B', phase, str(ordinal), view]
            if fields[:4] != prefix:
                raise ValueError('Frame ordinal or view differs')
            before = parse_meta(fields[4:], regular=True)
            size = before['size']
            if base in ORIGINALS[1:3] and size != 0:
                raise ValueError('Template is nonempty')
            if base == OVERRIDE and size != 33:
                raise ValueError('Override size differs')
            if view == 'target' and base != OVERRIDE:
                total += size
            data = raw[offset:offset + size]
            offset += size
            if len(data) != size or raw[offset:offset + 1] != b'\n':
                raise ValueError('Short or excess content frame')
            offset += 1
            fields = line()
            if fields[:4] != ['A', phase, str(ordinal), view]:
                raise ValueError('Post-read frame differs')
            after = parse_meta(fields[4:], regular=True)
            if before != after:
                raise ValueError('Metadata changed during read')
            if base == OVERRIDE and data != OVERRIDE_BYTES:
                raise ValueError('Override bytes differ')
            frames[(base, view)] = {'metadata': before, 'bytes': data,
                                   'sha256': hashlib.sha256(data).hexdigest()}
        if frames[(base, 'target')] != frames[(base, 'link')]:
            raise ValueError('Target and link views differ')
    if total > 81920:
        raise ValueError('Original byte total exceeds ceiling')
    layout()
    # Creating the override may change directory size, but it happens before this phase.
    if layouts[0] != layouts[1]:
        raise ValueError('Layout changed during phase')
    if line() != ['E', phase] or (not allow_trailing and offset != len(raw)):
        raise ValueError('Unexpected trailing frames')
    return {'frames': frames, 'layouts': layouts, 'metadata_bytes': metadata_bytes,
            'consumed_offset': offset, 'initial_layout_end': initial_layout_end}


def decode_helper(raw, role):
    if len(raw) > STDOUT_LIMIT:
        raise ValueError('Helper stdout ceiling exceeded')
    if role != 'seed':
        if role == 'baseline':
            marker = b'C|od-canonical-v1\n'
            if not raw.startswith(marker):
                raise ValueError('Native canonical encoding compatibility missing')
            raw = raw[len(marker):]
        return {role: decode_frames(raw, role, role == 'reader')}
    first = decode_frames(raw, 'before', False, allow_trailing=True)
    cursor = first['consumed_offset']
    # These exact layout bytes end at the structurally consumed first layout.
    # Binary payloads have already been consumed solely by admitted lengths.
    expected_layout = raw[:first['initial_layout_end']]
    repeated_layout_bytes = 0
    for _ in range(2):
        end = cursor + len(expected_layout)
        if raw[cursor:end] != expected_layout:
            raise ValueError('Immediate pre-write admission changed')
        cursor = end
        repeated_layout_bytes += len(expected_layout)
    second = decode_frames(raw, 'after', True, start_offset=cursor)
    if first['metadata_bytes'] + repeated_layout_bytes + second['metadata_bytes'] > METADATA_LIMIT:
        raise ValueError('Combined metadata ceiling exceeded')
    return {'before': first, 'after': second}


def compare_originals(baseline, observed):
    for base in ORIGINALS:
        expected = baseline['frames'][(base, 'target')]
        actual = observed['frames'][(base, 'target')]
        if expected['bytes'] != actual['bytes'] or expected['sha256'] != actual['sha256']:
            raise ValueError('Original bytes changed: ' + base)
        for key in ('mode', 'uid', 'gid', 'size', 'nlink', 'native_type'):
            if expected['metadata'][key] != actual['metadata'][key]:
                raise ValueError('Original metadata changed: ' + base)


def bounded_capture(args, timeout, stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT, *, data=None):
    """Drain both pipes incrementally and stop before retaining an excess byte."""
    if data is not None and (not isinstance(data, bytes) or len(data) > 65536):
        raise ValueError('Public stdin must be bytes within 64 KiB')
    started = time.monotonic()
    written = 0
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    reason = None
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE if data is not None else None)
    with selectors.DefaultSelector() as selector:
        for stream, key in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, key)
        if data is not None:
            os.set_blocking(proc.stdin.fileno(), False)
            if data:
                selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
            else:
                proc.stdin.close()
        try:
            while selector.get_map():
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    reason = 'timeout'
                    break
                for event, _ in selector.select(min(remaining, .1)):
                    key = event.data
                    if key == 'stdin':
                        try:
                            written += os.write(event.fileobj.fileno(), data[written:written + 8192])
                        except BrokenPipeError:
                            reason = 'premature stdin close'
                            break
                        if written == len(data):
                            selector.unregister(event.fileobj)
                            event.fileobj.close()
                        continue
                    limit = stdout_limit if key == 'stdout' else stderr_limit
                    chunk = os.read(event.fileobj.fileno(), min(8192, limit - len(buffers[key]) + 1))
                    if not chunk:
                        selector.unregister(event.fileobj)
                        continue
                    capacity = limit - len(buffers[key])
                    buffers[key].extend(chunk[:capacity])
                    if len(chunk) > capacity:
                        reason = key + ' limit'
                        break
                if reason:
                    break
            if reason:
                proc.kill()
            try:
                proc.wait(timeout=max(.01, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                reason = 'timeout'
                proc.kill()
                proc.wait(timeout=1)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1)
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
    return {'stdout': bytes(buffers['stdout']), 'stderr': bytes(buffers['stderr']),
            'exit_code': proc.returncode, 'refusal': reason,
            'duration_seconds': time.monotonic() - started}


def admit_native_result(result):
    if result['refusal']:
        raise RuntimeError('Native streaming refusal: ' + result['refusal'])
    if result['exit_code'] or result['stderr'] or DIAGNOSTICS.search(result['stdout']):
        raise RuntimeError('Native command diagnosed or failed')


def admit_absence(result, args):
    if result['refusal']:
        raise RuntimeError('Absence capture refused')
    identity = args[-1].encode()
    if args[1:3] == ['volume', 'inspect']:
        wanted = b'Error response from daemon: get ' + identity + b': no such volume\n'
    elif args[1:3] == ['network', 'inspect']:
        wanted = b'Error response from daemon: network ' + identity + b' not found\n'
    elif args[1:3] == ['container', 'inspect']:
        wanted = b'Error response from daemon: No such container: ' + identity + b'\n'
    else:
        raise ValueError('Unreviewed absence command')
    if result['exit_code'] != 1 or result['stdout'] != b'[]\n' or result['stderr'] != wanted:
        raise RuntimeError('Exact resource absence not established')


def admit_volume(item, name, owner):
    if (item.get('Name') != name or item.get('Driver') != 'local' or item.get('Options')
            or item.get('Labels') != {'io.sbarbase.owner': owner} or item.get('Scope') != 'local'):
        raise ValueError('Volume admission differs')


def expected_mounts(role, volumes):
    if role == 'baseline':
        return []
    return [{'Type': 'volume', 'Name': volumes[0], 'Destination': '/etc/postgresql-custom', 'RW': False},
            {'Type': 'volume', 'Name': volumes[1], 'Destination': TARGET, 'RW': role == 'seed'}]


def admit_container(item, cid, name, owner, image, role, volumes, terminal=False, baseline_arguments=()):
    config, host = item['Config'], item['HostConfig']
    health = dict(image['Config'].get('Healthcheck') or {})
    health['Test'] = ['NONE']
    if (item.get('Id') != cid or not re.fullmatch(r'[a-f0-9]{64}', cid)
            or item.get('Name') != '/' + name or item.get('Image') != image['Id']
            or config.get('Labels', {}).get('io.sbarbase.owner') != owner
            or config.get('User') != '100:101' or config.get('Entrypoint') != ['/bin/sh']
            or config.get('Cmd') != helper_command(role, baseline_arguments) or config.get('Volumes')
            or config.get('Healthcheck') != health
            or config.get('ExposedPorts') != image['Config'].get('ExposedPorts')
            or image['Config'].get('ExposedPorts') != {'5432/tcp': {}}
            or host.get('Binds') or host.get('Tmpfs') or host.get('PortBindings')
            or host.get('PublishAllPorts') or host.get('Privileged')
            or host.get('NetworkMode') != 'none' or host.get('ReadonlyRootfs') is not True
            or host.get('PidMode') or host.get('UTSMode') or host.get('IpcMode') != 'private'
            or host.get('Devices') or host.get('DeviceRequests') or host.get('VolumesFrom')
            or host.get('Memory') != 67108864 or host.get('MemorySwap') != 67108864
            or host.get('NanoCpus') != 250000000 or host.get('PidsLimit') != 16
            or host.get('CapAdd') or host.get('CapDrop') != ['ALL']
            or host.get('SecurityOpt') != ['no-new-privileges']
            or host.get('RestartPolicy') != {'Name': 'no', 'MaximumRetryCount': 0}
            or host.get('AutoRemove') or host.get('ExtraHosts') or host.get('Links')
            or set(item.get('NetworkSettings', {}).get('Networks', {})) != {'none'}
            or set(item.get('NetworkSettings', {}).get('Ports') or {}) - {'5432/tcp'}
            or any(v is not None for v in (item.get('NetworkSettings', {}).get('Ports') or {}).values())):
        raise ValueError('Container isolation or identity differs')
    actual = item.get('Mounts') or []
    wanted = expected_mounts(role, volumes)
    if len(actual) != len(wanted):
        raise ValueError('Mount count differs')
    for expected in wanted:
        matches = [mount for mount in actual if mount.get('Destination') == expected['Destination']]
        if len(matches) != 1 or any(matches[0].get(k) != v for k, v in expected.items()):
            raise ValueError('Mount identity or access differs')
        if matches[0].get('Driver') != 'local' or matches[0].get('Propagation'):
            raise ValueError('Mount driver or propagation differs')
    declared = host.get('Mounts') or []
    if len(declared) != len(wanted):
        raise ValueError('Declared mount count differs')
    for expected in wanted:
        matches = [m for m in declared if m.get('Target') == expected['Destination']]
        if (len(matches) != 1 or matches[0].get('Type') != 'volume'
                or matches[0].get('Source') != expected['Name']
                or bool(matches[0].get('ReadOnly', False)) == expected['RW']
                or (matches[0].get('VolumeOptions') or {}).get('NoCopy', False)
                or matches[0].get('BindOptions') or matches[0].get('TmpfsOptions')):
            raise ValueError('Declared volume options differ')
    state = item['State']
    if terminal:
        if (state.get('Status') != 'exited' or state.get('ExitCode') != 0 or state.get('OOMKilled')
                or state.get('Error') or state.get('Running') or state.get('Restarting')):
            raise ValueError('Terminal helper state differs')
    elif state.get('Status') != 'created' or state.get('Running') or state.get('OOMKilled') or state.get('Error'):
        raise ValueError('Helper was not admitted before start')


def encode_public_argument(data):
    return '\n'.join(' ' + format(value, '02x') for value in data)


def decode_public_argument(encoded):
    if not isinstance(encoded, str) or (encoded and not re.fullmatch(r' [a-f0-9]{2}(?:\n [a-f0-9]{2})*', encoded)):
        raise ValueError('Noncanonical baseline encoding')
    data = bytes.fromhex(encoded)
    if encode_public_argument(data) != encoded:
        raise ValueError('Baseline encoding differs')
    return data


def helper_command(role, baseline_arguments=()):
    if role != 'seed':
        if baseline_arguments:
            raise ValueError('Baseline bytes only apply to seed')
        return ['-ec', SCRIPTS[role]]
    if len(baseline_arguments) != 5:
        raise ValueError('Seed requires exactly five baseline public-byte arguments')
    total = 0
    for ordinal, encoded in enumerate(baseline_arguments):
        data = decode_public_argument(encoded)
        if len(data) > 16384 or (ordinal in (1, 2) and data):
            raise ValueError('Baseline argument exceeds manifest limits')
        total += len(data)
    if total > 81920:
        raise ValueError('Baseline argument total exceeds ceiling')
    return ['-ec', SCRIPTS[role], 'configpreserve', *baseline_arguments]


def create_arguments(name, owner, role, volumes, baseline_arguments=()):
    args = ['docker', 'create', '--pull=never', '--name', name, '--label', 'io.sbarbase.owner=' + owner,
            '--network', 'none', '--read-only', '--user', '100:101', '--no-healthcheck',
            '--memory', '64m', '--memory-swap', '64m', '--cpus', '.25', '--pids-limit', '16',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', '/bin/sh']
    for mount in expected_mounts(role, volumes):
        value = 'type=volume,source=' + mount['Name'] + ',target=' + mount['Destination']
        if not mount['RW']:
            value += ',readonly'
        args += ['--mount', value]
    return args + [REFERENCE] + helper_command(role, baseline_arguments)


def _preserve(fixture, source_sha256, *, bootstrap_preparation=False, outer_work_deadline=None, outer_deadline=None):
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    output = Path('/evidence/configuration-preservation' if bootstrap_preparation else '/evidence')
    output.mkdir(exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Owned evidence directory must be empty')
    owner = 'sbarbase-fixture-' + fixture
    names = {role: fixture + '-configpreserve-' + role for role in SCRIPTS}
    volumes = [fixture + ('-defaults-config' if bootstrap_preparation else '-bootstrap-config'), fixture + '-bootstrap-confdir']
    report = {'scope': 'original-image-config-preservation-independent-visibility',
              'source_sha256': source_sha256, 'passed': False, 'commands': [], 'cleanup': [],
              'helper_names': names, 'volume_names': volumes, 'owner_label': owner,
              'limitations': ['No PostgreSQL startup, effective SQL setting, data, keys or authentication.',
                              'No bootstrap, restore, interruption-cleanup or release acceptance.']}
    deadline = time.monotonic() + 180
    work_deadline = deadline - 60
    if outer_deadline is not None:
        deadline = min(deadline, outer_deadline)
        work_deadline = min(work_deadline, outer_work_deadline)
    cids = {}
    attempted_names = []
    attempted_volumes = []
    image = None
    step = 'preexisting resource refusal'
    transfer_ready = False

    def native(args, *, allow_absence=False, cleanup=False):
        entry = {'args': args, 'args_sha256': hashlib.sha256(json.dumps(args, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest(), 'started_at_unix_seconds': time.time()}
        report['commands'].append(entry)
        budget = min(8, deadline - time.monotonic()) if cleanup else min(30, work_deadline - time.monotonic())
        if budget <= 0:
            entry['refusal'] = 'overall deadline'
            raise RuntimeError('Overall deadline exceeded')
        result = bounded_capture(args, budget)
        ordinal = len(report['commands'])
        for key in ('stdout', 'stderr'):
            artifact = f'command-{ordinal:03d}-{key}.bin'
            (output / artifact).write_bytes(result[key])
            entry[key + '_artifact'] = artifact
            entry[key + '_bytes'] = len(result[key])
            entry[key + '_sha256'] = hashlib.sha256(result[key]).hexdigest()
        entry.update({k: result[k] for k in ('exit_code', 'refusal', 'duration_seconds')})
        if allow_absence:
            admit_absence(result, args)
            entry['recognized_absence'] = True
            return None
        admit_native_result(result)
        return result['stdout']

    def inspect(identity, kind=None, cleanup=False):
        args = ['docker'] + (['volume', 'inspect'] if kind == 'volume' else ['inspect']) + [identity]
        return image_identity.record(native(args, cleanup=cleanup).decode('utf-8', errors='strict'))

    def remove_container(role, cleanup=False):
        cid = cids.get(role)
        if cid:
            item = inspect(cid, cleanup=cleanup)
        else:
            # Creation may have succeeded without returning its CID. Exact-name
            # lookup requires label/name/image admission before any removal.
            item = inspect(names[role], cleanup=cleanup)
            cid = item['Id']
        if (item.get('Name') != '/' + names[role] or item.get('Image') != image['Id']
                or item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != owner
                or not re.fullmatch(r'[a-f0-9]{64}', cid)):
            raise ValueError('Cleanup helper ownership differs')
        report.setdefault('helper_cids', {})[role] = cid
        native(['docker', 'rm', '-f', cid], cleanup=cleanup)
        cids.pop(role, None)
        attempted_names.remove(role)
        report['cleanup'].append({'kind': 'container', 'name': names[role], 'identity': cid, 'passed': True})

    def retain(phase, parsed):
        value = {'layouts': parsed['layouts'], 'metadata_bytes': parsed['metadata_bytes'], 'files': []}
        for (base, view), frame in parsed['frames'].items():
            artifact = phase + '-' + view + '-' + base + '.bin'
            (output / artifact).write_bytes(frame['bytes'])
            value['files'].append({'basename': base, 'view': view, 'metadata': frame['metadata'],
                                   'size': len(frame['bytes']), 'sha256': frame['sha256'], 'artifact': artifact})
        report.setdefault('phases', {})[phase] = value

    try:
        for name in names.values():
            native(['docker', 'container', 'inspect', name], allow_absence=True)
        for name in volumes:
            native(['docker', 'volume', 'inspect', name], allow_absence=True)
        step = 'image admission'
        image = image_identity.record(native(['docker', 'image', 'inspect', REFERENCE]).decode())
        image_identity.resolved_id(REFERENCE, image)
        if image['Config'].get('Volumes'):
            raise ValueError('Image declares anonymous volumes')
        report.update(image=image, reference=REFERENCE)
        step = 'volume creation'
        for name in volumes:
            attempted_volumes.append(name)
            report['attempted_volumes'] = list(attempted_volumes)
            created = native(['docker', 'volume', 'create', '--driver', 'local', '--label', 'io.sbarbase.owner=' + owner, name])
            if created != (name + '\n').encode():
                raise ValueError('Volume creation identity differs')
            item = inspect(name, 'volume')
            admit_volume(item, name, owner)
            report.setdefault('volumes_created', []).append(item)
        phases = {}
        for role in SCRIPTS:
            step = role + ' create admission'
            attempted_names.append(role)
            baseline_arguments = tuple(encode_public_argument(phases['baseline']['frames'][(base, 'target')]['bytes']) for base in ORIGINALS) if role == 'seed' else ()
            cid = native(create_arguments(names[role], owner, role, volumes, baseline_arguments)).decode().strip()
            if not re.fullmatch(r'[a-f0-9]{64}', cid):
                raise ValueError('Created CID differs')
            cids[role] = cid
            report.setdefault('helper_cids', {})[role] = cid
            item = inspect(cid)
            report.setdefault('helpers_created', {})[role] = item
            admit_container(item, cid, names[role], owner, image, role, volumes, baseline_arguments=baseline_arguments)
            step = role + ' bounded observation'
            raw = native(['docker', 'start', '--attach', cid])
            parsed = decode_helper(raw, role)
            for phase, observation in parsed.items():
                retain(phase, observation)
                phases[phase] = observation
            final = inspect(cid)
            report.setdefault('helpers_final', {})[role] = final
            admit_container(final, cid, names[role], owner, image, role, volumes, terminal=True, baseline_arguments=baseline_arguments)
            if role == 'seed':
                compare_originals(phases['baseline'], phases['before'])
                compare_originals(phases['baseline'], phases['after'])
                for base in ORIGINALS:
                    if phases['before']['frames'][(base, 'target')] != phases['after']['frames'][(base, 'target')]:
                        raise ValueError('Seed changed an original object')
                remove_container(role)
            elif role == 'reader':
                compare_originals(phases['baseline'], phases['reader'])
                for base in ORIGINALS + (OVERRIDE,):
                    if phases['after']['frames'][(base, 'target')] != phases['reader']['frames'][(base, 'target')]:
                        raise ValueError('Independent shared-volume object differs')
            else:
                remove_container(role)
        if bootstrap_preparation:
            remove_container('reader')
            for role, cid in report['helper_cids'].items():
                native(['docker', 'container', 'inspect', cid], allow_absence=True)
                native(['docker', 'container', 'inspect', names[role]], allow_absence=True)
            transfer_ready = True
        if time.monotonic() > work_deadline:
            raise RuntimeError('Main work budget exceeded')
        report['passed'] = True
    except Exception as error:
        report.update(error=type(error).__name__ + ': ' + str(error), failed_step=step)
    finally:
        for role in list(reversed(attempted_names)):
            try:
                remove_container(role, cleanup=True)
            except Exception as error:
                report['passed'] = False
                report['cleanup'].append({'kind': 'container', 'name': names[role], 'passed': False, 'error': str(error)})
        # Volumes can only be removed once every attempted consumer is gone.
        if not attempted_names and not (bootstrap_preparation and transfer_ready and report['passed']):
            for name in reversed(attempted_volumes):
                try:
                    item = inspect(name, 'volume', cleanup=True)
                    admit_volume(item, name, owner)
                    native(['docker', 'volume', 'rm', name], cleanup=True)
                    report['cleanup'].append({'kind': 'volume', 'name': name, 'passed': True})
                except Exception as error:
                    report['passed'] = False
                    report['cleanup'].append({'kind': 'volume', 'name': name, 'passed': False, 'error': str(error)})
        elif attempted_names and attempted_volumes:
            report['passed'] = False
            report['cleanup'].append({'kind': 'volumes', 'passed': False, 'error': 'Consumer cleanup not established'})
        if time.monotonic() > deadline:
            report['passed'] = False
            report['deadline_error'] = 'Whole fragment deadline exceeded'
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if bootstrap_preparation:
        if not report['passed']:
            raise RuntimeError('Configuration preparation failed: ' + report.get('error', 'cleanup uncertainty'))
        return BootstrapConfigReceipt(fixture, owner, REFERENCE, image['Id'], source_sha256,
                                      tuple(volumes), str(output),
                                      json.dumps(report['phases'], sort_keys=True).encode())
    print(json.dumps({key: report.get(key) for key in ('scope', 'source_sha256', 'passed', 'error')}))
    return 0 if report['passed'] else 1


@dataclass(frozen=True)
class BootstrapConfigReceipt:
    fixture: str
    owner: str
    reference: str
    image_id: str
    source_sha256: str
    volumes: tuple[str, str]
    evidence_directory: str
    phases_json: bytes


def prepare_bootstrap_config(fixture, source_sha256, outer_work_deadline, outer_deadline):
    if source_digest() != source_sha256:
        raise ValueError('Baked source handoff differs')
    return _preserve(fixture, source_sha256, bootstrap_preparation=True,
                     outer_work_deadline=outer_work_deadline, outer_deadline=outer_deadline)


def main():
    return _preserve(os.environ['SBARBASE_FIXTURE_ID'], source_digest())


if __name__ == '__main__':
    raise SystemExit(main())
