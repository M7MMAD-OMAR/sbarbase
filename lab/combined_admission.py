"""Conservative local split-placement admission based on retained container limits.

The counted set is derived from the daemon by owner label, not from a literal
name list, so a container this module was never told about (a future Studio or
postgres-meta) is still counted against the installation ceiling. Every counted
container must carry a policy tier label and a finite limit per resource field,
which is the contract docs/RESOURCE-POLICY.md section 3.5 item 3 asks for.
"""
import json
import math
import os
from pathlib import Path
import socket
import run as lab
from pressure_admission import avg10,THRESHOLDS
import resource_policy

MIB=1024**2
MAX_MEMORY=6*1024**3
MAX_CPUS=6
RESERVE=2*1024**3+512*MIB

# HostConfig fields that must be a positive integer on every counted container.
FINITE_LIMITS=('Memory','MemorySwap','NanoCpus','CpuShares','BlkioWeight','PidsLimit')


def refusal(memory,cpus,available,host_cpus):
    if any(type(value) is not int for value in (memory,available,host_cpus)) or type(cpus) not in (int,float) or not math.isfinite(cpus):return 'measurement_unavailable'
    if memory<=0 or cpus<=0:return 'unbounded_limits'
    if memory>MAX_MEMORY or cpus>MAX_CPUS:return 'installation_ceiling'
    if available<memory+RESERVE:return 'host_memory_headroom'
    if host_cpus<cpus+2:return 'host_cpu_headroom'
    return None


def owned_names(owner):
    """Container names the daemon itself attributes to an owner label."""
    return lab.docker('ps','-a','--filter','label=io.sbarbase.owner='+owner,'--format','{{.Names}}').stdout.split()


def counted_names(target):
    """Every container of the recorded placement, read from the daemon.

    The owner label is the placement's own record, so nothing has to be
    remembered into this module when a component is added. An owned container
    whose name falls outside the recorded prefix is a refusal rather than a
    silent omission.
    """
    import durable_runtime as runtime
    names=[]
    for owner,prefix in ((runtime.OWNER,runtime.PREFIX),('recovery-target',target.prefix)):
        for name in owned_names(owner):
            if not name.startswith(prefix+'-'):
                raise RuntimeError('Owned container outside the recorded placement')
            names.append(name)
    if not names:
        raise RuntimeError('No owned containers found for the recorded placement')
    return names


def limits(item):
    """Return (memory bytes, cpus) for a counted container, or refuse.

    Refuses when the container carries no tier this policy knows, when any
    resource field is missing or non positive, or when swap is not disabled.
    """
    tier=item.get('Config',{}).get('Labels',{}).get('io.sbarbase.tier')
    if not resource_policy.known_label(tier):
        raise RuntimeError('Counted container carries no policy tier')
    host=item.get('HostConfig',{})
    for field in FINITE_LIMITS:
        if type(host.get(field)) is not int or host[field]<=0:
            raise RuntimeError('Counted container has an unbounded '+field)
    if host['MemorySwap']!=host['Memory']:
        raise RuntimeError('Counted container swap is not disabled')
    return host['Memory'],host['NanoCpus']/1_000_000_000


class CombinedAdmission:
    def __init__(self,source,target):
        # Runtime objects use module constants, not mutable placement names.
        import durable_runtime as runtime
        counted=counted_names(target)
        expected={runtime.DB,runtime.PREFIX+'-storage',runtime.PREFIX+'-management-auth'}
        expected |= {runtime.PREFIX+'-'+e+'-'+kind for e in source.values['environments'] for kind in ('auth','rest')}
        expected |= {target.prefix+'-'+kind for kind in ('db','auth','rest','storage')}
        missing=expected-set(counted)
        if missing:
            raise RuntimeError('Recorded placement container is absent')
        self.ids=[]
        for name in counted:
            item=json.loads(lab.docker('inspect',name).stdout)[0]
            expected_owner='recovery-target' if name.startswith(target.prefix+'-') else runtime.OWNER
            if item['Config']['Labels'].get('io.sbarbase.owner')!=expected_owner:raise RuntimeError('Combined resource ownership mismatch')
            self.ids.append(item['Id'])
        self.check_current()

    def check_current(self):
        info=json.loads(lab.docker('info','--format','{{json .}}').stdout)
        if info.get('Name')!=socket.gethostname() or info.get('OSType')!='linux':raise RuntimeError('Native local daemon required')
        memory=0;cpus=0
        for identity in self.ids:
            item=json.loads(lab.docker('inspect',identity).stdout)[0]
            container_memory,container_cpus=limits(item)
            memory+=container_memory;cpus+=container_cpus
        # The counted set was read from the same labels just above, so this only
        # catches a container that appeared between that listing and this check.
        for owner in ('durable-upstream','recovery-target'):
            running=lab.docker('ps','--no-trunc','-q','--filter','label=io.sbarbase.owner='+owner).stdout.split()
            if any(identity not in self.ids for identity in running):raise RuntimeError('Unexpected running owned resource')
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        reason=refusal(memory,cpus,available,os.cpu_count() or 0)
        if reason:raise RuntimeError('Combined resource admission refused: '+reason)
        metrics={'cpu_some10':avg10(Path('/proc/pressure/cpu').read_text(),'some'),'io_full10':avg10(Path('/proc/pressure/io').read_text(),'full'),'memory_full10':avg10(Path('/proc/pressure/memory').read_text(),'full')}
        if any(metrics[key]>=limit for key,limit in THRESHOLDS.items()):raise RuntimeError('Host pressure too high')
        return {'planned_memory_mib':memory//MIB,'planned_cpus':cpus,'available_memory_mib':available//MIB,'host_pressure':metrics}