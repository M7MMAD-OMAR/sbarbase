"""Execute startup ordering in an owned checkout with inert command doubles."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ContainerStartTests(unittest.TestCase):
    def run_start(self, reject_at=0, copied_guard=False):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder)
            (checkout / 'lab').mkdir()
            # Historical checkouts need not carry the new validator.
            (checkout / 'lab/upgrade_guard.py').write_text('historical guard')
            if copied_guard:
                guard = checkout / '.lab/upgrades/guard.py'
                guard.parent.mkdir(parents=True)
                guard.write_text('saved guard')
            commands = checkout / 'commands'
            commands.mkdir()
            log = checkout / 'calls'
            interpreter = commands / 'python'
            interpreter.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
  */docker_profile.py)
    if [ "$2" = runtime ]; then
      n=0
      if [ -f "$COUNT" ]; then n=$(cat "$COUNT"); fi
      n=$((n + 1))
      printf '%s' "$n" > "$COUNT"
      if [ "$n" = "$REJECT_AT" ]; then exit 42; fi
    fi
    ;;
esac
''')
            interpreter.chmod(0o755)
            bun = commands / 'bun'
            bun.write_text('#!/bin/sh\nprintf "bun %s\\n" "$*" >> "$CALLS"\n')
            bun.chmod(0o755)
            script = checkout / 'start.sh'
            script.write_text((ROOT / 'deploy/container/start.sh').read_text().replace('/usr/bin/python3', str(interpreter)))
            env = dict(os.environ, SBARBASE_ROOT=str(checkout), CALLS=str(log), COUNT=str(checkout / 'count'),
                       REJECT_AT=str(reject_at), PATH=str(commands) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True, timeout=10)
            return result.returncode, log.read_text().splitlines()

    def test_baked_validator_allows_guard_in_historical_checkout(self):
        status, calls = self.run_start(copied_guard=True)
        self.assertEqual(status, 0)
        self.assertEqual(calls[0], '/usr/local/lib/sbarbase/docker_profile.py check')
        self.assertTrue(calls[1].startswith('/usr/local/lib/sbarbase/docker_profile.py runtime '))
        self.assertEqual(calls[2], '.lab/upgrades/guard.py')
        self.assertEqual(calls[3], '/usr/local/lib/sbarbase/docker_profile.py check')
        self.assertTrue(calls[4].startswith('/usr/local/lib/sbarbase/docker_profile.py runtime '))
        self.assertEqual(calls[5:], ['bun install --frozen-lockfile', 'lab/install_server.py images', 'lab/dev.py'])
        self.assertIn('COPY lab/docker_profile.py /usr/local/lib/sbarbase/docker_profile.py', (ROOT / 'Dockerfile').read_text())
        self.assertIn('!lab/docker_profile.py', (ROOT / '.dockerignore').read_text())

    def test_unsupported_initial_runtime_refuses_before_guard(self):
        status, calls = self.run_start(reject_at=1)
        self.assertEqual(status, 42)
        self.assertEqual(len(calls), 2)

    def test_guard_selected_unsupported_runtime_refuses_before_dependencies(self):
        status, calls = self.run_start(reject_at=2)
        self.assertEqual(status, 42)
        self.assertEqual(calls[2], 'lab/upgrade_guard.py')
        self.assertEqual(len(calls), 5)


if __name__ == '__main__':
    unittest.main()
