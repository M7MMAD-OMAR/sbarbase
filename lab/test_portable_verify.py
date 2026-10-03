"""Acceptance boundaries of the portable source verification harness."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("portable_unit_checks", ROOT / "deploy/verify/unittest_checks.py")
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


class PortableVerificationTests(unittest.TestCase):
    def acceptance(self, count, skipped, successful=True):
        result = SimpleNamespace(testsRun=count, skipped=skipped, wasSuccessful=lambda: successful)
        with patch.object(CHECKS.unittest.defaultTestLoader, "discover"), \
             patch.object(CHECKS.unittest, "TextTestRunner") as runner, \
             patch("sys.stderr", new=io.StringIO()):
            runner.return_value.run.return_value = result
            return CHECKS.main()

    def test_skipped_tests_fail_even_when_unittest_reports_success(self):
        self.assertEqual(self.acceptance(2, [("required-test", "missing tool")]), 1)

    def test_empty_discovery_fails(self):
        self.assertEqual(self.acceptance(0, []), 1)

    def test_failed_suite_fails(self):
        self.assertEqual(self.acceptance(2, [], successful=False), 1)

    def test_wrapper_preserves_failed_container_exit_and_copies_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            calls = base / "calls"
            fake = base / "docker"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$VERIFY_CALLS\"\n"
                            "if [ \"$1\" = create ]; then printf 'created-container-id\\n'; fi\n"
                            "if [ \"$1\" = start ]; then exit 17; fi\nexit 0\n")
            fake.chmod(0o755)
            result = subprocess.run(["sh", str(ROOT / "deploy/verify/run.sh"), str(base / "output with spaces")],
                                    cwd=directory, capture_output=True, text=True,
                                    env={**os.environ, "PATH": directory + ":" + os.environ["PATH"],
                                         "VERIFY_CALLS": str(calls)})
            self.assertEqual(result.returncode, 17, result.stderr)
            commands = calls.read_text().splitlines()
            self.assertTrue(any(command.startswith("cp ") for command in commands))
            create = next(command for command in commands if command.startswith("create "))
            self.assertIn("--init", create)
            self.assertIn("--network none", create)
            self.assertNotIn("--mount", create)
            self.assertNotIn("--volume", create)
            self.assertNotIn("docker.sock", create)
            self.assertIn("rm -f created-container-id", commands)


if __name__ == "__main__":
    unittest.main()
