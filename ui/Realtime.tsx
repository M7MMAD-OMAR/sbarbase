import {useEffect,useState} from 'react';
import {Radio,Square} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type State={desired:'on'|'off';state:'off'|'pending'|'on'|'failed';failure:string|null};
const LABELS:Record<State['state'],string>={off:'Off',pending:'Applying…',on:'On',failed:'Not applied'};

/** Realtime for this environment: database changes, broadcast and presence over one socket. */
export function RealtimeSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/realtime','GET',undefined,signal),[path,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const current=data.data?.data;
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 async function turn(enabled:boolean){setBusy(true);setError('');try{await request(path+'/realtime','PUT',{enabled});data.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const on=current?.state==='on'||(current?.state==='pending'&&current.desired==='on');
 return <section className="details"><h2>Realtime</h2>
 <p className="muted small">Listen to database changes, and send broadcast and presence messages between clients, with <code>supabase.channel()</code>. Changes to every table in the <code>public</code> schema are published; row level security decides who receives them.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  <p><span className={'state '+(current.state==='on'?'applied':current.state==='failed'?'failed':current.state==='pending'?'running':'')}>{LABELS[current.state]}</span></p>
  {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
  <div className="form-row">{on
   ?<button disabled={busy||current.state==='pending'} onClick={()=>void turn(false)}><Square aria-hidden="true"/>Turn off Realtime</button>
   :<button className="primary" disabled={busy||current.state==='pending'} onClick={()=>void turn(true)}><Radio aria-hidden="true"/>Turn on Realtime</button>}</div>
  <p className="small muted">Realtime runs as its own small service for this environment, and only while it is on.</p>
 </>}</section>;
}
