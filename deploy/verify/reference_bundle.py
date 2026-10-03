#!/usr/bin/env python3
"""Freeze or verify public Supabase metadata without pulling or starting images.

Requires git, Docker Compose and Docker buildx CLI plugins, not a Docker socket.
Native parsers inspect source only. Credentials and ambient Compose variables
are excluded. Missing metadata is retained and causes a nonzero result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPOSITORY = 'https://github.com/supabase/supabase'
COMMIT = '564eab8ad7840b13324f68b1bfac074ef8d51c21'
TAG = 'self-hosted/v0.8.2'
TAG_OBJECT = '47111f95a43ffcc20ab288e29c48ce0b80174bd6'
CORE_HASH = '8ba18fd43e9afba90e8287c8e8afc4bdda1e38d2228e57ff660968bb550da054'
PLATFORMS = ('linux/amd64', 'linux/arm64')
AUXILIARY = {
    'docker/volumes/api/envoy/cds.yaml': '1d7514b891370ed27c25911df008887402e16ab09273e6e433225bb7f09f7905',
    'docker/volumes/api/envoy/envoy.yaml': '3697f23b0be9ec5b829f937c600eb9b878f1f778ab510b42ad5e4f14742447e9',
    'docker/volumes/api/envoy/lds.template.yaml': '9d9a6d6ea0a372be99e165d995f012d383d5cc8473b92a06b7aa0bbd73566c3d',
    'docker/volumes/api/kong.yml': 'acdce0ff4ac9e1dfa496733df7ca97cc89a9632de4b727af3f3a0d600ada2db7',
    'docker/volumes/logs/vector.yml': '8f9fa080e3cd8107ac3e6d3bf8d6aa8959b6845d3cd8d5144fb8f28a45607a41',
}
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


class Refusal(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, env, diagnostics=None):
    result = subprocess.run(argv, env=env, capture_output=True, timeout=120)
    if result.stderr.strip() and not result.returncode and diagnostics is not None:
        diagnostic = result.stderr.decode(errors='replace')
        diagnostics.append(re.sub(r'time="[^"]*" ', '', diagnostic))
    if result.returncode or (result.stderr.strip() and diagnostics is None):
        raise Refusal(f'{argv[0]} command refused ({result.returncode}): '
                      + result.stderr.decode(errors='replace')[:2000])
    return result.stdout


def source(root, env):
    refs = run(['git', 'ls-remote', '--tags', REPOSITORY, 'refs/tags/' + TAG,
                'refs/tags/' + TAG + '^{}'], env).decode().splitlines()
    expected = {TAG_OBJECT + '\trefs/tags/' + TAG, COMMIT + '\trefs/tags/' + TAG + '^{}'}
    if set(refs) != expected or len(refs) != 2:
        raise Refusal('public annotated tag or peeled commit changed')
    repo = root / 'public-source'
    run(['git', 'init', '-q', str(repo)], env)
    run(['git', '-C', str(repo), 'fetch', '-q', '--filter=blob:none', '--depth=1', REPOSITORY, COMMIT], env)
    if run(['git', '-C', str(repo), 'rev-parse', 'FETCH_HEAD'], env).decode().strip() != COMMIT:
        raise Refusal('public source commit mismatch')
    tree = run(['git', '-C', str(repo), 'ls-tree', '-rz', COMMIT, '--', 'docker/'], env)
    entries = [entry for entry in tree.split(b'\0') if entry]
    for entry in entries:
        metadata, filename = entry.split(b'\t', 1)
        mode, kind, _ = metadata.decode().split()
        if mode not in ('100644', '100755') or kind != 'blob':
            raise Refusal(f'nonregular or symlink source entry: {filename.decode()}')
    run(['git', '-C', str(repo), '-c', 'core.autocrlf=false', 'checkout', '-q', COMMIT, '--', 'docker'], env)
    rows = []
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        metadata, filename = entry.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split()
        path = filename.decode('utf-8')
        if mode not in ('100644', '100755') or kind != 'blob':
            raise Refusal(f'nonregular or symlink source entry: {path}')
        if not path.startswith('docker/') or '..' in Path(path).parts or '\\' in path:
            raise Refusal('unsafe source path')
        data = (repo / path).read_bytes()
        # Bind checkout bytes to the Git blob, detecting any checkout conversion.
        blob_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if blob_hash != blob:
            raise Refusal(f'checkout differs from pinned Git blob: {path}')
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(int(mode[-3:], 8))
        rows.append({'path': path, 'mode': mode, 'git_blob': blob,
                     'bytes': len(data), 'sha256': sha(data)})
    rows.sort(key=lambda item: item['path'])
    core = [row for row in rows if row['path'] == 'docker/docker-compose.yml']
    if len(core) != 1 or core[0]['sha256'] != CORE_HASH:
        raise Refusal('known upstream Compose checksum mismatch')
    return repo, rows


def compose_inventory(repo, rows, env):
    # All YAML is accounted for. Native schema validation distinguishes auxiliary
    # service configurations from Compose files. A Compose-like filename can
    # never be silently excluded when parsing fails.
    documents, images, auxiliary, errors = [], set(), [], []
    for row in rows:
        path = row['path']
        if Path(path).suffix not in ('.yaml', '.yml'):
            continue
        if path in AUXILIARY:
            if row.get('sha256') != AUXILIARY[path]:
                errors.append(f'{path}: known auxiliary checksum differs')
            auxiliary.append({'path': path, 'classification': 'pinned-non-compose-service-configuration',
                              'sha256': row.get('sha256')})
            continue
        diagnostics = []
        command = ['docker', 'compose', '--project-name', 'reference', '--env-file',
                   str(repo / 'docker/.env.example'), '-f', str(repo / path), 'config',
                   '--no-interpolate', '--no-env-resolution', '--no-path-resolution',
                   '--no-consistency']
        try:
            model = json.loads(run(command + ['--format', 'json'], env, diagnostics))
        except (Refusal, ValueError) as error:
            errors.append(f'{path}: native schema rejection: {error}'.replace(str(repo), '<public-checkout>'))
            continue
        services = model.get('services')
        if not isinstance(services, dict):
            errors.append(f'{path}: missing Compose services')
            continue
        refs = sorted(set(service['image'] for service in services.values() if 'image' in service))
        try:
            # Compose v5 --images cannot normalize uninterpolated port strings.
            # Only the pinned public sample may interpolate this cross-check.
            # Image-less overlays get native project-service placeholders. These
            # remain explicit inherited entries, never public registry images.
            image_command = [arg for arg in command if arg != '--no-interpolate']
            native_refs = sorted(set(line for line in run(image_command + ['--images'],
                                     env, diagnostics).decode().splitlines() if line))
        except Refusal as error:
            errors.append(f'{path}: native image listing failed: {error}'.replace(str(repo), '<public-checkout>'))
            native_refs = []
        errors.extend(f'{path}: native parser diagnostic: {d}'.replace(str(repo), '<public-checkout>')
                      for d in sorted(set(diagnostics)))
        expected_native = sorted(set(service.get('image') or 'reference-' + name
                                     for name, service in services.items()))
        if expected_native != native_refs:
            errors.append(f'{path}: native image inventory mismatch')
        for name, service in services.items():
            if service.get('build') and not service.get('image'):
                errors.append(f'{path}: build-only service image cannot be frozen: {name}')
        documents.append({'path': path, 'services': {name: {'image': service.get('image'),
                         'build': service.get('build')} for name, service in sorted(services.items())},
                         'images': refs, 'model_sha256': sha(canonical(model))})
        for ref in refs:
            if not isinstance(ref, str) or '$' in ref or not ref or any(c.isspace() for c in ref):
                errors.append(f'{path}: unresolved image {ref!r}')
            else:
                images.add(ref)
    return {'documents': documents, 'auxiliary_yaml': auxiliary,
            'images': sorted(images), 'errors': errors,
            'configuration': 'public .env.example; interpolation disabled; no env-file or path resolution'}


def valid_digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise Refusal('missing immutable sha256 digest')
    return value


def inspect_image(ref, env, pinned=None):
    item = {'reference': ref, 'platforms': {p: {'error': 'not inspected: root metadata unresolved'}
                                        for p in PLATFORMS}, 'errors': []}
    try:
        selector = ref if pinned is None else ref.split('@')[0] + '@' + valid_digest(pinned)
        descriptor = json.loads(run(['docker', 'buildx', 'imagetools', 'inspect', selector,
                                     '--format', '{{json .Manifest}}'], env))
        item['digest'] = valid_digest(descriptor.get('digest'))
        if pinned is not None and item['digest'] != pinned:
            raise Refusal('registry returned wrong immutable digest')
        raw = run(['docker', 'buildx', 'imagetools', 'inspect', selector, '--raw'], env)
        if 'sha256:' + sha(raw) != item['digest']:
            raise Refusal('raw root manifest checksum mismatch')
        item['manifest'] = raw.decode()
        manifest = json.loads(raw)
        children = manifest.get('manifests')
        for platform in PLATFORMS:
            try:
                os_name, arch = platform.split('/')
                if children is not None:
                    matches = [m for m in children if m.get('platform', {}).get('os') == os_name
                               and m.get('platform', {}).get('architecture') == arch]
                    if len(matches) != 1:
                        raise Refusal(f'platform absent or ambiguous: {len(matches)} matches')
                    digest = valid_digest(matches[0].get('digest'))
                else:
                    digest = item['digest']
                address = ref.split('@')[0] + '@' + digest
                platform_raw = run(['docker', 'buildx', 'imagetools', 'inspect', address, '--raw'], env)
                if 'sha256:' + sha(platform_raw) != digest:
                    raise Refusal('platform manifest checksum mismatch')
                platform_manifest = json.loads(platform_raw)
                config_digest = valid_digest(platform_manifest.get('config', {}).get('digest'))
                config = json.loads(run(['docker', 'buildx', 'imagetools', 'inspect', address,
                                         '--format', '{{json .Image}}'], env))
                if config.get('os') != os_name or config.get('architecture') != arch:
                    raise Refusal('config platform does not match requested platform')
                item['platforms'][platform] = {'manifest_digest': digest,
                        'config_digest': config_digest, 'manifest': platform_raw.decode(),
                        'os': config['os'], 'architecture': config['architecture']}
            except (Refusal, ValueError, KeyError, TypeError, AttributeError) as error:
                item['platforms'][platform] = {'error': str(error)}
                item['errors'].append(f'{platform}: {error}')
    except (Refusal, ValueError, KeyError, TypeError, AttributeError) as error:
        item['errors'].append(str(error))
    return item


def packet_errors(packet):
    errors = list(packet.get('errors', []))
    if (packet.get('schema') != 1 or packet.get('commit') != COMMIT or packet.get('repository') != REPOSITORY
            or packet.get('tag') != TAG or packet.get('tag_object') != TAG_OBJECT):
        errors.append('invalid pinned source identity')
    files = packet.get('files', [])
    if not files or len({f.get('path') for f in files}) != len(files):
        errors.append('empty or duplicate source inventory')
    for entry in files:
        if (not isinstance(entry.get('path'), str) or not entry['path'].startswith('docker/')
                or '..' in Path(entry['path']).parts or entry.get('mode') not in ('100644', '100755')
                or not re.fullmatch('[0-9a-f]{64}', entry.get('sha256', ''))
                or not re.fullmatch('[0-9a-f]{40}', entry.get('git_blob', ''))
                or not isinstance(entry.get('bytes'), int) or entry['bytes'] < 0):
            errors.append('invalid mandatory source checksum/mode/path metadata')
    inventory = packet.get('compose', {})
    errors.extend(inventory.get('errors', []))
    refs = inventory.get('images', [])
    images = packet.get('images', [])
    if not refs or sorted(i.get('reference', '') for i in images) != sorted(refs):
        errors.append('missing or duplicate image metadata')
    for image in images:
        errors.extend(f"{image.get('reference')}: {e}" for e in image.get('errors', []))
        try:
            valid_digest(image.get('digest'))
            if 'sha256:' + sha(image['manifest'].encode()) != image['digest']:
                raise Refusal('root manifest tampering')
            root_manifest = json.loads(image['manifest'])
            for platform in PLATFORMS:
                data = image['platforms'][platform]
                os_name, arch = platform.split('/')
                if data.get('os') != os_name or data.get('architecture') != arch:
                    raise Refusal('missing or incorrect config platform identity')
                children = root_manifest.get('manifests')
                if children is not None:
                    matches = [child for child in children if child.get('platform', {}).get('os') == os_name
                               and child.get('platform', {}).get('architecture') == arch]
                    if len(matches) != 1 or matches[0].get('digest') != data.get('manifest_digest'):
                        raise Refusal('platform manifest is absent, ambiguous or not bound to root')
                elif image['digest'] != data.get('manifest_digest'):
                    raise Refusal('single manifest root mismatch')
                valid_digest(data.get('config_digest'))
                valid_digest(data.get('manifest_digest'))
                raw = data['manifest'].encode()
                if 'sha256:' + sha(raw) != data['manifest_digest']:
                    raise Refusal('platform manifest tampering')
                if json.loads(raw)['config']['digest'] != data['config_digest']:
                    raise Refusal('config digest mismatch')
        except (KeyError, ValueError, TypeError) as error:
            errors.append(f"{image.get('reference')}: incomplete or invalid metadata: {error}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--bundle', type=Path, required=True)
    verify.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == 'verify':
        try:
            initial = json.loads(args.bundle.read_text())
            initial_errors = packet_errors(initial)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            initial_errors = [str(error)]
        if initial_errors:
            result = {'schema': 1, 'commit': COMMIT, 'status': 'refused',
                      'errors': initial_errors,
                      'source_refetch': 'not run: packet already lacks mandatory proof',
                      'registry_reinspection': 'not run: packet already lacks mandatory proof'}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
            print(json.dumps({'status': result['status'], 'errors': initial_errors}, sort_keys=True))
            return 1
    with tempfile.TemporaryDirectory(prefix='public-reference-') as temp:
        root = Path(temp)
        docker_config = root / 'docker-config'
        docker_config.mkdir()
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': temp,
               'DOCKER_CONFIG': str(docker_config), 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'}
        try:
            repo, files = source(root, env)
            inventory = compose_inventory(repo, files, env)
            if args.mode == 'freeze':
                packet = {'schema': 1, 'repository': REPOSITORY, 'tag': TAG, 'tag_object': TAG_OBJECT, 'commit': COMMIT,
                          'reviewed_at': '2026-10-03',
                          'files': files, 'compose': inventory,
                          'images': [inspect_image(ref, env) for ref in inventory['images']],
                          'errors': [], 'scope': 'source and registry metadata only; runtime unrun',
                          'config_digest_evidence': 'descriptor in hash-verified manifest; platform identity from buildx registry config inspection; raw config bytes not retained',
                          'docs': ['https://docs.docker.com/reference/cli/docker/compose/config/',
                                   'https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/']}
                errors = packet_errors(packet)
                packet['status'] = 'complete-metadata-runtime-unrun' if not errors else 'metadata-collected-with-unresolved-proof'
                result = packet
            else:
                packet = json.loads(args.bundle.read_text())
                errors = packet_errors(packet)
                if files != packet.get('files'):
                    errors.append('fresh pinned source inventory differs')
                if inventory != packet.get('compose'):
                    errors.append('fresh native Compose inventory differs')
                if not errors:
                    for image in packet['images']:
                        fresh = inspect_image(image['reference'], env, image['digest'])
                        if fresh != image:
                            errors.append(f"immutable registry metadata differs: {image['reference']}")
                result = {'schema': 1, 'commit': COMMIT, 'bundle_sha256': sha(args.bundle.read_bytes()),
                          'status': 'verified-metadata-runtime-unrun' if not errors else 'refused',
                          'errors': errors}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
            errors = [str(error)]
            result = {'schema': 1, 'status': 'refused', 'errors': errors}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'errors': errors}, sort_keys=True))
        return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
