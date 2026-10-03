"""Immutable repository references and daemon-local identity proof.

A registry index digest is a logical pin, not necessarily Docker's image Id.
No tag, bare digest or unrelated repository digest can authorize an image.
"""
import json
import re

DIGEST = re.compile(r'sha256:[a-f0-9]{64}')
PATH_PART = re.compile(r'[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*')
HOST = re.compile(r'[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?')


class IdentityError(ValueError):
    """Missing or inconsistent immutable image proof."""


def repository(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise IdentityError('Invalid image repository')
    parts = value.split('/')
    explicit = len(parts) > 1 and ('.' in parts[0] or ':' in parts[0] or parts[0] == 'localhost')
    host, path = (parts[0], parts[1:]) if explicit else ('docker.io', parts)
    if host in ('index.docker.io', 'registry-1.docker.io'):
        host = 'docker.io'
    if not HOST.fullmatch(host) or not path or any(not PATH_PART.fullmatch(p) for p in path):
        raise IdentityError('Invalid image repository')
    if host == 'docker.io' and len(path) == 1:
        path.insert(0, 'library')
    return host + '/' + '/'.join(path)


def immutable(value):
    if not isinstance(value, str) or value.count('@') != 1:
        raise IdentityError('Repository-qualified immutable reference required')
    repo, digest = value.split('@')
    if not DIGEST.fullmatch(digest):
        raise IdentityError('Complete SHA-256 image digest required')
    return repository(repo) + '@' + digest


def reference(pin):
    if not isinstance(pin, dict) or not isinstance(pin.get('id'), str) or not DIGEST.fullmatch(pin['id']):
        raise IdentityError('Complete SHA-256 lock identity required')
    tag = pin.get('tag')
    if not isinstance(tag, str) or '@' in tag or ':' not in tag.rsplit('/', 1)[-1]:
        raise IdentityError('Declared image repository and version tag required')
    repo, version = tag.rsplit(':', 1)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', version) or version.lower() == 'latest':
        raise IdentityError('Declared image version tag required')
    expected = repository(repo) + '@' + pin['id']
    digests = pin.get('digests')
    if not isinstance(digests, list) or not digests:
        raise IdentityError('Immutable repository digest proof required in lock')
    if any(immutable(item) != expected for item in digests):
        raise IdentityError('Lock repository digests disagree with declared repository or identity')
    return expected


def record(output):
    try:
        values = json.loads(output)
    except (ValueError, TypeError) as error:
        raise IdentityError('Image inspect returned invalid JSON') from error
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise IdentityError('Image inspect must return exactly one image record')
    return values[0]


def resolved_id(reference, value):
    requested = immutable(reference)
    if not isinstance(value, dict) or not isinstance(value.get('Id'), str) or not DIGEST.fullmatch(value['Id']):
        raise IdentityError('Complete daemon image identity required')
    digests = value.get('RepoDigests')
    if not isinstance(digests, list) or not digests:
        raise IdentityError('Daemon repository digest proof unavailable')
    if requested not in {immutable(item) for item in digests}:
        raise IdentityError('Daemon image does not prove the requested repository digest')
    return value['Id']


def is_missing(reference, output, error):
    """Accept only native, single-reference absence evidence, never API failure."""
    requested = immutable(reference)
    try:values = json.loads(output)
    except (ValueError, TypeError):return False
    if values != [] or not isinstance(error, str):return False
    match = re.fullmatch(r'Error response from daemon: No such image: ([^\r\n]+)\n?', error)
    if match is None:return False
    try:return immutable(match[1]) == requested
    except IdentityError:return False


def inspection_failure(reference, output, error):
    """Retain native failure text when missing-image evidence is absent."""
    immutable(reference)
    detail = error.strip() if isinstance(error, str) else ''
    if not detail:detail = output.strip() if isinstance(output, str) else ''
    if not detail:detail = 'native image inspection failed without a diagnostic'
    return IdentityError('Image inspection refused for '+reference+': '+detail)
