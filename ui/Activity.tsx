import {useData,type Api,type Organization} from './api';
import {ErrorMessage,Loading,Empty,Refresh} from './components';
type Event={at:number;actor:string;action:string;kind:string;subject:string;detail:Record<string,string|number|boolean>};
/** Plain words for the recorded actions; an action without one shows its own name. */
const words:Record<string,string>={
 'organization.created':'Organization created','installation.initialized':'Installation set up','membership.changed':'Member role changed',
 'project.created':'Project created','project.ownership_changed':'Project moved here','environment.created':'Environment requested',
 'provision.started':'Provisioning started','provision.succeeded':'Environment ready','provision.failed':'Provisioning failed',
 'provision.cancelled':'Provisioning cancelled','provision.retried':'Provisioning retried','studio.requested':'Studio started',
 'studio.stop_requested':'Studio stopped','sign_in.saved':'Sign-in settings saved','gateway.share_changed':'Gateway share changed',
 'backup.completed':'Backup completed','backup.failed':'Backup failed'};
/** What happened in this organization, newest first. Owners and admins only; read only. */
export function Activity({organization,request,self}:{organization:Organization;request:Api;self:string}){
 const events=useData<{data:Event[]}>(signal=>request(`/organizations/${organization.id}/audit`,'GET',undefined,signal),[organization.id,request]);
 const who=(actor:string)=>actor===self?'You':actor.startsWith('system')?'System':actor.slice(0,8);
 const detail=(event:Event)=>Object.entries(event.detail).map(([key,value])=>`${key.replace(/_/g,' ')}: ${value}`).join(', ');
 return <><div className="page-heading"><div><h1>Activity</h1><p className="muted">What happened in {organization.name}, newest first.</p></div></div>
 <ErrorMessage message={events.error}/>{events.error&&<Refresh onClick={events.refresh}/>}
 {events.loading?<Loading/>:events.data?.data.length?<div className="table-wrap"><table><thead><tr><th>When</th><th>What</th><th>Where</th><th>Who</th></tr></thead>
 <tbody>{events.data.data.map((event,k)=><tr key={k}><td>{new Date(event.at).toLocaleString()}</td><td>{words[event.action]??event.action}{detail(event)&&<div className="small muted">{detail(event)}</div>}</td>
 <td>{event.subject||organization.name}</td><td>{who(event.actor)}</td></tr>)}</tbody></table></div>:!events.error&&<Empty>Nothing recorded yet.</Empty>}</>;
}
