"""Experimental startup ownership for HBA intent, without recovery or replay."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import uuid
import hba_journal as journal
import hba_ownership as ownership
import hba_target
import hba_generation


NAMES=('worker.lock','effect.lock','operation.lock')


def require_clear(state,migration=False):
    # Only a missing entry is clear. Permission and lookup errors stay fatal.
    for name in ('worker-effect.json',journal.NAME):
        try:(state/name).lstat()
        except FileNotFoundError:continue
        raise RuntimeError('Startup requires prior operation reconciliation')
    # A generation migration record, torn or whole, blocks every ordinary startup
    # and every repeated migration. Only the migration path may hold it.
    try:(state/hba_generation.MIGRATION).lstat()
    except FileNotFoundError:present=False
    else:present=True
    if migration and not present:
        raise RuntimeError('Generation migration ownership requires a migration record')
    if not migration and present:
        raise RuntimeError('Generation migration requires reconciliation')


class Startup:
    def __init__(self,state,descriptors,migration=False):
        self.state=state
        self.migration=migration
        self.descriptors=tuple(descriptors)
        if len(self.descriptors)!=3:raise ValueError('Exactly three startup descriptors required')
        self.process=os.getpid()
        self.identity={'kind':'startup','startup':str(uuid.uuid4())}
        self.active=True
        self.attempted=False
        self.initialization_attempted=False

    def verify(self):
        if not self.active or os.getpid()!=self.process:raise RuntimeError('Startup ownership context expired')
        if len(self.descriptors)!=3:raise ValueError('Exactly three startup descriptors required')
        locks=[ownership.require_lock(self.state,name,descriptor) for name,descriptor in zip(NAMES,self.descriptors)]
        if len(set(locks))!=3:raise RuntimeError('Startup ownership locks must be distinct')
        require_clear(self.state,self.migration)

    def initialize(self,docker,*,target):
        self.verify()
        if self.initialization_attempted or self.attempted:raise RuntimeError('Startup initialization already attempted')
        self.initialization_attempted=True
        return hba_generation.initialize(docker,self.state,target=target)

    def begin(self,docker,snapshot,prepared,token,*,target):
        self.verify()
        if self.attempted:raise RuntimeError('Startup intent already attempted; reconciliation required')
        self.attempted=True
        hba_generation.require(self.state,target,snapshot.generation)
        hba_target.require(docker,target,snapshot,prepared)
        return journal.begin(docker,self.state/journal.NAME,snapshot,prepared,token,self.identity)


@contextmanager
def acquire(state,*,worker_fd=None,migration=False):
    """Always obtain fresh effect/operation ownership; optionally share supervisor worker lock.

    Caller must have exclusive control of its private state directory. Inherited
    worker ownership is not evidence that all prior effects ended: the separate
    fresh effect lock enforces exclusion against surviving workers/guardians.
    `migration` is the one context that may hold a generation migration record.
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
        require_clear(state,migration)
        lease=Startup(state,descriptors,migration)
        yield lease
    finally:
        if lease is not None:lease.active=False
        # Closing a duplicate must not explicitly unlock the supervisor's shared
        # open file description. Fresh descriptions release on final close.
        for descriptor in reversed(descriptors):os.close(descriptor)
