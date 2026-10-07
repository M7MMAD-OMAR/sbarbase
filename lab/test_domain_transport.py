"""Owned source fixtures. Local TLS success is never public trust or delivery."""
import copy
import datetime
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import domain_transport as domain


def configuration():
    return {'version': 1, 'profile': 'public', 'public_url': 'https://api.example.org',
            'ipv4': ['8.8.8.8'], 'ipv6': [], 'provider_access': True, 'tls': 'managed',
            'acme_email': 'operator@example.org', 'certificate_ref': None,
            'site_url': 'https://app.example.org', 'redirect_urls': ['https://app.example.org/return'], 'smtp_ref': 'environment-mail'}


class ConfigurationTests(unittest.TestCase):
    def test_tls_context_verifies_without_inheriting_key_log_file(self):
        with tempfile.TemporaryDirectory(prefix='sb08-no-keylog-') as temporary:
            target = Path(temporary) / 'keylog'
            with patch.dict(os.environ, {'SSLKEYLOGFILE': str(target)}):
                context = domain.verified_context()
            self.assertIsNone(context.keylog_filename)
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.verify_flags & ssl.VERIFY_X509_STRICT)
            self.assertFalse(target.exists())
    def test_callback_and_exact_records(self):
        view = domain.preview(configuration(), 'e_' + 'a' * 24)
        self.assertEqual(view['callback_url'], 'https://api.example.org/e_' + 'a' * 24 + '/auth/v1/callback')
        self.assertEqual(view['dns_records'], [{'name': 'api.example.org', 'type': 'A', 'value': '8.8.8.8'}])
        self.assertFalse(view['production_ready'])

    def test_no_unknown_secrets_or_fields(self):
        for key in ['smtp_password', 'tls_key', 'provider_token', 'accepted', 'sha256:\nsecret']:
            with self.subTest(key=key):
                value = configuration(); value[key] = 'SYNTHETIC_SECRET'
                with self.assertRaises(domain.SetupRefusal) as error:
                    domain.validate(value)
                self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
                self.assertNotIn(key, str(error.exception))

    def test_missing_fields(self):
        for key in domain.FIELDS:
            value = configuration(); del value[key]
            with self.subTest(key=key), self.assertRaises(domain.SetupRefusal):
                domain.validate(value)

    def test_duplicate_json_rejected(self):
        raw = json.dumps(configuration()).replace('"version": 1', '"version": 1, "version": 1')
        with self.assertRaisesRegex(domain.SetupRefusal, 'duplicate_field'):
            domain.parse(raw.encode())

    def test_invalid_encoding_and_bounded_json(self):
        for raw in [b'\xff', b'[' * 4000, b' ' * (domain.MAX_BYTES + 1), b'{']:
            with self.subTest(size=len(raw)), self.assertRaises(domain.SetupRefusal):
                domain.parse(raw)

    def test_public_origin_rejects_hostile_addresses(self):
        for origin in ['http://api.example.org', 'https://u:password@api.example.org', 'https://api.example.org/path',
                       'https://api.example.org/?token=secret', 'https://api.example.org/#secret', 'https://localhost',
                       'https://127.0.0.1', 'https://api.example.org:8443', 'https://a.local', 'https://a.ts.net',
                       'https://api.example.org\nheader', 'https://api.example.org\\secret']:
            value = configuration(); value['public_url'] = origin
            with self.subTest(origin=origin), self.assertRaises(domain.SetupRefusal):
                domain.validate(value)

    def test_empty_url_components_refused_in_every_address_field(self):
        for address in ['https://@api.example.org', 'https://:@api.example.org',
                        'https://api.example.org?', 'https://api.example.org#']:
            for field in ['public_url', 'site_url', 'redirect_urls']:
                value = configuration(); value[field] = [address] if field == 'redirect_urls' else address
                with self.subTest(address=address, field=field), self.assertRaises(domain.SetupRefusal):
                    domain.validate(value)
                    domain.preview(value, 'e_' + 'a' * 24)
                    domain.render_proxy(value, 8790)

    def test_local_listener_cannot_use_console_upstream_port(self):
        for host, ipv4, ipv6 in [('localhost', ['127.0.0.1'], []), ('127.0.0.1', ['127.0.0.1'], []),
                                ('[::1]', [], ['::1'])]:
            value = configuration(); value.update(profile='local', public_url='https://' + host + ':8790',
                site_url='http://localhost:3000', redirect_urls=[], ipv4=ipv4, ipv6=ipv6, tls='local', acme_email='')
            with self.subTest(host=host), self.assertRaisesRegex(domain.SetupRefusal, 'console_port'):
                domain.render_proxy(value, 8790)
            with self.subTest(compose=host), self.assertRaisesRegex(domain.SetupRefusal, 'console_port'):
                domain.render_compose(value, domain.CADDY_IMAGE, 8790)

    def test_redirects_no_wildcards_or_secrets(self):
        for redirect in ['https://app.example.org/**', 'https://app.example.org/?token=secret',
                         'https://user:pass@app.example.org', 'javascript://alert', 'https://app.example.org/#token']:
            value = configuration(); value['redirect_urls'] = [redirect]
            with self.subTest(redirect=redirect), self.assertRaises(domain.SetupRefusal):
                domain.validate(value)

    def test_ip_families_duplicates_and_private_addresses(self):
        for field, ips in [('ipv4', ['::1']), ('ipv6', ['8.8.8.8']), ('ipv4', ['8.8.8.8'] * 2),
                           ('ipv4', ['127.0.0.1']), ('ipv4', ['169.254.169.254']), ('ipv6', ['fc00::1']), ('ipv4', [True])]:
            value = configuration(); value[field] = ips
            with self.subTest(field=field, ips=ips), self.assertRaises(domain.SetupRefusal):
                domain.validate(value)

    def test_no_address_is_refused(self):
        value = configuration(); value['ipv4'] = []
        with self.assertRaisesRegex(domain.SetupRefusal, 'dns_addresses'):
            domain.validate(value)

    def test_reference_path_traversal_and_key_injection(self):
        for reference in ['../other', '/private/key', 'key.pem', 'a\nsecret', 'a' * 65, {'pass': 'secret'}]:
            value = configuration(); value['smtp_ref'] = reference
            with self.subTest(reference=reference), self.assertRaises(domain.SetupRefusal):
                domain.validate(value)

    def test_local_profile_explicit_loopback(self):
        value = configuration(); value.update(profile='local', public_url='https://localhost:8443', site_url='http://localhost:3000',
                                             redirect_urls=[], ipv4=['127.0.0.1'], tls='local', acme_email='')
        view = domain.preview(value, 'e_' + 'a' * 24)
        self.assertIn('owned CA', ' '.join(view['warnings']))
        self.assertFalse(view['production_ready'])
        self.assertIn('bind 127.0.0.1', domain.render_proxy(value, 8790))
        self.assertIn('auto_https disable_redirects', domain.render_proxy(value, 8790))
        value['public_url'] = 'https://api.example.org'
        with self.assertRaisesRegex(domain.SetupRefusal, 'local_loopback'):
            domain.validate(value)

    def test_local_origin_requires_a_matching_listener_address(self):
        for origin, ipv4, ipv6 in [('https://127.0.0.1:8443', [], ['::1']),
                                 ('https://[::1]:8443', ['127.0.0.1'], []),
                                 ('https://127.0.0.1:8443', ['127.0.0.2'], []),
                                 ('https://localhost:8443', ['127.0.0.2'], [])]:
            value = configuration(); value.update(profile='local', public_url=origin, ipv4=ipv4, ipv6=ipv6,
                site_url='http://localhost:3000', redirect_urls=[], tls='local', acme_email='')
            with self.subTest(origin=origin), self.assertRaisesRegex(domain.SetupRefusal, 'local_listener'):
                domain.preview(value, 'e_' + 'a' * 24)
        for origin, ipv4, ipv6 in [('https://127.0.0.1:8443', ['127.0.0.1'], []),
                                 ('https://[::1]:8443', [], ['::1']),
                                 ('https://localhost:8443', ['127.0.0.1'], []),
                                 ('https://localhost:8443', [], ['::1']),
                                 ('https://localhost:8443', ['127.0.0.1'], ['::1'])]:
            value = configuration(); value.update(profile='local', public_url=origin, ipv4=ipv4, ipv6=ipv6,
                site_url='http://localhost:3000', redirect_urls=[], tls='local', acme_email='')
            with self.subTest(aligned=origin, ipv4=ipv4, ipv6=ipv6):
                self.assertEqual(domain.preview(value, 'e_' + 'a' * 24)['callback_url'], origin + '/e_' + 'a' * 24 + '/auth/v1/callback')
                self.assertIn('bind ' + ' '.join(ipv4 + ipv6), domain.render_proxy(value, 8790))
                self.assertIn('sbarbase-tls', domain.render_compose(value, domain.CADDY_IMAGE, 8790)['services'])

    def test_provider_loss_refuses_render_and_offers_private_recovery(self):
        value = configuration(); value['provider_access'] = False
        self.assertIn('private administration', ' '.join(domain.preview(value, 'e_' + 'a' * 24)['warnings']))
        with self.assertRaisesRegex(domain.SetupRefusal, 'dns_provider_access_lost'):
            domain.render_proxy(value, 8790)

    def test_dual_stack_stale_aaaa_detected(self):
        observation = domain.dns_observation(configuration(), ['8.8.8.8'], ['2001:4860:4860::8888'])
        self.assertTrue(observation['ipv4_matches']); self.assertFalse(observation['ipv6_matches'])
        self.assertTrue(observation['unexpected_dns_or_proxy']); self.assertFalse(observation['production_ready'])

    def test_exact_aaaa_not_optional(self):
        value = configuration(); value['ipv6'] = ['2001:4860:4860::8888']
        observation = domain.dns_observation(value, ['8.8.8.8'], [])
        self.assertFalse(observation['ipv6_matches'])

    def test_proxy_origin_and_no_client_forwarding_trust(self):
        rendered = domain.render_proxy(configuration(), 8790)
        for expected in ['admin off', '127.0.0.1:8790', 'header_up Host 127.0.0.1:8790', 'header_up X-Forwarded-Host api.example.org',
                         'header_up X-Forwarded-Proto https', 'header_up -Forwarded']:
            self.assertIn(expected, rendered)
        self.assertNotIn('trusted_proxies', rendered); self.assertNotIn('log ', rendered)

    def test_proxy_loopback_host_preserves_public_authority_for_each_profile(self):
        public = configuration(); public['public_url'] = 'https://api.example.org:443'
        local = configuration(); local.update(profile='local', public_url='https://localhost:8443',
            site_url='http://localhost:3000', redirect_urls=[], ipv4=['127.0.0.1'], tls='local', acme_email='')
        ipv6 = dict(local, public_url='https://[::1]:8443', ipv4=[], ipv6=['::1'])
        for value, authority in [(public, 'api.example.org:443'), (local, 'localhost:8443'), (ipv6, '[::1]:8443')]:
            with self.subTest(authority=authority):
                rendered = domain.render_proxy(value, 8790)
                self.assertIn('header_up Host 127.0.0.1:8790\n', rendered)
                self.assertIn('header_up X-Forwarded-Host ' + authority + '\n', rendered)
                self.assertNotIn('header_up Host ' + authority + '\n', rendered)

    def test_digest_only_compose_persistent_data(self):
        rendered = domain.render_compose(configuration(), 'library/caddy@sha256:' + 'a' * 64, 8790)
        service = rendered['services']['sbarbase-tls']
        self.assertEqual(service['restart'], 'unless-stopped')
        self.assertTrue(any(mount['target'] == '/data' for mount in service['volumes']))
        self.assertTrue(all(not mount['bind']['create_host_path'] for mount in service['volumes']))
        for image in ['caddy:latest', 'caddy:2', 'private/caddy@sha256:' + 'a' * 64]:
            with self.assertRaisesRegex(domain.SetupRefusal, 'proxy_image_digest'):
                domain.render_compose(configuration(), image, 8790)

    def test_external_certificate_needs_private_reference(self):
        value = configuration(); value['tls'] = 'external'
        with self.assertRaisesRegex(domain.SetupRefusal, 'certificate_ref'):
            domain.validate(value)
        value['certificate_ref'] = 'bundle-a'
        self.assertIn('/run/sbarbase-tls/key.pem', domain.render_proxy(value, 8790))
        self.assertIn('separate', ' '.join(domain.preview(value, 'e_' + 'a' * 24)['warnings']).replace('operator renewal job', 'separate'))


class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.now = datetime.datetime.now(datetime.timezone.utc)
        cls.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Owned SB08 fixture CA')])
        cls.ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(cls.ca_key.public_key())
                  .serial_number(1).not_valid_before(cls.now - datetime.timedelta(days=1)).not_valid_after(cls.now + datetime.timedelta(days=90))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(cls.ca_key.public_key()), critical=False)
                  .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                              data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                              encipher_only=False, decipher_only=False), critical=True)
                  .sign(cls.ca_key, hashes.SHA256()))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sb08-cert-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.bundle = self.root / 'bundle-a'; self.bundle.mkdir(mode=0o700)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.write_certificate('localhost', self.now + datetime.timedelta(days=60))

    def write_certificate(self, hostname, expires):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(self.ca.subject).public_key(self.key.public_key())
                .serial_number(2).not_valid_before(self.now - datetime.timedelta(days=2)).not_valid_after(expires)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()), critical=False)
                .sign(self.ca_key, hashes.SHA256()))
        for leaf, data in [('chain.pem', cert.public_bytes(serialization.Encoding.PEM) + self.ca.public_bytes(serialization.Encoding.PEM)),
                           ('key.pem', self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))]:
            target = self.bundle / leaf; target.write_bytes(data); target.chmod(0o600)

    def test_valid_pair_never_implies_public_trust(self):
        result = domain.certificate(self.root, 'bundle-a', 'localhost', self.now)
        self.assertTrue(result['key_pair']); self.assertFalse(result['public_trust']); self.assertFalse(result['renewal_due'])

    def check_certificate_datetime_api(self, modern):
        native = x509.load_pem_x509_certificate((self.bundle / 'chain.pem').read_bytes())
        before = (self.now - datetime.timedelta(days=2)).replace(microsecond=0)
        after = (self.now + datetime.timedelta(days=60)).replace(microsecond=0)
        class CertificateApi:
            def __getattr__(self, name):
                if name in ('not_valid_before_utc', 'not_valid_after_utc'):
                    if not modern:
                        raise AttributeError(name)
                    return before if name == 'not_valid_before_utc' else after
                if name in ('not_valid_before', 'not_valid_after'):
                    if modern:
                        raise AssertionError('Modern certificate must not use the legacy datetime API')
                    return (before if name == 'not_valid_before' else after).replace(tzinfo=None)
                return getattr(native, name)
        with patch('cryptography.x509.load_pem_x509_certificate', return_value=CertificateApi()):
            result = domain.certificate(self.root, 'bundle-a', 'localhost', self.now)
            self.assertEqual(result['expires_at'], after.isoformat())
            self.assertTrue(result['key_pair']); self.assertFalse(result['public_trust']); self.assertFalse(result['renewal_due'])
            self.assertTrue(domain.certificate(self.root, 'bundle-a', 'localhost', before)['key_pair'])
            for instant in (before - datetime.timedelta(seconds=1), after, after + datetime.timedelta(seconds=1)):
                with self.subTest(modern=modern, instant=instant), self.assertRaisesRegex(domain.SetupRefusal, 'expired_or_not_yet_valid'):
                    domain.certificate(self.root, 'bundle-a', 'localhost', instant)
            self.assertTrue(domain.certificate(self.root, 'bundle-a', 'localhost', after - datetime.timedelta(days=30))['renewal_due'])
            with self.assertRaisesRegex(domain.SetupRefusal, 'certificate_hostname'):
                domain.certificate(self.root, 'bundle-a', 'wrong.example.org', self.now)

    def test_legacy_certificate_datetime_api_preserves_utc_validation(self):
        self.check_certificate_datetime_api(modern=False)

    def test_modern_certificate_datetime_api_avoids_legacy_properties(self):
        self.check_certificate_datetime_api(modern=True)

    def test_wrong_hostname_refused(self):
        with self.assertRaisesRegex(domain.SetupRefusal, 'certificate_hostname'):
            domain.certificate(self.root, 'bundle-a', 'other.example.org', self.now)

    def test_expired_and_future_refused(self):
        self.write_certificate('localhost', self.now - datetime.timedelta(hours=1))
        with self.assertRaisesRegex(domain.SetupRefusal, 'expired'):
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now)
        with self.assertRaisesRegex(domain.SetupRefusal, 'not_yet_valid'):
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now - datetime.timedelta(days=3))

    def test_renewal_due(self):
        self.write_certificate('localhost', self.now + datetime.timedelta(days=7))
        self.assertTrue(domain.certificate(self.root, 'bundle-a', 'localhost', self.now)['renewal_due'])

    def test_mismatched_key(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (self.bundle / 'key.pem').write_bytes(other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        with self.assertRaisesRegex(domain.SetupRefusal, 'certificate_key_pair'):
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now)

    def test_insecure_key_and_directory(self):
        (self.bundle / 'key.pem').chmod(0o644)
        with self.assertRaisesRegex(domain.SetupRefusal, 'private_file'):
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now)
        (self.bundle / 'key.pem').chmod(0o600); self.bundle.chmod(0o755)
        with self.assertRaisesRegex(domain.SetupRefusal, 'private_directory'):
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now)

    def test_symlink_ancestor_and_leaf_refused(self):
        link = self.root / 'bundle-link'; link.symlink_to(self.bundle)
        with self.assertRaises(domain.SetupRefusal):
            domain.private_read(self.root, 'bundle-link', 'key.pem')
        (self.bundle / 'chain.pem').unlink(); (self.bundle / 'chain.pem').symlink_to(self.bundle / 'key.pem')
        with self.assertRaises(domain.SetupRefusal):
            domain.private_read(self.root, 'bundle-a', 'chain.pem')

    def test_fifo_and_hardlink_refused(self):
        target = self.bundle / 'chain.pem'; target.unlink(); os.mkfifo(target, mode=0o600)
        with self.assertRaises(domain.SetupRefusal):
            domain.private_read(self.root, 'bundle-a', 'chain.pem')
        target.unlink(); os.link(self.bundle / 'key.pem', target)
        with self.assertRaises(domain.SetupRefusal):
            domain.private_read(self.root, 'bundle-a', 'chain.pem')

    def test_corrupt_and_oversized_certificate_secret_is_redacted(self):
        target = self.bundle / 'chain.pem'; target.write_bytes(b'SYNTHETIC_PRIVATE_SECRET')
        with self.assertRaises(domain.SetupRefusal) as error:
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now)
        self.assertNotIn('SYNTHETIC', str(error.exception))
        target.write_bytes(b'a' * (256 * 1024 + 1))
        with self.assertRaises(domain.SetupRefusal):
            domain.certificate(self.root, 'bundle-a', 'localhost', self.now)

    def listener(self, smtp=False, offer_tls=True, implicit=False):
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(5)
        port = listener.getsockname()[1]; commands = []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(self.bundle / 'chain.pem', self.bundle / 'key.pem')
        def serve():
            try:
                stream, _ = listener.accept(); stream.settimeout(5)
                with stream:
                    if not smtp:
                        with context.wrap_socket(stream, server_side=True):
                            pass
                    else:
                        if implicit:
                            stream = context.wrap_socket(stream, server_side=True)
                        stream.sendall(b'220 owned fixture\r\n'); reader = stream.makefile('rb')
                        try:
                            while True:
                                line = reader.readline(4096)
                                if not line: break
                                commands.append(line.split()[0].decode())
                                if line.upper().startswith(b'EHLO'):
                                    stream.sendall(b'250-owned fixture\r\n250 STARTTLS\r\n' if offer_tls else b'250 owned fixture\r\n')
                                elif line.upper().startswith(b'STARTTLS'):
                                    stream.sendall(b'220 ready\r\n'); reader.close(); stream = context.wrap_socket(stream, server_side=True)
                                    reader = stream.makefile('rb')
                                else:
                                    stream.sendall(b'500 refused\r\n')
                        finally:
                            reader.close(); stream.close()
            except (ssl.SSLError, OSError):
                pass
            finally:
                listener.close()
        thread = threading.Thread(target=serve); thread.start()
        def finish():
            thread.join(6); listener.close(); self.assertFalse(thread.is_alive(), 'owned listener leaked')
        self.addCleanup(finish)
        return port, commands

    def test_owned_tls_chain_success_is_local(self):
        port, _ = self.listener()
        result = domain.tls_probe('localhost', '127.0.0.1', port, self.ca.public_bytes(serialization.Encoding.PEM).decode())
        self.assertEqual(result['state'], 'verified', result); self.assertFalse(result['public_trust'])

    def test_public_store_rejects_owned_ca(self):
        port, _ = self.listener()
        self.assertEqual(domain.tls_probe('localhost', '127.0.0.1', port)['state'], 'tls_failed')

    def test_owned_tls_wrong_host(self):
        port, _ = self.listener()
        result = domain.tls_probe('wrong.example.org', '127.0.0.1', port, self.ca.public_bytes(serialization.Encoding.PEM).decode())
        self.assertEqual(result['state'], 'tls_failed')

    def test_smtp_starttls_without_auth_or_delivery(self):
        port, commands = self.listener(smtp=True)
        result = domain.smtp_probe('localhost', '127.0.0.1', port, self.ca.public_bytes(serialization.Encoding.PEM).decode())
        self.assertEqual(result['state'], 'verified', result); self.assertEqual(result['delivery'], 'unproven')
        self.assertFalse(result['public_trust']); self.assertIn('STARTTLS', commands)
        self.assertFalse(set(commands) & {'AUTH', 'MAIL', 'RCPT', 'DATA'})

    def test_smtp_without_starttls_refuses_downgrade(self):
        port, commands = self.listener(smtp=True, offer_tls=False)
        self.assertEqual(domain.smtp_probe('localhost', '127.0.0.1', port)['state'], 'smtp_tls_required')
        self.assertFalse(set(commands) & {'AUTH', 'MAIL', 'RCPT', 'DATA'})

    def test_smtp_wrong_host_chain_refused(self):
        port, _ = self.listener(smtp=True)
        result = domain.smtp_probe('wrong.example.org', '127.0.0.1', port, self.ca.public_bytes(serialization.Encoding.PEM).decode())
        self.assertEqual(result['state'], 'smtp_tls')

    def test_smtp_implicit_tls_uses_hostname_verification(self):
        port, commands = self.listener(smtp=True, implicit=True)
        result = domain.smtp_probe('localhost', '127.0.0.1', port, self.ca.public_bytes(serialization.Encoding.PEM).decode(), tls_mode='implicit')
        self.assertEqual(result['state'], 'verified', result)
        self.assertEqual(result['delivery'], 'unproven')
        self.assertFalse(set(commands) & {'STARTTLS', 'AUTH', 'MAIL', 'RCPT', 'DATA'})


if __name__ == '__main__':
    unittest.main()
