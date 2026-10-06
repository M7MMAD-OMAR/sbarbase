#!/usr/bin/env python3
"""Bound the static checks used by the management MFA evidence packet."""
import argparse
from pathlib import Path
import shutil
import subprocess
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('check', choices=['control-types', 'ui-types', 'ui-build'])
args = parser.parse_args()
scripts = {'control-types': 'typecheck:control', 'ui-types': 'typecheck:ui', 'ui-build': 'build:ui'}
root = Path(__file__).resolve().parents[1]
result = subprocess.run(['systemd-run', '--user', '--quiet', '--collect', '--wait', '--pipe',
                         '--unit=sb06-static-' + uuid.uuid4().hex[:12], '-p', 'MemoryMax=512M',
                         '-p', 'CPUQuota=50%', '-p', 'TasksMax=128', '-p', 'RuntimeMaxSec=60',
                         '--working-directory=' + str(root), shutil.which('bun') or 'bun', 'run', scripts[args.check]],
                        timeout=70)
raise SystemExit(result.returncode)
