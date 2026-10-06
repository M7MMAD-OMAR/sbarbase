#!/usr/bin/env python3
"""Run one retained lifecycle operation under installation worker ownership."""
import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--state-root', required=True)
    parser.add_argument('--checkout', required=True)
    args, _ = parser.parse_known_args()
    state = Path(args.state_root)
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        raise ValueError('Explicit private installation state required')
    descriptor = os.open(state / 'worker.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        environment = dict(os.environ, SBARBASE_LIFECYCLE_WORKER_LOCKED='1')
        return subprocess.run(['bun', str(Path(args.checkout) / 'lab/lifecycle-worker.ts'), *sys.argv[1:]],
                              env=environment, check=False).returncode
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    raise SystemExit(main())
