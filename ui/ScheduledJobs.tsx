import {useEffect,useState} from 'react';
import {CalendarClock,Square} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type State={desired:'on'|'off';state:'off'|'pending'|'on'|'failed';failure:string|null};
const LABELS:Record<State['state'],string>={off:'Off',pending:'Applying…',on:'On',failed:'Not applied'};

/** Scheduled jobs for this environment: `cron.schedule` and `net.http_*`, run by Sbarbase's scheduler. */
export function ScheduledJobsSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/scheduled-jobs','GET',undefined,signal),[path,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const current=data.data?.data;
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 async function turn(enabled:boolean){setBusy(true);setError('');try{await request(path+'/scheduled-jobs','PUT',{enabled});data.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const on=current?.state==='on'||(current?.state==='pending'&&current.desired==='on');
 return <section className="details"><h2>Scheduled jobs</h2>
 <p className="muted small">Run SQL on a schedule with <code>cron.schedule()</code>, and call other services from the database with <code>net.http_post()</code>, as with Supabase Cron and pg_net. Jobs run as the environment's developer role.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  <p><span className={'state '+(current.state==='on'?'applied':current.state==='failed'?'failed':current.state==='pending'?'running':'')}>{LABELS[current.state]}</span></p>
  {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
  <div className="form-row">{on
   ?<button disabled={busy||current.state==='pending'} onClick={()=>void turn(false)}><Square aria-hidden="true"/>Turn off scheduled jobs</button>
   :<button className="primary" disabled={busy||current.state==='pending'} onClick={()=>void turn(true)}><CalendarClock aria-hidden="true"/>Turn on scheduled jobs</button>}</div>
  <p className="small muted">Turning them off stops the runs and keeps your jobs and their history. Requests from the database cannot reach private addresses or this server, except through its public address.</p>
 </>}</section>;
}
