import {useEffect,useState} from 'react';
import {ArrowLeft,Copy,Plus,ExternalLink,Play,Square} from 'lucide-react';
import {useData,type Api,type Organization,type Environment} from './api';
import {mailDetails,mailState,type MailEntry} from './mail';
import {ErrorMessage,Loading,Empty,Refresh} from './components';
import {SignInSection} from './SignIn';
import {RealtimeSection} from './Realtime';
type Key={id:string;kind:string;created_at:number;revoked_at:number|null};
type Studio={desired:'running'|'stopped';state:'stopped'|'starting'|'running'|'failed';failure:string|null};
const studioLabels:Record<string,string>={stopped:'Stopped',starting:'Starting',running:'Running',failed:'Failed'};
/** Supabase Studio for this environment: started on demand, opened with the console's own login. */
function StudioSection({path,request}:{path:string;request:Api}){
 const studio=useData<{data:Studio}>(signal=>request(path+'/studio','GET',undefined,signal),[path,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const current=studio.data?.data;
 const waiting=!!current&&(current.desired==='running'?current.state!=='running'&&!(current.state==='failed'&&current.failure):current.state!=='stopped'&&current.state!=='failed');
 useEffect(()=>{if(!waiting)return;const timer=setTimeout(studio.refresh,3000);return()=>clearTimeout(timer);},[waiting,studio.data]);
 async function change(method:'POST'|'DELETE'){setBusy(true);setError('');try{await request(path+'/studio',method);studio.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function open(){
  // Opened before the request so the browser does not treat it as an unrequested pop-up.
  const tab=window.open('about:blank','_blank');setError('');
  try{const {host,path:entry}=await request(path+'/studio/session','POST') as {host:string;path:string};
   const url=`${location.protocol}//${host}${location.port?':'+location.port:''}${entry}`;
   if(tab){tab.opener=null;tab.location.href=url;}else location.href=url;}
  catch(e){tab?.close();setError((e as Error).message);}
 }
 const label=current?(waiting?(current.desired==='running'?'Starting':'Stopping'):studioLabels[current.state]??'Unknown'):'';
 return <section className="details"><h2>Studio</h2><p className="muted small">Supabase Studio for this environment: tables, SQL editor, users and storage. It starts when you need it and uses your console sign-in.</p>
 <ErrorMessage message={error||studio.error}/>{studio.loading?<Loading/>:current&&<><p><span className={'state '+(current.state==='running'&&!waiting?'succeeded':current.state==='failed'?'failed':waiting?'running':'')}>{label}</span></p>
 {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
 <div className="form-row">{current.state==='running'&&current.desired==='running'?<><button className="primary" onClick={()=>void open()}><ExternalLink aria-hidden="true"/>Open Studio</button><button disabled={busy} onClick={()=>void change('DELETE')}><Square aria-hidden="true"/>Stop</button></>
 :<button className="primary" disabled={busy||waiting} onClick={()=>void change('POST')}><Play aria-hidden="true"/>{waiting?'Starting…':'Start Studio'}</button>}</div>
 <p className="small muted">Studio opens at its own address on this console's port. From another computer, reach the console through an SSH tunnel to the server.</p></>}</section>;
}
export function Connection({environment,organization,request,onBack}:{environment:Environment;organization:Organization;request:Api;onBack:()=>void}){
 const path=`/environments/${environment.id}`,canWrite=organization.role!=='viewer';
 const info=useData<{apiPath:string;services:string[]}>(signal=>request(path+'/connection','GET',undefined,signal),[environment.id,request]);
 const keys=useData<{data:Key[]}>(signal=>canWrite?request(path+'/keys','GET',undefined,signal):Promise.resolve({data:[]}),[environment.id,request,canWrite]);
 const mail=useData<{data:MailEntry}>(signal=>request(path+'/mail','GET',undefined,signal),[environment.id,request]);
 const mailView=mailState(mail.data?.data),mailRows=mailDetails(mail.data?.data);
 const [raw,setRaw]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[confirm,setConfirm]=useState<string>(),[copied,setCopied]=useState('');
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the value manually.');}}
 async function issue(){setBusy(true);setError('');try{const result=await request(path+'/keys','POST');setRaw(result.token);keys.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function revoke(id:string){setBusy(true);setError('');try{await request(path+'/keys/'+id,'DELETE');setConfirm(undefined);setRaw('');keys.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <><button className="back" onClick={onBack}><ArrowLeft aria-hidden="true"/>Environments</button><div className="page-heading"><div><h1>{environment.name}</h1><p className="muted">Connect your application.</p></div></div>
 <ErrorMessage message={info.error}/>{info.error&&<Refresh onClick={info.refresh}/>} {info.loading?<Loading/>:info.data&&<section className="details"><h2>Connection</h2><label htmlFor="project-url">Project URL</label><div className="form-row"><input id="project-url" readOnly value={location.origin+info.data.apiPath}/><button onClick={()=>copy(location.origin+info.data!.apiPath)}><Copy aria-hidden="true"/>Copy URL</button></div><p className="small muted">Available services: {info.data.services.join(', ')}</p></section>}
 <section className="details"><h2>Email</h2><ErrorMessage message={mail.error}/>{mail.error&&<Refresh onClick={mail.refresh}/>} {mail.loading?<Loading/>:<><p><span className={'state '+mailView.state}>{mailView.label}</span></p><p className="muted small">{mailView.text}</p>{mailRows.map(row=><p className="small" key={row.label}><span className="muted">{row.label}: </span>{row.value}</p>)}</>}</section>
 {canWrite&&<SignInSection path={path} request={request}/>}
 {canWrite&&<RealtimeSection path={path} request={request}/>}
 {canWrite&&<StudioSection path={path} request={request}/>}
 <section className="keys-section"><div className="page-heading"><div><h2>Publishable keys</h2><p className="muted small">Use these in your application. Row level security still applies.</p></div>{canWrite&&<button className="primary" onClick={issue} disabled={busy||!!raw}><Plus aria-hidden="true"/>Create key</button>}</div>
 <ErrorMessage message={error||keys.error}/>{raw&&<div className="new-key"><label htmlFor="new-key">Save this key now. It is only shown once.</label><textarea id="new-key" readOnly value={raw}/><div className="form-row"><button onClick={()=>copy(raw)}><Copy aria-hidden="true"/>Copy key</button><button onClick={()=>{setRaw('');setCopied('');}}>I have saved this key</button></div></div>}
 <p className="small muted" role="status">{copied}</p>{!canWrite?<Empty>An organization owner or admin can manage keys.</Empty>:keys.loading?<Loading/>:keys.data?.data.length?<div className="table-wrap"><table><thead><tr><th>Key ID</th><th>Created</th><th>Status</th><th>Action</th></tr></thead><tbody>{keys.data.data.map(key=><tr key={key.id}><td><code>{key.id.slice(0,8)}</code></td><td>{new Date(key.created_at).toLocaleDateString()}</td><td>{key.revoked_at?'Revoked':'Active'}</td><td>{!key.revoked_at&&(confirm===key.id?<div className="confirm"><span>Revoke this key?</span><button className="danger" disabled={busy} onClick={()=>revoke(key.id)}>Confirm revoke</button><button onClick={()=>setConfirm(undefined)}>Cancel</button></div>:<button disabled={busy} onClick={()=>setConfirm(key.id)}>Revoke</button>)}</td></tr>)}</tbody></table></div>:!keys.error&&<Empty>No publishable keys yet.</Empty>}</section></>;
}
