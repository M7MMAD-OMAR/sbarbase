"""Bounded domain setup, private references and verified transport observations.

Configuration rendering has no Docker, DNS-provider, issuance or mail effects.
Probes accept an operator-selected address, never resolve a browser-supplied URL.
An owned CA is explicitly reported as local trust, not public trust.
"""
import argparse
import datetime
import hashlib
import ipaddress
import json
import os
import re
import smtplib
import socket
import ssl
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 32768
# Official Caddy 2.11.7-alpine, Linux amd64 manifest metadata verified 2026-10-06.
# The image has not been pulled or run by this source role.
CADDY_IMAGE = 'library/caddy@sha256:173b26306d711395accaeba8b67afcaad2a085ccb1e6bf26010bfe8c095a5229'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
REFERENCE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
HOST = re.compile(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*')
FIELDS = {'version', 'profile', 'public_url', 'ipv4', 'ipv6', 'provider_access',
          'tls', 'acme_email', 'certificate_ref', 'site_url', 'redirect_urls', 'smtp_ref'}


class SetupRefusal(ValueError):
    """Errors contain fixed field names, never supplied values or paths."""


def refuse(field):
    raise SetupRefusal(field)


def parse(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        refuse('configuration_size')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                refuse('duplicate_field')
            out[key] = value
        return out
    try:
        return validate(json.loads(raw.decode('utf-8'), object_pairs_hook=unique))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        refuse('configuration_json')


def address(value, profile, origin=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r'[\s\x00-\x20\x7f\\,]', value):
        refuse('public_url' if origin else 'redirect_url')
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError:
        refuse('public_url' if origin else 'redirect_url')
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or
            p.password is not None or '?' in value or '#' in value):
        refuse('public_url' if origin else 'redirect_url')
    if origin and (p.path not in ('', '/') or p.scheme != 'https'):
        refuse('public_url')
    if '*' in value or '%' in p.netloc or (port is not None and not 1 <= port <= 65535):
        refuse('redirect_url')
    if profile == 'public':
        if p.scheme != 'https' or port not in (None, 443) or not HOST.fullmatch(p.hostname):
            refuse('public_https')
        if '.' not in p.hostname or p.hostname.endswith(('.localhost', '.local', '.internal', '.test', '.invalid', '.example', '.home.arpa', '.ts.net')):
            refuse('public_hostname')
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            pass
        else:
            refuse('public_hostname')
    else:
        if p.hostname not in ('localhost', '127.0.0.1', '::1'):
            refuse('local_loopback')
    return value.rstrip('/') if origin else value


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        refuse('configuration_fields')
    if type(value['version']) is not int or value['version'] != 1:
        refuse('version')
    profile = value['profile']
    if profile not in ('local', 'public') or type(value['provider_access']) is not bool:
        refuse('profile')
    out = dict(value)
    out['public_url'] = address(value['public_url'], profile, origin=True)
    out['site_url'] = address(value['site_url'], profile)
    redirects = value['redirect_urls']
    if not isinstance(redirects, list) or len(redirects) > 50 or len(set(str(x) for x in redirects)) != len(redirects):
        refuse('redirect_urls')
    out['redirect_urls'] = [address(x, profile) for x in redirects]
    for field, version in (('ipv4', 4), ('ipv6', 6)):
        items = value[field]
        if not isinstance(items, list) or len(items) > 16:
            refuse(field)
        normalized = []
        for item in items:
            try:
                ip = ipaddress.ip_address(item) if isinstance(item, str) else None
            except ValueError:
                refuse(field)
            if ip is None or ip.version != version or (profile == 'public' and not ip.is_global) or (profile == 'local' and not ip.is_loopback):
                refuse(field)
            normalized.append(str(ip))
        if len(set(normalized)) != len(normalized):
            refuse(field)
        out[field] = normalized
    if not out['ipv4'] and not out['ipv6']:
        refuse('dns_addresses')
    if profile == 'local':
        hostname = urlsplit(out['public_url']).hostname
        listeners = out['ipv4'] + out['ipv6']
        if hostname == 'localhost':
            if any(item not in ('127.0.0.1', '::1') for item in listeners):
                refuse('local_listener')
        elif hostname not in listeners:
            refuse('local_listener')
    if value['tls'] not in ('managed', 'external', 'local') or (profile == 'local') != (value['tls'] == 'local'):
        refuse('tls')
    email = value['acme_email']
    if not isinstance(email, str) or len(email) > 254 or (email and not re.fullmatch(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', email)):
        refuse('acme_email')
    if value['tls'] == 'managed' and not email:
        refuse('acme_email')
    for field in ('certificate_ref', 'smtp_ref'):
        ref = value[field]
        if ref is not None and (not isinstance(ref, str) or not REFERENCE.fullmatch(ref)):
            refuse(field)
    if (value['tls'] == 'external') != (value['certificate_ref'] is not None):
        refuse('certificate_ref')
    return out


def preview(config, runtime):
    config = validate(config)
    if not isinstance(runtime, str) or not RUNTIME.fullmatch(runtime):
        refuse('runtime')
    host = urlsplit(config['public_url']).hostname
    records = [{'name': host, 'type': kind, 'value': ip} for kind, field in (('A', 'ipv4'), ('AAAA', 'ipv6')) for ip in config[field]]
    warnings = ['Changing the public origin changes Auth callbacks, email links, clients and signed URLs. Reconcile every affected Auth service.']
    if not config['provider_access']:
        warnings.append('DNS-provider access is lost. Keep the current origin and use the private administration endpoint to recover provider access or plan a new domain.')
    if config['profile'] == 'local':
        warnings.append('Local evaluation uses an owned CA and cannot establish public certificate trust or production readiness.')
    if config['tls'] == 'external':
        warnings.append('External certificates require an operator renewal job, certificate validation and proxy reload. A loaded certificate does not prove renewal.')
    if not config['smtp_ref']:
        warnings.append('SMTP is unconfigured. Confirmation and reset delivery remain unproven.')
    return {'dns_records': records, 'callback_url': f"{config['public_url']}/{runtime}/auth/v1/callback",
            'auth_external_url': f"{config['public_url']}/{runtime}/auth/v1", 'site_url': config['site_url'],
            'redirect_urls': config['redirect_urls'], 'warnings': warnings,
            'required_checks': ['public_dns_ipv4', 'public_dns_ipv6', 'inbound_http', 'trusted_https',
                                'auth_confirmation', 'auth_reset', 'oauth_callback', 'studio', 'websocket',
                                'smtp_delivery', 'management_email', 'renewal', 'reboot', 'rollback', 'neighbors'],
            'production_ready': False}


def private_read(directory, reference, leaf, limit=MAX_BYTES):
    """Walk with pinned no-follow directory descriptors, including absolute ancestors."""
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or leaf not in ('chain.pem', 'key.pem', 'mail.json'):
        refuse('private_reference')
    base = Path(directory)
    if not base.is_absolute() or '..' in base.parts:
        refuse('private_directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in base.parts[1:] + (reference,):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            refuse('private_directory')
        child = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(child)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > limit or info.st_nlink != 1:
                refuse('private_file')
            data = os.read(child, limit + 1)
            if len(data) > limit:
                refuse('private_file')
            return data
        finally:
            os.close(child)
    except OSError:
        refuse('private_reference')
    finally:
        os.close(fd)


def certificate(directory, reference, hostname, now=None):
    """Check leaf identity, validity and key pair; this is not chain trust proof."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        chain = private_read(directory, reference, 'chain.pem', 256 * 1024)
        key = private_read(directory, reference, 'key.pem', 32 * 1024)
        cert = x509.load_pem_x509_certificate(chain)
        private = serialization.load_pem_private_key(key, password=None)
        form = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if cert.public_key().public_bytes(*form) != private.public_key().public_bytes(*form):
            refuse('certificate_key_pair')
        now = now or datetime.datetime.now(datetime.timezone.utc)
        try:
            valid_before, valid_after = cert.not_valid_before_utc, cert.not_valid_after_utc
        except AttributeError:
            # Cryptography before 42 returns naive datetimes already expressed in UTC.
            valid_before = cert.not_valid_before.replace(tzinfo=datetime.timezone.utc)
            valid_after = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        if not valid_before <= now < valid_after:
            refuse('certificate_expired_or_not_yet_valid')
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        def matches(name):
            if name == hostname:
                return True
            return name.startswith('*.') and hostname.count('.') == name.count('.') and hostname.endswith(name[1:])
        if not any(matches(name.lower()) for name in names):
            refuse('certificate_hostname')
        return {'fingerprint': cert.fingerprint(__import__('cryptography.hazmat.primitives.hashes', fromlist=['SHA256']).SHA256()).hex(),
                'expires_at': valid_after.isoformat(), 'renewal_due': (valid_after - now).total_seconds() <= 30 * 86400,
                'key_pair': True, 'public_trust': False}
    except SetupRefusal:
        raise
    except Exception:
        refuse('certificate_invalid')


def dns_observation(config, observed_v4, observed_v6):
    config = validate(config)
    observed = {}
    for field, items, version in (('ipv4', observed_v4, 4), ('ipv6', observed_v6, 6)):
        if not isinstance(items, list) or len(items) > 64:
            refuse('dns_observation')
        try:
            addresses = [ipaddress.ip_address(item) for item in items]
        except ValueError:
            refuse('dns_observation')
        if any(ip.version != version for ip in addresses):
            refuse('dns_observation')
        observed[field] = sorted(set(str(ip) for ip in addresses))
    return {'ipv4_matches': set(config['ipv4']) == set(observed['ipv4']),
            'ipv6_matches': set(config['ipv6']) == set(observed['ipv6']),
            'unexpected_dns_or_proxy': any(set(observed[field]) - set(config[field]) for field in observed),
            'observed': observed, 'scope': 'supplied_observation', 'production_ready': False}


def tls_probe(hostname, address, port=443, ca_pem=None):
    """Connect to one frozen address with original hostname verification enabled."""
    if not isinstance(hostname, str) or not HOST.fullmatch(hostname) or type(port) is not int or not 1 <= port <= 65535:
        refuse('probe_target')
    try:
        ipaddress.ip_address(address)
    except ValueError:
        refuse('probe_target')
    context = verified_context(ca_pem)
    try:
        with socket.create_connection((address, port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as stream:
                cert = stream.getpeercert()
                return {'state': 'verified', 'public_trust': ca_pem is None,
                        'expires_at': cert.get('notAfter'), 'sha256': hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()}
    except ssl.SSLCertVerificationError as error:
        return {'state': 'tls_failed', 'public_trust': False, 'verify_code': error.verify_code}
    except ssl.SSLError:
        return {'state': 'tls_failed', 'public_trust': False}
    except OSError:
        return {'state': 'unreachable', 'public_trust': False}


def verified_context(ca_pem=None):
    """Verified TLS without inheriting SSLKEYLOGFILE into private key logs."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    if ca_pem is None:
        context.load_default_certs()
    else:
        context.load_verify_locations(cadata=ca_pem)
    return context


def smtp_probe(hostname, address, port=587, ca_pem=None, tls_mode=None):
    """STARTTLS or implicit TLS on 465; never authenticate, send or downgrade."""
    if not isinstance(hostname, str) or not HOST.fullmatch(hostname) or type(port) is not int or not 1 <= port <= 65535:
        refuse('probe_target')
    try:
        ipaddress.ip_address(address)
    except ValueError:
        refuse('probe_target')
    if tls_mode not in (None, 'starttls', 'implicit'):
        refuse('smtp_tls_mode')
    context = verified_context(ca_pem)
    implicit = tls_mode == 'implicit' or (tls_mode is None and port == 465)
    client = smtplib.SMTP(timeout=5, local_hostname='setup.invalid')
    client._host = hostname
    raw = None
    try:
        # SMTP.connect replaces _host with its address. Pin the socket separately
        # so both implicit TLS and STARTTLS verify the declared hostname instead.
        raw = socket.create_connection((address, port), timeout=5)
        client.sock = context.wrap_socket(raw, server_hostname=hostname) if implicit else raw
        client.file = None
        code, _ = client.getreply()
        if code != 220:
            return {'state': 'smtp_rejected', 'delivery': 'unproven'}
        client.ehlo()
        if not implicit and not client.has_extn('starttls'):
            return {'state': 'smtp_tls_required', 'delivery': 'unproven'}
        if not implicit:
            client.starttls(context=context)
            client.ehlo()
        return {'state': 'verified', 'public_trust': ca_pem is None, 'delivery': 'unproven'}
    except ssl.SSLCertVerificationError as error:
        return {'state': 'smtp_tls', 'delivery': 'unproven', 'verify_code': error.verify_code}
    except (ssl.SSLError, smtplib.SMTPNotSupportedError):
        return {'state': 'smtp_tls', 'delivery': 'unproven'}
    except smtplib.SMTPException:
        return {'state': 'smtp_rejected', 'delivery': 'unproven'}
    except OSError:
        return {'state': 'smtp_unreachable', 'delivery': 'unproven'}
    finally:
        client.close()
        if raw is not None:
            raw.close()


def render_proxy(config, console_port):
    """Caddy config without secrets, logging, DNS tokens or client forwarding trust."""
    config = validate(config)
    if type(console_port) is not int or not 1024 <= console_port <= 65535 or console_port in (2019,):
        refuse('console_port')
    if config['profile'] == 'public' and not config['provider_access']:
        refuse('dns_provider_access_lost')
    origin = urlsplit(config['public_url'])
    if (origin.port or 443) == console_port:
        refuse('console_port')
    authority = origin.netloc
    global_options = '    admin off\n    skip_install_trust\n'
    if config['profile'] == 'local':
        global_options += '    auto_https disable_redirects\n'
    if config['tls'] == 'managed':
        global_options += f"    email {config['acme_email']}\n"
    tls = '    tls internal\n' if config['tls'] == 'local' else ''
    if config['tls'] == 'external':
        tls = '    tls /run/sbarbase-tls/chain.pem /run/sbarbase-tls/key.pem\n'
    bind = '    bind ' + ' '.join(config['ipv4'] + config['ipv6']) + '\n' if config['profile'] == 'local' else ''
    return ('{\n' + global_options + '}\n' + config['public_url'] + ' {\n' + bind + tls +
            '    header {\n        X-Content-Type-Options nosniff\n        Referrer-Policy no-referrer\n    }\n' +
            f'    reverse_proxy 127.0.0.1:{console_port} {{\n' +
            f'        header_up Host 127.0.0.1:{console_port}\n        header_up X-Forwarded-Host {authority}\n' +
            '        header_up X-Forwarded-Proto https\n        header_up -Forwarded\n' +
            '    }\n}\n')


def render_compose(config, image, console_port):
    """Portable sidecar fragment; paths are operator-private fixed references."""
    validate(config)
    if not isinstance(image, str) or not re.fullmatch(r'(?:docker.io/)?library/caddy@sha256:[0-9a-f]{64}', image):
        refuse('proxy_image_digest')
    render_proxy(config, console_port)
    mounts = [{'type': 'bind', 'source': '${SBARBASE_DOMAIN_DIRECTORY}/Caddyfile', 'target': '/etc/caddy/Caddyfile',
               'read_only': True, 'bind': {'create_host_path': False}}]
    mounts.append({'type': 'bind', 'source': '${SBARBASE_DOMAIN_DIRECTORY}/data', 'target': '/data', 'bind': {'create_host_path': False}})
    if config['tls'] == 'external':
        mounts.append({'type': 'bind', 'source': '${SBARBASE_DOMAIN_DIRECTORY}/certificates/' + config['certificate_ref'],
                       'target': '/run/sbarbase-tls', 'read_only': True, 'bind': {'create_host_path': False}})
    return {'services': {'sbarbase-tls': {'image': image, 'network_mode': 'host', 'restart': 'unless-stopped',
            'read_only': True, 'tmpfs': ['/config:rw,noexec,nosuid,size=16m'], 'mem_limit': '128m', 'memswap_limit': '128m',
            'cpus': 0.25, 'pids_limit': 64, 'security_opt': ['no-new-privileges:true'], 'volumes': mounts,
            'command': ['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile']}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Domain setup preparation; no provider or service mutations')
    parser.add_argument('action', choices=['preview', 'proxy', 'compose'])
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--console-port', type=int, default=8790)
    parser.add_argument('--proxy-image', default=CADDY_IMAGE)
    args = parser.parse_args(argv)
    try:
        config = parse(sys.stdin.buffer.read(MAX_BYTES + 1))
        if args.action == 'preview':
            print(json.dumps(preview(config, args.runtime), sort_keys=True))
        elif args.action == 'proxy':
            preview(config, args.runtime)
            print(render_proxy(config, args.console_port), end='')
        else:
            preview(config, args.runtime)
            print(json.dumps(render_compose(config, args.proxy_image, args.console_port), sort_keys=True))
        return 0
    except SetupRefusal as error:
        print(json.dumps({'state': 'refused', 'field': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
