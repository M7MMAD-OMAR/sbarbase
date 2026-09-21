"""Per environment application mail configuration: schema, private storage, load.

One environment at a time. The switch that turns application mail on for an
environment is the presence of one operator supplied file:

    .secrets/upstream/<environment runtime id>-mail.json

mode 0600, holding exactly the keys this module validates. There is no
installation wide mail setting on purpose: a shared setting would let one
environment's abuse spend another environment's provider quota.

What the file mode does and does not protect: mode 0600 keeps the value from
other host users, and nothing else. The value is handed to the environment's
Auth container through `--env-file`, so it appears in the container's
`Config.Env` and anyone who can reach the Docker daemon can read it. That is the
same exposure every credential this installation already writes has, and it is
the reason the mail credential must be scoped to one environment and revocable on
its own.

Rules this module holds to:

- The password arrives on stdin or from a no echo prompt. It is never accepted as
  a command line argument, never written to a log line, and never placed in an
  error message. Errors name a key, never a value.
- `show` is the only sanctioned way to display a configuration: it prints the non
  secret fields and the literal strings `pass set` or `pass empty`.
- `load` returns None only when the file is absent. A file that exists and fails
  validation raises: a half configured relay that silently becomes the noop
  client is worse than a refused start.
- A write refuses to proceed outside the git ignored secrets tree, the same guard
  the durable runtime applies before it persists anything.

The four states an environment's mail can be in, named by the design and rendered
by the console, are `unconfigured`, `applied`, `failed` and `off`. The classified
failure reasons are `invalid_configuration`, `smtp_unreachable`, `smtp_rejected`
and `smtp_tls`; a transport error text is never one of them.
"""
import argparse
import getpass
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIL_DIR = ROOT / '.secrets' / 'upstream'
MAX_BYTES = 8192

STATES = ('unconfigured', 'applied', 'failed', 'off')
STATE_TEXT = {
    'unconfigured': 'Email is off for this environment. Confirmation, recovery and magic link messages are not sent.',
    'applied': "The environment's Auth service was restarted to apply this. Mail is on for this environment only.",
    'failed': 'Email could not be applied. The environment keeps working and sends no mail.',
    'off': 'A previous configuration was removed. Email is off for this environment.',
}
FAILURE_REASONS = ('invalid_configuration', 'smtp_unreachable', 'smtp_rejected', 'smtp_tls')

RUNTIME_ID = re.compile(r'e_[a-f0-9]{24}')
HOST_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
ADDRESS = re.compile(r'[^@\s<>,;:"]+@[^@\s<>,;:"]+\.[^@\s<>,;:"]+')
DURATION = re.compile(r'(?:[0-9]+(?:ns|us|ms|s|m|h))+')
RATE = re.compile(r'(?:[0-9]+|[0-9]+/(?:[0-9]+(?:ns|us|ms|s|m|h))+)')
HEADER = re.compile(r'[A-Za-z0-9-]+')

# Key to validator. The order is the order the tool and the design list them in.
SCHEMA = (
    ('host', 'host'),
    ('port', 'port'),
    ('user', 'text'),
    ('pass', 'secret'),
    ('admin_email', 'address'),
    ('sender_name', 'name'),
    ('reply_to', 'optional_address'),
    ('max_frequency', 'duration'),
    ('otp_exp', 'seconds'),
    ('otp_length', 'otp_length'),
    ('secure_email_change', 'flag'),
    ('autoconfirm', 'flag'),
    ('rate_limit_email_sent', 'rate'),
    ('rate_limit_otp', 'positive'),
    ('rate_limit_verify', 'positive'),
    ('rate_limit_header', 'optional_header'),
)
KEY_ORDER = tuple(key for key, _ in SCHEMA)
SECRET_KEYS = ('user', 'pass')


class MailConfigurationError(Exception):
    """Anything an operator can fix, reported without echoing what they typed."""


class InvalidMailConfiguration(MailConfigurationError):
    """A file that exists and does not satisfy the schema. Never a fallback."""


def _refuse(key, reason):
    raise InvalidMailConfiguration(key + ' ' + reason)


def _duration_seconds(value):
    unit = {'ns': 1e-9, 'us': 1e-6, 'ms': 1e-3, 's': 1, 'm': 60, 'h': 3600}
    total = 0.0
    for number, suffix in re.findall(r'([0-9]+)(ns|us|ms|s|m|h)', value):
        total += int(number) * unit[suffix]
    return total


def _one(key, kind, value):
    if kind == 'host':
        if not isinstance(value, str) or not HOST_NAME.fullmatch(value):
            _refuse(key, 'must be a plain SMTP host name with no scheme, port or whitespace')
        return value
    if kind == 'port':
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            _refuse(key, 'must be an integer from 1 to 65535')
        return value
    if kind in ('text', 'secret'):
        if not isinstance(value, str):
            _refuse(key, 'must be a string')
        if any(character in value for character in '\r\n\x00'):
            _refuse(key, 'must not contain a line break or a null byte')
        if kind == 'secret' and not value:
            _refuse(key, 'must be a non-empty string')
        return value
    if kind == 'name':
        if not isinstance(value, str):
            _refuse(key, 'must be a string')
        if any(character in value for character in '\r\n\x00<>"'):
            _refuse(key, 'must not contain a line break or a mail header delimiter')
        return value
    if kind in ('address', 'optional_address'):
        if not isinstance(value, str):
            _refuse(key, 'must be a string')
        if kind == 'optional_address' and value == '':
            return value
        if not ADDRESS.fullmatch(value):
            _refuse(key, 'must be a single address with no whitespace, display name or angle brackets')
        return value
    if kind == 'duration':
        if not isinstance(value, str) or not DURATION.fullmatch(value):
            _refuse(key, 'must be a Go duration string such as 60s or 1h30m')
        if _duration_seconds(value) <= 0:
            _refuse(key, 'must be greater than zero: at zero the per user cooldown disappears')
        return value
    if kind == 'seconds':
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 604800:
            _refuse(key, 'must be an integer number of seconds from 1 to 604800')
        return value
    if kind == 'otp_length':
        if isinstance(value, bool) or not isinstance(value, int) or not 6 <= value <= 10:
            _refuse(key, 'must be an integer from 6 to 10: upstream clamps anything else')
        return value
    if kind == 'flag':
        if not isinstance(value, bool):
            _refuse(key, 'must be true or false')
        return value
    if kind == 'rate':
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not RATE.fullmatch(str(value)):
            _refuse(key, 'must be a number or a number with an over-time suffix, such as 30 or 30/1h')
        return str(value)
    if kind == 'positive':
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _refuse(key, 'must be an integer of at least 1')
        return value
    if kind == 'optional_header':
        if not isinstance(value, str) or (value and not HEADER.fullmatch(value)):
            _refuse(key, 'must be empty or an HTTP header name')
        return value
    raise MailConfigurationError('unknown field kind: ' + kind)


def validate(payload):
    """Return the normalized configuration, or raise naming the offending key.

    Exactly the keys in SCHEMA, no others. No value ever appears in an error:
    the password is one of the values this function rejects.
    """
    if not isinstance(payload, dict):
        raise InvalidMailConfiguration('the configuration must be a JSON object')
    for key in sorted(set(payload) - set(KEY_ORDER)):
        _refuse(key, 'is not a recognized mail configuration key')
    for key in KEY_ORDER:
        if key not in payload:
            _refuse(key, 'is required and is missing')
    return {key: _one(key, kind, payload[key]) for key, kind in SCHEMA}


def parse(raw):
    """Parse bytes from stdin or a file. Never echoes what it was given."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_BYTES:
            raise InvalidMailConfiguration('the configuration is larger than 8192 bytes')
        try:
            raw = raw.decode()
        except UnicodeDecodeError:
            raise InvalidMailConfiguration('the configuration is not valid UTF-8') from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise InvalidMailConfiguration('the configuration is not valid JSON') from None
    return validate(payload)


def warnings(config):
    """Statements an operator must read, none of which is a refusal."""
    said = []
    if config['autoconfirm']:
        said.append('autoconfirm is true: upstream applies the process wide email rate limit only when '
                    'autoconfirm is false (design section 3.4), so recovery and magic link mail are unthrottled')
    if config['rate_limit_email_sent'] == '0' or config['rate_limit_email_sent'].startswith('0/'):
        said.append('rate_limit_email_sent of zero is not unlimited: upstream rejects every mail attempt '
                    'with 429. Turning mail off is done by removing the configuration (design section 3.2)')
    if config['user'] == '':
        said.append('user is empty: the pinned client sends no SMTP AUTH at all, which only works on a relay '
                    'that accepts anonymous submission')
    if re.fullmatch(r'[0-9.]+', config['host']):
        said.append('host is an address literal: the pinned client verifies the certificate against the host '
                    'name, so a bare address fails the handshake on port 465 and after any STARTTLS upgrade')
    return said


def summarize(config):
    """The non secret view. `show` prints this and nothing else."""
    return {
        'host': config['host'],
        'port': config['port'],
        'user': 'set' if config['user'] else 'empty',
        'pass': 'set' if config['pass'] else 'empty',
        'admin_email': config['admin_email'],
        'sender_name': config['sender_name'] if config['sender_name'] else 'empty',
        'reply_to': config['reply_to'] if config['reply_to'] else 'empty',
        'max_frequency': config['max_frequency'],
        'otp_exp': config['otp_exp'],
        'otp_length': config['otp_length'],
        'secure_email_change': config['secure_email_change'],
        'autoconfirm': config['autoconfirm'],
        'rate_limit_email_sent': config['rate_limit_email_sent'],
        'rate_limit_otp': config['rate_limit_otp'],
        'rate_limit_verify': config['rate_limit_verify'],
        'rate_limit_header': config['rate_limit_header'] if config['rate_limit_header'] else 'empty',
    }


def path_for(runtime_id, directory=None):
    if not isinstance(runtime_id, str) or not RUNTIME_ID.fullmatch(runtime_id):
        raise MailConfigurationError('invalid environment runtime identifier')
    base = Path(directory) if directory else MAIL_DIR
    return base / (runtime_id + '-mail.json')


def load(runtime_id, directory=None):
    """The parsed configuration, or None when the environment has no mail file.

    Raises when the file exists and is invalid, when it is a symlink, and when it
    is readable by anyone but its owner. There is no silent fallback to a default
    or to the noop client.
    """
    path = path_for(runtime_id, directory)
    # The link check runs first on purpose. `exists()` follows a link, so a
    # dangling one reports False and would fall through to the unconfigured
    # return below, which is the silent fallback this function refuses.
    if path.is_symlink():
        raise InvalidMailConfiguration('the mail configuration must be a regular file, not a link')
    if not path.exists():
        return None
    if not path.is_file():
        raise InvalidMailConfiguration('the mail configuration must be a regular file, not a link')
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise InvalidMailConfiguration('the mail configuration is not private: mode ' + oct(mode))
    return parse(path.read_bytes())


def write_config(path, payload, force=False):
    """Create the file with mode 0600. Returns the resulting mode."""
    path = Path(path)
    if force and path.exists():
        if path.is_symlink() or not path.is_file():
            raise MailConfigurationError('refusing to replace something that is not a regular file: ' + str(path))
        path.unlink()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write('\n')
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return stat.S_IMODE(path.stat().st_mode)


def target_path(raw, force=False):
    """An absolute, non symlink path whose parent exists, inside the ignored tree."""
    path = Path(raw)
    if not path.is_absolute():
        raise MailConfigurationError('the path must be absolute, so a relative write cannot land elsewhere')
    if not path.parent.is_dir():
        raise MailConfigurationError('the parent directory does not exist: ' + str(path.parent))
    if path.is_symlink():
        raise MailConfigurationError('refusing to write through a symlink: ' + str(path))
    if path.exists() and not force:
        raise MailConfigurationError('the file exists; pass --force to replace it: ' + str(path))
    if path.exists() and not path.is_file():
        raise MailConfigurationError('refusing to replace something that is not a regular file: ' + str(path))
    return path


def require_git_ignored(path):
    """Refuse a path git does not ignore, the guard the durable runtime applies."""
    result = subprocess.run(['git', 'check-ignore', '-q', str(path)], cwd=ROOT, capture_output=True)
    if result.returncode != 0:
        raise MailConfigurationError('refusing to touch a path outside the ignored secrets tree: ' + str(path))


def refuse_unthrottled(payload, allow_autoconfirm):
    """A file that leaves autoconfirm true needs an explicit, printed override."""
    if payload.get('autoconfirm') is True and not allow_autoconfirm:
        raise MailConfigurationError(
            'autoconfirm is true: with a working relay that leaves recovery and magic link mail unthrottled '
            '(design section 3.4). Pass --allow-autoconfirm to write it anyway.')


def read_stdin(stream):
    raw = stream.buffer.read(MAX_BYTES + 1) if hasattr(stream, 'buffer') else stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise MailConfigurationError('stdin is larger than 8192 bytes')
    return raw


def write(path, payload, force=False, allow_autoconfirm=False):
    """Validate, then write. The password path in one place for both callers."""
    path = target_path(path, force)
    require_git_ignored(path)
    refuse_unthrottled(payload, allow_autoconfirm)
    config = validate(payload)
    mode = write_config(path, config, force)
    if mode != 0o600:
        raise MailConfigurationError('the file was written with mode ' + oct(mode) + ', not 0600')
    return config


def show(path):
    return summarize(_read_config(Path(path)))


def _read_config(path):
    if not path.is_file():
        raise MailConfigurationError('the mail configuration file does not exist: ' + str(path))
    if path.is_symlink():
        raise MailConfigurationError('the mail configuration must be a regular file, not a link: ' + str(path))
    return parse(path.read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write, show or remove one environment mail configuration.')
    commands = parser.add_subparsers(dest='command', required=True)
    writer = commands.add_parser('write', help='validate a JSON object on stdin and write it with mode 0600')
    writer.add_argument('path', help='absolute path, for example .secrets/upstream/e_<id>-mail.json')
    writer.add_argument('--stdin', action='store_true', help='read the JSON object from stdin, at most 8192 bytes')
    writer.add_argument('--force', action='store_true', help='replace an existing file')
    writer.add_argument('--allow-autoconfirm', action='store_true',
                        help='accept autoconfirm true; recovery and magic link mail are then unthrottled')
    shower = commands.add_parser('show', help='print the non secret fields; the password is never printed')
    shower.add_argument('path')
    remover = commands.add_parser('remove', help='remove one environment mail configuration')
    remover.add_argument('path')
    args = parser.parse_args(argv)
    try:
        if args.command == 'write':
            path = Path(args.path)
            if not path.name.endswith('-mail.json'):
                raise MailConfigurationError('the file name must end with -mail.json: ' + str(path))
            if args.stdin:
                raw = read_stdin(sys.stdin)
            else:
                if not sys.stdin.isatty():
                    raise MailConfigurationError('no terminal for a prompt; use --stdin for automation')
                raw = getpass.getpass('Mail configuration (one JSON object, not echoed): ').encode()
            config = write(args.path, parse(raw), args.force, args.allow_autoconfirm)
            for said in warnings(config):
                print('warning: ' + said, file=sys.stderr)
            print('wrote ' + str(args.path) + ' with mode 0600; no value was printed')
            return 0
        if args.command == 'show':
            for key, value in show(Path(args.path)).items():
                print(key + ' ' + str(value))
            return 0
        path = Path(args.path)
        if not path.name.endswith('-mail.json'):
            raise MailConfigurationError('the file name must end with -mail.json: ' + str(path))
        if path.is_symlink():
            raise MailConfigurationError('refusing to remove a symlink: ' + str(path))
        if not path.exists():
            raise MailConfigurationError('the mail configuration file does not exist: ' + str(path))
        require_git_ignored(path)
        path.unlink()
        print('removed ' + str(args.path) + '; this environment is unconfigured until it is reconciled')
        return 0
    except MailConfigurationError as error:
        print('refused: ' + str(error), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('refused: input ended before the configuration was complete', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())