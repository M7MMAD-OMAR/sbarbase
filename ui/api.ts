import {createClient} from '@supabase/supabase-js';
import {useEffect,useState} from 'react';
import {classifyStatus,type Outcome,type UpdateSettings,type UpdatesView} from './releases';
export const auth=createClient(location.origin+'/management','sb_publishable_sbarbase_local_management',{
 auth:{persistSession:false,autoRefreshToken:true,detectSessionInUrl:false}});
export type Organization={id:string;name:string;role:'owner'|'admin'|'viewer'};
export type Project={id:string;name:string};
export type Environment=Project&{state?:string|null;failure?:'capacity_exceeded'|'runtime_failed'|null};
export type Api=(path:string,method?:string,body?:unknown,signal?:AbortSignal)=>Promise<any>;
/** The 409 answers the management API gives, in the words the console shows. */
const conflicts:Record<string,string>={
 'Name already used':'That name is already used here. Choose another name.',
 'Environment capacity reached':'This installation has reached its environment limit. Ask the installation owner to review capacity.',
 'Operation is not retryable':'Only a failed or cancelled environment can be retried.',
 'Environment is not ready':'This environment is not provisioned yet.',
 'Studio is not running':'Studio is not running yet. Start it first.',
 'Last owner cannot be removed':'An organization needs at least one owner. Make someone else an owner first.',
 'Shares exceed gateway capacity':'The gateway has no free share left. Lower another environment\'s share first.',
 'Organization has projects':'Delete or move this organization\'s projects first.',
 'Project has environments':'Delete this project\'s environments first.',
 'Installation organization cannot be deleted':'This organization runs the installation and cannot be deleted.',
 'Provisioning is active':'Wait until provisioning finishes.',
 'Environment services are still on':'Turn off Studio, Realtime, Edge Functions and database access for this environment first, and wait for changes in progress to finish.'};
export function api(token:string):Api {
 return async(path,method='GET',body,signal)=>{
  const response=await fetch('/management/v1'+path,{method,signal,headers:{authorization:'Bearer '+token,...(body===undefined?{}:{'content-type':'application/json'})},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
  if(response.status===401){void auth.auth.signOut({scope:'local'});throw new Error('Your session expired. Sign in again.');}
  if(response.status===409){
   const message=await response.json().then(value=>(value as {message?:string}).message).catch(()=>undefined);
   throw new Error(conflicts[message??'']??'This environment is not provisioned yet.');
  }
  if(!response.ok)throw new Error(response.status===403?'You no longer have permission for this action.':'The request failed. Refresh and try again.');
  return response.json();
 };
}
export function useData<T>(load:(signal:AbortSignal)=>Promise<T>,dependencies:unknown[]) {
 const [data,setData]=useState<T>(),[error,setError]=useState(''),[loading,setLoading]=useState(true),[version,setVersion]=useState(0);
 useEffect(()=>{const controller=new AbortController();setData(undefined);setError('');setLoading(true);
  load(controller.signal).then(value=>{if(!controller.signal.aborted)setData(value);}).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Request failed.');})
   .finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();
 },[...dependencies,version]);
 return {data,error,loading,refresh:()=>setVersion(value=>value+1)};
}
/** The installation's update routes. They keep their own error handling: while the server
 * restarts an answer may be a network error, a gateway page or a briefly rejected token, and
 * none of those may sign the operator out or read as a failure. The token is read on each
 * call so a refresh during a long restart is picked up. */
export class UpdateError extends Error{constructor(message:string,readonly outcome:Exclude<Outcome,'ok'>){super(message);}}
export type UpdatesApi={
 get:(signal?:AbortSignal)=>Promise<UpdatesView>;
 check:()=>Promise<void>;
 apply:(version:string)=>Promise<void>;
 rollback:()=>Promise<void>;
 saveSettings:(settings:UpdateSettings)=>Promise<UpdateSettings>;
};
export function updatesApi(token:()=>string):UpdatesApi{
 async function send(path:string,method:string,body?:unknown,signal?:AbortSignal):Promise<unknown>{
  let response:Response;
  try{response=await fetch('/management/v1/updates'+path,{method,headers:{authorization:'Bearer '+token(),...(body===undefined?{}:{'content-type':'application/json'})},
   ...(body===undefined?{}:{body:JSON.stringify(body)}),signal:signal??AbortSignal.timeout(10000)});}
  catch{throw new UpdateError('The server did not answer. It may be restarting; try again in a moment.','restarting');}
  const outcome=classifyStatus(response.status);
  if(outcome==='ok')return response.status===202?undefined:response.json();
  if(outcome==='refused'){
   const message=await response.json().then(value=>(value as {message?:string}).message).catch(()=>undefined);
   throw new UpdateError(message||'The server refused this request.','refused');
  }
  throw new UpdateError(outcome==='restarting'?'The server did not answer. It may be restarting; try again in a moment.'
   :outcome==='forbidden'?'Only the installation operator can manage updates.':'The request failed. Refresh and try again.',outcome);
 }
 return {
  get:async signal=>((await send('','GET',undefined,signal)) as {data:UpdatesView}).data,
  check:async()=>{await send('/check','POST');},
  apply:async version=>{await send('/apply','POST',{version});},
  rollback:async()=>{await send('/rollback','POST');},
  saveSettings:async settings=>((await send('/settings','PUT',settings)) as {data:UpdateSettings}).data};
}
