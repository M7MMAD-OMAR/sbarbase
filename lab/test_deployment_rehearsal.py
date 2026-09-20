"""Deployment rehearsal helpers: status mapping and honest failure recording."""
from pathlib import Path
from unittest.mock import patch
import os
import tempfile
import unittest
import deployment_rehearsal as rehearsal

ROOT=Path(rehearsal.__file__).resolve().parent.parent


class RehearsalTests(unittest.TestCase):
    def test_http_status_maps_failure_to_a_value_never_to_success(self):
        status=rehearsal.http_status('http://127.0.0.1:1/')
        self.assertNotEqual(status,200)
        self.assertIsInstance(status,str)

    def test_rehearsal_stops_at_preflight_blockers_without_starting_anything(self):
        blocker=[('blocker','Host headroom insufficient: 100 MiB available, plan needs 8448 MiB')]
        with patch.object(rehearsal.install_server,'preflight',return_value=blocker), \
             patch.object(rehearsal.install_server,'install') as install, \
             patch.object(rehearsal,'start_supervisor') as start:
            findings,_=rehearsal.rehearse(None,False,5)
            install.assert_not_called();start.assert_not_called()
        self.assertEqual([item['ok'] for item in findings],[False])

    def test_recorded_checks_are_all_required_for_success(self):
        findings=[{'check':'a','ok':True},{'check':'b','ok':False}]
        self.assertFalse(all(item['ok'] for item in findings))
        self.assertTrue(all(item['ok'] for item in findings[:1]))

    def test_owned_running_reads_docker_ps(self):
        with patch.object(rehearsal.install_server,'docker') as docker:
            docker.return_value=type('R',(),{'stdout':''})()
            self.assertFalse(rehearsal.owned_running('durable-upstream'))
            docker.return_value=type('R',(),{'stdout':'abc123\n'})()
            self.assertTrue(rehearsal.owned_running('durable-upstream'))


class ServerEvidenceTests(unittest.TestCase):
    """The evidence has to let an operator certify a server run."""

    def test_host_facts_carry_versions_and_headroom_without_paths(self):
        facts=rehearsal.host_facts()
        for key in ('platform','kernel','machine','python','docker','bun','systemd','mem_available_mib','disk_free_mib'):
            self.assertIn(key,facts)
        self.assertTrue(facts['python'])
        self.assertIsInstance((facts['mem_available_mib'],facts['disk_free_mib'])[0],(int,type(None)))

    def test_unit_status_reports_absence_rather_than_guessing(self):
        from pathlib import Path
        status=rehearsal.unit_status(Path('/nonexistent/sbarbase.service'))
        self.assertFalse(status['installed'])
        self.assertIsNone(status['enabled'])
        self.assertEqual(status['verify_source'],'template')
        self.assertTrue(Path(status['verified_path']).exists())

    def test_the_shipped_unit_verifies_under_systemd_analyze(self):
        from pathlib import Path
        status=rehearsal.unit_status(Path(rehearsal.__file__).resolve().parent.parent/'deploy'/'sbarbase.service')
        self.assertTrue(status['installed'])
        self.assertEqual(status['verify'],'passed')

    def test_evidence_records_pins_that_match_the_lock_files(self):
        pins=[{'component':label,'digest':digest,'pull':reference} for label,digest,reference in rehearsal.install_server.pinned_images()]
        self.assertGreaterEqual(len(pins),4)
        for pin in pins:
            self.assertTrue(pin['digest'].startswith('sha256:'))
            self.assertIn('@sha256:',pin['pull'])


class StartupDiagnosticsTests(unittest.TestCase):
    """A supervisor that dies during startup must state why, in the evidence."""

    def test_supervisor_tail_reports_missing_and_empty_logs_honestly(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            missing=Path(directory)/'none.log'
            self.assertEqual(rehearsal.supervisor_tail(missing),'no supervisor output captured')
            empty=Path(directory)/'empty.log';empty.write_text('   \n')
            self.assertEqual(rehearsal.supervisor_tail(empty),'supervisor printed nothing')
            loud=Path(directory)/'loud.log';loud.write_text('\n'.join(f'line {n}' for n in range(12)))
            tail=rehearsal.supervisor_tail(loud,lines=3)
            self.assertEqual(tail,'line 9 | line 10 | line 11')

    def test_startup_failure_is_recorded_with_the_cause_and_no_crash(self):
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.console_build_check,'verify',return_value=([],{})), \
             patch.object(rehearsal,'start_supervisor',side_effect=RuntimeError('Supervisor exited during startup; reason line')):
            findings,_=rehearsal.rehearse(None,True,5)
        failed=[item for item in findings if not item['ok']]
        self.assertEqual(len(failed),1)
        self.assertIn('supervisor started and owns the console',failed[0]['check'])
        self.assertIn('reason line',failed[0]['detail'])
        self.assertFalse(all(item['ok'] for item in findings))

    def test_a_transient_refusal_is_retried_and_the_attempts_are_recorded(self):
        calls={'count':0}
        def flaky(timeout):
            calls['count']+=1
            if calls['count']==1:raise RuntimeError('Supervisor exited during startup; host_memory_headroom')
            return (object(),{'url':'http://127.0.0.1:1'})
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.console_build_check,'verify',return_value=([],{})), \
             patch.object(rehearsal,'start_supervisor',side_effect=flaky), \
             patch.object(rehearsal,'http_status',return_value=0), \
             patch.object(rehearsal.install_server,'smoke',return_value=True), \
             patch.object(rehearsal,'stop_supervisor',return_value=0), \
             patch.object(rehearsal,'owned_running',return_value=False), \
             patch.object(rehearsal,'run_bootstrap_check',return_value=None), \
             patch.object(rehearsal,'combined_gateway_check',return_value=(0,'stubbed')), \
             patch.object(rehearsal,'cutover_recorded',return_value=False), \
             patch.object(rehearsal,'unit_status',return_value={'installed':False,'verify':'not-run','verified_path':None,'verify_source':None}):
            findings,_=rehearsal.rehearse(None,True,5,attempts=3,delay=0)
        self.assertEqual(calls['count'],2)
        started=[item for item in findings if item['check']=='supervisor started and owns the console'][0]
        self.assertTrue(started['ok'])
        self.assertIn('after 2 attempts',started['detail'])
        self.assertIn('host_memory_headroom',started['detail'])

    def test_exhausting_the_attempts_is_context_not_a_green_check(self):
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.console_build_check,'verify',return_value=([],{})), \
             patch.object(rehearsal,'start_supervisor',side_effect=RuntimeError('Supervisor exited during startup; host_memory_headroom')):
            findings,context=rehearsal.rehearse(None,True,5,attempts=2,delay=0)
        self.assertEqual(context['startup_attempts'],2)
        self.assertEqual(len(context['startup_refusals']),2)
        self.assertNotIn('startup attempts before refusal',[item['check'] for item in findings])
        self.assertFalse([item for item in findings if item['check']=='supervisor started and owns the console'][0]['ok'])

    def test_the_systemd_unit_is_informational_unless_the_run_requires_it(self):
        absent={'installed':False,'enabled':None,'active':None,'verify':'not-run','verified_path':None,'verify_source':None}
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.console_build_check,'verify',return_value=([],{})), \
             patch.object(rehearsal,'start_supervisor',return_value=(object(),{'url':'http://127.0.0.1:1'})), \
             patch.object(rehearsal,'http_status',return_value=200), \
             patch.object(rehearsal.install_server,'smoke',return_value=True), \
             patch.object(rehearsal,'stop_supervisor',return_value=0), \
             patch.object(rehearsal,'owned_running',return_value=False), \
             patch.object(rehearsal,'cutover_recorded',return_value=False), \
             patch.object(rehearsal,'run_bootstrap_check',return_value=None), \
             patch.object(rehearsal,'unit_status',return_value=absent):
            lenient,_=rehearsal.rehearse(None,True,5)
            strict,_=rehearsal.rehearse(None,True,5,require_unit=True)
        lenient_unit=[item for item in lenient if item['check']=='the shipped supervisor unit is not required for this run'][0]
        strict_unit=[item for item in strict if item['check']=='the shipped supervisor unit is installed for an acceptance run'][0]
        self.assertTrue(lenient_unit['ok'])
        self.assertIn('nothing under systemd was exercised',lenient_unit['detail'])
        self.assertFalse(strict_unit['ok'])
        self.assertIn('requires it',strict_unit['detail'])

    def test_the_acceptance_script_requires_the_unit(self):
        from pathlib import Path
        script=(Path(__file__).resolve().parent.parent/'deploy'/'server-acceptance.sh').read_text()
        self.assertIn('--require-unit',script)
        self.assertIn('--attempts 3',script)

    def test_the_acceptance_script_can_install_the_unit_only_as_root(self):
        from pathlib import Path
        script=(Path(__file__).resolve().parent.parent/'deploy'/'server-acceptance.sh').read_text()
        self.assertIn('--install-unit',script)
        self.assertIn('"${supervise_args[@]}" --apply',script)
        self.assertIn('"$(id -u)" = "0"',script)


class BootstrapStepTests(unittest.TestCase):
    """Install step "operator bootstrap" is part of the acceptance evidence."""

    def test_the_step_is_skipped_only_when_explicitly_disabled(self):
        with patch.dict(os.environ,{'SBARBASE_SKIP_BOOTSTRAP_CHECK':'1'}):
            self.assertIsNone(rehearsal.run_bootstrap_check())
        with patch.dict(os.environ,{},clear=False), \
             patch.object(rehearsal.subprocess,'run') as run:
            os.environ.pop('SBARBASE_SKIP_BOOTSTRAP_CHECK',None)
            run.return_value=type('R',(),{'returncode':0,'stdout':'18 live operator bootstrap checks passed.\n','stderr':''})()
            self.assertEqual(rehearsal.run_bootstrap_check(),(0,'18 live operator bootstrap checks passed.'))

    def test_a_failing_bootstrap_step_is_recorded_with_its_output(self):
        with patch.dict(os.environ,{},clear=False), \
             patch.object(rehearsal.subprocess,'run') as run:
            os.environ.pop('SBARBASE_SKIP_BOOTSTRAP_CHECK',None)
            run.return_value=type('R',(),{'returncode':1,'stdout':'','stderr':'Test operator cleanup failed'})()
            code,detail=rehearsal.run_bootstrap_check()
        self.assertEqual(code,1)
        self.assertIn('cleanup failed',detail)

    def test_the_rehearsal_records_the_bootstrap_check_when_it_runs(self):
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.console_build_check,'verify',return_value=([],{})), \
             patch.object(rehearsal,'start_supervisor',return_value=(object(),{'url':'http://127.0.0.1:1'})), \
             patch.object(rehearsal,'http_status',return_value=200), \
             patch.object(rehearsal.install_server,'smoke',return_value=True), \
             patch.object(rehearsal,'run_bootstrap_check',return_value=(0,'18 live operator bootstrap checks passed.')), \
             patch.object(rehearsal,'cutover_recorded',return_value=True), \
             patch.object(rehearsal,'combined_gateway_check',return_value=(0,'14 combined gateway checks passed')), \
             patch.object(rehearsal,'stop_supervisor',return_value=0), \
             patch.object(rehearsal,'owned_running',return_value=False), \
             patch.object(rehearsal,'cutover_recorded',return_value=False), \
             patch.object(rehearsal,'unit_status',return_value={'installed':False,'verify':'not-run','verified_path':None,'verify_source':None}):
            findings,_=rehearsal.rehearse(None,True,5)
        names=[item['check'] for item in findings]
        self.assertIn('operator bootstrap checks passed against the live management Auth',names)
        self.assertTrue(all(item['ok'] for item in findings))

    def test_a_skipped_bootstrap_step_leaves_no_check_behind(self):
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.console_build_check,'verify',return_value=([],{})), \
             patch.object(rehearsal,'start_supervisor',return_value=(object(),{'url':'http://127.0.0.1:1'})), \
             patch.object(rehearsal,'http_status',return_value=200), \
             patch.object(rehearsal.install_server,'smoke',return_value=True), \
             patch.object(rehearsal,'run_bootstrap_check',return_value=None), \
             patch.object(rehearsal,'stop_supervisor',return_value=0), \
             patch.object(rehearsal,'owned_running',return_value=False), \
             patch.object(rehearsal,'cutover_recorded',return_value=False), \
             patch.object(rehearsal,'unit_status',return_value={'installed':False,'verify':'not-run','verified_path':None,'verify_source':None}):
            findings,_=rehearsal.rehearse(None,True,5)
        self.assertNotIn('operator bootstrap checks passed against the live management Auth',
                         [item['check'] for item in findings])


class InstallFailureTests(unittest.TestCase):
    """An install refusal is a finding the rehearsal records, not a crash."""

    def test_a_refused_install_is_recorded_and_the_rehearsal_returns(self):
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.install_server,'install',side_effect=SystemExit('Runtime startup failed: lock held')):
            findings,context=rehearsal.rehearse(None,False,5)
        failed=[item for item in findings if item['check']=='installation steps completed'][0]
        self.assertFalse(failed['ok'])
        self.assertIn('lock held',failed['detail'])
        self.assertIn('lock held',context['install_failed'])
        self.assertFalse([item for item in findings if item['check']=='supervisor started and owns the console'])

    def test_a_non_system_exit_from_the_installer_is_also_recorded(self):
        with patch.object(rehearsal.install_server,'preflight',return_value=[]), \
             patch.object(rehearsal.install_server,'install',side_effect=RuntimeError('BlockingIOError: [Errno 11] Resource temporarily unavailable')):
            findings,context=rehearsal.rehearse(None,False,5)
        self.assertIn('Resource temporarily unavailable',context['install_failed'])
        self.assertFalse([item for item in findings if item['check']=='installation steps completed'][0]['ok'])


class EvidenceHygieneTests(unittest.TestCase):
    def test_the_bootstrap_file_path_is_redacted_from_the_recorded_command(self):
        redacted=rehearsal.redacted_arguments(['--skip-install','--bootstrap-file','/root/operator.json','--attempts','2'])
        self.assertEqual(redacted,['--skip-install','--bootstrap-file','<bootstrap-file>','--attempts','2'])
        self.assertNotIn('/root/operator.json',' '.join(redacted))

    def test_the_equals_form_of_the_bootstrap_argument_is_redacted_too(self):
        redacted=rehearsal.redacted_arguments(['--rehearse','--bootstrap-file=/root/operator.json','--attempts','2'])
        self.assertEqual(redacted[1],'--bootstrap-file=<bootstrap-file>')
        self.assertNotIn('/root/operator.json',' '.join(redacted))

    def test_the_unit_is_verified_where_systemd_runs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            installed=Path(directory)/'sbarbase.service'
            installed.write_text((ROOT/'deploy'/'sbarbase.service').read_text())
            with patch.object(rehearsal,'UNIT',installed):
                status=rehearsal.unit_status(path=installed)
        self.assertTrue(status['installed'])
        self.assertEqual(status['verify_source'],'installed')
        self.assertEqual(status['verified_path'],str(installed))

    def test_without_an_installed_unit_only_the_template_is_verified_and_labelled(self):
        status=rehearsal.unit_status(path=Path('/nonexistent/sbarbase.service'))
        self.assertFalse(status['installed'])
        self.assertEqual(status['verify_source'],'template')
        self.assertIn('deploy/sbarbase.service',status['verified_path'])

    def test_the_evidence_carries_the_attempts_outside_the_checks(self):
        source=(ROOT/'lab'/'deployment_rehearsal.py').read_text()
        self.assertIn("'startup':{'attempts_allowed'",source)
        self.assertNotIn("'check':'startup attempts before refusal'",source)


if __name__=='__main__':unittest.main()