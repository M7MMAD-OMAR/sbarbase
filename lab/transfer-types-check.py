#!/usr/bin/env python3
"""Bounded control/UI type and build gate, not original-service transfer proof."""
from pathlib import Path
import shutil
import subprocess
import sys
from transfer_check_process import bounded_check

ROOT = Path(__file__).resolve().parents[1]


def main(types_only=False):
    bun = shutil.which('bun')
    if bun is None:
        print('Transfer type verification resource boundary unavailable', file=sys.stderr)
        return 1
    scripts = ('typecheck:control', 'typecheck:ui') if types_only else ('typecheck:control', 'typecheck:ui', 'build:ui')
    for script in scripts:
        try:
            result = bounded_check(ROOT, [bun, 'run', script], kind='types', memory='512M' if types_only else '768M', service_seconds=15 if types_only else 25)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            print('Transfer type verification did not complete', file=sys.stderr)
            return 1
        if result.returncode:
            print('Transfer type verification failed: ' + script, file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    if sys.argv[1:] not in ([], ['--types-only']):
        raise SystemExit('Unsupported transfer type arguments')
    raise SystemExit(main(types_only=sys.argv[1:] == ['--types-only']))
