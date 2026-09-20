"""Source-runtime integration of the owned, single-attempt HBA protocol.

Existing containers without an exact generation pin need explicit adoption.
This module never resets a registry or upgrades legacy writers automatically.

Every managed database container gets its own authority state: the source
database uses the installation state root, and a recovery-target database uses
<installation state>/targets/<prefix>, with its own worker/effect/operation
locks, generation pin, journals, attempts, completions and outcomes.
"""
import os
import re
import stat
import uuid
from pathlib import Path
import atomic_hba
import effect_receipt
import hba_apply
import hba_authority
import hba_generation
import hba_journal
import hba_ownership
import hba_settlement
import hba_startup
import hba_target

TARGETS='targets'
TARGET_PREFIX=re.compile(r'sbarbase-restore-[a-f0-9]{12}')


def absent(path):
    try:path.lstat()
    except FileNotFoundError:return True
    return False


def target_state(installation_state,prefix):
    """Private per-target authority state; never the installation root."""
    if not isinstance(prefix,str) or not TARGET_PREFIX.fullmatch(prefix):
        raise ValueError('Invalid recovery target prefix')
    return Path(installation_state)/TARGETS/prefix


class SourceHBA:
    def __init__(self,docker,state,name,owner,image,*,startup=None,operation_fd=None):
        self.docker=docker;self.state=state
        self.name=name;self.owner=owner;self.image=image
        hba_target.policy(name,owner,image)
        if (startup is None)==(operation_fd is None):raise ValueError('Exactly one HBA owner required')
        self.startup=startup
        self.descriptors=startup.descriptors if startup is not None else (3,4,operation_fd)
        self.target=None;self.runtime=None;self.used=False;self.fresh=None;self.expected_cid=None

    def locks(self):
        identities=[hba_ownership.require_lock(self.state,name,fd)
                    for name,fd in zip(hba_startup.NAMES,self.descriptors)]
        if len(self.descriptors)!=3 or len(set(identities))!=3:raise RuntimeError('Distinct HBA ownership required')

    def before_start(self,existing,volume_exists):
        if self.startup is None:raise RuntimeError('Startup HBA ownership required')
        self.startup.verify()
        if self.fresh is not None:raise RuntimeError('HBA startup preparation already attempted')
        self.fresh=existing is None
        if existing is None:
            if volume_exists or not absent(self.state/hba_generation.NAME):
                raise RuntimeError('Retained database state requires explicit HBA generation migration')
        else:
            target=hba_target.Target(existing['Id'],self.name,self.owner,self.image)
            pin=hba_generation.load(self.state)
            hba_generation.require(self.state,target,pin['generation'])
            if existing.get('Name')!='/'+self.name or existing.get('Image')!=self.image or existing.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')!=self.owner:
                raise RuntimeError('Existing database differs from configured HBA target')
            self.expected_cid=target.container_id

    def read_idle(self,target):
        snapshot=hba_generation.read_existing(self.docker,self.state,target=target)
        if any(item['state']=='active' for item in hba_authority.decode(snapshot.text,snapshot.generation)['operations'].values()):
            raise RuntimeError('Active HBA authority requires reconciliation')
        return snapshot

    def ready(self,launched_cid,*,created):
        if self.startup is None or self.fresh is None:raise RuntimeError('HBA startup preparation required')
        self.startup.verify()
        if type(created) is not bool or created!=self.fresh:
            raise RuntimeError('HBA initialization requires matching creation evidence')
        target=hba_target.capture(self.docker,self.name,self.owner,self.image)
        if target.container_id!=launched_cid or (not self.fresh and target.container_id!=self.expected_cid):
            raise RuntimeError('Database identity changed during startup')
        if self.fresh:self.startup.initialize(self.docker,target=target)
        else:self.read_idle(target)
        self.target=target

    def worker_preflight(self,runtime):
        if self.startup is not None:raise RuntimeError('Worker HBA ownership required')
        self.locks()
        effect_receipt.hba_preflight_identity(self.state,runtime)
        if not absent(self.state/hba_journal.NAME):raise RuntimeError('Pending HBA operation requires reconciliation')
        target=hba_target.capture(self.docker,self.name,self.owner,self.image)
        self.read_idle(target)
        self.target=target;self.runtime=runtime

    def publish(self,content):
        if self.used or self.target is None:raise RuntimeError('HBA writer unavailable or already attempted')
        self.used=True
        self.locks()
        if self.startup is not None:self.startup.verify()
        else:effect_receipt.hba_identity(self.state,self.runtime)
        snapshot=hba_generation.read_existing(self.docker,self.state,target=self.target)
        prepared=atomic_hba.prepare(self.docker,self.target.container_id,content)
        token=str(uuid.uuid4())
        if self.startup is not None:
            self.startup.begin(self.docker,snapshot,prepared,token,target=self.target)
        else:
            hba_ownership.begin_worker(self.docker,self.state,self.descriptors[2],self.runtime,snapshot,prepared,token,target=self.target)
        hba_apply.execute(self.docker,self.state,self.descriptors,target=self.target,startup=self.startup)
        return hba_settlement.complete_owned(self.docker,self.state,self.descriptors,target=self.target,startup=self.startup)


def prepare_target_state(installation_state,prefix):
    """Create and verify the private per-target state directory."""
    state=target_state(installation_state,prefix)
    root=Path(installation_state)
    if not root.exists():
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
    metadata=root.lstat()
    if not stat.S_ISDIR(metadata.st_mode):raise ValueError('Installation state root must be a directory')
    if metadata.st_uid!=os.getuid():raise ValueError('Installation state root must be owned by the caller')
    if stat.S_IMODE(metadata.st_mode)!=0o700:
        # Tighten the caller's own state root; its contents are private evidence.
        os.chmod(root,0o700)
        if stat.S_IMODE(root.lstat().st_mode)!=0o700:raise ValueError('Installation state root must be private')
    parent=state.parent
    if not parent.exists():
        parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(parent,0o700)
    metadata=parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode)!=0o700:
        raise ValueError('Target authority parent directory must be private')
    if not state.exists():
        state.mkdir(mode=0o700)
    metadata=state.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode)!=0o700:
        raise ValueError('Target authority state directory must be private')
    return state


class TargetHBA(SourceHBA):
    """The same owned protocol against a recovery target's own authority state."""

    def __init__(self,docker,installation_state,prefix,name,owner,image,*,startup=None,operation_fd=None):
        state=prepare_target_state(installation_state,prefix)
        super().__init__(docker,state,name,owner,image,startup=startup,operation_fd=operation_fd)

    def before_create(self,*,preexisting_volume):
        """Prepare a first-generation target that this run is about to create.

        The caller must have verified that no target resource existed immediately
        before. A preexisting volume is retained state: it requires explicit
        adoption of the existing container, never silent initialization here.
        """
        if self.startup is None:raise RuntimeError('Startup HBA ownership required')
        if type(preexisting_volume) is not bool:raise ValueError('Explicit volume creation evidence required')
        if preexisting_volume:raise RuntimeError('Existing target volume requires explicit adoption')
        if self.fresh is not None:raise RuntimeError('HBA startup preparation already attempted')
        if not absent(self.state/hba_journal.NAME):raise RuntimeError('Pending HBA operation requires reconciliation')
        self.startup.verify()
        self.fresh=True
        return self
