"""Prepare a Sbarbase release: the maintainer's side of the release channel.

Usage:
  /usr/bin/python3 lab/release.py prepare --version X.Y.Z --notes-en TEXT --notes-ar TEXT
      [--minimum-from X.Y.Z] [--migration TEXT ...]

Updates release.json and the package.json version, then prints the exact commands that
commit, sign, verify and push the release tag. It never tags or pushes by itself: the
signing key and the push stay in the maintainer's hands. See lab/release_channel.py for
how installations read and verify the result.
"""
import argparse
import contextlib
import gzip
import hashlib
import tempfile
import zlib
from pathlib import Path
import json
import os
import shutil
import signal
import subprocess
import sys

import release_channel as channel

ROOT = channel.ROOT
RELEASE = ROOT / channel.MANIFEST
PACKAGE = ROOT / 'package.json'
# The capability registry names the Sbarbase version too, and deploy/verify refuses a mismatch.
REGISTRY = ROOT / 'deploy' / 'capabilities' / 'registry.json'
# A control store schema change must ship with its schema version and its record in
# src/control/store-schemas.json, which the release check compares between versions: the
# fingerprint test fails when the record does not say what the code creates. The parser test checks
# that the rollback guard reads each schema constant as Bun evaluates it.
STORE_TESTS = ('tests/store-schema-fingerprint.test.ts', 'tests/store-version.test.ts')
PARSER_TEST = 'test_release_channel.StoreVersionParserTests'
STORE_TEST_TIMEOUT = 600


def run_group(command, cwd, timeout):
    """Runs a command in a process group of its own. On a timeout, an interrupt or any other
    exception while it waits, the whole group is killed and reaped before the exception goes on, so
    a child it started (the parity test runs `bun -e`) cannot outlive it. Raises TimeoutExpired."""
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        out, err = process.communicate(timeout=timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        # Bounded: a child that left the group may still hold the pipes open.
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=10)
        raise
    return subprocess.CompletedProcess(command, process.returncode, out, err)


RUNNER = run_group


class PrepareError(Exception):
    pass


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise PrepareError(f'{path.name} could not be read: {error}')


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def dirty():
    """Paths `git status` reports in the checkout, tracked or untracked. prepare checks the tree it
    would release, and the commands it prints commit only the release files, so anything else
    changed would be checked here and then left out of the tag."""
    result = subprocess.run(['git', '-c', 'color.ui=never', 'status', '--porcelain', '-z', '--untracked-files=normal'],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise PrepareError(f'git status failed in the checkout ({result.stderr.strip()}); nothing was written')
    # -z: unquoted paths, one `XY path` per NUL; a rename or copy is followed by its source path.
    fields, paths = iter(result.stdout.split('\0')), []
    for field in fields:
        if field:
            paths.append(field[3:])
            if field[0] in 'RC' or field[1] in 'RC':
                next(fields, None)
    return paths


def store_checks():
    """The commands check_stores runs, from the checkout root."""
    return [[shutil.which('bun') or 'bun', 'test', *STORE_TESTS],
            ['/usr/bin/env', 'PYTHONPATH=lab', '/usr/bin/python3', '-m', 'unittest', PARSER_TEST]]


def check_stores():
    """Runs the store schema tests and the parser test; raises PrepareError unless both pass in time."""
    for command in store_checks():
        shown = ' '.join(command[1:] if command[0] != '/usr/bin/env' else command[2:])
        try:
            result = RUNNER(command, cwd=ROOT, timeout=STORE_TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise PrepareError(f'The control store checks did not finish within {STORE_TEST_TIMEOUT} seconds ({shown}); '
                               'nothing was written')
        except (OSError, subprocess.SubprocessError) as error:
            raise PrepareError(f'The control store checks could not run ({shown}: {error}); nothing was written')
        if result.returncode:
            detail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-20:])
            raise PrepareError('The control store checks fail, so a store schema may have changed without its schema '
                               f'version, or the release check may misread one; fix that before releasing ({shown}). '
                               f'Nothing was written.\n{detail}')


def prepare(version, notes_en, notes_ar, minimum_from=None, migrations=()):
    """Writes the new manifest and package version; returns the commands to run next."""
    if channel.parse(version) is None:
        raise PrepareError(f'{version!r} is not a semantic version (X.Y.Z)')
    changed = dirty()
    if changed:
        shown = ', '.join(changed[:10]) + (f' and {len(changed) - 10} more' if len(changed) > 10 else '')
        raise PrepareError(f'The checkout has changes the release commit would leave out: {shown}. '
                           'Commit or remove them first; nothing was written')
    previous = read(RELEASE) if RELEASE.exists() else None
    package = read(PACKAGE)
    current = previous['version'] if previous else package.get('version')
    if channel.parse(current) is not None and channel.key(version) <= channel.key(current):
        raise PrepareError(f'{version} is not newer than the current release {current}')
    # Every installation that could upgrade before still can, unless the maintainer says otherwise.
    minimum = minimum_from or (previous or {}).get('minimum_from') or current or version
    try:
        manifest = channel.validate({'version': version, 'minimum_from': minimum,
                                     'notes': {'en': notes_en, 'ar': notes_ar}, 'migrations': list(migrations)})
    except channel.ReleaseError as error:
        raise PrepareError(str(error))
    check_stores()
    write(RELEASE, manifest)
    write(PACKAGE, {**package, 'version': version})
    if REGISTRY.exists():
        write(REGISTRY, {**read(REGISTRY), 'version': version})
    tag = 'v' + version
    return ['git add release.json package.json deploy/capabilities/registry.json',
            f'git commit -m "chore(release): {tag}"',
            f'git -c gpg.format=ssh -c user.signingkey=<release signing key> tag -s {tag} -m "Sbarbase {tag}"',
            f'git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=deploy/release-signers verify-tag {tag}',
            f'git push {channel.CANONICAL} HEAD:refs/heads/main refs/tags/{tag}']



REPOSITORY = 'M7MMAD-OMAR/sbarbase'


def gh(*args, check=True):
    result = subprocess.run(['gh', *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise PrepareError(f"gh {args[0]} failed: {result.stderr.strip()}")
    return result


def remote_tag(tag, oid):
    result = channel.git('ls-remote', '--tags', '--refs', channel.CANONICAL,
                         f'refs/tags/{tag}', network=True).stdout.strip()
    if result != f'{oid}\trefs/tags/{tag}':
        raise PrepareError('The canonical remote tag is missing or differs from the verified tag')


def release_records():
    """Authenticated listings include drafts; the tag endpoint is for published releases."""
    pages = json.loads(gh('api', f'repos/{REPOSITORY}/releases', '--paginate', '--slurp').stdout)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise PrepareError('Release history is unverifiable')
    records = [item for page in pages for item in page]
    if any(not isinstance(item, dict) for item in records):
        raise PrepareError('Release history is unverifiable')
    return records


def release_for_tag(tag):
    matches = [item for item in release_records() if item.get('tag_name') == tag]
    if len(matches) > 1:
        raise PrepareError('Multiple releases use this tag; inspect them manually')
    return matches[0] if matches else None


def newest_stable(tag):
    """Never demote a newer published stable version, including paginated history."""
    candidate = channel.tag_version(tag)
    for item in release_records():
        version = channel.tag_version(item.get('tag_name', ''))
        if not item.get('draft') and not item.get('prerelease') and version and not channel.parse(version)[3]:
            if channel.key(version) > channel.key(candidate):
                return False
    return True


def verify_source_archive(path, raw):
    """Bind source payload to the signed commit independently of gzip's encoder."""
    if path.stat().st_size > len(raw) + 1024 * 1024:
        raise PrepareError('Downloaded source archive exceeds its source bound')
    try:
        with gzip.open(path, 'rb') as stream:
            actual = stream.read(len(raw) + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise PrepareError('Downloaded source archive is invalid') from error
    if actual != raw:
        raise PrepareError('Downloaded source payload differs from the signed commit')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source_download(folder, name, raw):
    digest = verify_source_archive(folder / name, raw)
    if (folder / 'SHA256SUMS').read_bytes() != f'{digest}  {name}\n'.encode('utf-8'):
        raise PrepareError('Downloaded archive checksum does not match SHA256SUMS')


def publish(tag, latest=False):
    version = channel.tag_version(tag)
    if version is None:
        raise PrepareError('Invalid release tag')
    oid = channel.object_of('refs/tags/' + tag)
    refusal = channel.verify(oid, name=tag)
    if refusal:
        raise PrepareError(refusal)
    commit = channel.commit_of(oid)
    manifest = channel.manifest(commit, tag)
    remote_tag(tag, oid)
    prerelease = bool(channel.parse(version)[3])
    if latest and prerelease:
        raise PrepareError('A prerelease cannot be marked latest')
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        name = f'sbarbase-{version}.tar.gz'
        archive = base / name
        raw = subprocess.run(['git', '-c', 'tar.umask=0002', 'archive', '--format=tar', f'--prefix=sbarbase-{version}/', commit],
                             cwd=ROOT, capture_output=True, check=True).stdout
        with archive.open('wb') as out:
            with gzip.GzipFile(filename='', mode='wb', fileobj=out, mtime=0) as zipped:
                zipped.write(raw)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum = base / 'SHA256SUMS'
        checksum.write_text(f'{digest}  {name}\n', encoding='utf-8')
        expected = (name, checksum.name)
        release = release_for_tag(tag)
        if release is None:
            created = gh('api', '--method', 'POST', f'repos/{REPOSITORY}/releases',
                         '-f', 'tag_name=' + tag, '-f', 'name=' + tag,
                         '-f', 'body=Source-only release. No binaries are provided.\n\n' + manifest['notes']['en'],
                         '-F', 'draft=true', '-F', 'prerelease=' + str(prerelease).lower())
            release = json.loads(created.stdout)
        if not isinstance(release, dict):
            raise PrepareError('Release metadata is unverifiable')
        if release.get('tag_name') != tag or type(release.get('draft')) is not bool or release.get('prerelease') is not prerelease:
            raise PrepareError('Existing release is published or has conflicting metadata')
        assets = release.get('assets')
        if not isinstance(assets, list) or any(a.get('name') not in expected for a in assets):
            raise PrepareError('Draft has unexpected assets; inspect it manually')
        names = [a['name'] for a in assets]
        if len(names) != len(set(names)):
            raise PrepareError('Draft has duplicate asset names')
        if not release['draft'] and set(names) != set(expected):
            raise PrepareError('Published release is missing an expected asset; inspect it manually')
        if checksum.name in names and name not in names:
            raise PrepareError('Draft checksum has no source archive; inspect it manually')
        if name in names and checksum.name not in names:
            # A previous uploader may use another zlib encoder. Keep its verified
            # archive and generate the missing checksum from those exact bytes.
            existing = base / 'existing'
            existing.mkdir()
            gh('release', 'download', tag, '--repo', REPOSITORY, '--dir', str(existing))
            if {p.name for p in existing.iterdir()} != {name}:
                raise PrepareError('Partial draft assets changed during inspection')
            digest = verify_source_archive(existing / name, raw)
            checksum.write_text(f'{digest}  {name}\n', encoding='utf-8')
        for asset in expected:
            if asset not in names:
                if not release['draft']:
                    raise PrepareError('Published release is missing an expected asset; inspect it manually')
                gh('release', 'upload', tag, str(base / asset), '--repo', REPOSITORY)
        downloaded = base / 'download'
        downloaded.mkdir()
        gh('release', 'download', tag, '--repo', REPOSITORY, '--dir', str(downloaded))
        if {p.name for p in downloaded.iterdir()} != set(expected):
            raise PrepareError('Downloaded assets do not match the expected set')
        verify_source_download(downloaded, name, raw)
        remote_tag(tag, oid)
        mark_latest = latest and newest_stable(tag)
        if release['draft'] or mark_latest:
            gh('release', 'edit', tag, '--repo', REPOSITORY, '--draft=false',
               '--latest=' + ('true' if mark_latest else 'false'))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare a Sbarbase release')
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--version', required=True)
    command.add_argument('--notes-en', required=True)
    command.add_argument('--notes-ar', required=True)
    command.add_argument('--minimum-from')
    command.add_argument('--migration', action='append', default=[], help='a declared data migration (makes the release manual)')
    publisher = sub.add_parser('publish')
    publisher.add_argument('--tag', required=True)
    publisher.add_argument('--latest', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'publish':
        try:
            publish(args.tag, args.latest)
        except (PrepareError, channel.ReleaseError, OSError, subprocess.SubprocessError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        return 0
    try:
        commands = prepare(args.version, args.notes_en, args.notes_ar, args.minimum_from, args.migration)
    except PrepareError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f'release.json and package.json are at {args.version}. Review the diff, then run:')
    for line in commands:
        print('  ' + line)
    if not channel.signers_configured(channel.SIGNERS):
        print('note: deploy/release-signers lists no key yet, so installations refuse this release as unsigned')
    return 0


if __name__ == '__main__':
    sys.exit(main())
