import {useState} from 'react';
import {ChevronLeft,ChevronRight,RefreshCw} from 'lucide-react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type Entry={id:string;created_at:string;ip_address:string;actor:string;action:string;log_type:string};
type Answer={data:{page:number;per_page:number;total:number|null;entries:Entry[]}};
const PER_PAGE=50;
const when=(text:string)=>{const date=new Date(text);return Number.isNaN(date.getTime())?text:date.toLocaleString('en-US',{dateStyle:'medium',timeStyle:'medium',hour12:true});};

/** Audit Logs, as the Supabase cloud shows them: what Auth recorded about sign-ins and account changes, newest first. It reads Auth's own
 * admin API, so the environment's database access does not need to be on. `writesOff` is the switch "Write audit logs to the database". */
export function AuditPanel({path,request,writesOff}:{path:string;request:Api;writesOff:boolean}){
 const [page,setPage]=useState(1);
 const data=useData<Answer>(signal=>request(`${path}/auth-audit?page=${page}&per_page=${PER_PAGE}`,'GET',undefined,signal),[path,request,page]);
 const entries=data.data?.data.entries??[];
 const total=data.data?.data.total??null;
 const last=total===null?null:Math.max(1,Math.ceil(total/PER_PAGE));
 return <>
  {writesOff&&<p className="aw-notice" role="status">Audit logs are not written to the database for this environment, so no new entries appear here. Turn the switch above back on, save, and new entries are recorded again.</p>}
  <div className="aw-actions aw-inline">
   <button type="button" className="aw-ghost" disabled={data.loading} onClick={data.refresh}><RefreshCw aria-hidden="true"/>Refresh</button>
   <button type="button" className="aw-ghost" disabled={data.loading||page<=1} onClick={()=>setPage(page-1)} aria-label="Newer entries"><ChevronLeft aria-hidden="true"/></button>
   <button type="button" className="aw-ghost" disabled={data.loading||(last!==null?page>=last:entries.length<PER_PAGE)} onClick={()=>setPage(page+1)} aria-label="Older entries"><ChevronRight aria-hidden="true"/></button></div>
  <ErrorMessage message={data.error}/>
  {data.loading&&!data.data?<Loading/>:entries.length===0&&!data.error?<p className="aw-hint">No audit entries yet.</p>
   :<div className="aw-table"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>IP address</th></tr></thead>
    <tbody>{entries.map(entry=><tr key={entry.id}><td>{when(entry.created_at)}</td><td>{entry.actor||'System'}</td><td>{entry.action}{entry.log_type&&<span className="aw-tag">{entry.log_type}</span>}</td><td><code>{entry.ip_address||'None'}</code></td></tr>)}</tbody></table></div>}
  <p className="aw-hint">{total===null?`Page ${page}`:`Page ${page} of ${last}, ${total} entries`}. These entries come from the environment's Auth; the people in them are that environment's users.</p>
 </>;
}
