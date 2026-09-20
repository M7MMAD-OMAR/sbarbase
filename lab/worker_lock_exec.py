"""Validate and retain the inherited worker ownership descriptor before effects."""
import fcntl
import os
import sys


def main():
    if len(sys.argv)<3:
        raise SystemExit('Expected worker lock path and effect command')
    held,expected=os.fstat(3),os.stat(sys.argv[1])
    if (held.st_dev,held.st_ino)!=(expected.st_dev,expected.st_ino):
        raise SystemExit('Invalid effect ownership descriptor')
    fcntl.flock(3,fcntl.LOCK_EX|fcntl.LOCK_NB)
    os.set_inheritable(3,True)
    os.execvp(sys.argv[2],sys.argv[2:])


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Effect ownership unavailable') from None
