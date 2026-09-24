"""The Docker install: the compose settings the control plane depends on stay in place."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.compose = (ROOT / 'compose.yaml').read_text()

    def test_the_supervisor_is_never_process_one(self):
        # lab/parent_bound.py refuses a parent of pid 1.
        self.assertIn('init: true', self.compose)

    def test_paths_and_loopback_mean_the_same_inside_and_outside(self):
        self.assertIn('network_mode: host', self.compose)
        self.assertIn('- ${SBARBASE_ROOT:-${PWD}}:${SBARBASE_ROOT:-${PWD}}', self.compose)
        self.assertIn('SBARBASE_ROOT: ${SBARBASE_ROOT:-${PWD}}', self.compose)

    def test_the_daemon_and_its_disk_are_reachable(self):
        self.assertIn('- /var/run/docker.sock:/var/run/docker.sock', self.compose)
        self.assertIn('- /var/lib/docker:/var/lib/docker:ro', self.compose)

    def test_a_failed_upgrade_is_restarted_on_the_previous_version(self):
        # lab/upgrade.py moves the checkout back and the supervisor exits 1; the restart
        # policy is what starts the previous version.
        self.assertIn('restart: unless-stopped', self.compose)
        self.assertIn('Restart=on-failure', (ROOT / 'deploy' / 'sbarbase.service').read_text())

    def test_a_stop_leaves_time_to_stop_every_service(self):
        self.assertIn('stop_grace_period: 3m', self.compose)

    def test_the_image_carries_the_tested_interpreter(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertIn('FROM ubuntu:26.04', dockerfile)
        self.assertIn('python3-cryptography', dockerfile)
        self.assertIn('oven/bun:1.3.14', dockerfile)
        start = (ROOT / 'deploy/container/start.sh').read_text()
        self.assertIn('exec /usr/bin/python3 lab/dev.py', start)
        # A clean host has none of the pinned images; the supervisor does not pull them.
        self.assertLess(start.index('install_server.py images'), start.index('lab/dev.py'))


if __name__ == '__main__':
    unittest.main()
