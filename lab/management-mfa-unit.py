#!/usr/bin/env python3
"""Run repository boundary tests with a bounded process group and counted evidence."""
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
result = subprocess.run(['systemd-run', '--user', '--quiet', '--wait', '--pipe',
                         '--unit=sb06-unit-' + uuid.uuid4().hex[:12],
                         '-p', 'MemoryMax=512M', '-p', 'CPUQuota=50%', '-p', 'TasksMax=128', '-p', 'RuntimeMaxSec=60',
                         '--working-directory=' + str(root), shutil.which('bun') or 'bun', 'test'],
                        capture_output=True, text=True, timeout=70)
output = result.stdout + result.stderr
passed = re.findall(r'^\s*(\d+) pass\s*$', output, re.MULTILINE)
failed = re.findall(r'^\s*(\d+) fail\s*$', output, re.MULTILINE)
skipped = re.findall(r'^\s*(\d+) skip\s*$', output, re.MULTILINE)
if len(passed) != 1 or len(failed) != 1:
    print(json.dumps({'total': 1, 'failed': 1, 'skipped': 0, 'error': 'Missing Bun test count summary'}))
    raise SystemExit(1)
counts = {'total': int(passed[0]) + int(failed[0]) + (int(skipped[0]) if skipped else 0),
          'failed': int(failed[0]), 'skipped': int(skipped[0]) if skipped else 0}
if result.returncode:
    # Diagnostics use only the synthetic unit fixtures, never native secrets.
    print(output, file=__import__('sys').stderr)
print(json.dumps(counts))
raise SystemExit(int(result.returncode != 0 or counts['failed'] > 0))
