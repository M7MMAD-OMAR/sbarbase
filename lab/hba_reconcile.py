"""Retire exact interrupted HBA authority under fresh host ownership.

No journal settlement, deletion, HBA replay, reload or startup permission follows.
"""
import os
from pathlib import Path
import atomic_hba
import hba_authority as authority
import hba_journal as journal
import hba_ownership as ownership
import hba_target
import hba_generation


NAMES=('worker.lock','effect.lock','operation.lock')


def exact_entry(snapshot,record):
    entries=authority.decode(snapshot.text,record['generation'])['operations']
    item=entries.get(record['token'])
    if item is not None and item['binding']!=record['binding']:raise RuntimeError('Journal authority binding conflict')
    if any(token!=record['token'] and entry['state']=='active' for token,entry in entries.items()):
        raise RuntimeError('Another operation is active during HBA reconciliation')
    return item


def retire(docker,state,*,target):
    state=Path(state);descriptors=[]
    try:
        # No create, inheritance or shared open-description shortcuts in recovery.
        for name in NAMES:
            descriptor=os.open(state/name,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
            descriptors.append(descriptor)
            ownership.require_lock(state,name,descriptor)
        identities=[(os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in descriptors]
        if len(set(identities))!=3:raise RuntimeError('Recovery ownership locks must be distinct')
        record=journal.load(state/journal.NAME)
        hba_generation.require(state,target,record['generation'])
        prepared=atomic_hba.Prepared(record['container'],record['expected'],record['content'])
        snapshot=authority.read(docker,record['container'],record['generation'])
        hba_target.require(docker,target,snapshot,prepared)
        item=exact_entry(snapshot,record)
        if item is None or item['state']!='revoked':
            authority.update(docker,snapshot,record['token'],record['binding'],revoke=True)
        confirmed=authority.read(docker,record['container'],record['generation'])
        if exact_entry(confirmed,record)!={'binding':record['binding'],'state':'revoked'}:
            raise RuntimeError('Exact HBA retirement was not confirmed')
        fields=docker('exec',record['container'],'sha256sum','/etc/postgresql/pg_hba.conf').stdout.split()
        if len(fields)!=2 or fields[1]!='/etc/postgresql/pg_hba.conf':raise RuntimeError('HBA content observation unavailable')
        authority.exact(fields[0],authority.HEX)
        hba_target.require(docker,target,confirmed,prepared)
        desired=authority.digest(record['content']);observed=fields[0]
        if observed==record['expected']==desired:content='matches-before-and-desired'
        elif observed==record['expected']:content='matches-before'
        elif observed==desired:content='matches-desired'
        else:content='different'
        return {'token':record['token'],'container':record['container'],'generation':record['generation'],
                'authority':'revoked','observed_content':content,'observed_digest':observed,
                'application':'unknown','activation':'unknown','journal_retained':True}
    finally:
        for descriptor in reversed(descriptors):os.close(descriptor)
