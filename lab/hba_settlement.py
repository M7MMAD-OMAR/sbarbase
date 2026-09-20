"""Archive a retired HBA attempt with baseline observation or exact apply evidence.

This is not evidence of no past application, active policy or whole-job success.
"""
import json
import os
from pathlib import Path
import stat
import effect_receipt
import hba_authority as authority
import hba_journal as journal
import hba_reconcile as reconcile
import hba_apply

DIRECTORY='hba-outcomes'
LIMIT=journal.MAX_BYTES*2


def directory(state,create=False):
    path=Path(state)/DIRECTORY
    if create:
        try:path.mkdir(mode=0o700)
        except FileExistsError:pass
    metadata=path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o700:
        raise ValueError('HBA outcomes must use a private owned directory')
    if create:effect_receipt.sync_directory(state)
    return path


def validate(value,token):
    base={'version','kind','journal','observed_digest','application','activation'}
    if not isinstance(value,dict):raise ValueError('Invalid HBA outcome shape')
    applied=value.get('kind')=='retired-applied-reload-acknowledged'
    if set(value)!=(base|{'witness'} if applied else base):raise ValueError('Invalid HBA outcome shape')
    if type(value['version']) is not int or value['version']!=1:raise ValueError('Invalid HBA outcome version')
    record=journal.validate(value['journal'])
    if record['token']!=token or value['activation']!='unknown':raise ValueError('HBA outcome identity mismatch')
    if applied:
        witness=value['witness']
        if not isinstance(witness,dict):raise ValueError('HBA completion witness required')
        hba_apply.validate_completion(witness,record,witness.get('journal_digest'))
        if value['observed_digest']!=authority.digest(record['content']) or value['application']!='publication-witnessed':
            raise ValueError('Applied HBA outcome content mismatch')
    elif (value['kind']!='retired-baseline-observed' or value['observed_digest']!=record['expected']
            or authority.digest(record['content'])==record['expected'] or value['application']!='unknown'):
        raise ValueError('HBA outcome does not bind distinct baseline observation')
    return value


def read(state,token,*,sync=False):
    authority.exact(token,authority.UUID)
    parent=directory(state)
    fd=os.open(parent/(token+'.json'),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'r',encoding='utf-8') as source:
        metadata=os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_size>LIMIT:
            raise ValueError('Invalid private HBA outcome file')
        text=source.read(LIMIT+1)
        if len(text.encode())>LIMIT:raise ValueError('HBA outcome too large')
        envelope=json.loads(text,object_pairs_hook=authority.unique_object)
        if not isinstance(envelope,dict) or set(envelope)!={'record','checksum'}:raise ValueError('Invalid HBA outcome envelope')
        if envelope['checksum']!=authority.digest(authority.canonical(envelope['record'])):raise ValueError('HBA outcome checksum mismatch')
        result=validate(envelope['record'],token)
        if sync:os.fsync(source.fileno())
    if sync:effect_receipt.sync_directory(parent)
    return result


def persist(state,outcome):
    token=outcome['journal']['token'];validate(outcome,token)
    parent=directory(state,create=True)
    text=authority.encode(outcome)
    if len(text.encode())>LIMIT:raise ValueError('HBA outcome too large')
    try:fd=os.open(parent/(token+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        if read(state,token,sync=True)!=outcome:raise RuntimeError('Conflicting existing HBA outcome')
    else:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            output.write(text);output.flush();os.fsync(output.fileno())
        effect_receipt.sync_directory(parent)


def cancel_baseline(docker,state,*,target):
    with reconcile.fresh_ownership(state) as state:
        original=journal.read_text(state/journal.NAME)
        record,result=reconcile.retire_locked(docker,state,target=target)
        if record!=journal.decode(original):raise RuntimeError('Pending HBA journal changed during retirement')
        if result['observed_content']!='matches-before':
            raise RuntimeError('HBA cancellation requires distinct baseline bytes; journal remains pending')
        outcome={'version':1,'kind':'retired-baseline-observed','journal':record,
                 'observed_digest':result['observed_digest'],'application':'unknown','activation':'unknown'}
        persist(state,outcome)
        if journal.read_text(state/journal.NAME)!=original:raise RuntimeError('Pending HBA journal changed before settlement')
        (state/journal.NAME).unlink()
        effect_receipt.sync_directory(state)
        return outcome


def complete_applied(docker,state,*,target):
    with reconcile.fresh_ownership(state) as state:
        original=journal.read_text(state/journal.NAME)
        return _complete_locked(docker,state,target,original,journal.decode(original))


def complete_owned(docker,state,descriptors,*,target,startup=None):
    """Complete under the originating live owner's continuously held locks."""
    state=Path(state)
    original,record=hba_apply.require_owner(state,descriptors,startup=startup)
    return _complete_locked(docker,state,target,original,record)


def _complete_locked(docker,state,target,original,record):
    witness=hba_apply.read_completion(state,record,original)
    retired,result=reconcile.retire_locked(docker,state,target=target)
    if retired!=record:raise RuntimeError('Pending HBA journal changed during retirement')
    if result['observed_digest']!=authority.digest(record['content']):
        raise RuntimeError('HBA completion requires current desired bytes; journal remains pending')
    outcome={'version':1,'kind':'retired-applied-reload-acknowledged','journal':record,
             'observed_digest':result['observed_digest'],'application':'publication-witnessed',
             'activation':'unknown','witness':witness}
    persist(state,outcome)
    if journal.read_text(state/journal.NAME)!=original:raise RuntimeError('Pending HBA journal changed before settlement')
    (state/journal.NAME).unlink()
    effect_receipt.sync_directory(state)
    return outcome
