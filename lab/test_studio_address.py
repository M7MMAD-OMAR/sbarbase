import os
import ssl
import tempfile
import unittest
from types import SimpleNamespace

import studio_address as address


def done(code=0, out='', err=''):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class DomainTests(unittest.TestCase):
    def test_a_bare_domain_gives_the_pattern_and_the_three_things_to_provide(self):
        value = address.plan('Example.COM.')
        self.assertEqual(value['pattern'], 'studio-{id}.example.com')
        self.assertEqual(value['certificate'], ['*.example.com', 'example.com'])
        self.assertTrue(value['dns'].startswith('*.example.com'))
        self.assertEqual(value['setting'], 'SBARBASE_STUDIO_HOST=studio-{id}.example.com')

    def test_things_that_are_not_a_bare_domain_are_refused_with_a_reason(self):
        for bad in ('', 'https://example.com', 'example.com/x', '*.example.com', 'studio-{id}.example.com', 'localhost',
                    'a.localhost', 'single', 'bad_name.com', '-a.com', 'a' * 64 + '.com', 'host:8443'):
            with self.assertRaises(address.AddressError, msg=bad):
                address.plan(bad)

    def test_the_description_names_the_wildcard_and_warns_about_other_sites(self):
        text = address.describe('example.com')
        self.assertIn('*.example.com', text)
        self.assertIn('no other site', text)


class ApplyTests(unittest.TestCase):
    def apply(self, root, unit_text='', fail=None):
        calls = []

        def run(arguments):
            calls.append(arguments)
            if arguments[:2] == ['systemctl', 'cat']:
                return done(out=unit_text)
            if fail and arguments == fail:
                return done(1, err='boom')
            return done()
        written = address.apply('example.com', root, run=run, geteuid=lambda: 0)
        return written, calls

    def test_one_setting_is_written_for_the_console_and_the_proxy_and_both_restart_proxy_first(self):
        with tempfile.TemporaryDirectory() as root:
            written, calls = self.apply(root)
            self.assertEqual(sorted(os.path.basename(os.path.dirname(path)) for path in written),
                             ['sbarbase-proxy.service.d', 'sbarbase.service.d'])
            for path in written:
                self.assertEqual(open(path).read(), '[Service]\nEnvironment=SBARBASE_STUDIO_HOST=studio-{id}.example.com\n')
            self.assertEqual(calls[1:], [['systemctl', 'daemon-reload'], ['systemctl', 'restart', 'sbarbase-proxy.service'],
                                         ['systemctl', 'restart', 'sbarbase.service']])

    def test_a_different_flag_on_the_proxy_is_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(address.AddressError) as raised:
                self.apply(root, unit_text='ExecStart=bun x --studio-host studio-{id}.other.com')
            self.assertIn('other.com', str(raised.exception))
            self.assertEqual(os.listdir(root), [])

    def test_the_same_flag_is_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            self.apply(root, unit_text='ExecStart=bun x --studio-host studio-{id}.example.com')

    def test_apply_needs_root_and_reports_a_failed_restart(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(address.AddressError):
                address.apply('example.com', root, run=lambda a: done(), geteuid=lambda: 1000)
            with self.assertRaises(address.AddressError) as raised:
                self.apply(root, fail=['systemctl', 'restart', 'sbarbase.service'])
            self.assertIn('sbarbase failed', str(raised.exception))


class VerifyTests(unittest.TestCase):
    def test_each_failure_names_the_step_to_fix(self):
        def missing(host, port):
            raise OSError('no such name')
        ok, lines = address.verify('example.com', resolve=missing)
        self.assertFalse(ok)
        self.assertIn('wildcard record *.example.com', lines[0])

        def bad_certificate(host):
            raise ssl.SSLCertVerificationError(1, 'hostname mismatch')
        ok, lines = address.verify('example.com', resolve=lambda h, p: [], fetch=bad_certificate)
        self.assertFalse(ok)
        self.assertIn('must cover *.example.com', lines[-1])

        ok, lines = address.verify('example.com', resolve=lambda h, p: [], fetch=lambda h: 421)
        self.assertFalse(ok)
        self.assertIn('SBARBASE_STUDIO_HOST', lines[-1])

    def test_a_sign_in_check_answer_means_the_address_reaches_sbarbase(self):
        for status in (401, 404):
            ok, lines = address.verify('example.com', resolve=lambda h, p: [], fetch=lambda h, s=status: s)
            self.assertTrue(ok)


if __name__ == '__main__':
    unittest.main()
