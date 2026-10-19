import {useState,type FormEvent} from 'react';
import {Bot,Copy,Plus,ShieldAlert} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {ErrorMessage,Loading,Empty} from './components';

type Scope={kind:'all'}|{kind:'organization';id:string}|{kind:'project';id:string};
type Token={id:string;name:string;scope:Scope;read_only:boolean;created_at:number;expires_at:number;last_used_at:number|null;revoked_at:number|null};

/** Personal access tokens for AI agents and tools (docs/guides/agents.md). A token is shown once;
 * the steps to connect an agent are shown with it. */
export function AccessTokens({request,organizations}:{request:Api;organizations:Organization[]}){
 const tokens=useData<{data:Token[]}>(signal=>request('/access-tokens','GET',undefined,signal),[request]);
 const [name,setName]=useState(''),[scopeKind,setScopeKind]=useState<'all'|'organization'|'project'>('all'),[organization,setOrganization]=useState(organizations[0]?.id??'');
 const [project,setProject]=useState(''),[readOnly,setReadOnly]=useState(false),[days,setDays]=useState('90');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[created,setCreated]=useState<{token:string;name:string}>(),[confirm,setConfirm]=useState<string>(),[copied,setCopied]=useState('');
 const projects=useData<{data:Project[]}|undefined>(signal=>scopeKind==='project'&&organization?request(`/organizations/${organization}/projects`,'GET',undefined,signal):Promise.resolve(undefined),[request,scopeKind,organization]);
 const origin=location.origin;
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the text manually.');}}
 async function create(event:FormEvent){
  event.preventDefault();setBusy(true);setError('');setCopied('');
  const scope=scopeKind==='all'?{kind:'all'}:scopeKind==='organization'?{kind:'organization',id:organization}:{kind:'project',id:project};
  try{const result=await request('/access-tokens','POST',{name:name.trim(),scope,read_only:readOnly,days:Number(days)});setCreated({token:result.token,name:name.trim()});setName('');tokens.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function revoke(id:string){setBusy(true);setError('');try{await request('/access-tokens/'+id,'DELETE');setConfirm(undefined);tokens.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const scopeLabel=(scope:Scope)=>scope.kind==='all'?'Everything you can reach':scope.kind==='organization'?`Organization ${organizations.find(item=>item.id===scope.id)?.name??scope.id.slice(0,8)}`:`One project (${scope.id.slice(0,8)})`;
 const claude=created?`claude mcp add --transport http sbarbase ${origin}/mcp --header "Authorization: Bearer ${created.token}"`:'';
 const live=(tokens.data?.data??[]).filter(token=>!token.revoked_at&&token.expires_at>Date.now());
 const ended=(tokens.data?.data??[]).filter(token=>token.revoked_at||token.expires_at<=Date.now());
 return <><div className="page-heading"><div><h1>Access tokens</h1><p className="muted">Let an AI agent (Claude Code, Cursor, Codex) or a script create projects, run SQL and migrations, and deploy Edge Functions on this server, as you.</p></div></div>
  <ErrorMessage message={error||tokens.error}/>
  {created&&<section className="details new-key"><h2><Bot aria-hidden="true"/> Connect your agent</h2>
   <label htmlFor="new-token">Token "{created.name}". Save it now: it is only shown once.</label><textarea id="new-token" readOnly value={created.token}/>
   <div className="form-row"><button onClick={()=>void copy(created.token)}><Copy aria-hidden="true"/>Copy token</button></div>
   <p className="small"><strong>Claude Code</strong>: run this once in a terminal.</p><textarea readOnly rows={3} value={claude}/>
   <div className="form-row"><button className="primary" onClick={()=>void copy(claude)}><Copy aria-hidden="true"/>Copy command</button></div>
   <p className="small"><strong>Cursor, Codex and others</strong>: add an MCP server with the address <code>{origin}/mcp</code> and the header <code>Authorization: Bearer &lt;token&gt;</code>. Then tell the agent, for example: "Deploy this app to Sbarbase".</p>
   <div className="form-row"><button onClick={()=>{setCreated(undefined);setCopied('');}}>I have saved the token</button></div></section>}
  <p className="small muted" role="status">{copied}</p>
  <section className="details"><h2>New token</h2>
   <p className="small callout"><ShieldAlert aria-hidden="true"/> A token can do what you can do in its scope: create projects, read and change data, deploy code. Give each agent its own token, prefer one project and read only when that is enough, and revoke a token you no longer use.</p>
   <form onSubmit={create}>
    <label htmlFor="token-name">Name</label><input id="token-name" value={name} maxLength={64} placeholder="Claude Code on my laptop" onChange={event=>setName(event.target.value)} required/>
    <label htmlFor="token-scope">Can reach</label>
    <select id="token-scope" value={scopeKind} onChange={event=>setScopeKind(event.target.value as typeof scopeKind)}>
     <option value="all">Everything you can reach</option><option value="organization">One organization</option><option value="project">One project</option></select>
    {scopeKind!=='all'&&<><label htmlFor="token-organization">Organization</label><select id="token-organization" value={organization} onChange={event=>{setOrganization(event.target.value);setProject('');}}>
     {organizations.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></>}
    {scopeKind==='project'&&<><label htmlFor="token-project">Project</label>{projects.loading?<Loading/>:<select id="token-project" value={project} onChange={event=>setProject(event.target.value)} required>
     <option value="">Choose a project</option>{(projects.data?.data??[]).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>}</>}
    <label htmlFor="token-days">Expires after</label><select id="token-days" value={days} onChange={event=>setDays(event.target.value)}>
     {['7','30','90','180','365'].map(value=><option key={value} value={value}>{value} days</option>)}</select>
    <label className="check"><input type="checkbox" checked={readOnly} onChange={event=>setReadOnly(event.target.checked)}/> Read only (SQL runs read only; nothing can be changed)</label>
    <div className="form-row"><button type="submit" className={created?undefined:'primary'} disabled={busy||!name.trim()||(scopeKind!=='all'&&!organization)||(scopeKind==='project'&&!project)}><Plus aria-hidden="true"/>Create token</button></div>
   </form></section>
  <section className="details"><h2>Your tokens</h2>{tokens.loading?<Loading/>:live.length?<div className="table-wrap"><table><thead><tr><th>Name</th><th>Can reach</th><th>Access</th><th>Expires</th><th>Last used</th><th>Action</th></tr></thead><tbody>
   {live.map(token=><tr key={token.id}><td>{token.name}</td><td>{scopeLabel(token.scope)}</td><td>{token.read_only?'Read only':'Read and write'}</td><td>{new Date(token.expires_at).toLocaleDateString()}</td>
    <td>{token.last_used_at?new Date(token.last_used_at).toLocaleString():'Never'}</td>
    <td>{confirm===token.id?<div className="confirm"><span>Revoke? Agents using it stop at once.</span><button className="danger" disabled={busy} onClick={()=>void revoke(token.id)}>Confirm revoke</button><button onClick={()=>setConfirm(undefined)}>Cancel</button></div>
     :<button disabled={busy} onClick={()=>setConfirm(token.id)}>Revoke</button>}</td></tr>)}</tbody></table></div>:!tokens.error&&<Empty>No active tokens.</Empty>}
   {ended.length>0&&<p className="small muted">{ended.length} revoked or expired in the last 30 days.</p>}
   <p className="small muted">Tokens stop working when they expire, when you revoke them, and when your password is reset on the server. Changes made with a token appear in the activity log under your name.</p></section>
 </>;
}
