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


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare a Sbarbase release')
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--version', required=True)
    command.add_argument('--notes-en', required=True)
    command.add_argument('--notes-ar', required=True)
    command.add_argument('--minimum-from')
    command.add_argument('--migration', action='append', default=[], help='a declared data migration (makes the release manual)')
    args = parser.parse_args(argv)
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
