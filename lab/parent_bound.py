"""Linux exec launcher: terminate this child if its exact launching parent dies."""
import ctypes
import os
import signal
import sys


def main():
    if len(sys.argv)<3:raise SystemExit('Parent-bound launcher requires parent and command')
    expected=int(sys.argv[1])
    if expected<=1:raise SystemExit('Invalid parent identity')
    libc=ctypes.CDLL(None,use_errno=True)
    libc.prctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]
    libc.prctl.restype=ctypes.c_int
    if libc.prctl(1,signal.SIGTERM,0,0,0)!=0:raise SystemExit('Parent-death binding unavailable')
    # Parent could die before prctl was installed. Never exec an orphan command.
    if os.getppid()!=expected:raise SystemExit('Launching parent already exited')
    os.execvp(sys.argv[2],sys.argv[2:])


if __name__=='__main__':main()
