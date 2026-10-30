import {useState} from 'react';
import {Choice,CopyButton,Tabs} from './kit';
import {FileEditor,type Deployed} from './FileEditor';
import {TEMPLATES} from './templates';
import type {Api} from '../api';

type Way='editor'|'command';

/** Two ways to add a function: write it here, from a template, in the editor with a file tree; or deploy a project folder with the
 * command, which stays the way for a whole `supabase/functions` folder with `config.toml`. */
export function AddFunction({path,request,environmentId,origin,existing,onDeployed,onClose,onDirty,guard}:{guard:(action:()=>void)=>void;existing:string[];path:string;request:Api;environmentId:string;origin:string;
 onDeployed:(result:Deployed)=>void;onClose:()=>void;onDirty:(dirty:boolean)=>void}){
 const [way,setWay]=useState<Way>('editor'),[template,setTemplate]=useState(TEMPLATES[0]!.id);
 const command=`SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts ${origin} ${environmentId} supabase/functions`;
 const chosen=TEMPLATES.find(item=>item.id===template)!;
 return <section className="fw-card" aria-label="Add function"><div className="fw-card-head"><h3>Add function</h3><button type="button" className="fw-ghost" onClick={onClose}>Close</button></div>
  <Tabs label="Way to add a function" prefix="add" value={way} onChange={value=>guard(()=>setWay(value))} tabs={[{id:'editor',label:'In the editor'},{id:'command',label:'From a project folder'}]}/>
  <div role="tabpanel" id={`add-panel-${way}`} aria-labelledby={`add-tab-${way}`}>
   {way==='command'?<><p className="fw-hint">From a checkout of Sbarbase, point at your project's <code>supabase/functions</code> folder. <code>_shared</code> and <code>verify_jwt</code> in <code>config.toml</code> work as with the Supabase CLI. This is the way for a whole project.</p>
    <div className="fw-copyline"><code>{command}</code><CopyButton quiet text={command} label="Copy the deploy command"/></div></>
   :<div className="fw-stack"><div className="fw-formrow"><span className="fw-label" id="template-label">Start from</span>
     <Choice label="Template" value={template} options={TEMPLATES.map(item=>({id:item.id,label:item.label,hint:item.hint}))} onChange={value=>guard(()=>setTemplate(value))} wide/>
     <p className="fw-hint">{chosen.hint} Choosing another template replaces what you have written.</p></div>
    <FileEditor key={template} path={path} request={request} initial={chosen.files} verifyJwt={chosen.verify_jwt} existingNames={existing} onDeployed={onDeployed} onDirty={onDirty}/></div>}
  </div></section>;
}
