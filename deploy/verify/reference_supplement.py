#!/usr/bin/env python3
"""Pinned public source and supplemental metadata integrity, never runtime proof."""
import argparse
import base64
import binascii
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
import posixpath
from pathlib import Path
import re
import signal
import stat
import tarfile
import time
import urllib.error
import urllib.request

COMMIT = '564eab8ad7840b13324f68b1bfac074ef8d51c21'
TREE = '8d5b6befca58030fd783d7fdad0b97ad6d5bced0'
REFERENCE = {'repository': 'https://github.com/supabase/supabase',
             'tag': 'self-hosted/v0.8.2', 'commit': COMMIT,
             'tag_object': '47111f95a43ffcc20ab288e29c48ce0b80174bd6'}
BASELINE = '9299a0cfbf7c46c2fbbb79bf6dbda290cd4754902906c88b8cbe0ac83dd1db80'
PLATFORMS = ('linux/amd64', 'linux/arm64')
URL = 'https://codeload.github.com/supabase/supabase/tar.gz/' + COMMIT
LIMITS = {'requests': 1, 'archive_bytes': 64 * 1024**2,
          'expanded_bytes': 512 * 1024**2, 'file_bytes': 16 * 1024**2,
          'entries': 50000, 'disk_bytes': 128 * 1024**2,
          'wall_seconds': 90, 'socket_seconds': 10}
STATES = {'source_inventory': 'verified', 'working_tree': 'unobserved',
          'build_inputs': 'unproven', 'build': 'unrun', 'runtime': 'unrun',
          'security': 'unproven', 'acceptance': 'unproven', 'production': 'unproven'}


class Refusal(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'missing or extra object keys')


def integer(value, minimum=1, maximum=512 * 1024**2):
    require(type(value) is int and minimum <= value <= maximum, 'invalid integer or budget')
    return value


def relative(value):
    require(type(value) is str and value and '\\' not in value and '\x00' not in value,
            'invalid relative path')
    require(not value.startswith('/') and all(p not in ('', '.', '..', '.git')
            for p in value.split('/')), 'unsafe relative path')
    require(len(value.encode('utf-8')) <= 1024 and len(value.split('/')) <= 64,
            'path length or depth budget exceeded')
    return value


def read_regular(path, maximum):
    """Walk without symlinks, read one stable single-link regular file."""
    path = Path(path).absolute()
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = new
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(leaf)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                    'nonregular, symlink or hardlinked artifact')
            require(before.st_size <= maximum, 'artifact byte budget exceeded')
            chunks, size = [], 0
            while True:
                data = os.read(leaf, min(65536, maximum + 1 - size))
                if not data:
                    break
                size += len(data)
                require(size <= maximum, 'artifact byte budget exceeded')
                chunks.append(data)
            after = os.fstat(leaf)
            require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                     before.st_ctime_ns, before.st_mode, before.st_nlink) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                     after.st_ctime_ns, after.st_mode, after.st_nlink) and size == before.st_size,
                    'artifact changed while reading')
            return b''.join(chunks)
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def own_directory(path):
    path = Path(path).absolute()
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = new
        os.mkdir(path.name, mode=0o700, dir_fd=descriptor)
        return os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def write_owned(directory, name, data):
    leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                   0o600, dir_fd=directory)
    with os.fdopen(leaf, 'wb') as target:
        target.write(data)


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise Refusal('float or nonfinite JSON number')
    return json.loads(data, object_pairs_hook=pairs, parse_float=invalid, parse_constant=invalid)


def artifact(root, value, maximum=4 * 1024**2):
    keys(value, ('path', 'bytes', 'sha256'))
    relative(value['path'])
    integer(value['bytes'], maximum=maximum)
    require(type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256']),
            'invalid artifact hash')
    raw = read_regular(Path(root) / value['path'], maximum)
    require(len(raw) == value['bytes'] and sha(raw) == value['sha256'], 'artifact identity mismatch')
    return raw


def descriptor(path, raw):
    return {'path': relative(path), 'bytes': len(raw), 'sha256': sha(raw)}


def git_hash(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def commit_tree(raw, expected=COMMIT):
    require(git_hash('commit', raw) == expected, 'Git commit object identity mismatch')
    line = raw.split(b'\n', 1)[0]
    require(re.fullmatch(b'tree [0-9a-f]{40}', line), 'invalid Git commit tree')
    return line[5:].decode()


def inventory_tree(rows):
    tree = {}
    for row in rows:
        parts = relative(row['path']).split('/')
        node = tree
        for part in parts[:-1]:
            require(part not in node or type(node[part]) is dict, 'file used as directory')
            node = node.setdefault(part, {})
        require(parts[-1] not in node, 'duplicate or conflicting source path')
        require(row['mode'] in ('100644', '100755', '120000') and
                re.fullmatch('[0-9a-f]{40}', row['git_blob']), 'invalid source mode/blob')
        node[parts[-1]] = (row['mode'], row['git_blob'])
    def digest(node):
        entries = []
        for name, item in node.items():
            directory = type(item) is dict
            mode, value = ('40000', digest(item)) if directory else item
            encoded = name.encode('utf-8')
            entries.append((encoded + (b'/' if directory else b''),
                            mode.encode() + b' ' + encoded + b'\0' + bytes.fromhex(value)))
        return git_hash('tree', b''.join(value for _, value in sorted(entries)))
    return digest(tree)


def archive_inventory(raw, commit_raw, limits, expected=COMMIT):
    import io
    for name in LIMITS:
        integer(limits[name], maximum=LIMITS[name])
    require(set(limits) == set(LIMITS), 'invalid budget keys')
    require(len(raw) <= limits['archive_bytes'], 'archive budget exceeded')
    expected_tree = commit_tree(commit_raw, expected)
    started, size, entries, seen, rows, directories = time.monotonic(), 0, 0, set(), [], set()
    class BoundedInfo(tarfile.TarInfo):
        @classmethod
        def checked(cls, value):
            nonlocal entries
            require(time.monotonic() - started <= limits['wall_seconds'], 'source wall budget exceeded')
            entries += 1
            require(entries <= limits['entries'], 'source header/entry budget exceeded')
            require(value.size <= limits['file_bytes'], 'source header byte budget exceeded')
            return value
        @classmethod
        def _frombuf(cls, *args, **kwargs):
            return cls.checked(super()._frombuf(*args, **kwargs))
        @classmethod
        def frombuf(cls, *args):
            value = super().frombuf(*args)
            return value if hasattr(tarfile.TarInfo, '_frombuf') else cls.checked(value)
    class BoundedStream:
        def __init__(self):
            self.stream = gzip.GzipFile(fileobj=io.BytesIO(raw))
            self.bytes = 0
        def read(self, count):
            require(time.monotonic() - started <= limits['wall_seconds'], 'source wall budget exceeded')
            require(count >= 0, 'unbounded source read refused')
            chunk = self.stream.read(min(count, limits['expanded_bytes'] + 1 - self.bytes))
            self.bytes += len(chunk)
            require(self.bytes <= limits['expanded_bytes'], 'expanded source byte budget exceeded')
            return chunk
    stream = BoundedStream()
    prefix = 'supabase-' + expected
    with tarfile.open(fileobj=stream, mode='r|', tarinfo=BoundedInfo) as archive:
        for entry in archive:
            require(time.monotonic() - started <= limits['wall_seconds'], 'source wall budget exceeded')
            name = entry.name.rstrip('/') if entry.isdir() else entry.name
            require(name == prefix or name.startswith(prefix + '/'), 'archive root identity mismatch')
            require(name not in seen, 'duplicate archive path')
            seen.add(name)
            require(not entry.mode & 0o7000, 'unsafe archive mode')
            if name == prefix:
                require(entry.isdir(), 'archive root is not a directory')
                continue
            path = relative(name[len(prefix) + 1:])
            if entry.isdir():
                directories.add(path)
                continue
            require(entry.isfile() or entry.issym(), 'unsupported archive entry type')
            if entry.issym():
                require(type(entry.linkname) is str and entry.linkname and
                        not entry.linkname.startswith('/') and '\\' not in entry.linkname,
                        'unsafe source symlink')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), entry.linkname))
                require(resolved != '..' and not resolved.startswith('../'), 'escaping source symlink')
                data, mode = entry.linkname.encode('utf-8'), '120000'
            else:
                integer(entry.size, minimum=0, maximum=limits['file_bytes'])
                data = archive.extractfile(entry).read(limits['file_bytes'] + 1)
                require(len(data) == entry.size, 'truncated source blob')
                mode = '100755' if entry.mode & 0o111 else '100644'
            require(len(data) <= limits['file_bytes'], 'source blob byte budget exceeded')
            size += len(data)
            require(size <= limits['expanded_bytes'], 'expanded source byte budget exceeded')
            rows.append({'path': path, 'mode': mode, 'bytes': len(data),
                         'sha256': sha(data), 'git_blob': git_hash('blob', data)})
        # The tar stream may prefetch tail bytes; check that buffer as well.
        require(not any(archive.fileobj.buf), 'nonzero archive tail')
        while True:
            tail = stream.read(65536)
            if not tail:
                break
            require(not any(tail), 'nonzero archive tail')
    allowed_dirs = {row['path'][:i] for row in rows for i, c in enumerate(row['path']) if c == '/'}
    require(directories <= allowed_dirs, 'extra source directory')
    require(rows and inventory_tree(rows) == expected_tree, 'complete Git tree identity mismatch')
    return sorted(rows, key=lambda row: row['path'])


def baseline(path):
    raw = read_regular(path, 4 * 1024**2)
    require(sha(raw) == BASELINE, 'original baseline changed')
    return decode(raw)


def verify_source(path):
    root = Path(path).parent
    packet = decode(read_regular(path, 32 * 1024**2))
    keys(packet, ('schema', 'reference', 'baseline', 'limits', 'archive', 'commit_object',
                  'files', 'states', 'gaps'))
    require(type(packet['schema']) is int and packet['schema'] == 1, 'unsupported source schema')
    require(packet['reference'] == REFERENCE and packet['baseline'] == BASELINE,
            'source reference or baseline mismatch')
    require(packet['states'] == STATES, 'source proof state escalation')
    require(packet['gaps'] == ['external Studio base/package/build inputs unproven',
            'publisher build provenance unproven', 'working tree/build/runtime/security unrun'],
            'source closure gap removed')
    raw = artifact(root, packet['archive'], LIMITS['archive_bytes'])
    commit = artifact(root, packet['commit_object'], 1024**2)
    require(commit_tree(commit) == TREE, 'pinned root tree mismatch')
    rows = archive_inventory(raw, commit, packet['limits'])
    require(type(packet['files']) is list, 'invalid source inventory')
    for row in packet['files']:
        keys(row, ('path', 'mode', 'bytes', 'sha256', 'git_blob'))
        integer(row['bytes'], minimum=0, maximum=packet['limits']['file_bytes'])
    require(packet['files'] == rows, 'source inventory changed, missing or extra')
    return packet


def media_type(value):
    token = r'[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-]{0,126}'
    require(type(value) is str and re.fullmatch(token + '/' + token, value),
            'invalid descriptor media type')


def string_map(value):
    require(type(value) is dict and all(type(k) is str and type(v) is str
            for k, v in value.items()), 'invalid annotations')


def raw_descriptor(value):
    """Validate every descriptor, without fetching its referenced content."""
    require(type(value) is dict, 'invalid raw descriptor')
    media_type(value.get('mediaType'))
    digest = value.get('digest')
    require(type(digest) is str and re.fullmatch(
            r'[a-z0-9]+(?:[+._-][a-z0-9]+)*:[A-Za-z0-9=_-]+', digest),
            'invalid descriptor digest')
    algorithm, encoded = digest.split(':', 1)
    if algorithm in ('sha256', 'sha512'):
        require(re.fullmatch('[0-9a-f]{' + str(64 if algorithm == 'sha256' else 128) + '}',
                             encoded), 'invalid registered descriptor digest')
    integer(value.get('size'), minimum=0, maximum=2**63 - 1)
    if 'annotations' in value:
        string_map(value['annotations'])
    if 'artifactType' in value:
        media_type(value['artifactType'])
    if 'urls' in value:
        require(type(value['urls']) is list and all(type(url) is str and
                re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', url) and
                not any(c.isspace() or ord(c) < 32 for c in url) for url in value['urls']),
                'invalid descriptor URLs')
    if 'data' in value:
        require(type(value['data']) is str, 'invalid embedded descriptor data')
        try:
            embedded = base64.b64decode(value['data'], validate=True)
        except (ValueError, binascii.Error) as error:
            raise Refusal('invalid embedded descriptor data') from error
        require(len(embedded) == value['size'], 'embedded descriptor size mismatch')
        require(algorithm in ('sha256', 'sha512') and
                hashlib.new(algorithm, embedded).hexdigest() == encoded,
                'embedded descriptor digest mismatch or unsupported algorithm')
    if 'platform' in value:
        platform = value['platform']
        require(type(platform) is dict and all(type(platform.get(key)) is str and
                platform[key] for key in ('os', 'architecture')), 'invalid descriptor platform')
        for key in ('os.version', 'variant'):
            if key in platform:
                require(type(platform[key]) is str, 'invalid platform string')
        for key in ('os.features', 'features'):
            if key in platform:
                require(type(platform[key]) is list and all(type(x) is str
                        for x in platform[key]), 'invalid platform features')


def raw_document(value, indexes, images):
    require(type(value) is dict and type(value.get('schemaVersion')) is int and
            value['schemaVersion'] == 2, 'invalid manifest schema')
    require(value.get('mediaType') in indexes + images, 'manifest media type mismatch')
    if value['mediaType'] in indexes:
        require(type(value.get('manifests')) is list and 'config' not in value and
                'layers' not in value, 'invalid index children or mixed manifest')
        for child in value['manifests']:
            raw_descriptor(child)
    else:
        require('manifests' not in value and type(value.get('layers')) is list,
                'invalid manifest layers or mixed index')
        raw_descriptor(value.get('config'))
        for layer in value['layers']:
            raw_descriptor(layer)
    if 'subject' in value:
        raw_descriptor(value['subject'])
    if 'annotations' in value:
        string_map(value['annotations'])
    if 'artifactType' in value:
        media_type(value['artifactType'])


def verify_image(root, row, original):
    keys(row, ('reference', 'root', 'platforms', 'provenance'))
    require(row['reference'] == original['reference'], 'image reference changed')
    keys(row['platforms'], PLATFORMS)
    require(row['provenance'] == {'state': 'unverified', 'evidence': []},
            'unimplemented publisher/source provenance claim')
    raw = artifact(root, row['root'])
    root_digest, manifest = 'sha256:' + sha(raw), decode(raw)
    if original.get('digest'):
        require(root_digest == original['digest'], 'original frozen image digest changed')
    indexes = ('application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json')
    images = ('application/vnd.oci.image.manifest.v1+json',
              'application/vnd.docker.distribution.manifest.v2+json')
    raw_document(manifest, indexes, images)
    for platform in PLATFORMS:
        data = row['platforms'][platform]
        keys(data, ('manifest', 'config'))
        os_name, arch = platform.split('/')
        child = artifact(root, data['manifest'])
        digest = 'sha256:' + sha(child)
        if 'manifests' in manifest:
            require(type(manifest['manifests']) is list, 'invalid index children')
            matches = [x for x in manifest['manifests'] if type(x) is dict and
                       type(x.get('platform')) is dict and x['platform'].get('os') == os_name and
                       x['platform'].get('architecture') == arch]
            require(len(matches) == 1, 'platform absent or ambiguous')
            item = matches[0]
            integer(item.get('size'), maximum=4 * 1024**2)
            require(item.get('digest') == digest and item['size'] == len(child),
                    'index/platform descriptor mismatch')
        else:
            require(digest == root_digest, 'single manifest root/platform mismatch')
        parsed = decode(child)
        raw_document(parsed, (), images)
        require(type(parsed) is dict and type(parsed.get('schemaVersion')) is int and
                parsed['schemaVersion'] == 2 and type(parsed.get('config')) is dict,
                'invalid platform manifest')
        require(parsed.get('mediaType') in images, 'platform manifest media type mismatch')
        if 'manifests' in manifest:
            require(item.get('mediaType') == parsed['mediaType'], 'platform media descriptor mismatch')
        require(parsed['config'].get('mediaType') in
                ('application/vnd.oci.image.config.v1+json', 'application/vnd.docker.container.image.v1+json'),
                'config media type mismatch')
        config = artifact(root, data['config'])
        integer(parsed['config'].get('size'), maximum=4 * 1024**2)
        require(parsed['config'].get('digest') == 'sha256:' + sha(config) and
                parsed['config']['size'] == len(config), 'config descriptor mismatch')
        parsed_config = decode(config)
        require(type(parsed_config) is dict and parsed_config.get('os') == os_name and
                parsed_config.get('architecture') == arch, 'config platform mismatch')
        previous = original.get('platforms', {}).get(platform, {})
        if previous.get('manifest_digest'):
            require(digest == previous['manifest_digest'], 'original platform digest changed')
        if previous.get('config_digest'):
            require('sha256:' + sha(config) == previous['config_digest'], 'original config digest changed')


def verify_supplement(path, gate='metadata'):
    root = Path(path).parent
    packet = decode(read_regular(path, 4 * 1024**2))
    keys(packet, ('schema', 'reference', 'baseline', 'source', 'images', 'states'))
    require(type(packet['schema']) is int and packet['schema'] == 1, 'unsupported supplement schema')
    require(packet['reference'] == REFERENCE, 'supplement reference changed')
    original_raw = artifact(root, packet['baseline'])
    require(sha(original_raw) == BASELINE, 'original packet identity changed')
    original = decode(original_raw)
    source_raw = artifact(root, packet['source'], 32 * 1024**2)
    source_path = root / packet['source']['path']
    source = verify_source(source_path)
    require(source_raw == read_regular(source_path, 32 * 1024**2), 'source changed during verification')
    by_path = {r['path']: r for r in source['files']}
    require(all(by_path.get(r['path']) == r for r in original['files']), 'original docker inventory differs')
    require(type(packet['images']) is list and len(packet['images']) == len(original['images']),
            'missing or extra image inventory')
    images = {r['reference']: r for r in packet['images'] if type(r) is dict and 'reference' in r}
    require(len(images) == len(original['images']) and set(images) == set(original['compose']['images']),
            'duplicate, unknown or missing image reference')
    require(packet['states'] == {'metadata': 'verified', 'build_inputs': 'unproven',
            'provenance': 'unverified', 'build': 'unrun', 'runtime': 'unrun',
            'security': 'unproven', 'acceptance': 'unproven', 'production': 'unproven'},
            'supplement proof state escalation')
    for old in original['images']:
        verify_image(root, images[old['reference']], old)
    require(gate == 'metadata', 'distribution requires actual build/provenance/startup/native/security evidence')
    return {'scope': 'complete source inventory and raw metadata integrity only',
            'states': packet['states'], 'original_status': original['status'],
            'original_diagnostics_preserved': True, 'acceptance_granted': False}


def make_source(archive, commit, limits):
    require(commit_tree(commit) == TREE, 'pinned source tree mismatch')
    rows = archive_inventory(archive, commit, limits)
    return {'schema': 1, 'reference': REFERENCE, 'baseline': BASELINE, 'limits': limits,
            'archive': descriptor('source.tar.gz', archive),
            'commit_object': descriptor('commit.raw', commit), 'files': rows, 'states': STATES,
            'gaps': ['external Studio base/package/build inputs unproven',
                     'publisher build provenance unproven', 'working tree/build/runtime/security unrun']}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise Refusal('public source redirect refused')


def _collect_source(commit_path, output, limits):
    commit = read_regular(commit_path, 1024**2)
    require(commit_tree(commit) == TREE, 'pinned source anchor mismatch')
    require(limits['disk_bytes'] >= limits['archive_bytes'] + 33 * 1024**2 + 65536,
            'insufficient declared source disk budget')
    require(not any(name in os.environ for name in
            ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'SSLKEYLOGFILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')),
            'ambient trust configuration refused')
    output = Path(output)
    owned = own_directory(output)
    declaration = {'schema': 1, 'url': URL, 'limits': limits, 'reference': REFERENCE,
                   'proxy': False, 'credentials': False, 'redirects': False, 'retries': 0,
                   'scope': 'public source bytes only, no image layers/build/runtime'}
    write_owned(owned, 'declaration.json', canonical(declaration))
    started, size = time.monotonic(), 0
    request = urllib.request.Request(URL, headers={'User-Agent': 'sbarbase-reference-source/1'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=limits['socket_seconds']) as response:
            require(response.status == 200, 'public source HTTP refusal')
            length = response.headers.get('Content-Length')
            if length is not None:
                require(length.isdecimal() and int(length) <= limits['archive_bytes'], 'source length budget exceeded')
            leaf = os.open('source.tar.gz', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600, dir_fd=owned)
            with os.fdopen(leaf, 'wb') as target:
                while True:
                    require(time.monotonic() - started <= limits['wall_seconds'], 'source request wall budget exceeded')
                    chunk = response.read(min(65536, limits['archive_bytes'] + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= limits['archive_bytes'], 'source request byte budget exceeded')
                    require(size + len(commit) + 32 * 1024**2 <= limits['disk_bytes'], 'owned disk budget exceeded')
                    target.write(chunk)
        archive = read_regular(output / 'source.tar.gz', limits['archive_bytes'])
        packet = make_source(archive, commit, limits)
        require(time.monotonic() - started <= limits['wall_seconds'], 'collection total wall budget exceeded')
        raw = canonical(packet)
        require(len(raw) <= 32 * 1024**2 and size + len(raw) + len(commit) + 16384 <= limits['disk_bytes'],
                'source packet disk budget exceeded')
        write_owned(owned, 'commit.raw', commit)
        write_owned(owned, 'source.json', raw)
        return {'source_packet': 'source.json', 'states': packet['states'], 'requests': 1,
                'source_bytes': size, 'elapsed_seconds': int(time.monotonic() - started)}
    except Exception as error:
        refusal = {'error': str(error), 'requests': 1,
                   'source_bytes_retained': min(size, limits['archive_bytes']), 'acceptance_granted': False}
        if isinstance(error, urllib.error.HTTPError):
            refusal.update(status=error.code, retry_after=error.headers.get('Retry-After'))
        write_owned(owned, 'refusal.json', canonical(refusal))
        if isinstance(error, urllib.error.HTTPError):
            body = error.read(min(16384, limits['archive_bytes']))
            write_owned(owned, 'http-refusal.body', body)
            write_owned(owned, 'http-refusal-info.json', canonical({'status': error.code,
                        'body_sha256': sha(body), 'body_capture_limit': 16384}))
        raise
    finally:
        os.close(owned)


@contextmanager
def wall_budget(seconds):
    def expired(_signum, _frame):
        raise Refusal('source collection wall budget exceeded')
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'existing process timer refused')
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def collect_source(commit_path, output, budget_path):
    limits = decode(read_regular(budget_path, 16384))
    keys(limits, LIMITS)
    for name in LIMITS:
        integer(limits[name], maximum=LIMITS[name])
    with wall_budget(limits['wall_seconds']):
        return _collect_source(commit_path, output, limits)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    collect = sub.add_parser('collect-source')
    for arg in ('commit-object', 'output', 'budget'):
        collect.add_argument('--' + arg, required=True)
    source = sub.add_parser('verify-source')
    source.add_argument('--packet', required=True)
    supplement = sub.add_parser('verify-supplement')
    supplement.add_argument('--packet', required=True)
    supplement.add_argument('--gate', choices=('metadata', 'distribution'), default='metadata')
    sub.add_parser('contract')
    args = parser.parse_args(argv)
    try:
        if args.command == 'contract':
            result = {'reference': REFERENCE, 'limits': LIMITS, 'states': STATES,
                      'network_url': URL, 'acceptance_granted': False}
        elif args.command == 'collect-source':
            result = collect_source(args.commit_object, args.output, args.budget)
        elif args.command == 'verify-source':
            packet = verify_source(args.packet)
            result = {'states': packet['states'], 'files': len(packet['files']),
                      'acceptance_granted': False, 'gaps': packet['gaps']}
        else:
            result = verify_supplement(args.packet, args.gate)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refusal, ValueError, OSError, tarfile.TarError, urllib.error.URLError, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error), 'acceptance_granted': False}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
