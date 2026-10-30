import {useEffect,useRef,useState} from 'react';
import {Save} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {Switch} from './AwSwitch';

type Provider={id:string;label:string;fields:{name:string;label:string;secret:boolean}[]};
type Sms={enabled:boolean;provider:string;phone_confirmations:boolean;rate_limit_sms_sent:number;fields:Record<string,string>;secrets_set:Record<string,boolean>;providers:Provider[]};
type Answer={state:'unconfigured'|'pending'|'applied'|'failed';revision:number;failure:string|null;sms:Sms};

/** Phone, as the Supabase cloud shows it: the switch, the SMS provider and its credentials, phone confirmations and two limits. A credential is
 * written to the server and never shown again. The environment's Auth gets a way out of the runtime network while this is on, because every
 * provider here is an HTTPS service. */
export function SmsPanel({path,request,onSaved}:{path:string;request:Api;onSaved:()=>void}){
 const data=useData<{data:Answer}>(signal=>request(path+'/sms','GET',undefined,signal),[path,request]);
 const latest=useRef<Answer|undefined>(undefined);
 if(data.data?.data)latest.current=data.data.data;
 const current=data.data?.data??latest.current;
 const loadedRevision=useRef<number|undefined>(undefined);
 const [form,setForm]=useState({enabled:false,provider:'twilio',values:{} as Record<string,string>,confirmations:true,limit:'30'});
 const [loaded,setLoaded]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[validated,setValidated]=useState(false);
 useEffect(()=>{
  if(!current)return;
  if(loadedRevision.current===current.revision&&loaded!=='')return;
  loadedRevision.current=current.revision;
  const next={enabled:current.sms.enabled,provider:current.sms.provider,values:{...current.sms.fields},confirmations:current.sms.phone_confirmations,
   limit:String(current.sms.rate_limit_sms_sent)};
  setForm(next);setLoaded(JSON.stringify(next));setValidated(false);
 },[current?.revision]);
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 if(!current)return data.error?<ErrorMessage message={data.error}/>:<Loading/>;
 const provider=current.sms.providers.find(item=>item.id===form.provider)??current.sms.providers[0]!;
 // A saved secret counts only for the provider it was saved for.
 const secretSet=(name:string)=>current.sms.enabled&&current.sms.provider===provider.id&&current.sms.secrets_set[name]===true;
 const errors:Record<string,string>={};
 if(validated&&form.enabled){
  for(const field of provider.fields){
   const typed=(form.values[field.name]??'').trim();
   if(field.secret?(!typed&&!secretSet(field.name)):!typed)errors[field.name]=`Enter the ${field.label.toLowerCase()}.`;
  }
  if(!/^\d+$/.test(form.limit.trim())||Number(form.limit)<1||Number(form.limit)>1000000)errors.limit='Use a whole number from 1 to 1000000.';
 }
 const dirty=loaded!==''&&JSON.stringify(form)!==loaded;
 const bad=(id:string)=>({'aria-invalid':Boolean(errors[id]),'aria-describedby':errors[id]?id+'-error':undefined,disabled:busy});
 const message=(id:string)=>errors[id]?<p id={id+'-error'} className="aw-error" role="alert">{errors[id]}</p>:null;
 async function save(){
  if(busy)return;setValidated(true);
  if(form.enabled){
   for(const field of provider.fields){const typed=(form.values[field.name]??'').trim();if(field.secret?(!typed&&!secretSet(field.name)):!typed)return;}
   if(!/^\d+$/.test(form.limit.trim())||Number(form.limit)<1||Number(form.limit)>1000000)return;
  }
  setBusy(true);setError('');
  try{
   await request(path+'/sms','PUT',form.enabled?{base_revision:current!.revision,provider:provider.id,phone_confirmations:form.confirmations,rate_limit_sms_sent:Number(form.limit),credentials:Object.fromEntries(provider.fields.map(field=>[field.name,(form.values[field.name]??'').trim()]).filter(([,text])=>text!==''))}
    :{enabled:false,base_revision:current!.revision});
   data.refresh();onSaved();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <>
  <ErrorMessage message={error||data.error}/>
  {current.state==='failed'&&current.failure&&<p className="aw-notice" role="status">{current.failure}</p>}
  <div className="aw-group"><Switch id="phone-enabled" checked={form.enabled} disabled={busy} label="Enable phone sign-ups and sign-ins" hint="Allow people to sign up and sign in with a phone number and a code sent by SMS." onChange={value=>setForm({...form,enabled:value})}/></div>
  {form.enabled&&<>
   <h4>SMS provider</h4>
   <div className="aw-field"><label htmlFor="sms-provider">Provider</label>
    <select id="sms-provider" disabled={busy} value={form.provider} onChange={event=>setForm({...form,provider:event.target.value,values:{}})}>{current.sms.providers.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select>
    <p className="aw-hint">The service that delivers the code. While this is on, this environment's Auth can reach the internet; nothing else about the environment changes.</p></div>
   {provider.fields.map(field=><div className="aw-field" key={provider.id+field.name}><label htmlFor={'sms-'+field.name}>{field.label}</label>
    <input id={'sms-'+field.name} type={field.secret?'password':'text'} autoComplete="off" maxLength={256} {...bad(field.name)} value={form.values[field.name]??''}
     placeholder={field.secret&&secretSet(field.name)?'Stored value is hidden':''} onChange={event=>setForm({...form,values:{...form.values,[field.name]:event.target.value}})}/>{message(field.name)}
    {field.secret&&<p className="aw-hint">{secretSet(field.name)?'Stored value is hidden. Enter a new one to replace it.':'This stays on the server and is never shown again.'}</p>}</div>)}
   <h4>Confirmation and limits</h4>
   <div className="aw-group"><Switch id="phone-confirm" checked={form.confirmations} disabled={busy} label="Enable phone confirmations" hint="People confirm their number with the code before they can sign in. Turning it off signs new numbers in without checking that the number is theirs." onChange={value=>setForm({...form,confirmations:value})}/></div>
   <div className="aw-field"><label htmlFor="sms-limit">Rate limit for sending SMS (messages/h)</label><input id="sms-limit" type="number" min={1} max={1000000} {...bad('limit')} value={form.limit} onChange={event=>setForm({...form,limit:event.target.value})}/>{message('limit')}<p className="aw-hint">Messages this environment may send per hour, across all numbers. It bounds what the provider can bill you.</p></div>
   <p className="aw-hint" role="note">Applied means this environment's Auth started with these settings, not that the provider accepts the credentials or delivers messages: sign up with a real number to check.</p>
  </>}
  {!form.enabled&&current.sms.enabled&&<p className="aw-notice" role="status">Saving with this off removes the stored provider credentials from this environment.</p>}
  <div className="aw-actions aw-inline"><button type="button" className="aw-primary" disabled={busy||(!dirty&&current.state!=='failed')||current.state==='pending'} onClick={()=>void save()}><Save aria-hidden="true"/>Save changes</button></div>
 </>;
}
