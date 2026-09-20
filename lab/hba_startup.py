"""Experimental startup ownership for HBA intent, without recovery or replay."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import uuid
import hba_journal as journal
import hba_ownership as ownership
import hba_target


NAMES=('worker.lock','effect.lock','operation.lock')


def require_clear(state):
    # Only a missing entry is clear. Permission and lookup errors stay fatal.
    for name in ('worker-effect.json',journal.NAME):
        try:(state/name).lstat()
        except FileNotFoundError:continue
        raise RuntimeError('Startup requires prior operation reconciliation')


class Startup:
    def __init__(self,state,descriptors):
        self.state=state
        self.descriptors=tuple(descriptors)
        if len(self.descriptors)!=3:raise ValueError('Exactly three startup descriptors required')
        self.process=os.getpid()
        self.identity={'kind':'startup','startup':str(uuid.uuid4())}
        self.active=True
        self.attempted=False

    def begin(self,docker,snapshot,prepared,token,*,target):
        if not self.active or os.getpid()!=self.process:raise RuntimeError('Startup ownership context expired')
        if len(self.descriptors)!=3:raise ValueError('Exactly three startup descriptors required')
        if self.attempted:raise RuntimeError('Startup intent already attempted; reconciliation required')
        self.attempted=True
        locks=[ownership.require_lock(self.state,name,descriptor) for name,descriptor in zip(NAMES,self.descriptors)]
        if len(set(locks))!=3:raise RuntimeError('Startup ownership locks must be distinct')
        require_clear(self.state)
        hba_target.require(docker,target,snapshot,prepared)
        return journal.begin(docker,self.state/journal.NAME,snapshot,prepared,token,self.identity)


@contextmanager
def acquire(state,*,worker_fd=None):
    """Always obtain fresh effect/operation ownership; optionally share supervisor worker lock.

    Caller must have exclusive control of its private state directory. Inherited
    worker ownership is not evidence that all prior effects ended: the separate
    fresh effect lock enforces exclusion against surviving workers/guardians.
    """
    state=Path(state)
    descriptors=[];lease=None
    try:
        for name in NAMES:
            if name=='worker.lock' and worker_fd is not None:
                ownership.require_lock(state,name,worker_fd)
                descriptor=fcntl.fcntl(worker_fd,fcntl.F_DUPFD_CLOEXEC,10)
            else:
                descriptor=os.open(state/name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            descriptors.append(descriptor)
            ownership.require_lock(state,name,descriptor)
        identities=[(os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in descriptors]
        if len(set(identities))!=3:raise RuntimeError('Startup ownership locks must be distinct')
        require_clear(state)
        lease=Startup(state,descriptors)
        yield lease
    finally:
        if lease is not None:lease.active=False
        # Closing a duplicate must not explicitly unlock the supervisor's shared
        # open file description. Fresh descriptions release on final close.
        for descriptor in reversed(descriptors):os.close(descriptor)
