import {Cpu,HardDrive,MemoryStick,Server,ExternalLink} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading,Refresh} from './components';
import type {ServerHealthSnapshot} from '../src/control/server-health';

function bytes(value:number|null){if(value===null)return 'Unavailable';return value>=1024**3?`${(value/1024**3).toFixed(1)} GiB`:`${Math.round(value/1024**2)} MiB`;}
function duration(value:number|null){if(value===null)return 'Unavailable';const hours=Math.floor(value/3600);return hours>=24?`${Math.floor(hours/24)} days, ${hours%24} hours`:`${hours} hours, ${Math.floor(value%3600/60)} minutes`;}

export function ServerHealth({request}:{request:Api}){
 const result=useData<{data:ServerHealthSnapshot}>(signal=>request('/server','GET',undefined,signal),[request]);
 const health=result.data?.data;
 return <><div className="page-heading"><div><h1>Server</h1><p className="muted">Installation overview for operators.</p></div><Refresh onClick={result.refresh}/></div>
  <ErrorMessage message={result.error}/>{result.loading?<Loading/>:health&&<>
   <p className="server-health-note muted small">{health.scope==='container'?'Sbarbase detected a container. These are OS-visible counters and the filesystem available to the application.':'These are OS-visible counters and the filesystem available to the application.'} They may include host-wide values and do not report container resource limits. Refresh manually to update. Snapshots are cached for five seconds.</p>
   <div className="server-health-grid">
    <section className="server-health-card"><h2><Cpu aria-hidden="true"/>Processor</h2><strong>{health.cpu.cores===null?'Unavailable':`${health.cpu.cores} visible cores`}</strong><p className="muted small">Load average, 1 / 5 / 15 minutes</p><p>{health.cpu.loadAverage?.map(value=>value.toFixed(2)).join(' / ')??'Unavailable'}</p></section>
    <section className="server-health-card"><h2><MemoryStick aria-hidden="true"/>Memory</h2><strong>{bytes(health.memory.usedBytes)} used</strong><p className="muted small">{bytes(health.memory.availableBytes)} available of {bytes(health.memory.totalBytes)}</p><p className="small">Swap: {bytes(health.memory.swapFreeBytes)} free of {bytes(health.memory.swapTotalBytes)}</p></section>
    <section className="server-health-card"><h2><HardDrive aria-hidden="true"/>Disk</h2><strong>{bytes(health.disk.availableBytes)} available</strong><p className="muted small">{bytes(health.disk.totalBytes)} filesystem capacity</p><p className="small">Free including reserved space: {bytes(health.disk.freeBytes)}</p></section>
    <section className="server-health-card"><h2><Server aria-hidden="true"/>Uptime</h2><strong>{duration(health.uptimeSeconds)}</strong><p className="muted small">OS uptime visible to the application</p><p className="small">Measured {new Date(health.capturedAt).toLocaleTimeString()}</p></section>
   </div>
  </>}
  <section className="details server-health-monitor"><h2>Full server monitoring</h2><p>This overview is a snapshot. Beszel is an independent open-source monitor under the MIT license, with history and alerts for host CPU, memory, disks, networking and Docker containers.</p><p className="muted small">Beszel requires a separate installation and administrator account. A dashboard has not been configured here. Keep monitoring private and restrict Docker API access when enabling container statistics.</p><a href="https://beszel.dev/guide/getting-started" target="_blank" rel="noopener noreferrer">Set up Beszel <ExternalLink aria-hidden="true"/></a></section>
 </>;
}
