import {useState} from 'react';
import {Copy,Plus,KeyRound,ShieldAlert} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading,Empty} from './components';

type Key={id:string;kind:'publishable'|'secret';name:string;created_at:number;revoked_at:number|null;token?:string|null};
const NAME=/^[a-z_][a-z0-9_]{3,63}$/;

/** Publishable and secret API keys of one environment (docs/guides/api-keys.md). A secret key is
 * shown once, when it is created; the server keeps only its digest. A publishable key can be
 * copied again (one issued before 0.5.0 cannot: only its digest was kept). */
export function ApiKeysSection({path,request,canWrite}:{path:string;request:Api;canWrite:boolean}){
 const keys=useData<{data:Key[]}|undefined>(signal=>canWrite?request(path+'/keys','GET',undefined,signal):Promise.resolve(undefined),[path,request,canWrite]);
 const [raw,setRaw]=useState<{token:string;kind:string}>(),[busy,setBusy]=useState(false),[error,setError]=useState(''),[confirm,setConfirm]=useState<string>(),[copied,setCopied]=useState('');
 const [name,setName]=useState(''),[naming,setNaming]=useState(false);
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the value manually.');}}
 async function issue(kind:'publishable'|'secret',keyName?:string){
  setBusy(true);setError('');setCopied('');
  try{const result=await request(path+'/keys','POST',{type:kind,...(keyName?{name:keyName}:{})});setRaw({token:result.token,kind});setName('');setNaming(false);keys.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function revoke(id:string){setBusy(true);setError('');try{await request(path+'/keys/'+id,'DELETE');setConfirm(undefined);keys.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const nameError=name&&!NAME.test(name)?'Use 4 to 64 lowercase letters, digits or underscores, not starting with a digit.':'';
 function table(kind:'publishable'|'secret'){
  const rows=keys.data?.data.filter(key=>key.kind===kind)??[];
  if(!rows.length)return !keys.error&&<Empty>{kind==='secret'?'No secret keys yet.':'No publishable keys yet.'}</Empty>;
  return <div className="table-wrap"><table><thead><tr><th>Name</th><th>Key ID</th><th>Created</th><th>Status</th><th>Action</th></tr></thead><tbody>{rows.map(key=><tr key={key.id}>
   <td>{key.name}</td><td><code>{key.id.slice(0,8)}</code></td><td>{new Date(key.created_at).toLocaleDateString()}</td><td>{key.revoked_at?'Revoked':'Active'}</td>
   <td>{!key.revoked_at&&key.token&&confirm!==key.id&&<button onClick={()=>void copy(key.token!)}><Copy aria-hidden="true"/>Copy</button>}{!key.revoked_at&&(confirm===key.id?<div className="confirm"><span>Revoke this key? Anything using it stops working at once.</span><button className="danger" disabled={busy} onClick={()=>void revoke(key.id)}>Confirm revoke</button><button onClick={()=>setConfirm(undefined)}>Cancel</button></div>
    :<button disabled={busy} onClick={()=>setConfirm(key.id)}>Revoke</button>)}</td></tr>)}</tbody></table></div>;
 }
 if(!canWrite)return <Empty>An organization owner or admin can manage keys.</Empty>;
 return <div>
  <ErrorMessage message={error||keys.error}/>
  {raw&&<div className="new-key"><label htmlFor="new-key">Save this {raw.kind} key now. It is only shown once.</label><textarea id="new-key" readOnly value={raw.token}/>
   <div className="form-row"><button onClick={()=>void copy(raw.token)}><Copy aria-hidden="true"/>Copy key</button><button onClick={()=>{setRaw(undefined);setCopied('');}}>I have saved this key</button></div></div>}
  <p className="small muted" role="status">{copied}</p>
  <section className="keys-section"><div className="page-heading"><div><h2>Publishable keys</h2><p className="muted small">Use these in your application, in the browser or a mobile app. Row level security still applies. A publishable key is public by design, so it can be copied again here.</p></div>
   <button className="primary" onClick={()=>void issue('publishable')} disabled={busy||!!raw}><Plus aria-hidden="true"/>Create key</button></div>
   {keys.loading?<Loading/>:table('publishable')}</section>
  <section className="keys-section"><div className="page-heading"><div><h2>Secret keys</h2><p className="muted small">For your server, scripts, Edge Functions and AI agents. A secret key acts as <code>service_role</code>: it bypasses row level security and can manage users.</p></div>
   {!naming&&<button onClick={()=>setNaming(true)} disabled={busy||!!raw}><KeyRound aria-hidden="true"/>Create secret key</button>}</div>
   <p className="small callout"><ShieldAlert aria-hidden="true"/> Never put a secret key in a web page or a mobile app. Browsers are refused, and anyone holding the key can read and change all data. Give each server or agent its own key, so you can revoke one without the others.</p>
   {naming&&<form className="form-row" onSubmit={event=>{event.preventDefault();if(!nameError&&name)void issue('secret',name);}}>
    <label className="sr-only" htmlFor="secret-name">Key name</label>
    <input id="secret-name" value={name} autoComplete="off" spellCheck={false} placeholder="backend" aria-invalid={Boolean(nameError)} aria-describedby={nameError?'secret-name-error':undefined} onChange={event=>setName(event.target.value.trim())}/>
    <button type="submit" className="primary" disabled={busy||!name||Boolean(nameError)}>Create</button><button type="button" onClick={()=>{setNaming(false);setName('');}}>Cancel</button>
   </form>}
   {nameError&&<p id="secret-name-error" className="field-error" role="alert">{nameError}</p>}
   {keys.loading?<Loading/>:table('secret')}</section>
 </div>;
}
