"""Fresh effect ownership independent of the supervisor's shared worker flock."""
import fcntl
import os
import time


def acquire(state,timeout=5):
    fd=os.open(state/'effect.lock',os.O_CREAT|os.O_RDWR,0o600)
    deadline=time.monotonic()+timeout
    try:
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise RuntimeError('Previous provisioning effect still owns the lease') from None
                time.sleep(.02)
        os.set_inheritable(fd,True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def export_descriptors(worker,lease):
    """Keep source FDs away from child mapping slots 3 and 4.

    Installed Bun applies fd mappings in order; swapping 4 and 3 aliases them.
    The caller closes its old descriptors after these duplicates are ready.
    """
    copies=[]
    try:
        for fd in (worker,lease):
            duplicate=fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,10)
            copies.append(duplicate);os.set_inheritable(duplicate,True)
        return tuple(copies)
    except BaseException:
        for fd in copies:os.close(fd)
        raise
