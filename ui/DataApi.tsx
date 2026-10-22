import {useEffect,useState} from 'react';
import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';
import {dataApiDisplay,type DataApiView as State} from './data-api-view';

const list=(value:string)=>value.split(',').map(item=>item.trim()).filter(Boolean);

/** Which schemas the Data API (PostgREST) serves for this environment, as Supabase's API settings. */
export function DataApiSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:State}>(signal=>request(path+'/data-api','GET',undefined,signal),[path,request]);
 const current=data.data?.data;
 const [schemas,setSchemas]=useState(''),[searchPath,setSearchPath]=useState(''),[rows,setRows]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
 // The form follows the saved settings, not every poll: while a change is pending it must keep what the user types.
 const savedKey=current?JSON.stringify([current.schemas,current.extraSearchPath,current.maxRows]):'';
 useEffect(()=>{if(!current)return;setSchemas(current.schemas.join(', '));setSearchPath(current.extraSearchPath.join(', '));setRows(current.maxRows===null?'':String(current.maxRows));},[savedKey]);
 useEffect(()=>{if(current?.state!=='pending')return;const timer=setTimeout(data.refresh,3000);return()=>clearTimeout(timer);},[current?.state,data.data]);
 async function save(){
  setBusy(true);setError('');setSaved('');
  try{
   await request(path+'/data-api','PUT',{schemas:list(schemas),extraSearchPath:list(searchPath),maxRows:rows.trim()?Number(rows):null});
   setSaved('Saved. The Data API serves the new settings once they are applied.');data.refresh();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <section className="details"><h2>Data API</h2>
 <p className="muted small">The schemas your app reads and writes through <code>supabase.from()</code> and <code>supabase.rpc()</code>. The first is used by default; reach another with <code>supabase.schema('name')</code>. Each schema needs <code>GRANT USAGE</code> and table grants for <code>anon</code>, <code>authenticated</code> and <code>service_role</code>, and row level security on its tables.</p>
 <ErrorMessage message={error||data.error}/>{data.loading?<Loading/>:current&&<>
  {(display=>(<>
   <p><span className={'state '+display.tone}>{display.label}</span></p>
   {display.notices.map(text=><p key={text} className="notice">{text}</p>)}
   {display.servedNow!==null&&<p className="small muted">Served now: {display.servedNow}</p>}
  </>))(dataApiDisplay(current))}
  <label htmlFor="data-api-schemas">Exposed schemas</label>
  <input id="data-api-schemas" value={schemas} placeholder="public, app" onChange={event=>setSchemas(event.target.value)}/>
  <label htmlFor="data-api-search-path">Extra search path</label>
  <input id="data-api-search-path" value={searchPath} placeholder="public, extensions" onChange={event=>setSearchPath(event.target.value)}/>
  <label htmlFor="data-api-rows">Maximum rows per request</label>
  <input id="data-api-rows" type="number" min={1} max={1000000} step={1} value={rows} placeholder="No limit" onChange={event=>setRows(event.target.value)}/>
  <div className="form-row"><button className="primary" disabled={busy||current.state==='pending'} onClick={()=>void save()}>Save Data API settings</button></div>
  <p className="small muted" role="status">{saved}</p>
  <p className="small muted">Leave Exposed schemas empty to turn the Data API off. A schema must exist before it can be exposed, and cannot be renamed or dropped while it is exposed. Platform schemas such as <code>auth</code>, <code>storage</code> and <code>vault</code> are never exposed.</p>
 </>}</section>;
}
