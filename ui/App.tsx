import {useEffect,useMemo,useState} from 'react';
import type {Session} from '@supabase/supabase-js';
import {Folder,LogOut} from 'lucide-react';
import {api,auth,useData,type Organization,type Project,type Environment} from './api';
import {Brand} from './Brand';
import {Login} from './Login';import {Projects} from './Projects';import {Environments} from './Environments';import {Connection} from './Connection';
import {Empty,ErrorMessage,Loading,Refresh} from './components';
function Console({session}:{session:Session}){
 const request=useMemo(()=>api(session.access_token),[session.access_token]);
 const organizations=useData<{data:Organization[]}>(signal=>request('/organizations','GET',undefined,signal),[request]);
 const [selected,setSelected]=useState(''),[project,setProject]=useState<Project>(),[environment,setEnvironment]=useState<Environment>();
 const organization=organizations.data?.data.find(item=>item.id===selected)??organizations.data?.data[0];
 const home=()=>{setProject(undefined);setEnvironment(undefined);};
 return <div className="app-shell"><a className="skip" href="#content">Skip to content</a><aside><Brand/><label htmlFor="organization">Organization</label><select id="organization" value={organization?.id??''} disabled={!organizations.data?.data.length} onChange={event=>{setSelected(event.target.value);home();}}>{organizations.data?.data.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select><nav aria-label="Main"><button className="selected" onClick={home}><Folder aria-hidden="true"/>Projects</button></nav><footer><p className="small muted">Local installation</p><button onClick={()=>void auth.auth.signOut({scope:'local'})}><LogOut aria-hidden="true"/>Sign out</button></footer></aside>
 <div className="workspace"><header><span>{organization?.name??'Sbarbase'}</span><span aria-hidden="true">/</span><span>Projects</span>{project&&<><span aria-hidden="true">/</span><span>{project.name}</span></>}</header><main id="content"><ErrorMessage message={organizations.error}/>{organizations.error&&<Refresh onClick={organizations.refresh}/>}{organizations.loading?<Loading/>:!organization?<Empty>No organizations are assigned to your account. Contact your installation owner.</Empty>:environment?<Connection key={environment.id} environment={environment} organization={organization} request={request} onBack={()=>setEnvironment(undefined)}/>:project?<Environments key={project.id} project={project} organization={organization} request={request} onBack={home} onSelect={setEnvironment}/>:<Projects key={organization.id} organization={organization} request={request} onSelect={setProject}/>}</main></div></div>;
}
export function App(){const [session,setSession]=useState<Session|null>(null);useEffect(()=>{const {data}=auth.auth.onAuthStateChange((_event,session)=>setSession(session));return()=>data.subscription.unsubscribe();},[]);return session?<Console session={session}/>:<Login/>;}
