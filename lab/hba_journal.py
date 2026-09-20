"""Experimental immutable host intent, not runtime ownership or replay authority.

The caller must own its private state directory. Production use additionally
requires existing host leases, validated live receipts and startup reconciliation.
"""
import json
import os
import re
import stat
from pathlib import Path
import atomic_hba
import hba_authority as authority


NAME='hba-operation.json'
MAX_BYTES=4*1024*1024


def journal_path(path):
    path=Path(path)
    if path.name!=NAME:raise ValueError('Stable HBA journal filename required')
    return path


FIELDS={'version','token','generation','container','expected','content','identity','registry','binding'}


def identity(value):
    if not isinstance(value,dict):raise ValueError('HBA journal identity required')
    if value.get('kind')=='startup':
        if set(value)!={'kind','startup'}:raise ValueError('Exact startup identity required')
        authority.exact(value['startup'],authority.UUID)
    elif value.get('kind')=='worker':
        if set(value)!={'kind','runtime','receipt','claim','attempt'}:raise ValueError('Exact worker identity required')
        authority.exact(value['runtime'],re.compile(r'e_[a-f0-9]{24}'))
        authority.exact(value['receipt'],authority.UUID);authority.exact(value['claim'],authority.UUID)
        if type(value['attempt']) is not int or not 1<=value['attempt']<=2147483647:
            raise ValueError('Invalid worker attempt')
    else:raise ValueError('Unknown HBA operation kind')


def validate(record):
    if not isinstance(record,dict) or set(record)!=FIELDS or type(record['version']) is not int or record['version']!=1:
        raise ValueError('Invalid HBA journal shape')
    for key in ('token','generation'):authority.exact(record[key],authority.UUID)
    for key in ('container','expected','registry','binding'):authority.exact(record[key],authority.HEX)
    identity(record['identity'])
    prepared=atomic_hba.Prepared(record['container'],record['expected'],record['content'])
    if authority.operation_binding(prepared,record['identity'])!=record['binding']:
        raise ValueError('HBA journal binding mismatch')
    return record


def decode(text):
    value=json.loads(text,object_pairs_hook=authority.unique_object)
    if not isinstance(value,dict) or set(value)!={'record','checksum'}:raise ValueError('Invalid HBA journal envelope')
    if value['checksum']!=authority.digest(authority.canonical(value['record'])):raise ValueError('HBA journal checksum mismatch')
    return validate(value['record'])


def load(path):
    path=journal_path(path)
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'r',encoding='utf-8') as source:
        metadata=os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):raise ValueError('HBA journal must be a regular file')
        if metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o600:
            raise ValueError('HBA journal must be private and owned by caller')
        if metadata.st_size>MAX_BYTES:raise ValueError('HBA journal too large')
        text=source.read(MAX_BYTES+1)
        if len(text.encode('utf-8'))>MAX_BYTES:raise ValueError('HBA journal too large')
        return decode(text)


def publish(path,record):
    # A torn record remains discoverable and blocks another attempt. Never unlink.
    path=journal_path(path)
    validate(record)
    text=authority.encode(record)
    if len(text.encode('utf-8'))>MAX_BYTES:raise ValueError('HBA journal too large')
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as target:
        target.write(text);target.flush();os.fsync(target.fileno())
    parent=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(parent)
    finally:os.close(parent)


def begin(docker,path,snapshot,prepared,token,operation):
    """Persist intent before one register dispatch; uncertain results stay pending."""
    authority.decode(snapshot.text,snapshot.generation)
    if snapshot.container_id!=prepared.container_id:raise ValueError('HBA journal container mismatch')
    record={'version':1,'token':token,'generation':snapshot.generation,'container':prepared.container_id,
            'expected':prepared.expected_digest,'content':prepared.content,'identity':operation,
            'registry':authority.digest(snapshot.text),'binding':authority.operation_binding(prepared,operation)}
    publish(path,record)
    # Reload the durable bytes. Never infer permission from a mutable caller dict.
    saved=load(path)
    if saved!=record:raise RuntimeError('HBA journal changed before dispatch')
    return authority.update(docker,snapshot,saved['token'],saved['binding'])


def inspect(docker,path):
    """Report registry authority only. Absence does not exclude delayed dispatch.

    No status authorizes repair, journal deletion, registration or HBA replay.
    """
    record=load(path)
    snapshot=authority.read(docker,record['container'],record['generation'])
    item=authority.decode(snapshot.text,snapshot.generation)['operations'].get(record['token'])
    if item is None:state='absent'
    elif item['binding']!=record['binding']:raise RuntimeError('HBA journal disagrees with registry binding')
    else:state=item['state']
    return {'token':record['token'],'container':record['container'],'generation':record['generation'],
            'authority':state,'application':'unknown','activation':'unknown'}
