import {DataApiSection} from './DataApi';
import {useEffect,useState} from 'react';
import {ArrowLeft,Copy,ExternalLink,Play,Square,Database,Shield,Layers,Activity,KeyRound,LayoutGrid} from 'lucide-react';
import {useData,type Api,type Organization,type Environment} from './api';
import {mailDetails,mailState,type MailEntry} from './mail';
import {ErrorMessage,Loading,Refresh} from './components';
import {SignInSection} from './SignIn';
import {RealtimeSection} from './Realtime';
import {ScheduledJobsSection} from './ScheduledJobs';
import {MetricsSection,LogsSection} from './Observe';
import {FunctionsSection} from './Functions';
import {DatabaseSection} from './Database';
import {SigningSection} from './Signing';
import {shareError} from './settings-validation';
import {StudioSection} from './Studio';
import {ApiKeysSection} from './ApiKeys';
type Share={share:number;default:number;ceiling:number;operator:boolean;total?:number;allocated?:number};
/** The mail answer carries the runtime beside its entry, so the host commands can name it. */
type MailAnswer=MailEntry&{runtime?:string};
/** The environment's guaranteed share of the gateway. Installation operators change it when a saturation notice arrives. */
function ShareSection({path,request}:{path:string;request:Api}){
 const share=useData<{data:Share}>(signal=>request(path+'/share','GET',undefined,signal),[path,request]);
 const [value,setValue]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
 const [fieldError,setFieldError]=useState('');
 const current=share.data?.data;
 useEffect(()=>{if(current)setValue(String(current.share));},[current?.share]);
 async function save(){if(!current)return;const invalid=shareError(value,current.ceiling);setFieldError(invalid);if(invalid){document.getElementById('share')?.focus();return;}setBusy(true);setError('');setSaved('');
  try{await request(path+'/share','PUT',{share:Number(value)});share.refresh();setSaved('Saved. It applies to the next request.');}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="details"><h2>Gateway share</h2><p className="muted small">Requests this environment can always run at once. When the server has idle room it may borrow more, up to {current?.ceiling??24}; neighbours that are active keep their own share.</p>
 <ErrorMessage message={error||share.error}/>{share.loading?<Loading/>:current&&<><p><strong>{current.share}</strong> <span className="muted small">requests at a time{current.share===current.default?' (default)':''}.{current.operator?` ${current.allocated} of ${current.total} allocated across the installation.`:''}</span></p>
 {current.operator?<><div className="form-row"><label className="sr-only" htmlFor="share">Share</label><input id="share" type="number" min={1} max={current.ceiling} step={1} value={value} aria-invalid={Boolean(fieldError)} aria-describedby={fieldError?'share-error':undefined} onChange={event=>{setValue(event.target.value);setFieldError(shareError(event.target.value,current.ceiling));}}/>
 <button className="primary" disabled={busy||Boolean(shareError(value,current.ceiling))||Number(value)===current.share} onClick={()=>void save()}>Save share</button></div>{fieldError&&<p id="share-error" className="field-error" role="alert">{fieldError}</p>}</>:<p className="small muted">The installation operator can change it.</p>}
 <p className="small muted" role="status">{saved}</p></>}</section>;
}
export function Connection({environment,organization,request,onBack}:{environment:Environment;organization:Organization;request:Api;onBack:()=>void}){
 const path=`/environments/${environment.id}`,canWrite=organization.role!=='viewer';
 const [section,setSection]=useState('overview');
 const sections=[{id:'overview',label:'Overview',Icon:LayoutGrid},...(canWrite?[{id:'database',label:'Database connection',Icon:Database},{id:'authentication',label:'Sign-in settings',Icon:Shield},{id:'services',label:'Services',Icon:Layers}]:[]),{id:'usage',label:'Usage',Icon:Activity},{id:'keys',label:'API keys',Icon:KeyRound}];
 const activeSection=sections.some(item=>item.id===section)?section:'overview';
 const info=useData<{apiPath:string;services:string[]}|undefined>(signal=>activeSection==='overview'?request(path+'/connection','GET',undefined,signal):Promise.resolve(undefined),[environment.id,request,activeSection]);
 const mail=useData<{data:MailAnswer}|undefined>(signal=>activeSection==='overview'?request(path+'/mail','GET',undefined,signal):Promise.resolve(undefined),[environment.id,request,activeSection]);
 const mailView=mailState(mail.data?.data),mailRows=mailDetails(mail.data?.data);
 const runtime=mail.data?.data?.runtime;
 // The two commands an operator runs on the host. The password is read on stdin by the first
 // and never travels through this API, so there is no console form for it.
 const mailCommands=runtime?[`/usr/bin/python3 lab/mail_config.py write .secrets/upstream/${runtime}-mail.json --stdin`,
  `/usr/bin/python3 lab/durable_runtime.py mail ${runtime}`].join('\n'):'';
 const [copied,setCopied]=useState('');
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the value manually.');}}
 return <><button className="back" onClick={onBack}><ArrowLeft aria-hidden="true"/>Environments</button><div className="page-heading"><div><h1>{environment.name}</h1><p className="muted">Open Supabase Studio or connect your application. Advanced settings are available below.</p></div></div><nav className="environment-nav" aria-label="Environment sections">{sections.map(({id,label,Icon})=><button key={id} className={activeSection===id?'selected':''} aria-current={activeSection===id?'page':undefined} onClick={()=>setSection(id)}><Icon aria-hidden="true"/>{label}</button>)}</nav>
 {canWrite&&(activeSection==='overview'||activeSection==='database')&&<StudioSection path={path} request={request} compact={activeSection==='overview'} environmentName={environment.name}/>}
 {activeSection==='overview'&&<div className="environment-overview">
 <ErrorMessage message={info.error}/>{info.error&&<Refresh onClick={info.refresh}/>} {info.loading?<Loading/>:info.data&&<section className="details"><h2>Connection</h2><label htmlFor="project-url">Project URL</label><div className="form-row"><input id="project-url" readOnly value={location.origin+info.data.apiPath}/><button onClick={()=>copy(location.origin+info.data!.apiPath)}><Copy aria-hidden="true"/>Copy URL</button></div><p className="small muted" role="status">{copied}</p><p className="small muted">Available services: {info.data.services.join(', ')}</p></section>}
 <section className="details"><h2>Email</h2><ErrorMessage message={mail.error}/>{mail.error&&<Refresh onClick={mail.refresh}/>} {mail.loading?<Loading/>:<><p><span className={'state '+mailView.state}>{mailView.label}</span></p><p className="muted small">{mailView.text}</p>{mailRows.map(row=><p className="small" key={row.label}><span className="muted">{row.label}: </span>{row.value}</p>)}
  {mailCommands&&(mailView.state==='unconfigured'||mailView.state==='off')&&<><label htmlFor="mail-commands">Set it up on the host</label>
  <textarea id="mail-commands" readOnly rows={2} value={mailCommands}/>
  <div className="form-row"><button onClick={()=>void copy(mailCommands)}><Copy aria-hidden="true"/>Copy</button></div>
  <p className="small muted">The SMTP password never crosses the management API, so this is a host command, not a console form.</p></>}</>}</section>
 </div>}{activeSection==='authentication'&&<div>
 {canWrite&&<SignInSection path={path} request={request}/>}
 {canWrite&&<SigningSection path={path} request={request}/>}
 </div>}{activeSection==='services'&&<div>
 {canWrite&&<RealtimeSection path={path} request={request}/>}
  {canWrite&&<FunctionsSection path={path} request={request} environmentId={environment.id}/>}
  {canWrite&&<ScheduledJobsSection path={path} request={request}/>}
 </div>}{activeSection==='database'&&<div>
 {canWrite&&<DatabaseSection path={path} request={request}/>}
 {canWrite&&<DataApiSection path={path} request={request}/>}
 </div>}{activeSection==='usage'&&<div>
 <ShareSection path={path} request={request}/>
 <MetricsSection path={path} request={request}/>
 {canWrite&&<LogsSection path={path} request={request}/>}
 </div>}{activeSection==='keys'&&<ApiKeysSection path={path} request={request} canWrite={canWrite}/>}</>;
}
