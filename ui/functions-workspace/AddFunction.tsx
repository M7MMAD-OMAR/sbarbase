import {useState} from 'react';
import {Code2} from 'lucide-react';
import {ApiError,type Api} from '../api';

import {Chip,CopyButton,Tabs,Problem} from './kit';

const STARTER=`Deno.serve(async (request) => {
  const { name } = await request.json().catch(() => ({}))
  return Response.json({ message: \`Hello \${name ?? 'world'}\` })
})
`;
export const NAME=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
type Way='console'|'command';

/** Two ways to deploy today: one file written here, or a project folder with the deploy command. Both stay. */
export function AddFunction({path,request,environmentId,origin,existing,onDeployed,onClose}:{existing:string[];path:string;request:Api;environmentId:string;origin:string;onDeployed:(name:string)=>void;onClose:()=>void}){
 const [way,setWay]=useState<Way>('command'),[name,setName]=useState(''),[code,setCode]=useState(STARTER),[verify,setVerify]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const command=`SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts ${origin} ${environmentId} supabase/functions`;
 const valid=NAME.test(name.trim()),replaces=valid&&existing.includes(name.trim());
 async function deploy(){
  setBusy(true);setError('');
  try{await request(`${path}/functions/${name.trim()}`,'PUT',{files:{'index.ts':code},verify_jwt:verify});onDeployed(name.trim());}
  catch(reason){setError(reason instanceof ApiError&&reason.serverMessage?reason.serverMessage:(reason as Error).message);}
  finally{setBusy(false);}
 }
 return <section className="fw-card" aria-label="Add function"><div className="fw-card-head"><h3>Add function</h3><button type="button" className="fw-ghost" onClick={onClose}>Close</button></div>
  <Tabs label="Way to deploy" prefix="add" value={way} onChange={setWay} tabs={[{id:'command',label:'From a project folder'},{id:'console',label:'Write one file here'}]}/>
  <div role="tabpanel" id={`add-panel-${way}`} aria-labelledby={`add-tab-${way}`}>
   {way==='command'?<><p className="fw-hint">From a checkout of Sbarbase, point at your project's <code>supabase/functions</code> folder. <code>_shared</code> and <code>verify_jwt</code> in <code>config.toml</code> work as with the Supabase CLI. This is the way for several files.</p>
    <div className="fw-copyline"><code>{command}</code><CopyButton quiet text={command} label="Copy the deploy command"/></div></>
   :<><p className="fw-hint">One <code>index.ts</code>, good for a webhook or a first try. Several files and shared code need the project folder way.</p>
    <div className="fw-field stack"><label className="fw-label" htmlFor="function-name">Name</label><input id="function-name" autoComplete="off" spellCheck={false} placeholder="hello-world" value={name} aria-invalid={Boolean(name)&&!valid} onChange={event=>setName(event.target.value)}/>
     {Boolean(name)&&!valid&&<p className="fw-error" role="alert">Use letters, digits, - and _, starting with a letter or digit, at most 64.</p>}
     {replaces&&<p className="fw-hint" role="status">A function named <code>{name.trim()}</code> already exists. Deploying replaces its code and its Verify JWT setting at once.</p>}</div>
    <div className="fw-field stack"><label className="fw-label" htmlFor="function-code">index.ts</label><textarea id="function-code" className="fw-mono" rows={9} spellCheck={false} value={code} onChange={event=>setCode(event.target.value)}/></div>
    <label className="fw-check"><input type="checkbox" checked={verify} onChange={event=>setVerify(event.target.checked)}/>Verify JWT <Chip>turn off for webhooks</Chip></label>
    <Problem message={error}/>
    <div className="fw-actions"><button type="button" className="fw-primary" disabled={busy||!valid} onClick={()=>void deploy()}><Code2 aria-hidden="true"/>{replaces?'Replace function':'Deploy function'}</button></div></>}
  </div></section>;
}
