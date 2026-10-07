import {useEffect,useMemo,useRef,useState} from 'react';
import type {Session} from '@supabase/supabase-js';
import {Activity as ActivityIcon,CircleArrowUp,Folder,LogOut,Plus,Settings,Users,Server} from 'lucide-react';
import {api,auth,updatesApi,useData,type Api,type Organization,type Project,type Environment} from './api';
import {Brand} from './Brand';
import {Attribution} from './Attribution';
import {Login} from './Login';import {Projects} from './Projects';import {Environments} from './Environments';import {Connection} from './Connection';import {Activity} from './Activity';import {AcceptInvitation,JoinInvitation,inviteToken} from './Invite';
import {MfaGate,MfaSettings} from './Mfa';
import {sessionHasMfa} from './mfa-state';
import {notificationText} from './mail';
import {Team} from './Team';
import {ServerHealth} from './ServerHealth';
import {DeploymentSetup,deploymentSetupClient} from './DeploymentSetup';
import {OrganizationSettings} from './OrganizationSettings';
import {UpdateBanners,Updates,UpdatesProvider} from './Updates';
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
 const [selected,setSelected]=useState(''),[project,setProject]=useState<Project>(),[environment,setEnvironment]=useState<Environment>(),[requestedView,setView]=useState<'projects'|'team'|'settings'|'activity'|'updates'|'server'|'security'>('projects');
 const [projectOrganization,setProjectOrganization]=useState(''),[setupOpen,setSetupOpen]=useState(false);
 // The update routes read the token on every call, so a refresh during a long restart
 // neither restarts the watch nor sends a stale token.
 const token=useRef(session.access_token);token.current=session.access_token;
 const updatesClient=useMemo(()=>updatesApi(()=>token.current),[]);
 const [operator,setOperator]=useState(false);
 useEffect(()=>{if(organizations.data)setOperator(Boolean(organizations.data.operator));},[organizations.data]);
 const organization=organizations.data?.data.find(item=>item.id===selected)??organizations.data?.data[0];
 const home=()=>{setSetupOpen(false);setProject(undefined);setProjectOrganization('');setEnvironment(undefined);setView('projects');};
 const canAudit=organization?.role==='owner'||organization?.role==='admin';
 const owner=organization?.role==='owner';
 const unavailableView=((requestedView==='team'||requestedView==='activity')&&!canAudit)||(requestedView==='settings'&&!owner)||((requestedView==='updates'||requestedView==='server')&&!operator);
 const view=unavailableView?'projects':requestedView;
 const activeProject=projectOrganization===organization?.id?project:undefined;
 const activeEnvironment=activeProject?environment:undefined;
 const setupClient=useMemo(()=>activeEnvironment?deploymentSetupClient(activeEnvironment.id,()=>token.current):null,[activeEnvironment?.id]);
 useEffect(()=>setSetupOpen(false),[activeEnvironment?.id]);
 const openView=(next:typeof requestedView)=>{home();setView(next);};
 const viewLabels={projects:'Projects',team:'Team',settings:'Settings',activity:'Activity',updates:'Updates',server:'Server',security:'Account security'};
 // The update state lives in UpdatesProvider, so its polls render only the banner and the page.
 return <UpdatesProvider client={updatesClient} enabled={operator}><div className="app-shell"><a className="skip" href="#content">Skip to content</a><aside>
 <Brand/><label htmlFor="organization">Organization</label><select id="organization" value={organization?.id??''} disabled={!organizations.data?.data.length} onChange={event=>{setSelected(event.target.value);home();}}>{organizations.data?.data.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>
 {organizations.data?.operator&&(creating?<NameForm label="Organization name" onCancel={()=>setCreating(false)} onSubmit={async name=>{const created=await request('/organizations','POST',{name}) as {id:string};setCreating(false);setSelected(created.id);home();organizations.refresh();}}/>:<button className="new-organization" onClick={()=>setCreating(true)}><Plus aria-hidden="true"/>New organization</button>)}
 <nav aria-label="Main"><div className="nav-group"><p className="nav-group-label">Workspace</p>
 <button className={view==='projects'?'selected':''} aria-current={view==='projects'?'page':undefined} onClick={home}><Folder aria-hidden="true"/>Projects</button>
 {canAudit&&<button className={view==='team'?'selected':''} aria-current={view==='team'?'page':undefined} onClick={()=>openView('team')}><Users aria-hidden="true"/>Team</button>}
 {owner&&<button className={view==='settings'?'selected':''} aria-current={view==='settings'?'page':undefined} onClick={()=>openView('settings')}><Settings aria-hidden="true"/>Settings</button>}
 {canAudit&&<button className={view==='activity'?'selected':''} aria-current={view==='activity'?'page':undefined} onClick={()=>openView('activity')}><ActivityIcon aria-hidden="true"/>Activity</button>}</div>
 {operator&&<div className="nav-group"><p className="nav-group-label">Installation</p><button className={view==='server'?'selected':''} aria-current={view==='server'?'page':undefined} onClick={()=>openView('server')}><Server aria-hidden="true"/>Server</button>
 {operator&&<button className={view==='updates'?'selected':''} aria-current={view==='updates'?'page':undefined} onClick={()=>openView('updates')}><CircleArrowUp aria-hidden="true"/>Updates</button>}</div>}</nav>
 <NotificationCount request={request}/><footer><Attribution/><ThemeControl/><p className="small muted">Local installation</p><button onClick={()=>openView('security')}><Settings aria-hidden="true"/>Account security</button><button onClick={()=>void auth.auth.signOut({scope:'local'})}><LogOut aria-hidden="true"/>Sign out</button></footer></aside>
 <div className="workspace">{operator&&<UpdateBanners onPage={view==='updates'} onOpen={()=>openView('updates')}/>}
 <header><nav className="breadcrumb" aria-label="Breadcrumb"><span>{organization?.name??'Sbarbase'}</span><span aria-hidden="true">/</span>
 {view==='projects'&&activeProject?<><button onClick={home}>Projects</button><span aria-hidden="true">/</span>{activeEnvironment?<><button onClick={()=>setEnvironment(undefined)}>{activeProject.name}</button><span aria-hidden="true">/</span><span aria-current="page">{activeEnvironment.name}</span></>:<span aria-current="page">{activeProject.name}</span>}</>:<span aria-current="page">{viewLabels[view]}</span>}</nav></header>
 <main id="content"><JoinInvitation session={session} onJoined={()=>organizations.refresh()}/><ErrorMessage message={organizations.error}/>{organizations.error&&<Refresh onClick={organizations.refresh}/>}
 {view==='security'?<MfaSettings/>:view==='server'&&operator?<ServerHealth request={request}/>:view==='updates'&&operator?<Updates/>:organizations.loading?<Loading/>:!organization?<Empty>No organizations are assigned to your account. Contact your installation owner.</Empty>:view==='team'&&canAudit?<Team key={organization.id} organization={organization} request={request}/>:view==='settings'&&owner?<OrganizationSettings key={organization.id} organization={organization} request={request} onChanged={()=>{home();organizations.refresh();}}/>:view==='activity'&&canAudit?<Activity key={organization.id} organization={organization} request={request} self={session.user.id}/>:activeEnvironment?<>{operator&&<button type="button" onClick={()=>setSetupOpen(current=>!current)}>{setupOpen?'Back to connection':'Domain and email setup'}</button>}{setupOpen&&operator&&setupClient?<DeploymentSetup key={activeEnvironment.id} environmentName={activeEnvironment.name} client={setupClient}/>:<Connection key={activeEnvironment.id} environment={activeEnvironment} organization={organization} request={request} onBack={()=>setEnvironment(undefined)}/>}</>:activeProject?<Environments key={activeProject.id} project={activeProject} organization={organization} request={request} onBack={home} onSelect={setEnvironment}/>:<Projects key={organization.id} organization={organization} request={request} onSelect={next=>{setProject(next);setProjectOrganization(organization.id);setEnvironment(undefined);}}/>}</main></div></div></UpdatesProvider>;
}
export function App(){const [session,setSession]=useState<Session|null>(null),[signIn,setSignIn]=useState(false),[mfaVerified,setMfaVerified]=useState(false);useEffect(()=>{const {data}=auth.auth.onAuthStateChange((event,session)=>{
 setSession(session);if(!session||!sessionHasMfa(session.access_token)||event==='SIGNED_IN')setMfaVerified(false);
 if(event==='MFA_CHALLENGE_VERIFIED'&&session&&sessionHasMfa(session.access_token))setMfaVerified(true);
 });return()=>data.subscription.unsubscribe();},[]);
 const token=inviteToken();
 return session?mfaVerified?<Console session={session}/>:<MfaGate onVerified={()=>setMfaVerified(true)}/>:token&&!signIn?<AcceptInvitation token={token} onSignIn={()=>setSignIn(true)}/>:<Login/>;}
