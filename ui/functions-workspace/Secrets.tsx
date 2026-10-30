import {useEffect,useState} from 'react';
import {Eye,EyeOff,KeyRound,Plus,Search,Trash2} from 'lucide-react';
import {looksLikeEnv,parseEnv} from './env';
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
 type Row={id:number;name:string;value:string;shown:boolean};
 const blank=(id:number):Row=>({id,name:'',value:'',shown:false});
 const [rows,setRows]=useState<Row[]>([blank(1)]),[next,setNext]=useState(2),[tried,setTried]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
 const [query,setQuery]=useState(''),[confirm,setConfirm]=useState(''),[focusId,setFocusId]=useState<number>(),[note,setNote]=useState('');
 useEffect(()=>{if(focusId===undefined)return;document.getElementById(`secret-name-${focusId}`)?.focus();setFocusId(undefined);},[focusId]);
 const list=(data.data?.data.secrets??[]).filter(row=>!query.trim()||row.name.toLowerCase().includes(query.trim().toLowerCase()));
 // A row left empty is ignored; a row with only a name or only a value is a mistake.
 const filled=rows.filter(row=>row.name||row.value);
 const problems=(row:Row):FieldErrors=>secretErrors(row.name,row.value);
 const invalid=filled.some(row=>Object.keys(problems(row)).length>0)||new Set(filled.map(row=>row.name)).size!==filled.length;
 const change=(id:number,patch:Partial<Row>)=>{setRows(current=>current.map(row=>row.id===id?{...row,...patch}:row));setSaved('');};
 async function submit(changes:Record<string,string|null>,done:()=>void){
  setBusy(true);setError('');setSaved('');
  try{await request(path+'/function-secrets','PUT',{secrets:changes});done();data.refresh();onChanged();}
  catch(reason){setError(reason instanceof ApiError?(reason.serverMessage?`Check the ${reason.serverMessage.replace(/^Check the /,'')}`:reason.message):(reason as Error).message);}
  finally{setBusy(false);}
 }
 async function save(){
  setTried(true);
  if(!filled.length||invalid)return;
  await submit(Object.fromEntries(filled.map(row=>[row.name,row.value])),()=>{setSaved(`Saved ${filled.length} secret${filled.length===1?'':'s'}. They apply to the next call.`);setRows([blank(next)]);setNext(next+1);setTried(false);});
 }
 /** Pasting several `NAME=value` lines into a name field fills one row for each. */
 function paste(id:number,text:string){
  const parsed=parseEnv(text);
  if(!parsed.length)return false;
  const pairs=parsed.slice(0,100);
  let counter=next;
  const added=pairs.map(pair=>({id:counter++,name:pair.name.toUpperCase(),value:pair.value,shown:false}));
  setRows(current=>[...current.filter(row=>row.name||row.value),...added].slice(0,100));setNext(counter);setSaved('');setFocusId(added[0]!.id);
  const lines=text.split(/\r?\n/).filter(line=>line.trim()&&!line.trim().startsWith('#')).length;
  const kept=rows.filter(row=>row.name||row.value).length;
  setNote(kept+pairs.length>100?'Only 100 secrets fit in one save, so the rows after the 100th were left out.':parsed.length>100?'Only the first 100 pairs were taken: a function can have 100 secrets.':lines>pairs.length&&!/["']/.test(text)?`${lines-pairs.length} line${lines-pairs.length===1?' was':'s were'} not NAME=value and skipped.`:'');
  return true;
 }
 return <div className="fw-stack">
  <div className="fw-page-head"><div><h2>Edge Function Secrets</h2><p className="fw-hint">Values your functions read with <code>Deno.env.get('NAME')</code>. A value is kept in a private file on the server and never shown again; the digest tells you whether it changed.</p></div></div>
  <Problem message={error||data.error} retry={data.error?data.refresh:undefined}/>
  <section className="fw-card" aria-label="Add or replace secrets"><h3>ADD OR REPLACE SECRETS</h3>
   {rows.map((row,index)=>{const all=problems(row),own:FieldErrors={};
    // A name is judged as it is typed; a missing value is said only after Save was pressed or a value was started.
    if(row.name&&all['secret-name'])own['secret-name']=all['secret-name'];
    if(row.value&&all['secret-value']&&row.value.length>0&&row.value.length>65536)own['secret-value']=all['secret-value'];
    if(tried&&(row.name||row.value)){if(all['secret-name'])own['secret-name']=all['secret-name'];if(all['secret-value'])own['secret-value']=all['secret-value'];}
    const dup=filled.filter(other=>other.name&&other.name===row.name).length>1;
    return <div className="fw-secretrow" role="group" aria-label={`Secret ${index+1}`} key={row.id}>
     <div className="fw-field stack"><div className="fw-text"><label className="fw-label" htmlFor={`secret-name-${row.id}`}>Name</label>{index===0&&<p className="fw-hint">A unique name for your secret. Saving an existing name replaces its value.</p>}</div>
      <input id={`secret-name-${row.id}`} autoComplete="off" spellCheck={false} placeholder="e.g. CLIENT_KEY" maxLength={128} value={row.name} aria-invalid={Boolean(own['secret-name'])||dup}
       onPaste={event=>{if(looksLikeEnv(event.clipboardData.getData('text'))&&paste(row.id,event.clipboardData.getData('text')))event.preventDefault();}}
       onChange={event=>{const field=event.target,at=field.selectionStart;change(row.id,{name:field.value.toUpperCase()});requestAnimationFrame(()=>{if(at!==null)field.setSelectionRange(at,at);});}}/>
      {(own['secret-name']||dup)&&<p className="fw-error" role="alert">{own['secret-name']??'This name is used twice.'}</p>}</div>
     <div className="fw-field stack"><div className="fw-text"><label className="fw-label" htmlFor={`secret-value-${row.id}`}>Value</label>{index===0&&<p className="fw-hint">Supports multi-line values such as PEM keys, JSON, or functions.</p>}</div>
      <div className="fw-secret-input"><textarea id={`secret-value-${row.id}`} className={row.shown?'':'masked'} rows={3} autoComplete="off" spellCheck={false} maxLength={65536} aria-invalid={Boolean(own['secret-value'])}
       ref={node=>{if(node&&node.value!==row.value)node.value=row.value;}} onInput={event=>change(row.id,{value:(event.target as HTMLTextAreaElement).value})}/>
       <button type="button" className="fw-icon" aria-label={row.shown?'Hide the value':'Show the value'} aria-pressed={row.shown} onClick={()=>change(row.id,{shown:!row.shown})}>{row.shown?<EyeOff aria-hidden="true"/>:<Eye aria-hidden="true"/>}</button></div>
      {own['secret-value']&&<p className="fw-error" role="alert">{own['secret-value']}</p>}</div>
     {rows.length>1&&<div className="fw-actions"><button type="button" className="fw-ghost small" onClick={()=>setRows(current=>current.filter(item=>item.id!==row.id))}><Trash2 aria-hidden="true"/>Remove this row</button></div>}</div>;})}
   {note&&<Notice tone="info">{note}</Notice>}
   <p className="fw-hint">Insert or update multiple secrets at once by pasting key-value pairs (<code>NAME=value</code>, one a line, as in a <code>.env</code> file) into a name field.</p>
   <div className="fw-card-foot"><span className="fw-hint" role="status">{saved}</span><span className="fw-actions">
    <button type="button" className="fw-ghost" onClick={()=>{setRows(current=>[...current,blank(next)]);setNext(next+1);}}><Plus aria-hidden="true"/>Add another</button>
    <button type="button" className="fw-primary" disabled={busy||!filled.length||(tried&&invalid)} onClick={()=>void save()}><KeyRound aria-hidden="true"/>Save{filled.length>1?` ${filled.length} secrets`:''}</button></span></div></section>
  <section className="fw-card" aria-label="Custom secrets"><div className="fw-card-head"><div><h3>Custom secrets</h3><p className="fw-hint">Secrets you have defined for this environment.</p></div>
   <label className="fw-search"><Search aria-hidden="true"/><span className="fw-sr">Search for a secret</span><input type="search" placeholder="Search for a secret" value={query} onChange={event=>setQuery(event.target.value)}/></label></div>
   {data.loading&&!data.data?<Loading/>:list.length?<div className="fw-rows secrets" role="table" aria-label="Custom secrets">
    <div className="fw-row head" role="row"><span role="columnheader">NAME</span><span role="columnheader">DIGEST <Chip>HMAC-SHA256</Chip></span><span role="columnheader">UPDATED</span><span role="columnheader" className="fw-sr">Actions</span></div>
    {list.map(row=><div className="fw-row" role="row" key={row.name}><span role="cell"><code>{row.name}</code></span>
     <span role="cell" className="fw-digest"><code title={row.digest}>{row.digest.slice(0,16)}…</code><CopyButton quiet text={row.digest} label={`Copy the digest of ${row.name}`}/></span>
     <span role="cell">{row.updated_at?ago(row.updated_at):'unknown'}</span>
     <span role="cell" className="fw-end">{confirm===row.name?<span className="fw-inline-confirm"><span>Delete {row.name}?</span><button type="button" className="fw-danger-solid" disabled={busy} onClick={()=>void submit({[row.name]:null},()=>setConfirm(''))}>Delete</button><button type="button" className="fw-ghost" onClick={()=>setConfirm('')}>Cancel</button></span>
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
