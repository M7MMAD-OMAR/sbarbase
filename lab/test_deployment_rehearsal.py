"""Deployment rehearsal helpers: status mapping and honest failure recording."""
from unittest.mock import patch
import unittest
import deployment_rehearsal as rehearsal


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
        self.assertEqual(status,{'installed':False,'enabled':None,'active':None,'verify':'not-run'})

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


if __name__=='__main__':unittest.main()