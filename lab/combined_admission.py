"""Conservative local split-placement admission based on retained container limits."""
import json
import math
import os
from pathlib import Path
import socket
import run as lab
from pressure_admission import avg10,THRESHOLDS

MIB=1024**2
MAX_MEMORY=6*1024**3
MAX_CPUS=6
RESERVE=2*1024**3+512*MIB


def refusal(memory,cpus,available,host_cpus):
    if any(type(value) is not int for value in (memory,available,host_cpus)) or type(cpus) not in (int,float) or not math.isfinite(cpus):return 'measurement_unavailable'
    if memory<=0 or cpus<=0:return 'unbounded_limits'
    if memory>MAX_MEMORY or cpus>MAX_CPUS:return 'installation_ceiling'
    if available<memory+RESERVE:return 'host_memory_headroom'
    if host_cpus<cpus+2:return 'host_cpu_headroom'
    return None


class CombinedAdmission:
    def __init__(self,source,target):
        # Runtime objects use module constants, not mutable placement names.
        import durable_runtime as runtime
        names=[runtime.DB,runtime.PREFIX+'-storage',runtime.PREFIX+'-management-auth']
        names += [runtime.PREFIX+'-'+e+'-'+kind for e in source.values['environments'] for kind in ('auth','rest')]
        names += [target.prefix+'-'+kind for kind in ('db','auth','rest','storage')]
        self.ids=[]
        for name in names:
            item=json.loads(lab.docker('inspect',name).stdout)[0]
            expected='recovery-target' if name.startswith(target.prefix+'-') else runtime.OWNER
            if item['Config']['Labels'].get('io.sbarbase.owner')!=expected:raise RuntimeError('Combined resource ownership mismatch')
            self.ids.append(item['Id'])
        self.check_current()

    def check_current(self):
        info=json.loads(lab.docker('info','--format','{{json .}}').stdout)
        if info.get('Name')!=socket.gethostname() or info.get('OSType')!='linux':raise RuntimeError('Native local daemon required')
        memory=0;cpus=0
        for identity in self.ids:
            item=json.loads(lab.docker('inspect',identity).stdout)[0];limits=item['HostConfig']
            if limits['Memory']<=0 or limits['NanoCpus']<=0:raise RuntimeError('Unbounded container allocation')
            memory+=limits['Memory'];cpus+=limits['NanoCpus']/1_000_000_000
        for owner in ('durable-upstream','recovery-target'):
            running=lab.docker('ps','--no-trunc','-q','--filter','label=io.sbarbase.owner='+owner).stdout.split()
            if any(identity not in self.ids for identity in running):raise RuntimeError('Unexpected running owned resource')
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        reason=refusal(memory,cpus,available,os.cpu_count() or 0)
        if reason:raise RuntimeError('Combined resource admission refused: '+reason)
        metrics={'cpu_some10':avg10(Path('/proc/pressure/cpu').read_text(),'some'),'io_full10':avg10(Path('/proc/pressure/io').read_text(),'full'),'memory_full10':avg10(Path('/proc/pressure/memory').read_text(),'full')}
        if any(metrics[key]>=limit for key,limit in THRESHOLDS.items()):raise RuntimeError('Host pressure too high')
        return {'planned_memory_mib':memory//MIB,'planned_cpus':cpus,'available_memory_mib':available//MIB,'host_pressure':metrics}
