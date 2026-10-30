import {useEffect,useRef,useState} from 'react';
import {Copy,Save,Trash2,X} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {signInErrors,type FieldErrors} from './settings-validation';
import {ProviderIcon} from './ProviderIcon';
import './auth-workspace.css';

type Provider={enabled:boolean;client_id:string;secret_set?:boolean;secret?:string;url:string;skip_nonce_check?:boolean;email_optional?:boolean};
/** The providers whose sign-in accepts ID tokens with any nonce (the switch the cloud shows for them). */
const NONCE_PROVIDERS=['apple','google'];
type Session={access_token_seconds:number|null;timebox_seconds:number|null;inactivity_seconds:number|null};
type ConfigValue=boolean|number;
type SchemaField={key:string;label:string;type:string;value?:unknown;hint?:string;unit?:string;min?:number;max?:number;null_text?:string;store?:string};
type SchemaPage={id:string;fields:SchemaField[]};
type Settings={config?:Record<string,ConfigValue>;site_url:string;redirect_urls:string[];signup:boolean;anonymous:boolean;manual_linking:boolean;session:Session;providers:Record<string,Provider>};
type State={state:'unconfigured'|'pending'|'applied'|'failed';revision:number;failure:string|null;callback_url:string;settings:Settings|null};

/** The providers Auth offers, as people know them. `url` marks those that name their own server. */
export const PROVIDERS:Record<string,{label:string;url?:'optional'|'required'}>={
 apple:{label:'Apple'},azure:{label:'Microsoft (Azure)',url:'optional'},bitbucket:{label:'Bitbucket'},discord:{label:'Discord'},
 facebook:{label:'Facebook'},figma:{label:'Figma'},github:{label:'GitHub'},gitlab:{label:'GitLab',url:'optional'},google:{label:'Google'},
 kakao:{label:'Kakao'},keycloak:{label:'Keycloak',url:'required'},linkedin_oidc:{label:'LinkedIn'},notion:{label:'Notion'},
 slack_oidc:{label:'Slack'},spotify:{label:'Spotify'},twitch:{label:'Twitch'},twitter:{label:'X (Twitter)'},workos:{label:'WorkOS',url:'optional'},
 zoom:{label:'Zoom'}};
const EMPTY:Settings={site_url:'http://localhost:3000',redirect_urls:[],signup:true,anonymous:false,manual_linking:false,
 session:{access_token_seconds:null,timebox_seconds:null,inactivity_seconds:null},providers:{}};
type SessionText={token:string;timebox:string;inactivity:string};

/** The wire carries seconds; the two long lifetimes are entered as whole hours, so a part of
 * an hour is refused instead of rounded. Empty means no cap. */
function sessionErrors(value:SessionText):FieldErrors {
 const errors:FieldErrors={};
 const token=value.token.trim();
 if(token&&(!/^\d+$/.test(token)||Number(token)<60||Number(token)>86400))
  errors['session-token']='Use whole seconds from 60 to 86400, or leave this empty for the pinned default.';
 const hours=(id:string,text:string)=>{
  const raw=text.trim();if(!raw)return;
  if(!/^\d+$/.test(raw)){errors[id]=/^\d+\.\d+$/.test(raw)?'Use whole hours, not part of an hour.':'Use whole hours.';return;}
  if(Number(raw)<1||Number(raw)>87600)errors[id]='Use whole hours from 1 to 87600, or leave this empty for no limit.';
 };
 hours('session-timebox',value.timebox);hours('session-inactivity',value.inactivity);
 return errors;
}
const secondsOrNull=(text:string)=>text.trim()===''?null:Number(text.trim());
const hoursOrNull=(text:string)=>{const value=secondsOrNull(text);return value===null?null:value*3600;};

const TABS=[{id:'signin',label:'Sign In / Providers',hint:'Who can sign up, and which providers people sign in with.'},
 {id:'urls',label:'URL Configuration',hint:'Where Auth sends people after they sign in or open an email link.'},
 {id:'sessions',label:'Sessions',hint:'How long a person stays signed in, and how access and refresh tokens behave.'},
 {id:'rate-limits',label:'Rate Limits',hint:'Safeguard against bursts of incoming traffic to prevent abuse and maximize stability.'},
 {id:'protection',label:'Attack Protection',hint:'Security settings that protect this environment from attacks.'},
 {id:'mfa',label:'Multi-Factor',hint:'Requires people to provide additional verification factors to authenticate.'}] as const;
type TabId=typeof TABS[number]['id'];
/** The registry's pages that hold simple settings, by tab. */
const CONFIG_TABS:string[]=['sessions','rate-limits','protection','mfa'];

function Switch({id,checked,disabled,label,hint,onChange}:{id:string;checked:boolean;disabled:boolean;label:string;hint:string;onChange:(value:boolean)=>void}){
 return <div className="aw-row"><div className="aw-text"><label id={id+'-label'} htmlFor={id}>{label}</label><p className="aw-hint">{hint}</p></div>
  <button id={id} type="button" role="switch" aria-checked={checked} aria-labelledby={id+'-label'} disabled={disabled} className="aw-switch" onClick={()=>onChange(!checked)}><span/></button></div>;
}

/** The registry's simple settings of one page: a switch for each true or false setting, a number field for the rest.
 * A setting never touched stays unset, so Auth keeps its own default; clearing a number puts it back to that. */
function ConfigFields({fields,cfg,setCfg,busy,errors,persisted,failed}:{fields:SchemaField[];cfg:Record<string,ConfigValue|null>;setCfg:(value:Record<string,ConfigValue|null>)=>void;busy:boolean;errors:FieldErrors;persisted:Record<string,ConfigValue>;failed:string}){
 if(failed)return <p className="aw-notice" role="alert">These settings could not be loaded. Reload the page to try again. {failed}</p>;
 if(fields.length===0)return <p className="aw-hint">Loading these settings…</p>;
 const switches=fields.filter(field=>field.type==='bool'),numbers=fields.filter(field=>field.type!=='bool');
 return <>{switches.length>0&&<div className="aw-group">{switches.map(field=>{
   const saved=cfg[field.key],on=typeof saved==='boolean'?saved:field.value===true;
   return <Switch key={field.key} id={'config-'+field.key} checked={on} disabled={busy} label={field.label} hint={field.hint??''} onChange={value=>setCfg({...cfg,[field.key]:value===(field.value===true)&&persisted[field.key]===undefined?null:value})}/>;})}</div>}
  {numbers.map(field=>{const value=cfg[field.key],id='config-'+field.key;
   return <div className="aw-field" key={field.key}><label htmlFor={id}>{field.label}{field.unit?` (${field.unit})`:''}</label>
    <input id={id} type="number" min={field.min} max={field.max} step={1} disabled={busy} aria-invalid={Boolean(errors[id])} aria-describedby={errors[id]?id+'-error':undefined}
     placeholder={field.null_text??''} value={typeof value==='number'?String(value):''} onChange={event=>setCfg({...cfg,[field.key]:event.target.value.trim()===''?null:Number(event.target.value)})}/>
    {errors[id]&&<p id={id+'-error'} className="aw-error" role="alert">{errors[id]}</p>}{field.hint&&<p className="aw-hint">{field.hint}</p>}</div>;})}</>;
}

/** An opened provider that was never filled in is not a saved provider. */
function savedProviders(providers:Record<string,Provider>):Record<string,Provider>{
 return Object.fromEntries(Object.entries(providers).filter(([,entry])=>entry.enabled||entry.client_id||entry.secret||entry.secret_set||entry.url||entry.skip_nonce_check||entry.email_optional));
}

/** The environment's Authentication settings: tabbed like the Supabase cloud's pages, one save bar, one state.
 * The save is the same request as before (PUT sign-in with the base revision); only the screen changed. */
export function SignInSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/sign-in','GET',undefined,signal),[path,request]);
 // The last answer stays on screen while a refresh runs: polling a pending apply must not rebuild the page under an edit.
 const schema=useData<{data:{config:{pages:SchemaPage[]}}}>(signal=>request(path+'/auth-config','GET',undefined,signal),[path,request]);
 const configFields=(page:string)=>(schema.data?.data.config.pages.find(item=>item.id===page)?.fields??[]).filter(field=>field.store==='config');
 const [cfg,setCfg]=useState<Record<string,ConfigValue|null>>({});
 const latest=useRef<State|undefined>(undefined);
 if(data.data?.data)latest.current=data.data.data;
 const current=data.data?.data??latest.current;
 const loadedRevision=useRef<number|undefined>(undefined);
 const opener=useRef<HTMLElement|null>(null);
 const [form,setForm]=useState<Settings>(EMPTY),[redirects,setRedirects]=useState('');
 const [session,setSession]=useState<SessionText>({token:'',timebox:'',inactivity:''});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState('');
 const [validated,setValidated]=useState(false),[tab,setTab]=useState<TabId>('signin'),[open,setOpen]=useState('');
 const [baseline,setBaseline]=useState('');
 const fields:FieldErrors=validated?{...signInErrors(form.site_url,redirects,form.providers),...sessionErrors(session),...configErrors()}:{};
 const field=(id:string)=>({disabled:busy,'aria-invalid':Boolean(fields[id]),'aria-describedby':fields[id]?id+'-error':undefined});
 const fieldMessage=(id:string)=>fields[id]?<p id={id+'-error'} className="aw-error" role="alert">{fields[id]}</p>:null;
 /** Whole numbers inside the bounds the registry gives each setting. */
 function configErrors():FieldErrors{
  const errors:FieldErrors={};
  for(const page of schema.data?.data.config.pages??[])for(const field of page.fields){
   const value=cfg[field.key];
   if(field.store!=='config'||field.type!=='int'||value===null||value===undefined)continue;
   if(typeof value!=='number'||!Number.isInteger(value)||value<field.min!||value>field.max!)errors['config-'+field.key]=`Use a whole number from ${field.min} to ${field.max}.`;
  }
  return errors;
 }
 /** What the person can change, as one string: the save bar is lit only when it differs from what loaded.
 * It counts the providers `savedProviders` would send, so opening one and closing it again is not a change. */
 const snapshot=(value:Settings,lines:string,lifetimes:SessionText,values:Record<string,ConfigValue|null>)=>JSON.stringify([value.site_url,lines,value.signup,value.anonymous,value.manual_linking,lifetimes,Object.entries(values).filter(([,member])=>member!==null).sort(),
  Object.entries(savedProviders(value.providers)).map(([name,entry])=>[name,entry.enabled,entry.client_id,entry.url,Boolean(entry.secret),Boolean(entry.skip_nonce_check),Boolean(entry.email_optional)]).sort()]);
 useEffect(()=>{
  if(!current)return;
  // A refresh of the same revision (polling a pending apply) must not wipe what the person is typing.
  if(loadedRevision.current===current.revision&&baseline!=='')return;
  loadedRevision.current=current.revision;
  const saved=current.settings??EMPTY;setValidated(false);
  const loaded={...saved,providers:Object.fromEntries(Object.entries(saved.providers).map(([name,entry])=>[name,{...entry,secret:''}]))};
  setForm(loaded);
  const lines=saved.redirect_urls.join('\n');setRedirects(lines);
  const lifetimes=saved.session??EMPTY.session,hours=(seconds:number|null)=>seconds===null?'':String(seconds/3600);
  const text={token:lifetimes.access_token_seconds===null?'':String(lifetimes.access_token_seconds),
   timebox:hours(lifetimes.timebox_seconds),inactivity:hours(lifetimes.inactivity_seconds)};
  const savedConfig=saved.config??{};setCfg(savedConfig);
  setSession(text);setBaseline(snapshot(loaded,lines,text,savedConfig));
 },[current?.revision,current?.settings]);
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 useEffect(()=>{if(!open)return;const close=(event:KeyboardEvent)=>{if(event.key==='Escape'){setOpen('');setTimeout(()=>opener.current?.focus(),0);}};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[open]);
 /** Closing the panel puts focus back on the provider that opened it. */
 function close(){setOpen('');setTimeout(()=>opener.current?.focus(),0);}
 const dirty=baseline!==''&&snapshot(form,redirects,session,cfg)!==baseline;
 const blank:Provider={enabled:false,client_id:'',secret:'',url:''};
 const provider=(name:string,change:Partial<Provider>)=>setForm(value=>({...value,providers:{...value.providers,[name]:{...blank,...value.providers[name],...change}}}));
 function remove(name:string){setForm(value=>{const providers={...value.providers};delete providers[name];return {...value,providers};});setOpen('');}
 function discard(){if(!current)return;setValidated(false);setError('');
  const saved=current.settings??EMPTY;
  setForm({...saved,providers:Object.fromEntries(Object.entries(saved.providers).map(([name,entry])=>[name,{...entry,secret:''}]))});
  setRedirects(saved.redirect_urls.join('\n'));setCfg(saved.config??{});
  const lifetimes=saved.session??EMPTY.session,hours=(seconds:number|null)=>seconds===null?'':String(seconds/3600);
  setSession({token:lifetimes.access_token_seconds===null?'':String(lifetimes.access_token_seconds),timebox:hours(lifetimes.timebox_seconds),inactivity:hours(lifetimes.inactivity_seconds)});}
 async function save(){
  if(busy)return;
  const kept=savedProviders(form.providers);
  const invalid={...signInErrors(form.site_url,redirects,kept),...sessionErrors(session),...configErrors()};setValidated(true);const first=Object.keys(invalid)[0];
  if(first){const target=first.startsWith('config-')?(schema.data?.data.config.pages.find(item=>item.fields.some(field=>'config-'+field.key===first))?.id as TabId??'sessions'):first.startsWith('session')?'sessions':first.startsWith('site')||first==='redirects'?'urls':'signin';setTab(target);
   if(target==='signin'&&first.includes('-')){setOpen(first.split('-')[0]!);}
   setTimeout(()=>document.getElementById(first)?.focus(),50);return;}
  setBusy(true);setError('');
  try {
   const providers=Object.fromEntries(Object.entries(kept).map(([name,{enabled,client_id,secret,url,skip_nonce_check,email_optional}])=>
    [name,{enabled,client_id:name==='google'?client_id.split(',').map(part=>part.trim()).join(','):client_id,secret:secret??'',url,skip_nonce_check:Boolean(skip_nonce_check),email_optional:Boolean(email_optional)}]));
   await request(path+'/sign-in','PUT',{base_revision:current?.revision,site_url:form.site_url.trim(),redirect_urls:redirects.split('\n').map(line=>line.trim()).filter(Boolean),
    signup:form.signup,anonymous:form.anonymous,manual_linking:form.manual_linking,config:cfg,providers,
    session:{access_token_seconds:secondsOrNull(session.token),timebox_seconds:hoursOrNull(session.timebox),
     inactivity_seconds:hoursOrNull(session.inactivity)}});
   setOpen('');data.refresh();
  } catch(e){setError((e as Error).message);} finally{setBusy(false);}
 }
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Select and copy the address manually.');}}
 const active=TABS.find(item=>item.id===tab)!;
 const meta=open?PROVIDERS[open]:undefined,entry=open?form.providers[open]??blank:blank;
 const stateText=current?(current.state==='applied'?'Applied':current.state==='pending'?'Applying…':current.state==='failed'?'Not applied':'Default settings'):'';
 return <section className="aw" aria-label="Authentication settings">
 <div className="aw-top"><div><h2>Authentication</h2><p className="aw-hint">Sign-in settings of this environment only. Other environments are never touched.</p></div>
  {current&&<span className={'aw-chip '+current.state}>{stateText}</span>}</div>
 <ErrorMessage message={error||data.error}/>{!current&&data.loading?<Loading/>:current&&<div className="aw-layout" inert={open?true:undefined}>
  <nav className="aw-nav" aria-label="Authentication pages">{TABS.map(item=><button key={item.id} type="button" className={item.id===tab?'active':''} aria-current={item.id===tab?'page':undefined} onClick={()=>setTab(item.id)}>{item.label}</button>)}</nav>
  <div className="aw-pane">
   <h3>{active.label}</h3><p className="aw-hint aw-lead">{active.hint}</p>
   {current.state==='failed'&&current.failure&&<p className="aw-notice" role="status">{current.failure}</p>}
   {tab==='signin'&&<>
    <h4>User signups</h4>
    <div className="aw-group">
     <Switch id="allow-signup" checked={form.signup} disabled={busy} label="Allow new users to sign up" hint="If this is off, new users cannot sign up to your application." onChange={value=>setForm({...form,signup:value})}/>
     <Switch id="allow-manual-linking" checked={form.manual_linking} disabled={busy} label="Allow manual linking" hint="Lets signed-in people link another sign-in method to their account." onChange={value=>setForm({...form,manual_linking:value})}/>
     <Switch id="allow-anonymous" checked={form.anonymous} disabled={busy} label="Allow anonymous sign-ins" hint="Lets people use your application before they create an account." onChange={value=>setForm({...form,anonymous:value})}/>
    </div>
    <h4>Auth providers</h4>
    <p className="aw-hint">Open a provider to turn it on and enter its credentials.</p>
    <ul className="aw-providers">{Object.entries(PROVIDERS).map(([name,info])=>{const on=form.providers[name]?.enabled===true,bad=Object.keys(fields).some(key=>key.startsWith(name+'-'));
     return <li key={name}><button type="button" className="aw-provider" onClick={event=>{opener.current=event.currentTarget;setOpen(name);}}><ProviderIcon name={name} label={info.label}/><span className="aw-name">{info.label}</span><span className={'aw-badge'+(bad?' bad':on?' on':'')}>{bad?'Needs attention':on?'Enabled':'Disabled'}</span></button></li>;})}</ul>
   </>}
   {tab==='urls'&&<>
    <div className="aw-field"><label htmlFor="site-url">Site URL</label>
     <input id="site-url" type="url" required maxLength={2048} {...field('site-url')} value={form.site_url} onChange={event=>setForm({...form,site_url:event.target.value})} placeholder="https://app.example.com"/>
     {fieldMessage('site-url')}<p className="aw-hint">The default address Auth sends people to when no other allowed address applies. Wildcards cannot be used here.</p></div>
    <div className="aw-field"><label htmlFor="redirects">Redirect URLs</label>
     <textarea id="redirects" rows={5} {...field('redirects')} value={redirects} onChange={event=>setRedirects(event.target.value)} placeholder={'https://app.example.com/**\nmyapp://callback'}/>
     {fieldMessage('redirects')}<p className="aw-hint">One address per line. Wildcards are allowed: <code>*</code> and <code>**</code> match parts of an address. Total: {redirects.split('\n').map(line=>line.trim()).filter(Boolean).length}.</p></div>
   </>}
   {CONFIG_TABS.includes(tab)&&tab!=='sessions'&&<ConfigFields fields={configFields(tab)} cfg={cfg} setCfg={setCfg} busy={busy} errors={fields} persisted={current?.settings?.config??{}} failed={schema.error}/>}
   {tab==='sessions'&&<>
    <div className="aw-field"><label htmlFor="session-token">Access token expiry time (seconds)</label>
     <input id="session-token" type="number" min={60} max={86400} step={1} placeholder="3600" {...field('session-token')} value={session.token} onChange={event=>setSession({...session,token:event.target.value})}/>
     {fieldMessage('session-token')}<p className="aw-hint">How long an access token is valid before it must be refreshed. Empty keeps the default Auth applies.</p></div>
    <div className="aw-field"><label htmlFor="session-timebox">Time-box user sessions (hours)</label>
     <input id="session-timebox" type="number" min={1} max={87600} step={1} {...field('session-timebox')} value={session.timebox} onChange={event=>setSession({...session,timebox:event.target.value})}/>
     {fieldMessage('session-timebox')}<p className="aw-hint">The longest a person stays signed in from their first sign-in. Empty means no limit.</p></div>
    <div className="aw-field"><label htmlFor="session-inactivity">Inactivity timeout (hours)</label>
     <input id="session-inactivity" type="number" min={1} max={87600} step={1} {...field('session-inactivity')} value={session.inactivity} onChange={event=>setSession({...session,inactivity:event.target.value})}/>
     {fieldMessage('session-inactivity')}<p className="aw-hint">How long a person can be idle before they must sign in again. Empty means no limit.</p></div>
    <ConfigFields fields={configFields('sessions')} cfg={cfg} setCfg={setCfg} busy={busy} errors={fields} persisted={current?.settings?.config??{}} failed={schema.error}/>
   </>}
  </div>
 </div>}
 {current&&<div className="aw-savebar" role="region" aria-label="Save changes" inert={open?true:undefined}>
  <p role="status">{dirty?'You have unsaved changes.':current.state==='failed'?'Not applied. Save again to retry.':current.state==='pending'?'Auth is restarting with the saved settings.':'Saving restarts this environment\'s Auth. Users and sessions are kept.'}</p>
  <div className="aw-actions"><button type="button" className="aw-ghost" disabled={busy||!dirty} onClick={discard}>Discard</button>
   <button type="button" className="aw-primary" disabled={busy||(!dirty&&current.state!=='failed')||current.state==='pending'||Boolean(schema.error)} onClick={()=>void save()}><Save aria-hidden="true"/>Save and apply</button></div>
 </div>}
 {open&&meta&&<div className="aw-scrim" onMouseDown={event=>{if(event.target===event.currentTarget)close();}}><div className="aw-drawer" role="dialog" aria-modal="true" aria-labelledby="aw-drawer-title">
  <div className="aw-drawer-head"><ProviderIcon name={open} label={meta.label}/><h3 id="aw-drawer-title">{meta.label}</h3><button type="button" className="aw-close" aria-label="Close" onClick={close}><X aria-hidden="true"/></button></div>
  <div className="aw-drawer-body">
   <Switch id={open+'-enabled'} checked={entry.enabled} disabled={busy} label={'Enable Sign in with '+meta.label} hint="Turns this provider on for this environment only." onChange={value=>provider(open,{enabled:value})}/>
   <div className="aw-field"><label htmlFor={open+'-client'}>{open==='google'?'Client IDs':'Client ID'}</label><input id={open+'-client'} maxLength={512} autoFocus aria-required={entry.enabled} {...field(open+'-client')} value={entry.client_id} onChange={event=>provider(open,{client_id:event.target.value})}/>{fieldMessage(open+'-client')}{open==='google'&&<p className="aw-hint">Comma-separated list of client IDs for Web, OAuth, Android apps, One Tap and Chrome extensions.</p>}</div>
   <div className="aw-field"><label htmlFor={open+'-secret'}>Client secret</label><input id={open+'-secret'} type="password" autoComplete="off" maxLength={4096} aria-required={entry.enabled&&!entry.secret_set} {...field(open+'-secret')} value={entry.secret??''}
    placeholder={entry.secret_set?'Saved. Leave empty to keep it.':''} onChange={event=>provider(open,{secret:event.target.value})}/>{fieldMessage(open+'-secret')}
    <p className="aw-hint">The secret stays on the server. It is never shown again.</p></div>
   {meta.url&&<div className="aw-field"><label htmlFor={open+'-url'}>Server address{meta.url==='optional'?' (optional)':''}</label>
    <input id={open+'-url'} type="url" maxLength={2048} aria-required={meta.url==='required'&&entry.enabled} {...field(open+'-url')} value={entry.url} onChange={event=>provider(open,{url:event.target.value})} placeholder="https://"/>{fieldMessage(open+'-url')}</div>}
   {NONCE_PROVIDERS.includes(open)&&<Switch id={open+'-nonce'} checked={Boolean(entry.skip_nonce_check)} disabled={busy} label="Skip nonce checks" hint="Allows ID tokens with any nonce to be accepted, which is less secure. Useful when you cannot get the nonce used to issue the token, as on iOS." onChange={value=>provider(open,{skip_nonce_check:value})}/>}
   <Switch id={open+'-email-optional'} checked={Boolean(entry.email_optional)} disabled={busy} label="Allow users without an email" hint="Lets a person sign in when this provider does not return an email address." onChange={value=>provider(open,{email_optional:value})}/>
   <div className="aw-field"><label htmlFor="callback">Callback URL (for OAuth)</label>
    <div className="aw-copy"><input id="callback" readOnly value={current?.callback_url??''}/><button type="button" className="aw-ghost" onClick={()=>void copy(current!.callback_url)}><Copy aria-hidden="true"/>Copy</button></div>
    <p className="aw-hint" role="status">{copied||'Register this address with '+meta.label+' for this environment.'}</p></div>
   {(form.providers[open]?.secret_set||current?.settings?.providers[open])&&<button type="button" className="aw-danger" disabled={busy} onClick={()=>remove(open)}><Trash2 aria-hidden="true"/>Remove saved credentials</button>}
  </div>
  <div className="aw-drawer-foot"><p className="aw-hint">Changes apply when you save from the bar at the bottom of the page.</p><button type="button" className="aw-primary" onClick={close}>Done</button></div>
 </div></div>}
 </section>;
}
