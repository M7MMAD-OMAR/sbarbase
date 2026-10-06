import {useState} from 'react';
import {Folder,ChevronRight,Plus,Search} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {Empty,ErrorMessage,Loading,NameForm,Refresh} from './components';
export function Projects({organization,request,onSelect}:{organization:Organization;request:Api;onSelect:(project:Project)=>void}){
 const result=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const [query,setQuery]=useState(''),[creating,setCreating]=useState(false);
 const projects=result.data?.data.filter(item=>item.name.toLowerCase().includes(query.trim().toLowerCase()))??[];
 return <><div className="page-heading"><div><h1>Projects</h1><p className="muted">Choose a project to manage its environments and open Supabase Studio.</p></div>{organization.role!=='viewer'&&<button className="primary" onClick={()=>setCreating(true)} disabled={creating}><Plus aria-hidden="true"/>New project</button>}</div>
 {creating&&<NameForm label="Project name" onCancel={()=>setCreating(false)} onSubmit={async name=>{await request(`/organizations/${organization.id}/projects`,'POST',{name});setCreating(false);result.refresh();}}/>}
 <div className="search"><Search aria-hidden="true"/><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={e=>setQuery(e.target.value)}/></div>
 <ErrorMessage message={result.error}/>{result.error&&<Refresh onClick={result.refresh}/>}{result.loading?<Loading/>:projects.length?<><ul className="project-grid" aria-label="Projects">{projects.map(project=><li key={project.id}><button className="project-card" aria-label={'Open project '+project.name} onClick={()=>onSelect(project)}><span className="project-card-icon"><Folder aria-hidden="true"/></span><span className="project-card-title">{project.name}</span><span className="project-card-id">Project ID <code title={project.id}>{project.id.slice(0,8)}</code></span><span className="project-card-footer">Open project<ChevronRight aria-hidden="true"/></span></button></li>)}</ul><p className="page-summary small muted">{projects.length} {projects.length===1?'project':'projects'}</p></>:!result.error&&<Empty>{query?'No matching projects. Try another search.':organization.role==='viewer'?'No projects yet. Ask an owner or admin to create one.':'No projects yet. Choose New project to get started.'}</Empty>}</>;
}
