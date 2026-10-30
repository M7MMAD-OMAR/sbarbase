import {useEffect,useRef,useState} from 'react';
import type * as React from 'react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {ago} from './functions-workspace/format';
import {ABOUT,COST_NOTE,NAMES,STATE_CLASS,STATE_LABEL,STATE_NOTE,SWITCH_ROUTE,changing,confirmOff,costText,lastUsedText,limitsText,memory,memoryText,modeEffect,onDemandLine,staleText,unusedText,
 type Mode,type Service,type ServiceId,type Services} from './services-view';
import './auth-workspace.css';
import './services.css';

const ORDER:ServiceId[]=['realtime','functions','cron','database','studio'];
/** Where the services without a switch here are turned on: the section of the environment page that owns them. */
const OWNER_SECTION:Partial<Record<ServiceId,{section:string;label:string}>>={database:{section:'database',label:'Open Database connection'},studio:{section:'overview',label:'Open Overview'}};
const isOn=(service:Service)=>service.state==='on'||service.state==='starting';
/** Realtime and Edge Functions have a mode; the others have a switch. */
const hasMode=(service:Service)=>service.mode!==undefined;
const MODE_LABEL:Record<Mode,string>={off:'Off',on:'On',on_demand:'On demand'};
const MIN_IDLE=5;

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

/** A hint, never an action: the question and one button that asks, like the switch does. */
function Hint({service,busy,canAct,onOff}:{service:Service;busy:boolean;canAct:boolean;onOff:()=>void}){
 return <div className="svc-hint" role="note"><p>{unusedText(service)}</p>{canAct&&<button type="button" className="aw-ghost" disabled={busy} onClick={onOff}>Turn off {NAMES[service.id]}</button>}</div>;
}

/** Arrow keys move the choice of a radio group and its focus, as a radio group does. Right to left layouts flip left and right. */
export function arrow<T extends string>(event:React.KeyboardEvent<HTMLElement>,options:readonly T[],current:T,choose:(value:T)=>void){
 const rtl=getComputedStyle(event.currentTarget).direction==='rtl';
 const step=event.key==='ArrowRight'?(rtl?-1:1):event.key==='ArrowLeft'?(rtl?1:-1):event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:0;
 if(!step)return;
 event.preventDefault();
 const next=options[(options.indexOf(current)+step+options.length)%options.length]!;
 choose(next);
 requestAnimationFrame(()=>(event.currentTarget.parentElement?.querySelector<HTMLElement>('[aria-checked="true"]'))?.focus());
}

/** Off, On or On demand for Realtime and Edge Functions, with what the choice does in words before it is applied, the quiet period (minutes, 5 or more) and the
 * limits the gateway enforces, shown and not editable. */
function ModeControl({service,canChange,busy,limits,costNote,onApply}:{service:Service;canChange:boolean;busy:boolean;limits:Services['limits'];costNote:string|null;
 onApply:(mode:Mode,minutes:number)=>void}){
 const saved=service.mode!,savedMinutes=service.idleMinutes??30;
 const [draft,setDraft]=useState<Mode>(saved),[minutes,setMinutes]=useState(String(savedMinutes));
 // A change that arrived from outside (another admin, a refresh) replaces what was being edited.
 useEffect(()=>{setDraft(saved);setMinutes(String(savedMinutes));},[saved,savedMinutes,service.id]);
 const value=Number(minutes),valid=Number.isInteger(value)&&value>=MIN_IDLE;
 const changed=draft!==saved||(draft==='on_demand'&&value!==savedMinutes);
 const running=service.state==='on'||service.state==='starting'||service.state==='waking';
 const options:Mode[]=service.onDemandAvailable===false?['off','on']:['off','on','on_demand'];
 const id='mode-'+service.id;
 return <div className="svc-mode">
  <div className="aw-sub svc-radios" role="radiogroup" aria-label={NAMES[service.id]+' mode'}>
   {options.map(mode=><button key={mode} type="button" role="radio" aria-checked={draft===mode} tabIndex={draft===mode?0:-1} className={draft===mode?'active':''} disabled={busy||!canChange||changing(service)}
    onClick={()=>setDraft(mode)} onKeyDown={event=>arrow(event,options,draft,setDraft)}>{MODE_LABEL[mode]}</button>)}</div>
  <p className="aw-hint svc-effect" role="status">{modeEffect(service,draft,valid?value:savedMinutes,saved,running)}</p>
  {draft!=='on_demand'&&draft!=='off'&&costNote&&<p className="aw-hint">{costNote}</p>}
  {draft==='on_demand'&&<div className="aw-field svc-idle"><label htmlFor={id+'-idle'}>Quiet period (minutes)</label>
   <input id={id+'-idle'} type="number" inputMode="numeric" min={MIN_IDLE} step={1} value={minutes} disabled={busy||!canChange} aria-invalid={!valid} aria-describedby={id+'-idle-hint'} onChange={event=>setMinutes(event.target.value)}/>
   <p id={id+'-idle-hint'} className={valid?'aw-hint':'aw-error'}>{valid?`How long it must stay unused, with nothing open, before it sleeps. At least ${MIN_IDLE}.`:`Enter a whole number of at least ${MIN_IDLE}.`}</p></div>}
  {limits&&draft==='on_demand'&&<p className="aw-hint svc-limits">{limitsText(limits)}</p>}
  {canChange&&<div className="aw-actions svc-apply"><button type="button" className="aw-primary" disabled={busy||!changed||(draft==='on_demand'&&!valid)||changing(service)} onClick={()=>onApply(draft,draft==='on_demand'?value:savedMinutes)}>Apply</button></div>}
 </div>;
}

/** The Services of one environment: what is running, what it holds in memory, when it was last used and how to turn each on or off
 * (docs/guides/services.md). Realtime and Edge Functions are Off, On or On demand; Scheduled jobs has a switch; direct database access and Studio keep
 * their own pages and link to them. */
export function ServicesPage({path,request,go}:{path:string;request:Api;go:(section:string)=>void}){
 const data=useData<{data:Services}>(signal=>request(path+'/services','GET',undefined,signal),[path,request]);
 const [tab,setTab]=useState<'overview'|ServiceId>('overview'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[ask,setAsk]=useState<{id:ServiceId;text:string;mode?:Mode;minutes?:number}|null>(null);
 // A refresh empties `data` for a moment; the page keeps showing the last answer meanwhile, so nothing flashes or loses its place.
 const last=useRef<Services|undefined>(undefined);
 if(data.data?.data)last.current=data.data.data;
 const current=last.current,now=Date.now();
 // A service that is starting, waking or stopping is asked about again every few seconds, and the page refreshes itself once a minute while open.
 const waiting=current?.services.some(changing);
 useEffect(()=>{const timer=setTimeout(data.refresh,waiting?3000:60_000);return()=>clearTimeout(timer);},[waiting,data.data,data.error]);
 async function send(id:ServiceId,body:unknown){
  const route=SWITCH_ROUTE[id];if(!route)return;
  setBusy(true);setError('');setAsk(null);
  try{await request(path+route,'PUT',body);data.refresh();}catch(failure){setError((failure as Error).message);}finally{setBusy(false);}
 }
 /** Turning a service off asks first when something is open, judged from a fresh answer, not from what the page loaded a minute ago. */
 async function stopWithCheck(service:Service,body:unknown,ask_:{mode?:Mode;minutes?:number}={}){
  setBusy(true);setError('');
  try{
   const fresh=(await request(path+'/services','GET') as {data:Services}).data;
   const now=fresh.services.find(item=>item.id===service.id)??service;
   const text=confirmOff(now);
   if(text){setAsk({id:service.id,text,...ask_});data.refresh();}else{await request(path+SWITCH_ROUTE[service.id],'PUT',body);data.refresh();}
  }catch(failure){setError((failure as Error).message);}finally{setBusy(false);}
 }
 function toggle(service:Service){
  if(!isOn(service)){void send(service.id,{enabled:true});return;}
  void stopWithCheck(service,{enabled:false});
 }
 function apply(service:Service,mode:Mode,minutes:number){
  const body=mode==='on_demand'?{mode,idle_minutes:minutes}:{mode};
  if(mode==='off'&&(service.state==='on'||service.state==='starting'))void stopWithCheck(service,body,{mode,minutes});
  else void send(service.id,body);
 }
 const byId=(id:ServiceId)=>current?.services.find(item=>item.id===id);
 const stale=current?staleText(current.stats,now,ago):null;
 const free=current?.capacity.availableBytes;
 const locked=busy||!current?.canChange;
 const anyOnDemand=!!current?.killSwitch&&current.services.some(item=>item.mode==='on_demand');
 const confirmBody=(service:Service)=>ask?.mode?(ask.mode==='on_demand'?{mode:ask.mode,idle_minutes:ask.minutes}:{mode:ask.mode}):{enabled:false};
 return <section className="aw">
  <div className="aw-top"><div><h2>Services</h2><p className="aw-hint">The optional services of this environment, and only this one. An unused service holds memory for nothing, so turn on what you use, or let it start only when something asks for it.</p></div>
   {free!==null&&free!==undefined&&<span className="aw-chip" title="Memory the server can still give to new services"><bdi>{memory(free)}</bdi> free</span>}</div>
  <ErrorMessage message={error||data.error}/>
  {!current&&data.loading?<Loading/>:current&&<div className="aw-layout">
   <nav className="aw-nav" aria-label="Services pages"><button type="button" className={tab==='overview'?'active':''} aria-current={tab==='overview'?'page':undefined} onClick={()=>setTab('overview')}>Overview</button>
    {ORDER.map(id=><button key={id} id={'svc-tab-'+id} type="button" className={tab===id?'active':''} aria-current={tab===id?'page':undefined} onClick={()=>setTab(id)}>{NAMES[id]}</button>)}</nav>
   <div className="aw-pane">
    {stale&&<p className="aw-notice" role="status">{stale}</p>}
    {anyOnDemand&&<p className="aw-notice" role="status">The installation operator has turned On demand off for now. Services saved as On demand are kept running, and your choices are kept.</p>}
    {!current.canChange&&<p className="aw-hint svc-readonly">You can see these services. Owners and admins can change them.</p>}
    {tab==='overview'&&<>
     <h3>Overview</h3><p className="aw-hint">What runs for this environment now, what it holds in memory and when it was last used.</p>
     <div className="aw-group svc-list">{ORDER.map(id=>{const service=byId(id);if(!service)return null;
      const onDemand=service.mode==='on_demand';
      return <div key={id} className="svc-item"><div className="aw-row svc-row">
       <div className="aw-text"><button type="button" className="svc-name" onClick={()=>setTab(id)}>{NAMES[id]}{hasMode(service)&&<span className="svc-mode-tag">{MODE_LABEL[service.mode!]}</span>}</button>
        <p className="aw-hint"><bdi>{memoryText(service)}</bdi><span aria-hidden="true"> · </span>Last used: <bdi>{lastUsedText(service,now,ago)}</bdi></p>
        {onDemand&&<p className="aw-hint svc-line">{onDemandLine(service.idleMinutes??30)}</p>}</div>
       <Chip service={service}/>
       {SWITCH_ROUTE[id]&&!hasMode(service)?<Switcher service={service} disabled={locked} onToggle={toggle}/>:hasMode(service)?<button type="button" className="aw-ghost svc-open" onClick={()=>setTab(id)}>{current.canChange?'Mode':'Details'}</button>:<span className="svc-spacer" aria-hidden="true"/>}</div>
       {unusedText(service)&&<Hint service={service} busy={locked} canAct={current.canChange} onOff={()=>void apply(service,'off',service.idleMinutes??30)}/>}
       {ask?.id===id&&<Confirm service={service} text={ask.text} busy={busy} onKeep={()=>setAsk(null)} onOff={()=>void send(id,confirmBody(service))}/>}</div>;})}</div>
    </>}
    {tab!=='overview'&&(()=>{const service=byId(tab);if(!service)return null;const cost=service.state==='off'||service.state==='sleeping'?costText(service,current.capacity):null,owner=current.canChange?OWNER_SECTION[tab]:undefined;
     return <>
      <div className="aw-top"><div><h3>{NAMES[tab]}</h3><p className="aw-hint aw-lead">{ABOUT[tab]}</p></div><Chip service={service}/></div>
      {service.state==='failed'&&service.failure&&<p className="aw-notice" role="alert">{service.failure}</p>}
      {STATE_NOTE[service.state]&&<p className="aw-hint svc-state-note" role="status">{STATE_NOTE[service.state]}</p>}
      <dl className="svc-facts"><div><dt>Memory now</dt><dd><bdi>{memoryText(service)}</bdi></dd></div><div><dt>Last used</dt><dd><bdi>{lastUsedText(service,now,ago)}</bdi></dd></div>
       {service.inUse!==null&&<div><dt>{service.id==='realtime'?'Open connections':'Calls running'}</dt><dd>{service.inUse}</dd></div>}</dl>
      {hasMode(service)&&<ModeControl service={service} canChange={current.canChange} busy={busy} limits={current.limits} costNote={cost?costText(service,current.capacity):null} onApply={(mode,minutes)=>apply(service,mode,minutes)}/>}
      {SWITCH_ROUTE[tab]&&!hasMode(service)&&<div className="aw-group"><div className="aw-row"><div className="aw-text"><span className="svc-state">{isOn(service)?'On':'Off'}</span>
        <p className="aw-hint">{cost??COST_NOTE[tab]}</p></div>
        <Switcher id={'svc-'+tab} service={service} disabled={locked} onToggle={toggle}/></div></div>}
      {unusedText(service)&&<Hint service={service} busy={locked} canAct={current.canChange} onOff={()=>void apply(service,'off',service.idleMinutes??30)}/>}
      {ask?.id===tab&&<Confirm service={service} text={ask.text} busy={busy} onKeep={()=>setAsk(null)} onOff={()=>void send(tab,confirmBody(service))}/>}
      {(cost||owner)&&!hasMode(service)&&<p className="aw-hint svc-note">{COST_NOTE[tab]}</p>}
      {hasMode(service)&&<p className="aw-hint svc-note">{COST_NOTE[tab]}</p>}
      {owner&&<button type="button" className="aw-ghost" onClick={()=>go(owner.section)}>{owner.label}</button>}
     </>;})()}
   </div>
  </div>}
 </section>;
}
