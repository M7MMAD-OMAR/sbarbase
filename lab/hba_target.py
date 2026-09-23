"""Capture a configured HBA database target once, then verify its exact ID.

Expected name, owner and pinned image must come from trusted installation config,
never from an incoming project request or the journal being reconciled.
"""
from dataclasses import dataclass
import json
import re
import hba_authority as authority


@dataclass(frozen=True)
class Target:
    container_id:str
    name:str
    owner:str
    image:str


def policy(name,owner,image):
    if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',name):raise ValueError('Invalid configured database name')
    if not isinstance(owner,str) or not owner:raise ValueError('Database owner label required')
    if not isinstance(image,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('Pinned database image required')


def observed(docker,reference,name,owner,image):
    policy(name,owner,image)
    result=json.loads(docker('inspect',reference).stdout)
    if not isinstance(result,list) or len(result)!=1 or not isinstance(result[0],dict):raise RuntimeError('Database inspection unavailable')
    value=result[0]
    if (value.get('Name')!='/'+name or value.get('Image')!=image
            or value.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')!=owner
            or value.get('State',{}).get('Running') is not True):
        raise RuntimeError('Database target ownership or configuration mismatch')
    authority.exact(value.get('Id'),authority.HEX)
    return value['Id']


def capture(docker,name,owner,image):
    return Target(observed(docker,name,name,owner,image),name,owner,image)


def require(docker,target,snapshot,prepared):
    if not isinstance(target,Target):raise ValueError('Captured database target required')
    authority.exact(target.container_id,authority.HEX)
    if snapshot.container_id!=target.container_id or prepared.container_id!=target.container_id:
        raise RuntimeError('HBA intent targets a different database container')
    actual=observed(docker,target.container_id,target.name,target.owner,target.image)
    if actual!=target.container_id:raise RuntimeError('Captured database identity changed')


def mounts(info):
    """The mount identity of one inspected container, sorted by destination."""
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
