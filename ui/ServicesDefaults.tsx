import {useEffect,useState} from 'react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {Switch} from './AwSwitch';
import {arrow} from './Services';
import {limitsText} from './services-view';
import './auth-workspace.css';
import './services.css';

type Settings={unusedDays:number;killSwitch:boolean;realtimeOnDemand:boolean;defaultMode:'off'|'on_demand';idleMinutes:{realtime:number;functions:number};limits:{wakesPerHour:number;holdSeconds:number}};
const MIN_IDLE=5;
const whole=(text:string,minimum:number,maximum=Infinity)=>Number.isInteger(Number(text))&&text.trim()!==''&&Number(text)>=minimum&&Number(text)<=maximum;

/** Installation wide settings of the optional services, for operators: the kill switch for On demand, what a new environment starts with, the default quiet
 * periods, and the days before an unused service is reported. The wake limits are shown and cannot be changed here (docs/guides/services.md). */
export function ServicesDefaults({request}:{request:Api}){
 const data=useData<{data:Settings}>(signal=>request('/installation/services','GET',undefined,signal),[request]);
 const current=data.data?.data;
 const [mode,setMode]=useState<'off'|'on_demand'>('off'),[realtime,setRealtime]=useState('30'),[functions,setFunctions]=useState('15'),[days,setDays]=useState('7');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
 const [confirmKill,setConfirmKill]=useState(false);
 // Only a value that really changed replaces what is being edited, so a refresh after another switch never drops an unsaved draft.
 useEffect(()=>{if(current)setMode(current.defaultMode);},[current?.defaultMode]);
 useEffect(()=>{if(current)setRealtime(String(current.idleMinutes.realtime));},[current?.idleMinutes.realtime]);
 useEffect(()=>{if(current)setFunctions(String(current.idleMinutes.functions));},[current?.idleMinutes.functions]);
 useEffect(()=>{if(current)setDays(String(current.unusedDays));},[current?.unusedDays]);
 async function put(body:unknown,message:string){
  setBusy(true);setError('');setSaved('');
  try{await request('/installation/services','PUT',body);data.refresh();setSaved(message);}catch(failure){setError((failure as Error).message);}finally{setBusy(false);}
 }
 const valid=whole(realtime,MIN_IDLE,10080)&&whole(functions,MIN_IDLE,10080)&&whole(days,1,365);
 const changed=!!current&&(mode!==current.defaultMode||Number(realtime)!==current.idleMinutes.realtime||Number(functions)!==current.idleMinutes.functions||Number(days)!==current.unusedDays);
 return <section className="aw svc-defaults" aria-labelledby="svc-defaults-title">
  <div className="aw-top"><div><h2 id="svc-defaults-title">Optional services</h2><p className="aw-hint">Installation wide settings for Realtime and Edge Functions. Each environment can still choose its own mode on its Services page.</p></div></div>
  <ErrorMessage message={error||data.error}/>
  {!current&&data.loading?<Loading/>:current&&<div className="aw-pane">
   <h3>Kill switch</h3>
   <div className="aw-group"><Switch id="svc-kill" checked={current.killSwitch} disabled={busy} label="Keep every On demand service on"
    hint="Turns every On demand service into On while this is set: sleeping services start, and nothing goes to sleep. Nobody's saved choice is changed, so turning it off puts them back to sleeping on their own."
    onChange={value=>{if(value)setConfirmKill(true);else void put({killSwitch:false},'On demand is back.');}}/></div>
   {confirmKill&&<div className="aw-notice" role="alertdialog" aria-label="Turn on the kill switch"><p className="svc-ask">This starts every sleeping On demand service on this server, one at a time, and keeps them running until you turn this off again. Each one needs its memory (Realtime about 320 MiB, Edge Functions about 384 MiB). Turn it on?</p>
    <div className="aw-actions"><button type="button" className="aw-ghost" onClick={()=>setConfirmKill(false)}>Cancel</button>
     <button type="button" className="aw-primary" disabled={busy} onClick={()=>{setConfirmKill(false);void put({killSwitch:true},'On demand is paused. Sleeping services are starting, one at a time.');}}>Turn it on</button></div></div>}
   <h3 className="svc-h">Realtime On demand</h3>
   <div className="aw-group"><Switch id="svc-realtime-od" checked={current.realtimeOnDemand} disabled={busy} label="Allow On demand for Realtime"
    hint="Off by default. Realtime's own start takes about 12 to 15 seconds on the machine it was measured on (Edge Functions: 2 to 3), longer than a client waits comfortably for its first connection. Allow it only if it starts faster on your server."
    onChange={value=>void put({realtimeOnDemand:value},value?'Environments can now choose On demand for Realtime.':'New choices of On demand for Realtime are closed. Environments that already chose it keep it.')}/></div>
   <h3 className="svc-h">New environments</h3>
   <p className="aw-hint">What a new environment gets for a service that can be On demand (Edge Functions; Realtime only if allowed below). Realtime otherwise starts Off. Environments that already exist keep what they have.</p>
   <div className="aw-sub svc-radios" role="radiogroup" aria-label="Mode of new environments">
    {([['off','Off'],['on_demand','On demand']] as const).map(([value,label])=><button key={value} type="button" role="radio" aria-checked={mode===value} tabIndex={mode===value?0:-1} className={mode===value?'active':''} disabled={busy}
     onClick={()=>setMode(value)} onKeyDown={event=>arrow(event,['off','on_demand'] as const,mode,setMode)}>{label}</button>)}</div>
   <div className="svc-grid">
    <div className="aw-field"><label htmlFor="svc-idle-realtime">Realtime quiet period (minutes)</label><input id="svc-idle-realtime" type="number" min={MIN_IDLE} max={10080} step={1} value={realtime} disabled={busy} aria-invalid={!whole(realtime,MIN_IDLE)} onChange={event=>setRealtime(event.target.value)}/></div>
    <div className="aw-field"><label htmlFor="svc-idle-functions">Edge Functions quiet period (minutes)</label><input id="svc-idle-functions" type="number" min={MIN_IDLE} max={10080} step={1} value={functions} disabled={busy} aria-invalid={!whole(functions,MIN_IDLE)} onChange={event=>setFunctions(event.target.value)}/></div>
    <div className="aw-field"><label htmlFor="svc-unused">Days before an unused service is reported</label><input id="svc-unused" type="number" min={1} max={365} step={1} value={days} disabled={busy} aria-invalid={!whole(days,1)||Number(days)>365} onChange={event=>setDays(event.target.value)}/></div></div>
   <p className="aw-hint">Quiet periods are {MIN_IDLE} to 10080 minutes (a week). The days are 1 to 365.</p>
   <div className="aw-actions svc-apply"><button type="button" className="aw-primary" disabled={busy||!valid||!changed}
    onClick={()=>void put({defaultMode:mode,idleMinutes:{realtime:Number(realtime),functions:Number(functions)},unusedDays:Number(days)},'Saved. It applies to environments created from now on.')}>Save</button></div>
   <p className="aw-hint" role="status">{saved}</p>
   <h3 className="svc-h">Limits</h3>
   <p className="aw-hint">{limitsText({wakesPerHour:current.limits.wakesPerHour,holdSeconds:current.limits.holdSeconds,idleFloorMinutes:MIN_IDLE})} These are set by Sbarbase and are not editable here.</p>
  </div>}
 </section>;
}
