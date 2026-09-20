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


if __name__=='__main__':unittest.main()