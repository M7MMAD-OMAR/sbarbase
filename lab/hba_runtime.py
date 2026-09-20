"""Source-runtime integration of the owned, single-attempt HBA protocol.

Existing containers without an exact generation pin need explicit adoption.
This module never resets a registry or upgrades legacy writers automatically.
"""
import uuid
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


def absent(path):
    try:path.lstat()
    except FileNotFoundError:return True
    return False


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
