import {useState} from 'react';
import {RefreshCw,ScrollText} from 'lucide-react';
import type {Api} from '../api';

import {useLive} from './live';
import {RANGES,cpuTime,mebibytes,share,usageNote,type FunctionRow,type Metrics,type Range,type Usage} from './types';
import {Segmented,CopyButton,Notice,Problem} from './kit';
import {ago,milliseconds,percent,clock} from './format';
import {bars,line} from './chart';

const W=600,H=110;
function Invocations({metrics}:{metrics:Metrics}){
 const requests=metrics.points.map(point=>point.requests),top=Math.max(1,...requests);
 const all=bars(requests,W,H),client=bars(metrics.points.map(point=>point.clientErrors+point.serverErrors),W,H,0.2,top),server=bars(metrics.points.map(point=>point.serverErrors),W,H,0.2,top);
 return <svg className="fw-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`Calls over the last ${RANGES.find(item=>item.id===metrics.range)!.label}, at most ${top} in one step`}>
  {all.map((bar,index)=><g key={index}><rect className="bar" x={bar.x} y={bar.y} width={bar.width} height={bar.height}><title>{`${clock(metrics.points[index]!.at)}: ${requests[index]} calls`}</title></rect>
   {client[index]!.height>0&&<rect className="bar warn" x={client[index]!.x} y={client[index]!.y} width={client[index]!.width} height={client[index]!.height}/>}
   {server[index]!.height>0&&<rect className="bar bad" x={server[index]!.x} y={server[index]!.y} width={server[index]!.width} height={server[index]!.height}/>}</g>)}
 </svg>;
}
function Timing({metrics}:{metrics:Metrics}){
 const averages=line(metrics.points.map(point=>point.averageMs),W,H),peaks=line(metrics.points.map(point=>point.maxMs),W,H);
 if(!averages.length)return <div className="fw-empty-chart">No calls in this range.</div>;
 return <svg className="fw-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Response time over the range">
  {peaks.map((points,index)=><polyline key={'p'+index} className="line faint" points={points}/>)}
  {averages.map((points,index)=><polyline key={'a'+index} className="line" points={points}/>)}</svg>;
}
/** One bar for the average of each step, with the largest behind it: a worker reports when it ends, so most steps are empty and a line would show nothing. */
function Series({label,caption,at,format,average,peak}:{label:string;caption:string;at:number[];format:(value:number|null)=>string;average:(number|null)[];peak:(number|null)[]}){
 const top=Math.max(1,...peak.map(value=>value??0),...average.map(value=>value??0));
 const back=bars(peak.map(value=>value??0),W,H,0.2,top),front=bars(average.map(value=>value??0),W,H,0.2,top);
 return <><p className="fw-hint">{caption}</p><svg className="fw-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
  {back.map((bar,index)=>bar.height>0&&<rect key={'p'+index} className="bar faint" x={bar.x} y={bar.y} width={bar.width} height={bar.height}><title>{`${clock(at[index]!)}: largest ${format(peak[index]!)}`}</title></rect>)}
  {front.map((bar,index)=>bar.height>0&&<rect key={'a'+index} className="bar" x={bar.x} y={bar.y} width={bar.width} height={bar.height}><title>{`${clock(at[index]!)}: average ${format(average[index]!)}`}</title></rect>)}</svg></>;
}
function Stat({label,value,tone}:{label:string;value:string;tone?:'bad'|'warn'}){return <div className="fw-stat"><span className={'fw-stat-label '+(tone??'')}>{label}</span><strong>{value}</strong></div>;}

/** The numbers the gateway counted for one function: calls, 5xx and 4xx rates, response time. CPU time and memory come from the runtime's
 * event worker (the Usage section), per worker lifetime. */
export function Overview({fn,path,request,origin,openLogs}:{fn:FunctionRow;path:string;request:Api;origin:string;openLogs:()=>void}){
 const [range,setRange]=useState<Range>('1h');
 const data=useLive<{data:Metrics}>(signal=>request(`${path}/functions/${fn.name}/metrics?range=${range}`,'GET',undefined,signal),[path,fn.name,range,fn.updated_at],30000);
 const usage=useLive<{data:Usage}>(signal=>request(`${path}/functions/${fn.name}/usage?range=${range}`,'GET',undefined,signal),[path,fn.name,range,fn.updated_at],30000);
 const used=usage.data?.data;
 const metrics=data.data?.data;
 const url=origin+fn.path;
 const since=metrics?.sinceDeploy,errors=since?since.clientErrors+since.serverErrors:0;
 return <div className="fw-stack">
  <div className="fw-overview-head"><div><h2>{fn.name}</h2><p className="fw-url"><code>{url}</code><CopyButton quiet text={url} label="Copy the function URL"/></p>
   <p className="fw-hint">Last deployed {ago(fn.updated_at)}. {fn.deployments} deployment{fn.deployments===1?'':'s'}.</p></div>
   <div className="fw-head-tools"><Segmented label="Time range" options={RANGES} value={range} onChange={setRange}/>
    <button type="button" className="fw-ghost" onClick={data.refresh} aria-label="Refresh the numbers"><RefreshCw aria-hidden="true"/></button></div></div>
  <Problem message={data.error} retry={data.refresh}/>
  <section className="fw-card" aria-busy={data.loading} aria-label="Invocations"><div className="fw-stats">
   <Stat label="TOTAL INVOCATIONS" value={String(metrics?.totals.requests??0)}/>
   <Stat label="5XX RATE" tone="bad" value={percent(metrics?.totals.serverErrors??0,metrics?.totals.requests??0)}/>
   <Stat label="4XX RATE" tone="warn" value={percent(metrics?.totals.clientErrors??0,metrics?.totals.requests??0)}/></div>
   {metrics&&(metrics.totals.requests?<Invocations metrics={metrics}/>:<div className="fw-empty-chart">No calls in this range.</div>)}</section>
  <section className="fw-card" aria-label="Errors since the last deploy"><div className="fw-card-head"><h3>Errors since the last deploy</h3><button type="button" className="fw-ghost" onClick={openLogs}><ScrollText aria-hidden="true"/>View logs</button></div>
   {since&&<p>There {since.requests===1?'has':'have'} been <strong>{since.requests} invocation{since.requests===1?'':'s'}</strong> {since.complete?'since the last deploy':`since ${ago(since.since)}`} and {errors?<><strong>{errors} error{errors===1?'':'s'}</strong> ({since.serverErrors} server, {since.clientErrors} client)</>:'no errors'}.</p>}</section>
  <section className="fw-card" aria-label="Performance"><h3>Performance</h3><div className="fw-stats">
   <Stat label="AVERAGE RESPONSE TIME" value={milliseconds(metrics?.totals.averageMs??null)}/><Stat label="MAX RESPONSE TIME" value={milliseconds(metrics?.totals.maxMs??null)}/></div>
   {metrics&&<Timing metrics={metrics}/>}
   <p className="fw-hint">Time until the function starts answering, as the gateway sees it. A streamed answer takes longer than the number shows.</p></section>
  <section className="fw-card" aria-label="Usage" aria-busy={usage.loading}><h3>Usage</h3><Problem message={usage.error} retry={usage.refresh}/>
   {used&&usageNote(used)?<Notice tone="info">{usageNote(used)}</Notice>:used&&used.available?<>
    <div className="fw-stats"><Stat label="AVERAGE CPU TIME" value={cpuTime(used.totals.averageCpuMs)}/><Stat label="MAX CPU TIME" value={cpuTime(used.totals.maxCpuMs)}/>
     <Stat label="AVERAGE MEMORY USAGE" value={mebibytes(used.totals.averageMemory)}/><Stat label="HEAP" value={share(used.totals.heapPercent)}/><Stat label="EXTERNAL" value={share(used.totals.externalPercent)}/></div>
    <Series at={used.points.map(point=>point.at)} format={cpuTime} label="CPU time per worker over the range" caption="CPU time of the workers that ended in each step: the average, and the largest behind it" average={used.points.map(point=>point.averageCpuMs)} peak={used.points.map(point=>point.maxCpuMs)}/>
    <Series at={used.points.map(point=>point.at)} format={mebibytes} label="Memory per worker over the range" caption="Memory of the workers that ended in each step: the average, and the largest behind it" average={used.points.map(point=>point.averageMemory)} peak={used.points.map(point=>point.maxMemory)}/>
    <p className="fw-hint">{used.totals.workers} worker{used.totals.workers===1?'':'s'} ended in this range, {used.totals.boots} started{used.totals.averageBootMs!==null?`, in ${used.totals.averageBootMs} ms on average`:''}. Each number is for one worker's whole life, not one call, and memory is what the worker held when it ended. Counted since {ago(used.since)}, when the runtime first reported this function.</p></>:null}
  </section>
  {metrics&&<p className="fw-hint">Counted by this server since {ago(metrics.countingSince)}. Counting starts again when Sbarbase restarts. Calls you send from the Test panel are counted too.</p>}
 </div>;
}
