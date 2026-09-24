import {createClient} from '@supabase/supabase-js';
import {useEffect,useState} from 'react';
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
 'Shares exceed gateway capacity':'The gateway has no free share left. Lower another environment\'s share first.'};
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
