import {useState} from 'react';
import {Radio,RefreshCw,Search} from 'lucide-react';
import type {Api} from '../api';
import {Loading} from '../components';
import {useDebounced,useLive} from './live';
import {RANGES,type Call,type FunctionRow,type Range} from './types';
import {Choice,Notice,Problem} from './kit';
import {clock,milliseconds} from './format';

/** Calls to one function as the gateway saw them, and the lines the Edge Functions runtime wrote for the whole environment.
 * The runtime does not say which function wrote a line, so the output is shown for the environment, with the same filter. */
export function Logs({fn,path,request}:{fn:FunctionRow;path:string;request:Api}){
 const [range,setRange]=useState<Range>('1h'),[query,setQuery]=useState(''),[errors,setErrors]=useState(false),[live,setLive]=useState(false);
 const search=useDebounced(query.trim());
 const calls=useLive<{data:{calls:Call[]}}>(signal=>request(`${path}/functions/${fn.name}/logs?range=${range}&limit=200${errors?'&errors=1':''}${search?`&q=${encodeURIComponent(search)}`:''}`,'GET',undefined,signal),
  [path,fn.name,range,errors,search],live?5000:0);
 const output=useLive<{data:{lines?:string[]}}>(signal=>request(`${path}/logs?source=functions&lines=200${errors?'&errors=1':''}`,'GET',undefined,signal),[path,errors],live?5000:0);
 const rows=calls.data?.data.calls??[],lines=(output.data?.data.lines??[]).filter(line=>!query.trim()||line.toLowerCase().includes(query.trim().toLowerCase()));
 return <div className="fw-stack">
  <div className="fw-toolbar"><label className="fw-search"><Search aria-hidden="true"/><span className="fw-sr">Filter calls</span><input type="search" value={query} placeholder="Filter by path, method or status" onChange={event=>setQuery(event.target.value)}/></label>
   <Choice label="Time range" value={range} options={RANGES} onChange={setRange}/>
   <button type="button" className="fw-ghost" role="switch" aria-checked={errors} onClick={()=>setErrors(!errors)}>{errors?'Errors only':'All calls'}</button>
   <button type="button" className={'fw-ghost'+(live?' on':'')} role="switch" aria-checked={live} onClick={()=>setLive(!live)}><Radio aria-hidden="true"/>Live</button>
   <button type="button" className="fw-ghost" aria-label="Refresh the logs" onClick={()=>{calls.refresh();output.refresh();}}><RefreshCw aria-hidden="true"/></button></div>
  <Problem message={calls.error} retry={calls.refresh}/>
  <section className="fw-card" aria-label="Calls"><h3>Calls to {fn.name}</h3>
   {calls.loading&&!calls.data?<Loading/>:rows.length?<div className="fw-rows" role="table" aria-label="Calls">
    <div className="fw-row head" role="row"><span role="columnheader">TIME</span><span role="columnheader">METHOD</span><span role="columnheader">PATH</span><span role="columnheader">STATUS</span><span role="columnheader">TIME TAKEN</span></div>
    {rows.map((row,index)=><div className="fw-row" role="row" key={index}><span role="cell" data-label="Time">{clock(row.at)}</span><span role="cell" data-label="Method"><code>{row.method}</code></span><span role="cell" data-label="Path" className="fw-path"><code>{row.path}</code></span>
     <span role="cell" data-label="Status" className={row.status>=500?'bad':row.status>=400?'warn':''}>{row.status}</span><span role="cell" data-label="Time taken">{milliseconds(row.ms)}</span></div>)}</div>
    :<p className="fw-empty">No calls in this range. Calls the server counted since it started are listed here, at most 200.</p>}</section>
  <section className="fw-card" aria-label="Output"><h3>Output of all functions in this environment</h3>
   <Notice tone="info">The runtime does not tell which function wrote a line, so these lines are for the whole environment. Keys, tokens and passwords are hidden.</Notice>
   {output.error?<Problem message={output.error} retry={output.refresh}/>:output.loading&&!output.data?<Loading/>:lines.length?<pre className="fw-log">{lines.join('\n')}</pre>:<p className="fw-empty">No lines yet.</p>}</section>
 </div>;
}
