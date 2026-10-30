import {useEffect,useRef,useState} from 'react';
import {RotateCcw,Save} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type Template={kind:string;label:string;subject:string;body:string;default_subject:string;default_body:string;customized:boolean};
type Answer={state:'unconfigured'|'pending'|'applied'|'failed';revision:number;failure:string|null;templates:Template[];variables:{name:string;about:string}[];mail_enabled:boolean};
type Form=Record<string,{subject:string;body:string}>;
const MAX_BODY=32768,MAX_SUBJECT=200;
/** The server counts bytes, so an Arabic subject is checked the same way. */
const bytes=(text:string)=>new TextEncoder().encode(text).length;
const pairs=(text:string)=>text.split('{{').length===text.split('}}').length;
/** What a preview shows for each variable: a stand-in, never a real address or token. */
const SAMPLE:Record<string,string>={'.ConfirmationURL':'#','.Token':'123456','.TokenHash':'hash','.SiteURL':'https://app.example.com','.RedirectTo':'https://app.example.com/welcome',
 '.Email':'person@example.com','.NewEmail':'new@example.com','.Data':'{}'};
const preview=(body:string)=>body.replace(/\{\{\s*(\.[A-Za-z]+)\s*\}\}/g,(_,name:string)=>SAMPLE[name]??'');

/** Emails > Templates, as the Supabase cloud shows it: the six messages, each with a subject and an HTML body, a list of the
 * variables Auth fills, and a preview. The body is saved on the server and Auth fetches it from the console when it sends, so
 * the page says so; saving restarts this environment's Auth (a few seconds), as every Authentication change does. */
export function TemplatesPanel({path,request,onSaved}:{path:string;request:Api;onSaved:()=>void}){
 const data=useData<{data:Answer}>(signal=>request(path+'/mail-templates','GET',undefined,signal),[path,request]);
 const latest=useRef<Answer|undefined>(undefined);
 if(data.data?.data)latest.current=data.data.data;
 const current=data.data?.data??latest.current;
 const loadedRevision=useRef<number|undefined>(undefined);
 const [form,setForm]=useState<Form>({}),[loaded,setLoaded]=useState('');
 const [kind,setKind]=useState('confirmation'),[view,setView]=useState<'source'|'preview'>('source');
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{
  if(!current)return;
  if(loadedRevision.current===current.revision&&loaded!=='')return;
  loadedRevision.current=current.revision;
  const next:Form=Object.fromEntries(current.templates.map(item=>[item.kind,{subject:item.subject,body:item.body}]));
  setForm(next);setLoaded(JSON.stringify(next));
 },[current?.revision]);
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 if(!current)return data.error?<ErrorMessage message={data.error}/>:<Loading/>;
 const selected=current.templates.find(item=>item.kind===kind)??current.templates[0]!;
 const value=form[selected.kind]??{subject:selected.subject,body:selected.body};
 const was:Form=loaded?JSON.parse(loaded):{};
 const changed=current.templates.filter(item=>JSON.stringify(form[item.kind])!==JSON.stringify(was[item.kind])).map(item=>item.kind);
 const problem=(item:{subject:string;body:string})=>/[\r\n]/.test(item.subject)?'The subject must be one line.':bytes(item.subject)>MAX_SUBJECT?`The subject can be up to ${MAX_SUBJECT} bytes (about 100 Arabic letters).`
  :bytes(item.body)>MAX_BODY?'The message is larger than 32 KiB.':!pairs(item.subject)||!pairs(item.body)?'Every {{ needs a matching }}.':'';
 const shown=problem(value);
 const anyProblem=changed.some(name=>problem(form[name]!)!=='');
 const set=(change:Partial<{subject:string;body:string}>)=>setForm({...form,[selected.kind]:{...value,...change}});
 async function save(){
  if(busy||anyProblem)return;
  setBusy(true);setError('');
  try{
   await request(path+'/mail-templates','PUT',{base_revision:current!.revision,templates:Object.fromEntries(changed.map(name=>[name,form[name]]))});
   data.refresh();onSaved();
  }catch(e){setError((e as Error).message);data.refresh();}finally{setBusy(false);}
 }
 return <>
  <ErrorMessage message={error||data.error}/>
  {current.state==='failed'&&current.failure&&<p className="aw-notice" role="status">{current.failure}</p>}
  {!current.mail_enabled&&<p className="aw-notice" role="status">Custom SMTP is off for this environment, so these messages are not sent and Auth is not given your templates. Turn on custom SMTP under SMTP Settings first.</p>}
  <p className="aw-hint aw-lead">Customize the messages this environment's Auth sends. Auth fetches the message from this console each time it sends one, so a template needs custom SMTP to be delivered. Saving restarts this environment's Auth.</p>
  <nav className="aw-sub" aria-label="Email templates">{current.templates.map(item=>
   <button key={item.kind} type="button" className={item.kind===selected.kind?'active':''} aria-current={item.kind===selected.kind?'page':undefined} onClick={()=>setKind(item.kind)}>
    {item.label}{(item.customized||JSON.stringify(form[item.kind])!==JSON.stringify(was[item.kind]))&&<><span className="aw-dot" aria-hidden="true"/><span className="sr-only"> (customized)</span></>}</button>)}</nav>
  <div className="aw-field aw-wide"><label htmlFor="tpl-subject">Subject heading</label>
   <input id="tpl-subject" disabled={busy} value={value.subject} aria-invalid={shown.includes('subject')} onChange={event=>set({subject:event.target.value})}/></div>
  <div className="aw-field aw-wide">
   <div className="aw-tabs-inline" role="group" aria-label="Message view">
    <button type="button" className={view==='source'?'active':''} aria-pressed={view==='source'} onClick={()=>setView('source')}>Source</button>
    <button type="button" className={view==='preview'?'active':''} aria-pressed={view==='preview'} onClick={()=>setView('preview')}>Preview</button></div>
   {view==='source'
    ?<><label htmlFor="tpl-body" className="sr-only">Message body</label><textarea id="tpl-body" className="aw-code" rows={16} spellCheck={false} disabled={busy} value={value.body} aria-invalid={shown!==''&&!shown.includes('subject')} onChange={event=>set({body:event.target.value})}/></>
    :<iframe className="aw-preview" title="Preview of the message" sandbox="" {...{csp:"default-src 'none'; style-src 'unsafe-inline'; img-src data:"}} srcDoc={preview(value.body)}/>}
   {shown&&<p className="aw-error" role="alert">{shown}</p>}
   <p className="aw-hint">HTML. Auth fills the variables below when it sends; the preview shows stand-in values.</p></div>
  <h4>Variables</h4>
  <ul className="aw-vars">{current.variables.map(item=><li key={item.name}><code>{item.name}</code><span>{item.about}</span></li>)}</ul>
  <div className="aw-actions aw-inline">
   <button type="button" className="aw-ghost" disabled={busy||(value.subject===selected.default_subject&&value.body===selected.default_body)}
    onClick={()=>set({subject:selected.default_subject,body:selected.default_body})}><RotateCcw aria-hidden="true"/>Reset to default</button>
   <button type="button" className="aw-primary" disabled={busy||data.loading||anyProblem||changed.length===0||current.state==='pending'} onClick={()=>void save()}><Save aria-hidden="true"/>Save changes</button></div>
  {current.state==='pending'&&<p className="aw-hint" role="status">Restarting this environment's Auth to apply the change…</p>}
 </>;
}
