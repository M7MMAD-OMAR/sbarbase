"""Translate Bun's real executed-test summary for the checkpoint evidence parser."""
import json
import re
import subprocess
import sys

result = subprocess.run(['bun', 'test', *sys.argv[1:]], capture_output=True, text=True)
output = result.stdout + result.stderr
passed = re.search(r'^\s*(\d+) pass\s*$', output, re.M)
failed = re.search(r'^\s*(\d+) fail\s*$', output, re.M)
skipped = re.search(r'^\s*(\d+) skip\s*$', output, re.M)
if not passed or not failed:
    raise SystemExit('Bun did not report an executed-test summary')
p, f, s = int(passed[1]), int(failed[1]), int(skipped[1]) if skipped else 0
print(json.dumps({'total': p + f + s, 'failed': f, 'skipped': s, 'output': output}))
raise SystemExit(result.returncode)
