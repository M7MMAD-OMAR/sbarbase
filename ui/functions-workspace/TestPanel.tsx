import {useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Plus,Send,Trash2,X} from 'lucide-react';
import type {Api} from '../api';
import {useLive} from './live';
import type {ApiKey,FunctionRow} from './types';
import {Choice,Notice} from './kit';
import {milliseconds} from './format';

const METHODS=['POST','GET','PUT','PATCH','DELETE'].map(id=>({id,label:id}));
type Pair={name:string;value:string};
type Outcome={status:number;statusText:string;ms:number;headers:[string,string][];body:string;json:boolean}|{error:string};
const MAX_SHOWN=200_000;

function Pairs({label,pairs,onChange,placeholder,add}:{label:string;pairs:Pair[];onChange:(pairs:Pair[])=>void;placeholder:[string,string];add:string}){
 const set=(index:number,field:keyof Pair,value:string)=>onChange(pairs.map((pair,position)=>position===index?{...pair,[field]:value}:pair));
 return <div className="fw-pairs" role="group" aria-label={label}><span className="fw-label">{label}</span>
  {pairs.map((pair,index)=><div className="fw-pair" key={index}><input aria-label={`${label} name ${index+1}`} placeholder={placeholder[0]} value={pair.name} onChange={event=>set(index,'name',event.target.value)}/>
   <input aria-label={`${label} value ${index+1}`} placeholder={placeholder[1]} value={pair.value} onChange={event=>set(index,'value',event.target.value)}/>
   <button type="button" className="fw-icon" aria-label={`Remove ${label.toLowerCase()} ${index+1}`} onClick={()=>onChange(pairs.length>1?pairs.filter((_,position)=>position!==index):[{name:'',value:''}])}><Trash2 aria-hidden="true"/></button></div>)}
  <button type="button" className="fw-ghost small" onClick={()=>onChange([...pairs,{name:'',value:''}])}><Plus aria-hidden="true"/>{add}</button></div>;
}

/** Sends one request to the function from the browser, with the environment's publishable key, as the Supabase dashboard does.
 * A browser shows only the response headers the gateway lets it read. */
export function TestPanel({fn,path,request,origin,onClose}:{fn:FunctionRow;path:string;request:Api;origin:string;onClose:()=>void}){
 const [method,setMethod]=useState('POST'),[body,setBody]=useState('{ "name": "Functions" }'),[headers,setHeaders]=useState<Pair[]>([{name:'',value:''}]),[query,setQuery]=useState<Pair[]>([{name:'',value:''}]);
 const [useKey,setUseKey]=useState(true),[busy,setBusy]=useState(false),[outcome,setOutcome]=useState<Outcome>(),close=useRef<HTMLButtonElement>(null),portal=useRef<HTMLDivElement>(null),closing=useRef(onClose);
 closing.current=onClose;
 const keys=useLive<{data:ApiKey[]}>(signal=>request(path+'/keys','GET',undefined,signal),[path]);
 const key=keys.data?.data.find(item=>item.kind==='publishable'&&!item.revoked_at&&item.token)?.token??undefined;
 // A modal dialog: the page behind cannot be reached (inert, so Tab stays inside), Escape closes it unless a list inside it
 // used the key first, and focus goes back to what opened it. Runs once; a re-render of the page behind does not move focus.
 useEffect(()=>{
  const opener=document.activeElement as HTMLElement|null,hidden:Element[]=[];
  for(const child of Array.from(document.body.children))if(child!==portal.current&&child.tagName!=='SCRIPT'&&!child.hasAttribute('inert')){child.setAttribute('inert','');hidden.push(child);}
  close.current?.focus();
  const key=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!event.defaultPrevented)closing.current();};
  document.addEventListener('keydown',key);
  return()=>{document.removeEventListener('keydown',key);for(const child of hidden)child.removeAttribute('inert');opener?.focus();};
 },[]);
 async function send(){
  setBusy(true);setOutcome(undefined);
  const url=new URL(origin+fn.path);
  for(const pair of query)if(pair.name.trim())url.searchParams.append(pair.name.trim(),pair.value);
  const sent=new Headers();
  if(useKey&&key){sent.set('apikey',key);sent.set('authorization','Bearer '+key);}
  const hasBody=method!=='GET'&&body.trim()!=='';
  if(hasBody)sent.set('content-type','application/json');
  try{for(const pair of headers)if(pair.name.trim())sent.set(pair.name.trim(),pair.value);}catch{setOutcome({error:'A header name or value is not valid.'});setBusy(false);return;}
  const started=performance.now();
  try{
   const response=await fetch(url,{method,headers:sent,body:hasBody?body:undefined,credentials:'omit',signal:AbortSignal.timeout(160_000)});
   const text=(await response.text()).slice(0,MAX_SHOWN);
   let shown=text,json=false;
   try{shown=JSON.stringify(JSON.parse(text),null,2);json=true;}catch{}
   setOutcome({status:response.status,statusText:response.statusText,ms:Math.round(performance.now()-started),headers:[...response.headers.entries()],body:shown,json});
  }catch(reason){setOutcome({error:(reason as Error).name==='TimeoutError'?'No answer within 160 seconds.':'The request did not complete. A function that sets its own CORS headers can still be called, but a browser may hide its answer.'});}
  finally{setBusy(false);}
 }
 return createPortal(<div className="fw fw-portal" ref={portal}><div className="fw-scrim" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>
  <div className="fw-drawer" role="dialog" aria-modal="true" aria-label={`Test ${fn.name}`}>
   <div className="fw-drawer-head"><h3>Test {fn.name}</h3><button ref={close} type="button" className="fw-icon" aria-label="Close" onClick={onClose}><X aria-hidden="true"/></button></div>
   <div className="fw-drawer-body">
    <div className="fw-formrow"><span className="fw-label" id="test-method">HTTP method</span><Choice label="HTTP method" value={method} options={METHODS} onChange={setMethod} wide/></div>
    <div className="fw-formrow"><label className="fw-label" htmlFor="test-body">Request body</label><textarea id="test-body" rows={4} spellCheck={false} value={body} disabled={method==='GET'} onChange={event=>setBody(event.target.value)}/></div>
    <Pairs label="Headers" pairs={headers} onChange={setHeaders} placeholder={['Header name','Header value']} add="Add header"/>
    <Pairs label="Query parameters" pairs={query} onChange={setQuery} placeholder={['Parameter name','Parameter value']} add="Add parameter"/>
    <label className="fw-check"><input type="checkbox" checked={useKey} onChange={event=>setUseKey(event.target.checked)}/>Send the publishable key{!key&&!keys.loading&&useKey&&<span className="fw-hint"> (this environment has none that can be shown)</span>}</label>
    {!useKey&&<p className="fw-hint">Without a key only a function with Verify JWT turned off can answer, as for a webhook.</p>}
    <div className="fw-result" aria-live="polite">{busy?<p className="fw-empty">Sending…</p>:!outcome?<p className="fw-empty"><Send aria-hidden="true"/>Send your first test request</p>:'error' in outcome?<Notice tone="bad">{outcome.error}</Notice>
     :<><p className={'fw-status '+(outcome.status>=500?'bad':outcome.status>=400?'warn':'ok')}><strong>{outcome.status}</strong> {outcome.statusText} · {milliseconds(outcome.ms)}</p>
      {outcome.headers.length>0&&<details><summary>Headers a browser may read ({outcome.headers.length})</summary><pre className="fw-snippet">{outcome.headers.map(([name,value])=>`${name}: ${value}`).join('\n')}</pre></details>}
      <pre className="fw-snippet" tabIndex={0}>{outcome.body||'(empty body)'}</pre></>}</div>
   </div>
   <div className="fw-drawer-foot"><span className="fw-hint">Test calls are counted in this function's numbers.</span><button type="button" className="fw-primary" disabled={busy} onClick={()=>void send()}><Send aria-hidden="true"/>Send request</button></div>
  </div></div></div>,document.body);
}
