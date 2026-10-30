import {useEffect,useRef,useState} from 'react';
import {Save} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {Switch} from './AwSwitch';

type Smtp={enabled:boolean;admin_email:string;sender_name:string;host:string;port:number;user:string;password_set:boolean};
type Answer={state:'unconfigured'|'pending'|'applied'|'failed';revision:number;failure:string|null;smtp:Smtp};
const HOST=/^[A-Za-z0-9][A-Za-z0-9._-]*$/,ADDRESS=/^[^@\s<>,;:"]+@[^@\s<>,;:"]+\.[^@\s<>,;:"]+$/;

/** Emails > SMTP Settings, as the Supabase cloud shows it: the switch, the sender, and the relay. The password is
 * written to the server and never shown again ("Stored password is hidden. Enter a new password to replace it."). */
export function SmtpPanel({path,request,onSaved}:{path:string;request:Api;onSaved:()=>void}){
 const data=useData<{data:Answer}>(signal=>request(path+'/smtp','GET',undefined,signal),[path,request]);
 // The last answer stays on screen while a refresh runs, so polling a pending apply does not rebuild the form under an edit.
 const latest=useRef<Answer|undefined>(undefined);
 if(data.data?.data)latest.current=data.data.data;
 const current=data.data?.data??latest.current;
 const loadedRevision=useRef<number|undefined>(undefined);
 const [form,setForm]=useState({enabled:false,admin_email:'',sender_name:'',host:'',port:'587',user:'',password:''});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[validated,setValidated]=useState(false),[loaded,setLoaded]=useState('');
 useEffect(()=>{
  if(!current)return;
  if(loadedRevision.current===current.revision&&loaded!=='')return;
  loadedRevision.current=current.revision;
  const next={enabled:current.smtp.enabled,admin_email:current.smtp.admin_email,sender_name:current.smtp.sender_name,host:current.smtp.host,
   port:String(current.smtp.port),user:current.smtp.user,password:''};
  setForm(next);setLoaded(JSON.stringify(next));setValidated(false);
 },[current?.revision]);
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 const errors:Record<string,string>={};
 if(validated&&form.enabled){
  if(!ADDRESS.test(form.admin_email.trim()))errors.admin_email='Enter one email address, with no name or angle brackets.';
  if(/[<>"\r\n]/.test(form.sender_name))errors.sender_name='Do not use angle brackets, quotes or line breaks.';
  if(!HOST.test(form.host.trim()))errors.host='Enter a host name only, with no scheme or port.';
  const port=Number(form.port);if(!Number.isInteger(port)||port<1||port>65535)errors.port='Enter a port from 1 to 65535.';
  if(!current?.smtp.password_set&&!form.password)errors.password='Enter the SMTP password.';
 }
 const dirty=loaded!==''&&JSON.stringify(form)!==loaded;
 const bad=(id:string)=>({'aria-invalid':Boolean(errors[id]),'aria-describedby':errors[id]?id+'-error':undefined,disabled:busy});
 const message=(id:string)=>errors[id]?<p id={id+'-error'} className="aw-error" role="alert">{errors[id]}</p>:null;
 async function save(){
  if(busy)return;setValidated(true);
  if(form.enabled&&(!ADDRESS.test(form.admin_email.trim())||/[<>"\r\n]/.test(form.sender_name)||!HOST.test(form.host.trim())||!Number.isInteger(Number(form.port))||Number(form.port)<1||Number(form.port)>65535||(!current?.smtp.password_set&&!form.password)))return;
  setBusy(true);setError('');
  try{
   await request(path+'/smtp','PUT',form.enabled?{enabled:true,base_revision:current?.revision,admin_email:form.admin_email.trim(),sender_name:form.sender_name,host:form.host.trim(),
    port:Number(form.port),user:form.user,password:form.password}:{enabled:false,base_revision:current?.revision});
   data.refresh();onSaved();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 if(!current)return data.error?<ErrorMessage message={data.error}/>:<Loading/>;
 return <>
  <ErrorMessage message={error||data.error}/>
  {current.state==='failed'&&current.failure&&<p className="aw-notice" role="status">{current.failure}</p>}
  <div className="aw-group"><Switch id="smtp-enabled" checked={form.enabled} disabled={busy} label="Enable custom SMTP" hint="Send this environment's auth emails through your own SMTP provider. Without it, no email is sent."
   onChange={value=>setForm({...form,enabled:value})}/></div>
  {form.enabled&&<>
   <h4>Sender details</h4>
   <div className="aw-field"><label htmlFor="smtp-admin-email">Sender email address</label><input id="smtp-admin-email" type="email" maxLength={254} {...bad('admin_email')} value={form.admin_email} onChange={event=>setForm({...form,admin_email:event.target.value})} placeholder="noreply@example.com"/>{message('admin_email')}<p className="aw-hint">The email address the emails are sent from.</p></div>
   <div className="aw-field"><label htmlFor="smtp-sender-name">Sender name</label><input id="smtp-sender-name" maxLength={200} {...bad('sender_name')} value={form.sender_name} onChange={event=>setForm({...form,sender_name:event.target.value})}/>{message('sender_name')}<p className="aw-hint">Name displayed in the recipient's inbox.</p></div>
   <h4>SMTP provider settings</h4>
   <div className="aw-field"><label htmlFor="smtp-host">Host</label><input id="smtp-host" maxLength={253} {...bad('host')} value={form.host} onChange={event=>setForm({...form,host:event.target.value})} placeholder="smtp.example.com"/>{message('host')}<p className="aw-hint">Hostname or IP address of your SMTP server.</p></div>
   <div className="aw-field"><label htmlFor="smtp-port">Port number</label><input id="smtp-port" type="number" min={1} max={65535} {...bad('port')} value={form.port} onChange={event=>setForm({...form,port:event.target.value})}/>{message('port')}<p className="aw-hint">Port used by your SMTP server. Common ports include 465 and 587. Avoid port 25, which providers often block to curb spam.</p></div>
   <div className="aw-field"><label htmlFor="smtp-user">Username</label><input id="smtp-user" autoComplete="off" maxLength={512} disabled={busy} value={form.user} onChange={event=>setForm({...form,user:event.target.value})}/><p className="aw-hint">Username for your SMTP server.</p></div>
   <div className="aw-field"><label htmlFor="smtp-password">Password</label><input id="smtp-password" type="password" autoComplete="new-password" maxLength={1024} {...bad('password')} value={form.password}
    placeholder={current.smtp.password_set?'Stored password is hidden':''} onChange={event=>setForm({...form,password:event.target.value})}/>{message('password')}
    <p className="aw-hint">{current.smtp.password_set?'Stored password is hidden. Enter a new password to replace it.':'The password stays on the server and is never shown again.'}</p></div>
  </>}
  {form.enabled&&<p className="aw-hint" role="note">With custom SMTP on, new sign-ups must confirm their email address; people who already signed up are not affected. Applied means this environment's Auth started with these settings, not that your SMTP provider accepts them: send a test sign-up to check.</p>}
  {!form.enabled&&current.smtp.enabled&&<p className="aw-notice" role="status">Saving with this off removes the stored SMTP settings and password from this environment.</p>}
  <div className="aw-actions aw-inline"><button type="button" className="aw-primary" disabled={busy||(!dirty&&current.state!=='failed')||current.state==='pending'} onClick={()=>void save()}><Save aria-hidden="true"/>Save changes</button></div>
 </>;
}
