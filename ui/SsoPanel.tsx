import {useState} from 'react';
import {Copy,Plus,Trash2} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type Provider={id:string;entity_id:string;domains:string[];created_at:string;updated_at:string};
type Answer={data:{providers:Provider[]}};
const DOMAIN=/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;

/** Single sign-on, as the Supabase cloud's SSO page: the SAML identity providers this environment accepts, matched by email domain. The
 * providers live in the environment's Auth, which the console reaches through its admin API; nothing here needs database access. A provider is
 * added by pasting its metadata XML (a metadata address is not fetched: Auth would need a way out of the runtime network for that). */
export function SsoPanel({path,request,enabled,applying,callbackBase}:{path:string;request:Api;enabled:boolean;applying:boolean;callbackBase:string}){
 const data=useData<Answer>(signal=>enabled?request(path+'/sso-providers','GET',undefined,signal):Promise.resolve({data:{providers:[]}}),[path,request,enabled]);
 const [open,setOpen]=useState(false),[xml,setXml]=useState(''),[domains,setDomains]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[validated,setValidated]=useState(false);
 const list=domains.split(/[\s,]+/).map(item=>item.trim().toLowerCase()).filter(Boolean);
 const problems:Record<string,string>={};
 if(validated){
  if(!/<(?:\w+:)?EntityDescriptor[\s>]/.test(xml))problems.xml='Paste the identity provider\'s metadata XML. It starts with an EntityDescriptor element.';
  if(list.length===0||list.length>20||!list.every(item=>DOMAIN.test(item)))problems.domains='Enter one to 20 domains such as example.com, separated by commas or spaces.';
 }
 async function add(){
  if(busy)return;setValidated(true);
  if(!/<(?:\w+:)?EntityDescriptor[\s>]/.test(xml)||list.length===0||list.length>20||!list.every(item=>DOMAIN.test(item)))return;
  setBusy(true);setError('');
  try{await request(path+'/sso-providers','POST',{metadata_xml:xml,domains:list});setXml('');setDomains('');setOpen(false);setValidated(false);data.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function remove(provider:Provider){
  if(busy||!window.confirm(`Remove the identity provider for ${provider.domains.join(', ')||provider.entity_id}? People who sign in with it can no longer do so.`))return;
  setBusy(true);setError('');
  try{await request(`${path}/sso-providers/${provider.id}`,'DELETE');data.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 const metadata=`${callbackBase}/sso/saml/metadata`,acs=`${callbackBase}/sso/saml/acs`;
 return <>
  {!enabled&&<p className="aw-notice" role="status">SAML 2.0 is off for this environment. Turn it on, save and apply, then add identity providers here.</p>}
  {enabled&&applying&&<p className="aw-notice" role="status">The latest save is not applied yet. If SAML was only just turned on, the list below works once Auth has restarted with it.</p>}
  {enabled&&<>
   <h4>Service provider details</h4>
   <p className="aw-hint">Give these to the identity provider when you register this environment with it.</p>
   {[['SAML metadata URL',metadata],['Assertion consumer service (ACS) URL',acs]].map(([label,value])=><div className="aw-field" key={label}><label>{label}</label>
    <div className="aw-copy"><input readOnly value={value} aria-label={label}/><button type="button" className="aw-ghost" onClick={()=>void navigator.clipboard?.writeText(value!)}><Copy aria-hidden="true"/>Copy</button></div></div>)}
   <h4>Identity providers</h4>
   <ErrorMessage message={error||data.error}/>
   {data.loading&&!data.data?<Loading/>:(data.data?.data.providers.length??0)===0?<p className="aw-hint">No identity providers yet.</p>
    :<ul className="aw-vars">{data.data!.data.providers.map(provider=><li key={provider.id}><strong>{provider.domains.join(', ')||'No domain'}</strong><span>{provider.entity_id}</span>
     <button type="button" className="aw-danger" disabled={busy} onClick={()=>void remove(provider)}><Trash2 aria-hidden="true"/>Remove</button></li>)}</ul>}
   {!open?<div className="aw-actions aw-inline"><button type="button" className="aw-primary" onClick={()=>setOpen(true)}><Plus aria-hidden="true"/>Add identity provider</button></div>
    :<div className="aw-group-form">
     <div className="aw-field aw-wide"><label htmlFor="sso-xml">Metadata XML</label><textarea id="sso-xml" className="aw-code" rows={8} spellCheck={false} disabled={busy} aria-invalid={Boolean(problems.xml)} value={xml} onChange={event=>setXml(event.target.value)}/>
      {problems.xml&&<p className="aw-error" role="alert">{problems.xml}</p>}<p className="aw-hint">Download it from your identity provider and paste it here. A metadata web address is not fetched.</p></div>
     <div className="aw-field"><label htmlFor="sso-domains">Domains</label><input id="sso-domains" disabled={busy} aria-invalid={Boolean(problems.domains)} value={domains} placeholder="example.com" onChange={event=>setDomains(event.target.value)}/>
      {problems.domains&&<p className="aw-error" role="alert">{problems.domains}</p>}<p className="aw-hint">People whose email is on one of these domains can sign in through this provider when your app asks for single sign-on (<code>signInWithSSO</code>).</p></div>
     <div className="aw-actions"><button type="button" className="aw-ghost" disabled={busy} onClick={()=>{setOpen(false);setValidated(false);setError('');}}>Cancel</button>
      <button type="button" className="aw-primary" disabled={busy} onClick={()=>void add()}><Plus aria-hidden="true"/>Add provider</button></div></div>}
  </>}
 </>;
}
