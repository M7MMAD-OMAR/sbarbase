import {useEffect,useState} from 'react';
import {KeyRound} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type State={state:'never'|'pending'|'done'|'failed';failure:string|null;rotatedAt:number|null};
const LABELS:Record<State['state'],string>={never:'Never rotated',pending:'Rotating…',done:'Rotated',failed:'Not finished'};

/** The environment's JWT signing key: rotate it when it may have leaked. */
export function SigningSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/signing-key','GET',undefined,signal),[path,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[confirm,setConfirm]=useState(false);
 const current=data.data?.data;
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 async function rotate(){
  setBusy(true);setError('');
  try{await request(path+'/signing-key/rotate','POST');setConfirm(false);data.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <section className="details"><h2>Signing key</h2>
 <p className="muted small">Auth signs every session with this environment's key. Rotate it if it may have leaked: every signed-in person is signed out, and anything that holds a token signed with the old key must get a new one. Publishable keys keep working.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  <p><span className={'state '+(current.state==='done'?'applied':current.state==='failed'?'failed':current.state==='pending'?'running':'')}>{LABELS[current.state]}</span>
   {current.rotatedAt&&<span className="small muted"> Last rotated {new Date(current.rotatedAt).toLocaleString()}</span>}</p>
  {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
  <div className="form-row">{confirm
   ?<div className="confirm"><span>Sign everyone out of this environment?</span><button className="danger" disabled={busy} onClick={()=>void rotate()}>Rotate signing key</button><button onClick={()=>setConfirm(false)}>Cancel</button></div>
   :<button disabled={busy||current.state==='pending'} onClick={()=>setConfirm(true)}><KeyRound aria-hidden="true"/>Rotate signing key</button>}</div>
 </>}</section>;
}
