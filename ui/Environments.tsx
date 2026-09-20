import {useEffect,useState} from 'react';
import {ArrowLeft,Plus,ChevronRight} from 'lucide-react';
import {useData,type Api,type Organization,type Project,type Environment} from './api';
import {Empty,ErrorMessage,Loading,NameForm,Refresh} from './components';
const labels:Record<string,string>={queued:'Queued',running:'Provisioning',succeeded:'Provisioned',failed:'Failed',cancelled:'Cancelled'};
export function Environments({project,organization,request,onBack,onSelect}:{project:Project;organization:Organization;request:Api;onBack:()=>void;onSelect:(environment:Environment)=>void}){
 const result=useData<Environment[]>(async signal=>{const list=await request(`/projects/${project.id}/environments`,'GET',undefined,signal) as {data:Environment[]};return Promise.all(list.data.map(async environment=>({...environment,...await request(`/environments/${environment.id}/provision`,'GET',undefined,signal)})));},[project.id,request]);
 const [creating,setCreating]=useState(false);
 const pending=result.data?.some(item=>['queued','running'].includes(item.state??''));
 useEffect(()=>{if(!pending)return;const timer=setTimeout(result.refresh,3000);return()=>clearTimeout(timer);},[pending,result.data]);
 return <><button className="back" onClick={onBack}><ArrowLeft aria-hidden="true"/>Projects</button><div className="page-heading"><div><h1>{project.name}</h1><p className="muted">Environments</p></div>{organization.role!=='viewer'&&<button className="primary" disabled={creating} onClick={()=>setCreating(true)}><Plus aria-hidden="true"/>New environment</button>}</div>
 {creating&&<NameForm label="Environment name" onCancel={()=>setCreating(false)} onSubmit={async name=>{await request(`/projects/${project.id}/environments`,'POST',{name});setCreating(false);result.refresh();}}/>}
 <div className="toolbar"><span className="muted small">Provisioning status</span><Refresh onClick={result.refresh}/></div><ErrorMessage message={result.error}/>{result.loading?<Loading/>:result.data?.length?<div className="table-wrap"><table><thead><tr><th>Environment</th><th>Status</th><th>Connection</th></tr></thead><tbody>{result.data.map(environment=><tr key={environment.id}><td>{environment.name}</td><td><span className={'state '+environment.state}>{environment.state==='failed'&&environment.failure==='capacity_exceeded'?'Capacity limit':labels[environment.state??'']??'Unknown'}</span></td><td>{environment.state==='succeeded'?<button onClick={()=>onSelect(environment)}>Connect<ChevronRight aria-hidden="true"/></button>:<span className="muted small">Not available yet</span>}</td></tr>)}</tbody></table></div>:!result.error&&<Empty>No environments yet. Create one to connect your application.</Empty>}
 {pending&&<p className="notice" role="status">Your environment is waiting for the installation worker. This page updates automatically.</p>}
 {result.data?.some(item=>item.state==='failed'&&item.failure==='capacity_exceeded')&&<p className="notice">This installation has reached its configured environment limit. Ask the installation owner to review capacity before retrying.</p>}
 {result.data?.some(item=>item.state==='failed'&&item.failure!=='capacity_exceeded')&&<p className="notice">Provisioning failed. Ask your installation owner to inspect and retry the operation.</p>}</>;
}
