"""Server acceptance entry point: prerequisites, refusals, and no secret leakage.

These run the shipped script for real against the local host; the only checks
taken from source are the ones that would need a server to observe.
"""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/'deploy'/'server-acceptance.sh'


def run(*args,env=None):
    environment=dict(os.environ)
    if env:environment.update(env)
    return subprocess.run([str(SCRIPT),*args],capture_output=True,text=True,timeout=600,cwd=ROOT,env=environment)


class ServerAcceptanceTests(unittest.TestCase):
    def test_the_script_is_executable_and_strict(self):
        self.assertTrue(SCRIPT.exists())
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        source=SCRIPT.read_text()
        self.assertIn('set -euo pipefail',source)

    def test_help_lists_usage_without_touching_the_host(self):
        result=run('--help')
        self.assertEqual(result.returncode,0)
        self.assertIn('--rehearse',result.stdout)

    def test_an_unknown_argument_is_refused(self):
        result=run('--nonsense')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unknown argument',result.stderr)

    def test_a_missing_prerequisite_names_the_tool(self):
        result=run(env={'PATH':'/usr/bin:/bin'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('is not on PATH',result.stderr)

    def test_a_world_readable_bootstrap_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'operator.json'
            path.write_text('{"note":"placeholder"}')
            path.chmod(0o644)
            result=run('--bootstrap-file',str(path))
            self.assertNotEqual(result.returncode,0)
            self.assertIn('mode 600',result.stderr)
            self.assertNotIn('placeholder',result.stdout+result.stderr)

    def test_the_acceptance_rehearsal_keeps_its_own_evidence_file(self):
        from pathlib import Path
        script=(Path(__file__).resolve().parent.parent/'deploy'/'server-acceptance.sh').read_text()
        self.assertIn('--evidence docs/evidence/server-acceptance-rehearsal.json',script)
        rehearsal=(Path(__file__).resolve().parent/'deployment_rehearsal.py').read_text()
        self.assertIn("default='docs/evidence/deployment-rehearsal.json'",rehearsal)

    def test_the_acceptance_script_never_prints_bootstrap_contents(self):
        source=SCRIPT.read_text()
        for leak in ('cat "$BOOTSTRAP"','cat "${BOOTSTRAP}"','echo "$BOOTSTRAP"','head "$BOOTSTRAP"'):
            self.assertNotIn(leak,source)
        self.assertIn('contents never printed',source)

    def test_the_prerequisite_step_passes_on_this_host_so_preflight_speaks_next(self):
        result=run()
        output=result.stdout+result.stderr
        self.assertIn('ok: docker',output)
        self.assertIn('ok: /usr/bin/python3',output)
        self.assertIn('read-only preflight',output)
        # Either the host admits the plan, or the refusal is stated as a blocker.
        self.assertTrue('Preflight: 0 blocker' in output or 'FAIL: preflight refused' in output)


class AcceptanceScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.source=(Path(__file__).resolve().parent.parent/'deploy'/'server-acceptance.sh').read_text()

    def test_a_failed_step_stops_the_run(self):
        self.assertIn('set -euo pipefail',self.source)
        self.assertNotIn('|| true',self.source)

    def test_the_failure_message_does_not_promise_a_file_that_may_not_exist(self):
        self.assertIn('before it could write evidence',self.source)
        self.assertIn('[ -f docs/evidence/server-acceptance-rehearsal.json ]',self.source)

    def test_the_unit_is_released_before_the_rehearsal_and_restored_after(self):
        stop=self.source.index('systemctl stop sbarbase.service')
        rehearsal=self.source.index('lab/deployment_rehearsal.py "${rehearsal_args[@]}"')
        restart=self.source.index('systemctl start sbarbase.service')
        self.assertLess(stop,rehearsal)
        self.assertLess(rehearsal,restart)
        self.assertIn('did not stop',self.source)


if __name__=='__main__':unittest.main()