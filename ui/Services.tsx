import {useEffect,useRef,useState} from 'react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {ago} from './functions-workspace/format';
import {ABOUT,COST_NOTE,NAMES,STATE_CLASS,STATE_LABEL,SWITCH_ROUTE,changing,confirmOff,costText,lastUsedText,memory,memoryText,staleText,
 type Service,type ServiceId,type Services} from './services-view';
import './auth-workspace.css';
import './services.css';

const ORDER:ServiceId[]=['realtime','functions','cron','database','studio'];
/** Where the services without a switch here are turned on: the section of the environment page that owns them. */
const OWNER_SECTION:Partial<Record<ServiceId,{section:string;label:string}>>={database:{section:'database',label:'Open Database connection'},studio:{section:'overview',label:'Open Overview'}};
const isOn=(service:Service)=>service.state==='on'||service.state==='starting';

function Chip({service}:{service:Service}){
 return <span className={'aw-chip '+STATE_CLASS[service.state]}>{STATE_LABEL[service.state]}</span>;
}

/** The switch of one service. Named by the service, not by its state, so a screen reader hears the same name before and after a change. */
function Switcher({service,disabled,onToggle,id}:{service:Service;disabled:boolean;onToggle:(service:Service)=>void;id?:string}){
 return <button id={id} type="button" role="switch" aria-checked={isOn(service)} aria-label={NAMES[service.id]} className="aw-switch" disabled={disabled||changing(service)||!SWITCH_ROUTE[service.id]}
  onClick={()=>onToggle(service)}><span/></button>;
}

/** The question before a service that is in use is turned off. It takes focus when it opens, so a keyboard user lands on it. */
function Confirm({service,text,busy,onKeep,onOff}:{service:Service;text:string;busy:boolean;onKeep:()=>void;onOff:()=>void}){
 const box=useRef<HTMLDivElement>(null);
 useEffect(()=>{box.current?.focus();},[]);
 return <div ref={box} tabIndex={-1} className="aw-notice" role="alertdialog" aria-label={'Turn off '+NAMES[service.id]}><p className="svc-ask">{text}</p>
  <div className="aw-actions"><button type="button" className="aw-ghost" onClick={onKeep}>Keep it on</button>
   <button type="button" className="aw-primary" disabled={busy} onClick={onOff}>Turn off {NAMES[service.id]}</button></div></div>;
}

/** The Services of one environment: what is running, what it holds in memory, when it was last used and a switch for each
 * (docs/guides/services.md). Realtime, Edge Functions and Scheduled jobs are switched here; direct database access and Studio
 * keep their own pages and link to them. */
export function ServicesPage({path,request,go}:{path:string;request:Api;go:(section:string)=>void}){
 const data=useData<{data:Services}>(signal=>request(path+'/services','GET',undefined,signal),[path,request]);
 const [tab,setTab]=useState<'overview'|ServiceId>('overview'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[ask,setAsk]=useState<{id:ServiceId;text:string}|null>(null);
 // A refresh empties `data` for a moment; the page keeps showing the last answer meanwhile, so nothing flashes or loses its place.
 const last=useRef<Services|undefined>(undefined);
 if(data.data?.data)last.current=data.data.data;
 const current=last.current,now=Date.now();
 // A service that is starting or stopping is asked about again every few seconds, and the page refreshes itself once a minute while open.
 const waiting=current?.services.some(changing);
 useEffect(()=>{const timer=setTimeout(data.refresh,waiting?3000:60_000);return()=>clearTimeout(timer);},[waiting,data.data,data.error]);
 async function turn(id:ServiceId,enabled:boolean){
  const route=SWITCH_ROUTE[id];if(!route)return;
  setBusy(true);setError('');setAsk(null);
  try{await request(path+route,'PUT',{enabled});data.refresh();}catch(failure){setError((failure as Error).message);}finally{setBusy(false);}
 }
 /** Turning off asks first when something is open, judged from a fresh answer, not from what the page loaded a minute ago. */
 async function toggle(service:Service){
  if(!isOn(service)){void turn(service.id,true);return;}
  setBusy(true);setError('');
  try{
   const fresh=(await request(path+'/services','GET') as {data:Services}).data;
   const now=fresh.services.find(item=>item.id===service.id)??service;
   const text=confirmOff(now);
   if(text){setAsk({id:service.id,text});data.refresh();}else{await request(path+SWITCH_ROUTE[service.id],'PUT',{enabled:false});data.refresh();}
  }catch(failure){setError((failure as Error).message);}finally{setBusy(false);}
 }
 const byId=(id:ServiceId)=>current?.services.find(item=>item.id===id);
 const stale=current?staleText(current.stats,now,ago):null;
 const free=current?.capacity.availableBytes;
 const locked=busy||!current?.canChange;
 return <section className="aw">
  <div className="aw-top"><div><h2>Services</h2><p className="aw-hint">The optional services of this environment, and only this one. An unused service holds memory for nothing, so turn on what you use.</p></div>
   {free!==null&&free!==undefined&&<span className="aw-chip" title="Memory the server can still give to new services"><bdi>{memory(free)}</bdi> free</span>}</div>
  <ErrorMessage message={error||data.error}/>
  {!current&&data.loading?<Loading/>:current&&<div className="aw-layout">
   <nav className="aw-nav" aria-label="Services pages"><button type="button" className={tab==='overview'?'active':''} aria-current={tab==='overview'?'page':undefined} onClick={()=>setTab('overview')}>Overview</button>
    {ORDER.map(id=><button key={id} type="button" className={tab===id?'active':''} aria-current={tab===id?'page':undefined} onClick={()=>setTab(id)}>{NAMES[id]}</button>)}</nav>
   <div className="aw-pane">
    {stale&&<p className="aw-notice" role="status">{stale}</p>}
    {!current.canChange&&<p className="aw-hint svc-readonly">You can see these services. Owners and admins can change them.</p>}
    {tab==='overview'&&<>
     <h3>Overview</h3><p className="aw-hint">What runs for this environment now, what it holds in memory and when it was last used.</p>
     <div className="aw-group svc-list">{ORDER.map(id=>{const service=byId(id);if(!service)return null;
      return <div key={id} className="svc-item"><div className="aw-row svc-row">
       <div className="aw-text"><button type="button" className="svc-name" onClick={()=>setTab(id)}>{NAMES[id]}</button>
        <p className="aw-hint"><bdi>{memoryText(service)}</bdi><span aria-hidden="true"> · </span>Last used: <bdi>{lastUsedText(service,now,ago)}</bdi></p></div>
       <Chip service={service}/>
       {SWITCH_ROUTE[id]?<Switcher service={service} disabled={locked} onToggle={s=>void toggle(s)}/>:<span className="svc-spacer" aria-hidden="true"/>}</div>
       {ask?.id===id&&<Confirm service={service} text={ask.text} busy={busy} onKeep={()=>setAsk(null)} onOff={()=>void turn(id,false)}/>}</div>;})}</div>
    </>}
    {tab!=='overview'&&(()=>{const service=byId(tab);if(!service)return null;const cost=service.state==='off'?costText(service,current.capacity):null,owner=current.canChange?OWNER_SECTION[tab]:undefined;
     return <>
      <div className="aw-top"><div><h3>{NAMES[tab]}</h3><p className="aw-hint aw-lead">{ABOUT[tab]}</p></div><Chip service={service}/></div>
      {service.state==='failed'&&service.failure&&<p className="aw-notice" role="alert">{service.failure}</p>}
      <dl className="svc-facts"><div><dt>Memory now</dt><dd><bdi>{memoryText(service)}</bdi></dd></div><div><dt>Last used</dt><dd><bdi>{lastUsedText(service,now,ago)}</bdi></dd></div>
       {service.inUse!==null&&<div><dt>{service.id==='realtime'?'Open connections':'Calls running'}</dt><dd>{service.inUse}</dd></div>}</dl>
      {SWITCH_ROUTE[tab]&&<div className="aw-group"><div className="aw-row"><div className="aw-text"><span className="svc-state">{isOn(service)?'On':'Off'}</span>
        <p className="aw-hint">{cost??COST_NOTE[tab]}</p></div>
        <Switcher id={'svc-'+tab} service={service} disabled={locked} onToggle={s=>void toggle(s)}/></div></div>}
      {ask?.id===tab&&<Confirm service={service} text={ask.text} busy={busy} onKeep={()=>setAsk(null)} onOff={()=>void turn(tab,false)}/>}
      {(cost||owner)&&<p className="aw-hint svc-note">{COST_NOTE[tab]}</p>}
      {owner&&<button type="button" className="aw-ghost" onClick={()=>go(owner.section)}>{owner.label}</button>}
     </>;})()}
   </div>
  </div>}
 </section>;
}
