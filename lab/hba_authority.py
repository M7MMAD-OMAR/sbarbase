"""Experimental HBA operation registry. Not wired into startup or provisioning.

Host journals and registry-generation reconciliation are required before use.
All backend updates compare validated snapshots under the existing HBA lock.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import uuid
import atomic_hba

PATH='/etc/postgresql/.sbarbase-hba-authority.json'
MARKER='/etc/postgresql/.sbarbase-hba-authority-initialized'
HEX=re.compile(r'[a-f0-9]{64}')
UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
UPDATE=atomic_hba.CAS_SCRIPT.replace('target=/etc/postgresql/pg_hba.conf','target='+PATH,1).replace(
    'current=$(sha256sum', '[ -d '+MARKER+' ]\n[ ! -L '+MARKER+' ]\ncurrent=$(sha256sum',1)
INIT=r'''set -eu
umask 077
directory=/etc/postgresql
[ ! -L "$directory/.sbarbase-hba.lock" ]
exec 9>> "$directory/.sbarbase-hba.lock"
flock -n 9
[ ! -e /etc/postgresql/.sbarbase-hba-authority.json ]
[ ! -L /etc/postgresql/.sbarbase-hba-authority.json ]
mkdir /etc/postgresql/.sbarbase-hba-authority-initialized
temporary=$(mktemp "$directory/.sbarbase-hba-authority.XXXXXXXX")
trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
cat > "$temporary"
[ "$(wc -c < "$temporary")" -eq "$1" ]
computed=$(sha256sum "$temporary")
[ "${computed%% *}" = "$2" ]
sync "$temporary"
mv -f -- "$temporary" /etc/postgresql/.sbarbase-hba-authority.json
sync "$directory"
'''
# The authority and HBA revision comparisons share one unbroken lock lifetime.
APPLY=atomic_hba.CAS_SCRIPT.replace('current=$(sha256sum',r'''[ -d /etc/postgresql/.sbarbase-hba-authority-initialized ]
[ ! -L /etc/postgresql/.sbarbase-hba-authority-initialized ]
[ -f /etc/postgresql/.sbarbase-hba-authority.json ]
[ ! -L /etc/postgresql/.sbarbase-hba-authority.json ]
authority=$(sha256sum /etc/postgresql/.sbarbase-hba-authority.json)
[ "${authority%% *}" = "$4" ] || { printf 'HBA authority changed\n' >&2; exit 75; }
current=$(sha256sum''',1)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
def exact(value,pattern):
    if not isinstance(value,str) or not pattern.fullmatch(value):raise ValueError('Invalid HBA authority identity')


@dataclass(frozen=True)
class Snapshot:
    container_id:str
    generation:str
    text:str


@dataclass(frozen=True)
class Permit:
    prepared:atomic_hba.Prepared
    registry_digest:str
    token:str
    binding:str


def encode(record):return canonical({'record':record,'checksum':digest(canonical(record))})+'\n'


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise RuntimeError('Duplicate HBA registry key')
        result[key]=value
    return result


def decode(text,generation):
    exact(generation,UUID)
    value=json.loads(text,object_pairs_hook=unique_object)
    if not isinstance(value,dict) or set(value)!= {'record','checksum'}:raise RuntimeError('HBA registry envelope invalid')
    record=value['record']
    if value['checksum']!=digest(canonical(record)):raise RuntimeError('HBA registry checksum mismatch')
    if (not isinstance(record,dict) or set(record)!= {'version','generation','revision','operations'}
            or type(record['version']) is not int or record['version']!=1 or record['generation']!=generation
            or not isinstance(record['operations'],dict)):
        raise RuntimeError('HBA registry generation or shape mismatch')
    exact(record['revision'],UUID)
    active=0
    for token,item in record['operations'].items():
        exact(token,UUID)
        if not isinstance(item,dict) or set(item)!= {'binding','state'} or item['state'] not in ('active','revoked'):
            raise RuntimeError('HBA registry operation invalid')
        exact(item['binding'],HEX);active+=item['state']=='active'
    if active>1:raise RuntimeError('HBA registry has conflicting authority')
    return record


def dispatch(docker,cid,script,text,*args):
    exact(cid,HEX)
    docker('exec','-i',cid,'sh','-c',script,'sbarbase-hba-authority',str(len(text.encode())),digest(text),*args,data=text)


def initialize(docker,cid,generation):
    exact(generation,UUID)
    record={'version':1,'generation':generation,'revision':str(uuid.uuid4()),'operations':{}}
    text=encode(record);dispatch(docker,cid,INIT,text)
    return Snapshot(cid,generation,text)


def read(docker,cid,generation):
    exact(cid,HEX);exact(generation,UUID)
    result=docker('exec',cid,'sh','-c',f'[ -d {MARKER} ] && [ ! -L {MARKER} ] && [ -f {PATH} ] && [ ! -L {PATH} ] && cat {PATH}')
    decode(result.stdout,generation)
    return Snapshot(cid,generation,result.stdout)


def operation_binding(prepared,identity):
    if not isinstance(prepared,atomic_hba.Prepared) or not isinstance(identity,dict) or identity.get('kind') not in ('startup','worker'):
        raise ValueError('HBA operation context required')
    exact(prepared.container_id,HEX);exact(prepared.expected_digest,HEX)
    if not isinstance(prepared.content,str) or not prepared.content.endswith('\n') or '\x00' in prepared.content:
        raise ValueError('HBA content must be complete text')
    return digest(canonical({'container':prepared.container_id,'expected':prepared.expected_digest,
                             'content':digest(prepared.content),'operation':identity}))


def update(docker,snapshot,token,binding,revoke=False):
    exact(token,UUID);exact(binding,HEX)
    if type(revoke) is not bool:raise ValueError('Explicit boolean revocation required')
    record=decode(snapshot.text,snapshot.generation)
    entries=record['operations'];prior=entries.get(token)
    if prior is not None and prior['binding']!=binding:raise RuntimeError('HBA operation binding mismatch')
    if not revoke:
        if prior is not None and prior['state']=='revoked':raise RuntimeError('HBA operation revoked')
        if any(key!=token and item['state']=='active' for key,item in entries.items()):raise RuntimeError('Another HBA operation is active')
    entries[token]={'binding':binding,'state':'revoked' if revoke else 'active'}
    record['revision']=str(uuid.uuid4());text=encode(record)
    dispatch(docker,snapshot.container_id,UPDATE,text,digest(snapshot.text))
    return Snapshot(snapshot.container_id,snapshot.generation,text)


def authorize(snapshot,prepared,token,identity):
    exact(token,UUID)
    if snapshot.container_id!=prepared.container_id:raise RuntimeError('HBA authority container mismatch')
    binding=operation_binding(prepared,identity)
    if decode(snapshot.text,snapshot.generation)['operations'].get(token)!={'binding':binding,'state':'active'}:
        raise RuntimeError('HBA operation is not authorized')
    return Permit(prepared,digest(snapshot.text),token,binding)


def apply(docker,permit):
    if not isinstance(permit,Permit):raise ValueError('HBA permit required')
    exact(permit.registry_digest,HEX);exact(permit.token,UUID);exact(permit.binding,HEX)
    prepared=permit.prepared
    operation_binding(prepared,{'kind':'startup'})
    dispatch(docker,prepared.container_id,APPLY,prepared.content,prepared.expected_digest,permit.registry_digest)
