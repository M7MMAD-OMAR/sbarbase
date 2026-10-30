import {useEffect,useRef,useState} from 'react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {ago} from './functions-workspace/format';
import {CACHE_GUARANTEE,LEVEL_CLASS,LEVEL_LABEL,actionText,busy,cacheText,diskNote,filesystemLabel,memoryNote,size,swapNote,type Capacity} from './capacity-view';
import './auth-workspace.css';
import './services.css';

/** The Server page's room and cleanup for operators: memory and disk against the numbers the supervisor enforces, and Docker's build cache with a button
 * that clears only that (docs/guides/services.md). The numbers come from the supervisor's own file, never a copy of its thresholds. */
export function ServerCapacity({request}:{request:Api}){
 const data=useData<{data:Capacity}>(signal=>request('/server/capacity','GET',undefined,signal),[request]);
 const [working,setWorking]=useState(false),[error,setError]=useState(''),[ask,setAsk]=useState(false);
 const capacity=data.data?.data,now=Date.now();
 const asking=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(ask)asking.current?.focus();},[ask]);
 useEffect(()=>{if(!busy(capacity?.action??null))return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[capacity?.action?.state,data.data]);
 async function clear(){
  setWorking(true);setError('');setAsk(false);
  try{await request('/server/build-cache','POST');data.refresh();}catch(failure){setError((failure as Error).message);}finally{setWorking(false);}
 }
 const note=capacity?actionText(capacity.action,now,ago):null;
 return <section className="aw svc-defaults" aria-labelledby="capacity-title">
  <div className="aw-top"><div><h2 id="capacity-title">Room on this server</h2><p className="aw-hint">What a new environment or service can still get, against the limits the supervisor enforces, and Docker's build cache.</p></div></div>
  <ErrorMessage message={error||data.error}/>
  {!capacity&&data.loading?<Loading/>:capacity&&<div className="aw-pane">
   {capacity.stale&&<p className="aw-notice" role="status">The limits are out of date or not available yet. They are measured once a minute by the supervisor.</p>}
   <dl className="svc-facts">
    <div><dt>Memory available</dt><dd><bdi>{size(capacity.memory.availableBytes)}</bdi> <span className={'aw-chip '+LEVEL_CLASS[capacity.memory.level]}>{LEVEL_LABEL[capacity.memory.level]}</span></dd></div>
    {capacity.disk.filesystems.length?capacity.disk.filesystems.map(item=><div key={item.name}><dt>{filesystemLabel(item.name)}</dt><dd><bdi>{size(item.availableBytes)}</bdi> free <span className={'aw-chip '+LEVEL_CLASS[item.level]}>{LEVEL_LABEL[item.level]}</span></dd></div>)
     :<div><dt>Disk available</dt><dd><bdi>{size(capacity.disk.availableBytes)}</bdi> <span className={'aw-chip '+LEVEL_CLASS[capacity.disk.level]}>{LEVEL_LABEL[capacity.disk.level]}</span></dd></div>}</dl>
   <p className="aw-hint svc-effect">{memoryNote(capacity)}</p>
   <p className="aw-hint">{diskNote(capacity)}</p>
   <p className="aw-hint">{swapNote(capacity.memory.swapTotalBytes)}</p>
   <h3 className="svc-h">Docker build cache</h3>
   <p className="aw-hint svc-effect">{cacheText(capacity,now,ago)}</p>
   <p className="aw-hint">{CACHE_GUARANTEE}</p>
   {note&&<p className={capacity.action?.state==='failed'?'aw-error':'aw-hint'} role={capacity.action?.state==='failed'?'alert':'status'}>{note}</p>}
   <div className="aw-actions svc-apply"><button type="button" className="aw-ghost" disabled={working||busy(capacity.action,now)||!capacity.buildCache||capacity.buildCache.beforeClear||capacity.buildCache.reclaimableBytes===0} onClick={()=>setAsk(true)}>Clear build cache</button></div>
   {ask&&capacity.buildCache&&<div ref={asking} tabIndex={-1} className="aw-notice" role="alertdialog" aria-label="Clear the build cache" onKeyDown={event=>{if(event.key==='Escape')setAsk(false);}}><p className="svc-ask">Clear up to {size(capacity.buildCache.reclaimableBytes)} of Docker build cache? Only the build cache is removed. The next image build, if there is one, will be slower once.</p>
    <div className="aw-actions"><button type="button" className="aw-ghost" onClick={()=>setAsk(false)}>Cancel</button><button type="button" className="aw-primary" disabled={working} onClick={()=>void clear()}>Yes, clear it</button></div></div>}
  </div>}
 </section>;
}
