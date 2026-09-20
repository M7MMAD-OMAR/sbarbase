"""Compatibility entry point for the isolated partial-database crash probe."""
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    probe=Path(__file__).resolve().with_name('partial-database-crash-check.py')
    raise SystemExit(subprocess.run(['/usr/bin/python3',str(probe),*sys.argv[1:]]).returncode)
