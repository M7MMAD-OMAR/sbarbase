"""Inert admission boundary for caller unit tests; never host acceptance evidence."""
import os
from contextlib import contextmanager
import unittest
from unittest.mock import patch
import docker_profile
import shutil


class IsolatedHostCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        admission = patch.object(docker_profile, 'validate', return_value='unit-fixture-daemon')
        admission.start()
        self.addCleanup(admission.stop)
        tools = patch.object(shutil, 'which', return_value='/usr/bin/unit-fixture-tool')
        tools.start()
        self.addCleanup(tools.stop)


@contextmanager
def admitted_host_fixture():
    """Scoped caller-unit admission; preserve PATH, fixture state and other environment."""
    profile = {'SBARBASE_DOCKER_PROFILE': 'local-v1', 'SBARBASE_CONTAINER': '',
               'SBARBASE_DOCKER_DATA_ROOT': '/var/lib/docker',
               'SBARBASE_DOCKER_SOCKET': '/var/run/docker.sock',
               'DOCKER_HOST': 'unix:///var/run/docker.sock',
               'DOCKER_CONTEXT': '', 'DOCKER_TLS': '', 'DOCKER_TLS_VERIFY': ''}
    with patch.dict(os.environ, profile, clear=False), \
            patch.object(docker_profile, 'validate', return_value='unit-fixture-daemon') as admission:
        yield admission
