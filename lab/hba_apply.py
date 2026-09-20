"""Single-attempt HBA publication with durable reload-acknowledgment evidence.

A reload signal acknowledgment is not activation or whole-job completion.
"""
import json
import os
from pathlib import Path
import stat
import effect_receipt
import atomic_hba
import hba_authority as authority
import hba_generation
import hba_journal as journal
import hba_ownership as ownership
import hba_target

ATTEMPTS='hba-apply-attempts'
COMPLETIONS='hba-applied'
LIMIT=journal.MAX_BYTES*2


def publish(state,directory,token,record):
    authority.exact(token,authority.UUID)
    parent=state/directory
    try:parent.mkdir(mode=0o700)
    except FileExistsError:pass
    metadata=parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode)!=0o700 or metadata.st_uid!=os.getuid():
        raise ValueError('HBA execution evidence directory is not private')
    effect_receipt.sync_directory(state)
    text=authority.encode(record)
    if len(text.encode())>LIMIT:raise ValueError('HBA execution evidence too large')
    descriptor=os.open(parent/(token+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:
        output.write(text);output.flush();os.fsync(output.fileno())
    effect_receipt.sync_directory(parent)


def file_digest(docker,cid):
    fields=docker('exec',cid,'sha256sum','/etc/postgresql/pg_hba.conf').stdout.split()
    if len(fields)!=2 or fields[1]!='/etc/postgresql/pg_hba.conf':raise RuntimeError('HBA digest unavailable')
    authority.exact(fields[0],authority.HEX)
    return fields[0]


def sql(docker,cid,query):
    return docker('exec','-i',cid,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d','postgres',data=query).stdout.strip()


def execute(docker,state,descriptors,*,target,startup=None):
    """Caller keeps these descriptors held throughout this native execution."""
    state=Path(state)
    if len(descriptors)!=3:raise ValueError('Three HBA execution descriptors required')
    locks=[ownership.require_lock(state,name,fd) for name,fd in zip(('worker.lock','effect.lock','operation.lock'),descriptors)]
    if len(set(locks))!=3:raise RuntimeError('HBA execution ownership must be distinct')
    original=journal.read_text(state/journal.NAME);record=journal.decode(original)
    identity=record['identity']
    if identity['kind']=='worker':
        expected=effect_receipt.hba_identity(state,identity['runtime'])
        if expected!=(identity['runtime'],identity['receipt'],identity['claim'],identity['attempt']):
            raise RuntimeError('HBA execution worker identity changed')
    else:
        from hba_startup import Startup
        if (not isinstance(startup,Startup) or not startup.active or startup.process!=os.getpid()
                or startup.state!=state or startup.identity!=identity or not startup.attempted
                or tuple(descriptors)!=startup.descriptors):
            raise RuntimeError('HBA application requires originating live startup context')
        try:(state/'worker-effect.json').lstat()
        except FileNotFoundError:pass
        else:raise RuntimeError('Startup HBA execution conflicts with worker receipt')
    hba_generation.require(state,target,record['generation'])
    prepared=atomic_hba.Prepared(record['container'],record['expected'],record['content'])
    current=authority.read(docker,record['container'],record['generation'])
    hba_target.require(docker,target,current,prepared)
    permit=authority.authorize(current,prepared,record['token'],identity)
    evidence={'version':1,'journal':record,'journal_digest':authority.digest(original)}
    publish(state,ATTEMPTS,record['token'],{**evidence,'phase':'apply-started'})
    authority.apply(docker,permit)
    desired=authority.digest(prepared.content)
    if file_digest(docker,record['container'])!=desired:raise RuntimeError('Applied HBA bytes differ')
    if sql(docker,record['container'],'SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;')!='0':
        raise RuntimeError('Applied HBA contains parser errors')
    if sql(docker,record['container'],'SELECT pg_reload_conf();')!='t':raise RuntimeError('HBA reload signal not acknowledged')
    if file_digest(docker,record['container'])!=desired:raise RuntimeError('HBA bytes changed after reload signal')
    confirmed=authority.read(docker,record['container'],record['generation'])
    hba_target.require(docker,target,confirmed,prepared)
    authority.authorize(confirmed,prepared,record['token'],identity)
    if journal.read_text(state/journal.NAME)!=original:raise RuntimeError('HBA journal changed during execution')
    result={**evidence,'phase':'applied-reload-acknowledged','content_digest':desired,
            'parser_errors':0,'reload_acknowledged':True,'activation':'unknown'}
    publish(state,COMPLETIONS,record['token'],result)
    return result
