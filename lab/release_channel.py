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
import fnmatch
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
# Files a restart does not read again: the image is built from the Dockerfile (with whatever
# it copies in, image_sources), a restart policy reuses the container compose.yaml created,
# and systemd runs the rendered copy of the unit that `install_server.py supervise --apply`
# installed.
REBUILD = {'Dockerfile': 'the container image definition',
           '.dockerignore': 'what the container image is built from',
           'compose.yaml': 'the container configuration',
           'deploy/sbarbase.service': 'the installed systemd unit'}
# The TLS proxy runs as its own unit the operator installs (lab/vm-milestones.sh calls it
# sbarbase-tls.service; a server may name it otherwise), which an update never restarts. It
# imports only Node built-ins, so this file is everything it runs.
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
# What every control store's schema is, per version (tests/store-schema-fingerprint.test.ts keeps it
# true). A move that changes it changes a store the old and new console processes would both open,
# so the check reports it beside the class, never as one.
SCHEMAS = 'src/control/store-schemas.json'
# Where each control store declares its schema version (src/control/store-version.ts and the
# catalog's own ladder): read by the rollback guard (lab/upgrade.py schema_support), never to
# decide stores_changed.
STORES = {'src/control/catalog.ts': 'CATALOG_SCHEMA_VERSION', 'src/control/keys.ts': 'KEY_STORE_SCHEMA_VERSION',
          'src/control/access-tokens.ts': 'ACCESS_TOKEN_SCHEMA_VERSION',
          'src/control/deployment-setup.ts': 'DEPLOYMENT_SETUP_SCHEMA_VERSION'}
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
        # Output is parsed: the operator's colour settings must not reach it.
        result = subprocess.run(['git', '-c', 'color.ui=never', *args], cwd=ROOT, text=True, capture_output=True, env=env,
                                timeout=TIMEOUT if network else None)
    except subprocess.TimeoutExpired:
        raise ReleaseError(f'git {args[0]} did not finish within {TIMEOUT} seconds')
    if check and result.returncode:
        raise ReleaseError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result


@contextlib.contextmanager
def exclusive(path, refusal, wait=0, error=ReleaseError):
    """Holds an exclusive flock on path, waiting up to `wait` seconds, or raises error(refusal).
    lab/upgrade.py takes its own locks through this too, with its own error class."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    with path.open('a') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise error(refusal) from None
                time.sleep(.2)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def reading():
    """One check or start at a time reads and writes the private release refs. A run that
    cannot take the lock within LOCK_WAIT seconds fails; it never reads a ref another run is
    writing."""
    return exclusive(LOCK, 'Another release check or update is reading the release source; try again in a moment', LOCK_WAIT)


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
    listed = git('ls-remote', '--tags', '--refs', where or source(), network=True).stdout
    releases = []
    for line in listed.splitlines():
        # Only the names count: what is applied comes from the tag fetched and verified later.
        tag = line.partition('\t')[2].removeprefix('refs/tags/')
        version = tag_version(tag)
        if version is None or (parse(version)[3] and channel != 'preview'):
            continue
        releases.append({'version': version, 'tag': tag})
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

    `ref` may be a ref or an object id. examine() passes the id object_of() read once from
    the fetched ref, so every step here reads that one object. `name` is the release tag the
    object must call itself (by default the last part of the ref). Fails closed: no signers
    file or no key in it, a lightweight or unsigned tag, a key that is not listed, and a tag
    object whose own name is not the expected one.
    """
    signers = Path(signers or SIGNERS)
    tag = name or ref.rsplit('/', 1)[-1]
    if not signers_configured(signers):
        return f'No release signing key is listed in {signers.name}; every release is refused until one is'
    if not shutil.which('ssh-keygen'):
        # Still refused, but for the real reason: a container image built before
        # openssh-client was added to it cannot check any signature.
        return 'ssh-keygen is not installed, so no release signature can be checked; rebuild the container image'
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


# Lock files read at a commit, by (commit id, lock name). A commit id names its content for
# good, so a lock once read is never read again, however many diffs look at it.
LOCKS_AT = {}
COMMIT_ID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def lock_at(commit, name):
    """A lock file as it is at a commit; None when that version has no such file."""
    if (commit, name) in LOCKS_AT:
        return LOCKS_AT[commit, name]
    result = git('show', f'{commit}:lab/{name}', check=False)
    if result.returncode:
        # Not remembered: a commit this checkout does not have yet may arrive with a fetch.
        return None
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise ReleaseError(f'lab/{name} at {commit[:12]} is not JSON')
    if COMMIT_ID.fullmatch(commit):
        LOCKS_AT[commit, name] = value
    return value


def entries(lock):
    if not isinstance(lock, dict):
        return {}
    if isinstance(lock.get('id'), str):
        return {'default': lock}
    return {name: value for name, value in lock.items() if isinstance(value, dict) and isinstance(value.get('id'), str)}


def pin_diffs(current, target):
    """(lock name, entry, before, after) for every pinned image that differs between two
    commits; before or after is {} where that version has no such pin."""
    for name in install_server.LOCKS:
        before, after = entries(lock_at(current, name)), entries(lock_at(target, name))
        for entry in sorted(set(before) | set(after)):
            old, new = before.get(entry, {}), after.get(entry, {})
            if old.get('id') != new.get('id'):
                yield name, entry, old, new


def changes(current, target, diffs=None):
    """Pinned images that differ between two commits, as the console and lab/upgrade.py show them."""
    return [{'image': f'{name}:{entry}', 'from': old.get('tag', 'none'), 'to': new.get('tag', 'none')}
            for name, entry, old, new in (pin_diffs(current, target) if diffs is None else diffs)]


def database_changes(diffs):
    """One sentence per PostgreSQL image entry that differs: replacing the database container is
    lab/migrate-generation.py's job, never an upgrade's."""
    return [f"lab/{DATABASE_LOCK} changes the PostgreSQL image ({old.get('tag', 'none')} -> {new.get('tag', 'none')}); "
            "that is lab/migrate-generation.py's job" for name, _, old, new in diffs if name == DATABASE_LOCK]


def migrating(diffs):
    """Why a move changes environment databases as services start: one sentence per Auth,
    Storage or Realtime pin that differs."""
    return [f"lab/{name} changes the {MIGRATING[name, entry]} image ({old.get('tag', 'none')} -> {new.get('tag', 'none')}); "
            f'{MIGRATING[name, entry]} migrates each environment database when it starts, so going back may need the '
            'environment backups taken before the upgrade'
            for name, entry, old, new in diffs if (name, entry) in MIGRATING]


def image_sources(commit):
    """What the Dockerfile at a commit copies from the checkout into the image: the sources of
    its COPY and ADD instructions (one from another image, --from, is not the checkout's), so
    a file the image bakes in is never missed by a hand-kept list."""
    result = git('show', f'{commit}:Dockerfile', check=False)
    sources = []
    for line in result.stdout.replace('\\\n', ' ').splitlines() if not result.returncode else []:
        words = line.split()
        if not words or words[0].upper() not in ('COPY', 'ADD') or any(word.startswith('--from') for word in words):
            continue
        rest = line.split(None, 1)[1] if len(words) > 1 else ''
        arguments = [word for word in words[1:] if not word.startswith('--')]
        if rest.lstrip().startswith('['):
            try:
                arguments = json.loads(rest[rest.index('['):])
            except ValueError:
                continue
        sources += [source.removeprefix('./') for source in arguments[:-1] if isinstance(source, str)]
    return sources


def copied(path, sources):
    """Whether a changed path is one of the image's sources, or under one (a directory, `.`, a glob)."""
    return any(source in ('.', '') or path == source or path.startswith(source.rstrip('/') + '/')
               or fnmatch.fnmatchcase(path, source) for source in sources)


def findings(current, target, release=None, diffs=None):
    """What moving from one commit to another needs: {manual, rebuild, attended}, each a list of reasons."""
    if release is None:
        try:
            release = manifest(target)
        except ReleaseError:
            release = {'migrations': []}
    diffs = list(pin_diffs(current, target)) if diffs is None else diffs
    changed = set(git('diff', '--name-only', current, target).stdout.split())
    manual = database_changes(diffs) + [f'The release declares a data migration: {migration}'
                                        for migration in release.get('migrations') or []]
    rebuild = [f'{path} changes ({what}); a restart does not pick it up' for path, what in REBUILD.items() if path in changed]
    sources = image_sources(target)
    rebuild += [f'{path} changes (the container image copies it in); a restart does not pick it up'
                for path in sorted(changed - set(REBUILD) - set(PROXY)) if copied(path, sources)]
    rebuild += [f'{path} changes ({what}); it runs as its own unit (named {PROXY_UNIT} in the VM rehearsal), '
                'which an update does not restart: restart that unit after the update'
                for path, what in PROXY.items() if path in changed]
    return {'manual': manual, 'rebuild': rebuild, 'attended': migrating(diffs)}


# A numeric literal as TypeScript writes one: decimal, hex, octal or binary, with _ separators.
NUMBER = r'0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*|0[oO][0-7](?:_?[0-7])*|0[bB][01](?:_?[01])*|[0-9](?:_?[0-9])*'
# The one declaration read as a value, complete on its own line: the semicolon is required, so a
# value that goes on to the next line (`=0` then `+1;`) or anything after the literal is unknown.
STRICT = r'\s*export\s+const\s+{name}(?:\s*:\s*number)?\s*=\s*({number})\s*(?:as\s+const\s*)?;\s*(?://.*)?'
# Words that declare, export or import a name: a line with one of them, or one that assigns to
# the name, is a declaration, and only the strict form may be one.
DECLARING = re.compile(r'\b(?:export|import|const|let|var|declare|enum|namespace)\b')


def schema_reading(text, constant):
    """('absent', 0), ('declared', value) or ('unknown', None) for a constant in a source file.

    Absent only when the name does not occur in the file at all. Declared when exactly one line
    declares it and that line is the strict form. Anything else that may declare it is unknown:
    another declaration or export form, a re-export, a duplicate, a comment inside the declaration,
    a value that is not one numeric literal or goes on past its line. A module with any unicode
    escape is unknown too, since `\\u0058` may spell the name on any line. Plain uses of the name
    elsewhere (a comparison, a call) are fine.

    Known limit (X2): this reads lines, not TypeScript. A strict line inside a block comment or a
    template literal, with the real declaration split so that no line both names and assigns the
    constant (`export const` / `X` / `= 1;` on three lines), reads as the commented value.
    `lab/release.py prepare` catches it by running StoreVersionParserTests, which compares this
    parser with what Bun evaluates. Only the rollback guard reads constants this way, where reading
    too little only refuses more; whether a release changes a store is read from the recorded
    schemas instead (stores_changed)."""
    if '\\u' in text:
        return 'unknown', None
    name = re.escape(constant)
    lines = [line for line in text.splitlines() if re.search(rf'\b{name}\b', line)]
    if not lines:
        return 'absent', 0
    declaring = [line for line in lines if DECLARING.search(line) or re.search(rf'\b{name}\s*(?:[-+*/%&|^]|\*\*|<<|>>>?|\?\?|&&|\|\|)?=(?!=)', line)]
    found = re.fullmatch(STRICT.format(name=name, number=NUMBER), declaring[0].rstrip()) if len(declaring) == 1 else None
    if not found:
        return 'unknown', None
    literal = found.group(1).replace('_', '')
    return 'declared', int(literal, 0) if literal[:2].lower() in ('0x', '0o', '0b') else int(literal)


def schema_constant(text, constant):
    """A constant's value in a source file: 0 when absent, None when unknown (schema_reading)."""
    return schema_reading(text, constant)[1]


# Only regular files are read: a symlink or a submodule is unknown.
REGULAR = ('100644', '100755')
# Every git call here parses its output, whichever runner runs it.
PLAIN = ('-c', 'color.ui=never')


def regular_blob(commit, path, run):
    """('absent' | 'regular' | 'unknown') for a path at a commit, from its git ls-tree entry."""
    if run(*PLAIN, 'cat-file', '-e', f'{commit}^{{commit}}').returncode:
        return 'unknown'
    listed = run(*PLAIN, 'ls-tree', commit, '--', path)
    if listed.returncode:
        return 'unknown'
    if not listed.stdout.strip():
        return 'absent'
    entry = listed.stdout.split('\t', 1)[0].split()
    return 'regular' if len(entry) == 3 and entry[0] in REGULAR and entry[1] == 'blob' else 'unknown'


def store_reading(commit, path, constant, run=None):
    """One store's schema constant at a commit, as schema_reading gives it: absent when the commit
    has no such file, unknown when the commit or the file cannot be read or decoded or is not a
    regular file (a symlink). Absent is only what git ls-tree says is absent; any other failure,
    such as a missing or corrupt object, is unknown. `run` runs git in another checkout
    (lab/upgrade.py passes its own)."""
    run = run or (lambda *args: git(*args, check=False))
    try:
        kind = regular_blob(commit, path, run)
        if kind != 'regular':
            return ('absent', 0) if kind == 'absent' else ('unknown', None)
        result = run(*PLAIN, 'show', f'{commit}:{path}')
    except UnicodeDecodeError:
        return 'unknown', None
    return ('unknown', None) if result.returncode else schema_reading(result.stdout, constant)


def store_version(commit, path, constant, run=None):
    """One store's schema version at a commit: 0 when absent, None when unknown (store_reading)."""
    return store_reading(commit, path, constant, run)[1]


def store_versions(commit):
    """{constant: version} of every control store schema at a commit (store_version)."""
    return {constant: store_version(commit, path, constant) for path, constant in STORES.items()}


def store_schemas(commit, run=None):
    """The recorded control store schemas (SCHEMAS) at a commit, {store: {version, sha256}}, or None
    when they are missing, unreadable, not a regular file, not JSON or not that shape.
    tests/store-schema-fingerprint.test.ts keeps the file equal to what the code creates."""
    run = run or (lambda *args: git(*args, check=False))
    try:
        if regular_blob(commit, SCHEMAS, run) != 'regular':
            return None
        result = run(*PLAIN, 'show', f'{commit}:{SCHEMAS}')
        value = None if result.returncode else json.loads(result.stdout)
    except ValueError:
        return None
    hexadecimal = re.compile(r'[0-9a-f]{64}')
    if not isinstance(value, dict) or not value or not all(
            isinstance(name, str) and isinstance(item, dict) and set(item) == {'version', 'sha256'}
            and type(item['version']) is int and item['version'] >= 0
            and isinstance(item['sha256'], str) and hexadecimal.fullmatch(item['sha256'])
            for name, item in value.items()):
        return None
    return value


def stores_changed(current, target):
    """Whether moving between two commits changes a control store schema: the recorded schemas
    differ as data. Missing or unreadable at either commit counts as a change, so a move from a
    version before the record (v0.6.0 and earlier) always does. As a safety net, a schema constant
    the rollback guard reads differently at the two commits (store_version), or cannot read at
    either one where its module exists, is a change too, whatever the record says."""
    try:
        before, after = store_schemas(current), store_schemas(target)
        if before is None or after is None or before != after:
            return True
        for path, constant in STORES.items():
            (was, old), (now, new) = store_reading(current, path, constant), store_reading(target, path, constant)
            if 'unknown' in (was, now) or old != new:
                return True
    except (ReleaseError, OSError, subprocess.SubprocessError):
        return True
    return False


def kind_of(found):
    """('safe' | 'attended' | 'rebuild' | 'manual', reasons) from findings(). The most demanding
    class wins (manual, then rebuild, then attended); the reasons list every finding from that
    class down, so a release that needs a rebuild and also migrates says both."""
    order = ('manual', 'rebuild', 'attended')
    for index, kind in enumerate(order):
        if found[kind]:
            return kind, [reason for name in order[index:] for reason in found[name]]
    return 'safe', []


def classify(current, target, release=None):
    """('safe' | 'attended' | 'rebuild' | 'manual', reasons) for moving from one commit to another."""
    return kind_of(findings(current, target, release))


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

    Returns (details, blockers, attended): blockers are what makes this release itself
    impossible to install here (no valid signature, a manual migration), attended the reasons
    it migrates environment databases. The private ref is read exactly once: its object id is
    verified, and that same id is peeled to the commit whose manifest and diff are read, so
    nothing that moves the ref afterwards changes what was checked.
    """
    oid = object_of(fetch_release(tag, where))
    refusal = verify(oid, signers, name=tag)
    commit = commit_of(oid)
    release = manifest(commit, tag)
    diffs = list(pin_diffs(current['commit'], commit))
    found = findings(current['commit'], commit, release, diffs)
    kind, reasons = kind_of(found)
    details = {'version': release['version'], 'tag': tag, 'commit': commit, 'class': kind, 'reasons': reasons,
               'notes': release['notes'], 'changes': changes(current['commit'], commit, diffs), 'signed': refusal is None,
               'minimum_from': release['minimum_from'], 'stores_changed': stores_changed(current['commit'], commit)}
    blockers = [refusal] if refusal else []
    if kind == 'manual':
        blockers.append(f'{tag} cannot be applied as an upgrade: ' + '; '.join(reasons))
    return details, blockers, found['attended']


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
    the offered release; a note about another release never blocks this one.

    A source that cannot be read raises ReleaseError: that is no result at all, so the last
    good one stays where it is (lab/upgrade.py `channel` writes only a result).
    """
    current = current_version()
    result = {'current': current, 'available': None, 'refusals': [], 'skipped': [], 'newest': None, 'checked_at': now()}
    try:
        releases = list_releases(where, channel)
    except ReleaseError as error:
        raise ReleaseError(f'The release source could not be read: {error}') from None
    # With no signing key listed every release is refused for that one reason: naming the newest
    # is enough, and fetching every older one would only repeat it.
    keyless = not signers_configured(Path(signers or SIGNERS))
    with reading():
        for release in reversed(releases):
            if key(release['version']) <= key(current['version']):
                break
            details = None
            try:
                details, reasons, _ = examine(release['tag'], current, where, signers)
            except ReleaseError as error:
                reasons = [str(error)]
            if details is not None and not reasons and unreachable(details, current):
                reasons = [unreachable(details, current)]
            if reasons:
                result['skipped'].append(f"{release['tag']} was passed over: " + '; '.join(reasons))
                if result['newest'] is None:
                    result['newest'] = summary(release, details, reasons)
                if keyless:
                    break
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
    with reading():
        details, refusals, attended = examine(tag, current, where, signers)
    for reason in (unreachable(details, current), left_behind(details, current)):
        if reason:
            refusals.append(reason)
    if details['class'] == 'rebuild' and 'rebuild' not in allow:
        refusals.append(f'{tag} needs a rebuild: ' + '; '.join(details['reasons'])
                        + '. Pass --allow-class rebuild to apply it, then rebuild')
    if details['class'] in ('attended', 'rebuild') and attended and 'attended' not in allow:
        refusals.append(f'{tag} updates services that migrate environment databases: ' + '; '.join(attended)
                        + '. If it moves back, environment data may need restoring from the backups taken before the '
                        'upgrade. Pass --allow-class attended to apply it')
    if refusals:
        raise ReleaseError('\n'.join(refusals))
    return details
