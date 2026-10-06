"""Inert admission boundary for caller unit tests; never host acceptance evidence."""
import os
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
