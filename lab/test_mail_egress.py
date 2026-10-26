"""An environment's Auth reaches its relay only while that environment has a mail configuration.

Measured on this host: from a container on the runtime network, `getent hosts smtp.resend.com`
fails and the relay port is unreachable, because that network is created `--internal`. The same
probe on the outbound network succeeds. `durable_runtime.mail_egress` is the only thing that puts
an environment's Auth on that network, and it takes it off again when the mail file is removed.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import durable_runtime

AUTH = 'sbarbase-durable-e_' + 'a' * 24 + '-auth'


class MailEgressTests(unittest.TestCase):
    def setUp(self):
        self.docker = []
        for item in [patch.object(durable_runtime.lab, 'docker',
                                  side_effect=lambda *args, **kw: self.docker.append(args) or SimpleNamespace(stdout='', returncode=0)),
                     patch.object(durable_runtime, 'ensure_egress', side_effect=lambda: self.docker.append(('ensure',)))]:
            item.start()
            self.addCleanup(item.stop)

    def container(self, networks):
        return {'Id': AUTH, 'Name': AUTH, 'NetworkSettings': {'Networks': {name: {} for name in networks}}}

    def test_a_configured_environment_joins_the_outbound_network(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK])):
            durable_runtime.mail_egress(AUTH, True)
        self.assertEqual(self.docker, [('ensure',), ('network', 'connect', durable_runtime.EGRESS, AUTH)])

    def test_an_environment_without_mail_never_joins_it(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK])):
            durable_runtime.mail_egress(AUTH, False)
        self.assertEqual(self.docker, [])

    def test_removing_the_mail_configuration_takes_the_network_away(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK, durable_runtime.EGRESS])):
            durable_runtime.mail_egress(AUTH, False)
        self.assertEqual(self.docker, [('network', 'disconnect', '--force', durable_runtime.EGRESS, AUTH)])

    def test_a_configured_environment_already_on_it_is_left_alone(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK, durable_runtime.EGRESS])):
            durable_runtime.mail_egress(AUTH, True)
        self.assertEqual(self.docker, [])

    def test_a_missing_container_is_not_an_error(self):
        with patch.object(durable_runtime, 'inspect', return_value=None):
            durable_runtime.mail_egress(AUTH, True)
        self.assertEqual(self.docker, [])

    def test_the_outbound_network_is_never_extended_to_a_missing_environment(self):
        """No mail configuration, no network: the internal-only posture is the default."""
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK])):
            durable_runtime.mail_egress(AUTH, False)
        self.assertFalse([args for args in self.docker if args[:2] == ('network', 'connect')])


if __name__ == '__main__':
    unittest.main()
