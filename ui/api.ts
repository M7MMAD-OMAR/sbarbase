import {createClient} from '@supabase/supabase-js';
import {useEffect,useState} from 'react';
import {classifyStatus,type Outcome,type UpdateSettings,type UpdatesView} from './releases';
import {createSessionStorage,SESSION_STORAGE_KEY} from './session-storage';
export const auth=createClient(location.origin+'/management','sb_publishable_sbarbase_local_management',{
 auth:{persistSession:true,storage:createSessionStorage(),storageKey:SESSION_STORAGE_KEY,autoRefreshToken:true,detectSessionInUrl:false}});
export type Organization={id:string;name:string;role:'owner'|'admin'|'viewer'};
export type Project={id:string;name:string};
export type Environment=Project&{state?:string|null;failure?:'capacity_exceeded'|'runtime_failed'|null};
export type Api=(path:string,method?:string,body?:unknown,signal?:AbortSignal)=>Promise<any>;
export type MfaPolicy={mfa_required:boolean;epoch:number;needs_mfa:boolean;authorized:boolean;can_manage_policy:boolean};
export async function managementPolicy(required?:boolean,signal?:AbortSignal):Promise<MfaPolicy> {
 const {data}=await auth.auth.getSession();
 const response=await fetch('/management/auth/v1/policy',{method:required===undefined?'GET':'PATCH',signal,
  headers:{apikey:'sb_publishable_sbarbase_local_management',authorization:'Bearer '+data.session?.access_token,
   ...(required===undefined?{}:{'content-type':'application/json'})},...(required===undefined?{}:{body:JSON.stringify({mfa_required:required})})});
 if(!response.ok)throw new Error(response.status===403?'Verify a fresh authenticator code as the installation owner to change this setting.':'Unable to read or save the authentication policy. Try again.');
 return response.json();
}
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
/** What a refused request carries beyond the sentence the console shows: the status and the server's own message
 * (our management API sends plain ones of at most 600 characters, such as "Check the files: an index.ts"). Screens that can use the
 * message, such as a deploy, read `serverMessage`; every other screen keeps showing `message`. */
export class ApiError extends Error{constructor(message:string,readonly status:number,readonly serverMessage?:string){super(message);}}
const serverMessage=(response:Response)=>response.clone().json().then(value=>{const text=(value as {message?:unknown}).message;return typeof text==='string'&&text.length<=700?text:undefined;}).catch(()=>undefined);
/** One request with the session handling every call shares: an expired token is refreshed and the request retried once,
 * and a refused refresh or a lost authenticator signs out. Returns the response whatever its status. */
async function exchange(token:string,path:string,method:string,body:unknown,signal?:AbortSignal):Promise<Response> {
 const send=(bearer:string)=>fetch('/management/v1'+path,{method,signal,headers:{authorization:'Bearer '+bearer,...(body===undefined?{}:{'content-type':'application/json'})},
  ...(body===undefined?{}:{body:JSON.stringify(body)})});
 let response:Response;
 try{response=await send(token);}
 catch(error){if(signal?.aborted)throw error;throw new Error('Unable to reach the server. Check your connection and try again.');}
 if(response.status===401){
  // The access token lasts an hour and a sleeping or background tab can miss its refresh, so a 401 usually means
  // "expired", not "signed out". Signing out here would delete the still valid session on the server.
  // Refresh once, retry, and sign out only when the refresh itself is refused.
  const {data}=await auth.auth.refreshSession().catch(()=>({data:{session:null}}));
  if(data.session?.access_token&&data.session.access_token!==token){
   try{response=await send(data.session.access_token);}
   catch(error){if(signal?.aborted)throw error;throw new Error('Unable to reach the server. Check your connection and try again.');}
  }
  if(response.status===401){void auth.auth.signOut({scope:'local'});throw new Error('Your session expired. Sign in again.');}
 }
 if(response.status===403){
  const denied=await response.clone().json().catch(()=>null) as {code?:string}|null;
  if(denied?.code==='mfa_required'){void auth.auth.signOut({scope:'local'});throw new Error('Verify your authenticator again. Sign in to continue.');}
 }
 return response;
}
function refusal(response:Response,message:string|undefined):ApiError {
 if(response.status===409)return new ApiError(conflicts[message??'']??'This environment is not provisioned yet.',409,message);
 return new ApiError(response.status===403?'You no longer have permission for this action.':
  response.status===400||response.status===422?'Check the entered values and try again.':
  response.status===429?'Too many requests. Wait a moment and try again.':
  response.status>=500?'The server is temporarily unavailable. Try again.':'The request failed. Refresh and try again.',response.status,message);
}
export function api(token:string):Api {
 return async(path,method='GET',body,signal)=>{
  const response=await exchange(token,path,method,body,signal);
  if(!response.ok)throw refusal(response,await serverMessage(response));
  try{return await response.json();}catch{throw new Error('The server returned an unreadable response. Try again.');}
 };
}
/** A file from the management API, such as a function's ZIP. A link cannot carry the bearer token, so the console fetches
 * it with the same session handling and hands the bytes to the browser. */
export type Download=(path:string,signal?:AbortSignal)=>Promise<{blob:Blob;filename:string}>;
export function download(token:string):Download {
 return async(path,signal)=>{
  const response=await exchange(token,path,'GET',undefined,signal);
  if(!response.ok)throw refusal(response,await serverMessage(response));
  const name=response.headers.get('content-disposition')?.match(/filename="([A-Za-z0-9._-]{1,100})"/)?.[1];
  return {blob:await response.blob(),filename:name??'download'};
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
 apply:(version:string,acknowledged?:boolean)=>Promise<void>;
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
  apply:async(version,acknowledged)=>{await send('/apply','POST',acknowledged?{version,acknowledged:true}:{version});},
  rollback:async()=>{await send('/rollback','POST');},
  saveSettings:async settings=>((await send('/settings','PUT',settings)) as {data:UpdateSettings}).data};
}
