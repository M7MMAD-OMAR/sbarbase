"""deploy/hyperdrive with ssh and wrangler replaced by stubs: the order of steps, what reaches Cloudflare, and that the password never prints."""
import os, stat, subprocess, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'deploy' / 'hyperdrive'
URL = 'postgresql://e_aaaaaaaaaaaaaaaaaaaaaaaa_developer:S3cretPw_value@127.0.0.1:6543/e_aaaaaaaaaaaaaaaaaaaaaaaa'


class HyperdriveScript(unittest.TestCase):
    def run_script(self, *args):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            log = home / 'calls.log'
            ssh = home / 'ssh'
            ssh.write_text('#!/bin/sh\necho "ssh $*" >> "%s"\ncase "$*" in *"cat "*) echo "%s";; esac\n' % (log, URL))
            wrangler = home / 'wrangler'
            wrangler.write_text('#!/bin/sh\necho "wrangler $*" >> "%s"\ncase "$*" in\n'
                                '*"vpc service create"*) echo "Created VPC service: svc-123";;\n'
                                '*"hyperdrive create"*) echo "Created new Hyperdrive PostgreSQL config: hd-456";;\nesac\n' % log)
            for tool in (ssh, wrangler):
                tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            env = dict(os.environ, PATH=f'{home}:{os.environ["PATH"]}', WRANGLER=str(wrangler), HOME=str(home))
            result = subprocess.run(['/bin/sh', str(SCRIPT), *args], env=env, capture_output=True, text=True, cwd=home)
            saved = home / 'out.env'
            self.saved = (stat.S_IMODE(saved.stat().st_mode), saved.read_text()) if saved.exists() else None
            return result, (log.read_text() if log.exists() else ''), home

    def test_full_run_creates_service_and_hyperdrive_and_hides_the_password(self):
        result, calls, home = self.run_script('you@srv', 'shop/production', 'my-db', '--tunnel-id', 'tun-1', '--save-url', 'out.env')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Hyperdrive id: hd-456', result.stdout)
        self.assertNotIn('S3cretPw_value', result.stdout + result.stderr)
        self.assertLess(calls.index('database-external shop/production on'), calls.index('database-password'))
        self.assertIn('--tunnel-id tun-1', calls)
        self.assertIn('--service-id svc-123', calls)
        self.assertIn('--caching-disabled', calls)
        self.assertEqual(self.saved, (0o600, f'DATABASE_URL={URL}\n'))

    def test_existing_service_is_reused(self):
        result, calls, _ = self.run_script('you@srv', 'shop/production', 'my-db', '--service-id', 'svc-9')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('vpc service create', calls)
        self.assertIn('--service-id svc-9', calls)

    def test_refusals_change_nothing(self):
        result, calls, _ = self.run_script('you@srv', 'shop/production', 'my-db')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, '')
        result, calls, home = self.run_script('you@srv', 'shop/production', 'my-db', '--tunnel-id', 't', '--save-url', 'x.env', '--remote-cmd')
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
