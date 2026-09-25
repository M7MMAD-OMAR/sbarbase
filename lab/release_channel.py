"""The release channel: which signed Sbarbase release is newer, and what applying it takes.

Releases are annotated `vMAJOR.MINOR.PATCH` tags in the canonical repository, signed with
an SSH key whose public half is in deploy/release-signers, each carrying release.json at
the root of its commit. The check reads the canonical repository, never the local `origin`,
and fetches a release only into refs/sbarbase-releases/, so the operator's branches, tags
and remotes stay as they are.

What a release needs is read from the diff between the running commit and the release,
not from what the manifest claims:
  safe      a restart picks everything up (code, dependencies, and the PostgREST, Edge
            Functions and Studio pins, none of which changes an environment database)
  attended  the Auth, Storage or Realtime pin changes: each runs its own schema migrations in
            the environment databases when it starts, and the previous image may not run on
            the migrated schema. The operator may install it after an explicit warning; it is
            never installed automatically
  rebuild   the container image, the compose file, the installed service unit or the TLS
            proxy changes, which a restart of Sbarbase does not pick up
  manual    the PostgreSQL image changes (lab/migrate-generation.py) or the manifest
            declares a data migration
"""
import contextlib
import datetime
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import install_server
import run as lab

ROOT = lab.ROOT
CANONICAL = 'https://github.com/M7MMAD-OMAR/sbarbase.git'
SIGNERS = ROOT / 'deploy' / 'release-signers'
AVAILABLE = lab.STATE / 'upgrades' / 'available.json'
# Held while a check or a start fetches and reads releases, so two runs never write the same
# private ref at once. Created on first use, never at import.
LOCK = lab.STATE / 'upgrades' / 'channel.lock'
LOCK_WAIT = 300
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
# The TLS proxy runs as its own unit the operator installed (sbarbase-tls.service in the
# deployment guide and the VM rehearsal), which an update never restarts. It imports only
# Node built-ins, so this file is everything it runs.
PROXY = {'deploy/console-tls-proxy.ts': 'the console TLS proxy'}
PROXY_UNIT = 'sbarbase-tls.service'
# Pinned services that run their own schema migrations in each environment database when they
# start (lab/durable_runtime.py): Auth (GoTrue) its auth schema, Storage its storage schema
# per tenant, and Realtime its realtime schema (realtime_start(migrate=...) runs them again
# whenever the pin changes). PostgREST only reads the schema, Edge Functions have no database
# connection of their own, and Studio with postgres-meta only inspects; the images.lock.json
# `db` entry is a lab probe image, overridden by distro-image.lock.json in the runtime.
MIGRATING = {('images.lock.json', 'auth'): 'Auth', ('storage-image.lock.json', 'default'): 'Storage',
             ('realtime-image.lock.json', 'default'): 'Realtime'}
CLASSES = ('safe', 'attended', 'rebuild', 'manual')
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


@contextlib.contextmanager
def exclusive(wait=None):
    """One check or start at a time reads and writes the private release refs. A run that
    cannot take the lock within LOCK_WAIT seconds fails; it never reads a ref another run is
    writing."""
    path = Path(LOCK)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + (LOCK_WAIT if wait is None else wait)
    with path.open('a') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ReleaseError('Another release check or update is reading the release source; try again in a moment')
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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


def object_of(ref):
    """The object id a ref names, read once; everything after works on that id."""
    oid = git('rev-parse', '--verify', '-q', ref, check=False).stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
        raise ReleaseError(f'{ref} names no object')
    return oid


def signers_configured(signers):
    try:
        lines = Path(signers).read_text().splitlines()
    except OSError:
        return False
    return any(line.strip() and not line.lstrip().startswith('#') for line in lines)


def verify(ref, signers=None, name=None):
    """None when ref is an annotated tag signed by a key in the signers file; else why not.

    `ref` may be a ref or an object id; it is resolved once and every step reads that one
    object. `name` is the release tag the object must call itself (by default the last part
    of the ref). Fails closed: no signers file or no key in it, a lightweight or unsigned
    tag, a key that is not listed, and a tag object whose own name is not the expected one.
    """
    signers = Path(signers or SIGNERS)
    tag = name or ref.rsplit('/', 1)[-1]
    if not signers_configured(signers):
        return f'No release signing key is listed in {signers.name}; every release is refused until one is'
    if not shutil.which('ssh-keygen'):
        # Still refused, but for the real reason: a container image built before
        # openssh-client was added to it cannot check any signature.
        return 'ssh-keygen is not installed, so no release signature can be checked; rebuild the container image'
    try:
        oid = object_of(ref)
    except ReleaseError:
        return f'{tag} is not an annotated, signed tag'
    kind = git('cat-file', '-t', oid, check=False).stdout.strip()
    if kind != 'tag':
        return f'{tag} is not an annotated, signed tag'
    header = git('cat-file', '-p', oid).stdout.split('\n\n', 1)[0].splitlines()
    fields = dict(line.split(' ', 1) for line in header if ' ' in line)
    # A signed tag object replayed under another name would carry its own name inside.
    if fields.get('tag') != tag:
        return f"The tag object fetched as {tag} names itself {fields.get('tag')!r}"
    if fields.get('type') != 'commit':
        return f'{tag} does not point at a commit'
    result = git('-c', 'gpg.format=ssh', '-c', f'gpg.ssh.allowedSignersFile={signers}', '-c', 'gpg.minTrustLevel=fully',
                 'verify-tag', oid, check=False)
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


def migrating(current, target):
    """Why moving between two commits changes environment databases as services start: one
    sentence per Auth, Storage or Realtime pin that differs."""
    reasons = []
    for (name, entry), service in MIGRATING.items():
        before, after = entries(lock_at(current, name)).get(entry, {}), entries(lock_at(target, name)).get(entry, {})
        if before.get('id') != after.get('id'):
            reasons.append(f"lab/{name} changes the {service} image ({before.get('tag', 'none')} -> {after.get('tag', 'none')}); "
                           f'{service} migrates each environment database when it starts, so going back may need the '
                           'environment backups taken before the upgrade')
    return reasons


def classify(current, target, release=None):
    """('safe' | 'attended' | 'rebuild' | 'manual', reasons) for moving from one commit to another.

    The most demanding class wins (manual, then rebuild, then attended); the reasons list
    every finding, so a release that needs a rebuild and also migrates says both."""
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
    for path, what in PROXY.items():
        if path in changed:
            rebuild.append(f'{path} changes ({what}); it runs as its own unit, {PROXY_UNIT}, which an update does not '
                           'restart: restart that unit after the update')
    attended = migrating(current, target)
    if manual:
        return 'manual', manual + rebuild + attended
    if rebuild:
        return 'rebuild', rebuild + attended
    return ('attended', attended) if attended else ('safe', [])


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

    Returns (details, blockers): blockers are what makes this release itself impossible to
    install here (no valid signature, a manual migration). The private ref is read exactly
    once: its object id is verified, and that same id is peeled to the commit whose manifest
    and diff are read, so nothing that moves the ref afterwards changes what was checked.
    """
    oid = object_of(fetch_release(tag, where))
    refusal = verify(oid, signers, name=tag)
    commit = commit_of(oid)
    release = manifest(commit, tag)
    kind, reasons = classify(current['commit'], commit, release)
    details = {'version': release['version'], 'tag': tag, 'commit': commit, 'class': kind, 'reasons': reasons,
               'notes': release['notes'], 'changes': changes(current['commit'], commit), 'signed': refusal is None,
               'minimum_from': release['minimum_from'], 'migrations': release['migrations']}
    blockers = [refusal] if refusal else []
    if kind == 'manual':
        blockers.append(f'{tag} cannot be applied as an upgrade: ' + '; '.join(reasons))
    return details, blockers


def unreachable(details, current):
    """Why a release cannot be applied from the running version, or None."""
    if key(details['minimum_from']) <= key(current['version']):
        return None
    return (f"{details['tag']} needs at least version {details['minimum_from']}; this installation is at "
            f"{current['version']}, so an earlier release has to be applied first")


def left_behind(details, current):
    """Why moving to the release would drop commits of this checkout, or None. The checkout
    may only move forward to a release that contains everything it runs."""
    result = git('merge-base', '--is-ancestor', current['commit'], details['commit'], check=False)
    if result.returncode == 0:
        return None
    if result.returncode == 1:
        count = git('rev-list', '--count', f"{details['commit']}..{current['commit']}", check=False).stdout.strip() or 'some'
        return (f"This checkout has {count} commit(s) that {details['tag']} does not contain; installing it would leave "
                'them behind. Merge them upstream or move them to a branch of their own first')
    return f"Whether {details['tag']} contains this checkout could not be read ({result.stderr.strip() or 'git merge-base failed'})"


def summary(release, details, reasons):
    """What the check says about a release it passed over."""
    return {'version': release['version'], 'tag': release['tag'], 'class': (details or {}).get('class'),
            'signed': bool((details or {}).get('signed')), 'reasons': reasons}


def check(where=None, channel='stable', signers=None):
    """The whole check: the newest release this installation can install, and what it takes.

    Walks down from the newest release to the newest one that is signed, not manual and
    reachable from this version (minimum_from). Releases passed over on the way are named in
    `skipped`, and the newest of them in `newest`, so the console can say that a newer
    release exists and why it is not offered. `refusals` names only problems with installing
    the offered release (and an unreachable source); a note about another release never
    blocks this one.
    """
    current = current_version()
    result = {'current': current, 'available': None, 'refusals': [], 'skipped': [], 'newest': None, 'checked_at': now()}
    try:
        releases = list_releases(where, channel)
    except ReleaseError as error:
        result['refusals'].append(f'The release source could not be read: {error}')
        return result
    with exclusive():
        for release in reversed(releases):
            if key(release['version']) <= key(current['version']):
                break
            details = None
            try:
                details, reasons = examine(release['tag'], current, where, signers)
            except ReleaseError as error:
                reasons = [str(error)]
            if details is not None and not reasons and unreachable(details, current):
                reasons = [unreachable(details, current)]
            if reasons:
                result['skipped'].append(f"{release['tag']} was passed over: " + '; '.join(reasons))
                if result['newest'] is None:
                    result['newest'] = summary(release, details, reasons)
                continue
            behind = left_behind(details, current)
            result.update(available=details, refusals=[behind] if behind else [])
            break
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
    minimum_from this checkout does not meet, one that does not contain every commit of this
    checkout, a `manual` release always, a `rebuild` release unless the operator allowed it,
    and one that migrates environment databases (`attended`) unless the operator allowed that.
    """
    if tag_version(tag) is None:
        raise ReleaseError(f'{tag!r} is not a release tag (vMAJOR.MINOR.PATCH)')
    current = current_version()
    if key(tag_version(tag)) <= key(current['version']):
        raise ReleaseError(f"{tag} is not newer than this installation ({current['version']})")
    with exclusive():
        details, refusals = examine(tag, current, where, signers)
    for reason in (unreachable(details, current), left_behind(details, current)):
        if reason:
            refusals.append(reason)
    if details['class'] == 'rebuild' and 'rebuild' not in allow:
        refusals.append(f'{tag} needs a rebuild: ' + '; '.join(details['reasons'])
                        + '. Pass --allow-class rebuild to apply it, then rebuild')
    if details['class'] in ('attended', 'rebuild') and 'attended' not in allow:
        migrations = migrating(current['commit'], details['commit'])
        if migrations:
            refusals.append(f'{tag} updates services that migrate environment databases: ' + '; '.join(migrations)
                            + '. If it moves back, environment data may need restoring from the backups taken before the '
                            'upgrade. Pass --allow-class attended to apply it')
    if refusals:
        raise ReleaseError('\n'.join(refusals))
    return details
