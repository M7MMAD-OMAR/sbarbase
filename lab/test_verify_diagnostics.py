"""Reject successful child commands that emit build diagnostics."""
import importlib.util
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_diagnostic_runner', ROOT/'deploy/verify/run_checks.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class SourceDiagnosticTests(unittest.TestCase):
    def test_real_zero_exit_warning_fails_and_later_command_still_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root/'evidence'
            stages = (
                ('ui-build', [sys.executable, '-c', 'print("\\x1b]8;;https://example.invalid\\x07warning: visible linked warning\\x1b]8;;\\x07")']),
                ('python-compile', [sys.executable, '-c', 'print("later stage executed")']),
            )
            cwd = Path.cwd()
            try:
                with patch.object(RUNNER, 'ROOT', root), patch.object(RUNNER, 'STAGES', stages), patch.object(RUNNER, 'VERSIONS', ()), \
                     patch.object(RUNNER, 'source_digest', return_value='fixture-public-source'):
                    status = RUNNER.main(evidence_path=evidence)
            finally:
                os.chdir(cwd)
            import json
            report = json.loads((evidence/'report.json').read_text())
            self.assertEqual(status, 1)
            self.assertFalse(report['passed'])
            self.assertEqual(report['stages'][0]['exit_code'], 0)
            self.assertEqual(report['stages'][0]['status'], 'failed-diagnostics')
            self.assertEqual(report['stages'][0]['diagnostics'][0]['line'], 1)
            self.assertIn('\x1b]8;;https://example.invalid\x07', (evidence/'ui-build.log').read_text())
            self.assertEqual(report['stages'][0]['diagnostics'][0]['message'], 'warning: visible linked warning')
            self.assertEqual(report['stages'][1]['status'], 'passed')
            self.assertIn('later stage executed', (evidence/'python-compile.log').read_text())

    def test_known_formats_and_ansi_cannot_pass(self):
        for output in ('(!) Some chunks are larger than 500 kB',
                       '\x1b[33m[UNRESOLVED_IMPORT] bad import\x1b[0m',
                       'WARNING: compiler diagnostic', 'error: compiler diagnostic',
                       'module.py:4: SyntaxWarning: invalid escape sequence'):
            with self.subTest(output=output):
                self.assertTrue(RUNNER.stage_diagnostics('ui-build', output))

    def test_terminal_links_titles_and_c1_controls_cannot_hide_diagnostics(self):
        for output in ('\x1b]8;;https://example.invalid\x07warning: visible linked warning\x1b]8;;\x07',
                       '\x1b]0;build\x1b\\[MODULE_LEVEL_DIRECTIVE] visible warning',
                       '\x9b33mwarning: C1 formatted warning\x9b0m',
                       '\x1b(Bwarning: selected character set',
                       '\x1b]unclosed title warning: refuse unknown control',
                       '\x08warning: unexpected terminal backspace'):
            with self.subTest(output=output):
                self.assertTrue(RUNNER.stage_diagnostics('ui-build', output))

    def test_multiline_invisible_payload_preserves_original_log_line(self):
        output='\x1b]0;invisible title\ncontinuation\x07warning: visible warning\n'
        diagnostics=RUNNER.stage_diagnostics('ui-build', output)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]['line'], 2)
        self.assertEqual(diagnostics[0]['message'], 'warning: visible warning')

    def test_expected_unit_output_is_not_misclassified_as_build_diagnostics(self):
        output = 'test_warns ... ok\nwarning: intentionally simulated\nerror: refused as expected\n'
        self.assertEqual(RUNNER.stage_diagnostics('python-unit', output), [])
        self.assertEqual(RUNNER.stage_diagnostics('bun-test', output), [])

    def test_clean_output_has_no_diagnostics(self):
        self.assertEqual(RUNNER.stage_diagnostics('ui-build', '✓ built in 143ms\n544 modules transformed\n'), [])
