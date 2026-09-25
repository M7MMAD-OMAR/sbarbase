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


def host_python_is_modern():
    try:
        result=subprocess.run(['/usr/bin/python3','-c','import sys;print(sys.version_info>=(3,14))'],capture_output=True,text=True,timeout=30)
    except OSError:
        return False
    return result.stdout.strip()=='True'


def docker_answers():
    try:
        return subprocess.run(['docker','info'],capture_output=True,timeout=30).returncode==0
    except (OSError,subprocess.TimeoutExpired):
        return False


# The script checks /usr/bin/python3 and then the Docker daemon before it reads any
# argument's file, so a refusal that comes later can only be observed on a host that
# passes both checks.
MODERN_PYTHON=host_python_is_modern()
DOCKER=docker_answers()
HOST_GAPS=', '.join(gap for gap,missing in (('/usr/bin/python3 is older than 3.14',not MODERN_PYTHON),
                                            ('no Docker daemon answers',not DOCKER)) if missing)


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

    @unittest.skipUnless(MODERN_PYTHON and DOCKER,'the script refuses this host first: '+HOST_GAPS)
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

    @unittest.skipUnless(MODERN_PYTHON and DOCKER,'the script refuses this host first: '+HOST_GAPS)
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

    def test_the_service_account_and_paths_are_named_for_the_installer(self):
        for flag in ('--service-user','--home','--bun-dir'):
            self.assertIn(flag,self.source)
        self.assertIn('supervise_args+=(--service-user "$SERVICE_USER")',self.source)
        self.assertIn('"${supervise_args[@]}"',self.source)

    def test_an_empty_host_builds_the_console_before_the_serving_check_reads_it(self):
        # Found by the first empty-host run: the serving check ran before anything
        # had been built and failed with 503 on every page request.
        build=self.source.index('step "console build"')
        serving=self.source.index('step "console static-serving check"')
        self.assertLess(build,serving)
        section=self.source[build:serving]
        self.assertIn('bun install --frozen-lockfile',section)
        self.assertIn('lab/console_build_check.py',section)

    def test_the_first_project_step_runs_only_on_request_and_after_the_unit_is_back(self):
        self.assertIn('--first-project) FIRST_PROJECT=1',self.source)
        step=self.source.index('step "first project"')
        self.assertGreater(step,self.source.index('step "evidence"'))
        section=self.source[step:]
        self.assertIn('--first-project needs --bootstrap-file',section)
        self.assertIn('lab/first-project-check.ts "$BOOTSTRAP"',section)

    def test_the_unit_is_restored_once_not_again_by_the_exit_trap(self):
        restore=self.source.index('  restore_unit_on_exit\n  # Restored once here')
        self.assertIn('STOPPED_UNIT=0',self.source[restore:restore+200])

    def test_bun_dir_is_added_to_path_for_every_step(self):
        self.assertIn('PATH="$BUN_DIR:$PATH"',self.source)
        self.assertIn('export PATH',self.source)

    def test_a_missing_bun_under_sudo_names_the_flag_that_fixes_it(self):
        self.assertIn('sudo replaces PATH',self.source)
        self.assertIn('--bun-dir /home/$SUDO_USER/.bun/bin',self.source)

    def test_the_failure_message_does_not_promise_a_file_that_may_not_exist(self):
        self.assertIn('before it could write evidence',self.source)
        self.assertIn('[ -f docs/evidence/server-acceptance-rehearsal.json ]',self.source)

    def test_the_unit_is_released_before_the_rehearsal_and_restored_after(self):
        stop=self.source.index('unit_control stop sbarbase.service')
        rehearsal=self.source.index('lab/deployment_rehearsal.py "${rehearsal_args[@]}"')
        self.assertLess(stop,rehearsal)
        # the restore runs from a trap, and is also called explicitly afterwards
        self.assertIn('trap restore_unit_on_exit EXIT',self.source)
        self.assertIn('unit_control start sbarbase.service',self.source)
        self.assertGreater(self.source.rindex('restore_unit_on_exit'),rehearsal)
        self.assertIn('did not stop',self.source)

    def test_the_unit_is_released_whether_or_not_it_was_just_installed(self):
        release=self.source.index('step "release the supervised installation for the rehearsal"')
        self.assertIn('if [ "$(unit_state)" = "active" ] || [ "$(unit_state)" = "activating" ]',self.source)
        self.assertGreater(release,0)
        # the stop is no longer nested under --install-unit alone
        nested=[line for line in self.source.splitlines() if 'INSTALL_UNIT' in line and 'systemctl stop' in line]
        self.assertEqual(nested,[])

    def test_the_restore_function_actually_starts_the_unit(self):
        lines=self.source.splitlines()
        def block(name):
            start=next(i for i,line in enumerate(lines) if line.startswith(name))
            if lines[start].rstrip().endswith('}'):return lines[start]+'\n'
            end=next(i for i in range(start+1,len(lines)) if lines[i]=='}')
            return '\n'.join(lines[start:end+1])+'\n'
        harness=('systemctl() { echo active; return 0; }; unit_control() { systemctl "$@"; }; STOPPED_UNIT=1; '
                 'console_answers() { return 0; }; '
                 +block('unit_state()')+block('restore_unit_on_exit()')+'restore_unit_on_exit')
        result=subprocess.run(['bash','-c',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('sbarbase.service active again and its console answers',result.stdout)
        # Active but silent is a failure, not a restore.
        silent=harness.replace('console_answers() { return 0; }','console_answers() { return 1; }')
        result=subprocess.run(['bash','-c',silent],capture_output=True,text=True)
        self.assertIn('its console did not answer',result.stderr)
        self.assertNotIn('ok: sbarbase.service active again',result.stdout)

    def test_every_start_of_the_unit_waits_for_the_console_to_answer(self):
        # systemd said active in the same second it started the unit; the
        # acceptance recorded that as a working installation.
        self.assertIn('lab/install_server.py wait-console --timeout "$CONSOLE_WAIT"',self.source)
        self.assertIn('--apply --timeout "$CONSOLE_WAIT"',self.source)
        lines=self.source.splitlines()
        rehearsal_restore=next(i for i,line in enumerate(lines) if 'is not active after the rehearsal' in line)
        self.assertIn('console_answers ||',lines[rehearsal_restore+1])
        first=self.source[self.source.index('step "first project"'):]
        self.assertIn('console_answers ||',first)
        self.assertNotIn('seq 1 60',first)

    def test_the_console_bound_is_validated(self):
        result=run('--console-timeout','soon')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('--console-timeout must be a positive whole number',result.stderr)

    def test_a_failure_after_the_stop_still_starts_the_unit_again(self):
        self.assertIn('trap restore_unit_on_exit EXIT',self.source)
        self.assertIn('STOPPED_UNIT=1',self.source)
        self.assertIn('restore_unit_on_exit',self.source)

    def test_unit_state_reports_the_units_own_word_for_a_stopped_unit(self):
        definition=[line for line in self.source.splitlines() if line.startswith('unit_state()')][0]
        script='systemctl() { echo inactive; return 3; }; '+definition+'; printf "[%s]" "$(unit_state)"'
        result=subprocess.run(['bash','-c',script],capture_output=True,text=True)
        self.assertEqual(result.stdout,'[inactive]',result.stdout+result.stderr)

    def test_every_step_that_touches_the_installation_runs_as_its_account(self):
        for step in ('run_as_installation "$PYTHON" lab/install_server.py check',
                     'run_as_installation bun lab/console-serve-check.ts',
                     'run_as_installation "$PYTHON" lab/tls_termination_check.py',
                     'run_as_installation "$PYTHON" lab/deployment_rehearsal.py'):
            self.assertIn(step,self.source)
        # the unit install itself needs root, and only it
        self.assertIn('"$PYTHON" lab/install_server.py "${supervise_args[@]}" --apply',self.source)
        self.assertNotIn('run_as_installation "$PYTHON" lab/install_server.py "${supervise_args[@]}" --apply',self.source)

    def test_system_control_uses_sudo_when_the_run_is_not_root(self):
        self.assertIn('unit_control() {',self.source)
        self.assertIn('sudo -n systemctl "$@"',self.source)
        for call in ('unit_control stop sbarbase.service','unit_control start sbarbase.service',
                     'unit_control is-active --quiet sbarbase.service'):
            self.assertIn(call,self.source)

    def test_the_service_user_defaults_to_the_checkout_owner(self):
        self.assertIn("REPO_OWNER=\"$(stat -c '%U' \"$REPO_ROOT\")\"",self.source)
        self.assertIn('using the checkout owner',self.source)

    @unittest.skipIf(os.geteuid()==0,'this run is root, so the wrapper switches user instead')
    def test_the_wrapper_runs_the_command_directly_when_the_run_is_not_root(self):
        lines=self.source.splitlines()
        start=next(i for i,line in enumerate(lines) if line.startswith('run_as_installation()'))
        end=next(i for i in range(start+1,len(lines)) if lines[i]=='}')
        definition='\n'.join(lines[start:end+1])+'\n'
        harness=('fail() { echo "fail: $1" >&2; return 1; }; SERVICE_USER=sbarah; '
                 +definition+'run_as_installation bash -c "echo ran-as $(id -un)"')
        result=subprocess.run(['bash','-c',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('ran-as '+subprocess.run(['id','-un'],capture_output=True,text=True).stdout.strip(),result.stdout)

    def test_the_docker_endpoint_is_named_and_forwarded_to_every_step(self):
        self.assertIn('--docker-host) shift;',self.source)
        self.assertIn('export DOCKER_HOST=\"$DOCKER_HOST_ARG\"',self.source)
        self.assertIn('*[[:space:]]*) fail \"--docker-host must be a single endpoint',self.source)
        # named before the steps that use it, and carried into the step account
        self.assertLess(self.source.index('export DOCKER_HOST=\"$DOCKER_HOST_ARG\"'),
                        self.source.index('install_server.py check'))
        self.assertIn('environment+=(\"DOCKER_HOST=$DOCKER_HOST\")',self.source)

    def test_the_help_documents_the_docker_endpoint(self):
        help_text=subprocess.run(['bash',str(ROOT/'deploy'/'server-acceptance.sh'),'--help'],
                                 capture_output=True,text=True)
        self.assertEqual(help_text.returncode,0,help_text.stderr)
        self.assertIn('--docker-host',help_text.stdout)

    def test_the_step_messages_say_whether_evidence_was_written(self):
        for path in ('docs/evidence/console-serve.json','docs/evidence/tls-termination.json'):
            self.assertIn(path,self.source)
        self.assertIn('before it could write evidence',self.source)


if __name__=='__main__':unittest.main()