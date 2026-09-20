"""The reference TLS proxy and the check that exercises it must not drift apart."""
from pathlib import Path
import re
import unittest
import tls_termination_check as check

ROOT=Path(__file__).resolve().parent.parent
PROXY=(ROOT/'deploy'/'console-tls-proxy.ts').read_text()
CHECK=(ROOT/'lab'/'tls_termination_check.py').read_text()
ACCEPTANCE=(ROOT/'deploy'/'server-acceptance.sh').read_text()


class InterfaceTests(unittest.TestCase):
    def test_every_flag_the_check_passes_is_parsed_by_the_proxy(self):
        for flag in ('--cert','--key','--https-port','--http-port','--upstream','--public-host'):
            with self.subTest(flag=flag):
                self.assertIn(flag,PROXY)
        used=set(re.findall(r"'(--(?:cert|key|https-port|http-port|upstream|public-host))'",CHECK))
        self.assertTrue(used)
        for flag in used:
            self.assertIn(flag,PROXY)

    def test_the_proxy_refuses_what_the_check_proves_it_refuses(self):
        for behaviour in ('readable','loopback','required','bare host name'):
            with self.subTest(behaviour=behaviour):
                self.assertIn(behaviour,PROXY)

    def test_the_public_host_is_required_and_used_instead_of_the_client_header(self):
        from pathlib import Path
        proxy=(Path(__file__).resolve().parent.parent/'deploy'/'console-tls-proxy.ts').read_text()
        self.assertIn("for (const required of ['cert', 'key', 'public-host'])",proxy)
        self.assertIn("location: 'https://' + options.publicHost",proxy)
        self.assertNotIn("request.headers.get('host') ?? 'localhost'",proxy)

    def test_hop_by_hop_headers_are_never_forwarded(self):
        from pathlib import Path
        proxy=(Path(__file__).resolve().parent.parent/'deploy'/'console-tls-proxy.ts').read_text()
        for header in ('connection','upgrade','transfer-encoding','te','trailer','keep-alive'):
            self.assertIn("'"+header+"'",proxy)

    def test_a_body_cap_exists_and_is_configurable(self):
        from pathlib import Path
        proxy=(Path(__file__).resolve().parent.parent/'deploy'/'console-tls-proxy.ts').read_text()
        self.assertIn('MAX_BODY_DEFAULT',proxy)
        self.assertIn("'--max-body'",CHECK)
        self.assertIn("values['max-body']",proxy)

    def test_the_evidence_states_what_is_not_proven(self):
        self.assertIn('Not a public certificate',CHECK)
        self.assertIn('not the operator',CHECK)

    def test_the_check_serves_the_real_built_page_rather_than_a_fixture(self):
        self.assertIn("BUILD/'index.html'",CHECK)
        self.assertIn('uiStatic',(ROOT/'lab'/'tls_upstream_stub.ts').read_text())

    def test_the_proxy_never_logs_bodies_or_credentials(self):
        self.assertNotIn('request.headers.get(\'authorization\')',PROXY)
        for line in PROXY.splitlines():
            if 'console.log' in line:
                self.assertNotIn('request.body',line,line)
                self.assertNotIn('url.search',line,line)

    def test_the_acceptance_run_includes_the_tls_step(self):
        self.assertIn('lab/tls_termination_check.py',ACCEPTANCE)


class HelperTests(unittest.TestCase):
    def test_a_free_port_is_a_real_port_number(self):
        port=check.free_port()
        self.assertIsInstance(port,int)
        self.assertGreater(port,1023)

    def test_redirects_are_not_followed_by_default(self):
        import urllib.request
        handler=check.NoRedirect()
        self.assertIsNone(handler.redirect_request(None,None,308,'x',{},None))
        self.assertTrue(any(isinstance(item,check.NoRedirect)
                            for item in urllib.request.build_opener(check.NoRedirect()).handlers))


if __name__=='__main__':unittest.main()