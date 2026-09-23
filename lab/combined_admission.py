"""Conservative local split-placement admission based on retained container limits.

The counted set is derived from the daemon by owner label, not from a literal
name list. The rule, in full:

- Every container carrying the label key io.sbarbase.owner is read from the
  daemon, whatever its value.
- A container whose owner is one of the two placement owners (the retained
  runtime and the recorded recovery target) and whose name starts with that
  owner's recorded prefix is counted in the plan, running or stopped.
- Any other labelled container is a component this module was never told about.
  While it is running it consumes the installation ceiling, so it is a refusal.
  While it is stopped it consumes nothing, so it is not counted and does not
  refuse, and its name is returned in the plan metrics under `unrecorded_stopped`
  so it is never silent. A second recovery target generation and the stopped
  component containers on this host are that case, not a defect.
- A placement owner's container whose name falls outside that owner's recorded
  prefix follows the same rule: a refusal while it runs, an entry in
  `unrecorded_stopped` while it is stopped.

Every counted container must carry a policy tier label and a finite limit per
resource field, which is the contract docs/engineering/RESOURCE-POLICY.md section 3.5 item 3
asks for.
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


def labelled_names():
    """Every container the daemon attributes to the owner label key, whatever its value.

    A key only filter, so a container launched by an actor this module was never
    told about is still visible even though its owner string is new.
    """
    return lab.docker('ps','-a','--filter','label=io.sbarbase.owner','--format','{{.Names}}').stdout.split()


def placement(target):
    """(counted names, unrecorded stopped names) for the recorded placement.

    The owner label and the recorded prefix are the placement's own record, so
    nothing has to be remembered into this module when a component is added. A
    labelled container outside the placement is a refusal only while it is
    running, because only then does it consume resources; a stopped one is
    reported instead of refused.
    """
    import durable_runtime as runtime
    recorded=((runtime.OWNER,runtime.PREFIX),('recovery-target',target.prefix))
    counted,unrecorded=[],[]
    for name in sorted(labelled_names()):
        item=json.loads(lab.docker('inspect',name).stdout)[0]
        owner=item.get('Config',{}).get('Labels',{}).get('io.sbarbase.owner')
        prefix=next((value for known,value in recorded if owner==known),None)
        if prefix is not None and name.startswith(prefix+'-'):
            counted.append(name)
        elif item.get('State',{}).get('Running'):
            raise RuntimeError('Labelled container outside the recorded placement is running: '+name)
        else:
            unrecorded.append(name)
    if not counted:
        raise RuntimeError('No owned containers found for the recorded placement')
    return counted,unrecorded


def counted_names(target):
    """The counted names only, for a caller that does not report the unrecorded ones."""
    return placement(target)[0]


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
        counted,self.unrecorded_stopped=placement(target)
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
        # The counted set was read from the same label key just above, so this only
        # catches a labelled container that appeared and started running between
        # that listing and this check.
        for name in labelled_names():
            item=json.loads(lab.docker('inspect',name).stdout)[0]
            if item.get('State',{}).get('Running') and item['Id'] not in self.ids:raise RuntimeError('Unexpected running owned resource')
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        reason=refusal(memory,cpus,available,os.cpu_count() or 0)
        if reason:raise RuntimeError('Combined resource admission refused: '+reason)
        metrics={'cpu_some10':avg10(Path('/proc/pressure/cpu').read_text(),'some'),'io_full10':avg10(Path('/proc/pressure/io').read_text(),'full'),'memory_full10':avg10(Path('/proc/pressure/memory').read_text(),'full')}
        if any(metrics[key]>=limit for key,limit in THRESHOLDS.items()):raise RuntimeError('Host pressure too high')
        return {'planned_memory_mib':memory//MIB,'planned_cpus':cpus,'available_memory_mib':available//MIB,'host_pressure':metrics,'unrecorded_stopped':self.unrecorded_stopped}