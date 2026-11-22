import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Bot,Copy,Plus,ShieldAlert} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {ErrorMessage,Empty} from './components';
import {AGENT_PERMISSIONS,READ_PERMISSIONS,SAFE_ADMIN_PERMISSIONS,DANGEROUS_PERMISSIONS,type AgentPermission} from '../src/control/agent-permissions';

type Scope={kind:'all'}|{kind:'organization';id:string}|{kind:'project';id:string};
type Token={id:string;name:string;scope:Scope;permissions?:AgentPermission[];read_only:boolean;created_at:number;expires_at:number;last_used_at:number|null;revoked_at:number|null};
type Preset='read'|'admin'|'custom'|'all';
const labels:Record<AgentPermission,string>={
 'projects.read':'View projects and environments','projects.write':'Create and rename projects and environments',
 'organization.read':'View organizations','organization.write':'Rename the organization',
 'auth.read':'View Auth settings','auth.write':'Change sign-in security, redirect URLs and email','auth.delete':'Delete SSO providers',
 'database.read':'Read the database','database.configure':'Configure the Data API and scheduled jobs','database.write':'Run SQL and migrations, and enable direct connections',
 'functions.read':'View functions, their code and secret names','functions.write':'Deploy function code and manage secrets','functions.delete':'Delete functions',
 'storage.read':'View supported Storage information',
 'keys.read':'View publishable API keys and key metadata','keys.write':'Create API keys, including secret keys','keys.delete':'Revoke API keys',
 'logs.read':'View logs and metrics','realtime.read':'View Realtime settings','realtime.write':'Change Realtime settings',
};
const date=(value:number,time=false)=>new Date(value).toLocaleString(undefined,time?{dateStyle:'short',timeStyle:'short',hour12:true}:{dateStyle:'medium'});

/** One-time token display and owner-controlled capabilities share the backend's permission catalog. */
export function AccessTokens({request,organizations}:{request:Api;organizations:Organization[]}){
 const tokens=useData<{data:Token[]}>(signal=>request('/access-tokens','GET',undefined,signal),[request]);
 const [name,setName]=useState(''),[scopeKind,setScopeKind]=useState<Scope['kind']>('project'),[organization,setOrganization]=useState(organizations[0]?.id??'');
 const [project,setProject]=useState(''),[days,setDays]=useState('30'),[preset,setPreset]=useState<Preset>('read');
 const [permissions,setPermissions]=useState<AgentPermission[]>([...READ_PERMISSIONS]),[acknowledged,setAcknowledged]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[created,setCreated]=useState<{token:string;name:string;scope:Scope;permissions:AgentPermission[]}>(),[confirm,setConfirm]=useState<string>(),[copied,setCopied]=useState('');
 const [client,setClient]=useState<'json'|'codex'|'claude'>('json');
 const saving=useRef(false),createdHeading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{if(created)createdHeading.current?.focus();},[created]);
 const projects=useData<{data:Project[]}|undefined>(signal=>scopeKind==='project'&&organization?request(`/organizations/${organization}/projects`,'GET',undefined,signal):Promise.resolve(undefined),[request,scopeKind,organization]);
 const origin=location.origin;
 const dangerous=permissions.filter(permission=>DANGEROUS_PERMISSIONS.includes(permission));
 const missing=!name.trim()?'Enter a token name.':scopeKind!=='all'&&!organization?'Choose an organization.':scopeKind==='project'&&!project?'Choose a project.':!permissions.length?'Choose at least one permission.':dangerous.length&&!acknowledged?'Confirm that you understand the risk of the selected permissions.':'';
 function choosePreset(value:Preset){setPreset(value);setAcknowledged(false);if(value!=='custom')setPermissions([...(value==='read'?READ_PERMISSIONS:value==='admin'?SAFE_ADMIN_PERMISSIONS:AGENT_PERMISSIONS)]);}
 function toggle(permission:AgentPermission){setPreset('custom');setAcknowledged(false);setPermissions(current=>current.includes(permission)?current.filter(item=>item!==permission):[...current,permission]);}
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Could not copy. Select the text and copy it manually.');}}
 async function create(event:FormEvent){
  event.preventDefault();if(saving.current||missing)return;saving.current=true;setBusy(true);setError('');setCopied('');
  const scope:Scope=scopeKind==='all'?{kind:'all'}:{kind:scopeKind,id:scopeKind==='organization'?organization:project};
  try{const result=await request('/access-tokens','POST',{name:name.trim(),scope,permissions,read_only:permissions.every(permission=>permission.endsWith('.read')),days:Number(days)});setCreated({token:result.token,name:name.trim(),scope,permissions:[...permissions]});setName('');setAcknowledged(false);tokens.refresh();}
  catch(e){setError((e as Error).message);}finally{saving.current=false;setBusy(false);}
 }
 async function revoke(id:string){if(saving.current)return;saving.current=true;setBusy(true);setError('');try{await request('/access-tokens/'+id,'DELETE');setConfirm(undefined);tokens.refresh();}catch(e){setError((e as Error).message);}finally{saving.current=false;setBusy(false);}}
 const scopeLabel=(scope:Scope)=>scope.kind==='all'?'Everything your role allows':scope.kind==='organization'?`Organization ${organizations.find(item=>item.id===scope.id)?.name??scope.id.slice(0,8)}`:`Project ${projects.data?.data?.find(item=>item.id===scope.id)?.name??scope.id.slice(0,8)}`;
 const configuration=created?(client==='json'?JSON.stringify({mcpServers:{sbarbase:{url:origin+'/mcp',headers:{Authorization:'Bearer '+created.token}}}},null,2):client==='codex'?`# Set in the terminal that starts Codex:\nexport SBARBASE_ACCESS_TOKEN='${created.token}'\n\n# Add to ~/.codex/config.toml:\n[mcp_servers.sbarbase]\nurl = "${origin}/mcp"\nbearer_token_env_var = "SBARBASE_ACCESS_TOKEN"`:`claude mcp add --transport http sbarbase ${origin}/mcp --header "Authorization: Bearer ${created.token}"`):'';
 const live=(tokens.data?.data??[]).filter(token=>!token.revoked_at&&token.expires_at>Date.now());
 const ended=(tokens.data?.data??[]).filter(token=>token.revoked_at||token.expires_at<=Date.now());
 return <div className="agent-access">
  <div className="page-heading"><div><h1>Agent access</h1><p className="muted">One token per agent, limited to the projects it can reach and the operations it can run.</p></div></div>
  <div id="agent-token-errors"><ErrorMessage message={error||tokens.error||projects.error}/></div>
  {created&&<section className="details new-key" aria-labelledby="agent-created-heading"><h2 id="agent-created-heading" ref={createdHeading} tabIndex={-1}><Bot aria-hidden="true"/>Connect your agent</h2>
   <p>Token <bdi>{created.name}</bdi> was issued for {scopeLabel(created.scope)} with {created.permissions.length} {created.permissions.length===1?'permission':'permissions'}.</p>
   <label htmlFor="new-token">Token (shown only once)</label><textarea id="new-token" dir="ltr" readOnly value={created.token} spellCheck={false}/>
   <div className="form-row"><button onClick={()=>void copy(created.token)}><Copy aria-hidden="true"/>Copy token</button></div>
   <label htmlFor="agent-endpoint">MCP URL</label><input id="agent-endpoint" dir="ltr" readOnly value={origin+'/mcp'}/>
   <label htmlFor="agent-client">Client</label><select id="agent-client" value={client} onChange={event=>setClient(event.target.value as typeof client)}><option value="json">Cursor and other JSON clients</option><option value="codex">Codex</option><option value="claude">Claude Code</option></select>
   <label htmlFor="agent-configuration">Client configuration</label><textarea id="agent-configuration" dir="ltr" readOnly rows={client==='json'?11:client==='codex'?8:3} value={configuration} spellCheck={false}/>
   <p className="small muted">This configuration contains the token. Keep it on your machine, out of repositories and shared messages. The connection uses the token; there is no OAuth sign-in.</p>
   <div className="form-row"><button onClick={()=>void copy(configuration)}><Copy aria-hidden="true"/>Copy configuration</button><button onClick={()=>{setCreated(undefined);setCopied('');}}>I saved it, hide the token</button></div>
   <p className="small">Once connected, the agent can call <code dir="ltr">get_sbarbase_operations</code> to list the operations it may use, then <code dir="ltr">execute_sbarbase_operation</code> to preview and run them.</p>
  </section>}
  <p className="small muted" role="status">{copied}</p>
  <section className="details"><h2>New token</h2>
   <p className="small callout"><ShieldAlert aria-hidden="true"/>A token can never do more than your current role. Agent tokens cannot issue other tokens, change your account security, or permanently delete projects and environments.</p>
   <form onSubmit={create} aria-busy={busy} aria-describedby="agent-token-errors agent-submit-help">
    <fieldset disabled={busy} className="agent-token-fields">
     <label htmlFor="token-name">Token name</label><input id="token-name" value={name} maxLength={64} placeholder="Project agent on my laptop" onChange={event=>setName(event.target.value)} required autoComplete="off"/>
     <div className="agent-form-grid"><div><label htmlFor="token-scope">Access scope</label><select id="token-scope" value={scopeKind} onChange={event=>{setScopeKind(event.target.value as Scope['kind']);setAcknowledged(false);}}><option value="project">One project</option><option value="organization">One organization</option><option value="all">Everything your role allows</option></select></div>
      <div><label htmlFor="token-days">Expires after</label><select id="token-days" value={days} onChange={event=>setDays(event.target.value)}>{['7','30','90','180','365'].map(value=><option key={value} value={value}>{value} days</option>)}</select></div></div>
     {scopeKind!=='all'&&<><label htmlFor="token-organization">Organization</label><select id="token-organization" value={organization} onChange={event=>{setOrganization(event.target.value);setProject('');setAcknowledged(false);}} required><option value="">Choose an organization</option>{organizations.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></>}
     {scopeKind==='project'&&<><label htmlFor="token-project">Project</label>{projects.loading?<p role="status">Loading projects...</p>:<select id="token-project" value={project} onChange={event=>{setProject(event.target.value);setAcknowledged(false);}} required><option value="">Choose a project</option>{(projects.data?.data??[]).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>}</>}
     <p className="small muted">An organization scope covers its current and future projects. The full scope covers everything your role grants, now and later.</p>
     <label htmlFor="token-preset">Permission set</label><select id="token-preset" value={preset} onChange={event=>choosePreset(event.target.value as Preset)}><option value="read">Read only</option><option value="admin">Manage settings</option><option value="custom">Custom</option><option value="all">All agent permissions</option></select>
     <p className="small muted">Manage settings excludes running SQL, deploying code, issuing keys, and all delete and revoke permissions. Settings changes can still affect your application's security or availability.</p>
     <fieldset className="agent-permissions" aria-describedby="agent-permission-help"><legend>Selected permissions: {permissions.length}</legend><p id="agent-permission-help" className="small muted">Each permission is independent. Change the selection directly.</p>
      <div className="agent-permission-grid">{AGENT_PERMISSIONS.filter(permission=>!DANGEROUS_PERMISSIONS.includes(permission)).map(permission=><label className="check" key={permission}><input type="checkbox" checked={permissions.includes(permission)} onChange={()=>toggle(permission)}/><span>{labels[permission]}<code dir="ltr">{permission}</code></span></label>)}</div>
     </fieldset>
     <fieldset className="agent-permissions agent-sensitive" aria-describedby="agent-danger-help"><legend>Sensitive permissions</legend><p id="agent-danger-help" className="small">Running SQL, deploying code and issuing secret keys can let the agent change or delete data. Grant them only to an agent you trust.</p>
      <div className="agent-permission-grid">{DANGEROUS_PERMISSIONS.map(permission=><label className="check" key={permission}><input type="checkbox" checked={permissions.includes(permission)} onChange={()=>toggle(permission)}/><span>{labels[permission]}<code dir="ltr">{permission}</code></span></label>)}</div>
      {dangerous.length>0&&<label className="check agent-risk-ack"><input type="checkbox" checked={acknowledged} onChange={event=>setAcknowledged(event.target.checked)} aria-describedby="agent-danger-help" required/><span>I understand these permissions can allow destructive data changes, and I want to grant them to this token.</span></label>}
     </fieldset>
    </fieldset>
    <p id="agent-submit-help" className="small muted" role="status">{missing||'Ready to issue the token with the selected permissions.'}</p>
    <div className="form-row"><button type="submit" className="primary" disabled={busy||!!missing}><Plus aria-hidden="true"/>{busy?'Issuing token...':'Issue token'}</button></div>
   </form>
  </section>
  <section className="details"><h2>Active tokens</h2>{tokens.loading?<p role="status">Loading tokens...</p>:live.length?<div className="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Scope</th><th scope="col">Permissions</th><th scope="col">Expires</th><th scope="col">Last used</th><th scope="col">Action</th></tr></thead><tbody>
   {live.map(token=><tr key={token.id}><td><bdi>{token.name}</bdi></td><td>{scopeLabel(token.scope)}</td><td><details><summary>Permissions: {(token.permissions??READ_PERMISSIONS).length}</summary><ul className="agent-token-grants">{(token.permissions??READ_PERMISSIONS).map(permission=><li key={permission}>{labels[permission]??permission}</li>)}</ul></details></td><td>{date(token.expires_at)}</td><td>{token.last_used_at?date(token.last_used_at,true):'Never'}</td>
    <td>{confirm===token.id?<div className="confirm"><span>Revoking blocks new requests. An operation already in progress may still finish.</span><button className="danger" disabled={busy} onClick={()=>void revoke(token.id)}>Confirm revoke</button><button disabled={busy} onClick={()=>setConfirm(undefined)}>Cancel</button></div>:<button disabled={busy} onClick={()=>setConfirm(token.id)} aria-label={'Revoke token '+token.name}>Revoke</button>}</td></tr>)}</tbody></table></div>:!tokens.error&&<Empty>No active tokens.</Empty>}
   {ended.length>0&&<p className="small muted">Revoked or expired in the last 30 days: {ended.length}.</p>}
   <p className="small muted">Revoking a token, letting it expire, or resetting the account password on the server stops new requests. A token follows changes to your membership or role. To change a token's permissions, revoke it and issue a new one.</p>
  </section>
 </div>;
}
