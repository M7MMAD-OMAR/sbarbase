"""Server preflight classification and installation driver (no docker required)."""
from pathlib import Path
from unittest.mock import patch
import json
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