"""Write the 0600 operator bootstrap file an install reads.

`lab/install_server.py install --bootstrap-file` and
`deploy/server-acceptance.sh --bootstrap-file` take the operator identity from a
0600 JSON object with exactly `email`, `password` and `organization`, piped on
stdin. Typing that file by hand leaves the password in the shell's history, and
passing it as an argument puts it in the process list, so this writes it from a
prompt (no echo, no history) or from bounded stdin for automation. It never
prints the password, and it never writes to a path it was not given.
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

FIELDS = {'email', 'organization', 'password'}
MIN_PASSWORD = 12
MAX_BYTES = 8192


class InputError(Exception):
    """Anything the operator can fix, reported without echoing what they typed."""


def parse_payload(raw):
    try:
        payload = json.loads(raw)
    except ValueError:
        raise InputError('stdin is not JSON')
    return validate(payload)


def validate(payload):
    if not isinstance(payload, dict):
        raise InputError('the payload must be a JSON object')
    if set(payload) != FIELDS:
        raise InputError('the object must hold exactly ' + ', '.join(sorted(FIELDS)))
    email = payload['email']
    organization = payload['organization']
    password = payload['password']
    for name, value in (('email', email), ('organization', organization), ('password', password)):
        if not isinstance(value, str) or not value:
            raise InputError(name + ' must be a non-empty string')
    if email.count('@') != 1 or any(character.isspace() for character in email):
        raise InputError('email must be a single address with no whitespace')
    if organization != organization.strip():
        raise InputError('organization must not begin or end with whitespace')
    if len(password) < MIN_PASSWORD:
        raise InputError('password must be at least ' + str(MIN_PASSWORD) + ' characters')
    return {'email': email, 'organization': organization, 'password': password}


def prompt(stdin):
    if not stdin.isatty():
        raise InputError('no terminal for a prompt; use --stdin for automation')
    email = input('Owner email: ').strip()
    organization = input('Organization name: ').strip()
    password = getpass.getpass('Password (at least ' + str(MIN_PASSWORD) + ' characters): ')
    if password != getpass.getpass('Repeat password: '):
        raise InputError('passwords do not match')
    return validate({'email': email, 'organization': organization, 'password': password})


def target_path(raw):
    path = Path(raw)
    if not path.is_absolute():
        raise InputError('the path must be absolute, so a relative write cannot land elsewhere')
    if not path.parent.is_dir():
        raise InputError('the parent directory does not exist: ' + str(path.parent))
    if path.is_symlink():
        raise InputError('refusing to write through a symlink: ' + str(path))
    return path


def write_payload(path, payload, force):
    """Create the file with mode 0600 and refuse an existing one unless forced."""
    if force and path.exists():
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(payload, handle)
            handle.write('\n')
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path.stat().st_mode & 0o777


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write the 0600 operator bootstrap file an install reads.')
    parser.add_argument('path', help='absolute path of the file to create, for example /root/sbarbase-operator.json')
    parser.add_argument('--force', action='store_true', help='replace an existing file')
    parser.add_argument('--stdin', action='store_true',
                        help='read a JSON object with email, password and organization from stdin, at most 8192 bytes')
    args = parser.parse_args(argv)
    try:
        path = target_path(args.path)
        if path.exists() and not args.force:
            raise InputError('the file exists; pass --force to replace it: ' + str(path))
        if args.stdin:
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise InputError('stdin is larger than ' + str(MAX_BYTES) + ' bytes')
            payload = parse_payload(raw)
        else:
            payload = prompt(sys.stdin)
        mode = write_payload(path, payload, args.force)
        if mode != 0o600:
            raise InputError('the file was written with mode ' + oct(mode) + ', not 0600')
        print('wrote ' + str(path) + ' with mode 0600; the install reads it on stdin and never prints it')
    except InputError as error:
        print('refused: ' + str(error), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('refused: input ended before the operator identity was complete', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
