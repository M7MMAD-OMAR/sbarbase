import {useState} from 'react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading,Empty,Refresh} from './components';

type Minute={minute:number;requests:number;clientErrors:number;serverErrors:number;averageMs:number|null};
type Metrics={since:number|null;window:{minutes:number;requests:number;clientErrors:number;serverErrors:number;p50:number|null;p95:number|null;
 services:Record<string,number>};perMinute:Minute[];services:{service:string;cpuPercent:number|null;memoryBytes:number|null;memoryLimitBytes:number|null}[]};
type RequestRow={at:number;method:string;service:string;path:string;status:number;ms:number};
const SOURCES=[['requests','Requests'],['auth','Auth'],['rest','REST'],['storage','Storage'],['realtime','Realtime']] as const;

function mebibytes(value:number|null){return value===null?'unknown':`${Math.round(value/1024/1024)} MiB`;}
function percent(part:number,total:number){return total?`${Math.round(part/total*1000)/10}%`:'0%';}

/** Requests per minute for the last hour, errors in their own colour. */
function Chart({rows}:{rows:Minute[]}){
 const top=Math.max(1,...rows.map(row=>row.requests));
 const width=rows.length*6;
 return <svg className="chart" viewBox={`0 0 ${width} 60`} preserveAspectRatio="none" role="img"
  aria-label={`Requests per minute for the last hour, at most ${top} in one minute`}>
  {rows.map((row,index)=>{const errors=row.clientErrors+row.serverErrors;const all=row.requests/top*58,bad=errors/top*58;
   return <g key={row.minute}><rect x={index*6} y={60-all} width={4.5} height={all} className="bar"/>
    {errors>0&&<rect x={index*6} y={60-bad} width={4.5} height={bad} className="bar error-bar"/>}</g>;})}
 </svg>;
}

/** What the gateway saw for this environment in the last hour, and what its services use now. */
export function MetricsSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:Metrics}>(signal=>request(path+'/metrics','GET',undefined,signal),[path,request]);
 const metrics=data.data?.data;
 return <section className="details"><div className="section-heading"><h2>Usage</h2><Refresh onClick={data.refresh}/></div>
  <p className="muted small">Requests through the API in the last hour. Counting starts again when Sbarbase restarts.</p>
  <ErrorMessage message={data.error}/>{data.loading?<Loading/>:metrics&&<>
   <div className="stats">
    <div><span className="muted small">Requests</span><strong>{metrics.window.requests}</strong></div>
    <div><span className="muted small">Client errors (4xx)</span><strong>{percent(metrics.window.clientErrors,metrics.window.requests)}</strong></div>
    <div><span className="muted small">Server errors (5xx)</span><strong>{percent(metrics.window.serverErrors,metrics.window.requests)}</strong></div>
    <div><span className="muted small">Response time, median / 95%</span><strong>{metrics.window.p50===null?'none':`${metrics.window.p50} / ${metrics.window.p95} ms`}</strong></div>
   </div>
   <Chart rows={metrics.perMinute}/>
   <p className="small muted">{Object.entries(metrics.window.services).map(([name,count])=>`${name} ${count}`).join(' · ')||'No requests yet.'}</p>
   {metrics.services.length>0&&<div className="table-wrap compact"><table><thead><tr><th>Service</th><th>Memory</th><th>Processor</th></tr></thead><tbody>
    {metrics.services.map(row=><tr key={row.service}><td>{row.service}</td><td>{mebibytes(row.memoryBytes)} of {mebibytes(row.memoryLimitBytes)}</td>
     <td>{row.cpuPercent===null?'unknown':`${row.cpuPercent}%`}</td></tr>)}</tbody></table></div>}
  </>}</section>;
}

/** The last requests through the gateway, or the last lines a service wrote. */
export function LogsSection({path,request}:{path:string;request:Api}){
 const [source,setSource]=useState<typeof SOURCES[number][0]>('requests'),[errors,setErrors]=useState(false);
 const data=useData<{data:{requests?:RequestRow[];lines?:string[]}}>(signal=>
  request(`${path}/logs?source=${source}&lines=200${errors?'&errors=1':''}`,'GET',undefined,signal),[path,request,source,errors]);
 const result=data.data?.data;
 return <section className="details"><div className="section-heading"><h2>Logs</h2><Refresh onClick={data.refresh}/></div>
  <p className="muted small">The last requests through the API, or the last lines each service wrote. Keys, tokens and passwords are hidden.</p>
  <div className="form-row log-controls"><div><label htmlFor="log-source">Source</label>
   <select id="log-source" value={source} onChange={event=>setSource(event.target.value as typeof source)}>
    {SOURCES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></div>
   <label className="check"><input type="checkbox" checked={errors} onChange={event=>setErrors(event.target.checked)}/>Errors only</label></div>
  <ErrorMessage message={data.error}/>{data.loading?<Loading/>:result?.requests?(result.requests.length?
   <div className="table-wrap compact"><table><thead><tr><th>Time</th><th>Request</th><th>Status</th><th>Time taken</th></tr></thead><tbody>
    {result.requests.map((row,index)=><tr key={index}><td>{new Date(row.at).toLocaleTimeString()}</td>
     <td><code>{row.method} {row.service}{row.path}</code></td><td className={row.status>=500?'status-bad':row.status>=400?'status-warn':''}>{row.status}</td>
     <td>{row.ms} ms</td></tr>)}</tbody></table></div>:<Empty>No requests yet.</Empty>)
   :result?.lines?(result.lines.length?<pre className="log">{result.lines.join('\n')}</pre>:<Empty>No lines yet.</Empty>):null}
 </section>;
}
