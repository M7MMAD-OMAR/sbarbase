import {useState,type ReactNode} from 'react';
import {ArrowDownUp,Plus,RefreshCw,Search,Square} from 'lucide-react';
import type {RuntimeState} from './types';
import {Choice,Chip,CopyButton,Nothing} from './kit';
import {ago,visibleFunctions,type SortKey} from './format';

const SORTS:{id:SortKey;label:string}[]=[{id:'name',label:'Sort by name'},{id:'created',label:'Sort by created'},{id:'updated',label:'Sort by updated'}];
const LABELS:Record<RuntimeState['state'],string>={off:'Off',pending:'Applying…',on:'On',failed:'Not applied'};

/** The functions of one environment, as the cloud lists them, and the switch for the runtime that serves them. */
export function List({state,origin,busy,onOpen,onRefresh,onAdd,onSwitch,adding,panel}:{panel?:ReactNode;state:RuntimeState;origin:string;busy:boolean;onOpen:(name:string)=>void;onRefresh:()=>void;onAdd:()=>void;onSwitch:(on:boolean)=>void;adding:boolean}){
 const [query,setQuery]=useState(''),[sort,setSort]=useState<SortKey>('name'),[descending,setDescending]=useState(false);
 const rows=visibleFunctions(state.functions,query,sort,descending);
 return <div className="fw-stack">
  <div className="fw-page-head"><div><h2>Edge Functions</h2><p className="fw-hint">Run server-side logic close to your users. Deno functions called with <code>supabase.functions.invoke()</code>.</p></div>
   <div className="fw-head-tools"><Chip tone={state.state==='on'?'ok':state.state==='failed'?'bad':state.state==='pending'?'wait':'muted'}>{LABELS[state.state]}</Chip>
    {(state.state==='on'||state.desired==='on')&&<button type="button" className="fw-ghost" disabled={busy||state.state==='pending'} onClick={()=>onSwitch(false)}><Square aria-hidden="true"/>Turn off</button>}
    {state.state!=='on'&&state.desired==='off'&&state.functions.length>0&&<button type="button" className="fw-primary" disabled={busy} onClick={()=>onSwitch(true)}>Turn on Edge Functions</button>}
    <button type="button" className="fw-primary" aria-expanded={adding} onClick={onAdd}><Plus aria-hidden="true"/>Add function</button></div></div>
  {state.state==='failed'&&state.failure&&<p className="fw-notice bad" role="alert">{state.failure}</p>}
  {panel}
  <div className="fw-toolbar"><label className="fw-search"><Search aria-hidden="true"/><span className="fw-sr">Search function names</span><input type="search" value={query} placeholder="Search function names" onChange={event=>setQuery(event.target.value)}/></label>
   <Choice label="Sort" value={sort} options={SORTS} onChange={setSort} icon={<ArrowDownUp aria-hidden="true"/>}/>
   <button type="button" className="fw-ghost" aria-pressed={descending} onClick={()=>setDescending(!descending)}>{descending?'Descending':'Ascending'}</button>
   <button type="button" className="fw-ghost" onClick={onRefresh}><RefreshCw aria-hidden="true"/>Refresh</button>
   <span className="fw-hint" role="status">Viewing {rows.length} function{rows.length===1?'':'s'}{rows.length!==state.functions.length?` of ${state.functions.length}`:' in total'}</span></div>
  {rows.length?<div className="fw-rows functions" role="table" aria-label="Functions">
   <div className="fw-row head" role="row"><span role="columnheader">NAME</span><span role="columnheader">URL</span><span role="columnheader">CREATED</span><span role="columnheader">UPDATED</span><span role="columnheader">DEPLOYMENTS</span></div>
   {rows.map(row=><div className="fw-row link" role="row" key={row.name}>
    <span role="cell"><button type="button" className="fw-name" onClick={()=>onOpen(row.name)}>{row.name}</button></span>
    <span role="cell" className="fw-urlcell"><code>{origin+row.path}</code><CopyButton quiet text={origin+row.path} label={`Copy the URL of ${row.name}`}/></span>
    <span role="cell" data-label="Created">{ago(row.created_at)}</span><span role="cell" data-label="Updated">{ago(row.updated_at)}</span><span role="cell" data-label="Deployments">{row.deployments}</span></div>)}</div>
   :<Nothing>{state.functions.length?'No function matches your search.':'No functions yet. Add one from a project folder, or write a first one here.'}</Nothing>}
 </div>;
}
