"""Immutable installation binding for one HBA registry/container generation.

Explicit bootstrap only. Missing or replaced established state is never reset.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import uuid
import hba_authority as authority
import hba_target

NAME='hba-generation.json'
LIMIT=16384
# The migration record lives beside the pin: a private directory holding the
# intent, its immutable checkpoints and the retired generation's archive. Its
# presence is a startup refusal, never a silent re-pin.
MIGRATION='hba-migration'
MIGRATION_INTENT='intent.json'


def validate(record):
    if not isinstance(record,dict) or set(record)!={'version','generation','target'} or type(record['version']) is not int or record['version']!=1:
        raise ValueError('Invalid HBA generation pin')
    authority.exact(record['generation'],authority.UUID)
    target=record['target']
    if not isinstance(target,dict) or set(target)!={'container_id','name','owner','image'}:raise ValueError('Invalid HBA generation target')
    authority.exact(target['container_id'],authority.HEX)
    hba_target.policy(target['name'],target['owner'],target['image'])
    return record


def publish(state,target,generation):
    record=validate({'version':1,'generation':generation,'target':asdict(target)})
    text=authority.encode(record)
    if len(text.encode())>LIMIT:raise ValueError('HBA generation record too large')
    fd=os.open(Path(state)/NAME,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as output:
        output.write(text);output.flush();os.fsync(output.fileno())
    fd=os.open(state,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return record


def load(state):
    fd=os.open(Path(state)/NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'r',encoding='utf-8') as source:
        metadata=os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o600:
            raise ValueError('HBA generation pin must be a private owned regular file')
        if metadata.st_size>LIMIT:raise ValueError('HBA generation record too large')
        text=source.read(LIMIT+1)
        if len(text.encode())>LIMIT:raise ValueError('HBA generation record too large')
    value=json.loads(text,object_pairs_hook=authority.unique_object)
    if not isinstance(value,dict) or set(value)!={'record','checksum'}:raise ValueError('Invalid HBA generation envelope')
    if value['checksum']!=authority.digest(authority.canonical(value['record'])):raise ValueError('HBA generation checksum mismatch')
    return validate(value['record'])


def require(state,target,generation):
    record=load(state)
    if record['target']!=asdict(target) or record['generation']!=generation:
        raise RuntimeError('Established HBA generation or target changed')
    return record


def read_existing(docker,state,*,target):
    record=load(state)
    require(state,target,record['generation'])
    cid=hba_target.observed(docker,target.container_id,target.name,target.owner,target.image)
    if cid!=target.container_id:raise RuntimeError('Established HBA container changed')
    return authority.read(docker,cid,record['generation'])


def initialize(docker,state,*,target):
    """Caller must hold verified startup ownership and request explicit bootstrap."""
    cid=hba_target.observed(docker,target.container_id,target.name,target.owner,target.image)
    if cid!=target.container_id:raise RuntimeError('HBA initialization target changed')
    generation=str(uuid.uuid4())
    publish(state,target,generation)
    # An uncertain dispatch leaves the host pin. No retry or new generation here.
    authority.initialize(docker,cid,generation)
    return read_existing(docker,state,target=target)
