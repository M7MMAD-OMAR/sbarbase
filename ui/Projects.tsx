import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Folder,ChevronRight,Plus,Search,RotateCcw,RefreshCw} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {ErrorMessage} from './components';

type ArchivedProject=Project&{archived_at:number;retained_environments:number};
const date=(value:number)=>new Date(value).toLocaleDateString(undefined,{dateStyle:'medium'});
export function Projects({organization,request,onSelect}:{organization:Organization;request:Api;onSelect:(project:Project,environment?:string,tab?:Window|null)=>void}){
 const result=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const archived=useData<{data:ArchivedProject[]}>(signal=>organization.role==='viewer'?Promise.resolve({data:[]}):request(`/organizations/${organization.id}/archived-projects`,'GET',undefined,signal),[organization.id,organization.role,request]);
 const [query,setQuery]=useState(''),[creating,setCreating]=useState(false),[name,setName]=useState(''),[busy,setBusy]=useState(false),[restoring,setRestoring]=useState<string>(),[restored,setRestored]=useState<string>(),[error,setError]=useState(''),[message,setMessage]=useState('');
 const saving=useRef(false),restoredCard=useRef<HTMLButtonElement>(null),nameInput=useRef<HTMLInputElement>(null);
 useEffect(()=>{if(creating)nameInput.current?.focus();},[creating]);
 useEffect(()=>{if(restored)restoredCard.current?.focus();},[restored,result.data]);
 const matches=(item:Project)=>item.name.toLowerCase().includes(query.trim().toLowerCase());
 const projects=result.data?.data.filter(matches)??[],retained=archived.data?.data.filter(matches)??[];
 async function create(event:FormEvent){
  event.preventDefault();if(saving.current||!name.trim())return;saving.current=true;setBusy(true);setError('');
  const title=name.trim(),tab=window.open('about:blank','_blank');
  if(tab){tab.opener=null;tab.document.title='Preparing your project';tab.document.body.textContent='Preparing production. Your Supabase dashboard will open here when ready. Follow progress in the console.';}
  try{const created=await request(`/organizations/${organization.id}/projects`,'POST',{name:title}) as {id:string;environment:string};setCreating(false);setName('');onSelect({id:created.id,name:title},created.environment,tab);}
  catch(error){tab?.close();const refused=error as {status?:number;serverMessage?:string};setError(refused.status===409&&refused.serverMessage==='Name already used'?'This name is already used in this organization. If it belongs to an archived project, restore it from the archive.':'Could not create the project. Check the name and your connection, then try again.');}
  finally{saving.current=false;setBusy(false);}
 }
 async function restore(project:ArchivedProject){
  if(saving.current)return;saving.current=true;setRestoring(project.id);setError('');setMessage('');
  try{await request(`/projects/${project.id}/restore`,'POST');setRestored(project.id);setMessage('The project is back in the list. Its deleted environments stay under retention.');result.refresh();archived.refresh();}
  catch{setError('Could not restore the project. Check your connection and permissions, then try again.');}
  finally{saving.current=false;setRestoring(undefined);}
 }
 return <div>
  <div className="page-heading"><div><h1>Projects</h1><p className="muted">Choose a project to manage its environments and open Supabase Studio.</p></div>{organization.role!=='viewer'&&<button className="primary" onClick={()=>{setCreating(true);setError('');}} disabled={creating||!!restoring}><Plus aria-hidden="true"/>New project</button>}</div>
  {creating&&<form className="create-form" onSubmit={create} aria-busy={busy} aria-describedby="project-action-error"><p className="notice">Production is created automatically. Your Supabase dashboard opens in a new tab when ready.</p><label htmlFor="new-project-name">Project name</label><div className="form-row"><input id="new-project-name" ref={nameInput} value={name} maxLength={100} required disabled={busy} onChange={event=>setName(event.target.value)}/><button type="submit" className="primary" disabled={busy||!name.trim()}>{busy?'Creating...':'Create project'}</button><button type="button" disabled={busy} onClick={()=>setCreating(false)}>Cancel</button></div></form>}
  <div className="search"><Search aria-hidden="true"/><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={event=>setQuery(event.target.value)}/></div>
  <div id="project-action-error"><ErrorMessage message={error}/></div><ErrorMessage message={result.error?'Could not load projects. Refresh to try again.':''}/>
  {result.error&&<button onClick={result.refresh}><RefreshCw aria-hidden="true"/>Refresh projects</button>}
  <p className="small muted" role="status">{message}</p>
  {result.loading?<p role="status">Loading projects...</p>:projects.length?<><ul className="project-grid" aria-label="Active projects">{projects.map(project=><li key={project.id}><button ref={project.id===restored?restoredCard:undefined} className="project-card" aria-label={'Open project '+project.name} onClick={()=>onSelect(project)}><span className="project-card-icon"><Folder aria-hidden="true"/></span><span className="project-card-title"><bdi>{project.name}</bdi></span><span className="project-card-id">Project ID <code dir="ltr" title={project.id}>{project.id.slice(0,8)}</code></span><span className="project-card-footer">Open project<ChevronRight aria-hidden="true"/></span></button></li>)}</ul><p className="page-summary small muted">{projects.length} {projects.length===1?'project':'projects'}</p></>:!result.error&&<div className="empty">{query?'No matching projects. Try another search.':organization.role==='viewer'?'No projects yet. Ask an owner or admin to create one.':'No projects yet. Choose New project to get started.'}</div>}
  {organization.role!=='viewer'&&<section className="details" aria-labelledby="archived-projects-heading" aria-busy={archived.loading||!!restoring}><h2 id="archived-projects-heading">Archived projects</h2><p className="small muted">These projects keep records of their deleted environments. Restoring a project returns it to the list; it does not start its environments.</p>
   <ErrorMessage message={archived.error?'Could not load the archive. Refresh to try again.':''}/>
   {archived.error&&<button onClick={archived.refresh}><RefreshCw aria-hidden="true"/>Refresh archive</button>}
   {archived.loading?<p role="status">Loading archive...</p>:retained.length?<ul className="project-grid" aria-label="Archived projects">{retained.map(project=><li key={project.id} className="project-card"><span className="project-card-title"><bdi>{project.name}</bdi></span><p className="small muted">Archived {date(project.archived_at)}<br/>Retained environment records: {project.retained_environments}</p>{organization.role==='owner'?<button disabled={!!restoring||busy} aria-label={'Restore project '+project.name} onClick={()=>void restore(project)}><RotateCcw aria-hidden="true"/>{restoring===project.id?'Restoring...':'Restore project'}</button>:<p className="small muted">Only the organization owner can restore projects.</p>}</li>)}</ul>:!archived.error&&<p className="small muted">{query?'No archived projects match your search.':'No archived projects.'}</p>}
  </section>}
 </div>;
}
