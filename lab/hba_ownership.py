"""Experimental worker entry gate for HBA intent. Runtime wiring is pending.

Caller retains ownership descriptors throughout dispatch and all later effects.
This does not authorize startup or settlement of an interrupted journal.
"""
import fcntl
import os
import stat
import effect_receipt
import hba_journal as journal


def require_lock(state,name,descriptor):
    if type(descriptor) is not int or descriptor<3:raise ValueError('HBA ownership descriptor required')
    path=state/name
    expected=path.lstat();held=os.fstat(descriptor)
    if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(held.st_mode)
            or expected.st_uid!=os.getuid() or held.st_uid!=os.getuid()
            or (expected.st_dev,expected.st_ino)!=(held.st_dev,held.st_ino)):
        raise RuntimeError('HBA ownership inode mismatch')
    # Matching an inode alone does not prove exclusive ownership. This obtains
    # or retains the actual exclusive lock and refuses a competing open file.
    fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    current=path.lstat()
    if (current.st_dev,current.st_ino)!=(held.st_dev,held.st_ino):
        raise RuntimeError('HBA ownership path changed')
    return held.st_dev,held.st_ino


def begin_worker(docker,state,operation_fd,runtime,snapshot,prepared,token):
    locks=(require_lock(state,'operation.lock',operation_fd),
           require_lock(state,'worker.lock',3),require_lock(state,'effect.lock',4))
    if len(set(locks))!=3:raise RuntimeError('HBA ownership locks must be distinct')
    runtime,receipt,claim,attempt=effect_receipt.hba_identity(state,runtime)
    identity={'kind':'worker','runtime':runtime,'receipt':receipt,'claim':claim,'attempt':attempt}
    return journal.begin(docker,state/journal.NAME,snapshot,prepared,token,identity)
