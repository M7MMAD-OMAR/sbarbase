#!/usr/bin/env python3
"""Execute focused transfer units only after the parent admits a verification slot."""
import json
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from transfer_check_process import bounded_check, retain_source_output

ROOT = Path(__file__).resolve().parents[1]


def python_units():
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'lab'), pattern='test_transfer_journal.py')
    diagnostics = io.StringIO()
    result = unittest.TextTestRunner(stream=diagnostics, verbosity=2).run(suite)
    retain_source_output('python-units', '', diagnostics.getvalue())
    failed = set()
    for test, _diagnostic in result.failures + result.errors:
        # A failed subtest counts its actual parent once. Do not publish private
        # synthetic credentials from assertion diffs or exception diagnostics.
        failed.add(getattr(test, 'test_case', test).id())
    for identity in sorted(failed):
        print('Transfer Python unit failed: ' + identity, file=sys.stderr)
    counts = {'total': result.testsRun, 'failed': len(failed),
              'skipped': len(result.skipped)}
    print(json.dumps(counts))
    return int(not result.wasSuccessful() or bool(result.skipped))


def bounded(command):
    return bounded_check(ROOT, command, kind='unit', memory='512M', service_seconds=45)


def main():
    counts = {'total': 0, 'failed': 0, 'skipped': 0}
    try:
        python = bounded([sys.executable, str(Path(__file__).resolve()), '--python-only'])
        observed = json.loads(python.stdout)
        if (type(observed) is not dict or set(observed) != set(counts)
                or any(type(value) is not int or value < 0 for value in observed.values())
                or observed['failed'] + observed['skipped'] > observed['total']):
            raise ValueError('Python test counts unavailable')
        counts.update(observed)
        if counts['total'] != 67:
            raise ValueError('Prepared Python source cases missing')
        if python.returncode or counts['failed'] or counts['skipped']:
            print('Transfer Python units failed or skipped', file=sys.stderr)
            print(json.dumps(counts))
            return 1
        bun = shutil.which('bun')
        if bun is None:
            raise RuntimeError('Bun unavailable')
        result = bounded([bun, 'test', 'tests/transfer-contract.test.ts', 'tests/transfer-store.test.ts'])
        output = result.stdout + result.stderr
        passed = re.findall(r'^\s*(\d+) pass\s*$', output, re.MULTILINE)
        failed = re.findall(r'^\s*(\d+) fail\s*$', output, re.MULTILINE)
        skipped = re.findall(r'^\s*(\d+) skip\s*$', output, re.MULTILINE)
        if len(passed) != 1 or len(failed) != 1 or len(skipped) > 1:
            raise ValueError('Bun test count summary unavailable')
        failures, skips = int(failed[0]), int(skipped[0]) if skipped else 0
        counts['total'] += int(passed[0]) + failures + skips
        counts['failed'] += failures
        counts['skipped'] += skips
        if int(passed[0]) + failures + skips != 36:
            raise ValueError('Prepared Bun source cases missing')
        if result.returncode or failures or skips:
            print('Transfer Bun units failed or skipped', file=sys.stderr)
            print(json.dumps(counts))
            return 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        # Do not invent test counts or admit an unbounded fallback process.
        print('Transfer unit execution or counted evidence unavailable', file=sys.stderr)
        print(json.dumps(counts))
        return 1
    print(json.dumps(counts))
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--python-only']:
        raise SystemExit(python_units())
    if sys.argv[1:]:
        raise SystemExit('Unsupported transfer unit arguments')
    raise SystemExit(main())
