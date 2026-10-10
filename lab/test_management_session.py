import os
from pathlib import Path
import unittest
from unittest.mock import patch

from management_session import management_session_seconds, DEFAULT_SECONDS, MIN_SECONDS, MAX_SECONDS


class ManagementSessionSecondsTests(unittest.TestCase):
    def test_default_is_twelve_hours_without_the_variable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SBARBASE_MANAGEMENT_SESSION_SECONDS', None)
            self.assertEqual(management_session_seconds(), 43200)
        self.assertEqual((DEFAULT_SECONDS, MIN_SECONDS, MAX_SECONDS), (43200, 3600, 2592000))

    def test_values_in_range_are_used_including_both_ends(self):
        for value, seconds in (('3600', 3600), ('86400', 86400), ('2592000', 2592000)):
            with self.subTest(value=value):
                self.assertEqual(management_session_seconds(value), seconds)

    def test_malformed_or_out_of_range_falls_back_to_the_default(self):
        for value in ('', ' ', 'abc', '1e5', '-86400', '3599', '2592001', '99999999', ' 86400', '86400 ', '86400.5', '0x10000', '١٢٣٤٥٦٧'):
            with self.subTest(value=value):
                self.assertEqual(management_session_seconds(value), 43200)

    def test_environment_variable_is_read_on_each_call(self):
        with patch.dict(os.environ, {'SBARBASE_MANAGEMENT_SESSION_SECONDS': '7200'}):
            self.assertEqual(management_session_seconds(), 7200)
        with patch.dict(os.environ, {'SBARBASE_MANAGEMENT_SESSION_SECONDS': 'nonsense'}):
            self.assertEqual(management_session_seconds(), 43200)

    def test_lifecycle_authority_has_no_private_copy_of_the_cap(self):
        source = (Path(__file__).parent / 'management_access.py').read_text()
        self.assertNotIn('43200', source)
        self.assertEqual(source.count('management_session_seconds()'), 1)

    def test_control_plane_has_no_private_copy_of_the_cap(self):
        root = Path(__file__).parent.parent / 'src' / 'control'
        for path in sorted(root.glob('*.ts')):
            if path.name != 'management-session.ts':
                with self.subTest(path=path.name):
                    self.assertNotIn('43200', path.read_text())

    def test_compose_passes_the_variable_to_the_container(self):
        compose = (Path(__file__).parent.parent / 'compose.yaml').read_text()
        self.assertIn('SBARBASE_MANAGEMENT_SESSION_SECONDS: ${SBARBASE_MANAGEMENT_SESSION_SECONDS:-}', compose)

    def test_launchers_validate_and_forward_the_variable(self):
        for name in ('compose.sh', 'host-preflight.sh'):
            text = (Path(__file__).parent.parent / 'deploy' / name).read_text()
            with self.subTest(script=name):
                self.assertIn('scalar SBARBASE_MANAGEMENT_SESSION_SECONDS "${SBARBASE_MANAGEMENT_SESSION_SECONDS-}"', text)
        compose = (Path(__file__).parent.parent / 'deploy' / 'compose.sh').read_text()
        self.assertIn('SBARBASE_MANAGEMENT_SESSION_SECONDS="${SBARBASE_MANAGEMENT_SESSION_SECONDS-}"', compose)


if __name__ == '__main__':
    unittest.main()
