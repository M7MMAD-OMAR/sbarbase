import {useEffect,useRef,useState} from 'react';
import {ExternalLink,Square} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
type Studio={desired:'running'|'stopped';state:'stopped'|'starting'|'running'|'failed';failure:string|null};
/** One click starts the dashboard and opens it after the worker confirms readiness. */
export function StudioSection({path,request,ready=true,compact=false,environmentName,autoOpen=false,autoOpenTab,onAutoOpenFinished}:{autoOpen?:boolean;autoOpenTab?:Window|null;onAutoOpenFinished?:()=>void;path:string;request:Api;ready?:boolean;compact?:boolean;environmentName?:string}){
 const studio=useData<{data:Studio}|null>(signal=>ready?request(path+'/studio','GET',undefined,signal):Promise.resolve(null),[path,request,ready]);
 const [busy,setBusy]=useState(false),[opening,setOpening]=useState(false),[automatic,setAutomatic]=useState(false),[error,setError]=useState(''),[tunnel,setTunnel]=useState(false);
 const autoStarted=useRef(false),autoCancelled=useRef(false),autoMode=useRef(false);
 const openingSnapshot=useRef<unknown>(null);
 const tab=useRef<Window|null>(autoOpenTab??null),mounted=useRef(true);
 const current=studio.data?.data;
 const running=current?.state==='running'&&current.desired==='running';
 const waiting=!!current&&(current.desired==='running'?current.state!=='running'&&!(current.state==='failed'&&current.failure):current.state!=='stopped'&&current.state!=='failed');
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;tab.current?.close();};},[]);
 useEffect(()=>{if(!ready||!waiting&&!opening)return;const timer=setTimeout(studio.refresh,2000);return()=>clearTimeout(timer);},[ready,waiting,opening,studio.data]);
 async function enter(){
  try{
   const {host,path:entry,public:reachable}=await request(path+'/studio/session','POST') as {host:string;path:string;public?:boolean};
   if(!mounted.current||autoMode.current&&autoCancelled.current){tab.current?.close();return;}
   // A Studio address on *.localhost works only through an SSH tunnel: from the console's public
   // address the browser could not reach it, so say how instead of opening a dead tab.
   const local=['localhost','127.0.0.1','[::1]'].includes(location.hostname)||location.hostname.endsWith('.localhost');
   if(!reachable&&!local){tab.current?.close();tab.current=null;setTunnel(true);return;}
   if(autoMode.current&&(!tab.current||tab.current.closed)){setError('The dashboard is ready. Choose Open Supabase dashboard to open it.');return;}
   const url=reachable?`https://${host}${entry}`:`${location.protocol}//${host}${location.port?':'+location.port:''}${entry}`;
   if(tab.current&&!tab.current.closed){tab.current.opener=null;tab.current.location.href=url;tab.current=null;}else location.href=url;
  }catch(reason){tab.current?.close();tab.current=null;if(mounted.current)setError((reason as Error).message);}
  finally{if(mounted.current){setBusy(false);setOpening(false);setAutomatic(false);if(autoMode.current)onAutoOpenFinished?.();}}
 }
 useEffect(()=>{if(!opening||busy)return;
  if(running){setBusy(true);void enter();}
  else if(studio.data!==openingSnapshot.current&&(studio.error||current?.state==='failed'&&current.failure)){tab.current?.close();tab.current=null;setOpening(false);setAutomatic(false);setError(studio.error||'The dashboard could not start. Try again.');if(autoMode.current)onAutoOpenFinished?.();}
 },[opening,busy,running,current,studio.error]);
 async function open(automatically=false){
  if(busy||opening)return;
  openingSnapshot.current=studio.data;autoMode.current=automatically;autoCancelled.current=false;setAutomatic(automatically);
  if(!automatically)tab.current=window.open('about:blank','_blank');if(tab.current&&!tab.current.closed){tab.current.opener=null;tab.current.document.title='Opening Supabase dashboard';tab.current.document.body.textContent='Starting Supabase Studio. This tab will open your dashboard when ready.';}
  setError('');setBusy(true);
  if(running){await enter();return;}
  try{if(current?.desired!=='running'||current.state==='failed')await request(path+'/studio','POST');if(!mounted.current||automatically&&autoCancelled.current)return;setOpening(true);studio.refresh();}
  catch(reason){tab.current?.close();tab.current=null;if(mounted.current){setAutomatic(false);setError((reason as Error).message);if(automatically)onAutoOpenFinished?.();}}
  finally{if(mounted.current)setBusy(false);}
 }
 useEffect(()=>{if(!autoOpen||autoStarted.current||!ready||studio.loading||studio.error||busy||opening||current?.desired==='stopped'&&waiting)return;
  autoStarted.current=true;if(autoOpenTab?.closed){onAutoOpenFinished?.();return;}void open(true);
 },[autoOpen,ready,studio.loading,studio.error,busy,opening,current,waiting]);
 function stay(){autoCancelled.current=true;tab.current?.close();tab.current=null;setAutomatic(false);setOpening(false);onAutoOpenFinished?.();}
 async function stop(){setBusy(true);setError('');try{await request(path+'/studio','DELETE');studio.refresh();}catch(reason){setError((reason as Error).message);}finally{setBusy(false);}}
 return <section className="details" aria-label="Supabase dashboard"><div className="page-heading"><div><h2>Supabase dashboard</h2><p className="muted small">{environmentName?`${environmentName}: `:''}Manage tables, SQL, users and storage in Supabase Studio.</p></div>
 <button className="primary" disabled={!ready||studio.loading||!!studio.error||busy||opening||current?.desired==='stopped'&&waiting} onClick={()=>void open()}><ExternalLink aria-hidden="true"/>{busy||opening?'Opening Supabase dashboard...':'Open Supabase dashboard'}</button></div>
 {(autoOpen||automatic)&&<div><p className="small muted" role="status">Your Supabase dashboard will open in its new tab when ready.</p><button onClick={stay}>Stay in console</button></div>}
 <ErrorMessage message={error||studio.error}/>
 {tunnel&&<div className="callout small" role="status"><p><strong>Studio is running, but this server gives it no public address yet.</strong> Open it through an SSH tunnel: run <code>ssh -N -L 8787:127.0.0.1:8787 you@your-server</code> (with the console's port), open <code>http://localhost:8787</code> and open the dashboard from there. Or the operator sets a public Studio address (<code>SBARBASE_STUDIO_HOST</code>, see the Studio guide).</p></div>}
 {ready&&studio.error&&<button disabled={studio.loading} onClick={()=>{setError('');studio.refresh();}}>Retry dashboard status</button>}
 {!ready?<p className="small muted" role="status">Available after environment provisioning finishes.</p>:studio.loading?<Loading/>:<>
 <p className="small muted" role="status">{opening?'Starting the dashboard. It will open automatically when ready.':running?'Dashboard ready. It uses your console sign-in.':waiting?'The dashboard is changing state. This page updates automatically.':current?.state==='failed'?'The dashboard could not start. Try opening it again.':'Opening starts Supabase Studio for this environment.'}</p>
 {!compact&&running&&<button disabled={busy||opening} onClick={()=>void stop()}><Square aria-hidden="true"/>Stop dashboard</button>}
 </>}
 </section>;
}
