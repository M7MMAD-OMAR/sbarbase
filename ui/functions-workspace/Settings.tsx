import {useState} from 'react';
import {Eye,EyeOff,Trash2} from 'lucide-react';
import {ApiError,type Api} from '../api';

import type {ApiKey,FunctionRow} from './types';
import {LANGUAGES,snippet,type Language} from './snippets';
import {Chip,ConfirmByName,CopyButton,Tabs,Problem} from './kit';

/** The pieces of Settings that need no server: how the function is called, and how to work on it locally. */
export function Settings({fn,path,request,origin,environmentId,onDeleted}:{fn:FunctionRow;path:string;request:Api;origin:string;environmentId:string;onDeleted:()=>void}){
 const [language,setLanguage]=useState<Language>('curl'),[key,setKey]=useState<string>(),[keyError,setKeyError]=useState(''),[confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const text=snippet(language,{origin,path:fn.path,name:fn.name,key});
 const command=`SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts ${origin} ${environmentId} supabase/functions`;
 async function reveal(){
  if(key){setKey(undefined);return;}
  setKeyError('');
  try{
   const keys=(await request(path+'/keys')).data as ApiKey[];
   const found=keys.find(item=>item.kind==='publishable'&&!item.revoked_at&&item.token);
   if(found?.token)setKey(found.token);else setKeyError('This environment has no publishable key that can be shown. Create one under API keys.');
  }catch(reason){setKeyError((reason as Error).message);}
 }
 async function remove(){
  setBusy(true);setError('');
  try{await request(`${path}/functions/${fn.name}`,'DELETE');onDeleted();}
  catch(reason){setError(reason instanceof ApiError?reason.message:'Unable to delete the function.');setBusy(false);}
 }
 return <div className="fw-stack narrow">
  <section className="fw-card"><h3>Function configuration</h3>
   <div className="fw-field"><div className="fw-text"><span className="fw-label">Name</span><p className="fw-hint">Your slug and endpoint URL stay the same.</p></div><code className="fw-value">{fn.name}</code></div>
   <div className="fw-field"><div className="fw-text"><span className="fw-label">Verify JWT</span><p className="fw-hint">The function refuses a call without a valid token in the <code>Authorization</code> header. The publishable key satisfies it. Turn it off for webhooks and check tokens in your own code.</p></div>
    <Chip tone={fn.verify_jwt?'ok':'muted'}>{fn.verify_jwt?'On':'Off'}</Chip></div>
   <p className="fw-hint">It is set when you deploy, with <code>verify_jwt</code> in <code>config.toml</code> or the checkbox in Add function.</p></section>
  <section className="fw-card"><div className="fw-card-head"><h3>Invoke function</h3>
   <button type="button" className="fw-ghost" onClick={()=>void reveal()}>{key?<EyeOff aria-hidden="true"/>:<Eye aria-hidden="true"/>}{key?'Hide publishable key':'Show publishable key'}</button></div>
   <Tabs label="Client" prefix="invoke" tabs={LANGUAGES} value={language} onChange={setLanguage}/>
   <div role="tabpanel" id={`invoke-panel-${language}`} aria-labelledby={`invoke-tab-${language}`}><pre className="fw-snippet" tabIndex={0}>{text}</pre></div>
   <div className="fw-actions"><CopyButton text={text} label="Copy the example"/></div>
   <Problem message={keyError}/>
   <p className="fw-hint">The publishable key is safe in a browser: row level security still applies. Never put a secret key in client code.</p></section>
  <section className="fw-card"><h3>Develop locally</h3>
   <p className="fw-hint">Deploy a folder of functions from your project, with <code>_shared</code> code and <code>config.toml</code> as with the Supabase CLI. Use Download in the header to get this function's files.</p>
   <div className="fw-copyline"><code>{command}</code><CopyButton quiet text={command} label="Copy the deploy command"/></div></section>
  <section className="fw-card danger"><h3>Delete function</h3>
   <p className="fw-hint">Once your function is deleted, it can no longer be restored. Make a backup first: Download gives you its files.</p>
   <Problem message={error}/>
   {confirm?<ConfirmByName name={fn.name} busy={busy} action="Delete function" onConfirm={()=>void remove()} onCancel={()=>setConfirm(false)}/>
    :<button type="button" className="fw-danger" onClick={()=>setConfirm(true)}><Trash2 aria-hidden="true"/>Delete edge function</button>}</section>
 </div>;
}
