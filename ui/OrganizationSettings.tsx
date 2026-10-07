import {useState} from 'react';
import {Pencil,Trash2} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {ErrorMessage,NameForm,Refresh} from './components';
/** Owners rename the organization, or delete it once it holds no project. */
export function OrganizationSettings({organization,request,onChanged}:{organization:Organization;request:Api;onChanged:()=>void}){
 const projects=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const empty=Boolean(projects.data&&!projects.error&&!projects.loading&&!projects.data.data.length);
 const [renaming,setRenaming]=useState(false),[confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function remove(){setBusy(true);setError('');try{await request(`/organizations/${organization.id}`,'DELETE');onChanged();}catch(e){setError((e as Error).message);}finally{setBusy(false);setConfirm(false);}}
 return <><div className="page-heading"><div><h1>Organization settings</h1><p className="muted">Manage {organization.name}. These settings affect every project in this organization.</p></div></div><section className="details" aria-labelledby="organization-settings"><h2 id="organization-settings">Organization</h2>
 {renaming?<NameForm label="New organization name" action="Rename" onCancel={()=>setRenaming(false)} onSubmit={async name=>{await request(`/organizations/${organization.id}`,'PATCH',{name});setRenaming(false);onChanged();}}/>:<button disabled={busy} onClick={()=>setRenaming(true)}><Pencil aria-hidden="true"/>Rename organization</button>}
 {confirm?<div className="confirm"><span>Delete {organization.name}? Its members lose access.</span><button className="danger" disabled={busy} onClick={()=>void remove()}>Confirm delete</button><button onClick={()=>setConfirm(false)}>Cancel</button></div>:<button disabled={busy||!empty} title={empty?undefined:'Delete or move its projects first'} onClick={()=>setConfirm(true)}><Trash2 aria-hidden="true"/>Delete organization</button>}
 <p className="small muted">Delete or move all projects before deleting this organization.</p><ErrorMessage message={error||projects.error}/>{projects.error&&<Refresh onClick={projects.refresh}/>}</section></>;
}
