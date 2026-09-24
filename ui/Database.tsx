import {useEffect,useState} from 'react';
import {Database as DatabaseIcon,Square,RotateCcw,Copy} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type State={desired:'on'|'off';state:'off'|'pending'|'on'|'failed';failure:string|null;url:string;
 connection:{host:string;port:number;user:string;database:string}};
const LABELS:Record<State['state'],string>={off:'Off',pending:'Applying…',on:'On',failed:'Not applied'};

/** Direct database access: a PostgreSQL connection string for migrations, psql and ORMs. */
export function DatabaseSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/database','GET',undefined,signal),[path,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[secret,setSecret]=useState(''),[copied,setCopied]=useState('');
 const current=data.data?.data;
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 async function act(method:string,suffix:string,body?:unknown){
  setBusy(true);setError('');setCopied('');
  try{const result=await request(path+'/database'+suffix,method,body);setSecret(result?.data?.password?result.data.url:'');data.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the text manually.');}}
 const on=current&&(current.state==='on'||(current.state==='pending'&&current.desired==='on'));
 return <section className="details"><h2>Database</h2>
 <p className="muted small">Connect with <code>psql</code>, <code>pg_dump</code>, <code>supabase db push --db-url</code>, Prisma or Drizzle to run migrations. The developer login can change anything in this environment's database, including triggers on <code>auth.users</code> and Storage policies.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  <p><span className={'state '+(current.state==='on'?'applied':current.state==='failed'?'failed':current.state==='pending'?'running':'')}>{LABELS[current.state]}</span></p>
  {current.state==='failed'&&current.failure&&<p className="notice">{current.failure}</p>}
  {secret&&<div className="new-key"><label htmlFor="database-url">Save this connection string now. The password is only shown once.</label>
   <textarea id="database-url" readOnly value={secret}/><div className="form-row"><button onClick={()=>void copy(secret)}><Copy aria-hidden="true"/>Copy</button><button onClick={()=>setSecret('')}>I have saved it</button></div></div>}
  {!secret&&<><label htmlFor="database-template">Connection string</label><div className="form-row"><input id="database-template" readOnly value={current.url}/><button onClick={()=>void copy(current.url)}><Copy aria-hidden="true"/>Copy</button></div></>}
  <p className="small muted" role="status">{copied}</p>
  <div className="form-row">{on
   ?<><button disabled={busy||current.state==='pending'} onClick={()=>void act('POST','/password')}><RotateCcw aria-hidden="true"/>New password</button>
     <button disabled={busy||current.state==='pending'} onClick={()=>void act('PUT','',{enabled:false})}><Square aria-hidden="true"/>Turn off</button></>
   :<button className="primary" disabled={busy||current.state==='pending'} onClick={()=>void act('PUT','',{enabled:true})}><DatabaseIcon aria-hidden="true"/>Turn on database access</button>}</div>
  <p className="small muted">The address is on the server itself (port {current.connection.port}). From your computer, open an SSH tunnel first: <code>ssh -L {current.connection.port}:127.0.0.1:{current.connection.port} you@your-server</code>.</p>
 </>}</section>;
}
