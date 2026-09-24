import {useEffect,useState} from 'react';
import {Code2,Plus,Square,Trash2,KeyRound,Copy} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading,Empty} from './components';

type Fn={name:string;verify_jwt:boolean;updated_at:number;size:number;path:string};
type State={desired:'on'|'off';state:'off'|'pending'|'on'|'failed';failure:string|null;functions:Fn[];secrets:string[]};
const LABELS:Record<State['state'],string>={off:'Off',pending:'Applying…',on:'On',failed:'Not applied'};
const STARTER=`Deno.serve(async (request) => {
  const { name } = await request.json().catch(() => ({}))
  return Response.json({ message: \`Hello \${name ?? 'world'}\` })
})
`;

/** Edge Functions for this environment: Deno functions called at /functions/v1/<name>, as on Supabase. */
export function FunctionsSection({path,request,environmentId}:{path:string;request:Api;environmentId:string}){
 const data=useData<{data:State}>(signal=>request(path+'/functions','GET',undefined,signal),[path,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[editing,setEditing]=useState(false),[confirm,setConfirm]=useState('');
 const [name,setName]=useState(''),[code,setCode]=useState(STARTER),[verify,setVerify]=useState(true);
 const [secretName,setSecretName]=useState(''),[secretValue,setSecretValue]=useState(''),[copied,setCopied]=useState('');
 const current=data.data?.data;
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 async function act(run:()=>Promise<unknown>){setBusy(true);setError('');try{await run();data.refresh();return true;}catch(e){setError((e as Error).message);return false;}finally{setBusy(false);}}
 async function deploy(){if(await act(()=>request(`${path}/functions/${name.trim()}`,'PUT',{files:{'index.ts':code},verify_jwt:verify}))){setEditing(false);setName('');setCode(STARTER);}}
 async function saveSecret(){if(await act(()=>request(path+'/function-secrets','PUT',{secrets:{[secretName.trim()]:secretValue}}))){setSecretName('');setSecretValue('');}}
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the text manually.');}}
 const command=`SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts ${location.origin} ${environmentId} supabase/functions`;
 return <section className="details"><h2>Edge Functions</h2>
 <p className="muted small">Deno functions called with <code>supabase.functions.invoke()</code>, as on Supabase. Each gets <code>SUPABASE_URL</code>, <code>SUPABASE_ANON_KEY</code>, <code>SUPABASE_SERVICE_ROLE_KEY</code> and the secrets below.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  <p><span className={'state '+(current.state==='on'?'applied':current.state==='failed'?'failed':current.state==='pending'?'running':'')}>{LABELS[current.state]}</span></p>
  {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
  {current.functions.length?<div className="table-wrap compact"><table><thead><tr><th>Function</th><th>JWT check</th><th>Deployed</th><th>Action</th></tr></thead><tbody>
   {current.functions.map(fn=><tr key={fn.name}><td><code>{fn.name}</code></td><td>{fn.verify_jwt?'On':'Off'}</td><td>{new Date(fn.updated_at).toLocaleString()}</td>
    <td>{confirm===fn.name?<div className="confirm"><span>Delete {fn.name}?</span><button className="danger" disabled={busy} onClick={()=>void act(()=>request(`${path}/functions/${fn.name}`,'DELETE')).then(()=>setConfirm(''))}>Delete</button><button onClick={()=>setConfirm('')}>Cancel</button></div>
     :<button disabled={busy} onClick={()=>setConfirm(fn.name)}><Trash2 aria-hidden="true"/>Delete</button>}</td></tr>)}</tbody></table></div>
   :<Empty>No functions yet. Deploy a Supabase functions folder with the command below, or write one here.</Empty>}
  <h3>Deploy from a project folder</h3>
  <p className="small muted">From a checkout of Sbarbase, point at your project's <code>supabase/functions</code>. <code>_shared</code> and <code>verify_jwt</code> in <code>config.toml</code> work as with the Supabase CLI.</p>
  <div className="form-row"><input readOnly value={command} aria-label="Deploy command"/><button onClick={()=>void copy(command)}><Copy aria-hidden="true"/>Copy</button></div>
  <p className="small muted" role="status">{copied}</p>
  {editing?<div className="provider">
   <label htmlFor="function-name">Name</label><input id="function-name" value={name} onChange={event=>setName(event.target.value)} placeholder="hello-world"/>
   <label htmlFor="function-code">index.ts</label><textarea id="function-code" rows={10} value={code} onChange={event=>setCode(event.target.value)}/>
   <label className="check"><input type="checkbox" checked={verify} onChange={event=>setVerify(event.target.checked)}/>Require a valid JWT (turn off for webhooks)</label>
   <div className="form-row actions"><button className="primary" disabled={busy||!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name.trim())} onClick={()=>void deploy()}><Code2 aria-hidden="true"/>Deploy</button><button onClick={()=>setEditing(false)}>Cancel</button></div>
  </div>:<div className="form-row"><button onClick={()=>setEditing(true)}><Plus aria-hidden="true"/>Write a function</button>
   {(current.state==='on'||current.desired==='on')&&<button disabled={busy||current.state==='pending'} onClick={()=>void act(()=>request(path+'/functions','PUT',{enabled:false}))}><Square aria-hidden="true"/>Turn off Edge Functions</button>}
   {current.state!=='on'&&current.desired==='off'&&current.functions.length>0&&<button className="primary" disabled={busy} onClick={()=>void act(()=>request(path+'/functions','PUT',{enabled:true}))}>Turn on Edge Functions</button>}</div>}
  <h3>Secrets</h3>
  <p className="small muted">Read in a function with <code>Deno.env.get('NAME')</code>. Values are never shown again after you save them.</p>
  {current.secrets.length>0&&<p className="small">{current.secrets.map(secret=><span key={secret} className="secret-chip"><code>{secret}</code><button aria-label={`Remove ${secret}`} disabled={busy} onClick={()=>void act(()=>request(path+'/function-secrets','PUT',{secrets:{[secret]:null}}))}><Trash2 aria-hidden="true"/></button></span>)}</p>}
  <div className="form-row"><input aria-label="Secret name" placeholder="STRIPE_SECRET_KEY" value={secretName} onChange={event=>setSecretName(event.target.value.toUpperCase())}/>
   <input aria-label="Secret value" type="password" placeholder="value" value={secretValue} onChange={event=>setSecretValue(event.target.value)}/>
   <button disabled={busy||!/^[A-Z_][A-Z0-9_]{0,127}$/.test(secretName)||!secretValue} onClick={()=>void saveSecret()}><KeyRound aria-hidden="true"/>Save secret</button></div>
 </>}</section>;
}
