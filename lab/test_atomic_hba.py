"""Prepared configuration requests retain exact target and revision identity."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import atomic_hba


class AtomicHBATests(unittest.TestCase):
    def test_prepared_request_is_immutable_and_uses_captured_container(self):
        docker=Mock(side_effect=[SimpleNamespace(stdout='a'*64+'\n'),SimpleNamespace(stdout='b'*64+'  /etc/postgresql/pg_hba.conf\n'),SimpleNamespace(returncode=0)])
        prepared=atomic_hba.prepare(docker,'mutable-name','local all all trust\n')
        with self.assertRaises(FrozenInstanceError):prepared.expected_digest='c'*64
        atomic_hba.apply(docker,prepared)
        self.assertEqual(docker.call_args.args[2],'a'*64)
        self.assertEqual(docker.call_args.args[-1],'b'*64)
        self.assertEqual(docker.call_args.kwargs['data'],prepared.content)

    def test_failed_apply_never_recaptures_or_retries(self):
        docker=Mock(side_effect=[SimpleNamespace(stdout='a'*64),SimpleNamespace(stdout='b'*64+'  /etc/postgresql/pg_hba.conf'),RuntimeError('Uncertain publication')])
        with self.assertRaisesRegex(RuntimeError,'Uncertain publication'):
            atomic_hba.replace(docker,'fixture','local all all trust\n')
        self.assertEqual(docker.call_count,3)

    def test_invalid_container_or_revision_refuses_preparation(self):
        for outputs in ([SimpleNamespace(stdout='bad')],[SimpleNamespace(stdout='a'*64),SimpleNamespace(stdout='bad')]):
            docker=Mock(side_effect=outputs)
            with self.assertRaises(RuntimeError):atomic_hba.prepare(docker,'fixture','local all all trust\n')
            self.assertEqual(docker.call_count,len(outputs))
