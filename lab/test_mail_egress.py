"""An environment's Auth gets a way out only while it has a mail configuration or an enabled OAuth provider.

Measured on this host: from a container on the runtime network, `getent hosts smtp.resend.com`
fails and the relay port is unreachable, because that network is created `--internal`. The same
probe on the outbound network succeeds. `durable_runtime.egress` is the only thing that puts
an environment's Auth on that network, and it takes it off again when neither is left.
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
            durable_runtime.egress(AUTH, True)
        self.assertEqual(self.docker, [('ensure',), ('network', 'connect', durable_runtime.EGRESS, AUTH)])

    def test_an_environment_without_mail_never_joins_it(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK])):
            durable_runtime.egress(AUTH, False)
        self.assertEqual(self.docker, [])

    def test_removing_the_mail_configuration_takes_the_network_away(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK, durable_runtime.EGRESS])):
            durable_runtime.egress(AUTH, False)
        self.assertEqual(self.docker, [('network', 'disconnect', '--force', durable_runtime.EGRESS, AUTH)])

    def test_a_configured_environment_already_on_it_is_left_alone(self):
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK, durable_runtime.EGRESS])):
            durable_runtime.egress(AUTH, True)
        self.assertEqual(self.docker, [])

    def test_a_missing_container_is_not_an_error(self):
        with patch.object(durable_runtime, 'inspect', return_value=None):
            durable_runtime.egress(AUTH, True)
        self.assertEqual(self.docker, [])

    def test_the_outbound_network_is_never_extended_to_a_missing_environment(self):
        """No mail configuration, no network: the internal-only posture is the default."""
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: self.container([durable_runtime.NETWORK])):
            durable_runtime.egress(AUTH, False)
        self.assertFalse([args for args in self.docker if args[:2] == ('network', 'connect')])


class ProviderEgressTests(unittest.TestCase):
    """Auth fetches an OAuth provider's discovery document itself (issue 22), so a provider needs the route too."""

    def test_an_enabled_provider_alone_wants_the_route(self):
        settings = {'providers': {'google': {'enabled': True}}}
        self.assertTrue(durable_runtime.egress_wanted(None, settings))

    def test_a_disabled_provider_does_not(self):
        settings = {'providers': {'google': {'enabled': False}, 'github': {'enabled': False}}}
        self.assertFalse(durable_runtime.egress_wanted(None, settings))

    def test_one_enabled_provider_among_disabled_ones_is_enough(self):
        settings = {'providers': {'google': {'enabled': False}, 'github': {'enabled': True}}}
        self.assertTrue(durable_runtime.egress_wanted(None, settings))

    def test_no_settings_and_no_mail_means_no_route(self):
        self.assertFalse(durable_runtime.egress_wanted(None, None))
        self.assertFalse(durable_runtime.egress_wanted(None, {'providers': {}}))
        self.assertFalse(durable_runtime.egress_wanted(None, {}))

    def test_mail_alone_still_wants_it(self):
        self.assertTrue(durable_runtime.egress_wanted({'host': 'smtp.example.com'}, None))

    def test_a_provider_only_environment_keeps_the_route_when_mail_is_removed(self):
        """Removing the mail file must not cut off an environment whose Google sign-in still needs it."""
        wanted = durable_runtime.egress_wanted(None, {'providers': {'google': {'enabled': True}}})
        docker = []
        item = {'Id': AUTH, 'Name': AUTH, 'NetworkSettings': {'Networks': {durable_runtime.NETWORK: {}, durable_runtime.EGRESS: {}}}}
        with patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kw: docker.append(args) or SimpleNamespace(stdout='', returncode=0)), \
                patch.object(durable_runtime, 'inspect', return_value=item):
            durable_runtime.egress(AUTH, wanted)
        self.assertEqual(docker, [])


if __name__ == '__main__':
    unittest.main()
