"""The release channel: which signed Sbarbase release is newer, and what applying it takes.

Releases are annotated `vMAJOR.MINOR.PATCH` tags in the canonical repository, signed with
an SSH key whose public half is in deploy/release-signers, each carrying release.json at
the root of its commit. The check reads the canonical repository, never the local `origin`,
and fetches a release only into refs/sbarbase-releases/, so the operator's branches, tags
and remotes stay as they are.

What a release needs is read from the diff between the running commit and the release,
not from what the manifest claims:
  safe      a restart picks everything up (code, dependencies, Auth, REST, Storage pins)
  rebuild   the container image, the compose file or the installed service unit changes,
            which a restart does not pick up
  manual    the PostgreSQL image changes (lab/migrate-generation.py) or the manifest
            declares a data migration
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

import install_server
import run as lab

ROOT = lab.ROOT
CANONICAL = 'https://github.com/M7MMAD-OMAR/sbarbase.git'
SIGNERS = ROOT / 'deploy' / 'release-signers'
AVAILABLE = lab.STATE / 'upgrades' / 'available.json'
NAMESPACE = 'refs/sbarbase-releases/tags/'
MANIFEST = 'release.json'
DATABASE_LOCK = 'distro-image.lock.json'
# Files a restart does not read again: the Dockerfile bakes deploy/container/start.sh into
# the image, a restart policy reuses the container compose.yaml created, and systemd runs
# the rendered copy of the unit that `install_server.py supervise --apply` installed.
REBUILD = {'Dockerfile': 'the container image definition',
           '.dockerignore': 'what the container image is built from',
           'deploy/container/start.sh': 'the start script baked into the container image',
           'compose.yaml': 'the container configuration',
           'deploy/sbarbase.service': 'the installed systemd unit'}
SEMVER = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')
TIMEOUT = 120


class ReleaseError(Exception):
    pass


def source():
    return os.environ.get('SBARBASE_RELEASE_SOURCE') or CANONICAL


def git(*args, check=True, network=False):
    # No credential prompt: the canonical repository is public, and nobody answers a
    # prompt the supervisor raises.
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'} if network else None
    try:
        result = subprocess.run(['git', *args], cwd=ROOT, text=True, capture_output=True, env=env,
                                timeout=TIMEOUT if network else None)
    except subprocess.TimeoutExpired:
        raise ReleaseError(f'git {args[0]} did not finish within {TIMEOUT} seconds')
    if check and result.returncode:
        raise ReleaseError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result


def parse(version):
    """(major, minor, patch, pre-release identifiers) of a semantic version, or None."""
    match = SEMVER.fullmatch(version or '') if isinstance(version, str) else None
    if not match:
        return None
    return int(match[1]), int(match[2]), int(match[3]), tuple(match[4].split('.')) if match[4] else ()


def key(version):
    """A sort key following semver precedence: a pre-release sorts before its release."""
    major, minor, patch, pre = parse(version)
    # Numeric identifiers compare numerically and sort before alphanumeric ones.
    identifiers = tuple((0, int(item), '') if item.isdigit() else (1, 0, item) for item in pre)
    return major, minor, patch, 0 if pre else 1, identifiers


def tag_version(tag):
    """The version a release tag names, or None for a tag that is not one."""
    if not isinstance(tag, str) or not tag.startswith('v') or parse(tag[1:]) is None:
        return None
    return tag[1:]


def list_releases(where=None, channel='stable'):
    """Release tags at the source, oldest first. Pre-releases only on the `preview` channel."""
    listed = git('ls-remote', '--tags', where or source(), network=True).stdout
    objects, peeled = {}, {}
    for line in listed.splitlines():
        oid, _, ref = line.partition('\t')
        if not ref.startswith('refs/tags/'):
            continue
        name = ref[len('refs/tags/'):]
        if name.endswith('^{}'):
            peeled[name[:-3]] = oid
        else:
            objects[name] = oid
    releases = []
    for tag, oid in objects.items():
        version = tag_version(tag)
        if version is None or (parse(version)[3] and channel != 'preview'):
            continue
        # The listed commit is only a hint; what is applied comes from the verified tag.
        releases.append({'version': version, 'tag': tag, 'object': oid, 'commit': peeled.get(tag, oid),
                         'annotated': tag in peeled})
    return sorted(releases, key=lambda release: key(release['version']))


def fetch_release(tag, where=None):
    """Fetches one release tag into the private namespace; returns the local ref."""
    if tag_version(tag) is None:
        raise ReleaseError(f'{tag!r} is not a release tag (vMAJOR.MINOR.PATCH)')
    ref = NAMESPACE + tag
    git('fetch', '-q', '--no-tags', '--no-write-fetch-head', where or source(), f'+refs/tags/{tag}:{ref}', network=True)
    return ref


def signers_configured(signers):
    try:
        lines = Path(signers).read_text().splitlines()
    except OSError:
        return False
    return any(line.strip() and not line.lstrip().startswith('#') for line in lines)


def verify(ref, signers=None):
    """None when ref is an annotated tag signed by a key in the signers file; else why not.

    Fails closed: no signers file or no key in it, a lightweight or unsigned tag, a key
    that is not listed, and a tag object whose own name is not the ref's name.
    """
    signers = Path(signers or SIGNERS)
    tag = ref.rsplit('/', 1)[-1]
    if not signers_configured(signers):
        return f'No release signing key is listed in {signers.name}; every release is refused until one is'
    kind = git('cat-file', '-t', ref, check=False).stdout.strip()
    if kind != 'tag':
        return f'{tag} is not an annotated, signed tag'
    header = git('cat-file', '-p', ref).stdout.split('\n\n', 1)[0].splitlines()
    fields = dict(line.split(' ', 1) for line in header if ' ' in line)
    # A signed tag object replayed under another name would carry its own name inside.
    if fields.get('tag') != tag:
        return f"The tag object fetched as {tag} names itself {fields.get('tag')!r}"
    if fields.get('type') != 'commit':
        return f'{tag} does not point at a commit'
    result = git('-c', 'gpg.format=ssh', '-c', f'gpg.ssh.allowedSignersFile={signers}', '-c', 'gpg.minTrustLevel=fully',
                 'verify-tag', ref, check=False)
    output = result.stdout + result.stderr
    # The exit code alone is not trusted across git versions: a key that is not listed
    # still prints a good signature, followed by "No principal matched".
    if result.returncode or 'No principal matched' in output or not re.search(r'Good "git" signature for \S+', output):
        detail = output.strip().splitlines()[-1] if output.strip() else 'no output'
        return f'{tag} is not signed by a key listed in {signers.name} ({detail})'
    return None


def commit_of(ref):
    return git('rev-parse', '--verify', '-q', ref + '^{commit}').stdout.strip()


def validate(value):
    """A manifest checked strictly on the fields this version knows.

    Unknown fields are ignored on purpose: rejecting them would make every older
    installation refuse any later manifest that adds one.
    """
    if not isinstance(value, dict):
        raise ReleaseError(f'{MANIFEST} is not an object')
    version, minimum, notes, migrations = (value.get(name) for name in ('version', 'minimum_from', 'notes', 'migrations'))
    if parse(version) is None:
        raise ReleaseError(f'{MANIFEST} version {version!r} is not a semantic version')
    if parse(minimum) is None:
        raise ReleaseError(f'{MANIFEST} minimum_from {minimum!r} is not a semantic version')
    if key(minimum) > key(version):
        raise ReleaseError(f'{MANIFEST} minimum_from {minimum} is newer than its version {version}')
    if not isinstance(notes, dict) or not all(isinstance(notes.get(language), str) and notes[language].strip() for language in ('en', 'ar')):
        raise ReleaseError(f'{MANIFEST} notes need non-empty English (en) and Arabic (ar) text')
    if not isinstance(migrations, list) or not all(isinstance(item, str) and item.strip() for item in migrations):
        raise ReleaseError(f'{MANIFEST} migrations must be a list of non-empty descriptions')
    return {'version': version, 'minimum_from': minimum, 'notes': {'en': notes['en'], 'ar': notes['ar']},
            'migrations': list(migrations)}


def manifest(commit, tag=None):
    """release.json at a commit, validated; with tag, its version must be the tag's."""
    result = git('show', f'{commit}:{MANIFEST}', check=False)
    if result.returncode:
        raise ReleaseError(f'{tag or commit[:12]} has no {MANIFEST}')
    try:
        value = validate(json.loads(result.stdout))
    except ValueError:
        raise ReleaseError(f'{tag or commit[:12]} has a {MANIFEST} that is not JSON')
    if tag is not None and value['version'] != tag_version(tag):
        raise ReleaseError(f"{tag} carries {MANIFEST} for version {value['version']}")
    return value


def lock_at(commit, name):
    result = git('show', f'{commit}:lab/{name}', check=False)
    try:
        return json.loads(result.stdout) if not result.returncode else None
    except ValueError:
        raise ReleaseError(f'lab/{name} at {commit[:12]} is not JSON')


def entries(lock):
    if not isinstance(lock, dict):
        return {}
    if isinstance(lock.get('id'), str):
        return {'default': lock}
    return {name: value for name, value in lock.items() if isinstance(value, dict) and isinstance(value.get('id'), str)}


def changes(current, target):
    """Pinned images that differ between two commits (the same rows lab/upgrade.py reports)."""
    rows = []
    for name in install_server.LOCKS:
        before, after = entries(lock_at(current, name)), entries(lock_at(target, name))
        for entry in sorted(set(before) | set(after)):
            old, new = before.get(entry, {}), after.get(entry, {})
            if old.get('id') != new.get('id'):
                rows.append({'image': f'{name}:{entry}', 'from': old.get('tag', 'none'), 'to': new.get('tag', 'none')})
    return rows


def classify(current, target, release=None):
    """('safe' | 'rebuild' | 'manual', reasons) for moving from one commit to another."""
    if release is None:
        try:
            release = manifest(target)
        except ReleaseError:
            release = {'migrations': []}
    changed = set(git('diff', '--name-only', current, target).stdout.split())
    manual, rebuild = [], []
    before, after = entries(lock_at(current, DATABASE_LOCK)), entries(lock_at(target, DATABASE_LOCK))
    for entry in sorted(set(before) | set(after)):
        if before.get(entry, {}).get('id') != after.get(entry, {}).get('id'):
            manual.append(f"lab/{DATABASE_LOCK} changes the PostgreSQL image ({before.get(entry, {}).get('tag', 'none')} -> "
                          f"{after.get(entry, {}).get('tag', 'none')}); that is lab/migrate-generation.py's job")
    for migration in release.get('migrations') or []:
        manual.append(f'The release declares a data migration: {migration}')
    for path, what in REBUILD.items():
        if path in changed:
            rebuild.append(f'{path} changes ({what}); a restart does not pick it up')
    if manual:
        return 'manual', manual + rebuild
    return ('rebuild', rebuild) if rebuild else ('safe', [])


def current_version():
    """{version, commit} of the running checkout: release.json at HEAD, else package.json."""
    commit = git('rev-parse', 'HEAD').stdout.strip()
    try:
        return {'version': manifest(commit)['version'], 'commit': commit}
    except ReleaseError:
        pass
    result = git('show', f'{commit}:package.json', check=False)
    try:
        version = json.loads(result.stdout).get('version') if not result.returncode else None
    except ValueError:
        version = None
    if parse(version) is None:
        raise ReleaseError('The checkout names no version in release.json or package.json')
    return {'version': version, 'commit': commit}


def now():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


def examine(tag, current, where=None, signers=None):
    """Fetches and reads one release against the running checkout.

    Returns (details, refusals). The commit comes from the fetched tag itself, never from
    the listing, and the manifest is read from that same commit.
    """
    ref = fetch_release(tag, where)
    refusal = verify(ref, signers)
    commit = commit_of(ref)
    release = manifest(commit, tag)
    kind, reasons = classify(current['commit'], commit, release)
    details = {'version': release['version'], 'tag': tag, 'commit': commit, 'class': kind, 'reasons': reasons,
               'notes': release['notes'], 'changes': changes(current['commit'], commit), 'signed': refusal is None,
               'minimum_from': release['minimum_from'], 'migrations': release['migrations']}
    refusals = [refusal] if refusal else []
    if kind == 'manual':
        refusals.append(f'{tag} cannot be applied as an upgrade: ' + '; '.join(reasons))
    return details, refusals


def unreachable(details, current):
    """Why a release cannot be applied from the running version, or None."""
    if key(details['minimum_from']) <= key(current['version']):
        return None
    return (f"{details['tag']} needs at least version {details['minimum_from']}; this installation is at "
            f"{current['version']}, so an earlier release has to be applied first")


def check(where=None, channel='stable', signers=None):
    """The whole check: the newest release this installation can move to, and what it takes.

    A release whose minimum_from this version does not meet is named in refusals and the
    next older one is offered instead. A release that is not signed is still reported,
    with signed false and the reason in refusals, so the console can say why it cannot
    be applied.
    """
    current = current_version()
    result = {'current': current, 'available': None, 'refusals': [], 'checked_at': now()}
    try:
        releases = list_releases(where, channel)
    except ReleaseError as error:
        result['refusals'].append(f'The release source could not be read: {error}')
        return result
    skipped = []
    for release in reversed(releases):
        if key(release['version']) <= key(current['version']):
            break
        try:
            details, refusals = examine(release['tag'], current, where, signers)
        except ReleaseError as error:
            skipped.append(f"{release['tag']} was skipped: {error}")
            continue
        reason = unreachable(details, current)
        if reason:
            skipped.append(reason)
            continue
        result.update(available=details, refusals=skipped + refusals)
        return result
    result['refusals'] = skipped
    return result


def write_check(result, path=None):
    """Writes a check result atomically for the supervisor and the console to read."""
    path = Path(path or AVAILABLE)
    path.parent.mkdir(parents=True, exist_ok=True)
    lab.atomic(path, result)
    return path


def prepare(tag, allow=(), where=None, signers=None):
    """Everything `upgrade.py start --release` needs before it moves the checkout.

    Refuses an unsigned release, one that is not newer than this checkout, one whose
    minimum_from this checkout does not meet, a `manual` release always, and a `rebuild`
    release unless the operator allowed it.
    """
    if tag_version(tag) is None:
        raise ReleaseError(f'{tag!r} is not a release tag (vMAJOR.MINOR.PATCH)')
    current = current_version()
    if key(tag_version(tag)) <= key(current['version']):
        raise ReleaseError(f"{tag} is not newer than this installation ({current['version']})")
    details, refusals = examine(tag, current, where, signers)
    if unreachable(details, current):
        refusals.append(unreachable(details, current))
    if details['class'] == 'rebuild' and 'rebuild' not in allow:
        refusals.append(f'{tag} needs a rebuild: ' + '; '.join(details['reasons'])
                        + '. Pass --allow-class rebuild to apply it, then rebuild')
    if refusals:
        raise ReleaseError('\n'.join(refusals))
    return details
