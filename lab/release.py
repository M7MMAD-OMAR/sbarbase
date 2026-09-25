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
import json
import sys

import release_channel as channel

ROOT = channel.ROOT
RELEASE = ROOT / channel.MANIFEST
PACKAGE = ROOT / 'package.json'


class PrepareError(Exception):
    pass


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise PrepareError(f'{path.name} could not be read: {error}')


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def prepare(version, notes_en, notes_ar, minimum_from=None, migrations=()):
    """Writes the new manifest and package version; returns the commands to run next."""
    if channel.parse(version) is None:
        raise PrepareError(f'{version!r} is not a semantic version (X.Y.Z)')
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
    write(RELEASE, manifest)
    write(PACKAGE, {**package, 'version': version})
    tag = 'v' + version
    return ['git add release.json package.json',
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
