import {useState,type FormEvent} from 'react';
import {Folder,ChevronRight,Plus,Search,Users,UserPlus,Copy,Pencil,Trash2} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {Empty,ErrorMessage,Loading,NameForm,Refresh} from './components';
/** Who can act in this organization. Owners change a role or remove a member; access ends at once.
 * Adding someone new waits for invitations. */
function Members({organization,request}:{organization:Organization;request:Api}){
 const result=useData<{data:{actor:string;role:string}[]}>(signal=>request(`/organizations/${organization.id}/members`,'GET',undefined,signal),[organization.id,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[confirm,setConfirm]=useState<string>();
 const owner=organization.role==='owner';
 async function change(actor:string,role:string|null){setBusy(true);setError('');
  try{await request(`/organizations/${organization.id}/members/${encodeURIComponent(actor)}`,role?'PUT':'DELETE',role?{role}:undefined);setConfirm(undefined);result.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="members" aria-labelledby="members-heading"><h2 id="members-heading"><Users aria-hidden="true"/>Members</h2><ErrorMessage message={error||result.error}/>{result.loading?<Loading/>:result.data&&<div className="table-wrap"><table><thead><tr><th>Operator</th><th>Role</th>{owner&&<th>Action</th>}</tr></thead><tbody>{result.data.data.map(member=><tr key={member.actor}><td><code title={member.actor}>{member.actor.slice(0,8)}</code></td>
 <td>{owner?<select aria-label={'Role of '+member.actor.slice(0,8)} value={member.role} disabled={busy} onChange={event=>void change(member.actor,event.target.value)}><option value="owner">owner</option><option value="admin">admin</option><option value="viewer">viewer</option></select>:member.role}</td>
 {owner&&<td>{confirm===member.actor?<div className="confirm"><span>Remove this member?</span><button className="danger" disabled={busy} onClick={()=>void change(member.actor,null)}>Confirm remove</button><button onClick={()=>setConfirm(undefined)}>Cancel</button></div>:<button disabled={busy} onClick={()=>setConfirm(member.actor)}>Remove</button>}</td>}</tr>)}</tbody></table></div>}</section>;
}
type Invitation={id:string;email:string;role:string;inviter:string;expires_at:number};
/** Invite someone by email. The link is shown once; the inviter sends it. */
function Invitations({organization,request}:{organization:Organization;request:Api}){
 const pending=useData<{data:Invitation[]}>(signal=>request(`/organizations/${organization.id}/invitations`,'GET',undefined,signal),[organization.id,request]);
 const [email,setEmail]=useState(''),[role,setRole]=useState('viewer'),[link,setLink]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState('');
 async function invite(event:FormEvent){event.preventDefault();setBusy(true);setError('');setCopied('');
  try{const {data}=await request(`/organizations/${organization.id}/invitations`,'POST',{email,role}) as {data:{token:string}};
   setLink(`${location.origin}/#invite=${data.token}`);setEmail('');pending.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function cancel(id:string){setBusy(true);setError('');try{await request(`/organizations/${organization.id}/invitations/${id}`,'DELETE');pending.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function copy(){try{await navigator.clipboard.writeText(link);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the link manually.');}}
 return <section className="details"><h2>Invite someone</h2><p className="muted small">They get an account in this console and join {organization.name} with the role you choose. The link works once, for 7 days.</p>
 <form className="form-row" onSubmit={invite}><label className="sr-only" htmlFor="invite-email">Email</label><input id="invite-email" type="email" placeholder="name@example.com" required value={email} onChange={e=>setEmail(e.target.value)}/>
 <label className="sr-only" htmlFor="invite-role">Role</label><select id="invite-role" value={role} onChange={e=>setRole(e.target.value)}>{organization.role==='owner'&&<option value="owner">owner</option>}<option value="admin">admin</option><option value="viewer">viewer</option></select>
 <button className="primary" disabled={busy}><UserPlus aria-hidden="true"/>Create invitation</button></form><ErrorMessage message={error||pending.error}/>
 {link&&<div className="new-key"><label htmlFor="invite-link">Send this link to the person you invited. It is only shown once.</label><textarea id="invite-link" readOnly value={link}/><div className="form-row"><button onClick={()=>void copy()}><Copy aria-hidden="true"/>Copy link</button><button onClick={()=>{setLink('');setCopied('');}}>Done</button></div><p className="small muted" role="status">{copied}</p></div>}
 {!!pending.data?.data.length&&<div className="table-wrap"><table><thead><tr><th>Pending</th><th>Role</th><th>Expires</th><th>Action</th></tr></thead><tbody>{pending.data.data.map(item=><tr key={item.id}><td>{item.email}</td><td>{item.role}</td><td>{new Date(item.expires_at).toLocaleDateString()}</td><td><button disabled={busy} onClick={()=>void cancel(item.id)}>Cancel</button></td></tr>)}</tbody></table></div>}</section>;
}
/** Owners rename the organization, or delete it once it holds no project. */
function OrganizationSettings({organization,request,empty,onChanged}:{organization:Organization;request:Api;empty:boolean;onChanged:()=>void}){
 const [renaming,setRenaming]=useState(false),[confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function remove(){setBusy(true);setError('');try{await request(`/organizations/${organization.id}`,'DELETE');onChanged();}catch(e){setError((e as Error).message);}finally{setBusy(false);setConfirm(false);}}
 return <section className="details" aria-labelledby="organization-settings"><h2 id="organization-settings">Organization settings</h2>
 {renaming?<NameForm label="New organization name" action="Rename" onCancel={()=>setRenaming(false)} onSubmit={async name=>{await request(`/organizations/${organization.id}`,'PATCH',{name});setRenaming(false);onChanged();}}/>:<button disabled={busy} onClick={()=>setRenaming(true)}><Pencil aria-hidden="true"/>Rename organization</button>}
 {confirm?<div className="confirm"><span>Delete {organization.name}? Its members lose access.</span><button className="danger" disabled={busy} onClick={()=>void remove()}>Confirm delete</button><button onClick={()=>setConfirm(false)}>Cancel</button></div>:<button disabled={busy||!empty} title={empty?undefined:'Delete or move its projects first'} onClick={()=>setConfirm(true)}><Trash2 aria-hidden="true"/>Delete organization</button>}
 <ErrorMessage message={error}/></section>;
}
export function Projects({organization,request,onSelect,onOrganizationChanged}:{organization:Organization;request:Api;onSelect:(project:Project)=>void;onOrganizationChanged?:()=>void}){
 const result=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const [query,setQuery]=useState(''),[creating,setCreating]=useState(false);
 const projects=result.data?.data.filter(item=>item.name.toLowerCase().includes(query.toLowerCase()))??[];
 return <><div className="page-heading"><div><h1>Projects</h1><p className="muted">Your projects, organized in one place.</p></div>{organization.role!=='viewer'&&<button className="primary" onClick={()=>setCreating(true)} disabled={creating}><Plus aria-hidden="true"/>New project</button>}</div>
 {creating&&<NameForm label="Project name" onCancel={()=>setCreating(false)} onSubmit={async name=>{await request(`/organizations/${organization.id}/projects`,'POST',{name});setCreating(false);result.refresh();}}/>}
 <div className="search"><Search aria-hidden="true"/><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={e=>setQuery(e.target.value)}/></div>
 <ErrorMessage message={result.error}/>{result.error&&<Refresh onClick={result.refresh}/>}{result.loading?<Loading/>:projects.length?<><div className="table-wrap"><table><thead><tr><th>Project</th><th>Project ID</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{projects.map(project=><tr key={project.id}><td><button className="project-link" onClick={()=>onSelect(project)}><Folder aria-hidden="true"/>{project.name}</button></td><td><code title={project.id}>{project.id.slice(0,8)}</code></td><td><ChevronRight aria-hidden="true"/></td></tr>)}</tbody></table></div><p className="small muted">{projects.length} {projects.length===1?'project':'projects'}</p></>:!result.error&&<Empty>{query?'No matching projects.':'No projects yet. Create your first project to get started.'}</Empty>}
 {organization.role!=='viewer'&&<><Members organization={organization} request={request}/><Invitations organization={organization} request={request}/></>}
 {organization.role==='owner'&&onOrganizationChanged&&result.data&&<OrganizationSettings organization={organization} request={request} empty={!result.data.data.length} onChanged={onOrganizationChanged}/>}</>;
}
