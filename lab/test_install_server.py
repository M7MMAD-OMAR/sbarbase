"""Server preflight classification and installation driver (no docker required)."""
from pathlib import Path
import tempfile
import unittest
import install_server


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


if __name__=='__main__':unittest.main()