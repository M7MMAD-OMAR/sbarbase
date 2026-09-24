import {useEffect,useMemo,useState} from 'react';
import type {Session} from '@supabase/supabase-js';
import {Folder,LogOut,Plus} from 'lucide-react';
import {api,auth,useData,type Api,type Organization,type Project,type Environment} from './api';
import {Brand} from './Brand';
import {Login} from './Login';import {Projects} from './Projects';import {Environments} from './Environments';import {Connection} from './Connection';
import {notificationText} from './mail';
import {Empty,ErrorMessage,Loading,NameForm,Refresh,ThemeControl} from './components';
/** The operator's last-resort view of notifications: when every channel is down this count
 * is what still moves. Read only, refreshed once a minute, and absent when unreadable. */
function NotificationCount({request}:{request:Api}){
 const result=useData<{data:{undelivered:number}}>(signal=>request('/notifications','GET',undefined,signal),[request]);
 useEffect(()=>{const timer=setInterval(()=>result.refresh(),60000);return()=>clearInterval(timer);},[]);
 const count=result.data?.data.undelivered;
 return count===undefined?null:<p className="small muted" role="status">{notificationText(count)}</p>;
}
function Console({session}:{session:Session}){
 const request=useMemo(()=>api(session.access_token),[session.access_token]);
 const organizations=useData<{data:Organization[];operator?:boolean}>(signal=>request('/organizations','GET',undefined,signal),[request]);
 const [creating,setCreating]=useState(false);
 const [selected,setSelected]=useState(''),[project,setProject]=useState<Project>(),[environment,setEnvironment]=useState<Environment>();
 const organization=organizations.data?.data.find(item=>item.id===selected)??organizations.data?.data[0];
 const home=()=>{setProject(undefined);setEnvironment(undefined);};
 return <div className="app-shell"><a className="skip" href="#content">Skip to content</a><aside><Brand/><label htmlFor="organization">Organization</label><select id="organization" value={organization?.id??''} disabled={!organizations.data?.data.length} onChange={event=>{setSelected(event.target.value);home();}}>{organizations.data?.data.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>{organizations.data?.operator&&(creating?<NameForm label="Organization name" onCancel={()=>setCreating(false)} onSubmit={async name=>{const created=await request('/organizations','POST',{name}) as {id:string};setCreating(false);setSelected(created.id);home();organizations.refresh();}}/>:<button className="new-organization" onClick={()=>setCreating(true)}><Plus aria-hidden="true"/>New organization</button>)}<nav aria-label="Main"><button className="selected" onClick={home}><Folder aria-hidden="true"/>Projects</button></nav><NotificationCount request={request}/><footer><ThemeControl/><p className="small muted">Local installation</p><button onClick={()=>void auth.auth.signOut({scope:'local'})}><LogOut aria-hidden="true"/>Sign out</button></footer></aside>
 <div className="workspace"><header><span>{organization?.name??'Sbarbase'}</span><span aria-hidden="true">/</span><span>Projects</span>{project&&<><span aria-hidden="true">/</span><span>{project.name}</span></>}</header><main id="content"><ErrorMessage message={organizations.error}/>{organizations.error&&<Refresh onClick={organizations.refresh}/>}{organizations.loading?<Loading/>:!organization?<Empty>No organizations are assigned to your account. Contact your installation owner.</Empty>:environment?<Connection key={environment.id} environment={environment} organization={organization} request={request} onBack={()=>setEnvironment(undefined)}/>:project?<Environments key={project.id} project={project} organization={organization} request={request} onBack={home} onSelect={setEnvironment}/>:<Projects key={organization.id} organization={organization} request={request} onSelect={setProject}/>}</main></div></div>;
}
export function App(){const [session,setSession]=useState<Session|null>(null);useEffect(()=>{const {data}=auth.auth.onAuthStateChange((_event,session)=>setSession(session));return()=>data.subscription.unsubscribe();},[]);return session?<Console session={session}/>:<Login/>;}
