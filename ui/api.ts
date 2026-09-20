import {createClient} from '@supabase/supabase-js';
import {useEffect,useState} from 'react';
export const auth=createClient(location.origin+'/management','sb_publishable_sbarbase_local_management',{
 auth:{persistSession:false,autoRefreshToken:true,detectSessionInUrl:false}});
export type Organization={id:string;name:string;role:'owner'|'admin'|'viewer'};
export type Project={id:string;name:string};
export type Environment=Project&{state?:string};
export type Api=(path:string,method?:string,body?:unknown,signal?:AbortSignal)=>Promise<any>;
export function api(token:string):Api {
 return async(path,method='GET',body,signal)=>{
  const response=await fetch('/management/v1'+path,{method,signal,headers:{authorization:'Bearer '+token,...(body===undefined?{}:{'content-type':'application/json'})},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
  if(response.status===401){void auth.auth.signOut({scope:'local'});throw new Error('Your session expired. Sign in again.');}
  if(!response.ok)throw new Error(response.status===403?'You no longer have permission for this action.':response.status===409?'This environment is not provisioned yet.':'The request failed. Refresh and try again.');
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
