import {useCallback,useEffect,useState} from 'react';
import {ArrowLeft,Download as DownloadIcon,Send} from 'lucide-react';
import {ApiError,auth,download,type Api} from './api';
import {Loading} from './components';
import {useLive} from './functions-workspace/live';
import type {RuntimeState} from './functions-workspace/types';
import {Problem,Tabs} from './functions-workspace/kit';
import {List} from './functions-workspace/List';
import {AddFunction} from './functions-workspace/AddFunction';
import {Overview} from './functions-workspace/Overview';
import {Logs} from './functions-workspace/Logs';
import {Code} from './functions-workspace/Code';
import {Settings} from './functions-workspace/Settings';
import {Secrets} from './functions-workspace/Secrets';
import {TestPanel} from './functions-workspace/TestPanel';
import './functions-workspace.css';

type Page='functions'|'secrets';
type Tab='overview'|'logs'|'code'|'settings';
const TABS:{id:Tab;label:string}[]=[{id:'overview',label:'Overview'},{id:'logs',label:'Logs'},{id:'code',label:'Code'},{id:'settings',label:'Settings'}];

/** Edge Functions of one environment, laid out like the Supabase cloud's: Functions and Secrets, and for each function Overview, Logs,
 * Code and Settings with Test and Download. Every request goes to this environment's own routes. */
export function FunctionsWorkspace({path,request,environmentId}:{path:string;request:Api;environmentId:string}){
 const [page,setPage]=useState<Page>('functions'),[selected,setSelected]=useState<string>(),[tab,setTab]=useState<Tab>('overview'),[adding,setAdding]=useState(false);
 const [testing,setTesting]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[opening,setOpening]=useState<string>(),[saving,setSaving]=useState(false);
 const origin=location.origin;
 const data=useLive<{data:RuntimeState}>(signal=>request(path+'/functions','GET',undefined,signal),[path,request]);
 const state=data.data?.data;
 // A change of the runtime takes a moment: ask again until it settles.
 useEffect(()=>{if(state?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[state?.state,data.data]);
 const fn=state?.functions.find(item=>item.name===selected);
 // A function that was deleted, or one deployed a moment ago and not in the list yet: leave the page, or open the new one once it is listed.
 useEffect(()=>{if(selected&&state&&!fn){setSelected(undefined);setTesting(false);}},[selected,state,fn]);
 useEffect(()=>{if(opening&&state?.functions.some(item=>item.name===opening)){setOpening(undefined);setSelected(opening);setTab('overview');}},[opening,state]);
 async function act(run:()=>Promise<unknown>){setBusy(true);setError('');try{await run();data.refresh();}catch(reason){setError(reason instanceof ApiError&&reason.serverMessage?reason.serverMessage:(reason as Error).message);}finally{setBusy(false);}}
 async function save(){
  if(!fn||saving)return;setError('');setSaving(true);
  try{
   // A link cannot carry the bearer token, so the file is fetched with the current session and handed to the browser.
   const token=(await auth.auth.getSession()).data.session?.access_token??'';
   const file=await download(token)(`${path}/functions/${fn.name}/download`);
   const url=URL.createObjectURL(file.blob),link=document.createElement('a');
   link.href=url;link.download=file.filename;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(reason){setError((reason as Error).message);}
  finally{setSaving(false);}
 }
 const closeTest=useCallback(()=>setTesting(false),[]);
 const open=(name:string)=>{setSelected(name);setTab('overview');setAdding(false);};
 return <section className="fw" aria-label="Edge Functions">
  <nav className="fw-nav" aria-label="Edge Functions"><p className="fw-nav-label">MANAGE</p>
   <button type="button" className={page==='functions'?'active':''} aria-current={page==='functions'?'page':undefined} onClick={()=>{setPage('functions');setSelected(undefined);}}>Functions</button>
   <button type="button" className={page==='secrets'?'active':''} aria-current={page==='secrets'?'page':undefined} onClick={()=>{setPage('secrets');setSelected(undefined);}}>Secrets</button></nav>
  <div className="fw-main">
   <Problem message={error||data.error} retry={!state&&data.error?data.refresh:undefined}/>
   {data.loading&&!state?<Loading/>:state&&(page==='secrets'?<Secrets path={path} request={request} onChanged={data.refresh}/>:fn?<>
    <div className="fw-fn-head"><nav className="fw-crumb" aria-label="Breadcrumb"><button type="button" onClick={()=>setSelected(undefined)}><ArrowLeft aria-hidden="true"/>Edge Functions</button><span aria-hidden="true">/</span><span aria-current="page">{fn.name}</span></nav>
     <div className="fw-head-tools"><button type="button" className="fw-ghost" disabled={saving} onClick={()=>void save()}><DownloadIcon aria-hidden="true"/>{saving?'Preparing…':'Download'}</button>
      <button type="button" className="fw-ghost" aria-haspopup="dialog" onClick={()=>setTesting(true)}><Send aria-hidden="true"/>Test</button></div></div>
    <Tabs label="Function" prefix="fn" tabs={TABS} value={tab} onChange={setTab}/>
    <div role="tabpanel" id={`fn-panel-${tab}`} aria-labelledby={`fn-tab-${tab}`} key={fn.name+tab}>
     {tab==='overview'?<Overview fn={fn} path={path} request={request} origin={origin} openLogs={()=>setTab('logs')}/>
      :tab==='logs'?<Logs fn={fn} path={path} request={request}/>:tab==='code'?<Code fn={fn} path={path} request={request}/>
      :<Settings fn={fn} path={path} request={request} origin={origin} environmentId={environmentId} onDeleted={()=>{setSelected(undefined);data.refresh();}}/>}</div>
    {testing&&<TestPanel fn={fn} path={path} request={request} origin={origin} onClose={closeTest}/>}</>
   :<List state={state} origin={origin} busy={busy} adding={adding} onOpen={open} onRefresh={data.refresh} onAdd={()=>setAdding(!adding)}
     onSwitch={on=>void act(()=>request(path+'/functions','PUT',{enabled:on}))}
     panel={adding?<AddFunction path={path} request={request} environmentId={environmentId} origin={origin} onClose={()=>setAdding(false)} existing={state.functions.map(item=>item.name)} onDeployed={name=>{setAdding(false);setOpening(name);data.refresh();}}/>:undefined}/>)}
  </div></section>;
}
