#!/usr/bin/env python3
"""Count fixture-only current-history cases under the admitted source boundary."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from transfer_check_process import bounded_check

ROOT = Path(__file__).resolve().parents[1]


def main():
    counts = {'total': 0, 'failed': 0, 'skipped': 0}
    try:
        bun = shutil.which('bun')
        if bun is None:
            raise RuntimeError('Bun unavailable')
        result = bounded_check(ROOT, [bun, 'test', 'tests/transfer-history.test.ts'],
                               kind='history', memory='512M', service_seconds=20)
        output = result.stdout + result.stderr
        passed = re.findall(r'^\s*(\d+) pass\s*$', output, re.MULTILINE)
        failed = re.findall(r'^\s*(\d+) fail\s*$', output, re.MULTILINE)
        skipped = re.findall(r'^\s*(\d+) skip\s*$', output, re.MULTILINE)
        if len(passed) != 1 or len(failed) != 1 or len(skipped) > 1:
            raise ValueError('Current history counts unavailable')
        counts = {'total': int(passed[0]) + int(failed[0]) + (int(skipped[0]) if skipped else 0),
                  'failed': int(failed[0]), 'skipped': int(skipped[0]) if skipped else 0}
        print(json.dumps(counts))
        return int(bool(result.returncode or counts['failed'] or counts['skipped'] or counts['total'] < 20))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        print('Current history source verification unavailable', file=sys.stderr)
        print(json.dumps(counts))
        return 1


if __name__ == '__main__':
    if sys.argv[1:]:
        raise SystemExit('Unsupported current history arguments')
    raise SystemExit(main())
