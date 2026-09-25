"""SBARBASE_CONSOLE_PORT must reach the console listener under the supervisor.

The unit's drop-in sets it for lab/dev.py, which starts bun lab/upstream-server.ts
through lab/parent_bound.py. If either hop built a fresh environment, the console
would fall back to an ephemeral port and a TLS proxy would break on the next reboot.
"""
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parent.parent


class ConsolePortEnvironmentTests(unittest.TestCase):
    def test_parent_bound_hands_the_variable_to_the_command_it_runs(self):
        environment=dict(os.environ,SBARBASE_CONSOLE_PORT='8787')
        result=subprocess.run(['/usr/bin/python3','lab/parent_bound.py',str(os.getpid()),'/usr/bin/env'],
                              cwd=ROOT,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('SBARBASE_CONSOLE_PORT=8787',result.stdout.splitlines())

    def test_the_supervisor_spawns_the_console_with_its_own_environment(self):
        source=(ROOT/'lab'/'dev.py').read_text()
        spawn=source[source.index('    def spawn(self, command, **options):'):]
        spawn=spawn[:spawn.index('\n\n')]
        self.assertNotIn('env=',spawn)
        # The console is spawned without options of its own, so it keeps this process's environment.
        self.assertIn("self.server = self.spawn(['bun', 'lab/upstream-server.ts'])",source)

    def test_the_console_reads_the_variable(self):
        self.assertIn('consolePort(process.env.SBARBASE_CONSOLE_PORT)',(ROOT/'lab'/'upstream-server.ts').read_text())
