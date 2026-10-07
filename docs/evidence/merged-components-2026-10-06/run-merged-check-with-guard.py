import json
import pathlib
import subprocess
import sys

base = pathlib.Path(__file__).parent
out = pathlib.Path(sys.argv[1])
command = ['python3', '/home/sbarah/.codex/skills/checkpoint-workflow/scripts/workflow.py',
           'check', '--run', str(base / 'checkpoint-merged-components'),
           '--checkpoint', 'merged-components', '--check', 'merged-source',
           '--token', sys.argv[2]]
code = 1
guard = None
try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=650)
    (out / 'engine-check.stdout').write_text(result.stdout)
    (out / 'engine-check.stderr').write_text(result.stderr)
    code = result.returncode
finally:
    # This coordinator is outside the checkpoint command's process group.
    guard = subprocess.run(['python3', str(base / 'reconcile-merged-check.py'),
                            str(out / 'ledger.json')], capture_output=True, text=True, timeout=600)
    (out / 'guard.stdout').write_text(guard.stdout)
    (out / 'guard.stderr').write_text(guard.stderr)
    if guard.returncode:
        code = max(1, code)
print(json.dumps({'engine_returncode': code, 'guard_returncode': guard.returncode,
                  'evidence': str(out)}))
raise SystemExit(code)
