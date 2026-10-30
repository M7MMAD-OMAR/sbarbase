import {useState} from 'react';
import {RefreshCw,ScrollText} from 'lucide-react';
import type {Api} from '../api';

import {useLive} from './live';
import {RANGES,type FunctionRow,type Metrics,type Range} from './types';
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
function Stat({label,value,tone}:{label:string;value:string;tone?:'bad'|'warn'}){return <div className="fw-stat"><span className={'fw-stat-label '+(tone??'')}>{label}</span><strong>{value}</strong></div>;}

/** The numbers the gateway counted for one function: calls, 5xx and 4xx rates, response time. CPU time and memory need the runtime's
 * event worker and are not collected yet. */
export function Overview({fn,path,request,origin,openLogs}:{fn:FunctionRow;path:string;request:Api;origin:string;openLogs:()=>void}){
 const [range,setRange]=useState<Range>('1h');
 const data=useLive<{data:Metrics}>(signal=>request(`${path}/functions/${fn.name}/metrics?range=${range}`,'GET',undefined,signal),[path,fn.name,range,fn.updated_at],30000);
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
  <section className="fw-card" aria-label="Usage"><h3>Usage</h3><Notice tone="info">CPU time and memory per function are not collected yet. They come from the runtime's event worker, planned for a later release.</Notice></section>
  {metrics&&<p className="fw-hint">Counted by this server since {ago(metrics.countingSince)}. Counting starts again when Sbarbase restarts. Calls you send from the Test panel are counted too.</p>}
 </div>;
}
