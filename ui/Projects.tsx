import {useState} from 'react';
import {Folder,ChevronRight,Plus,Search,Users} from 'lucide-react';
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
export function Projects({organization,request,onSelect}:{organization:Organization;request:Api;onSelect:(project:Project)=>void}){
 const result=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const [query,setQuery]=useState(''),[creating,setCreating]=useState(false);
 const projects=result.data?.data.filter(item=>item.name.toLowerCase().includes(query.toLowerCase()))??[];
 return <><div className="page-heading"><div><h1>Projects</h1><p className="muted">Your projects, organized in one place.</p></div>{organization.role!=='viewer'&&<button className="primary" onClick={()=>setCreating(true)} disabled={creating}><Plus aria-hidden="true"/>New project</button>}</div>
 {creating&&<NameForm label="Project name" onCancel={()=>setCreating(false)} onSubmit={async name=>{await request(`/organizations/${organization.id}/projects`,'POST',{name});setCreating(false);result.refresh();}}/>}
 <div className="search"><Search aria-hidden="true"/><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={e=>setQuery(e.target.value)}/></div>
 <ErrorMessage message={result.error}/>{result.error&&<Refresh onClick={result.refresh}/>}{result.loading?<Loading/>:projects.length?<><div className="table-wrap"><table><thead><tr><th>Project</th><th>Project ID</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{projects.map(project=><tr key={project.id}><td><button className="project-link" onClick={()=>onSelect(project)}><Folder aria-hidden="true"/>{project.name}</button></td><td><code title={project.id}>{project.id.slice(0,8)}</code></td><td><ChevronRight aria-hidden="true"/></td></tr>)}</tbody></table></div><p className="small muted">{projects.length} {projects.length===1?'project':'projects'}</p></>:!result.error&&<Empty>{query?'No matching projects.':'No projects yet. Create your first project to get started.'}</Empty>}
 {organization.role!=='viewer'&&<Members organization={organization} request={request}/>}</>;
}
