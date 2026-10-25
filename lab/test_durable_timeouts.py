"""Bounds on the durable lifecycle's external calls: a stuck call fails the operation closed and its child is killed."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import durable_probe
import run as lab
import test_durable_inventory as base
import test_durable_lifecycle as lifecycle_tests

RUNTIME = base.RUNTIME


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f'/proc/{pid}/stat') as handle:
        return handle.read().split(') ')[-1][0] != 'Z'


class DockerTimeoutTests(unittest.TestCase):
    def test_a_timeout_kills_the_child_and_fails_with_a_plain_error(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pid'
            command = ['/bin/sh', '-c', f'echo $$ > {marker}; exec sleep 30']
            with patch.object(lab.docker_profile, 'docker_command', return_value=command):
                started = time.monotonic()
                with self.assertRaises(RuntimeError) as caught:
                    lab.docker('ps', timeout=0.5)
            self.assertLess(time.monotonic() - started, 10)
            self.assertIn('timed out', str(caught.exception))
            self.assertFalse(alive(int(marker.read_text())))

    def test_without_a_timeout_every_other_caller_is_unchanged(self):
        with patch.object(lab.subprocess, 'run') as run, patch.object(lab.docker_profile, 'docker_command', return_value=['x']):
            run.return_value.returncode = 0
            lab.docker('ps')
        self.assertIsNone(run.call_args.kwargs['timeout'])


class LiveProbeBoundTests(unittest.TestCase):
    def test_every_docker_and_sql_call_of_the_durable_probe_carries_a_timeout(self):
        probe = durable_probe.LiveProbe(Path('/nonexistent'), Path('/nonexistent'))
        with patch.object(lab, 'docker') as docker:
            docker.return_value.stdout = ''
            probe.docker('stop', 'x')
            probe.containers()
            probe.sql('SELECT 1;')
        self.assertGreaterEqual(docker.call_count, 3)
        for call in docker.call_args_list:
            self.assertIsNotNone(call.kwargs.get('timeout'), call)


class TerminateBoundTests(unittest.TestCase):
    def test_the_terminate_statement_runs_under_the_statement_timeout(self):
        host = base.FakeHost((RUNTIME, base.OTHER))
        queries = []
        original = host.sql
        host.sql = lambda query, database='postgres': (queries.append(query), original(query, database))[1]
        steps = lifecycle_tests.Steps(host)
        steps.run('quarantine', str(uuid4()))
        terminate = [query for query in queries if 'pg_terminate_backend' in query]
        self.assertEqual(len(terminate), 1)
        self.assertTrue(terminate[0].startswith("SET lock_timeout = '5s'; SET statement_timeout = '30s'; "))

    def test_a_timeout_mid_quarantine_stays_resumable(self):
        host = base.FakeHost((RUNTIME, base.OTHER))
        steps = lifecycle_tests.Steps(host)
        delete = str(uuid4())
        original = host.sql

        def stuck(query, database='postgres'):
            if 'pg_terminate_backend' in query:
                raise RuntimeError('Docker operation exec timed out after 45s')
            return original(query, database)
        host.sql = stuck
        with self.assertRaises(RuntimeError):
            steps.run('quarantine', delete)
        host.sql = original
        results = steps.run('quarantine', delete)
        self.assertEqual([item['outcome'] for item in results], ['quarantined'] * 3)
        self.assertEqual(host.journals[RUNTIME]['tenant']['phase'], 'quarantined')


if __name__ == '__main__':
    unittest.main()
