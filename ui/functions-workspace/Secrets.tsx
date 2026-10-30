import {useState} from 'react';
import {Eye,EyeOff,KeyRound,Search,Trash2} from 'lucide-react';
import {ApiError,type Api} from '../api';
import {Loading} from '../components';
import {secretErrors,type FieldErrors} from '../settings-validation';
import {useLive} from './live';
import type {DefaultSecret,SecretRow} from './types';
import {Chip,CopyButton,Notice,Problem} from './kit';
import {ago} from './format';

/** Secrets of this environment's functions: names, a digest that shows whether a value changed without showing it, and the
 * variables every function gets without one. A value is written once and never read back. */
export function Secrets({path,request,onChanged}:{path:string;request:Api;onChanged:()=>void}){
 const data=useLive<{data:{secrets:SecretRow[];defaults:DefaultSecret[]}}>(signal=>request(path+'/function-secrets','GET',undefined,signal),[path]);
 const [name,setName]=useState(''),[value,setValue]=useState(''),[shown,setShown]=useState(false),[fields,setFields]=useState<FieldErrors>({}),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
 const [query,setQuery]=useState(''),[confirm,setConfirm]=useState('');
 const rows=(data.data?.data.secrets??[]).filter(row=>!query.trim()||row.name.toLowerCase().includes(query.trim().toLowerCase()));
 const invalid=Object.keys(secretErrors(name,value)).length>0;
 async function change(changes:Record<string,string|null>,done:()=>void){
  setBusy(true);setError('');setSaved('');
  try{await request(path+'/function-secrets','PUT',{secrets:changes});done();data.refresh();onChanged();}
  catch(reason){setError(reason instanceof ApiError?(reason.serverMessage?`Check the ${reason.serverMessage.replace(/^Check the /,'')}`:reason.message):(reason as Error).message);}
  finally{setBusy(false);}
 }
 async function save(){
  const problems=secretErrors(name,value);setFields(problems);
  const first=Object.keys(problems)[0];if(first){document.getElementById(first)?.focus();return;}
  await change({[name]:value},()=>{setSaved(`Saved ${name}. It applies to the next call.`);setName('');setValue('');});
 }
 return <div className="fw-stack">
  <div className="fw-page-head"><div><h2>Edge Function Secrets</h2><p className="fw-hint">Values your functions read with <code>Deno.env.get('NAME')</code>. A value is kept in a private file on the server and never shown again; the digest tells you whether it changed.</p></div></div>
  <Problem message={error||data.error} retry={data.error?data.refresh:undefined}/>
  <section className="fw-card" aria-label="Add or replace secrets"><h3>ADD OR REPLACE SECRETS</h3>
   <div className="fw-field stack"><div className="fw-text"><label className="fw-label" htmlFor="secret-name">Name</label><p className="fw-hint">A unique name for your secret. Saving an existing name replaces its value.</p></div>
    <input id="secret-name" autoComplete="off" spellCheck={false} placeholder="e.g. CLIENT_KEY" maxLength={128} value={name} aria-invalid={Boolean(fields['secret-name'])} aria-describedby={fields['secret-name']?'secret-name-error':undefined}
     onChange={event=>{const next=event.target.value.toUpperCase();setName(next);const message=next?secretErrors(next,value)['secret-name']:undefined;setFields(message?{'secret-name':message}:{});}}/></div>
   {fields['secret-name']&&<p id="secret-name-error" className="fw-error" role="alert">{fields['secret-name']}</p>}
   <div className="fw-field stack"><div className="fw-text"><label className="fw-label" htmlFor="secret-value">Value</label><p className="fw-hint">Supports multi-line values such as PEM keys, JSON, or functions.</p></div>
    <div className="fw-secret-input"><textarea id="secret-value" className={shown?'':'masked'} rows={3} autoComplete="off" spellCheck={false} maxLength={65536} value={value} aria-invalid={Boolean(fields['secret-value'])}
     aria-describedby={fields['secret-value']?'secret-value-error':undefined} onChange={event=>{setValue(event.target.value);const problems=secretErrors(name,event.target.value);if(!name)delete problems['secret-name'];setFields(problems);}}/>
     <button type="button" className="fw-icon" aria-label={shown?'Hide the value':'Show the value'} aria-pressed={shown} onClick={()=>setShown(!shown)}>{shown?<EyeOff aria-hidden="true"/>:<Eye aria-hidden="true"/>}</button></div></div>
   {fields['secret-value']&&<p id="secret-value-error" className="fw-error" role="alert">{fields['secret-value']}</p>}
   <div className="fw-card-foot"><span className="fw-hint" role="status">{saved}</span><button type="button" className="fw-primary" disabled={busy||invalid} onClick={()=>void save()}><KeyRound aria-hidden="true"/>Save</button></div></section>
  <section className="fw-card" aria-label="Custom secrets"><div className="fw-card-head"><div><h3>Custom secrets</h3><p className="fw-hint">Secrets you have defined for this environment.</p></div>
   <label className="fw-search"><Search aria-hidden="true"/><span className="fw-sr">Search for a secret</span><input type="search" placeholder="Search for a secret" value={query} onChange={event=>setQuery(event.target.value)}/></label></div>
   {data.loading&&!data.data?<Loading/>:rows.length?<div className="fw-rows secrets" role="table" aria-label="Custom secrets">
    <div className="fw-row head" role="row"><span role="columnheader">NAME</span><span role="columnheader">DIGEST <Chip>HMAC-SHA256</Chip></span><span role="columnheader">UPDATED</span><span role="columnheader" className="fw-sr">Actions</span></div>
    {rows.map(row=><div className="fw-row" role="row" key={row.name}><span role="cell"><code>{row.name}</code></span>
     <span role="cell" className="fw-digest"><code title={row.digest}>{row.digest.slice(0,16)}…</code><CopyButton quiet text={row.digest} label={`Copy the digest of ${row.name}`}/></span>
     <span role="cell">{row.updated_at?ago(row.updated_at):'unknown'}</span>
     <span role="cell" className="fw-end">{confirm===row.name?<span className="fw-inline-confirm"><span>Delete {row.name}?</span><button type="button" className="fw-danger-solid" disabled={busy} onClick={()=>void change({[row.name]:null},()=>setConfirm(''))}>Delete</button><button type="button" className="fw-ghost" onClick={()=>setConfirm('')}>Cancel</button></span>
      :<button type="button" className="fw-icon" aria-label={`Delete ${row.name}`} disabled={busy} onClick={()=>setConfirm(row.name)}><Trash2 aria-hidden="true"/></button>}</span></div>)}</div>
    :<div className="fw-empty-block"><strong>{query.trim()?'No secret matches your search':'No custom secrets created'}</strong><p className="fw-hint">{query.trim()?'Try another name.':'This environment has no custom secrets yet.'}</p></div>}
   <p className="fw-hint">The digest is an HMAC with a key that belongs to this environment, so the same value gives a different digest in another environment, and a short secret cannot be guessed from it.</p></section>
  <section className="fw-card" aria-label="Default secrets"><div className="fw-card-head"><div><h3>Default secrets</h3><p className="fw-hint">What every function can read without you adding anything.</p></div></div>
   <div className="fw-rows defaults" role="table" aria-label="Default secrets"><div className="fw-row head" role="row"><span role="columnheader">NAME</span><span role="columnheader">DESCRIPTION</span></div>
    {(data.data?.data.defaults??[]).map(item=><div className="fw-row" role="row" key={item.name}><span role="cell"><code>{item.name}</code></span>
     <span role="cell">{item.description} {item.provided==='no'?<Chip>Not provided here</Chip>:item.provided==='unknown'?<Chip>Set by the runtime</Chip>:null}</span></div>)}</div>
   <Notice tone="info">A function can use <code>SUPABASE_SERVICE_ROLE_KEY</code> for this whole environment, as on Supabase. The boundary is the environment, not the function.</Notice></section>
 </div>;
}
