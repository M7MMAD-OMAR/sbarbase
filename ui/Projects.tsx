import {useState} from 'react';
import {Folder,ChevronRight,Plus,Search} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {Empty,ErrorMessage,Loading,NameForm,Refresh} from './components';
export function Projects({organization,request,onSelect}:{organization:Organization;request:Api;onSelect:(project:Project)=>void}){
 const result=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const [query,setQuery]=useState(''),[creating,setCreating]=useState(false);
 const projects=result.data?.data.filter(item=>item.name.toLowerCase().includes(query.toLowerCase()))??[];
 return <><div className="page-heading"><div><h1>Projects</h1><p className="muted">Your projects, organized in one place.</p></div>{organization.role!=='viewer'&&<button className="primary" onClick={()=>setCreating(true)} disabled={creating}><Plus aria-hidden="true"/>New project</button>}</div>
 {creating&&<NameForm label="Project name" onCancel={()=>setCreating(false)} onSubmit={async name=>{await request(`/organizations/${organization.id}/projects`,'POST',{name});setCreating(false);result.refresh();}}/>}
 <div className="search"><Search aria-hidden="true"/><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={e=>setQuery(e.target.value)}/></div>
 <ErrorMessage message={result.error}/>{result.error&&<Refresh onClick={result.refresh}/>}{result.loading?<Loading/>:projects.length?<><div className="table-wrap"><table><thead><tr><th>Project</th><th>Project ID</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{projects.map(project=><tr key={project.id}><td><button className="project-link" onClick={()=>onSelect(project)}><Folder aria-hidden="true"/>{project.name}</button></td><td><code title={project.id}>{project.id.slice(0,8)}</code></td><td><ChevronRight aria-hidden="true"/></td></tr>)}</tbody></table></div><p className="small muted">{projects.length} {projects.length===1?'project':'projects'}</p></>:!result.error&&<Empty>{query?'No matching projects.':'No projects yet. Create your first project to get started.'}</Empty>}</>;
}
