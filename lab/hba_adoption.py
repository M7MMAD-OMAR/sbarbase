"""Durable legacy source adoption, per HBA-LEGACY-ADOPTION-DESIGN.md.

Adopting the same container ID assumes old host clients and queued Docker
requests are quiesced; stopped containers and locks do not prove that. The
published intent is permission to adopt, never to regenerate identity. The
actual HBA rules are preserved (fresh revision marker only), so stale prepared
CAS requests lose, but raw legacy writers are not fenced by this operation.
"""
import json
import os
import stat
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
import atomic_hba
import effect_receipt
import hba_apply
import hba_authority as authority
import hba_generation
import hba_journal as journal
import hba_settlement
import hba_startup
import hba_target

NAME='hba-adoption.json'
CHECKPOINTS='hba-adoption-checkpoints'
LIMIT=journal.MAX_BYTES
HBA_PATH='/etc/postgresql/pg_hba.conf'
PGDATA='/var/lib/postgresql/data'
PHASES=('database-started','generation-initialized','hba-completed','source-stopped','completed')
# Container-side authority marker, exactly as the INIT/APPLY scripts test it.
BACKEND_MARKER='/etc/postgresql/.sbarbase-hba-authority-initialized'
BACKEND_REGISTRY='/etc/postgresql/.sbarbase-hba-authority.json'


def validate(record):
    if not isinstance(record,dict) or set(record)!={'version','adoption','generation','target','volume','mounts','initial_state'} \
            or type(record['version']) is not int or record['version']!=1:
        raise ValueError('Invalid HBA adoption intent shape')
    authority.exact(record['adoption'],authority.UUID);authority.exact(record['generation'],authority.UUID)
    target=record['target']
    if not isinstance(target,dict) or set(target)!={'container_id','name','owner','image'}:raise ValueError('Invalid adoption target')
    authority.exact(target['container_id'],authority.HEX)
    hba_target.policy(target['name'],target['owner'],target['image'])
    if not isinstance(record['volume'],str):raise ValueError('Adoption pgdata volume required')
    if record['initial_state']!='stopped':raise ValueError('Adoption requires initially stopped inventory')
    if not isinstance(record['mounts'],list) or not record['mounts']:raise ValueError('Adoption mount identity required')
    seen=set()
    for mount in record['mounts']:
        if not isinstance(mount,dict) or set(mount)!={'type','name','source','destination','mode'}:raise ValueError('Invalid adoption mount entry')
        if mount['destination'] in seen:raise ValueError('Duplicate adoption mount destination')
        seen.add(mount['destination'])
    if not any(m['destination']==PGDATA for m in record['mounts']):raise ValueError('Adoption pgdata mount missing')
    return record


def _mounts(info):
    result=[]
    for mount in sorted(info.get('Mounts') or [],key=lambda item:item.get('Destination','')):
        result.append({'type':mount.get('Type'),'name':mount.get('Name',''),'source':mount.get('Source',''),
                       'destination':mount.get('Destination'),'mode':str(mount.get('Mode',''))})
    # --tmpfs mounts appear only in HostConfig.Tmpfs.
    seen={m['destination'] for m in result}
    for destination,spec in sorted((info.get('HostConfig',{}) or {}).get('Tmpfs',{}).items()):
        if destination in seen:continue
        result.append({'type':'tmpfs','name':'','source':'','destination':destination,'mode':str(spec)})
    return sorted(result,key=lambda m:m['destination'])


def _volume(mounts):
    pgdata=next(m for m in mounts if m['destination']==PGDATA)
    return pgdata['name'] or pgdata['source']


def require_mounts(intent,info):
    """The captured pgdata mount identity must still describe this container."""
    mounts=_mounts(info)
    if mounts!=intent['mounts'] or _volume(mounts)!=intent['volume']:
        raise RuntimeError('Source mount identity changed since adoption capture')
    return mounts


def inspect_exact(docker,reference):
    result=json.loads(docker('inspect',reference).stdout)
    if not isinstance(result,list) or len(result)!=1 or not isinstance(result[0],dict):raise RuntimeError('Source inspection unavailable')
    return result[0]


def source_identity(docker,target):
    """Verify the exact configured identity of the inspected source container."""
    info=inspect_exact(docker,target.container_id)
    if (info.get('Name')!='/'+target.name or info.get('Image')!=target.image
            or info.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')!=target.owner):
        raise RuntimeError('Source identity changed')
    authority.exact(info.get('Id'),authority.HEX)
    return info


def source_running(docker,target):
    return bool(source_identity(docker,target).get('State',{}).get('Running'))


def stopped_identity(docker,target):
    info=source_identity(docker,target)
    if info.get('State',{}).get('Running'):raise RuntimeError('Source container is not stopped')
    return info


def publish_intent(docker,state,*,name,owner,image):
    """Exclusively publish the adoption intent; any existing or torn intent blocks."""
    hba_target.policy(name,owner,image)
    info=inspect_exact(docker,name)
    if (info.get('Name')!='/'+name or info.get('Image')!=image
            or info.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')!=owner):
        raise RuntimeError('Stopped source identity changed')
    if info.get('State',{}).get('Running'):raise RuntimeError('Source container is not stopped')
    authority.exact(info.get('Id'),authority.HEX)
    target=hba_target.Target(info['Id'],name,owner,image)
    mounts=_mounts(info)
    if not any(m['destination']==PGDATA for m in mounts):raise RuntimeError('Adoption pgdata mount missing')
    record=validate({'version':1,'adoption':str(uuid.uuid4()),'generation':str(uuid.uuid4()),
                     'target':asdict(target),'volume':_volume(mounts),
                     'mounts':mounts,'initial_state':'stopped'})
    state=Path(state)
    text=authority.encode(record)
    if len(text.encode())>LIMIT:raise ValueError('HBA adoption intent too large')
    descriptor=os.open(state/NAME,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:
        output.write(text);output.flush();os.fsync(output.fileno())
    effect_receipt.sync_directory(state)
    if load(state)!=record:raise RuntimeError('HBA adoption intent changed before use')
    return record


def load(state):
    path=Path(state)/NAME
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'r',encoding='utf-8',newline='') as source:
        metadata=os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_size>LIMIT:
            raise ValueError('HBA adoption intent must be a private owned regular file')
        text=source.read(LIMIT+1)
        if len(text.encode())>LIMIT:raise ValueError('HBA adoption intent too large')
    envelope=json.loads(text,object_pairs_hook=authority.unique_object)
    if not isinstance(envelope,dict) or set(envelope)!={'record','checksum'}:raise ValueError('Invalid adoption envelope')
    if envelope['checksum']!=authority.digest(authority.canonical(envelope['record'])):raise ValueError('Adoption checksum mismatch')
    return validate(envelope['record'])


def _phase(state,phase):
    if phase not in PHASES:raise ValueError('Unknown adoption checkpoint phase')
    return Path(state)/CHECKPOINTS/(phase+'.json')


def _present(path):
    """Only a missing entry is absent; denied or unreadable state stays fatal."""
    try:os.lstat(path)
    except FileNotFoundError:return False
    return True


def _done(state,phase):
    return _present(_phase(state,phase))


def read_checkpoint(state,phase):
    """Strict reader: private owned regular file, no symlink, envelope checksum."""
    path=_phase(state,phase)
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'r',encoding='utf-8',newline='') as source:
        metadata=os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_size>LIMIT:
            raise ValueError('Adoption checkpoint must be a private owned regular file')
        text=source.read(LIMIT+1)
        if len(text.encode())>LIMIT:raise ValueError('Adoption checkpoint too large')
    envelope=json.loads(text,object_pairs_hook=authority.unique_object)
    if not isinstance(envelope,dict) or set(envelope)!={'record','checksum'}:raise ValueError('Invalid adoption checkpoint envelope')
    if envelope['checksum']!=authority.digest(authority.canonical(envelope['record'])):raise ValueError('Adoption checkpoint checksum mismatch')
    record=envelope['record']
    if not isinstance(record,dict) or record.get('phase')!=phase:raise ValueError('Adoption checkpoint phase mismatch')
    return record


def checkpoint(state,phase,payload):
    """Publish one immutable checkpoint; an existing phase is re-bound, never rewritten."""
    path=_phase(state,phase)
    record={'version':1,'phase':phase,**payload}
    if _present(path):
        stored=read_checkpoint(state,phase)
        # A resumed phase may observe a different mode; identity must still match.
        if (authority.canonical({k:v for k,v in stored.items() if k!='mode'})
                !=authority.canonical({k:v for k,v in record.items() if k!='mode'})):
            raise RuntimeError('Conflicting adoption checkpoint')
        return stored
    parent=path.parent
    if not _present(parent):
        os.mkdir(parent,0o700)
        effect_receipt.sync_directory(Path(state))
    metadata=os.lstat(parent)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode)!=0o700 or metadata.st_uid!=os.getuid():
        raise ValueError('Adoption checkpoint directory is not private')
    text=authority.encode(record)
    if len(text.encode())>LIMIT:raise ValueError('Adoption checkpoint too large')
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:
        output.write(text);output.flush();os.fsync(output.fileno())
    effect_receipt.sync_directory(parent)
    return record


def _bind(intent,base):
    return {'adoption':intent['adoption'],'intent':authority.digest(authority.canonical(intent)),**base}


def _wait_ready(docker,cid):
    """Wait for stable SQL readiness: the entrypoint restarts its temp server
    during re-initialization, so a single success is not enough."""
    deadline=time.monotonic()+120;stable=0
    while True:
        probe=docker('exec',cid,'pg_isready','-U','supabase_admin',check=False)
        if probe.returncode==0:
            try:
                hba_apply.sql(docker,cid,'SELECT 1;')
                stable+=1
                if stable>=5:return
            except subprocess.CalledProcessError:stable=0
        else:stable=0
        if time.monotonic()>deadline:raise RuntimeError('Started source readiness deadline')
        time.sleep(1)


def backend_initialized(docker,cid):
    """Observe the container-side authority marker without mutating anything."""
    script=(f'[ -d {BACKEND_MARKER} ] && [ ! -L {BACKEND_MARKER} ] && [ -f {BACKEND_REGISTRY} ] '
            f'&& [ ! -L {BACKEND_REGISTRY} ] && printf yes || printf no')
    observed=docker('exec',cid,'sh','-c',script).stdout.strip()
    if observed not in ('yes','no'):raise RuntimeError('Backend authority observation unavailable')
    return observed=='yes'


def execute(docker,state,*,target):
    """Adopt the captured source under fresh ownership; resume from durable checkpoints."""
    state=Path(state)
    if _done(state,'completed'):raise RuntimeError('Adoption already completed')
    intent=load(state)
    if intent['target']!=asdict(target):raise RuntimeError('Adoption intent targets a different database')
    pin_path=state/hba_generation.NAME
    pinned=None
    if _present(pin_path):
        pinned=hba_generation.load(state)
        if pinned['target']!=asdict(target) or pinned['generation']!=intent['generation']:
            raise RuntimeError('Preexisting generation pin conflicts with adoption intent')
    with hba_startup.acquire(state) as lease:
        # Pending journals/worker receipts are refused by the startup gate.
        if not _done(state,'source-stopped'):
            if not _done(state,'database-started'):
                if source_running(docker,target):
                    require_mounts(intent,source_identity(docker,target));mode='observed-running'
                else:
                    require_mounts(intent,stopped_identity(docker,target));mode='stopped-start'
                docker('start',target.container_id)
                started=hba_target.observed(docker,target.container_id,target.name,target.owner,target.image)
                if started!=target.container_id:raise RuntimeError('Started source identity changed')
                checkpoint(state,'database-started',_bind(intent,{'mode':mode}))
            _wait_ready(docker,target.container_id)
            if not _done(state,'hba-completed'):
                # Generation: one INIT on the intent's exact generation. A durable pin
                # with no committed backend marker means the earlier dispatch never
                # applied, so that same generation is dispatched once here.
                if pinned is not None:
                    if backend_initialized(docker,target.container_id):
                        hba_generation.read_existing(docker,state,target=target);mode='observed'
                    else:
                        authority.initialize(docker,target.container_id,intent['generation'])
                        hba_generation.read_existing(docker,state,target=target);mode='dispatched-after-pin'
                else:
                    if backend_initialized(docker,target.container_id):
                        raise RuntimeError('Source already carries HBA authority; adoption requires explicit adjudication')
                    hba_generation.publish(state,target,intent['generation'])
                    # An uncertain dispatch retains the pin; a later resume resolves it.
                    authority.initialize(docker,target.container_id,intent['generation'])
                    hba_generation.read_existing(docker,state,target=target);mode='initialized'
                checkpoint(state,'generation-initialized',_bind(intent,{'mode':mode}))
                snapshot=authority.read(docker,target.container_id,intent['generation'])
                content=docker('exec',target.container_id,'cat',HBA_PATH).stdout
                if not content.endswith('\n'):raise RuntimeError('Observed HBA rules are not complete text')
                # Preserve the actual rules; only the revision marker is fresh.
                prepared=atomic_hba.prepare(docker,target.container_id,content)
                hba_target.require(docker,target,snapshot,prepared)
                lease.begin(docker,snapshot,prepared,str(uuid.uuid4()),target=target)
                hba_apply.execute(docker,state,lease.descriptors,target=target,startup=lease)
                outcome=hba_settlement.complete_owned(docker,state,lease.descriptors,target=target,startup=lease)
                checkpoint(state,'hba-completed',_bind(intent,{'outcome':outcome}))
            else:
                outcome=read_checkpoint(state,'hba-completed')['outcome']
                if hba_apply.file_digest(docker,target.container_id)!=authority.digest(outcome['journal']['content']):
                    raise RuntimeError('HBA bytes changed after recorded adoption completion')
            docker('stop',target.container_id)
            # A failed or uncertain stop raises before this checkpoint: adoption stays incomplete.
            require_mounts(intent,stopped_identity(docker,target))
            checkpoint(state,'source-stopped',_bind(intent,{'mode':'stopped-observed'}))
        completed=checkpoint(state,'completed',_bind(intent,{'target':asdict(target)}))
        os.unlink(state/NAME)
        effect_receipt.sync_directory(state)
        return completed