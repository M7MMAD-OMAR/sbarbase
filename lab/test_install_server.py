"""Server preflight classification and installation driver (no docker required)."""
from pathlib import Path
from unittest.mock import patch
import json
import os
import tempfile
import unittest
import install_server


def result(returncode=0,stdout='',stderr=''):
    return type('R',(),{'returncode':returncode,'stdout':stdout,'stderr':stderr})()


class TargetClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)

    def pin(self,prefix):
        directory=self.state/'targets'/prefix
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'hba-generation.json').write_text('{}')

    def test_current_target_without_pin_blocks(self):
        findings=install_server.target_findings(['sbarbase-restore-0123456789ab-db','sbarbase-restore-0123456789ab-auth'],
                                                self.state,'sbarbase-restore-0123456789ab')
        self.assertEqual([kind for kind,_ in findings],['blocker'])

    def test_current_target_with_pin_is_not_a_blocker(self):
        self.pin('sbarbase-restore-0123456789ab')
        findings=install_server.target_findings(['sbarbase-restore-0123456789ab-db'],self.state,'sbarbase-restore-0123456789ab')
        self.assertEqual([kind for kind,_ in findings],['action'])

    def test_historical_target_without_pin_is_information_only(self):
        self.pin('sbarbase-restore-0123456789ab')
        findings=install_server.target_findings(['sbarbase-restore-0123456789ab-db','sbarbase-restore-fedcba987654-db'],
                                                self.state,'sbarbase-restore-0123456789ab')
        kinds={kind for kind,_ in findings}
        self.assertEqual(kinds,{'action','info'})
        self.assertFalse(any(kind=='blocker' for kind,_ in findings))

    def test_no_current_target_reports_history_only(self):
        findings=install_server.target_findings(['sbarbase-restore-fedcba987654-db'],self.state,None)
        self.assertEqual([kind for kind,_ in findings],['info'])

    def test_service_containers_are_not_treated_as_databases(self):
        findings=install_server.target_findings(['sbarbase-restore-0123456789ab-auth','sbarbase-restore-0123456789ab-storage'],
                                                self.state,'sbarbase-restore-0123456789ab')
        self.assertEqual(findings,[])


class PlanTests(unittest.TestCase):
    def test_plan_lists_the_verified_steps(self):
        source=(Path(__file__).resolve().parent/'install_server.py').read_text()
        for step in ('check','plan','install','smoke'):
            self.assertIn("'"+step+"'",source)
        self.assertIn('bootstrap.py',source)
        self.assertIn('build:ui',source)
        self.assertIn('installation_runtime.py',source)


class InterpreterPreflightTests(unittest.TestCase):
    def findings(self,version,imports=True):
        def fake(command,**kwargs):
            if command[-1]=='import cryptography':return result(0 if imports else 1)
            return result(0,version+'\n')
        with patch.object(install_server.shutil,'which',return_value='/usr/bin/tool'), \
             patch.object(install_server.Path,'exists',return_value=True), \
             patch.object(install_server,'run',side_effect=fake):
            return install_server.versions()

    def test_the_ubuntu_and_debian_interpreters_pass(self):
        for version in ('3.12','3.13','3.14'):
            self.assertEqual(self.findings(version),[],version)

    def test_an_older_interpreter_is_a_blocker_that_names_the_floor(self):
        findings=self.findings('3.11')
        self.assertEqual([kind for kind,_ in findings],['blocker'])
        self.assertIn('3.12 or newer, found 3.11',findings[0][1])

    def test_a_missing_cryptography_module_names_the_package(self):
        findings=self.findings('3.12',imports=False)
        self.assertEqual([kind for kind,_ in findings],['blocker'])
        self.assertIn('python3-cryptography',findings[0][1])


class UnreachableDaemonPreflightTests(unittest.TestCase):
    """Without a daemon the preflight must not guess about images or containers."""

    def preflight(self,side_effect):
        with patch.object(install_server,'docker',side_effect=side_effect):
            return install_server.preflight()

    def test_no_pull_or_fresh_install_claim_is_made_without_a_daemon(self):
        findings=self.preflight(lambda *args,**kwargs: result(1,'','Cannot connect to the Docker daemon at unix:///nope.sock'))
        details=' | '.join(detail for _,detail in findings)
        self.assertIn('Docker daemon unreachable',details)
        self.assertIn('were not inspected',details)
        self.assertNotIn('will pull',details)
        self.assertNotIn('fresh install',details)
        self.assertTrue(any(kind=='blocker' for kind,detail in findings))

    def test_the_endpoint_it_tried_is_named(self):
        findings=self.preflight(lambda *args,**kwargs: result(1,'','nope'))
        self.assertIn('tried ', ' | '.join(detail for _,detail in findings))

    def test_with_a_reachable_daemon_the_inventory_is_reported(self):
        info=json.dumps({'OSType':'linux','Name':__import__('os').uname().nodename,'MemTotal':1})
        def side_effect(*args,**kwargs):
            command=list(args[0]) if args and isinstance(args[0],(list,tuple)) else list(args)
            if command[:2]==['info','--format']:return result(0,info)
            if command[0]=='context':return result(0,'unix:///var/run/docker.sock')
            return result(0,'')
        findings=self.preflight(side_effect)
        details=[detail for _,detail in findings]
        self.assertTrue(any('fresh install' in detail for detail in details),details)


if __name__=='__main__':unittest.main()

class InterruptedFirstInstallTests(unittest.TestCase):
    """Found by the first empty-VM install: a failed first launch was called a retained source."""

    def findings(self,started_at):
        def docker(*args,**kwargs):
            if args[:2]==('ps','-a') and 'label=io.sbarbase.owner=durable-upstream' in args:return result(stdout='sbarbase-durable-db\n')
            if args[:2]==('ps','-a'):return result(stdout='')
            if args[0]=='ps':return result(stdout='')
            if args[0]=='inspect':return result(stdout=started_at+'\n')
            raise AssertionError(args)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(install_server,'STATE',Path(directory)), \
             patch.object(install_server,'docker',side_effect=docker), \
             patch.object(install_server,'run',return_value=result(0)):
            return install_server.state()

    def test_never_started_containers_are_named_as_an_interrupted_install(self):
        blockers=[detail for kind,detail in self.findings('0001-01-01T00:00:00Z') if kind=='blocker']
        self.assertEqual(len(blockers),1)
        self.assertIn('interrupted first install',blockers[0])
        self.assertIn('Do not adopt them',blockers[0])

    def test_a_container_that_ran_is_still_a_retained_source_to_adopt(self):
        blockers=[detail for kind,detail in self.findings('2026-09-23T01:14:26Z') if kind=='blocker']
        self.assertEqual(len(blockers),1)
        self.assertIn('adopt it with lab/adopt-retained.py source',blockers[0])


class PinnedImagePullTests(unittest.TestCase):
    """Found by the second empty-VM rehearsal: a 1.7 GB pull hit the 600 s command timeout."""

    def test_a_pull_has_its_own_long_budget_and_shows_progress(self):
        calls=[]
        def runner(command,**kwargs):
            calls.append((command,kwargs));return result(0)
        install_server.pull_image('db','repo@sha256:x','1/5',runner=runner)
        command,kwargs=calls[0]
        self.assertEqual(command,['docker','pull','repo@sha256:x'])
        self.assertGreaterEqual(kwargs['timeout'],3600)
        self.assertNotIn('capture_output',kwargs)

    def test_a_timed_out_pull_is_retried_then_named(self):
        import subprocess
        attempts=[]
        def runner(command,**kwargs):
            attempts.append(command);raise subprocess.TimeoutExpired(command,kwargs['timeout'])
        with self.assertRaises(SystemExit) as refused:
            install_server.pull_image('db','repo@sha256:x','1/5',runner=runner)
        self.assertEqual(len(attempts),install_server.PULL_ATTEMPTS)
        self.assertIn('Pinned image pull failed for db',str(refused.exception))

    def test_a_retry_that_succeeds_continues_the_install(self):
        outcomes=iter([result(1),result(0)])
        install_server.pull_image('db','repo@sha256:x','1/5',runner=lambda command,**kwargs:next(outcomes))


class ConsoleWaitTests(unittest.TestCase):
    """systemd says active the moment dev.py is executed; a start counts once the console answers."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)

    def serve(self,status):
        import http.server
        import threading
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status);self.send_header('Content-Length','0');self.end_headers()
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}'

    def record(self,url,pid=None,server_pid=None):
        pid=pid or os.getpid()
        (self.state/'server.json').write_text(json.dumps({'url':url,'pid':pid}))
        (self.state/'supervisor.json').write_text(json.dumps({'pid':1,'serverPid':server_pid or pid}))

    def test_a_listening_console_answers_even_with_a_client_error_status(self):
        self.record(self.serve(404))
        answered,detail=install_server.console_answer(self.state)
        self.assertTrue(answered,detail)
        self.assertIn('HTTP 404',detail)

    def test_a_server_error_is_not_an_answer(self):
        self.record(self.serve(503))
        answered,detail=install_server.console_answer(self.state)
        self.assertFalse(answered)
        self.assertIn('HTTP 503',detail)

    def test_no_record_yet_is_not_an_answer(self):
        answered,detail=install_server.console_answer(self.state)
        self.assertFalse(answered)
        self.assertIn('server.json not written yet',detail)

    def test_a_stale_record_the_supervisor_does_not_own_is_not_an_answer(self):
        self.record(self.serve(200),server_pid=os.getpid()+1)
        answered,detail=install_server.console_answer(self.state)
        self.assertFalse(answered)
        self.assertIn('stale record',detail)

    def test_a_closed_port_is_not_an_answer(self):
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        self.record(f'http://127.0.0.1:{port}')
        answered,detail=install_server.console_answer(self.state)
        self.assertFalse(answered)
        self.assertIn('did not answer',detail)

    def test_a_non_loopback_address_is_refused(self):
        self.record('http://192.0.2.1:8080')
        answered,detail=install_server.console_answer(self.state)
        self.assertFalse(answered)
        self.assertIn('loopback',detail)

    def clock(self):
        now=[0.0]
        def sleep(seconds):now[0]+=seconds
        return (lambda:now[0]),sleep

    def test_the_wait_ends_when_the_console_answers(self):
        clock,sleep=self.clock()
        outcomes=iter([(False,'server.json not written yet')]*3+[(True,'console answered HTTP 200')])
        answered,detail=install_server.wait_for_console(60,probe=lambda:next(outcomes),clock=clock,sleep=sleep,interval=2)
        self.assertTrue(answered)
        self.assertEqual(detail,'console answered HTTP 200')
        self.assertEqual(clock(),6)

    def test_the_wait_is_bounded_and_names_the_last_observation(self):
        clock,sleep=self.clock()
        probes=[]
        def probe():
            probes.append(clock());return False,'console at http://127.0.0.1:1 did not answer (refused)'
        answered,detail=install_server.wait_for_console(10,probe=probe,clock=clock,sleep=sleep,interval=2)
        self.assertFalse(answered)
        self.assertIn('did not answer within 10 s',detail)
        self.assertIn('last: console at http://127.0.0.1:1 did not answer',detail)
        self.assertIn('journalctl -u sbarbase.service',detail)
        self.assertEqual(probes[-1],10)

    def test_a_failed_unit_ends_the_wait_at_once(self):
        clock,sleep=self.clock()
        answered,detail=install_server.wait_for_console(300,probe=lambda:(False,'server.json not written yet'),
                                                         unit_state=lambda:'failed',clock=clock,sleep=sleep)
        self.assertFalse(answered)
        self.assertIn('failed before the console answered',detail)
        self.assertEqual(clock(),0)

    def test_an_activating_unit_keeps_the_wait_going(self):
        clock,sleep=self.clock()
        outcomes=iter([(False,'x'),(True,'answered')])
        answered,_=install_server.wait_for_console(60,probe=lambda:next(outcomes),unit_state=lambda:'activating',
                                                   clock=clock,sleep=sleep)
        self.assertTrue(answered)

    def test_supervise_records_the_unit_applied_only_after_the_console_answers(self):
        source=Path(install_server.__file__).read_text()
        start=source.index('def supervise(');end=source.index('\ndef ',start+1)
        body=source[start:end]
        self.assertLess(body.index("'enable','--now','sbarbase.service'"),body.index('wait_for_console'))
        self.assertLess(body.index('wait_for_console'),body.index('applied=True'))
        self.assertIn("'console':console",body)
