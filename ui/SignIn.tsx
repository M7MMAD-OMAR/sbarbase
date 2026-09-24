import {useEffect,useState} from 'react';
import {Copy,Plus,Save,Trash2} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type Provider={enabled:boolean;client_id:string;secret_set?:boolean;secret?:string;url:string};
type Settings={site_url:string;redirect_urls:string[];signup:boolean;anonymous:boolean;providers:Record<string,Provider>};
type State={state:'unconfigured'|'pending'|'applied'|'failed';failure:string|null;callback_url:string;settings:Settings|null};

/** The providers Auth offers, as people know them. `url` marks those that name their own server. */
export const PROVIDERS:Record<string,{label:string;url?:'optional'|'required'}>={
 apple:{label:'Apple'},azure:{label:'Microsoft (Azure)',url:'optional'},bitbucket:{label:'Bitbucket'},discord:{label:'Discord'},
 facebook:{label:'Facebook'},figma:{label:'Figma'},github:{label:'GitHub'},gitlab:{label:'GitLab',url:'optional'},google:{label:'Google'},
 kakao:{label:'Kakao'},keycloak:{label:'Keycloak',url:'required'},linkedin_oidc:{label:'LinkedIn'},notion:{label:'Notion'},
 slack_oidc:{label:'Slack'},spotify:{label:'Spotify'},twitch:{label:'Twitch'},twitter:{label:'X (Twitter)'},workos:{label:'WorkOS',url:'optional'},
 zoom:{label:'Zoom'}};
const STATE_TEXT:Record<State['state'],string>={unconfigured:'Default settings',pending:'Applying…',applied:'Applied',failed:'Not applied'};
const EMPTY:Settings={site_url:'http://localhost:3000',redirect_urls:[],signup:true,anonymous:false,providers:{}};

/** Where Auth sends people, who may sign up, and which OAuth providers the environment offers. */
export function SignInSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/sign-in','GET',undefined,signal),[path,request]);
 const current=data.data?.data;
 const [form,setForm]=useState<Settings>(EMPTY),[redirects,setRedirects]=useState(''),[adding,setAdding]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState('');
 useEffect(()=>{
  if(!current)return;
  const saved=current.settings??EMPTY;
  setForm({...saved,providers:Object.fromEntries(Object.entries(saved.providers).map(([name,entry])=>[name,{...entry,secret:''}]))});
  setRedirects(saved.redirect_urls.join('\n'));
 },[current?.settings]);
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 const provider=(name:string,change:Partial<Provider>)=>setForm(value=>({...value,providers:{...value.providers,[name]:{...value.providers[name]!,...change}}}));
 function remove(name:string){setForm(value=>{const providers={...value.providers};delete providers[name];return {...value,providers};});}
 async function save(){
  setBusy(true);setError('');
  try {
   const providers=Object.fromEntries(Object.entries(form.providers).map(([name,{enabled,client_id,secret,url}])=>
    [name,{enabled,client_id,secret:secret??'',url}]));
   await request(path+'/sign-in','PUT',{site_url:form.site_url.trim(),redirect_urls:redirects.split('\n').map(line=>line.trim()).filter(Boolean),
    signup:form.signup,anonymous:form.anonymous,providers});
   data.refresh();
  } catch(e){setError((e as Error).message);} finally{setBusy(false);}
 }
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Select and copy the address manually.');}}
 const available=Object.keys(PROVIDERS).filter(name=>!form.providers[name]);
 return <section className="details"><h2>Sign-in</h2>
 <p className="muted small">Where Auth sends people after they sign in or open an email link, who may sign up, and which sign-in providers this environment offers.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  <p><span className={'state '+(current.state==='applied'?'applied':current.state==='failed'?'failed':current.state==='pending'?'running':'')}>{STATE_TEXT[current.state]}</span></p>
  {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
  <label htmlFor="site-url">Site URL</label>
  <input id="site-url" value={form.site_url} onChange={event=>setForm({...form,site_url:event.target.value})} placeholder="https://app.example.com"/>
  <p className="small muted">Your application's address. Auth sends people here when no other address is allowed.</p>
  <label htmlFor="redirects">Other allowed redirect addresses</label>
  <textarea id="redirects" value={redirects} onChange={event=>setRedirects(event.target.value)} placeholder={'https://app.example.com/**\nmyapp://callback'}/>
  <p className="small muted">One per line. <code>*</code> and <code>**</code> match parts of an address.</p>
  <label className="check"><input type="checkbox" checked={form.signup} onChange={event=>setForm({...form,signup:event.target.checked})}/>Allow new users to sign up</label>
  <label className="check"><input type="checkbox" checked={form.anonymous} onChange={event=>setForm({...form,anonymous:event.target.checked})}/>Allow anonymous sign-ins</label>
  <h3>Sign-in providers</h3>
  <label htmlFor="callback">Callback address to give each provider</label>
  <div className="form-row"><input id="callback" readOnly value={current.callback_url}/><button onClick={()=>void copy(current.callback_url)}><Copy aria-hidden="true"/>Copy</button></div>
  <p className="small muted" role="status">{copied}</p>
  {Object.entries(form.providers).map(([name,entry])=><div className="provider" key={name}>
   <div className="form-row"><strong>{PROVIDERS[name]?.label??name}</strong><label className="check"><input type="checkbox" checked={entry.enabled} onChange={event=>provider(name,{enabled:event.target.checked})}/>Enabled</label>
    <button className="secondary" onClick={()=>remove(name)}><Trash2 aria-hidden="true"/>Remove</button></div>
   <label htmlFor={name+'-client'}>Client ID</label><input id={name+'-client'} value={entry.client_id} onChange={event=>provider(name,{client_id:event.target.value})}/>
   <label htmlFor={name+'-secret'}>Client secret</label><input id={name+'-secret'} type="password" autoComplete="off" value={entry.secret??''}
    placeholder={entry.secret_set?'Saved. Leave empty to keep it.':''} onChange={event=>provider(name,{secret:event.target.value})}/>
   {PROVIDERS[name]?.url&&<><label htmlFor={name+'-url'}>Server address{PROVIDERS[name]!.url==='optional'?' (optional)':''}</label>
    <input id={name+'-url'} value={entry.url} onChange={event=>provider(name,{url:event.target.value})} placeholder="https://"/></>}
  </div>)}
  {available.length>0&&<div className="form-row"><label className="sr-only" htmlFor="add-provider">Add a provider</label>
   <select id="add-provider" value={adding} onChange={event=>setAdding(event.target.value)}><option value="">Choose a provider…</option>
    {available.map(name=><option key={name} value={name}>{PROVIDERS[name]!.label}</option>)}</select>
   <button disabled={!adding} onClick={()=>{provider(adding,{enabled:true,client_id:'',secret:'',url:''});setAdding('');}}><Plus aria-hidden="true"/>Add provider</button></div>}
  <div className="form-row actions"><button className="primary" disabled={busy||current.state==='pending'} onClick={()=>void save()}><Save aria-hidden="true"/>Save and apply</button></div>
  <p className="small muted">Saving restarts this environment's Auth with the new settings. Users and sessions are kept. If Auth does not start, the previous settings stay in use.</p>
 </>}</section>;
}
