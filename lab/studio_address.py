"""Give every environment's Studio its own public address under one domain, set once.

Usage:
  /usr/bin/python3 lab/studio_address.py plan DOMAIN
  sudo /usr/bin/python3 lab/studio_address.py apply DOMAIN [--service sbarbase] [--proxy-service sbarbase-proxy]
  /usr/bin/python3 lab/studio_address.py verify DOMAIN

DOMAIN is a name that holds no other site, such as example.com. `plan` prints the three things the
operator provides once: a wildcard DNS record, a certificate that covers the wildcard, and the
setting. `apply` writes that setting as a systemd drop-in for the console and for the TLS proxy
(both read SBARBASE_STUDIO_HOST) and restarts them. `verify` checks a sample address from outside.
New projects and environments need nothing more: each gets studio-<id>.DOMAIN by itself.
"""
import argparse
import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.request

LABEL = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')
SAMPLE = 'studio-' + '0' * 24
DROPIN = 'studio-host.conf'


class AddressError(Exception):
    pass


def domain_name(value):
    """The bare domain, or an AddressError saying what is wrong with it."""
    name = (value or '').strip().lower().rstrip('.')
    if '://' in name or '/' in name or ':' in name:
        raise AddressError('give a bare domain such as example.com, without https:// or a path')
    if '*' in name or '{' in name:
        raise AddressError('give the domain only; the wildcard and the {id} part are added for you')
    labels = name.split('.')
    if len(labels) < 2 or not all(LABEL.match(label) for label in labels):
        raise AddressError('that is not a domain name such as example.com')
    if name.endswith('.localhost') or name == 'localhost':
        raise AddressError('a public domain is needed; .localhost addresses work only through an SSH tunnel')
    if len(SAMPLE + '.' + name) > 253:
        raise AddressError('that domain is too long')
    return name


def pattern(domain):
    return 'studio-{id}.' + domain_name(domain)


def plan(domain):
    name = domain_name(domain)
    return {
        'pattern': 'studio-{id}.' + name,
        'dns': '*.' + name + '  (an A record to this server, proxied if you use Cloudflare)',
        'certificate': ['*.' + name, name],
        'setting': 'SBARBASE_STUDIO_HOST=studio-{id}.' + name,
        'example': SAMPLE + '.' + name,
    }


def describe(domain):
    value = plan(domain)
    return '\n'.join([
        f"Studio addresses will be {value['pattern']}.",
        'Provide these once; new projects then need nothing:',
        f"  1. DNS: {value['dns']}",
        f"  2. Certificate covering: {', '.join(value['certificate'])}",
        '     (Cloudflare Origin CA: create one with those hostnames and install it on the TLS proxy.)',
        f"  3. Setting: {value['setting']}  (apply writes it for the console and the TLS proxy)",
        'The domain must hold no other site you care about: the wildcard answers every unnamed name under it.',
        'On Cloudflare Free the pattern must stay one level under the domain, which this one does.',
    ])


def run_command(arguments):
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def write_dropin(root, service, value):
    directory = os.path.join(root, service + '.service.d')
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, DROPIN)
    with open(path, 'w') as handle:
        handle.write('[Service]\nEnvironment=SBARBASE_STUDIO_HOST=' + value + '\n')
    os.chmod(path, 0o644)
    return path


def flag_conflict(unit_text, value):
    """The proxy's own --studio-host wins over the environment, so a different one must be resolved first."""
    found = re.search(r'--studio-host\s+(\S+)', unit_text or '')
    return found.group(1) if found and found.group(1) != value else None


def apply(domain, root='/etc/systemd/system', service='sbarbase', proxy='sbarbase-proxy', run=run_command, geteuid=os.geteuid):
    value = pattern(domain)
    if geteuid() != 0:
        raise AddressError('run apply with sudo: it writes systemd drop-ins')
    conflict = flag_conflict(run(['systemctl', 'cat', proxy + '.service']).stdout, value)
    if conflict:
        raise AddressError(f'{proxy} already passes --studio-host {conflict}; remove that flag or use the same pattern first')
    written = [write_dropin(root, unit, value) for unit in (service, proxy)]
    reload = run(['systemctl', 'daemon-reload'])
    if reload.returncode != 0:
        raise AddressError('systemctl daemon-reload failed: ' + (reload.stderr or '').strip())
    # The proxy first: it reads the pattern when it starts, and the console reads it at its own start.
    for unit in (proxy, service):
        restart = run(['systemctl', 'restart', unit + '.service'])
        if restart.returncode != 0:
            raise AddressError(f'restarting {unit} failed: ' + (restart.stderr or '').strip())
    return written


def verify(domain, resolve=socket.getaddrinfo, fetch=None):
    """(ok, lines): does a sample address resolve, present a valid certificate and reach Sbarbase?"""
    name = domain_name(domain)
    host = SAMPLE + '.' + name
    lines = []
    try:
        resolve(host, 443)
        lines.append(f'DNS: {host} resolves')
    except OSError:
        return False, [f'DNS: {host} does not resolve yet. Add the wildcard record *.{name} and wait a minute.']
    try:
        status = (fetch or fetch_status)(host)
    except ssl.SSLError as error:
        return False, lines + [f'Certificate: not accepted for {host} ({getattr(error, 'reason', None) or error}). It must cover *.{name}.']
    except OSError as error:
        return False, lines + [f'Connection: could not reach https://{host}/ ({error}).']
    if status in (401, 404):
        lines.append(f'Sbarbase answered {status}: the address reaches Studio\'s own sign-in check.')
        return True, lines
    if status == 421:
        return False, lines + [f'The server refused the address (421): SBARBASE_STUDIO_HOST is not set to studio-{{id}}.{name} on the console and the TLS proxy.']
    return False, lines + [f'Unexpected answer {status} from https://{host}/.']


def fetch_status(host):
    request = urllib.request.Request('https://' + host + '/', method='GET', headers={'user-agent': 'sbarbase-studio-address-check'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main(argv=None):
    parser = argparse.ArgumentParser(description="Public address for every environment's Studio")
    parser.add_argument('command', choices=('plan', 'apply', 'verify'))
    parser.add_argument('domain')
    parser.add_argument('--service', default='sbarbase')
    parser.add_argument('--proxy-service', default='sbarbase-proxy')
    parser.add_argument('--dropin-root', default='/etc/systemd/system')
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            print(describe(args.domain))
            return 0
        if args.command == 'apply':
            for path in apply(args.domain, args.dropin_root, args.service, args.proxy_service):
                print('wrote ' + path)
            print('restarted ' + args.proxy_service + ' and ' + args.service + '. Check it with:')
            print('  /usr/bin/python3 lab/studio_address.py verify ' + domain_name(args.domain))
            return 0
        ok, lines = verify(args.domain)
        print('\n'.join(lines))
        return 0 if ok else 1
    except AddressError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
