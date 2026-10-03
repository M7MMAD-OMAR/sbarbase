"""Inspect only the pinned image's literal configuration layout metadata."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab'))
import image_identity
from run_checks import source_digest

TARGET = '/etc/postgresql/postgresql.conf.d'
LINK = '/etc/postgresql-custom/conf.d'
TEMP = LINK + '/zz-sbarbase-bootstrap.conf'
DIRECTORIES = ['/etc', '/etc/postgresql', '/etc/postgresql-custom', TARGET]
DIAGNOSTICS = re.compile(
    rb'(?im)(?:\b(?:WARNING|ERROR|FATAL|PANIC):|^[\t ]*(?:WARNING|ERROR|FATAL|PANIC)\b)')
SCRIPT = r'''set -eu
export LC_ALL=C
emit() {
    metadata=$(stat -c '%F|%a|%u|%g' -- "$1")
    [ "${#metadata}" -le 128 ] || exit 41
    printf '%s\000%s\000%s\000' "$1" "$metadata" "$2"
}
for path in /etc /etc/postgresql /etc/postgresql-custom /etc/postgresql/postgresql.conf.d; do
    [ ! -L "$path" ] && [ -d "$path" ] || exit 42
    emit "$path" ''
done
path=/etc/postgresql-custom/conf.d
[ -L "$path" ] || exit 43
target=$(readlink -- "$path")
[ "$target" = /etc/postgresql/postgresql.conf.d ] || exit 44
emit "$path" "$target"
path=/etc/postgresql-custom/conf.d/zz-sbarbase-bootstrap.conf
[ ! -e "$path" ] && [ ! -L "$path" ] || exit 45
printf '%s\000absent\000\000' "$path"
count=0
for path in /etc/postgresql/postgresql.conf.d/* /etc/postgresql/postgresql.conf.d/.[!.]* /etc/postgresql/postgresql.conf.d/..?*; do
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then continue; fi
    count=$((count + 1))
    [ "$count" -le 64 ] || exit 46
    basename=${path##*/}
    [ "${#basename}" -le 128 ] || exit 47
    case "$basename" in ''|*[!A-Za-z0-9_.-]*|..|.) exit 48;; esac
    [ ! -L "$path" ] && [ -f "$path" ] || exit 49
    emit "$path" ''
done
[ "$count" -gt 0 ] || exit 50
'''


def decode_metadata(raw):
    if not raw or len(raw) > 65536 or not raw.endswith(b'\0'):
        raise RuntimeError('Metadata byte boundary differs')
    fields = raw[:-1].decode('ascii', errors='strict').split('\0')
    if len(fields) % 3 or len(fields) // 3 > 70:
        raise RuntimeError('Metadata record boundary differs')
    records = []
    seen = set()
    for index in range(0, len(fields), 3):
        path, metadata, link = fields[index:index + 3]
        if path in seen:
            raise RuntimeError('Duplicate metadata pathname')
        seen.add(path)
        record = {'path': path, 'literal_link_target': link}
        if path == TEMP:
            if metadata != 'absent' or link:
                raise RuntimeError('Temporary pathname is not absent')
            record['type'] = 'absent'
        else:
            match = re.fullmatch(r'(directory|symbolic link|regular file|regular empty file)\|([0-7]{1,4})\|([0-9]+)\|([0-9]+)', metadata)
            if not match:
                raise RuntimeError('Unexpected non-following stat metadata')
            record.update(native_type=match[1],
                          type='regular file' if match[1] == 'regular empty file' else match[1],
                          mode=match[2], uid=int(match[3]), gid=int(match[4]))
        records.append(record)
    expected_prefix = DIRECTORIES + [LINK, TEMP]
    if [record['path'] for record in records[:6]] != expected_prefix:
        raise RuntimeError('Exact layout record sequence differs')
    if any(record['type'] != 'directory' or record['literal_link_target'] for record in records[:4]):
        raise RuntimeError('Original parents and target must be literal directories')
    if records[4]['type'] != 'symbolic link' or records[4]['literal_link_target'] != TARGET:
        raise RuntimeError('Original conf.d literal link differs')
    inventory = records[6:]
    if not 1 <= len(inventory) <= 64:
        raise RuntimeError('Original flat config inventory count differs')
    for record in inventory:
        basename = record['path'].removeprefix(TARGET + '/')
        if (record['path'] != TARGET + '/' + basename
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,123}\.conf', basename)
                or re.search(r'(?:secret|key|password)', basename, flags=re.I)
                or record['type'] != 'regular file' or record['literal_link_target']):
            raise RuntimeError('Unsafe or nonregular config inventory entry')
        record['basename'] = basename
    return records, inventory


def main():
    fixture = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', fixture):
        raise ValueError('Invalid fixture identity')
    output = Path('/evidence')
    output.mkdir(exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('Owned evidence directory must be empty')
    name = fixture + '-configmeta-only'
    owner = 'sbarbase-fixture-' + fixture
    report = {'scope': 'original-image-config-layout-metadata-only',
              'source_sha256': source_digest(), 'commands': [], 'passed': False,
              'limitations': ['No configuration content reads or byte/owner preservation proof.',
                              'No volumes, cross-container visibility or PostgreSQL startup.',
                              'No bootstrap, authentication, key continuity or readiness acceptance.']}
    cid = None
    step = 'image admission'

    def native(args, *, binary=False, artifact=None):
        epoch, started = time.time(), time.monotonic()
        entry = {'args': args, 'started_at_unix_seconds': epoch}
        report['commands'].append(entry)
        try:
            result = subprocess.run(args, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as error:
            raw = error.stdout or b''
            entry.update(exit_code=None, timed_out=True, duration_seconds=time.monotonic() - started,
                         stderr=(error.stderr or b'').decode('utf-8', errors='replace'),
                         stdout_bytes=len(raw), stdout_sha256=hashlib.sha256(raw).hexdigest())
            if artifact:
                (output / artifact).write_bytes(raw)
                entry['stdout_artifact'] = artifact
            raise
        entry.update(exit_code=result.returncode, duration_seconds=time.monotonic() - started,
                     stderr=result.stderr.decode('utf-8', errors='replace'),
                     stdout_bytes=len(result.stdout),
                     stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        if artifact:
            (output / artifact).write_bytes(result.stdout)
            entry['stdout_artifact'] = artifact
        if not binary:
            entry['stdout'] = result.stdout.decode('utf-8', errors='strict')
        if result.returncode or result.stderr or DIAGNOSTICS.search(result.stdout):
            raise RuntimeError('Native metadata command refused or diagnosed')
        return result.stdout

    try:
        pin = json.loads((ROOT / 'lab/distro-image.lock.json').read_text())
        reference = image_identity.reference(pin)
        image = image_identity.record(native(['docker', 'image', 'inspect', reference]).decode())
        expected_id = image_identity.resolved_id(reference, image)
        if image['Config'].get('Volumes'):
            raise RuntimeError('Admitted metadata image must not declare anonymous volumes')
        report.update(image=image, reference=reference, helper_name=name, owner_label=owner)
        step = 'helper creation and admission'
        created_id = native(['docker', 'create', '--pull=never', '--name', name, '--label', 'io.sbarbase.owner=' + owner,
                      '--network', 'none', '--read-only', '--user', '10001:10001', '--no-healthcheck',
                      '--memory', '64m', '--memory-swap', '64m', '--cpus', '.25', '--pids-limit', '16',
                      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                      '--entrypoint', '/bin/sh', reference, '-ec', SCRIPT]).decode().strip()
        if not re.fullmatch(r'[a-f0-9]{64}', created_id):
            raise RuntimeError('Created helper identity differs')
        cid = created_id
        inspection = image_identity.record(native(['docker', 'inspect', cid]).decode())
        report['helper_created'] = inspection
        host, config = inspection['HostConfig'], inspection['Config']
        expected_health = dict(image['Config'].get('Healthcheck') or {})
        expected_health['Test'] = ['NONE']
        if (inspection['Id'] != cid or inspection['Name'] != '/' + name or inspection['Image'] != expected_id
                or config.get('Labels', {}).get('io.sbarbase.owner') != owner
                or config.get('User') != '10001:10001' or config.get('Entrypoint') != ['/bin/sh']
                or config.get('Cmd') != ['-ec', SCRIPT] or config.get('Volumes')
                or config.get('Healthcheck') != expected_health
                or config.get('ExposedPorts') != image['Config'].get('ExposedPorts')
                or inspection.get('Mounts') or host.get('Binds') or host.get('Tmpfs')
                or host.get('PortBindings') or host.get('PublishAllPorts') or host.get('Privileged')
                or host.get('NetworkMode') != 'none' or not host.get('ReadonlyRootfs')
                or host.get('PidMode') or host.get('UTSMode') or host.get('IpcMode') != 'private'
                or host.get('Devices') or host.get('DeviceRequests') or host.get('VolumesFrom')
                or host.get('Memory') != 67108864 or host.get('MemorySwap') != 67108864
                or host.get('NanoCpus') != 250000000 or host.get('PidsLimit') != 16
                or host.get('CapAdd') or host.get('CapDrop') != ['ALL']
                or host.get('SecurityOpt') != ['no-new-privileges']
                or set(inspection.get('NetworkSettings', {}).get('Networks', {})) != {'none'}
                or set(inspection.get('NetworkSettings', {}).get('Ports') or {})
                   - set(image['Config'].get('ExposedPorts') or {})
                or any(value is not None for value in
                       (inspection.get('NetworkSettings', {}).get('Ports') or {}).values())
                or inspection['State'].get('Status') != 'created'):
            raise RuntimeError('Created metadata helper isolation differs')
        step = 'bounded literal metadata observation'
        raw = native(['docker', 'start', '--attach', cid], binary=True, artifact='metadata.bin')
        report['metadata'], report['inventory'] = decode_metadata(raw)
        final = image_identity.record(native(['docker', 'inspect', cid]).decode())
        report['helper_final'] = final
        if (final['Id'] != cid or final['Image'] != expected_id
                or final['State'].get('Status') != 'exited' or final['State'].get('ExitCode') != 0
                or final['State'].get('OOMKilled') or final['State'].get('Error')
                or final['State'].get('Running') or final['State'].get('Restarting')):
            raise RuntimeError('Metadata helper terminal state differs')
        report['passed'] = True
    except Exception as error:
        report.update(error=type(error).__name__ + ': ' + str(error), failed_step=step)
    finally:
        report['cleanup'] = []
        if cid:
            try:
                native(['docker', 'rm', '-f', cid])
                report['cleanup'].append({'identity': cid, 'passed': True})
            except Exception as error:
                report['passed'] = False
                report['cleanup'].append({'identity': cid, 'passed': False, 'error': str(error)})
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report.get(key) for key in ('scope', 'source_sha256', 'passed', 'error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
